#!/bin/bash

# First run "source ./launchpad_env.sh" from SCC-CONNECT-2026

spack install netcdf-c %nvhpc netcdf-fortran %nvhpc hdf5 %nvhpc
spack install parallel-netcdf %nvhpc ^openmpi@4.1.9a1

add_build "$(spack location -i netcdf-c %nvhpc)"         netcdf-c
add_build "$(spack location -i netcdf-fortran %nvhpc)"   netcdf-fortran
add_build "$(spack location -i parallel-netcdf %nvhpc ^openmpi@4.1.9a1)"  parallel-netcdf
add_build "$(spack location -i hdf5 %nvhpc)"             hdf5

rm -rf "$BUILD_DIR/WRF"

basic_cmake_github https://github.com/wrf-model/WRF \
    --name WRF \
    --cmake-args \
    -DWRF_CORE=ARW \
    -DWRF_NESTING=NONE \
    -DCMAKE_C_PREPROCESSOR=/usr/bin/cpp \
    -DWRF_CASE=EM_REAL \
    -DUSE_MPI=ON \
    -DUSE_OPENMP=ON \
    -DUSE_DOUBLE=OFF \
    -DUSE_PNETCDF=ON

spack install jasper %nvhpc