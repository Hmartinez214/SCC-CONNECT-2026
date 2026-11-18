#!/usr/bin/env bash
# NVIDIA-STREAM (GPU, NGC hpc-benchmarks 26.02.01) on one H100 NVL with the
# best configuration found on Launchpad: the top entry of outputs/best/ngc_stream.json.
#
#   scripts/Launchpad/stream/runbest_ngc.sh
#   scripts/Launchpad/stream/runbest_ngc.sh DEVICE=1 POWER_LIMIT_W=350   # override configuration keys
#   scripts/Launchpad/stream/runbest_ngc.sh --dry-run                    # show the settings, run nothing
#
# The configuration's GPU, array size and element type; its SM clock is
# locked and power limit set for the run (persistence mode on), all restored
# after. Waits for the node lock. Run directory: outputs/ngc/runbest/ngc_stream_<time>/
# (stream.log, result.json); one line per run in outputs/ngc/runbest/results.tsv.

set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/node.sh"
parse_args "" "$@"
IMAGE=nvcr.io/nvidia/hpc-benchmarks:26.02.01

# ---- best configuration -> BEST_* variables (every key of the top entry) ----
eval "$(python3 "$SCC/scripts/Launchpad/lib/best.py" "$SCC/outputs/best/ngc_stream.json" "${OVERRIDES[@]}")"
[[ -n ${BEST_SOURCE:-} ]] || exit 1
echo "best: $BEST_SOURCE ($BEST_OBJECTIVE MB/s Triad)"
GPU=$BEST_DEVICE
SIZE=(); (( BEST_ARRAY_SIZE > 0 )) && SIZE=(--n "$BEST_ARRAY_SIZE")      # 0 = the container's default size

run_dir ngc ngc_stream
echo "NGC STREAM: GPU $GPU, ${SIZE[*]:-default size} $BEST_DTYPE, SM $BEST_SM_CLOCK_MHZ MHz, $BEST_POWER_LIMIT_W W -> ${RUN#"$SCC"/}"
dry_run_exit

PM_FOUND=$(nvidia-smi -i "$GPU" --format=csv,noheader,nounits --query-gpu=persistence_mode)
PL_FOUND=$(nvidia-smi -i "$GPU" --format=csv,noheader,nounits --query-gpu=power.limit)
restore() {
    sudo -n nvidia-smi -i "$GPU" -rgc >/dev/null
    sudo -n nvidia-smi -i "$GPU" -pl "$PL_FOUND" >/dev/null
    [[ $PM_FOUND == Enabled ]] || sudo -n nvidia-smi -i "$GPU" -pm 0 >/dev/null
    echo "GPU $GPU restored: clocks unlocked, power limit $PL_FOUND W, persistence $PM_FOUND"
}
trap restore EXIT
trap 'exit 130' INT TERM HUP

node_lock
sudo -n nvidia-smi -i "$GPU" -pm 1 >/dev/null || { echo "needs passwordless sudo for nvidia-smi" >&2; exit 1; }
sudo -n nvidia-smi -i "$GPU" -pl "$BEST_POWER_LIMIT_W" >/dev/null
(( BEST_SM_CLOCK_MHZ > 0 )) && sudo -n nvidia-smi -i "$GPU" -lgc "$BEST_SM_CLOCK_MHZ,$BEST_SM_CLOCK_MHZ" >/dev/null   # 0 = unlocked
docker run --rm --device management.nvidia.com/gpu=all --ipc=host "$IMAGE" \
    /workspace/stream-gpu-test.sh --d "$GPU" "${SIZE[@]}" --dt "$BEST_DTYPE" 2>&1 | tee "$RUN/stream.log"
grep -E "^(Copy|Scale|Add|Triad)" "$RUN/stream.log"

triad=$(awk '$1 ~ /^Triad/ {print $2; exit}' "$RUN/stream.log")
valid=no; [[ -n $triad ]] && valid=yes
record_result triad_mb_s "${triad:-}" "$valid" "device=$GPU"
echo "result: Triad ${triad:-?} MB/s  (run: ${RUN#"$SCC"/})"
