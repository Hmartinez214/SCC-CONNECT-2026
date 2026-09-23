# Environment for building and running MFC with the NVIDIA HPC SDK.
#Automatically sourced by buildmfc.sh. Source before running mfc to setupenv


MFC_NVHPC_VERSION="${MFC_NVHPC_VERSION:-25.9}"
export NVHPC="/opt/nvidia/hpc_sdk/Linux_$(uname -m)/${MFC_NVHPC_VERSION}"
 
if [ ! -x "$NVHPC/compilers/bin/nvfortran" ]; then
    echo "mfc_env: NVIDIA HPC SDK ${MFC_NVHPC_VERSION} not found at $NVHPC" >&2
    echo "mfc_env: run scripts/build_mfc.sh to install it" >&2
    return 1 2>/dev/null || exit 1
fi
 
# Prepend a directory to a PATH-style variable, skipping duplicates.
_mfc_prepend() {
    case ":${!1:-}:" in
        *":$2:"*) ;;
        *) export "$1=$2${!1:+:${!1}}" ;;
    esac
}
 
# Directory holding cuFFT and the other CUDA math libraries.
if [ -e "$NVHPC/math_libs/lib64/libcufft.so" ]; then
    _mfc_mathlib="$NVHPC/math_libs/lib64"
else
    _mfc_mathlib="$(find "$NVHPC/math_libs" -name 'libcufft.so*' -path '*lib64*' 2>/dev/null \
                    | sort -V | tail -n1)"
    _mfc_mathlib="${_mfc_mathlib%/*}"
fi
 
_mfc_prepend PATH "$NVHPC/comm_libs/mpi/bin"
_mfc_prepend PATH "$NVHPC/compilers/bin"
 
_mfc_prepend LD_LIBRARY_PATH "$NVHPC/comm_libs/mpi/lib"
_mfc_prepend LD_LIBRARY_PATH "$NVHPC/compilers/lib"
_mfc_prepend LD_LIBRARY_PATH "$NVHPC/cuda/lib64"
[ -n "$_mfc_mathlib" ] && _mfc_prepend LD_LIBRARY_PATH "$_mfc_mathlib"
 
export CC=nvc CXX=nvc++ FC=nvfortran
 
unset -f _mfc_prepend
unset _mfc_mathlib
