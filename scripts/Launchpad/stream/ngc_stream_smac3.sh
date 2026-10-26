#!/usr/bin/env bash
# Run SMAC3 autotuning of NGC NVIDIA-STREAM (GPU) in the launchpad environment.
#
#   scripts/Launchpad/stream/ngc_stream_smac3.sh                # run until stopped
#   DURATION=300 scripts/Launchpad/stream/ngc_stream_smac3.sh   # stop after 5 min
#
# Ctrl-C stops it; running it again resumes from
# outputs/autotuning/<run_group>/evaluations.jsonl. GPUs go to the container
# via CDI (GPUS, default management.nvidia.com/gpu=all; see hpl/ngc_hpl.sh).

SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC_DIR" || exit 1

# shellcheck disable=SC1091
source ./launchpad_env.sh
cd "$SCC_DIR" || exit 1

if ! sudo -n nvidia-smi -L >/dev/null; then
    echo "needs passwordless sudo for nvidia-smi (SM clock / power limit)" >&2
    exit 1
fi

CONFIG="${1:-scripts/Launchpad/stream/ngc_stream_smac3.yaml}"

if [[ -n "${DURATION:-}" ]]; then
    # SIGINT takes the controller's clean-stop path (kills the running job).
    exec timeout --signal=INT "$DURATION" python scripts/Launchpad/stream/ngc_stream_autotune.py --config "$CONFIG"
fi
exec python scripts/Launchpad/stream/ngc_stream_autotune.py --config "$CONFIG"
