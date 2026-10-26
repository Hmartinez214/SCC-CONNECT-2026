#!bin/bash

source ./setup_env.sh
source ./setup_spack.sh

add_build /opt/nvidia/hpc_sdk/Linux_x86_64/25.9/compilers nvidia_compilers
add_build /opt/nvidia/hpc_sdk/Linux_x86_64/25.9/cuda nvidia_cuda
add_build /opt/nvidia/hpc_sdk/Linux_x86_64/25.9/math_libs nvidia_math

export HPCX_HOME=/opt/nvidia/hpc_sdk/Linux_x86_64/25.9/comm_libs/13.0/hpcx/hpcx-2.24
source $HPCX_HOME/hpcx-init.sh
hpcx_load

export CC=nvc
export CXX=nvc++
export FC=nvfortran
export CFLAGS="-O3 -fast -Minfo=accel"
export CXXFLAGS="$CFLAGS"
export FCFLAGS="$CFLAGS"

export CUDA_HOME=/opt/nvidia/hpc_sdk/Linux_x86_64/25.9/cuda/13.0
export CUDAToolkit_ROOT="$CUDA_HOME"
export CUDA_PATH="$CUDA_HOME"
export PATH="$CUDA_HOME/bin:$PATH"

spack compiler find
spack external find openmpi

# spack load intel-oneapi-mkl