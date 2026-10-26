#!/usr/bin/env bash
# MFC on both H100 NVLs with the best configuration found on Launchpad: the
# top example, 2D_mhd_magnetic_vortex, grind time 0.156 ns (throughput 6.41).
# Config: mfc_grid_2D_mhd_magnetic_vortex attempt 4 (outputs/best/mfc.json,
# which also has the best configuration for each of the other cases).
#
#   scripts/Launchpad/mfc/runbest.sh
#
# 2 ranks (one per GPU) pinned to cores, double precision, case-optimized
# build with -gpu=fastmath, GPU-direct (RDMA) MPI, 16 GB per GPU, 50 steps.
# runmfc.sh builds MFC for this configuration if needed (first run: several
# minutes). Waits for the node lock. Run directory: outputs/runbest/mfc_<time>/

set -uo pipefail
SCC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC" || exit 1
LABEL="mfc_$(date +%Y%m%d_%H%M%S)"

exec 9>"$SCC/outputs/autotuning/.node.lock"
flock -n 9 || { echo "waiting for the node lock..."; flock 9; }
scripts/Launchpad/mfc/runmfc.sh --out "$SCC/outputs/runbest" --label "$LABEL" \
    -n 2 --precision double --bind cores --gbpp 16 --steps 50 --case-opt --fastmath --rdma \
    2D_mhd_magnetic_vortex
