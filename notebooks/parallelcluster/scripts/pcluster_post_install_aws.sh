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
FEDERATION_NAME="$9"

# the head-node is used to run slurmdbd
if [ $8 == 'localhost' ]
then
  host_name=$(hostname)
else
  host_name="$8"
fi

#CORES=$(grep processor /proc/cpuinfo | wc -l)
lower_name=$(echo $PCLUSTER_NAME | tr '[:upper:]' '[:lower:]')

yum update -y
# change the cluster name
sed -i 's/ClusterName=parallelcluster/ClusterName='$lower_name'/g' /opt/slurm/etc/slurm.conf
rm /var/spool/slurm.state/*

# update the linked libs 
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/usr/local/lib
cat > /etc/ld.so.conf.d/slurmrestd.conf <<EOF
/lib64
EOF


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
AccountingStoragePort=6839

EOF


if [ $FEDERATION_NAME ]
then
    # store the munge.key in secret manager so the other cluster can use it
    aws secretsmanager get-secret-value --secret-id munge_key_$FEDERATION_NAME --region $REGION --query 'SecretBinary' |tr -d '"'| base64 --decode > /etc/munge/munge.key
    cp /etc/munge/munge.key /home/ec2-user/.munge/.munge.key
fi

##### restart the daemon and start the slurmrestd
systemctl daemon-reload
systemctl restart munge

# activate the cluster in sacctmgr
systemctl restart slurmctld

mkdir -p /shared/tmp
chown slurm /shared/tmp

cat <<EOF>/shared/tmp/batch_test.sh
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




