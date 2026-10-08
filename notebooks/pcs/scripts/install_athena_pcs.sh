#!/bin/bash
#
# Build Athena++ (VTK output, no HDF5) into the shared filesystem on an AWS PCS node.
# Run this on the PCS LOGIN node (e.g. via SSM Session Manager) if the automatic
# launch-template build did not produce /shared/athena-public-version/bin/athena.
#
# Why this exists: the PCS AL2023 sample AMI ships the EFA OpenMPI stack under
# /opt/amazon/openmpi, but 'module load openmpi' is not configured the way it is on
# the ParallelCluster AMI. A plain 'make' then fails with "mpicxx: No such file or
# directory". This script locates mpicxx directly and fails loudly if MPI is missing.

set -euxo pipefail

SHARED_DIR="${SHARED_DIR:-/shared}"

sudo yum install -y git gcc gcc-c++ make python3 || true

# ---- locate the MPI C++ wrapper ----
# 'module load openmpi' works interactively but may be a no-op in a non-interactive
# shell (the 'module' function is set up by /etc/profile.d, which such shells don't
# source). Source the module init if present, then fall back to the known PCS AMI path
# /opt/amazon/openmpi/bin where mpicxx actually lives.
for mi in /etc/profile.d/modules.sh /usr/share/Modules/init/bash; do
  # shellcheck disable=SC1090
  [ -f "$mi" ] && . "$mi" && module load openmpi 2>/dev/null || true
done
for d in /opt/amazon/openmpi/bin /usr/lib64/openmpi/bin /usr/bin; do
  if [ -x "$d/mpicxx" ]; then export PATH="$d:$PATH"; break; fi
done
if ! command -v mpicxx >/dev/null 2>&1; then
  echo "ERROR: mpicxx not found. Searched /opt/amazon/openmpi/bin, /usr/lib64/openmpi/bin, /usr/bin." >&2
  echo "Check 'module avail' and the EFA/OpenMPI install on this AMI." >&2
  exit 1
fi
echo "Using MPI: $(command -v mpicxx)"

# ---- clone + build ----
cd "${SHARED_DIR}"
[ -d athena-public-version ] || git clone https://github.com/PrincetonUniversity/athena-public-version
cd athena-public-version

# Clean any stale/partial build so a previously empty bin/athena self-heals.
make clean 2>/dev/null || true

# GCC 13 on Amazon Linux 2023 no longer transitively includes <limits>, so this older
# Athena++ fails with "'numeric_limits' is not a member of 'std'". Inject '#include
# <limits>' into any source that uses std::numeric_limits but doesn't already include it.
for f in $(grep -rl 'std::numeric_limits' src/ 2>/dev/null); do
  if ! grep -q '#include <limits>' "$f"; then
    # insert after the first #include line in the file
    sed -i '0,/^#include/s//#include <limits>\n#include/' "$f"
    echo "patched <limits> into $f"
  fi
done

# VTK output -> no -hdf5 flag / HDF5 path needed (matches the ParallelCluster lab build).
python3 configure.py --prob orszag_tang -b --flux hlld -omp -mpi
make -j "$(nproc)"

test -x bin/athena && echo "Athena++ built successfully at ${SHARED_DIR}/athena-public-version/bin/athena"
