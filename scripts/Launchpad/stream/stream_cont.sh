#!/usr/bin/env bash
# Endless STREAM around the best configuration (outputs/best/stream.json):
# alternates the unchanged best with a slight variation of one knob, so every
# variant is measured next to a baseline taken minutes apart.
#
#   scripts/Launchpad/stream/stream_cont.sh               # in the foreground
#   scripts/Launchpad/stream/stream_cont_tmux.sh          # in tmux session stream-cont
#   scripts/Launchpad/stream/stream_cont.sh --summary     # mean/sd per variant so far
#
# Each run is runbest.sh (node lock, machine settings applied and restored).
# Results: outputs/stream_cont/results.tsv (MB/s)

set -uo pipefail
SCC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUT="$SCC/outputs/stream_cont"
TSV="$OUT/results.tsv"
mkdir -p "$OUT"

summary() {
    awk -F'\t' 'NR > 1 && $8 == "yes" {k = $3; n[k]++; s[k] += $4; q[k] += $4 * $4; if ($4 > m[k]) m[k] = $4}
        END {for (k in n) {mu = s[k] / n[k]; sd = n[k] > 1 ? sqrt((q[k] - n[k] * mu * mu) / (n[k] - 1)) : 0
             printf "%-40s n=%-4d triad mean %9.1f  sd %6.1f  max %9.1f  vs best %+6.2f%%\n",
                    k, n[k], mu, sd, m[k], 100 * (mu / (s["best"] / n["best"]) - 1)}}' "$TSV" | sort -k5 -nr
}
[[ ${1:-} == --summary ]] && { summary; exit; }

# Slight variations, one knob at a time.
VARIANTS=(
    OMP_THREADS=56 OMP_THREADS=60 OMP_THREADS=62 OMP_THREADS=66 OMP_THREADS=68 OMP_THREADS=72
    ARRAY_SIZE=100663296 ARRAY_SIZE=201326592 ARRAY_SIZE=268435456
    OFFSET=0 OFFSET=32 OFFSET=128
    NTIMES=50 NTIMES=200
    UNCORE_PIN=on EPB=4 EPB=0 CSTATES=no_c6 CSTATES=all THP=madvise
    KMP_AFFINITY=granularity=fine,compact,1,0 KMP_AFFINITY=granularity=core,scatter
)

[[ -f $TSV ]] || printf "time\tcycle\tvariant\ttriad\tcopy\tscale\tadd\tvalid\trun\n" > "$TSV"
run() {    # run <label> [KEY=VALUE]...
    local label=$1 log; shift
    log=$("$SCC/scripts/Launchpad/stream/runbest.sh" "$@" 2>&1)
    local dir; dir=$(grep -oE 'outputs/runbest/stream_[0-9_]+' <<<"$log" | head -1)
    rate() { awk -v k="$1:" '$1 == k {print $2; exit}' <<<"$log"; }
    local ok=no; grep -q "Solution Validates" <<<"$log" && ok=yes
    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$(date +%FT%T)" "$cycle" "$label" \
        "$(rate Triad)" "$(rate Copy)" "$(rate Scale)" "$(rate Add)" "$ok" "${dir:-?}" | tee -a "$TSV"
    [[ $ok == yes ]] || tail -5 <<<"$log"
}

cycle=$(( $(tail -n +2 "$TSV" | cut -f2 | sort -n | tail -1) + 1 ))
while :; do
    v=${VARIANTS[RANDOM % ${#VARIANTS[@]}]}
    run best
    run "$v" "$v"
    (( cycle % 10 == 0 )) && { echo "--- after cycle $cycle"; summary; }
    cycle=$((cycle + 1))
done
