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

spack install libpng %nvhpc
# Clone
git clone https://github.com/jasper-software/jasper.git \
    $CLONE_DIR/jasper

cd $CLONE_DIR/jasper
# Checkout the WPS-compatible version
git checkout version-1.900.29
# Generate the Autotools configure script
autoreconf -fi
# Fix old JasPer declarations incompatible with modern GCC
sed -i \
    's/jas_image_t \*jpg_decode(jas_stream_t \*in, char \*optstr)/jas_image_t *jpg_decode(jas_stream_t *in, const char *optstr)/' \
    src/libjasper/jpg/jpg_dummy.c
sed -i \
    's/int jpg_encode(jas_image_t \*image, jas_stream_t \*out, char \*optstr)/int jpg_encode(jas_image_t *image, jas_stream_t *out, const char *optstr)/' \
    src/libjasper/jpg/jpg_dummy.c
# Then let the generic helper do the actual build/install
CC=gcc CFLAGS="-O3" \
basic_cmake_github https://github.com/jasper-software/jasper.git \
    --name jasper \
    --checkout version-1.900.29 \
    -configure ON

add_build "$(spack location -i libpng %nvhpc)"            libpng
add_build $INSTALL_DIR/jasper jasper
add_build $INSTALL_DIR/WRF WRF
basic_cmake_github https://github.com/wrf-model/WPS.git \
    --name WPS \
    --cmake-args \
    -DWPS_DEFINITIONS="-D_UNDERSCORE -DBYTESWAP -DLINUX -DIO_NETCDF -DBIT32 -DNO_SIGNAL"