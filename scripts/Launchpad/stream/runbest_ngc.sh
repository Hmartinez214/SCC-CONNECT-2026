#!/usr/bin/env bash
# NVIDIA-STREAM (GPU, NGC hpc-benchmarks 26.02.01) on one H100 NVL with the
# best configuration found on Launchpad: the top entry of outputs/best/ngc_stream.json.
#
#   scripts/Launchpad/stream/runbest_ngc.sh
#
# The configuration's GPU, array size and element type; its SM clock is
# locked and power limit set for the run (persistence mode on), all restored
# after. Waits for the node lock. Log: outputs/runbest/ngc_stream_<time>/

set -uo pipefail
SCC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
IMAGE=nvcr.io/nvidia/hpc-benchmarks:26.02.01

# ---- best configuration -> BEST_* variables (every key of the top entry) ----
eval "$(python3 - "$SCC/outputs/best/ngc_stream.json" <<'EOF'
import json, shlex, sys
top = json.load(open(sys.argv[1]))["top"][0]
v = {**top["configuration"], "OBJECTIVE": top["objective"], "SOURCE": f"{top['run_group']} attempt {top['attempt']}"}
for key, value in v.items():
    print(f"BEST_{key.upper()}={shlex.quote(str(value))}")
EOF
)"
[[ -n ${BEST_SOURCE:-} ]] || { echo "could not read outputs/best/ngc_stream.json" >&2; exit 1; }
echo "best: $BEST_SOURCE ($BEST_OBJECTIVE MB/s Triad)"
GPU=$BEST_DEVICE
SIZE=(); (( BEST_ARRAY_SIZE > 0 )) && SIZE=(--n "$BEST_ARRAY_SIZE")      # 0 = the container's default size

RUN="$SCC/outputs/runbest/ngc_stream_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN"

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

exec 9>"$SCC/outputs/autotuning/.node.lock"
flock -n 9 || { echo "waiting for the node lock..."; flock 9; }
sudo -n nvidia-smi -i "$GPU" -pm 1 >/dev/null || { echo "needs passwordless sudo for nvidia-smi" >&2; exit 1; }
sudo -n nvidia-smi -i "$GPU" -pl "$BEST_POWER_LIMIT_W" >/dev/null
(( BEST_SM_CLOCK_MHZ > 0 )) && sudo -n nvidia-smi -i "$GPU" -lgc "$BEST_SM_CLOCK_MHZ,$BEST_SM_CLOCK_MHZ" >/dev/null   # 0 = unlocked
echo "NGC STREAM: GPU $GPU, ${SIZE[*]:-default size} $BEST_DTYPE, SM $BEST_SM_CLOCK_MHZ MHz, $BEST_POWER_LIMIT_W W -> $RUN"
docker run --rm --device management.nvidia.com/gpu=all --ipc=host "$IMAGE" \
    /workspace/stream-gpu-test.sh --d "$GPU" "${SIZE[@]}" --dt "$BEST_DTYPE" 2>&1 | tee "$RUN/stream.log"
grep -E "^(Copy|Scale|Add|Triad)" "$RUN/stream.log"
