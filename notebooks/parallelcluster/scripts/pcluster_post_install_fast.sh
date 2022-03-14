#!/bin/bash

# terminate script if there is an error
set -e 

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
slurm_version="$7"


# pcluster version 3.1.2 uses 21.08.6
#slurm_version=20.11.8
tar_ball=workshop-pcluster3-slurm${slurm_version}-athena-hdf5.tar.gz


# the head-node is used to run slurmdbd
host_name=$(hostname)
CORES=$(grep processor /proc/cpuinfo | wc -l)
lower_name=$(echo $PCLUSTER_NAME | tr '[:upper:]' '[:lower:]')

yum update -y

# change the cluster name
sed -i 's/ClusterName=parallelcluster/ClusterName='$lower_name'/g' /opt/slurm/etc/slurm.conf
rm /var/spool/slurm.state/*

#####
#install pre-requisites for slurmrestd
#####
yum install –y epel-release
yum-config-manager --enable epel
yum install -y hdf5-devel
yum install -y libyaml http-parser-devel json-c-devel
yum install -y libjwt libjwt-devel

# update the linked libs 
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/lib64
cat > /etc/ld.so.conf.d/slurmrestd.conf <<EOF
/lib64
EOF


##### 
# Install athena and hdf5
#####
#get and build hdf5, which is required by athena++
PATH=/bin:/usr/bin/:/usr/local/bin/:/opt/amazon/openmpi/bin

# Get precompiled athena++, hdf5, slurm so the pcluster creation will be faster for workshops
cd /shared
wget https://static.myoctank.net/public/${tar_ball}
tar xvzf ${tar_ball}

cd hdf5-1.12.0
make install

cd /shared/slurm-${slurm_version}
# config and build slurm
make install
make install-contrib

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
Environment="SLURM_JWT=daemon"
ExecStart=/opt/slurm/sbin/slurmrestd -vvvv 0.0.0.0:8082 -u ec2-user
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
export \$(/opt/slurm/bin/scontrol token -u ec2-user)
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
chown ec2-user:ec2-user /shared/tmp

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




