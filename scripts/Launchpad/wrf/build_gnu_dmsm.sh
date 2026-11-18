#!/usr/bin/env bash
# Build WRF 4.8 (em_real) as dm+sm (MPI + OpenMP) with gfortran 13 and
# -O3 -march=sapphirerapids, against the system netCDF (/usr) and Open MPI
# 4.1.6, into outputs/wrf/build/WRF-gnu-dmsm; installs
# outputs/wrf/bin/wrf_gnu_dmsm.exe (the BUILD=gnu_dmsm of runwrf.py).
#
#   scripts/Launchpad/wrf/build_gnu_dmsm.sh        # about 25 min with -j 48
#
# The source is a copy of libs/clone/WRF (with its submodules), so the
# shared clone is never reconfigured.

set -euo pipefail
SCC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
B="$SCC/outputs/wrf/build/WRF-gnu-dmsm"

rm -rf "$B"
mkdir -p "$(dirname "$B")" "$SCC/outputs/wrf/bin"
cp -a "$SCC/libs/clone/WRF" "$B"
cd "$B"
rm -rf _build
./clean -a >/dev/null 2>&1 || true

export PATH="/usr/bin:$PATH"          # system mpif90 (Open MPI 4.1.6), not HPC-X
export NETCDF=/usr NETCDF_classic=1
printf '35\n1\n' | ./configure        # 35 = GNU (gfortran/gcc) dm+sm, 1 = basic nesting
# Ubuntu keeps libnetcdff in /usr/lib/x86_64-linux-gnu, which configure misses
sed -i 's#-lwrfio_nf -L/usr/lib\b.*#-lwrfio_nf -L/usr/lib/x86_64-linux-gnu -lnetcdff -lnetcdf#' configure.wrf
sed -i 's/^FCOPTIM .*/FCOPTIM         =       -O3 -march=sapphirerapids -mtune=sapphirerapids -ftree-vectorize -funroll-loops/;
        s/^CFLAGS_LOCAL .*/CFLAGS_LOCAL    =       -w -O3 -march=sapphirerapids -c/' configure.wrf
J="-j ${JOBS:-48}" ./compile em_real > compile.log 2>&1
cp main/wrf.exe "$SCC/outputs/wrf/bin/wrf_gnu_dmsm.exe"
echo "installed outputs/wrf/bin/wrf_gnu_dmsm.exe"
