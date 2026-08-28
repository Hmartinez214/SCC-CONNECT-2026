#!/usr/bin/env bash
# Fetch nccl-tests at a pinned commit. Bump NCCL_TESTS_REF to update.
set -euo pipefail
cd "$(dirname "$0")/.."

NCCL_TESTS_URL="${NCCL_TESTS_URL:-https://github.com/NVIDIA/nccl-tests.git}"
NCCL_TESTS_REF="${NCCL_TESTS_REF:-b4d5beebca8a76cf01335f724d154b9b9d394d96}"  # v2.20.0

if [ -d nccl-tests/.git ]; then
  git -C nccl-tests fetch --depth 1 origin "$NCCL_TESTS_REF" 2>/dev/null || git -C nccl-tests fetch origin
  git -C nccl-tests checkout -q "$NCCL_TESTS_REF"
else
  git clone "$NCCL_TESTS_URL" nccl-tests
  git -C nccl-tests checkout -q "$NCCL_TESTS_REF"
fi
echo "nccl-tests at $(git -C nccl-tests rev-parse --short HEAD)"
echo "next: scripts/build.sh"
