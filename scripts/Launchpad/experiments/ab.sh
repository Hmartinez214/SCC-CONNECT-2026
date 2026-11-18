#!/usr/bin/env bash
# Interleaved A/B(/C...) test of a runbest benchmark: each round runs every arm
# once, in a rotated order, so slow drift (temperature, other users) spreads
# over all arms. Each arm is a quoted set of `bench run` arguments.
#
#   scripts/Launchpad/experiments/ab.sh [-r ROUNDS] BENCH 'ARGS A' 'ARGS B' ...
#   bench exp ab -r 4 hpl 'N=90482 EXCLUDE_CPUS=none' 'N=90482 EXCLUDE_CPUS=0,64'
#   bench exp ab -r 3 hpl 'N=90482' 'N=90482 HUGEPAGES=2m' 'N=50000 HUGEPAGES=1g'
#   bench exp ab -r 5 stream '' 'OMP_THREADS=60'
#
# ROUNDS defaults to 3. Results: outputs/experiments/ab/<time>_<bench>/ab.tsv
# (round, arm, value, valid, run directory) and a mean / sd / vs-A summary at
# the end; each run is also an ordinary runbest run (its directory and
# results.tsv line).

set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/node.sh"
ROUNDS=3
[[ ${1:-} == -h || ${1:-} == --help ]] && { usage; exit 0; }
[[ ${1:-} == -r ]] && { ROUNDS=$2; shift 2; }
(( $# >= 3 )) || { usage >&2; exit 2; }
BENCH=$1; shift
ARMS=("$@")
NAMES=({A..Z})
OUT="$OUTPUTS/experiments/ab/$(date +%Y%m%d_%H%M%S)_$BENCH"
mkdir -p "$OUT"
TSV="$OUT/ab.tsv"
printf "round\tarm\targs\tvalue\tvalid\trun\n" > "$TSV"
for i in "${!ARMS[@]}"; do printf '%s: %s\n' "${NAMES[i]}" "${ARMS[i]:-(best as is)}"; done | tee "$OUT/arms.txt"

for (( r = 1; r <= ROUNDS; r++ )); do
    for (( k = 0; k < ${#ARMS[@]}; k++ )); do
        i=$(( (k + r - 1) % ${#ARMS[@]} ))
        arm=${NAMES[i]}
        read -ra args <<<"${ARMS[i]}"
        log="$OUT/round${r}_$arm.log"
        "$SCC/scripts/Launchpad/bench" run "$BENCH" "${args[@]}" > "$log" 2>&1
        rc=$?
        (( rc == 130 )) && exit 130
        run=$(grep -oE 'run: [^)]+' "$log" | tail -1 | cut -c6-)
        read -r value valid < <(python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r["value"], r["valid"])' \
                                "$SCC/$run/result.json" 2>/dev/null || echo "- False")
        printf "%s\t%s\t%s\t%s\t%s\t%s\n" "$r" "$arm" "${ARMS[i]}" "$value" "$valid" "${run:-?}" | tee -a "$TSV"
    done
done

echo "== summary (valid runs; $TSV)"
awk -F'\t' 'NR > 1 && $5 == "True" {n[$2]++; s[$2] += $4; q[$2] += $4 * $4; a[$2] = $3}
    END {for (k in n) {mu = s[k] / n[k]; sd = n[k] > 1 ? sqrt((q[k] - n[k] * mu * mu) / (n[k] - 1)) : 0
         base = "A" in n ? s["A"] / n["A"] : mu
         printf "%s  n=%d  mean %.2f  sd %.2f  vs A %+.2f%%  %s\n", k, n[k], mu, sd, 100 * (mu / base - 1), a[k]}}' "$TSV" |
    sort | tee "$OUT/summary.txt"
