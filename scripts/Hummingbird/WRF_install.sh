#!/bin/bash

set -e

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

module load ohpc
module load git
source "$REPO_DIR/setup_env.sh"

module purge

module load gnu13/13.2.0
module load openmpi5/5.0.5
module load netcdf/4.9.2
module load netcdf-fortran/4.6.1
module load cmake

export CFLAGS="-O2 -march=native"
export CXXFLAGS="$CFLAGS"
export FCFLAGS="$CFLAGS"
export FFLAGS="$FCFLAGS"
export LDFLAGS="-march=native"

basic_cmake_github https://github.com/wrf-model/WRF.git --cmake-args \
    -DCMAKE_C_COMPILER="$(which gcc)" \
    -DCMAKE_Fortran_COMPILER="$(which gfortran)" \
    -DMPI_C_COMPILER="$(which mpicc)" \
    -DMPI_Fortran_COMPILER="$(which mpif90)" \
    -DCMAKE_C_PREPROCESSOR="$(which cpp)" \
    -DCMAKE_C_PREPROCESSOR_FLAGS="-traditional" \
    -DUSE_MPI=ON \
    -DCMAKE_INSTALL_PREFIX="$HOME/SCC-CONNECT-2026/libs/opt/WRF" \
    "$HOME/SCC-CONNECT-2026/libs/clone/WRF"
