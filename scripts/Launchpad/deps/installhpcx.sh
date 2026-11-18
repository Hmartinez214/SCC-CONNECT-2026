#!/usr/bin/env bash
# Install NVIDIA HPC-X (standalone, Ubuntu 24.04 build) locally into
# libs/opt/hpcx-v<version>, with libs/opt/hpcx pointing at it. No root needed.
#
#   scripts/Launchpad/deps/installhpcx.sh                      # 2.25.3, inbox rdma-core, CUDA 13
#   HPCX_VER=2.24.1 HPCX_OFED=doca_ofed scripts/Launchpad/deps/installhpcx.sh
#
# Use it:
#   source libs/opt/hpcx/hpcx-init.sh && hpcx_load
#   HPCX_HOME=$PWD/libs/opt/hpcx source scripts/Launchpad/deps/stackenv.sh gpu
#
# "inbox" matches this node: Ubuntu's rdma-core (librdmacm1t64, ibverbs) with
# mlx5 NICs, no DOCA/MLNX OFED. Unlike the copy in the NVHPC SDK (built on
# RHEL 8, nvfortran Fortran modules), this is the Ubuntu gcc build and ships
# its Open MPI sources (sources/) for rebuilding with other compilers.
# Download URL: https://content.mellanox.com/hpc/hpc-x/v<ver>_cuda13/ (2.24+).

set -euo pipefail
SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
VER="${HPCX_VER:-2.25.3}"
OFED="${HPCX_OFED:-inbox}"
CUDA="${HPCX_CUDA:-cuda13}"
NAME="hpcx-v$VER-gcc-$OFED-ubuntu24.04-$CUDA-x86_64"
URL="https://content.mellanox.com/hpc/hpc-x/v${VER}_$CUDA/$NAME.tbz"
DEST="$SCC_DIR/libs/opt/hpcx-v$VER"

mkdir -p "$SCC_DIR/libs/clone" "$SCC_DIR/libs/opt"
cd "$SCC_DIR/libs/clone"
[[ -f "$NAME.tbz" ]] || wget -q --show-progress "$URL"
rm -rf "$DEST"; mkdir -p "$DEST"
tar -xjf "$NAME.tbz" -C "$DEST" --strip-components=1
ln -sfn "hpcx-v$VER" "$SCC_DIR/libs/opt/hpcx"

# The init scripts locate HPC-X from their own path, so the tree is relocatable.
set +u
source "$DEST/hpcx-init.sh"
hpcx_load
set -u
echo "installed $NAME -> libs/opt/hpcx-v$VER (libs/opt/hpcx)"
mpirun --version | head -1
ucx_info -v | head -2
