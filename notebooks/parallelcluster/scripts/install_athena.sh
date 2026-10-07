#!/bin/bash

set -e

# Athena++ is configured for VTK output, so HDF5 is NOT required. This removes the
# slow HDF5 source download + parallel build that previously dominated setup time.

# get athena++ , configure and build it
cd /shared
git clone https://github.com/PrincetonUniversity/athena-public-version
cd athena-public-version
# configure for different problem types
# prob: blast, orszag_tang, disk, jet, kh, shock_tube, ... for a complete list, check src/pgen/
#
# Output is VTK (set in the athinput file), so no -hdf5 flag / HDF5 path is needed.
python configure.py --prob orszag_tang -b --flux hlld -omp -mpi
make
