#!/usr/bin/env bash
# Run SMAC3 MFC autotuning on Launchpad.
#
#   scripts/Launchpad/mfc/mfc_smac3.sh                 # run until stopped
#   DURATION=3600 scripts/Launchpad/mfc/mfc_smac3.sh   # stop after an hour
#   scripts/Launchpad/mfc/mfc_smac3.sh path/to/other.yaml
#
# Ctrl-C stops it; running it again resumes from
# outputs/autotuning/mfc_smac3/evaluations.jsonl. Shares the node lock with
# the HPL and STREAM tuners, so their runs never overlap.
#
# Uses the repo venv's Python (HPC-Tools autotuner) without sourcing
# launchpad_env.sh: that loads a newer HPC-X and updates submodules (MFC
# included). Each run sets up its own MFC environment via runmfc.sh.

SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC_DIR" || exit 1

PY="$SCC_DIR/venv/bin/python"
"$PY" -c "import hpc_autotuner, smac" 2>/dev/null \
    || { echo "hpc_autotuner/smac not importable from $PY" >&2; exit 1; }

CONFIG="${1:-scripts/Launchpad/mfc/mfc_smac3.yaml}"

if [[ -n "${DURATION:-}" ]]; then
    # SIGINT takes the controller's clean-stop path (kills the running job).
    exec timeout --signal=INT "$DURATION" "$PY" scripts/Launchpad/mfc/mfc_autotune.py --config "$CONFIG"
fi
exec "$PY" scripts/Launchpad/mfc/mfc_autotune.py --config "$CONFIG"
