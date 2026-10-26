#!/usr/bin/env bash
# Build NCCL from source into ./nccl/build (env.sh picks it up as NCCL_HOME).
# For machines without a system/module NCCL (e.g. LaunchPad).
#   scripts/build_nccl.sh                      # pinned release, local GPU arch
#   NCCL_REF=v2.31.2-1 scripts/build_nccl.sh
set -euo pipefail
cd "$(dirname "$0")/.."
set +e; NCCL_HOME=none source ./env.sh; set -e   # CUDA_HOME + NVCC_GENCODE only

NCCL_URL="${NCCL_URL:-https://github.com/NVIDIA/nccl.git}"
NCCL_REF="${NCCL_REF:-v2.32.3-1}"

if [ -d nccl/.git ]; then
  git -C nccl fetch --depth 1 origin tag "$NCCL_REF"
else
  git clone --depth 1 --branch "$NCCL_REF" "$NCCL_URL" nccl
fi
git -C nccl checkout -q "$NCCL_REF"

echo "NCCL $NCCL_REF  CUDA_HOME=$CUDA_HOME  NVCC_GENCODE=$NVCC_GENCODE"
make -C nccl -j"$(nproc)" src.build CUDA_HOME="$CUDA_HOME" NVCC_GENCODE="$NVCC_GENCODE"
ls -1 nccl/build/lib/libnccl.so*
