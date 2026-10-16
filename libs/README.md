# libs/

External code and installs (mostly gitignored; see the root README).

| Path | Contents |
|---|---|
| `spack/` | Spack (submodule). Set up by `setup_spack.sh`; installs go to `spack-opt/`. |
| `spack-opt/` | Spack install tree (HPL, Intel oneAPI MPI/MKL/compilers, netCDF, ...). |
| `clone/` | Sources: MFC, WRF, WPS (submodules), plus downloads (HPL tarball, jasper, OpenBLAS, Open MPI) cloned by HPC-Tools' `basic_cmake_*`. |
| `build/` | Out-of-tree build directories (`$BUILD_DIR`). |
| `opt/` | HPC-Tools installs (`$INSTALL_DIR`): e.g. `OpenBLAS`, `openmpi-5.0.11`, `jasper`, `WRF`, `WPS`. Load with `add_build` or `scripts/Launchpad/deps/depsenv.sh`. |
