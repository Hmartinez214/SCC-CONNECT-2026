#!/usr/bin/env bash
# Run SMAC3 STREAM autotuning in the launchpad environment.
#
#   scripts/Launchpad/stream/stream_smac3.sh            # run until stopped
#   DURATION=300 scripts/Launchpad/stream/stream_smac3.sh   # stop after 5 min
#
# Ctrl-C stops it; running it again resumes from
# outputs/autotuning/stream_smac3/evaluations.jsonl.

SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC_DIR" || exit 1

# shellcheck disable=SC1091
source ./launchpad_env.sh
cd "$SCC_DIR" || exit 1

# Same icx as the earlier random sweep (its results are the priors).
spack load intel-oneapi-compilers || exit 1

CONFIG="${1:-scripts/Launchpad/stream/stream_smac3.yaml}"
echo "icx: $(command -v icx)"

if [[ -n "${DURATION:-}" ]]; then
    # SIGINT takes the controller's clean-stop path (kills the running job).
    exec timeout --signal=INT "$DURATION" python scripts/Launchpad/stream/stream_autotune.py --config "$CONFIG"
fi
exec python scripts/Launchpad/stream/stream_autotune.py --config "$CONFIG"
