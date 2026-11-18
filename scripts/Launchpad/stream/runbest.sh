#!/usr/bin/env bash
# STREAM (CPU, stream.c built with icx) with the best configuration found on
# Launchpad: the top entry of outputs/best/stream.json.
#
#   scripts/Launchpad/stream/runbest.sh
#   scripts/Launchpad/stream/runbest.sh OMP_THREADS=60 OFFSET=0   # override configuration keys
#   scripts/Launchpad/stream/runbest.sh --dry-run                 # show the build and settings, run nothing
#
# OpenMP-only (MPI_RANKS=1). For the run the machine-wide settings of the
# configuration are applied -- THP mode, uncore max (and min with
# UNCORE_PIN=on), energy-performance bias, disabled C-states -- and all are
# restored after. Waits for the node lock.
# Run directory: outputs/stream/runbest/stream_<time>/ (stream.log, result.json);
# one line per run in outputs/stream/runbest/results.tsv.

set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/node.sh"
parse_args "" "$@"
cd "$SCC" || exit 1

# ---- best configuration -> BEST_* variables (every key of the top entry) ----
eval "$(python3 scripts/Launchpad/lib/best.py outputs/best/stream.json "${OVERRIDES[@]}")"
[[ -n ${BEST_SOURCE:-} ]] || exit 1
(( BEST_MPI_RANKS == 1 )) || { echo "MPI_RANKS=$BEST_MPI_RANKS: this script runs OpenMP-only STREAM" >&2; exit 1; }
echo "best: $BEST_SOURCE ($BEST_OBJECTIVE MB/s Triad)"

# Tuner labels -> compiler flags / settings.
case $BEST_ISA in
    compiler_default) ISA=() ;;            host) ISA=(-xHost) ;;
    avx2) ISA=(-xCORE-AVX2) ;;             avx512) ISA=(-xCORE-AVX512 -qopt-zmm-usage=high) ;;
    march_native) ISA=(-march=native) ;;   march_mtune_native) ISA=(-march=native -mtune=native) ;;
esac
STORES="-qopt-streaming-stores=${BEST_STORES#streaming_}"       # auto | always | never
case $BEST_EXTRA in
    no_extra) EXTRA=() ;;          unroll_loops) EXTRA=(-funroll-loops) ;;   fast_math) EXTRA=(-ffast-math) ;;
    lto) EXTRA=(-flto) ;;          alias_analysis) EXTRA=(-fno-alias) ;;
esac
case $BEST_CSTATES in
    all) CSTATES="" ;;   no_c6) CSTATES="C6" ;;   c1_only) CSTATES="C1E|C6" ;;
esac
NUMA=(); [[ $BEST_NUMA == interleave_all ]] && NUMA=(numactl --interleave=all)

run_dir stream stream
source libs/spack/share/spack/setup-env.sh >/dev/null
spack load intel-oneapi-compilers || exit 1
icx -DSTREAM_ARRAY_SIZE="$BEST_ARRAY_SIZE" -DNTIMES="$BEST_NTIMES" -DOFFSET="$BEST_OFFSET" -DSTREAM_TYPE=double \
    "$BEST_OPT" -qopenmp -mcmodel=medium "${ISA[@]}" "$STORES" "${EXTRA[@]}" \
    stream.c -o "$RUN/stream.exe" || exit 1

export OMP_NUM_THREADS=$BEST_OMP_THREADS OMP_PROC_BIND=$BEST_OMP_PROC_BIND OMP_PLACES=$BEST_OMP_PLACES OMP_DYNAMIC=FALSE
export KMP_AFFINITY=$BEST_KMP_AFFINITY
if [[ $BEST_KMP_BLOCKTIME == default ]]; then unset KMP_BLOCKTIME; else export KMP_BLOCKTIME=$BEST_KMP_BLOCKTIME; fi
taskset -cp 0-"$(( $(nproc --all) - 1 ))" $$ >/dev/null    # undo any inherited CPU restriction (e.g. tmux)

THP=/sys/kernel/mm/transparent_hugepage/enabled
UNC=(/sys/devices/system/cpu/intel_uncore_frequency/package_*_die_*)
EPB=(/sys/devices/system/cpu/cpu*/power/energy_perf_bias)
CST=(); if [[ -n $CSTATES ]]; then for s in /sys/devices/system/cpu/cpu*/cpuidle/state*; do
    [[ $(cat "$s/name") =~ ^($CSTATES)$ ]] && CST+=("$s/disable")
done; fi
echo "STREAM: THP $BEST_THP, uncore max $BEST_UNCORE_MAX_MHZ MHz (pin $BEST_UNCORE_PIN), EPB $BEST_EPB," \
     "C-states $BEST_CSTATES, $BEST_OMP_THREADS threads, ${NUMA[*]:-default NUMA policy} -> ${RUN#"$SCC"/}"
env | grep -E '^(OMP|KMP)_' | sort
dry_run_exit

# Machine-wide settings for the run; restored on any exit.
restore() { sys_restore; rm -f "$RUN/stream.exe"; echo "restored: THP, uncore, EPB, C-states"; }
trap restore EXIT
trap 'exit 130' INT TERM HUP
node_lock
sys_set "$BEST_THP" "$THP" || { echo "needs passwordless sudo" >&2; exit 1; }
sys_set $(( BEST_UNCORE_MAX_MHZ * 1000 )) "${UNC[@]/%//max_freq_khz}"
[[ $BEST_UNCORE_PIN == on ]] && sys_set $(( BEST_UNCORE_MAX_MHZ * 1000 )) "${UNC[@]/%//min_freq_khz}"
sys_set "$BEST_EPB" "${EPB[@]}"
sys_set 1 "${CST[@]}"
"${NUMA[@]}" "$RUN/stream.exe" 2>&1 | tee "$RUN/stream.log"

triad=$(awk '$1 == "Triad:" {print $2; exit}' "$RUN/stream.log")
valid=no; grep -q "Solution Validates" "$RUN/stream.log" && valid=yes
record_result triad_mb_s "${triad:-}" "$valid" \
    $(awk '$1 ~ /^(Copy|Scale|Add):$/ {sub(":", "", $1); printf "%s_mb_s=%s ", tolower($1), $2}' "$RUN/stream.log")
echo "result: Triad ${triad:-?} MB/s, validates: $valid  (run: ${RUN#"$SCC"/})"
