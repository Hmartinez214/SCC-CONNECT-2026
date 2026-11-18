#!/usr/bin/env bash
# MFC on both H100 NVLs with the best configuration found on Launchpad, from
# outputs/best/mfc.json (best configuration per case).
#
#   scripts/Launchpad/mfc/runbest.sh                            # the top example (highest throughput)
#   scripts/Launchpad/mfc/runbest.sh CASE=5eq_rk3_weno3_hllc    # that case's best configuration
#   scripts/Launchpad/mfc/runbest.sh CASE=... PRECISION=single  # override configuration keys
#   scripts/Launchpad/mfc/runbest.sh --dry-run CASE=...         # show the runmfc.sh command, run nothing
#
# CASE may also be given as an environment variable. runmfc.sh builds MFC for
# the configuration if needed (first run: several minutes). Waits for the node
# lock. Run directory: outputs/mfc/runbest/mfc_<time>/ (runmfc.sh's files and
# result.json, plus runbest.json); one line per run in outputs/mfc/runbest/results.tsv.

set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/node.sh"
parse_args "CASE" "$@"
cd "$SCC" || exit 1

# ---- best configuration -> BEST_* variables (every key of the chosen entry) --
eval "$(python3 - "$SCC/outputs/best/mfc.json" "${CASE:-}" "${OVERRIDES[@]}" <<'PY'
import json, sys
sys.path.insert(0, "scripts/Launchpad/lib")
from best import emit, load
v = load(sys.argv[1], sys.argv[3:], entries="best_per_case", where={"case": sys.argv[2]} if sys.argv[2] else None)
case = v["case"]
top = next(e for e in json.load(open(sys.argv[1]))["best_per_case"] if e["configuration"]["case"] == case)
emit({**v, "GRIND_NS": top["metrics"].get("grind_ns")})
PY
)"
[[ -n ${BEST_SOURCE:-} ]] || exit 1
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

LABEL="mfc_$(date +%Y%m%d_%H%M%S)"
echo "runmfc.sh ${ARGS[*]} $BEST_CASE -> outputs/mfc/runbest/$LABEL"
(( DRY_RUN )) && { echo "dry run: nothing was run"; exit 0; }
node_lock
scripts/Launchpad/mfc/runmfc.sh --out "$OUTPUTS/mfc/runbest" --label "$LABEL" "${ARGS[@]}" "$BEST_CASE"

RUN="$OUTPUTS/mfc/runbest/$LABEL" RUN_BENCH=mfc RESULT_JSON=runbest.json
read -r grind status < <(python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r.get("grind_ns", ""), r.get("status", "failed"))' \
                         "$RUN/result.json" 2>/dev/null)
record_result grind_ns "${grind:-}" "$([[ ${status:-} == ok ]] && echo yes || echo no)" "case=$BEST_CASE"
echo "result: grind ${grind:-?} ns (best $BEST_GRIND_NS), status ${status:-failed}  (run: ${RUN#"$SCC"/})"
