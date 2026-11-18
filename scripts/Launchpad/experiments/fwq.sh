#!/usr/bin/env bash
# OS noise per CPU (fwq.c): one thread pinned to every CPU spins on the clock
# for SECS seconds and counts each gap > 2 us as time taken from it.
#
#   scripts/Launchpad/experiments/fwq.sh [SECS=30]
#   bench exp fwq SECS=60
#
# Prints the mean stolen time over all CPUs, the noisiest CPUs, and the worst
# 100 ms bucket (a bulk-synchronous code like HPL waits for its slowest
# thread). Measured 2026-10-29: 0.53% mean with Kubernetes stopped (the
# 1000 Hz timer tick is ~0.6% of that), core 0 (CPUs 0/64) the noisiest.
# Takes the node lock. Output: outputs/experiments/fwq/<time>.txt (raw) and .summary.

set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/node.sh"
SECS=30
parse_args "SECS" "$@"
[[ $SECS =~ ^[0-9]+$ ]] || { echo "SECS=$SECS: give whole seconds" >&2; exit 2; }
(( ${#OVERRIDES[@]} == 0 )) || { echo "unknown setting: ${OVERRIDES[*]} (only SECS=)" >&2; exit 2; }
OUT="$OUTPUTS/experiments/fwq"; mkdir -p "$OUT"
BIN="$OUT/fwq"
[[ $BIN -nt "$SCC/scripts/Launchpad/experiments/fwq.c" ]] ||
    gcc -O2 -pthread "$SCC/scripts/Launchpad/experiments/fwq.c" -o "$BIN" || exit 1
RAW="$OUT/$(date +%Y%m%d_%H%M%S).txt"
node_lock "fwq.sh"
echo "fwq: ${SECS} s on every CPU -> ${RAW#"$SCC"/}"
taskset -cp 0-"$(( $(nproc --all) - 1 ))" $$ >/dev/null
"$BIN" "$SECS" > "$RAW" || exit 1
{
    awk -v secs="$SECS" '/^cpu n/ {sec = 1; next}  /^timeline/ {sec = 2; next}
        sec == 1 {tot += $3 / (secs * 1e4); n++}
        sec == 2 && $2 > worst {worst = $2; wb = $1}
        END {printf "mean stolen time %.3f%% over %d CPUs; worst 100 ms bucket on one CPU %.2f ms (at %.1f s)\n",
             tot / n, n, worst, wb / 10}' "$RAW"
    echo "noisiest CPUs (stolen %, longest detour):"
    awk -v secs="$SECS" '/^cpu n/ {sec = 1; next} /^timeline/ {exit} sec {printf "%s %.3f %.1f\n", $1, $3 / (secs * 1e4), $4}' "$RAW" |
        sort -k2 -nr | head -8 | awk '{printf "  cpu %-4s %6.3f%%  %8.1f us\n", $1, $2, $3}'
} | tee "${RAW%.txt}.summary"
