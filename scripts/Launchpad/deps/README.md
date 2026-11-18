# deps/

- `builddeps.sh [openblas] [openmpi]`: build OpenBLAS v0.3.34 and Open MPI 5.0.11 into `libs/opt/` with HPC-Tools' `basic_cmake_github` / `basic_cmake_tarball`.
- `depsenv.sh`: `source` it to put them on PATH/LD_LIBRARY_PATH/CPATH (adds OpenBLAS's `include/openblas`).
- `installoneapi.sh [--toolkits]`: install Intel oneAPI (icx/ifx, Intel MPI, MKL; or the full toolkits) system-wide from Intel's apt repo. Load with `source /opt/intel/oneapi/setvars.sh`.

## MFC + WRF library stacks (Intel compilers + HPC-X)

- `buildstack.sh cpu|gpu [lib ...]`: build the MFC and WRF dependencies with HPC-Tools' `basic_cmake_*` into `libs/opt/<stack>-intel-hpcx/<lib>` (sources/build trees and per-lib logs in `libs/build/<stack>-intel-hpcx/`). The table at the top of the script lists every library, version and option.
- `stackenv.sh cpu|gpu`: `source` it to get that stack's compilers, MPI and libraries (also sets `NETCDF`, `PNETCDF`, `HDF5`, `JASPERLIB/INC` for WRF/WPS, and `CMAKE_PREFIX_PATH` so MFC's `find_package` picks up FFTW/HDF5/Silo/LAPACK instead of building its own).

| | cpu | gpu |
|---|---|---|
| C/C++ | icx/icpx `-O2 -xSAPPHIRERAPIDS -fp-model=precise` (FFTW `-O3`) | same |
| Fortran | ifx | nvfortran `-tp=sapphirerapids` (MFC GPU = OpenACC needs it) |
| MPI | Open MPI 4.1.9 from the local HPC-X 2.25.3's `sources/`, built with icx/ifx (`contrib/platform/mellanox/optimized`, no CUDA) on its UCX 1.20/UCC/HCOLL (`STACK_HPCX=local`; the default `sdk` builds Open MPI 5.0.11 on the SDK's HPC-X 2.50) | HPC-X 2.50's CUDA-aware Open MPI 5 from the NVHPC SDK (wrappers set to icx for C) |
| Libraries | zlib, parallel HDF5 1.14.6, PnetCDF 1.14.1, netCDF-C 4.9.3 + Fortran 4.6.2, FFTW 3.3.10 (AVX-512/OpenMP/MPI), Silo 4.12.1-pre1, LAPACK 3.12.0 on MKL BLAS, libpng, jasper | same minus LAPACK (SDK's `liblapack`), libpng and jasper (WPS is CPU-only) |

HPC-X's own Open MPI cannot serve the CPU stack: its Fortran modules are compiled by nvfortran and ifx cannot read them.
For the Intel-built Open MPI, `buildstack.sh` links against symlink views of HPC-X's UCX/UCC/HCOLL without their `.la` and `.pc` files: those name libraries and paths from NVIDIA's build host (`-liberty`, ...) and break libtool. `ompi_info` shows `pml ucx`, `osc ucx`, `coll ucc` and `coll hcoll`.

Checked on 2026-11-08: both stacks write a parallel netCDF-4 file from 4 MPI ranks via `nf90_create_par` (cpu: ifx + `--mca pml ucx`; gpu: nvfortran + HPC-X). The CPU LAPACK links MKL (sequential). Installed sizes: cpu 210 MB, gpu 87 MB. MFC and WRF have not been rebuilt against these stacks yet.

## Local HPC-X

- `installhpcx.sh`: install the standalone HPC-X 2.25.3 (Ubuntu 24.04, inbox rdma-core, CUDA 13; 1.4 GB) into `libs/opt/hpcx-v2.25.3`, linked as `libs/opt/hpcx`. Load with `source libs/opt/hpcx/hpcx-init.sh && hpcx_load`, or use it for a stack with `STACK_HPCX=local source stackenv.sh cpu|gpu`.
- Compared with the NVHPC SDK copy (HPC-X 2.50, RHEL 8 build, Open MPI 5 with nvfortran modules): Ubuntu gcc/gfortran build, Open MPI 4.1.9 (CUDA-aware, UCX/UCC/HCOLL), UCX 1.20, `.la` files without build-host paths, and `sources/` with the Open MPI and UCX tarballs for rebuilding with other compilers.
- Checked on 2026-11-08: UCX sees `mlx5_0`/`mlx5_3` (rc/ud verbs), `cuda_copy`/`cuda_ipc`, posix and cma; a 4-rank allreduce over `--mca pml ucx` is correct and `MPIX_Query_cuda_support()` returns 1.
- The CPU stack is built on it (`STACK_HPCX=local buildstack.sh cpu`, 2026-11-08): Open MPI 4.1.9 icx/ifx with pml/osc ucx, coll ucc/hcoll, linked to this UCX; parallel netCDF-4 test (4 ranks) and a 64-rank allreduce pass over `--mca pml ucx`. The GPU stack stays on the SDK's HPC-X 2.50: this HPC-X's Fortran MPI modules are gfortran's, which nvfortran cannot use.
- `buildstack.sh` records the HPC-X a stack was built with in `libs/opt/<stack>-intel-hpcx/.hpcx`; `stackenv.sh` loads that one unless `STACK_HPCX` is set.

## ParaView

- `installparaview.sh`: install the ParaView 6.0.1 binary release (MPI, Python 3.12) into `libs/opt/paraview-6.0.1`, linked as `libs/opt/paraview`. Its `pvpython` renders headless through EGL (GPU) or OSMesa (CPU); `../mfc/render3d.sh` uses it for 3D renders of MFC output (EGL and OSMesa both checked 2026-11-10). Its VisIt Silo reader segfaults when created, so MFC output goes through `../mfc/mfc2vtk.py` first.
