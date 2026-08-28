#!/usr/bin/env bash
# Run an nccl-tests perf binary over a size sweep and parse the table.
#   scripts/bench.sh [collective] [minbytes] [maxbytes]
#   scripts/bench.sh                        # all_reduce, 8 .. 8G, NGPUS from env.sh
#   NGPUS=2 scripts/bench.sh reduce_scatter 1K 2G
#   ITERS=100 WARMUP=50 NCCL_ALGO=Tree NCCL_PROTO=LL128 scripts/bench.sh all_reduce
#
# NGPUS ranks on THIS node (default: every visible GPU). >1 node needs the MPI
# build + a launcher. On 1 GPU busbw is ~0 (nothing to communicate).
set -euo pipefail
cd "$(dirname "$0")/.."
set +e; source ./env.sh; set -e   # probe script, must not abort us

COLL="${1:-all_reduce}"
BEG="${2:-8}"
END="${3:-8G}"
ITERS="${ITERS:-50}"
WARMUP="${WARMUP:-20}"
BIN="nccl-tests/build/${COLL}_perf"
[ -x "$BIN" ] || { echo "no $BIN - run scripts/setup.sh && scripts/build.sh"; exit 1; }

mkdir -p results
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="results/$STAMP"; mkdir -p "$OUT"
LOG="$OUT/${COLL}.txt"

{ echo "# host: $(hostname)   date: $(date -Is)   NGPUS: $NGPUS"
  echo "# env: NCCL_ALGO=${NCCL_ALGO:-} NCCL_PROTO=${NCCL_PROTO:-} NCCL_MIN_NCHANNELS=${NCCL_MIN_NCHANNELS:-} NCCL_MAX_NCHANNELS=${NCCL_MAX_NCHANNELS:-} NCCL_BUFFSIZE=${NCCL_BUFFSIZE:-} NCCL_NTHREADS=${NCCL_NTHREADS:-}"
  nvidia-smi --query-gpu=index,name,compute_cap --format=csv,noheader
  echo "# topo:"; nvidia-smi topo -m 2>/dev/null
} > "$OUT/machine.txt"

start=$(date +%s)
trap 'rc=$?; [ $rc -ne 0 ] && notify "bench $COLL failed (exit $rc)" "nvccl: bench FAILED" high "rotating_light"' EXIT

[ "$NGPUS" -lt 2 ] && echo "WARNING: NGPUS=$NGPUS - busbw will be ~0; smoke test only."
echo "# $BIN -b $BEG -e $END -f 2 -g $NGPUS -n $ITERS -w $WARMUP -c 1"
NCCL_DEBUG=WARN "$BIN" -b "$BEG" -e "$END" -f 2 -g "$NGPUS" -n "$ITERS" -w "$WARMUP" -c 1 | tee "$LOG"

dur=$(fmt_dur $(( $(date +%s) - start )))
echo; echo "=== parsed ==="
scripts/parse_nccl.py "$LOG" --csv | tee "$OUT/${COLL}.csv" || true

peak=$(scripts/parse_nccl.py "$LOG" 2>/dev/null | sed -n 's/.*"peak_busbw": *\([0-9.]*\).*/\1/p')
notify "bench $COLL done in $dur (peak busbw ${peak:-n/a} GB/s)
$OUT/" "nvccl: bench OK" default "white_check_mark,stopwatch"
