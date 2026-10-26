#!/usr/bin/env bash
# Build (if needed) and run one MFC case on the GPUs with a chosen
# configuration, in its own run directory, and record the result.
#
# Usage:
#   scripts/Launchpad/mfc/runmfc.sh [options] CASE [-- case args]
#
#   CASE              a case.py, a slug from cases.yaml (e.g. 5eq_rk3_weno3_hllc), or an
#                     MFC examples/ directory name (e.g. 2D_shockbubble)
#   -n N              MPI ranks (default: number of GPUs; MFC puts rank i on GPU i mod #GPUs)
#   --case-opt        build with --case-optimization (case parameters compiled in)
#   --fastmath        build with -gpu=fastmath
#   --precision P     double (default) | mixed | single
#   --rdma            run with rdma_mpi = 'T' (GPU buffers passed straight to MPI)
#   --bind MODE       rank pinning: none (default) | numa | cores (see launchpad_bind.sh)
#   --gbpp G          pass --gbpp G to the case (benchmarks/: GB of GPU memory per rank)
#   --steps S         pass --steps S to the case (benchmarks/: time steps); for cases.yaml
#                     entries with `steps: param` set t_step_start/stop/save instead
#   --param K=V       override case parameter K (repeatable; V parsed as JSON if it can be)
#   --env K=V         set environment variable K for the ranks (repeatable; e.g. UCX_TLS=...)
#   --label NAME      run directory name (default: <time>_<case>_<config>)
#   --out DIR         parent of the run directory (default: outputs/mfc_runs)
#   --keep            keep the field output (D/, p_all/, restart_data/); mfccheck.py needs D/
#   --build-only      build pre_process + simulation for this configuration and exit
#   -j N              build jobs (default: nproc)
#
# Examples:
#   scripts/Launchpad/mfc/runmfc.sh 5eq_rk3_weno3_hllc
#   scripts/Launchpad/mfc/runmfc.sh --case-opt --rdma --bind numa 5eq_rk3_weno3_hllc --steps 50
#   scripts/Launchpad/mfc/runmfc.sh --keep --gbpp 1 --label ref igr
#
# The run directory gets case.py (a stand-in that runs the real case and
# applies the overrides), mfc.log, summary.yaml and result.json. The last line
# printed is "MFC_RESULT {json}" (what mfc_autotune.py parses). Exit status is
# 0 only if the run produced a grind time.
#
# MFC remembers the last build configuration in build/lock.yaml, so this
# passes every config flag explicitly and puts lock.yaml back afterwards.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
MFC_DIR="$REPO_ROOT/libs/clone/MFC"

die()   { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 2; }
usage() { sed -n '2,38p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0; }

RANKS=""
CASE_OPT=0
FASTMATH=0
PRECISION=double
RDMA=0
BIND=none
LABEL=""
OUT="$REPO_ROOT/outputs/mfc_runs"
KEEP=0
BUILD_ONLY=0
JOBS="$(nproc)"
CASE=""
CASE_ARGS=()
PARAMS=()
ENVS=()
STEPS=""

while [ $# -gt 0 ]; do
    case "$1" in
        -n)           shift; RANKS="${1:?-n needs a number}" ;;
        --case-opt)   CASE_OPT=1 ;;
        --fastmath)   FASTMATH=1 ;;
        --precision)  shift; PRECISION="${1:?}" ;;
        --rdma)       RDMA=1 ;;
        --bind)       shift; BIND="${1:?}" ;;
        --gbpp)       shift; CASE_ARGS+=(--gbpp "${1:?}") ;;
        --steps)      shift; STEPS="${1:?}"; CASE_ARGS+=(--steps "$STEPS") ;;
        --param)      shift; PARAMS+=("${1:?}") ;;
        --env)        shift; [[ "${1:?}" == *=* ]] || die "--env $1: expected KEY=VALUE"; ENVS+=("$1") ;;
        --label)      shift; LABEL="${1:?}" ;;
        --out)        shift; OUT="${1:?}" ;;
        --keep)       KEEP=1 ;;
        --build-only) BUILD_ONLY=1 ;;
        -j)           shift; JOBS="${1:?}" ;;
        -j*)          JOBS="${1#-j}" ;;
        -h|--help)    usage ;;
        --)           shift; CASE_ARGS+=("$@"); break ;;
        -*)           die "unknown option: $1 (see --help)" ;;
        *)            [ -z "$CASE" ] || die "two cases given: $CASE, $1"; CASE="$1" ;;
    esac
    shift
done

[ -n "$CASE" ] || die "no case given (see --help)"
case "$PRECISION" in double|mixed|single) ;; *) die "--precision must be double, mixed or single" ;; esac
case "$BIND" in none|numa|cores) ;; *) die "--bind must be none, numa or cores" ;; esac

# launchpad_env.sh loads HPC-X from a newer SDK; its prefixes would point the
# SDK's mpirun at the wrong Open MPI.
unset OPAL_PREFIX PMIX_INSTALL_PREFIX
# shellcheck source=mfcenv.sh
source "$SCRIPT_DIR/mfcenv.sh"
export MFC_NVHPC_VERSION
# GCC flags from the other setup scripts break the NVIDIA compilers.
unset CFLAGS CXXFLAGS FFLAGS FCFLAGS LDFLAGS

[ -n "$RANKS" ] || RANKS="$(nvidia-smi -L | wc -l)"

PY=python3
resolved="$($PY "$SCRIPT_DIR/mfclib.py" resolve "$CASE")" || exit 2
IFS=$'\t' read -r CASE_PY SLUG FIXED_ARGS STEPS_PARAM <<< "$resolved"
mapfile -t fixed < <($PY -c 'import json,sys; print("\n".join(json.loads(sys.argv[1])))' "$FIXED_ARGS")
[ "${#fixed[@]}" -gt 0 ] && [ -n "${fixed[0]}" ] && CASE_ARGS=("${fixed[@]}" "${CASE_ARGS[@]}")

# Cases without a --steps argument (cases.yaml `steps: param`, e.g. examples/):
# set the run length through the case parameters. Unknown arguments such as
# --gbpp are ignored by those scripts.
# The case gets PASS_ARGS; CASE_ARGS (as given) is what result.json records.
PASS_ARGS=("${CASE_ARGS[@]}")
if [ "$STEPS_PARAM" = 1 ]; then
    [ -n "$STEPS" ] && PARAMS=(t_step_start=0 "t_step_stop=$STEPS" "t_step_save=$STEPS" "${PARAMS[@]}")
    # Their scripts may reject unknown arguments; drop --gbpp/--steps.
    PASS_ARGS=()
    for ((i = 0; i < ${#CASE_ARGS[@]}; i++)); do
        case "${CASE_ARGS[$i]}" in
            --gbpp|--steps) i=$((i + 1)) ;;
            *) PASS_ARGS+=("${CASE_ARGS[$i]}") ;;
        esac
    done
fi

# Case parameter overrides -> JSON object.
OVERRIDES="$($PY - "$RDMA" "${PARAMS[@]}" <<'EOF'
import json, sys
rdma, params = sys.argv[1], sys.argv[2:]
out = {"rdma_mpi": "T"} if rdma == "1" else {}
for p in params:
    if "=" not in p:
        raise SystemExit(f"--param {p}: expected KEY=VALUE")
    k, v = p.split("=", 1)
    try:
        out[k] = json.loads(v)
    except json.JSONDecodeError:
        out[k] = v
print(json.dumps(out))
EOF
)"

# Explicit MFC config flags (MFC would otherwise reuse lock.yaml's values).
CFG=(--gpu acc --mpi --no-debug --no-reldebug --no-gcov --no-unified)
[ "$FASTMATH" -eq 1 ] && CFG+=(--fastmath) || CFG+=(--no-fastmath)
case "$PRECISION" in
    double) CFG+=(--no-mixed --no-single) ;;
    mixed)  CFG+=(--mixed --no-single) ;;
    single) CFG+=(--no-mixed --single) ;;
esac
[ "$CASE_OPT" -eq 1 ] && CFG+=(--case-optimization)

tag="$PRECISION"
[ "$CASE_OPT" -eq 1 ] && tag+="-caseopt"
[ "$FASTMATH" -eq 1 ] && tag+="-fastmath"
[ "$RDMA" -eq 1 ] && tag+="-rdma"
tag+="-$BIND-n$RANKS"
[ -n "$LABEL" ] || LABEL="$(date +%Y%m%d-%H%M%S)_${SLUG}_${tag}"
RUN_DIR="$OUT/$LABEL"
[ -e "$RUN_DIR" ] && die "run directory exists: $RUN_DIR"
mkdir -p "$RUN_DIR"
RUN_DIR="$(cd "$RUN_DIR" && pwd)"

$PY "$SCRIPT_DIR/mfclib.py" wrap "$RUN_DIR" "$CASE_PY" "$RANKS" "$OVERRIDES"

LOCK="$MFC_DIR/build/lock.yaml"
if [ -f "$LOCK" ]; then
    cp "$LOCK" "$RUN_DIR/lock.yaml.saved"
    trap 'cp "$RUN_DIR/lock.yaml.saved" "$LOCK" 2>/dev/null || true' EXIT
fi

echo "case:      $CASE_PY ${CASE_ARGS[*]}"
echo "config:    $tag  overrides=$OVERRIDES"
echo "run dir:   $RUN_DIR"
echo "compiler:  $(nvfortran --version | grep -m1 -oE 'nvfortran [0-9.]+'), MFC_CUDA_CC=${MFC_CUDA_CC:-unset}"

cd "$MFC_DIR"
if [ "$BUILD_ONLY" -eq 1 ]; then
    ./mfc.sh build -i "$RUN_DIR/case.py" -t pre_process simulation "${CFG[@]}" -j "$JOBS" \
        -- "${PASS_ARGS[@]}" 2>&1 | tee "$RUN_DIR/build.log"
    exit "${PIPESTATUS[0]}"
fi

export MFC_BIND="$BIND"
export MFC_LAUNCHPAD_BIND="$SCRIPT_DIR/launchpad_bind.sh"
export MFC_MPIRUN_ARGS="${MFC_MPIRUN_ARGS:-}"
for kv in "${ENVS[@]}"; do
    export "${kv?}"
    echo "env:       $kv"
done
ENV_JSON="$($PY -c 'import json,sys; print(json.dumps(dict(a.split("=",1) for a in sys.argv[1:])))' "${ENVS[@]}")"

started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
set +e
./mfc.sh run "$RUN_DIR/case.py" -n "$RANKS" -t pre_process simulation "${CFG[@]}" \
    -c "$SCRIPT_DIR/launchpad.mako" -j "$JOBS" -o "$RUN_DIR/summary.yaml" \
    -- "${PASS_ARGS[@]}" 2>&1 | tee "$RUN_DIR/mfc.log"
rc="${PIPESTATUS[0]}"
set -e

if [ "$KEEP" -eq 0 ]; then
    rm -rf "$RUN_DIR/D" "$RUN_DIR/p_all" "$RUN_DIR/restart_data" "$RUN_DIR/silo_hdf5"
fi

status=ok
[ "$rc" -eq 0 ] || status=failed
MFC_COMMIT="$(git -C "$MFC_DIR" rev-parse --short HEAD)"
export LABEL started RUN_DIR SLUG CASE_PY RANKS CASE_OPT FASTMATH PRECISION RDMA BIND \
       OVERRIDES MFC_COMMIT rc ENV_JSON
META="$($PY - "${CASE_ARGS[@]}" <<'EOF'
import json, os, sys
e = os.environ
print(json.dumps({
    "label": e["LABEL"], "timestamp_utc": e["started"], "run_dir": e["RUN_DIR"],
    "case": e["SLUG"], "case_path": e["CASE_PY"], "case_args": sys.argv[1:],
    "ranks": int(e["RANKS"]), "case_opt": e["CASE_OPT"] == "1", "fastmath": e["FASTMATH"] == "1",
    "precision": e["PRECISION"], "rdma_mpi": e["RDMA"] == "1", "bind": e["BIND"],
    "overrides": json.loads(e["OVERRIDES"]), "env": json.loads(e["ENV_JSON"]),
    "mpirun_args": e.get("MFC_MPIRUN_ARGS", ""),
    "mfc_commit": e["MFC_COMMIT"], "nvhpc": e.get("MFC_NVHPC_VERSION", ""),
    "cuda_cc": e.get("MFC_CUDA_CC", ""), "exit_code": int(e["rc"]),
}))
EOF
)"
$PY "$SCRIPT_DIR/mfclib.py" result "$RUN_DIR" "$status" "$META"
