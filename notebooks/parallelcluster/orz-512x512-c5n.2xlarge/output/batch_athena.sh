#!/bin/bash
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=4
###SBATCH --cpus-per-task=1
#SBATCH --account=dept-2d
#SBATCH --partition=big
#SBATCH --job-name=orz-512x512-c5n.2xlarge

CPU=$SLURM_NTASKS
# output can be hdf5 ot vtk
OUTPUT_FORMAT=hdf5
TYPE=orszag_tang
RUNMAX=1
WORKDIR=/shared/tmp/orz-512x512-c5n.2xlarge
BUCKET_NAME=mypc5c-272202070856
PREFIX=athena/orz-512x512-c5n.2xlarge
OUTPUT_FOLDER=output/
USE_EFA=NO

cd $WORKDIR
export EXE=/shared/athena-public-version/bin/athena
##export OMP_NUM_THREADS=1

module purge
module load openmpi
module load libfabric-aws

export LD_LIBRARY_PATH=/shared/hdf5-1.12.0/hdf5/lib:$LD_LIBRARY_PATH
echo $LD_LIBRARY_PATH

echo "Athena simulation on AWS started on `date +%H:%M:%S--%m/%d/%y`"
echo "        Nodes: $SLURM_NODELIST"
echo "        Number of nodes: $SLURM_JOB_NUM_NODES"
echo "        Number of threads per task: $OMP_NUM_THREADS"
echo "        Number of tasks per node: $SLURM_NTASKS_PER_NODE"
echo "        JOB Name: $SLURM_JOB_NAME"
echo "        mpirun: `which mpirun`"
echo "        EXE: `which $EXE`"
echo "        #Threads: $OMP_NUM_THREADS" 

# patch alinux for a bug
# mpirun sudo bash -c 'echo 5128 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages'

for((RUN=1; RUN<=$RUNMAX; RUN++))
do
   if [ "NO" = "YES" ]
   then
        echo "With EFA          Iter.:$RUN   #MPI Proc.:$CPU   TYPE:$TYPE "      
        mpirun $EXE -i  athinput_orszag_tang.input >& $TYPE.$CPU.$OMP_NUM_THREADS.$RUN.out
        line=`cat $TYPE.$CPU.$OMP_NUM_THREADS.$RUN.out | grep 'zone-cycles/cpu_second'`
        echo "$line"
   else
        echo " NO EFA:          Iter.:$RUN   #MPI Proc.:$CPU   TYPE:$TYPE "      
        mpirun -mca mtl_ofi_provider_exclude efa -mca mtl ^ofi $EXE -i athinput_orszag_tang.input >& $TYPE.$CPU.$OMP_NUM_THREADS.$RUN.out
        line=`cat $TYPE.$CPU.$OMP_NUM_THREADS.$RUN.out | grep 'zone-cycles/cpu_second'`
        echo "$line"
   fi
done

echo  " Athena simulation on AWS done on `date +%H:%M:%S--%m/%d/%y`"

if [ "${OUTPUT_FORMAT}" = "vtk" ]
then
    ## now combine vtk from different mashblocks into single blocks
    list=$(ls | grep block |grep vtk)

    MAXBLOCK_ID=0
    MAX_OUTSTEP=0
    for l in $list
    do
      PROB_ID=$(echo $l |cut -d '.' -f 1)
      OUTPUT_ID=$(echo $l| cut -d '.' -f 3 |cut --complement -c 1-3)

      mb_id=$(echo $l| cut -d '.' -f 2 | cut --complement -c 1-5)
      MAXBLOCK_ID=$(( mb_id > MAXBLOCK_ID? mb_id: MAXBLOCK_ID))

      os_id=$(echo $l| cut -d '.' -f 4)
      os_id=$((10#$os_id))
      MAX_OUTSTEP=$(( os_id > MAX_OUTSTEP? os_id: MAX_OUTSTEP))
    done

    cp /shared/athena-public-version/vis/vtk/join_* .
    gcc -o join_vtk++ join_vtk++.c
    ./join_all_vtk.sh $PROB_ID $OUTPUT_ID $MAXBLOCK_ID $MAX_OUTSTEP
fi

# copy the output files to S3 , excluding the block files if using vtk
aws s3 cp . s3://$BUCKET_NAME/$PREFIX/$OUTPUT_FOLDER/ --recursive --exclude "*.block*"

