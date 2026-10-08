#!/usr/bin/python
#
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
#
# Helper for the AWS Parallel Computing Service (PCS) Athena++ workshop notebook.
# Mirrors the role that pcluster_athena.PClusterHelper plays for the ParallelCluster
# lab: it hides the "boring" orchestration (VPC/subnet discovery, security group, EFS
# shared storage, PCS node IAM instance profile, EC2 launch templates, and the PCS
# cluster / login node group / compute node group / queue) so the notebook can focus on
# running the simulation.
#
# PCS differs from ParallelCluster:
#  * The Slurm controller is fully managed (no head node you own).
#  * Compute/login nodes are "compute node groups"; a "queue" is the Slurm partition.
#  * Nodes launch from an EC2 launch template using a PCS-compatible sample AMI.
#  * Shared storage here is EFS, mounted at /shared on every node via launch-template
#    user-data (replacing ParallelCluster's EBS /shared).

import boto3
import botocore
import json
import time
import base64
from botocore.exceptions import ClientError


class PCSHelper:
    def __init__(self, cluster_name, slurm_version="25.11",
                 compute_instance_type="c5n.2xlarge", login_instance_type="c5.xlarge",
                 compute_max_nodes=4):
        self.session = boto3.session.Session()
        self.region = self.session.region_name
        self.account_id = boto3.client("sts").get_caller_identity()["Account"]
        self.cluster_name = cluster_name
        self.slurm_version = slurm_version
        self.compute_instance_type = compute_instance_type
        self.login_instance_type = login_instance_type
        self.compute_max_nodes = compute_max_nodes

        self.ec2 = self.session.client("ec2")
        self.efs = self.session.client("efs")
        self.iam = self.session.client("iam")
        self.pcs = self.session.client("pcs")

        # bucket for job files + simulation output (same convention as the pcluster lab)
        self.my_bucket_name = f"{cluster_name.lower()}-{self.account_id}"

        # populated by create_before()
        self.vpc_id = None
        self.subnet_id = None
        self.security_group_id = None
        self.efs_id = None
        self.instance_profile_arn = None
        self.compute_lt_id = None
        self.login_lt_id = None
        # populated by create_cluster()/create_node_groups()/create_queue()
        self.cluster_id = None
        self.login_node_group_id = None
        self.compute_node_group_id = None
        self.queue_id = None

    # ---------- small utilities ----------
    def replace_placeholder(self, content, values):
        for k, v in values.items():
            content = content.replace(k, v)
        return content

    def template_to_file(self, source_file, target_file, mapping):
        with open(source_file, "rt") as f:
            content = f.read()
        with open(target_file, "wt") as fo:
            fo.write(self.replace_placeholder(content, mapping))

    def upload_athena_files(self, input_file, batch_file, prefix):
        s3 = self.session.client("s3")
        s3.upload_file("build/" + input_file, self.my_bucket_name, prefix + "/" + input_file)
        s3.upload_file("build/" + batch_file, self.my_bucket_name, prefix + "/" + batch_file)

    # ---------- AMI discovery ----------
    def find_pcs_sample_ami(self, arch="x86_64"):
        """Resolve the newest AWS PCS sample AMI (al2023) that supports our Slurm version.
        There is no public SSM parameter for PCS sample AMIs, so we use describe_images."""
        resp = self.ec2.describe_images(
            Owners=["amazon"],
            Filters=[
                {"Name": "name", "Values": [f"aws-pcs-sample_ami-al2023-{arch}*"]},
                {"Name": "state", "Values": ["available"]},
            ],
        )
        images = resp.get("Images", [])
        # Prefer AMIs that advertise our Slurm version in name/description; otherwise take
        # the newest sample AMI (26.05+ AMIs support multiple versions and omit it).
        def matches(img):
            hay = (img.get("Name", "") + " " + img.get("Description", "")).lower()
            return self.slurm_version in hay
        preferred = [i for i in images if matches(i)]
        pool = preferred if preferred else images
        if not pool:
            raise RuntimeError(f"No PCS sample AMI found for al2023 {arch}")
        ami = sorted(pool, key=lambda i: i["CreationDate"])[-1]
        print(f"Selected PCS sample AMI: {ami['ImageId']} ({ami['Name']})")
        return ami["ImageId"]

    # ---------- prerequisites ----------
    def create_before(self):
        """Create everything PCS needs before the cluster: bucket, pick VPC/subnet,
        security group, EFS shared filesystem, node IAM instance profile, and the EC2
        launch templates for the login and compute node groups."""
        self._ensure_bucket()
        self._discover_network()
        self._ensure_security_group()
        self._ensure_efs()
        self._ensure_instance_profile()
        self.ami_id = self.find_pcs_sample_ami()
        self._ensure_launch_templates()
        print("Pre-requisites ready.")

    def _ensure_bucket(self):
        from lib import workshop  # reuse the repo helper if available
        try:
            self.my_bucket_name = workshop.create_bucket(
                self.region, self.session, self.my_bucket_name, False)
        except Exception:
            # fall back to a direct create if the helper isn't importable
            s3 = self.session.client("s3")
            try:
                if self.region == "us-east-1":
                    s3.create_bucket(Bucket=self.my_bucket_name)
                else:
                    s3.create_bucket(Bucket=self.my_bucket_name,
                                     CreateBucketConfiguration={"LocationConstraint": self.region})
            except ClientError as e:
                if e.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                    raise
        print("Bucket:", self.my_bucket_name)

    def _discover_network(self):
        """Use the default VPC + one subnet, matching the ParallelCluster lab's approach."""
        vpcs = self.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
        if not vpcs:
            raise RuntimeError("No default VPC found. Provide a VPC/subnet explicitly.")
        self.vpc_id = vpcs[0]["VpcId"]
        subnets = self.ec2.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [self.vpc_id]}])["Subnets"]
        # pick a subnet, avoiding capacity-constrained AZ ids used in the pcluster lab
        avoid = ("use1-az3", "usw2-az4")
        chosen = next((s for s in subnets if s.get("AvailabilityZoneId") not in avoid), subnets[0])
        self.subnet_id = chosen["SubnetId"]
        print(f"VPC: {self.vpc_id}, Subnet: {self.subnet_id} ({chosen['AvailabilityZone']})")

    def _ensure_security_group(self):
        name = f"{self.cluster_name}-pcs-sg"
        existing = self.ec2.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": [name]},
                     {"Name": "vpc-id", "Values": [self.vpc_id]}])["SecurityGroups"]
        if existing:
            self.security_group_id = existing[0]["GroupId"]
        else:
            self.security_group_id = self.ec2.create_security_group(
                GroupName=name, Description="PCS cluster/node communication",
                VpcId=self.vpc_id)["GroupId"]
            # PCS requires: inbound all from self; outbound all + to self.
            self.ec2.authorize_security_group_ingress(
                GroupId=self.security_group_id,
                IpPermissions=[{"IpProtocol": "-1",
                                "UserIdGroupPairs": [{"GroupId": self.security_group_id}]}])
        print("Security group:", self.security_group_id)

    def _ensure_efs(self):
        token = f"{self.cluster_name}-efs"
        # creation_token makes this idempotent
        existing = [fs for fs in self.efs.describe_file_systems()["FileSystems"]
                    if fs.get("Name") == token or fs.get("CreationToken") == token]
        if existing:
            self.efs_id = existing[0]["FileSystemId"]
        else:
            self.efs_id = self.efs.create_file_system(
                CreationToken=token, Encrypted=True,
                Tags=[{"Key": "Name", "Value": token}])["FileSystemId"]
            # wait until available
            for _ in range(30):
                st = self.efs.describe_file_systems(FileSystemId=self.efs_id)["FileSystems"][0]["LifeCycleState"]
                if st == "available":
                    break
                time.sleep(5)
        # mount target in our subnet (ignore if it already exists)
        try:
            self.efs.create_mount_target(FileSystemId=self.efs_id, SubnetId=self.subnet_id,
                                         SecurityGroups=[self.security_group_id])
        except ClientError as e:
            if e.response["Error"]["Code"] != "MountTargetConflict":
                raise
        print("EFS:", self.efs_id)

    def _ensure_instance_profile(self):
        # PCS requires the role path to start with /aws-pcs/ (or name to start with AWSPCS)
        role_name = f"{self.cluster_name}-pcs-node"
        path = "/aws-pcs/"
        assume = json.dumps({"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"},
             "Action": "sts:AssumeRole"}]})
        try:
            self.iam.create_role(Path=path, RoleName=role_name, AssumeRolePolicyDocument=assume)
            for arn in ["arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
                        "arn:aws:iam::aws:policy/AmazonElasticFileSystemClientReadWriteAccess"]:
                self.iam.attach_role_policy(RoleName=role_name, PolicyArn=arn)
            self.iam.put_role_policy(RoleName=role_name, PolicyName="PcsNode",
                PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [
                    {"Effect": "Allow", "Action": ["pcs:RegisterComputeNodeGroupInstance"], "Resource": "*"},
                    {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"], "Resource": "*"}]}))
        except ClientError as e:
            if e.response["Error"]["Code"] != "EntityAlreadyExists":
                raise
        try:
            self.iam.create_instance_profile(Path=path, InstanceProfileName=role_name)
            self.iam.add_role_to_instance_profile(InstanceProfileName=role_name, RoleName=role_name)
            time.sleep(10)  # allow the instance profile to propagate
        except ClientError as e:
            if e.response["Error"]["Code"] != "EntityAlreadyExists":
                raise
        self.instance_profile_arn = self.iam.get_instance_profile(
            InstanceProfileName=role_name)["InstanceProfile"]["Arn"]
        print("Instance profile:", self.instance_profile_arn)

    def _user_data(self, build_athena=False):
        """Cloud-init that mounts EFS at /shared on every node; the login node also builds
        Athena++ (VTK output, no HDF5) into /shared so compute nodes can run it."""
        mount = (
            "mkdir -p /shared\n"
            "if ! mountpoint -q /shared; then\n"
            f"  mount -t efs -o tls {self.efs_id}:/ /shared || "
            f"mount -t nfs4 -o nfsvers=4.1 {self.efs_id}.efs.{self.region}.amazonaws.com:/ /shared\n"
            "fi\n"
        )
        athena = ""
        if build_athena:
            athena = (
                "if [ ! -x /shared/athena-public-version/bin/athena ]; then\n"
                "  yum install -y git gcc gcc-c++ make python3 || true\n"
                "  # Locate the MPI C++ wrapper. The PCS AL2023 sample AMI ships the EFA\n"
                "  # OpenMPI stack at /opt/amazon/openmpi/bin/mpicxx. 'module load openmpi'\n"
                "  # works in an interactive shell but NOT in this non-interactive user-data\n"
                "  # shell (the 'module' function is only set up by /etc/profile.d, which\n"
                "  # isn't sourced here) -> a plain 'make' failed with 'mpicxx: No such file'.\n"
                "  # Source the module init if present, then fall back to putting the known\n"
                "  # OpenMPI bin directory on PATH directly.\n"
                "  for mi in /etc/profile.d/modules.sh /usr/share/Modules/init/bash; do\n"
                "    [ -f \"$mi\" ] && . \"$mi\" && module load openmpi 2>/dev/null || true\n"
                "  done\n"
                "  for d in /opt/amazon/openmpi/bin /usr/lib64/openmpi/bin /usr/bin; do\n"
                "    if [ -x \"$d/mpicxx\" ]; then export PATH=\"$d:$PATH\"; break; fi\n"
                "  done\n"
                "  if ! command -v mpicxx >/dev/null 2>&1; then\n"
                "    echo 'ERROR: mpicxx not found on this AMI; cannot build Athena++ with MPI.' >&2\n"
                "    echo 'Searched /opt/amazon/openmpi/bin, /usr/lib64/openmpi/bin, /usr/bin.' >&2\n"
                "    exit 1\n"
                "  fi\n"
                "  echo \"Using MPI: $(command -v mpicxx)\"\n"
                "  cd /shared\n"
                "  [ -d athena-public-version ] || git clone https://github.com/PrincetonUniversity/athena-public-version\n"
                "  cd athena-public-version\n"
                "  # Clean any stale partial build so an empty bin/athena self-heals on retry.\n"
                "  make clean 2>/dev/null || true\n"
                "  # GCC 13 on AL2023 dropped transitive <limits>; inject it where Athena++\n"
                "  # uses std::numeric_limits without including it, or the build fails with\n"
                "  # \"'numeric_limits' is not a member of 'std'\".\n"
                "  for f in $(grep -rl 'std::numeric_limits' src/ 2>/dev/null); do\n"
                "    grep -q '#include <limits>' \"$f\" || sed -i '0,/^#include/s//#include <limits>\\n#include/' \"$f\"\n"
                "  done\n"
                "  python3 configure.py --prob orszag_tang -b --flux hlld -omp -mpi\n"
                "  make -j \"$(nproc)\"\n"
                "  test -x bin/athena && echo 'Athena++ built successfully.'\n"
                "fi\n"
            )
        script = "#!/bin/bash\nset -euxo pipefail\nexec > /var/log/pcs-bootstrap.log 2>&1\n" + mount + athena
        mime = (
            'MIME-Version: 1.0\n'
            'Content-Type: multipart/mixed; boundary="==BOUNDARY=="\n\n'
            '--==BOUNDARY==\n'
            'Content-Type: text/x-shellscript; charset="us-ascii"\n\n'
            + script +
            '\n--==BOUNDARY==--\n'
        )
        return base64.b64encode(mime.encode()).decode()

    def _ensure_launch_templates(self):
        for name, build in [(f"{self.cluster_name}-pcs-compute", False),
                            (f"{self.cluster_name}-pcs-login", True)]:
            data = {
                "SecurityGroupIds": [self.security_group_id],
                "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"},
                "UserData": self._user_data(build_athena=build),
            }
            try:
                lt = self.ec2.create_launch_template(
                    LaunchTemplateName=name, LaunchTemplateData=data)["LaunchTemplate"]
                lt_id = lt["LaunchTemplateId"]
            except ClientError as e:
                if e.response["Error"]["Code"] != "InvalidLaunchTemplateName.AlreadyExistsException":
                    raise
                # Template already exists. Create a NEW VERSION with the current UserData
                # so bootstrap changes (EFS mount / Athena build fixes) are actually picked
                # up. Node groups use LatestVersionNumber, so new launches get this version.
                lt_id = self.ec2.describe_launch_templates(
                    LaunchTemplateNames=[name])["LaunchTemplates"][0]["LaunchTemplateId"]
                ver = self.ec2.create_launch_template_version(
                    LaunchTemplateId=lt_id, LaunchTemplateData=data,
                    VersionDescription="updated bootstrap")["LaunchTemplateVersion"]["VersionNumber"]
                self.ec2.modify_launch_template(
                    LaunchTemplateId=lt_id, DefaultVersion=str(ver))
                print(f"  updated launch template {name} to version {ver}")
            if build:
                self.login_lt_id = lt_id
            else:
                self.compute_lt_id = lt_id
        print(f"Launch templates: compute={self.compute_lt_id}, login={self.login_lt_id}")

    # ---------- PCS cluster / node groups / queue ----------
    def _find_cluster_id(self):
        """Return the id of an existing cluster with our name, or None."""
        try:
            for c in self.pcs.list_clusters().get("clusters", []):
                if c.get("name") == self.cluster_name:
                    return c["id"]
        except ClientError:
            pass
        return None

    def create_cluster(self):
        """Idempotent: reuse an existing cluster of the same name if present."""
        existing = self._find_cluster_id()
        if existing:
            self.cluster_id = existing
            print(f"Cluster '{self.cluster_name}' already exists ({self.cluster_id}); reusing.")
        else:
            resp = self.pcs.create_cluster(
                clusterName=self.cluster_name,
                scheduler={"type": "SLURM", "version": self.slurm_version},
                size="SMALL",
                networking={"subnetIds": [self.subnet_id],
                            "securityGroupIds": [self.security_group_id]},
            )
            self.cluster_id = resp["cluster"]["id"]
            print(f"Creating cluster {self.cluster_id} ... (this takes several minutes)")
        self._wait_cluster_active()
        return self.cluster_id

    def _wait_cluster_active(self, timeout=1800):
        start = time.time()
        while time.time() - start < timeout:
            status = self.pcs.get_cluster(clusterIdentifier=self.cluster_id)["cluster"]["status"]
            print("  cluster status:", status)
            if status == "ACTIVE":
                return
            if status in ("CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"):
                raise RuntimeError(f"Cluster entered {status}")
            time.sleep(20)
        raise TimeoutError("Cluster did not become ACTIVE in time")

    def _lt_version(self, lt_id):
        return str(self.ec2.describe_launch_templates(
            LaunchTemplateIds=[lt_id])["LaunchTemplates"][0]["LatestVersionNumber"])

    def _find_node_group_id(self, name):
        """Return the id of an existing compute node group with this name, or None."""
        try:
            for ng in self.pcs.list_compute_node_groups(
                    clusterIdentifier=self.cluster_id).get("computeNodeGroups", []):
                if ng.get("name") == name:
                    return ng["id"]
        except ClientError:
            pass
        return None

    def _ensure_node_group(self, name, launch_template_id, min_count, max_count, instance_type):
        """Find-or-create a compute node group; returns its id."""
        existing = self._find_node_group_id(name)
        if existing:
            print(f"Node group '{name}' already exists ({existing}); reusing.")
            return existing
        resp = self.pcs.create_compute_node_group(
            clusterIdentifier=self.cluster_id,
            computeNodeGroupName=name,
            amiId=self.ami_id,
            subnetIds=[self.subnet_id],
            iamInstanceProfileArn=self.instance_profile_arn,
            customLaunchTemplate={"id": launch_template_id,
                                  "version": self._lt_version(launch_template_id)},
            scalingConfiguration={"minInstanceCount": min_count, "maxInstanceCount": max_count},
            instanceConfigs=[{"instanceType": instance_type}],
        )
        ng_id = resp["computeNodeGroup"]["id"]
        print(f"Creating node group '{name}' ({ng_id}) ...")
        return ng_id

    def create_node_groups(self):
        """Idempotent: create (or reuse) a single-node login group and a compute group."""
        self.login_node_group_id = self._ensure_node_group(
            "login", self.login_lt_id, 1, 1, self.login_instance_type)
        self.compute_node_group_id = self._ensure_node_group(
            "compute", self.compute_lt_id, 0, self.compute_max_nodes, self.compute_instance_type)
        print(f"Node groups: login={self.login_node_group_id}, compute={self.compute_node_group_id}")
        self._wait_node_group_active(self.login_node_group_id)
        self._wait_node_group_active(self.compute_node_group_id)
        return self.login_node_group_id, self.compute_node_group_id

    def _wait_node_group_active(self, ng_id, timeout=1800):
        start = time.time()
        while time.time() - start < timeout:
            status = self.pcs.get_compute_node_group(
                clusterIdentifier=self.cluster_id, computeNodeGroupIdentifier=ng_id)["computeNodeGroup"]["status"]
            print(f"  node group {ng_id} status:", status)
            if status == "ACTIVE":
                return
            if status in ("CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"):
                raise RuntimeError(f"Node group {ng_id} entered {status}")
            time.sleep(20)
        raise TimeoutError("Node group did not become ACTIVE in time")

    def _find_queue_id(self, queue_name):
        """Return the id of an existing queue with this name, or None."""
        try:
            for q in self.pcs.list_queues(
                    clusterIdentifier=self.cluster_id).get("queues", []):
                if q.get("name") == queue_name:
                    return q["id"]
        except ClientError:
            pass
        return None

    def create_queue(self, queue_name="big"):
        """Idempotent: create (or reuse) the Slurm partition/queue for the compute node group."""
        existing = self._find_queue_id(queue_name)
        if existing:
            self.queue_id = existing
            print(f"Queue '{queue_name}' already exists ({self.queue_id}); reusing.")
            return self.queue_id
        resp = self.pcs.create_queue(
            clusterIdentifier=self.cluster_id,
            queueName=queue_name,
            computeNodeGroupConfigurations=[{"computeNodeGroupId": self.compute_node_group_id}],
        )
        self.queue_id = resp["queue"]["id"]
        print(f"Queue '{queue_name}': {self.queue_id}")
        return self.queue_id

    # ---------- teardown ----------
    def cleanup(self, delete_efs=False):
        """Delete PCS resources in dependency order, then optionally the EFS."""
        try:
            if self.queue_id:
                self.pcs.delete_queue(clusterIdentifier=self.cluster_id, queueIdentifier=self.queue_id)
                time.sleep(10)
        except Exception as e:
            print("queue delete:", e)
        for ng in [self.compute_node_group_id, self.login_node_group_id]:
            try:
                if ng:
                    self.pcs.delete_compute_node_group(clusterIdentifier=self.cluster_id,
                                                       computeNodeGroupIdentifier=ng)
            except Exception as e:
                print("node group delete:", e)
        time.sleep(30)
        try:
            if self.cluster_id:
                self.pcs.delete_cluster(clusterIdentifier=self.cluster_id)
        except Exception as e:
            print("cluster delete:", e)
        if delete_efs and self.efs_id:
            try:
                for mt in self.efs.describe_mount_targets(FileSystemId=self.efs_id)["MountTargets"]:
                    self.efs.delete_mount_target(MountTargetId=mt["MountTargetId"])
                time.sleep(30)
                self.efs.delete_file_system(FileSystemId=self.efs_id)
            except Exception as e:
                print("efs delete:", e)
        print("Cleanup requested. Verify in the PCS console that resources are deleted.")
