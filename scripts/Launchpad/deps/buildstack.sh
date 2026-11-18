#!/usr/bin/env bash
# Build the MFC + WRF dependency libraries with HPC-Tools' basic_cmake_github /
# basic_cmake_tarball, Intel compilers and HPC-X, as two separate stacks:
#
#   scripts/Launchpad/deps/buildstack.sh cpu            # everything, CPU stack
#   scripts/Launchpad/deps/buildstack.sh gpu            # everything, GPU stack
#   scripts/Launchpad/deps/buildstack.sh cpu hdf5 silo  # just these
#
# Then: source scripts/Launchpad/deps/stackenv.sh cpu|gpu
#
#   lib             cpu stack (libs/opt/cpu-intel-hpcx)     gpu stack (libs/opt/gpu-intel-hpcx)     used by
#   openmpi         icx/ifx on HPC-X UCX+UCC+HCOLL          (HPC-X's CUDA-aware Open MPI)           both
#                   STACK_HPCX=local: 4.1.9 from libs/opt/hpcx/sources; sdk: 5.0.11 release
#   zlib 1.3.1      icx                                     icx                                     HDF5, netCDF, libpng
#   hdf5 1.14.6     parallel, C + HL                        parallel (CUDA-aware MPI), C + HL       MFC (Silo), WRF (netCDF-4)
#   pnetcdf 1.14.1  icx + ifx                               icx + nvfortran                         WRF (io_form 11)
#   netcdf          C 4.9.3 + Fortran 4.6.2, ifx            C 4.9.3 + Fortran 4.6.2, nvfortran      WRF, WPS
#   fftw 3.3.10     AVX-512, OpenMP, MPI                    AVX-512, OpenMP, MPI                    MFC (CPU FFTs; GPU uses cuFFT)
#   silo 4.12.1-pre1 HDF5 driver                            HDF5 driver                             MFC post_process
#   lapack 3.12.0   reference LAPACK on MKL BLAS            (NVHPC SDK liblapack/libblas)           MFC post_process
#   libpng, jasper  WPS ungrib (GRIB2)                      -                                       WPS
#
# Sources and build trees: libs/build/<stack>-intel-hpcx/. Logs:
# libs/build/<stack>-intel-hpcx/logs/<lib>.log.

set -eo pipefail
STACK="${1:-}"; shift || true
case "$STACK" in cpu|gpu) ;; *) echo "usage: $0 cpu|gpu [lib ...]" >&2; exit 1 ;; esac
SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
source "$SCC_DIR/scripts/Launchpad/deps/stackenv.sh" "$STACK"

ALL=(zlib hdf5 pnetcdf netcdf-c netcdf-fortran fftw silo lapack libpng jasper)
[[ $STACK == cpu ]] && ALL=(openmpi "${ALL[@]}")
[[ $STACK == gpu ]] && ALL=(zlib hdf5 pnetcdf netcdf-c netcdf-fortran fftw silo)
targets=("$@"); [[ ${#targets[@]} -eq 0 ]] && targets=("${ALL[@]}")
mkdir -p "$BUILD_DIR/logs" "$CLONE_DIR" "$INSTALL_DIR"
echo "$STACK_HPCX" > "$INSTALL_DIR/.hpcx"   # stackenv.sh loads the same HPC-X later
CM=(-DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=ON)
P="$INSTALL_DIR"

build() {
    local lib="$1"
    case "$lib" in
    openmpi)
        [[ $STACK == cpu ]] || { echo "gpu stack uses HPC-X's Open MPI"; return 0; }
        # HPC-X's UCX/UCC/HCOLL .la files name libraries from NVIDIA's build host
        # (-liberty, librdmacm.so without the dev symlink, ...), which libtool then
        # fails to link. Build against symlink views of them without the .la files;
        # the .so files themselves resolve fine.
        local v="$BUILD_DIR/hpcx-view" d f
        for d in ucx ucc hcoll; do
            rm -rf "${v:?}/$d"; mkdir -p "$v/$d/lib"
            ln -s "$HPCX_HOME/$d/include" "$v/$d/include"
            for f in "$HPCX_HOME/$d/lib/"*; do [[ $f == *.la || $f == */pkgconfig ]] || ln -s "$f" "$v/$d/lib/"; done   # .pc files carry the real prefix
        done
        if [[ $STACK_HPCX == local ]]; then
            # Local HPC-X: its own Open MPI 4.1 sources, configured like NVIDIA's
            # build (sources/config_openmpi.log) but with icx/ifx and no CUDA.
            # Pre-placing the tarball and source tree makes basic_cmake_tarball
            # skip the download and extraction.
            local src="$HPCX_HOME/sources/openmpi-gitclone.tar.gz"
            rm -rf "$CLONE_DIR/openmpi"; mkdir -p "$CLONE_DIR/openmpi"
            cp -f "$src" "$CLONE_DIR/"
            tar -xzf "$src" -C "$CLONE_DIR/openmpi" --strip-components=1
            basic_cmake_tarball "file://$src" -n openmpi -configure ON --cmake-args \
                --with-platform=contrib/platform/mellanox/optimized \
                --with-ucx="$v/ucx" --with-ucc="$v/ucc" --with-hcoll="$v/hcoll" \
                --without-cuda --without-xpmem --with-slurm --with-libevent=internal \
                --enable-mpi-fortran=all --enable-mpi1-compatibility
        else
            basic_cmake_tarball https://download.open-mpi.org/release/open-mpi/v5.0/openmpi-5.0.11.tar.gz \
                -n openmpi -configure ON --cmake-args \
                --with-ucx="$v/ucx" --with-ucc="$v/ucc" --with-hcoll="$v/hcoll" \
                --without-cuda --with-hwloc=internal --with-libevent=internal --with-pmix=internal \
                --with-prrte=internal --enable-mpi-fortran=all --enable-mpi1-compatibility
        fi ;;
    zlib)
        basic_cmake_tarball https://github.com/madler/zlib/releases/download/v1.3.1/zlib-1.3.1.tar.gz \
            -n zlib --cmake-args "${CM[@]}" ;;
    hdf5)
        # The release tarball's paths start with "./", which basic_cmake_tarball's
        # --strip-components=1 does not strip: unpack it here (it then skips extraction).
        if [[ ! -f "$CLONE_DIR/hdf5/CMakeLists.txt" ]]; then
            rm -rf "$CLONE_DIR/hdf5"; mkdir -p "$CLONE_DIR/hdf5"
            ( cd "$CLONE_DIR" && { [[ -f hdf5-1.14.6.tar.gz ]] || wget -q https://github.com/HDFGroup/hdf5/releases/download/hdf5_1.14.6/hdf5-1.14.6.tar.gz; } \
              && tar -xzf hdf5-1.14.6.tar.gz -C hdf5 --strip-components=2 )
        fi
        basic_cmake_tarball https://github.com/HDFGroup/hdf5/releases/download/hdf5_1.14.6/hdf5-1.14.6.tar.gz \
            -n hdf5 --cmake-args "${CM[@]}" -DCMAKE_C_COMPILER=mpicc \
            -DHDF5_ENABLE_PARALLEL=ON -DHDF5_BUILD_HL_LIB=ON -DHDF5_BUILD_FORTRAN=OFF -DHDF5_BUILD_CPP_LIB=OFF \
            -DHDF5_BUILD_EXAMPLES=OFF -DBUILD_TESTING=OFF -DHDF5_BUILD_TOOLS=ON \
            -DHDF5_ENABLE_Z_LIB_SUPPORT=ON -DZLIB_ROOT="$P/zlib" -DHDF5_ENABLE_SZIP_SUPPORT=OFF \
            -DBUILD_SHARED_LIBS=ON -DBUILD_STATIC_LIBS=ON ;;
    pnetcdf)
        basic_cmake_tarball https://parallel-netcdf.github.io/Release/pnetcdf-1.14.1.tar.gz \
            -n pnetcdf -configure ON --cmake-args --enable-shared --disable-cxx \
            MPICC=mpicc MPIF77=mpif77 MPIF90=mpif90 ;;
    netcdf-c)
        basic_cmake_tarball https://downloads.unidata.ucar.edu/netcdf-c/4.9.3/netcdf-c-4.9.3.tar.gz \
            -n netcdf-c -configure ON --cmake-args --prefix="$P/netcdf" \
            CC=mpicc CPPFLAGS="-I$P/hdf5/include -I$P/zlib/include -I$P/pnetcdf/include" \
            LDFLAGS="$LDFLAGS -L$P/hdf5/lib -L$P/zlib/lib -L$P/pnetcdf/lib -Wl,-rpath,$P/hdf5/lib:$P/zlib/lib:$P/pnetcdf/lib" \
            --enable-pnetcdf --enable-parallel4 --disable-dap --disable-byterange --disable-libxml2 \
            --disable-nczarr --disable-testsets --enable-shared ;;
    netcdf-fortran)
        basic_cmake_tarball https://downloads.unidata.ucar.edu/netcdf-fortran/4.6.2/netcdf-fortran-4.6.2.tar.gz \
            -n netcdf-fortran -configure ON --cmake-args --prefix="$P/netcdf" \
            CC=mpicc FC=mpif90 F77=mpif77 CPPFLAGS="-I$P/netcdf/include" \
            LDFLAGS="$LDFLAGS -L$P/netcdf/lib -Wl,-rpath,$P/netcdf/lib" --enable-shared ;;
    fftw)
        CFLAGS="-O3 -xSAPPHIRERAPIDS -fPIC" basic_cmake_tarball https://www.fftw.org/fftw-3.3.10.tar.gz \
            -n fftw -configure ON --cmake-args MPICC=mpicc --enable-mpi --enable-openmp --enable-threads \
            --enable-shared --enable-sse2 --enable-avx --enable-avx2 --enable-avx512 --enable-fma ;;
    silo)
        basic_cmake_github https://github.com/LLNL/Silo -n silo -c 4.12.1-pre1 --cmake-args "${CM[@]}" \
            -DCMAKE_C_COMPILER=mpicc -DSILO_ENABLE_HDF5=ON -DSILO_HDF5_DIR="$P/hdf5" \
            -DSILO_ENABLE_SILOCK=OFF -DSILO_ENABLE_BROWSER=OFF -DSILO_ENABLE_ZFP=OFF -DSILO_ENABLE_SHARED=ON ;;
    lapack)
        [[ $STACK == cpu ]] || { echo "gpu stack uses the NVHPC SDK's liblapack"; return 0; }
        basic_cmake_github https://github.com/Reference-LAPACK/lapack -n lapack -c v3.12.0 --cmake-args "${CM[@]}" \
            -DBUILD_SHARED_LIBS=ON -DCBLAS=OFF -DLAPACKE=OFF -DBUILD_TESTING=OFF \
            -DUSE_OPTIMIZED_BLAS=ON -DBLA_VENDOR=Intel10_64lp_seq ;;
    libpng)
        basic_cmake_tarball https://download.sourceforge.net/libpng/libpng-1.6.50.tar.gz \
            -n libpng --cmake-args "${CM[@]}" -DPNG_TESTS=OFF -DZLIB_ROOT="$P/zlib" ;;
    jasper)
        basic_cmake_tarball https://github.com/jasper-software/jasper/releases/download/version-4.2.5/jasper-4.2.5.tar.gz \
            -n jasper --cmake-args "${CM[@]}" -DJAS_ENABLE_DOC=OFF -DJAS_ENABLE_PROGRAMS=OFF \
            -DJAS_ENABLE_OPENGL=OFF -DJAS_ENABLE_LIBJPEG=OFF -DJAS_ENABLE_LIBHEIF=OFF ;;
    *) echo "unknown lib: $lib (expected: ${ALL[*]})" >&2; return 1 ;;
    esac
}

for lib in "${targets[@]}"; do
    log="$BUILD_DIR/logs/$lib.log"
    echo "=== [$STACK] $lib  (log: ${log#"$SCC_DIR"/})"
    start=$SECONDS
    if ! ( build "$lib" ) >"$log" 2>&1; then
        echo "FAILED: $lib, see $log" >&2; tail -20 "$log" >&2; exit 1
    fi
    echo "    ok ($(( SECONDS - start )) s)"
    cd "$SCC_DIR"
    source "$SCC_DIR/scripts/Launchpad/deps/stackenv.sh" "$STACK" >/dev/null   # pick up what was just installed
done
echo "done: source scripts/Launchpad/deps/stackenv.sh $STACK"
