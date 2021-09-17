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
FEDERATION_NAME="$8"

# the head-node is used to run slurmdbd
if [ $7 == 'localhost' ]
then
  host_name=$(hostname)
else
  host_name="$7"
fi

CORES=$(grep processor /proc/cpuinfo | wc -l)
lower_name=$(echo $PCLUSTER_NAME | tr '[:upper:]' '[:lower:]')

yum update -y
# change the cluster name
sed -i 's/ClusterName=parallelcluster/ClusterName='$lower_name'/g' /opt/slurm/etc/slurm.conf
rm /var/spool/slurm.state/*

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
rpm -Uvh libjwt-1.9.0-1.of.el7.x86_64.rpm || true
wget http://repo.openfusion.net/centos7-x86_64/libjwt-devel-1.9.0-1.of.el7.x86_64.rpm
rpm -Uvh libjwt-devel-1.9.0-1.of.el7.x86_64.rpm || true




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




