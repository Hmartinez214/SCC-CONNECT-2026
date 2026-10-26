#!/usr/bin/env bash
# Run SMAC3 STREAM autotuning in the launchpad environment.
#
#   scripts/Launchpad/stream/stream_smac3.sh            # run until stopped
#   DURATION=300 scripts/Launchpad/stream/stream_smac3.sh   # stop after 5 min
#
# Ctrl-C stops it; running it again resumes from
# outputs/autotuning/<run_group>/evaluations.jsonl.

SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC_DIR" || exit 1

# shellcheck source=SCRIPTDIR/stream_env.sh
source scripts/Launchpad/stream/stream_env.sh || exit 1

CONFIG="${1:-scripts/Launchpad/stream/stream_smac3.yaml}"
echo "icx: $(command -v icx)  mpiicx: $(command -v mpiicx)"

if [[ -n "${DURATION:-}" ]]; then
    # SIGINT takes the controller's clean-stop path (kills the running job).
    exec timeout --signal=INT "$DURATION" python scripts/Launchpad/stream/stream_autotune.py --config "$CONFIG"
fi
exec python scripts/Launchpad/stream/stream_autotune.py --config "$CONFIG"
