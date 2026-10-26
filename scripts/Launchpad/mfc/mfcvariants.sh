#!/usr/bin/env bash
# Build (and optionally run) a matrix of MFC build variants for one case, so
# they can be compared, and so the tuner never spends its time limit compiling.
# Each variant gets its own build directory in libs/clone/MFC/build/ (MFC keys
# them by configuration), so building one never invalidates another.
#
# Usage:
#   scripts/Launchpad/mfc/mfcvariants.sh [options] [CASE] [-- case args]
#
#   CASE                case.py or cases.yaml slug (default: 5eq_rk3_weno3_hllc)
#   --variants "LIST"   space-separated variants; each joins with + any of
#                       fastmath, mixed, single ("base" = none)
#                       (default: "base fastmath mixed fastmath+mixed")
#   --case-opt MODE     off | on | both (default: both)
#   --run               also run each variant once (runmfc.sh) and print a table
#   -n N                ranks for --run (default: number of GPUs)
#   --bind MODE         --bind for --run (default: none)
#   --rdma              --rdma for --run
#   --gbpp G, --steps S passed to the case (benchmarks/)
#   --clean             ./mfc.sh clean first (needed once after changing MFC_CUDA_CC)
#   -j N                build jobs (default: nproc)
#
# Examples:
#   scripts/Launchpad/mfc/mfcvariants.sh                      # build the 8 default variants
#   scripts/Launchpad/mfc/mfcvariants.sh --run --steps 50     # ... and time each one
#   scripts/Launchpad/mfc/mfcvariants.sh --variants "base single" --case-opt on igr
#
# Runs land in outputs/mfc_runs/variants_<time>/<variant>/ (result.json each);
# compare them with `mfccheck.py summary outputs/mfc_runs/variants_<time>`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

die()   { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 2; }
info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
usage() { sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0; }

CASE=5eq_rk3_weno3_hllc
VARIANTS="base fastmath mixed fastmath+mixed"
CASE_OPT=both
RUN=0
CLEAN=0
JOBS="$(nproc)"
RUN_ARGS=()
CASE_ARGS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --variants) shift; VARIANTS="${1:?}" ;;
        --case-opt) shift; CASE_OPT="${1:?}" ;;
        --run)      RUN=1 ;;
        -n)         shift; RUN_ARGS+=(-n "${1:?}") ;;
        --bind)     shift; RUN_ARGS+=(--bind "${1:?}") ;;
        --rdma)     RUN_ARGS+=(--rdma) ;;
        --gbpp)     shift; CASE_ARGS+=(--gbpp "${1:?}") ;;
        --steps)    shift; CASE_ARGS+=(--steps "${1:?}") ;;
        --clean)    CLEAN=1 ;;
        -j)         shift; JOBS="${1:?}" ;;
        -j*)        JOBS="${1#-j}" ;;
        -h|--help)  usage ;;
        --)         shift; CASE_ARGS+=(-- "$@"); break ;;
        -*)         die "unknown option: $1 (see --help)" ;;
        *)          CASE="$1" ;;
    esac
    shift
done

case "$CASE_OPT" in
    off)  OPT_MODES=(0) ;;
    on)   OPT_MODES=(1) ;;
    both) OPT_MODES=(0 1) ;;
    *)    die "--case-opt must be off, on or both" ;;
esac

# variant -> runmfc.sh flags
variant_flags() {
    local v="$1" flags=() precision=double part
    [ "$v" = base ] && { echo ""; return; }
    IFS=+ read -ra parts <<< "$v"
    for part in "${parts[@]}"; do
        case "$part" in
            fastmath) flags+=(--fastmath) ;;
            mixed)    precision=mixed ;;
            single)   precision=single ;;
            *)        die "unknown variant part '$part' in '$v'" ;;
        esac
    done
    flags+=(--precision "$precision")
    echo "${flags[*]}"
}
for v in $VARIANTS; do variant_flags "$v" >/dev/null; done

if [ "$CLEAN" -eq 1 ]; then
    info "Cleaning MFC builds"
    (source "$SCRIPT_DIR/mfcenv.sh" && cd "$REPO_ROOT/libs/clone/MFC" && ./mfc.sh clean)
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$REPO_ROOT/outputs/mfc_runs/variants_$STAMP"
failed=()

for opt in "${OPT_MODES[@]}"; do
    for v in $VARIANTS; do
        name="$v"
        # shellcheck disable=SC2207
        flags=($(variant_flags "$v"))
        if [ "$opt" -eq 1 ]; then
            flags+=(--case-opt)
            name+="+caseopt"
        fi
        if [ "$RUN" -eq 1 ]; then
            info "Building and running $name"
            "$SCRIPT_DIR/runmfc.sh" "${flags[@]}" "${RUN_ARGS[@]}" -j "$JOBS" \
                --out "$OUT" --label "$name" "$CASE" "${CASE_ARGS[@]}" || failed+=("$name")
        else
            info "Building $name"
            "$SCRIPT_DIR/runmfc.sh" "${flags[@]}" "${RUN_ARGS[@]}" -j "$JOBS" --build-only \
                --out "$OUT/builds" --label "$name" "$CASE" "${CASE_ARGS[@]}" || failed+=("$name")
        fi
    done
done

echo
if [ "$RUN" -eq 1 ]; then
    python3 "$SCRIPT_DIR/mfccheck.py" summary "$OUT"
else
    info "Build logs: $OUT/builds/<variant>/build.log"
fi
if [ "${#failed[@]}" -gt 0 ]; then
    printf '\033[1;31mfailed:\033[0m %s\n' "${failed[*]}" >&2
    exit 1
fi
