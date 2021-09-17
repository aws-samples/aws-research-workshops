#!/bin/bash

# terminate script if there is an error
set -e
#set -x

#####
# don't do anything else if it's compute node, just install htop
#####
. "/etc/parallelcluster/cfnconfig"

case "${cfn_node_type}" in
    ComputeFleet)
        yum install -y htop
        exit 0
    ;;
    *)
    ;;
esac


# give slurm user permission to run aws CLI
/opt/parallelcluster/scripts/imds/imds-access.sh --allow slurm

#####
#Takes the following argument, $0 is the script name, $1 is the S3 url, $2 is the first arg for rds hostname
#####
PCLUSTER_RDS_HOST="$1"
PCLUSTER_RDS_PORT="$2"
PCLUSTER_RDS_USER="$3"
PCLUSTER_RDS_PASS="$4"
PCLUSTER_NAME="$5"
REGION="$6"
FEDERATION_NAME="$8"

# the head-node is used to run slurmdbd
host_name=$(hostname)
CORES=$(grep processor /proc/cpuinfo | wc -l)
lower_name=$(echo $PCLUSTER_NAME | tr '[:upper:]' '[:lower:]')

yum update -y

# change the cluster name
sed -i 's/ClusterName=parallelcluster/ClusterName='$lower_name'/g' /opt/slurm/etc/slurm.conf
rm -rf /var/spool/slurm.state/* || true

#####
#install pre-requisites
#####
yum install –y epel-release
yum-config-manager --enable epel
yum install -y hdf5-devel
yum install -y libyaml http-parser-devel json-c-devel

# update the linked libs 
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/usr/local/lib
cat > /etc/ld.so.conf.d/slurmrestd.conf <<EOF
/usr/local/lib
/usr/local/lib64
EOF

cd /shared
# install libjwt-devel as part of the pre-requisites. libjwt-devel.so and libjwt.so will be installed in /usr/lib64
wget http://repo.openfusion.net/centos7-x86_64/libjwt-1.9.0-1.of.el7.x86_64.rpm
# ignore the libjwt already installed error
rpm -Uvh libjwt-1.9.0-1.of.el7.x86_64.rpm
wget http://repo.openfusion.net/centos7-x86_64/libjwt-devel-1.9.0-1.of.el7.x86_64.rpm
rpm -Uvh libjwt-devel-1.9.0-1.of.el7.x86_64.rpm 

#####
# Update slurm, with slurmrestd
#####
# Python3 is requred to build slurm >= 20.02, 

#source /opt/parallelcluster/pyenv/versions/3.6.9/envs/cookbook_virtualenv/bin/activate
source /opt/parallelcluster/pyenv/versions/cookbook_virtualenv/bin/activate

cd /shared
# have to use the exact same slurm version as in the released version of ParallelCluster2.10.1 - 20.02.4
# as of May 13, 20.02.4 was removed from schedmd and was replaced with .7 
# error could be seen in the cfn-init.log file
# changelog: change to 20.11.7 from 20.02.7 on 2021/09/03 - pcluster 2.11.2 
slurm_version=20.11.8
wget https://download.schedmd.com/slurm/slurm-${slurm_version}.tar.bz2
tar xjf slurm-${slurm_version}.tar.bz2
cd slurm-${slurm_version}

# config and build slurm
./configure --prefix=/opt/slurm --with-pmix=/opt/pmix
make -j $CORES
make install
make install-contrib
deactivate

# set the jwt key
openssl genrsa -out /var/spool/slurm.state/jwt_hs256.key 2048
chown slurm /var/spool/slurm.state/jwt_hs256.key
chmod 0700 /var/spool/slurm.state/jwt_hs256.key

# add 'AuthAltTypes=auth/jwt' to /opt/slurm/etc/slurm.conf
cat >>/opt/slurm/etc/slurm.conf<<EOF
# Enable jwt auth for Slurmrestd
AuthAltTypes=auth/jwt
#
## /opt/slurm/etc/slurm.conf
#
# ACCOUNTING
JobAcctGatherType=jobacct_gather/linux
JobAcctGatherFrequency=30
#
AccountingStorageType=accounting_storage/slurmdbd
AccountingStorageHost=${host_name} # cluster headnode's DNS
AccountingStorageUser=${PCLUSTER_RDS_USER}
AccountingStoragePort=6839

EOF

# 

cat >/opt/slurm/etc/slurmdbd.conf<<EOF
ArchiveEvents=yes
ArchiveJobs=yes
ArchiveResvs=yes
ArchiveSteps=no
ArchiveSuspend=no
ArchiveTXN=no
ArchiveUsage=no
AuthType=auth/munge
DbdHost="${host_name}" #YOUR_MASTER_IP_ADDRESS_OR_NAME
DbdPort=6839
DebugLevel=info
PurgeEventAfter=1month
PurgeJobAfter=12month
PurgeResvAfter=1month
PurgeStepAfter=1month
PurgeSuspendAfter=1month
PurgeTXNAfter=12month
PurgeUsageAfter=24month
SlurmUser=slurm
LogFile=/var/log/slurmdbd.log
PidFile=/var/run/slurmdbd.pid
StorageType=accounting_storage/mysql
StorageUser=${PCLUSTER_RDS_USER}
StoragePass=${PCLUSTER_RDS_PASS}
StorageHost=${PCLUSTER_RDS_HOST} # Endpoint from RDS console
StoragePort=${PCLUSTER_RDS_PORT}
EOF

chown slurm /opt/slurm/etc/slurmdbd.conf 
chmod 600 /opt/slurm/etc/slurmdbd.conf

# create the slurmrestd.conf file
cat >/opt/slurm/etc/slurmrestd.conf<<EOF
include /opt/slurm/etc/slurm.conf
AuthType=auth/jwt
EOF

# 
/opt/slurm/sbin/slurmdbd

#####
# Enable slurmrestd to run as a service
# ExecStart=/opt/slurm/sbin/slurmrestd -vvvv 0.0.0.0:8082 -a jwt -u slurm
# slurm20.02.4 version doesn't support -a command line option. 
#
#####
cat >/etc/systemd/system/slurmrestd.service<<EOF
[Unit]
Description=Slurm restd daemon
After=network.target slurmctl.service
ConditionPathExists=/opt/slurm/etc/slurmrestd.conf

[Service]
Environment=SLURM_CONF=/opt/slurm/etc/slurmrestd.conf
ExecStart=/opt/slurm/sbin/slurmrestd -vvvv 0.0.0.0:8082 -u slurm
PIDFile=/var/run/slurmrestd.pid

[Install]
WantedBy=multi-user.target
EOF
##### restart the daemon and start the slurmrestd
systemctl daemon-reload
systemctl start slurmrestd

# restart slurmctd  - this needs to be done after slurmdbd start, otherwise the cluster won't register
systemctl restart slurmctld


## initialize the sacctmgr - this is done automatically by slurmdbd
##/opt/slurm/bin/sacctmgr add cluster parallelcluster

#####
# create a script to periodically save the slurm token in secret manager
#####
cat >/shared/token_refresher.sh<<EOF
#!/bin/bash
export \$(/opt/slurm/bin/scontrol token -u slurm)
aws secretsmanager describe-secret --secret-id slurm_token_${PCLUSTER_NAME} --region $REGION
if [ \$? -eq 0 ]
then
 aws secretsmanager update-secret --secret-id slurm_token_${PCLUSTER_NAME} --secret-string "\$SLURM_JWT" --region $REGION
else
 aws secretsmanager create-secret --name slurm_token_${PCLUSTER_NAME} --secret-string "\$SLURM_JWT" --region $REGION
fi
EOF

chmod +x /shared/token_refresher.sh

# add a cron job to run the token refresher every 20 mins
cat >/etc/cron.d/slurm-token<<EOF
# Run the slurm token update every 20 minue 
SHELL=/bin/bash
PATH=/sbin:/bin:/usr/sbin:/usr/bin
MAILTO=root
*/20 * * * * root /shared/token_refresher.sh 
EOF

#####
# we will be using /shared/tmp for running our program nad store the output files. 
#####
mkdir -p /shared/tmp
chown slurm:slurm /shared/tmp

cat >/shared/tmp/fetch_and_run.sh<<EOF
#!/bin/bash

PATH="/bin:/usr/bin/:/usr/local/bin/:/opt/slurm/bin:/opt/amazon/openmpi/bin"
LD_LIBRARY_PATH="/lib/:/lib64/:/usr/local/lib:/opt/slurm/lib:/opt/slurm/lib64"
# print error message and return exit 1
error_exit () {
  echo "Fetch and run error - \${1}" >&2
  exit 1
}
# Check if aws CLI is installed 
which aws >/dev/null 2>&1 || error_exit "Please install AWS CLI first."
# arg1-bucketname arg2-prefix arg3-input_filename arg4-program_filename arg5-program_folder
mkdir -p \$5
cd \$5
aws s3 cp "s3://\$1/\$2/\$3" \$3
aws s3 cp "s3://\$1/\$2/\$4" \$4
chmod +x \$4
sbatch \$4
EOF

chmod +x /shared/tmp/fetch_and_run.sh

# create the slurm token - the role permission with SecretManagerReadWrite must be added in the config file
# in the cluster section with additional_iam_policies = arn:aws:iam::aws:policy/SecretsManagerReadWrite
/shared/token_refresher.sh 


if [ $FEDERATION_NAME ]
then
    # store the munge.key in secret manager so the other cluster can use it, ignore if it doesn't exist
    aws secretsmanager delete-secret --secret-id munge_key_$FEDERATION_NAME --force-delete-without-recovery --region $REGION | true
    aws secretsmanager create-secret --name munge_key_$FEDERATION_NAME --secret-binary fileb:///etc/munge/munge.key --region $REGION
fi

systemctl restart slurmctld

cat >/shared/tmp/batch_test.sh <<EOF
#!/bin/bash
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=4
#SBATCH --partition=q1
#SBATCH --job-name=test

cd /shared/tmp

srun hostname
srun sleep 60
EOF

chown slurm /shared/tmp/batch_test.sh



