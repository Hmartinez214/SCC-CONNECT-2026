#!/usr/bin/env bash
# nccl-tests on the Launchpad NVLink pair (GPUs 0,1) with the best NCCL
# settings found per collective (outputs/best/nccl.json, from
# scripts/launchpad_tune.py), ~1 min.
#
#   scripts/runbest.sh                  (from nvccl/)
#
# Same measurement as the tuner: -b 1G -e 8G -f 2 -g 2 -n 20 -w 5 -c 0, one
# process driving both GPUs; the score is the peak out-of-place busbw.
# Waits for the node lock. Logs: outputs/runbest/nccl_<time>/

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck disable=SC1091
source ./env.sh >/dev/null
SCC="$(cd .. && pwd)"

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

RUN="$SCC/outputs/runbest/nccl_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN"

exec 9>"$SCC/outputs/autotuning/.node.lock"
flock -n 9 || { echo "waiting for the node lock..."; flock 9; }
for coll in $BEST_COLLS; do
    var="BEST_${coll^^}"; tuned="${var}_BUSBW"
    read -ra settings <<<"${!var}"
    log="$RUN/$coll.log"
    echo "# ${settings[*]}" > "$log"
    env "${settings[@]}" NCCL_DEBUG=WARN CUDA_VISIBLE_DEVICES=0,1 \
        "nccl-tests/build/${coll}_perf" -b 1G -e 8G -f 2 -g 2 -n 20 -w 5 -c 0 >> "$log" 2>&1
    # Peak out-of-place busbw (column 8 of the size rows).
    peak=$(awk '$1 ~ /^[0-9]+$/ && NF >= 12 {if ($8 + 0 > p) p = $8 + 0} END {print p + 0}' "$log")
    printf "%-15s peak busbw %7.2f GB/s (tuned %s)   %s\n" "$coll" "$peak" "${!tuned}" "${!var}"
done | tee "$RUN/summary.txt"
