# deps/

- `builddeps.sh [openblas] [openmpi]`: build OpenBLAS v0.3.34 and Open MPI 5.0.11 into `libs/opt/` with HPC-Tools' `basic_cmake_github` / `basic_cmake_tarball`.
- `depsenv.sh`: `source` it to put them on PATH/LD_LIBRARY_PATH/CPATH (adds OpenBLAS's `include/openblas`).
- `installoneapi.sh [--toolkits]`: install Intel oneAPI (icx/ifx, Intel MPI, MKL; or the full toolkits) system-wide from Intel's apt repo. Load with `source /opt/intel/oneapi/setvars.sh`.
