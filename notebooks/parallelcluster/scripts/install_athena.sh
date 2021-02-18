#!/bin/bash

set -e 

#get and build hdf5, which is required by athena++
cd /shared
wget https://hdf-wordpress-1.s3.amazonaws.com/wp-content/uploads/manual/HDF5/HDF5_1_12_0/source/hdf5-1.12.0.tar
tar xf hdf5-1.12.0.tar
cd hdf5-1.12.0
./configure --enable-parallel --enable-shared
make 
make install

# get athena++ , configure and build it
cd /shared
git clone https://github.com/PrincetonUniversity/athena-public-version
cd athena-public-version
# configure for different problem types
# prob: blast, orszag_tang, disk, jet, kh, shock_tube, ... for a complete list, check src/pgen/
#
python configure.py --prob orszag_tang -b --flux hlld -omp -mpi -hdf5 --hdf5_path=/shared/hdf5-1.12.0/hdf5
make





