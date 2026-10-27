#!/usr/bin/env bash
# STREAM (CPU, stream.c built with icx) with the best configuration found on
# Launchpad: the top entry of outputs/best/stream.json.
#
#   scripts/Launchpad/stream/runbest.sh
#   scripts/Launchpad/stream/runbest.sh OMP_THREADS=60 OFFSET=0   # override keys of the configuration
#
# OpenMP-only (MPI_RANKS=1). For the run the machine-wide settings of the
# configuration are applied -- THP mode, uncore max (and min with
# UNCORE_PIN=on), energy-performance bias, disabled C-states -- and all are
# restored after. Waits for the node lock. Log: outputs/runbest/stream_<time>/

set -uo pipefail
SCC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC" || exit 1

# ---- best configuration -> BEST_* variables (every key of the top entry) ----
eval "$(python3 - "$SCC/outputs/best/stream.json" "$@" <<'EOF'
import json, shlex, sys
top = json.load(open(sys.argv[1]))["top"][0]
over = dict(arg.split("=", 1) for arg in sys.argv[2:])
if set(over) - set(top["configuration"]):
    print(f"echo 'not a configuration key: {sorted(set(over) - set(top['configuration']))}' >&2; exit 1"); sys.exit()
v = {**top["configuration"], **over, "OBJECTIVE": top["objective"],
     "SOURCE": f"{top['run_group']} attempt {top['attempt']}" + "".join(f" {k}={x}" for k, x in over.items())}
for key, value in v.items():
    print(f"BEST_{key.upper()}={shlex.quote(str(value))}")
EOF
)"
[[ -n ${BEST_SOURCE:-} ]] || { echo "could not read outputs/best/stream.json" >&2; exit 1; }
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

RUN="$SCC/outputs/runbest/stream_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN"
source libs/spack/share/spack/setup-env.sh >/dev/null
spack load intel-oneapi-compilers || exit 1
icx -DSTREAM_ARRAY_SIZE="$BEST_ARRAY_SIZE" -DNTIMES="$BEST_NTIMES" -DOFFSET="$BEST_OFFSET" -DSTREAM_TYPE=double \
    "$BEST_OPT" -qopenmp -mcmodel=medium "${ISA[@]}" "$STORES" "${EXTRA[@]}" \
    stream.c -o "$RUN/stream.exe" || exit 1

export OMP_NUM_THREADS=$BEST_OMP_THREADS OMP_PROC_BIND=$BEST_OMP_PROC_BIND OMP_PLACES=$BEST_OMP_PLACES OMP_DYNAMIC=FALSE
export KMP_AFFINITY=$BEST_KMP_AFFINITY
if [[ $BEST_KMP_BLOCKTIME == default ]]; then unset KMP_BLOCKTIME; else export KMP_BLOCKTIME=$BEST_KMP_BLOCKTIME; fi
taskset -cp 0-"$(( $(nproc --all) - 1 ))" $$ >/dev/null    # undo any inherited CPU restriction (e.g. tmux)

# Machine-wide settings for the run; restored on any exit.
THP=/sys/kernel/mm/transparent_hugepage/enabled
UNC=(/sys/devices/system/cpu/intel_uncore_frequency/package_*_die_*)
EPB=(/sys/devices/system/cpu/cpu*/power/energy_perf_bias)
CST=(); if [[ -n $CSTATES ]]; then for s in /sys/devices/system/cpu/cpu*/cpuidle/state*; do
    [[ $(cat "$s/name") =~ ^($CSTATES)$ ]] && CST+=("$s/disable")
done; fi
THP_FOUND=$(sed -E 's/.*\[([a-z]+)\].*/\1/' "$THP")
UNC_MAX=(); UNC_MIN=(); for d in "${UNC[@]}"; do UNC_MAX+=("$(cat "$d/max_freq_khz")"); UNC_MIN+=("$(cat "$d/min_freq_khz")"); done
EPB_FOUND=(); for f in "${EPB[@]}"; do EPB_FOUND+=("$(cat "$f")"); done
CST_FOUND=(); for f in "${CST[@]}"; do CST_FOUND+=("$(cat "$f")"); done
put_back() {    # put_back <files array name> <values array name>: one sudo call per distinct old value
    local -n files=$1 values=$2
    local v i sel
    for v in $(printf '%s\n' "${values[@]}" | sort -u); do
        sel=(); for i in "${!files[@]}"; do [[ ${values[$i]} == "$v" ]] && sel+=("${files[$i]}"); done
        echo "$v" | sudo -n tee "${sel[@]}" >/dev/null
    done
}
restore() {
    echo "$THP_FOUND" | sudo -n tee "$THP" >/dev/null
    local i=0
    for d in "${UNC[@]}"; do
        echo "${UNC_MIN[$i]}" | sudo -n tee "$d/min_freq_khz" >/dev/null       # min first: min <= max at every step
        echo "${UNC_MAX[$i]}" | sudo -n tee "$d/max_freq_khz" >/dev/null; i=$((i + 1))
    done
    put_back EPB EPB_FOUND
    (( ${#CST[@]} )) && put_back CST CST_FOUND
    rm -f "$RUN/stream.exe"
    echo "restored: THP $THP_FOUND, uncore, EPB, C-states"
}
trap restore EXIT
trap 'exit 130' INT TERM HUP

exec 9>"$SCC/outputs/autotuning/.node.lock"
flock -n 9 || { echo "waiting for the node lock..."; flock 9; }
echo "$BEST_THP" | sudo -n tee "$THP" >/dev/null || { echo "needs passwordless sudo" >&2; exit 1; }
for d in "${UNC[@]}"; do
    echo $(( BEST_UNCORE_MAX_MHZ * 1000 )) | sudo -n tee "$d/max_freq_khz" >/dev/null
    [[ $BEST_UNCORE_PIN == on ]] && echo $(( BEST_UNCORE_MAX_MHZ * 1000 )) | sudo -n tee "$d/min_freq_khz" >/dev/null
done
echo "$BEST_EPB" | sudo -n tee "${EPB[@]}" >/dev/null
(( ${#CST[@]} )) && echo 1 | sudo -n tee "${CST[@]}" >/dev/null
echo "STREAM: THP $BEST_THP, uncore max $BEST_UNCORE_MAX_MHZ MHz (pin $BEST_UNCORE_PIN), EPB $BEST_EPB," \
     "C-states $BEST_CSTATES, $BEST_OMP_THREADS threads -> $RUN"
"${NUMA[@]}" "$RUN/stream.exe" 2>&1 | tee "$RUN/stream.log"
grep -E "^Triad:|Solution Validates" "$RUN/stream.log"
