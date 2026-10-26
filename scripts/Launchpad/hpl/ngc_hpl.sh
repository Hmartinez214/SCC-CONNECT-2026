#!/usr/bin/env bash
# GPU HPL from NVIDIA's NGC container (HPL-NVIDIA) on the Launchpad node's
# 2x H100 NVL. The x86 container has no CPU-only HPL.
#
#   scripts/Launchpad/hpl/ngc_hpl.sh                 # container's 2-GPU sample (N=136608, NB=1024)
#   N=160000 NB=1024 scripts/Launchpad/hpl/ngc_hpl.sh
#   DAT=path/to/HPL.dat scripts/Launchpad/hpl/ngc_hpl.sh
#
# Both GPUs sit on socket 0 (NUMA node 0, CPUs 0-31; nvidia-smi topo -m), so
# each rank gets half of socket 0's cores and node-0 memory. Holds the node
# lock shared with the HPL/STREAM tuners. SYS_NICE lets numactl bind memory
# (Docker's seccomp blocks set_mempolicy without it). Log: outputs/ngc_hpl/<timestamp>.log

set -euo pipefail
SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
IMAGE="${IMAGE:-nvcr.io/nvidia/hpc-benchmarks:26.02.01}"
OUT="$SCC_DIR/outputs/ngc_hpl"
mkdir -p "$OUT"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$OUT/$STAMP.log"

# GPUs via CDI. Not --gpus all: /var/run/cdi/nvidia.yaml predates the
# Oct 17 driver upgrade (595.71.05) and lists a library that no longer
# exists; the management spec is current.
GPUS="${GPUS:-management.nvidia.com/gpu=all}"

SAMPLE=/workspace/hpl-linux-x86_64/sample-dat/HPL-2GPUs.dat
MOUNT=()
if [[ -n "${DAT:-}" ]]; then
    cp "$DAT" "$OUT/$STAMP.dat"
elif [[ -n "${N:-}" || -n "${NB:-}" ]]; then
    # The sample with N / NB replaced (lines 6 and 8 of HPL.dat).
    docker run --rm --entrypoint cat "$IMAGE" "$SAMPLE" \
        | awk -v n="${N:-}" -v nb="${NB:-}" 'NR==6 && n {$1=n} NR==8 && nb {$1=nb} {print}' > "$OUT/$STAMP.dat"
fi
if [[ -f "$OUT/$STAMP.dat" ]]; then
    MOUNT=(-v "$OUT/$STAMP.dat:/run/HPL.dat:ro")
    DATFILE=/run/HPL.dat
else
    DATFILE=$SAMPLE
fi

echo "image: $IMAGE  dat: ${DAT:-$DATFILE}  log: $LOG"
flock "$SCC_DIR/outputs/autotuning/.node.lock" \
    docker run --rm --device "$GPUS" --cap-add SYS_NICE --ipc=host --ulimit memlock=-1 --ulimit stack=67108864 "${MOUNT[@]}" "$IMAGE" \
    mpirun --allow-run-as-root --bind-to none -np 2 \
    /workspace/hpl-linux-x86_64/hpl.sh --dat "$DATFILE" --no-multinode \
    --gpu-affinity 0:1 --cpu-affinity 0-15:16-31 --mem-affinity 0:0 \
    2>&1 | tee "$LOG"
grep -E "^WR|PASSED|FAILED" "$LOG" || true
