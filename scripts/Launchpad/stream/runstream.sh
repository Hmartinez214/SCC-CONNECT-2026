#!/usr/bin/env bash
# Run STREAM once with the best configuration the SMAC3 tuner found: same
# source, compile flags, threading / MPI placement, and machine-wide settings
# (uncore, THP, EPB, C-states; set for the run and restored after).
#
#   scripts/Launchpad/stream/runstream.sh                 # best run of the current run group
#   ATTEMPT=1941 scripts/Launchpad/stream/runstream.sh    # a specific attempt
#   CONFIG=path/to.yaml scripts/Launchpad/stream/runstream.sh
#
# Waits for the node lock (shared with the HPL/STREAM tuners and hpl_bign.sh).
# Job script and log: outputs/stream_best/<timestamp>.{sh,log}

SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC_DIR" || exit 1
# shellcheck source=SCRIPTDIR/stream_env.sh
source scripts/Launchpad/stream/stream_env.sh || exit 1
set -euo pipefail

CONFIG="${CONFIG:-scripts/Launchpad/stream/stream_smac3.yaml}"
OUT="$SCC_DIR/outputs/stream_best"
STAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT"

venv/bin/python - "$CONFIG" "${ATTEMPT:-}" "$OUT/$STAMP.sh" <<'EOF'
import json, sys
from pathlib import Path
sys.path[:0] = ["scripts/Launchpad", "scripts/Launchpad/stream"]
import autotune_local
import stream_autotune
from hpc_autotuner.experiments.config import ExperimentConfig

config_path, attempt, script = sys.argv[1:]
config = ExperimentConfig.from_yaml(config_path)
app = stream_autotune.StreamApplication.from_config(config)
log = Path(config.resolved_project_root()) / "outputs/autotuning" / config.run_group / "evaluations.jsonl"
runs = {a: r for a, r in autotune_local.final_records(log).items() if r.get("success") and r.get("objective")}
best = runs[int(attempt)] if attempt else max(runs.values(), key=lambda r: r["objective"])
conf = app.resolve_configuration(best["configuration"])
shown = {k: v for k, v in conf.items() if k not in ("array_memory_gib", "total_threads")}
Path(script).write_text("set -euo pipefail\n" + app.command(conf) + "\n")
print(f"{config.run_group} attempt {best['attempt']}: {best['objective'] / 1e3:.1f} GB/s Triad when tuned")
print(json.dumps(shown))
EOF

echo "job script: $OUT/$STAMP.sh"
flock -n "$SCC_DIR/outputs/autotuning/.node.lock" true || echo "waiting for the node lock (a tuner or hpl_bign.sh run is using the node)..."
export SLURM_JOB_ID="best_$STAMP"
flock "$SCC_DIR/outputs/autotuning/.node.lock" bash "$OUT/$STAMP.sh" 2>&1 | tee "$OUT/$STAMP.log"
grep -E "^(Copy|Scale|Add|Triad):|Solution Validates" "$OUT/$STAMP.log" || { echo "STREAM did not validate" >&2; exit 1; }
