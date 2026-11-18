#!/usr/bin/env bash
# nccl-tests on the Launchpad NVLink pair (GPUs 0,1) with the best NCCL
# settings found per collective (outputs/best/nccl.json, from
# scripts/launchpad_tune.py), ~1 min.
#
#   nvccl/scripts/runbest.sh
#   nvccl/scripts/runbest.sh COLLS=all_reduce,broadcast   # only these collectives
#   nvccl/scripts/runbest.sh --dry-run                    # show the settings, run nothing
#
# Same measurement as the tuner: -b 1G -e 8G -f 2 -g 2 -n 20 -w 5 -c 0, one
# process driving both GPUs; the score is the peak out-of-place busbw.
# Waits for the node lock. Run directory: outputs/nccl/runbest/nccl_<time>/
# (<coll>.log, <coll>.json, summary.txt); one line per collective in
# outputs/nccl/runbest/results.tsv.

set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../../scripts/Launchpad/lib/node.sh"
parse_args "COLLS" "$@"
(( ${#OVERRIDES[@]} == 0 )) || { echo "unknown setting: ${OVERRIDES[*]} (only COLLS=)" >&2; exit 2; }
cd "$SCC/nvccl" || exit 1
# shellcheck disable=SC1091
source ./env.sh >/dev/null

# ---- best configuration -> BEST_* variables -----------------------------------
# BEST_COLLS: the collectives; per collective BEST_<COLL> (its NCCL_* settings)
# and BEST_<COLL>_BUSBW (the busbw it measured), e.g. BEST_ALL_REDUCE.
eval "$(python3 - "$SCC/outputs/best/nccl.json" <<'EOF'
import json, shlex, sys
rows = json.load(open(sys.argv[1]))["best_per_collective"]
print(f"BEST_COLLS={shlex.quote(' '.join(r['collective'] for r in rows))}")
for r in rows:
    name = r["collective"].upper()
    print(f"BEST_{name}={shlex.quote(' '.join(f'{k}={v}' for k, v in r['configuration'].items()))}")
    print(f"BEST_{name}_BUSBW={r['objective']}")
EOF
)"
[[ -n ${BEST_COLLS:-} ]] || { echo "could not read outputs/best/nccl.json" >&2; exit 1; }
COLLS=${COLLS:-$BEST_COLLS}; COLLS=${COLLS//,/ }
for coll in $COLLS; do
    [[ " $BEST_COLLS " == *" $coll "* ]] || { echo "no collective '$coll' in outputs/best/nccl.json ($BEST_COLLS)" >&2; exit 2; }
done

run_dir nccl nccl
for coll in $COLLS; do var="BEST_${coll^^}"; echo "$coll: ${!var}"; done
dry_run_exit
node_lock
for coll in $COLLS; do
    var="BEST_${coll^^}"; tuned="${var}_BUSBW"
    read -ra settings <<<"${!var}"
    log="$RUN/$coll.log"
    echo "# ${settings[*]}" > "$log"
    env "${settings[@]}" NCCL_DEBUG=WARN CUDA_VISIBLE_DEVICES=0,1 \
        "nccl-tests/build/${coll}_perf" -b 1G -e 8G -f 2 -g 2 -n 20 -w 5 -c 0 >> "$log" 2>&1
    # Peak out-of-place busbw (column 8 of the size rows).
    peak=$(awk '$1 ~ /^[0-9]+$/ && NF >= 12 {if ($8 + 0 > p) p = $8 + 0} END {print p + 0}' "$log")
    BEST_SOURCE="outputs/best/nccl.json $coll" RESULT_JSON=$coll.json \
        record_result busbw_gb_s "$peak" "$(awk -v p="$peak" 'BEGIN {print (p > 0 ? "yes" : "no")}')" \
        "collective=$coll" "tuned_busbw_gb_s=${!tuned}"
    printf "%-15s peak busbw %7.2f GB/s (tuned %s)   %s\n" "$coll" "$peak" "${!tuned}" "${!var}"
done | tee "$RUN/summary.txt"
