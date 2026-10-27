#!/usr/bin/env bash
# MFC on both H100 NVLs with the best configuration found on Launchpad, from
# outputs/best/mfc.json (best configuration per case).
#
#   scripts/Launchpad/mfc/runbest.sh                       # the top example (highest throughput)
#   CASE=5eq_rk3_weno3_hllc scripts/Launchpad/mfc/runbest.sh  # that case's best configuration
#
# runmfc.sh builds MFC for the configuration if needed (first run: several
# minutes). Waits for the node lock. Run directory: outputs/runbest/mfc_<time>/

set -uo pipefail
SCC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC" || exit 1

# ---- best configuration -> BEST_* variables (every key of the chosen entry) --
eval "$(python3 - "$SCC/outputs/best/mfc.json" "${CASE:-}" <<'EOF'
import json, shlex, sys
entries = json.load(open(sys.argv[1]))["best_per_case"]
mine = [e for e in entries if e["configuration"]["case"] == sys.argv[2]] if sys.argv[2] else entries[:1]
if not mine:
    print(f"echo 'no case {sys.argv[2]!r} in outputs/best/mfc.json' >&2; exit 1"); sys.exit()
top = mine[0]
v = {**top["configuration"], "OBJECTIVE": top["objective"], "GRIND_NS": top["metrics"].get("grind_ns"),
     "SOURCE": f"{top['run_group']} attempt {top['attempt']}"}
for key, value in v.items():
    print(f"BEST_{key.upper()}={shlex.quote(str(value))}")
EOF
)"
[[ -n ${BEST_SOURCE:-} ]] || { echo "could not read outputs/best/mfc.json" >&2; exit 1; }
echo "best: $BEST_CASE, $BEST_SOURCE (grind $BEST_GRIND_NS ns)"

# Tuner labels -> runmfc.sh options.
ARGS=(-n "$BEST_RANKS" --precision "$BEST_PRECISION" --bind "$BEST_BIND" --gbpp "$BEST_GBPP" --steps "$BEST_STEPS")
[[ $BEST_CASE_OPT == on ]] && ARGS+=(--case-opt)
[[ $BEST_FASTMATH == on ]] && ARGS+=(--fastmath)
[[ $BEST_RDMA_MPI == on ]] && ARGS+=(--rdma)
case ${BEST_IGR_ITER_SOLVER:-case} in
    jacobi) ARGS+=(--param igr_iter_solver=1) ;;   gauss_seidel) ARGS+=(--param igr_iter_solver=2) ;;
esac
[[ ${BEST_NUM_IGR_ITERS:-case} != case ]] && ARGS+=(--param "num_igr_iters=$BEST_NUM_IGR_ITERS")
case ${BEST_UCX_TLS:-default} in
    sm_cuda_ipc) ARGS+=(--env "UCX_TLS=sm,self,cuda_copy,cuda_ipc") ;;   sm_no_ipc) ARGS+=(--env "UCX_TLS=sm,self,cuda_copy") ;;
esac
[[ ${BEST_UCX_RNDV_THRESH:-default} != default ]] && ARGS+=(--env "UCX_RNDV_THRESH=$BEST_UCX_RNDV_THRESH")
[[ ${BEST_UCX_RNDV_SCHEME:-default} != default ]] && ARGS+=(--env "UCX_RNDV_SCHEME=$BEST_UCX_RNDV_SCHEME")

exec 9>"$SCC/outputs/autotuning/.node.lock"
flock -n 9 || { echo "waiting for the node lock..."; flock 9; }
echo "runmfc.sh ${ARGS[*]} $BEST_CASE"
scripts/Launchpad/mfc/runmfc.sh --out "$SCC/outputs/runbest" --label "mfc_$(date +%Y%m%d_%H%M%S)" "${ARGS[@]}" "$BEST_CASE"
