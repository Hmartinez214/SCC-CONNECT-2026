#!/usr/bin/env bash
# Run SMAC3 WRF autotuning on Launchpad.
#
#   scripts/Launchpad/wrf/wrf_smac3.sh                 # run until the budget is used
#   DURATION=3600 scripts/Launchpad/wrf/wrf_smac3.sh   # stop after an hour
#
# Ctrl-C stops it; running it again resumes from
# outputs/wrf/autotuning/wrf_smac3/evaluations.jsonl. Shares the node lock with
# the other tuners, so runs never overlap. Uses the system Open MPI 4.1.6
# (/usr/bin/mpirun), which both WRF builds link against.

SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC_DIR" || exit 1

PY="$SCC_DIR/venv/bin/python"
"$PY" -c "import hpc_autotuner, smac" 2>/dev/null \
    || { echo "hpc_autotuner/smac not importable from $PY" >&2; exit 1; }

CONFIG="${1:-scripts/Launchpad/wrf/wrf_smac3.yaml}"

if [[ -n "${DURATION:-}" ]]; then
    exec timeout --signal=INT "$DURATION" "$PY" scripts/Launchpad/wrf/wrf_autotune.py --config "$CONFIG"
fi
exec "$PY" scripts/Launchpad/wrf/wrf_autotune.py --config "$CONFIG"
