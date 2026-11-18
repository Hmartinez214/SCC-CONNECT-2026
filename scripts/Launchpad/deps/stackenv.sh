#!/usr/bin/env bash
# Compiler + MPI environment for the MFC / WRF library stacks, and the
# libraries buildstack.sh installed into it. Must be sourced:
#
#   source scripts/Launchpad/deps/stackenv.sh cpu   # Intel icx/ifx + Open MPI built on HPC-X UCX/UCC/HCOLL
#   source scripts/Launchpad/deps/stackenv.sh gpu   # icx (C) + nvfortran + HPC-X's CUDA-aware Open MPI
#
# Stacks install into libs/opt/<stack>-intel-hpcx/<lib>. Override the SDK
# with NVHPC_VER (default 26.9) / NVHPC_CUDA (13.3); STACK_HPCX=local uses the
# standalone HPC-X from installhpcx.sh instead of the SDK copy.
#
# Why two stacks: HPC-X's Open MPI has nvfortran-compiled Fortran modules, so
# ifx cannot use them (CPU stack rebuilds Open MPI with Intel compilers on
# HPC-X's UCX/UCC/HCOLL). MFC on the GPU is OpenACC, which needs nvfortran,
# so the GPU stack keeps HPC-X's CUDA-aware MPI and builds Fortran interfaces
# (netcdf-fortran, pnetcdf) with nvfortran; C libraries use icx in both.

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "Error: this script must be sourced: source $0 cpu|gpu"
    exit 1
fi

STACK="${1:-}"
case "$STACK" in cpu|gpu) ;; *) echo "stackenv: usage: source stackenv.sh cpu|gpu"; return 1 ;; esac

SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
source "$SCC_DIR/HPC-Tools/add_build.sh"
source "$SCC_DIR/HPC-Tools/basic_build.sh"

NVHPC="/opt/nvidia/hpc_sdk/Linux_x86_64/${NVHPC_VER:-26.9}"
# HPC-Tools directories: one tree per stack (autotools build in the source dir).
export INSTALL_DIR="$SCC_DIR/libs/opt/$STACK-intel-hpcx"

# STACK_HPCX=local: the standalone HPC-X from installhpcx.sh (libs/opt/hpcx);
# sdk: the NVHPC SDK copy. Unset: whichever the stack was built with
# (buildstack.sh records it in <stack>-intel-hpcx/.hpcx), else sdk.
if [[ -z ${STACK_HPCX:-} && -f "$INSTALL_DIR/.hpcx" ]]; then STACK_HPCX="$(<"$INSTALL_DIR/.hpcx")"; fi
export STACK_HPCX="${STACK_HPCX:-sdk}"
if [[ $STACK_HPCX == local ]]; then export HPCX_HOME="$SCC_DIR/libs/opt/hpcx"
else export HPCX_HOME="$NVHPC/comm_libs/${NVHPC_CUDA:-13.3}/hpcx/latest"; fi
export BUILD_DIR="$SCC_DIR/libs/build/$STACK-intel-hpcx"
export CLONE_DIR="$BUILD_DIR/src"

# Intel compilers (not setvars.sh: that would also put Intel MPI first).
_u=$-; set +u
source /opt/intel/oneapi/compiler/latest/env/vars.sh >/dev/null 2>&1
source /opt/intel/oneapi/mkl/latest/env/vars.sh >/dev/null 2>&1
# HPC-X: UCX, UCC, HCOLL, SHARP (+ its Open MPI on gpu). On cpu only their
# runtime libraries: hpcx_load would also put HPC-X's own Open MPI and its
# pkg-config / LIBRARY_PATH entries (whose .la files break libtool) first.
source "$HPCX_HOME/hpcx-init.sh"
if [[ $STACK == gpu ]]; then
    hpcx_load
else
    export LD_LIBRARY_PATH="$HPCX_UCX_DIR/lib:$HPCX_UCX_DIR/lib/ucx:$HPCX_UCC_DIR/lib:$HPCX_UCC_DIR/lib/ucc:$HPCX_HCOLL_DIR/lib:$HPCX_SHARP_DIR/lib:${LD_LIBRARY_PATH:-}"
fi
[[ $_u == *u* ]] && set -u; unset _u

ICX_FLAGS="-O2 -xSAPPHIRERAPIDS -fp-model=precise -fPIC"   # icx defaults to fast FP; I/O libs test NaN/rounding
export LDFLAGS="-Wl,-rpath,$CMPLR_ROOT/lib"            # libimf/libsvml for icx-built libs
export CC=icx CXX=icpx
export CFLAGS="$ICX_FLAGS" CXXFLAGS="$ICX_FLAGS"

if [[ $STACK == cpu ]]; then
    export FC=ifx F77=ifx F90=ifx
    export FCFLAGS="$ICX_FLAGS" FFLAGS="$ICX_FLAGS"
    if [[ -x "$INSTALL_DIR/openmpi/bin/mpicc" ]]; then
        # Our Intel-built Open MPI, not HPC-X's (hpcx_load points OPAL_PREFIX at it).
        unset OPAL_PREFIX
        export MPI_HOME="$INSTALL_DIR/openmpi" OMPI_HOME="$INSTALL_DIR/openmpi"
        add_build "$INSTALL_DIR/openmpi" openmpi >/dev/null
    fi
else
    export PATH="$NVHPC/compilers/bin:$NVHPC/cuda/bin:$PATH"
    export LD_LIBRARY_PATH="$NVHPC/compilers/lib:$NVHPC/math_libs/lib64:$NVHPC/cuda/lib64:${LD_LIBRARY_PATH:-}"
    export CUDA_HOME="$NVHPC/cuda" CUDAToolkit_ROOT="$NVHPC/cuda"
    export FC=nvfortran F77=nvfortran F90=nvfortran
    export FCFLAGS="-O2 -tp=sapphirerapids -fPIC" FFLAGS="-O2 -tp=sapphirerapids -fPIC"   # libtool omits PIC for nvfortran
    # HPC-X's wrappers: C via icx, Fortran via its native nvfortran.
    export OMPI_CC=icx OMPI_CXX=icpx OMPI_FC=nvfortran
    export MPI_HOME="$OMPI_HOME"
fi
export MPICC=mpicc MPICXX=mpicxx MPIF77=mpif77 MPIF90=mpif90 MPIFC=mpif90

# Libraries already installed in this stack.
for _lib in zlib hdf5 pnetcdf netcdf fftw silo lapack libpng jasper; do
    [[ -d "$INSTALL_DIR/$_lib" ]] && { add_build "$INSTALL_DIR/$_lib" "$_lib" >/dev/null || true; }   # add_build returns 1 without include/
done
unset _lib
if [[ -d "$INSTALL_DIR/jasper" ]]; then export JASPERLIB="$INSTALL_DIR/jasper/lib" JASPERINC="$INSTALL_DIR/jasper/include"; fi
# WRF / WPS look for these.
export NETCDF="$INSTALL_DIR/netcdf" PNETCDF="$INSTALL_DIR/pnetcdf" HDF5="$INSTALL_DIR/hdf5"
export STACK
echo "stack $STACK: CC=$CC FC=$FC MPI=$(command -v mpicc) -> ${INSTALL_DIR#"$SCC_DIR"/}"
