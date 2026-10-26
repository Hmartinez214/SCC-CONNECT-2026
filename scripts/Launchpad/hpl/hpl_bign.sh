#!/usr/bin/env bash
# Endless large-N sweep of the best Intel HPL configuration, up to the largest
# N that fits in memory. Each cycle runs N at 80 / 88 / 94 / 100 % of N_max
# (~6 h per cycle); N_max is recomputed before every run from MemAvailable
# minus a margin, rounded down to a multiple of NB.
#
#   scripts/Launchpad/hpl/hpl_bign.sh            # run until Ctrl-C
#   scripts/Launchpad/hpl/hpl_bign_tmux.sh       # same, in tmux session hpl-bign
#
# Every run goes through system_ab.sh (arm "found"): the tuner's best config
# at fixed N, uncore capped as below, node lock held, system state restored
# after each run and on Ctrl-C.
# Results: outputs/hpl_bign/results.tsv (one row per run); logs in runs/.
#
# Env overrides: ATTEMPT (config source, default the best run of the tuner's
# run group), UNCORE_KHZ (default 1000000, the best config's cap), MARGIN_GIB
# (memory left free, default 96), NB (default 760, the best config's NB),
# FRACTIONS (percent of N_max, default "80 88 94 100").

set -uo pipefail
SCC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
ATTEMPT="${ATTEMPT:-}"
UNCORE_KHZ="${UNCORE_KHZ:-1000000}"
MARGIN_GIB="${MARGIN_GIB:-96}"
NB="${NB:-760}"
read -r -a FRACTIONS <<<"${FRACTIONS:-80 88 94 100}"
OUT="$SCC/outputs/hpl_bign/results.tsv"

n_max() {
    # Largest N (multiple of NB) whose 8 N^2-byte matrix leaves MARGIN_GIB free.
    awk -v m="$MARGIN_GIB" -v nb="$NB" '/^MemAvailable:/ {
        bytes = $2 * 1024 - m * 2^30
        n = int(sqrt(bytes / 8) / nb) * nb
        print (n > 0 ? n : 0) }' /proc/meminfo
}

cycle=0
while true; do
    cycle=$((cycle + 1))
    for f in "${FRACTIONS[@]}"; do
        max=$(n_max)
        n=$(( max * f / 100 / NB * NB ))
        echo "=== cycle $cycle: ${f}% of N_max=$max -> N=$n ($(date -u +%FT%TZ))"
        if (( n < 100000 )); then
            echo "not enough free memory for a large-N run; waiting 10 min"
            sleep 600; continue
        fi
        "$SCC/scripts/Launchpad/hpl/system_ab.sh" -n "$n" ${ATTEMPT:+-a "$ATTEMPT"} -u "$UNCORE_KHZ" -o "$OUT" found
        rc=$?
        (( rc == 130 )) && exit 130          # Ctrl-C: system_ab already restored the system
    done
done
