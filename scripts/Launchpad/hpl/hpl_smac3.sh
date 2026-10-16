#!/usr/bin/env bash
# Run SMAC3 HPL autotuning in the launchpad environment, restarting the
# controller if it dies. Ctrl-C (or tmux kill-session) stops it; running this
# again resumes from outputs/autotuning/smac3_launchpad/evaluations.jsonl.
#
# Normally started through hpl_smac3_tmux.sh.

SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC_DIR" || exit 1

# shellcheck disable=SC1091
source ./launchpad_env.sh
cd "$SCC_DIR" || exit 1

# Spack's HPL: the gcc build linked against MKL + Intel MPI (passes residuals).
# Loading it also puts spack's Intel MPI (mpirun) and MKL on the path.
HPL_SPEC="${HPL_SPEC:-hpl/zqzkvyu}"
spack load "$HPL_SPEC" || exit 1
HPL_EXE="$(spack location -i "$HPL_SPEC")/bin/xhpl"
export HPL_EXE
# Intel's Distribution for LINPACK, prebuilt in that MKL against that Intel MPI
# (used by the default config, hpl_smac3.yaml; netlib: hpl_netlib_smac3.yaml).
INTEL_HPL_EXE="$(spack location -i intel-oneapi-mkl)/mkl/latest/share/mkl/benchmarks/mp_linpack/xhpl_intel64_dynamic"
[[ -x "$INTEL_HPL_EXE" ]] || { echo "Intel HPL not found: $INTEL_HPL_EXE" >&2; exit 1; }
export INTEL_HPL_EXE

# For the MPI-only netlib build (sequential MKL). The Intel launch unsets
# them so its ranks thread across their NUMA node. Pinning is set per run.
export MKL_NUM_THREADS=1
export OMP_NUM_THREADS=1

CONFIG="${1:-scripts/Launchpad/hpl/hpl_smac3.yaml}"

echo "HPL: $INTEL_HPL_EXE (Intel) / $HPL_EXE (netlib)"
echo "mpirun: $(command -v mpirun)"

while true; do
    python scripts/Launchpad/hpl/hpl_autotune.py --config "$CONFIG"
    rc=$?
    case $rc in
        0)   echo "Autotuner finished its budget."; break ;;
        3)   echo "Another controller holds the lock; not starting a second one."; break ;;
        129|130|143) echo "Autotuner stopped by signal; run again to resume."; break ;;
    esac
    echo "Autotuner exited with code $rc at $(date); restarting in 60s (Ctrl-C to stop)."
    sleep 60 || break
done
