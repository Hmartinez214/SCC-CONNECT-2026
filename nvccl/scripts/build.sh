#!/usr/bin/env bash
# Build nccl-tests. CUDA/NCCL roots + gencode come from env.sh.
#   scripts/build.sh          # single-node (no MPI)
#   MPI=1 scripts/build.sh    # multi-node (needs mpicc / MPI_HOME)
set -euo pipefail
cd "$(dirname "$0")/.."
set +e; source ./env.sh; set -e   # probe script, must not abort us

[ -d nccl-tests ] || { echo "run scripts/setup.sh first"; exit 1; }
[ -n "${NCCL_HOME:-}" ] || { echo "no NCCL found; 'module load nccl' or set NCCL_HOME"; exit 1; }

start=$(date +%s)
trap 'rc=$?; d=$(fmt_dur $(($(date +%s)-start)));
      [ $rc -eq 0 ] && notify "nccl-tests built in $d" "nvccl: build OK" default "white_check_mark,hammer_and_wrench" \
                    || notify "build failed (exit $rc) after $d" "nvccl: build FAILED" high "rotating_light"' EXIT

MAKE_ARGS=( -j"$(nproc)" CUDA_HOME="$CUDA_HOME" NCCL_HOME="$NCCL_HOME" NVCC_GENCODE="$NVCC_GENCODE" )
if [ "${MPI:-0}" = "1" ]; then
  MAKE_ARGS+=( MPI=1 )
  [ -n "${MPI_HOME:-}" ] && MAKE_ARGS+=( MPI_HOME="$MPI_HOME" )
fi

cd nccl-tests
make clean >/dev/null 2>&1 || true
echo "make ${MAKE_ARGS[*]}"
make "${MAKE_ARGS[@]}"
echo; echo "binaries:"; ls -1 build/*_perf
