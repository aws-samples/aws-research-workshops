#!/bin/bash

PATH="/bin:/usr/bin/:/usr/local/bin/:/opt/slurm/bin:/opt/amazon/openmpi/bin"
LD_LIBRARY_PATH="/lib/:/lib64/:/usr/local/lib:/opt/slurm/lib:/opt/slurm/lib64"

# print error message and return exit 1
error_exit () {
  echo "Fetch and run error - ${1}" >&2
  exit 1
}

# Check if aws CLI is installed 
which aws >/dev/null 2>&1 || error_exit "Please install AWS CLI first."

mkdir $5
cd $5
aws s3 cp "s3://$1/$2/$3" $3
aws s3 cp "s3://$1/$2/$4" $4

chmod +x $4

sbatch $4
