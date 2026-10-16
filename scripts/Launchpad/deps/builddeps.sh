#!/usr/bin/env bash
# Build OpenBLAS and Open MPI with the HPC-Tools build helpers
# (basic_cmake_github / basic_cmake_tarball from HPC-Tools/basic_build.sh).
#
# Installs into $INSTALL_DIR (libs/opt):
#   libs/opt/OpenBLAS          OpenBLAS v0.3.34 (CMake, gcc/gfortran)
#   libs/opt/openmpi-5.0.11    Open MPI 5.0.11  (configure, gcc/gfortran)
#
# Usage (from anywhere):
#   scripts/Launchpad/deps/builddeps.sh [openblas] [openmpi]   # default: both
#
# Afterwards, load them with:
#   source scripts/Launchpad/deps/depsenv.sh

SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC_DIR"

# Sets INSTALL_DIR / BUILD_DIR / CLONE_DIR and sources the HPC-Tools helpers.
# shellcheck disable=SC1091
source ./setup_env.sh
cd "$SCC_DIR"

set -eo pipefail

OPENBLAS_TAG="${OPENBLAS_TAG:-v0.3.34}"
OPENMPI_URL="${OPENMPI_URL:-https://download.open-mpi.org/release/open-mpi/v5.0/openmpi-5.0.11.tar.gz}"

# setup_env.sh exports -ffast-math; it breaks OpenBLAS/LAPACK's NaN and
# rounding handling and buys nothing for MPI. Let each project pick its flags.
unset CFLAGS CXXFLAGS FCFLAGS FFLAGS LDFLAGS
export CC=gcc CXX=g++ FC=gfortran

targets=("$@")
[[ ${#targets[@]} -eq 0 ]] && targets=(openblas openmpi)

for target in "${targets[@]}"; do
    case "$target" in
        openblas)
            # Tuned kernels for this node's Sapphire/Emerald Rapids cores.
            basic_cmake_github https://github.com/OpenMathLib/OpenBLAS.git \
                -n OpenBLAS \
                -c "$OPENBLAS_TAG" \
                --cmake-args \
                -DCMAKE_BUILD_TYPE=Release \
                -DBUILD_SHARED_LIBS=ON \
                -DTARGET=SAPPHIRERAPIDS \
                -DUSE_THREAD=ON \
                -DNUM_THREADS=128 \
                -DBUILD_TESTING=OFF
            cd "$SCC_DIR"
            add_build "$INSTALL_DIR/OpenBLAS" openblas
            ;;
        openmpi)
            basic_cmake_tarball "$OPENMPI_URL" \
                -configure ON \
                --cmake-args \
                --enable-mpi-fortran=usempif08 \
                --with-hwloc=internal \
                --with-libevent=internal \
                --with-pmix=internal
            cd "$SCC_DIR"
            add_build "$INSTALL_DIR/openmpi-5.0.11" openmpi
            ;;
        *)
            echo "Unknown target: $target (expected openblas or openmpi)" >&2
            exit 1
            ;;
    esac
done
