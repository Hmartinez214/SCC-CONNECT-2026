#!bin/bash

source ./setup_env.sh
source ./setup_spack.sh

add_build /opt/nvidia/hpc_sdk/Linux_x86_64/26.9/compilers nvidia_compilers
add_build /opt/nvidia/hpc_sdk/Linux_x86_64/26.9/cuda nvidia_cuda
add_build /opt/nvidia/hpc_sdk/Linux_x86_64/26.9/math_libs nvidia_math
add_build /opt/nvidia/hpc_sdk/Linux_x86_64/26.9/comm_libs/hpcx hpcx

export CC=nvc
export CXX=nvc++
export FC=nvfortran
export CFLAGS="-O3 -fast -Minfo=accel"
export CXXFLAGS="$CFLAGS"
export FCFLAGS="$CFLAGS"

export HPCX_HOME=/opt/nvidia/hpc_sdk/Linux_x86_64/25.9/comm_libs/13.0/hpcx/hpcx-2.24
source $HPCX_HOME/hpcx-init.sh
hpcx_load

spack compiler find
spack external find openmpi