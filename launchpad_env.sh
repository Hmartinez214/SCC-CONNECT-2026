#!bin/bash

source ./setup_env.sh

add_build /opt/nvidia/hpc_sdk/Linux_x86_64/2026/compilers nvidia_compilers
add_build /opt/nvidia/hpc_sdk/Linux_x86_64/2026/cuda nvidia_cuda
add_build /opt/nvidia/hpc_sdk/Linux_x86_64/2026/math_libs nvidia_math

export CC=nvcc
export CXX=nvc++
export FC=nvfortran