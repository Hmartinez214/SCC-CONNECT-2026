#!/usr/bin/env bash
# WRF (Santa Cruz case, 9 km + 3 km nest) with the best configuration found on
# Launchpad: the top entry of outputs/best/wrf.json.
#
#   scripts/Launchpad/wrf/runbest.sh
#   scripts/Launchpad/wrf/runbest.sh RANKS=32 THREADS=4   # override configuration keys
#   scripts/Launchpad/wrf/runbest.sh HOURS=24             # longer forecast
#   scripts/Launchpad/wrf/runbest.sh --dry-run            # show the settings, run nothing
#
# For the run the machine-wide settings of the configuration are applied (THP
# mode, uncore max, energy-performance bias) and restored after. Waits for the
# node lock. Run directory: outputs/wrf/runbest/wrf_<time>/ (runwrf.py's files,
# result.json from runwrf.py, runbest.json); one line per run in
# outputs/wrf/runbest/results.tsv.

set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/node.sh"
parse_args "" "$@"
cd "$SCC" || exit 1

eval "$(python3 scripts/Launchpad/lib/best.py outputs/best/wrf.json "${OVERRIDES[@]}")"
[[ -n ${BEST_SOURCE:-} ]] || exit 1
echo "best: $BEST_SOURCE (${BEST_OBJECTIVE}x real time)"

ARGS=(--exe "$BEST_BUILD" --ranks "$BEST_RANKS" --threads "$BEST_THREADS" --tiles "$BEST_TILES"
      --decomp "$BEST_DECOMP" --bind "$BEST_BIND" --hours "$BEST_HOURS" --keep)
run_dir wrf wrf
THP=/sys/kernel/mm/transparent_hugepage/enabled
UNC=(/sys/devices/system/cpu/intel_uncore_frequency/package_*_die_*)
EPB=(/sys/devices/system/cpu/cpu*/power/energy_perf_bias)
echo "WRF: THP $BEST_THP, uncore max $BEST_UNCORE_MAX_MHZ MHz, EPB $BEST_EPB -> ${RUN#"$SCC"/}"
echo "runwrf.py ${ARGS[*]}"
dry_run_exit

restore() { sys_restore; echo "restored: THP, uncore, EPB"; }
trap restore EXIT
trap 'exit 130' INT TERM HUP
node_lock
taskset -cp 0-"$(( $(nproc --all) - 1 ))" $$ >/dev/null    # undo any inherited CPU restriction (e.g. tmux)
sys_set "$BEST_THP" "$THP" || { echo "needs passwordless sudo" >&2; exit 1; }
sys_set $(( BEST_UNCORE_MAX_MHZ * 1000 )) "${UNC[@]/%//max_freq_khz}"
sys_set "$BEST_EPB" "${EPB[@]}"
python3 scripts/Launchpad/wrf/runwrf.py --run-dir "$RUN" "${ARGS[@]}" | tee "$RUN/runwrf.log"

read -r speed ok < <(python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r.get("speed", ""), "yes" if r.get("success") else "no")' \
                     "$RUN/result.json" 2>/dev/null)
RESULT_JSON=runbest.json record_result speed "${speed:-}" "${ok:-no}" "hours=$BEST_HOURS"
echo "result: ${speed:-?}x real time (best $BEST_OBJECTIVE), success ${ok:-no}  (run: ${RUN#"$SCC"/})"
