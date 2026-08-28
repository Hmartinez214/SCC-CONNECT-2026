#!/usr/bin/env bash
# First-pass tuning sweep: run one collective under a grid of NCCL_ALGO x
# NCCL_PROTO x channel counts and print peak busbw for each.
#   scripts/sweep.sh [collective] [minbytes] [maxbytes]
set -euo pipefail
cd "$(dirname "$0")/.."
set +e; source ./env.sh; set -e   # probe script, must not abort us

COLL="${1:-all_reduce}"; BEG="${2:-1M}"; END="${3:-8G}"
ALGOS="${ALGOS:-Ring Tree}"
PROTOS="${PROTOS:-Simple LL128 LL}"
CHANS="${CHANS:-0 4 8 16}"          # 0 = let NCCL decide
BIN="nccl-tests/build/${COLL}_perf"
[ -x "$BIN" ] || { echo "build first"; exit 1; }

STAMP=$(date +%Y%m%d-%H%M%S); OUT="results/sweep-$STAMP"; mkdir -p "$OUT"
SUM="$OUT/summary.csv"; echo "algo,proto,channels,peak_busbw_GBps" > "$SUM"
start=$(date +%s)

for a in $ALGOS; do for p in $PROTOS; do for c in $CHANS; do
  env=( "NCCL_ALGO=$a" "NCCL_PROTO=$p" )
  [ "$c" != 0 ] && env+=( "NCCL_MIN_NCHANNELS=$c" "NCCL_MAX_NCHANNELS=$c" )
  tag="${a}_${p}_ch${c}"
  log="$OUT/$tag.txt"
  NCCL_DEBUG=WARN env "${env[@]}" \
    "$BIN" -b "$BEG" -e "$END" -f 2 -g "$NGPUS" -n 30 -w 10 -c 1 > "$log" 2>&1 || true
  peak=$(scripts/parse_nccl.py "$log" 2>/dev/null | sed -n 's/.*"peak_busbw": *\([0-9.]*\).*/\1/p')
  printf '%-6s %-7s ch=%-3s -> %s GB/s\n' "$a" "$p" "$c" "${peak:-FAIL}"
  echo "$a,$p,$c,${peak:-}" >> "$SUM"
done; done; done

best=$(tail -n +2 "$SUM" | sort -t, -k4 -gr | head -1)
echo; echo "top configs (algo,proto,channels,peak_busbw):"
tail -n +2 "$SUM" | sort -t, -k4 -gr | head -10 | sed 's/^/  /'
dur=$(fmt_dur $(( $(date +%s) - start )))
notify "sweep $COLL done in $dur; best: $best" "nvccl: sweep OK" default "white_check_mark,bar_chart"
echo "full results in $OUT/"
