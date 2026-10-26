#!/usr/bin/env bash
# STREAM (CPU, stream.c built with icx) with the best configuration found on
# Launchpad: 479.2 GB/s Triad, ~10 s.
# Config: stream_smac3_v5 attempt 3716 (outputs/best/stream.json).
#
#   scripts/Launchpad/stream/runbest.sh
#
# 64 OpenMP threads (one per physical core); for the run THP is set to
# "always" (STREAM's static arrays only get huge pages that way), the uncore
# max to 2.5 GHz, and C1E/C6 are disabled. All three are restored after.
# Energy-performance bias 6 is this node's default, so it is left alone.
# Waits for the node lock. Log: outputs/runbest/stream_<time>/

set -uo pipefail
SCC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC" || exit 1
RUN="$SCC/outputs/runbest/stream_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN"

source libs/spack/share/spack/setup-env.sh >/dev/null
spack load intel-oneapi-compilers || exit 1
icx -DSTREAM_ARRAY_SIZE=134217728 -DNTIMES=100 -DOFFSET=64 -DSTREAM_TYPE=double \
    -O2 -qopenmp -mcmodel=medium -march=native -qopt-streaming-stores=auto -funroll-loops \
    stream.c -o "$RUN/stream.exe" || exit 1

export OMP_NUM_THREADS=64 OMP_PROC_BIND=false OMP_PLACES=threads OMP_DYNAMIC=FALSE
export KMP_AFFINITY=granularity=core,compact,1,0
unset KMP_BLOCKTIME
taskset -cp 0-"$(( $(nproc --all) - 1 ))" $$ >/dev/null    # undo any inherited CPU restriction (e.g. tmux)

# Machine-wide settings for the run; restored on any exit.
THP=/sys/kernel/mm/transparent_hugepage/enabled
UNC=(/sys/devices/system/cpu/intel_uncore_frequency/package_*_die_*)
CST=(); for s in /sys/devices/system/cpu/cpu*/cpuidle/state*; do
    case "$(cat "$s/name")" in C1E|C6) CST+=("$s/disable") ;; esac
done
THP_FOUND=$(sed -E 's/.*\[([a-z]+)\].*/\1/' "$THP")
UNC_FOUND=(); for d in "${UNC[@]}"; do UNC_FOUND+=("$(cat "$d/max_freq_khz")"); done
CST_FOUND=(); for f in "${CST[@]}"; do CST_FOUND+=("$(cat "$f")"); done
restore() {
    echo "$THP_FOUND" | sudo -n tee "$THP" >/dev/null
    local i=0
    for d in "${UNC[@]}"; do echo "${UNC_FOUND[$i]}" | sudo -n tee "$d/max_freq_khz" >/dev/null; i=$((i + 1)); done
    for v in $(printf '%s\n' "${CST_FOUND[@]}" | sort -u); do    # one sudo call per old value
        local sel=(); i=0
        for f in "${CST[@]}"; do [[ ${CST_FOUND[$i]} == "$v" ]] && sel+=("$f"); i=$((i + 1)); done
        echo "$v" | sudo -n tee "${sel[@]}" >/dev/null
    done
    rm -f "$RUN/stream.exe"
    echo "restored: THP $THP_FOUND, uncore max ${UNC_FOUND[*]} kHz, C1E/C6"
}
trap restore EXIT
trap 'exit 130' INT TERM HUP

exec 9>"$SCC/outputs/autotuning/.node.lock"
flock -n 9 || { echo "waiting for the node lock..."; flock 9; }
echo always | sudo -n tee "$THP" >/dev/null || { echo "needs passwordless sudo" >&2; exit 1; }
for d in "${UNC[@]}"; do echo 2500000 | sudo -n tee "$d/max_freq_khz" >/dev/null; done
echo 1 | sudo -n tee "${CST[@]}" >/dev/null
echo "STREAM: THP always, uncore max 2500 MHz, C1E/C6 off -> $RUN"
"$RUN/stream.exe" 2>&1 | tee "$RUN/stream.log"
grep -E "^Triad:|Solution Validates" "$RUN/stream.log"
