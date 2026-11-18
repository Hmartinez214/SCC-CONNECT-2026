#!/usr/bin/env bash
# Endless large-N sweep of the best Intel HPL configuration, up to the largest
# N that fits in memory. Each cycle runs N at 80 / 88 / 94 / 100 % of N_max
# (~6 h per cycle); N_max is recomputed before every run from MemAvailable
# minus a margin, rounded down to a multiple of NB.
#
#   scripts/Launchpad/hpl/hpl_bign.sh            # run until Ctrl-C
#   scripts/Launchpad/hpl/hpl_bign_tmux.sh       # same, in tmux session hpl-bign
#   bench start hpl-bign NS=324520               # repeat one fixed N
#
# Every run goes through system_ab.sh (arm "found"): the tuner's best config
# at fixed N, uncore capped as below, node lock held, system state restored
# after each run and on Ctrl-C.
# Results: outputs/hpl/bign/results.tsv (one row per run); logs in runs/.
#
# Settings (KEY=VALUE arguments or environment): ATTEMPT (config source,
# default the best run of the tuner's run group), UNCORE_KHZ (default 1000000,
# the best config's cap), MARGIN_GIB (memory left free, default 96), NB
# (default 760, the best config's NB), FRACTIONS (percent of N_max, default
# "80 88 94 100"), NS (fixed sizes instead of FRACTIONS, e.g. NS=324520; a size
# that does not fit in free memory waits like a too-small one).

set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/node.sh"
ATTEMPT="${ATTEMPT:-}"
UNCORE_KHZ="${UNCORE_KHZ:-1000000}"
MARGIN_GIB="${MARGIN_GIB:-96}"
NB="${NB:-760}"
FRACTIONS="${FRACTIONS:-80 88 94 100}"
NS="${NS:-}"
parse_args "ATTEMPT UNCORE_KHZ MARGIN_GIB NB FRACTIONS NS" "$@"
(( ${#OVERRIDES[@]} == 0 )) || { echo "unknown setting: ${OVERRIDES[*]}" >&2; exit 2; }
read -r -a FRACTIONS <<<"$FRACTIONS"
read -r -a NS <<<"$NS"
OUT="$SCC/outputs/hpl/bign/results.tsv"

n_max() {
    # Largest N (multiple of NB) whose 8 N^2-byte matrix leaves MARGIN_GIB free.
    awk -v m="$MARGIN_GIB" -v nb="$NB" '/^MemAvailable:/ {
        bytes = $2 * 1024 - m * 2^30
        n = int(sqrt(bytes / 8) / nb) * nb
        print (n > 0 ? n : 0) }' /proc/meminfo
}

if (( DRY_RUN )); then
    if (( ${#NS[@]} )); then sizes="N=${NS[*]}"; else sizes="${FRACTIONS[*]} % of N_max (now $(n_max))"; fi
    echo "dry run: each cycle runs $sizes; NB=$NB, uncore cap $UNCORE_KHZ kHz; results -> ${OUT#"$SCC"/}"
    exit 0
fi

cycle=0
while true; do
    cycle=$((cycle + 1))
    for f in "${NS[@]:-${FRACTIONS[@]}}"; do
        max=$(n_max)
        if (( ${#NS[@]} )); then
            n=$f
            echo "=== cycle $cycle: N=$n (N_max=$max) ($(date -u +%FT%TZ))"
            (( n <= max )) || { echo "N=$n does not fit in free memory; waiting 10 min"; sleep 600; continue; }
        else
            n=$(( max * f / 100 / NB * NB ))
            echo "=== cycle $cycle: ${f}% of N_max=$max -> N=$n ($(date -u +%FT%TZ))"
        fi
        if (( n < 100000 )); then
            echo "not enough free memory for a large-N run; waiting 10 min"
            sleep 600; continue
        fi
        "$SCC/scripts/Launchpad/hpl/system_ab.sh" -n "$n" ${ATTEMPT:+-a "$ATTEMPT"} -u "$UNCORE_KHZ" -o "$OUT" found
        rc=$?
        (( rc == 130 )) && exit 130          # Ctrl-C: system_ab already restored the system
    done
done
