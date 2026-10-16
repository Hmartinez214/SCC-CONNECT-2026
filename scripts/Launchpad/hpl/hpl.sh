bash
#!/usr/bin/env bash
set -euo pipefail

spack install intel-oneapi-compilers
spack install intel-oneapi-mpi
spack install intel-oneapi-mkl

spack load intel-oneapi-compilers
spack load intel-oneapi-mpi
spack load intel-oneapi-mkl

spack install hpl %oneapi ^intel-oneapi-mpi ^intel-oneapi-mkl