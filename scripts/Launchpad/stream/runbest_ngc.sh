#!/usr/bin/env bash
# NVIDIA-STREAM (GPU, NGC hpc-benchmarks 26.02.01) on one H100 NVL with the
# best configuration found on Launchpad: 3446.5 GB/s Triad, ~20 s.
# Config: ngc_stream_smac3 attempt 4 (outputs/best/ngc_stream.json).
#
#   scripts/Launchpad/stream/runbest_ngc.sh
#
# GPU 0, 2^30 fp32 elements, SM clock locked at 1785 MHz and power limit
# 310 W for the run (persistence mode on); all restored after.
# Waits for the node lock. Log: outputs/runbest/ngc_stream_<time>/

set -uo pipefail
SCC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
IMAGE=nvcr.io/nvidia/hpc-benchmarks:26.02.01
GPU=0
RUN="$SCC/outputs/runbest/ngc_stream_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN"

PM_FOUND=$(nvidia-smi -i $GPU --format=csv,noheader,nounits --query-gpu=persistence_mode)
PL_FOUND=$(nvidia-smi -i $GPU --format=csv,noheader,nounits --query-gpu=power.limit)
restore() {
    sudo -n nvidia-smi -i $GPU -rgc >/dev/null
    sudo -n nvidia-smi -i $GPU -pl "$PL_FOUND" >/dev/null
    [[ $PM_FOUND == Enabled ]] || sudo -n nvidia-smi -i $GPU -pm 0 >/dev/null
    echo "GPU $GPU restored: clocks unlocked, power limit $PL_FOUND W, persistence $PM_FOUND"
}
trap restore EXIT
trap 'exit 130' INT TERM HUP

exec 9>"$SCC/outputs/autotuning/.node.lock"
flock -n 9 || { echo "waiting for the node lock..."; flock 9; }
sudo -n nvidia-smi -i $GPU -pm 1 >/dev/null || { echo "needs passwordless sudo for nvidia-smi" >&2; exit 1; }
sudo -n nvidia-smi -i $GPU -pl 310 >/dev/null
sudo -n nvidia-smi -i $GPU -lgc 1785,1785 >/dev/null
echo "NGC STREAM: GPU $GPU, SM 1785 MHz, 310 W -> $RUN"
docker run --rm --device management.nvidia.com/gpu=all --ipc=host "$IMAGE" \
    /workspace/stream-gpu-test.sh --d $GPU --n 1073741824 --dt fp32 2>&1 | tee "$RUN/stream.log"
grep -E "^(Copy|Scale|Add|Triad)" "$RUN/stream.log"
