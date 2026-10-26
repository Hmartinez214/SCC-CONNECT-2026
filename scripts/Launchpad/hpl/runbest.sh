#!/usr/bin/env bash
# Intel HPL (MKL mp_linpack, CPU) with the best configuration found on Launchpad:
# 4403.6 GFLOPS at N=324520 (hpl_bign.sh, 2026-10-26), ~86 min.
# Config: smac3_intel_hpl_fast attempt 355 (outputs/best/hpl_intel.json).
#
#   scripts/Launchpad/hpl/runbest.sh              # N=324520
#   N=90482 scripts/Launchpad/hpl/runbest.sh      # the tuner's size (~2 min, 4287.9 GFLOPS)
#
# One MPI rank over both sockets (HPL_HOST_NODE=0-1), one thread per physical
# core (CPUs 0-63), uncore capped at 1.0 GHz for the run (restored after).
# Waits for the node lock. Log and HPL.dat: outputs/runbest/hpl_intel_<time>/

set -uo pipefail
SCC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC" || exit 1
N="${N:-324520}"
RUN="$SCC/outputs/runbest/hpl_intel_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN"

# Intel MPI + MKL's Intel HPL binary (spack).
source libs/spack/share/spack/setup-env.sh >/dev/null
spack load intel-oneapi-mpi intel-oneapi-mkl || exit 1
PATH="$(spack location -i intel-oneapi-mpi)/mpi/latest/bin:$PATH"
mpirun --version 2>/dev/null | grep -q "Intel(R) MPI" || { echo "mpirun is not Intel MPI" >&2; exit 1; }
XHPL="$(spack location -i intel-oneapi-mkl)/mkl/latest/share/mkl/benchmarks/mp_linpack/xhpl_intel64_dynamic"
[[ -x $XHPL ]] || { echo "Intel HPL not found: $XHPL" >&2; exit 1; }
taskset -cp 0-"$(( $(nproc --all) - 1 ))" $$ >/dev/null    # undo any inherited CPU restriction (e.g. tmux)

cat > "$RUN/HPL.dat" <<EOF
HPLinpack benchmark input file
Innovative Computing Laboratory, University of Tennessee
HPL.out      output file name (if any)
6            device out (6=stdout,7=stderr,file)
1            # of problems sizes (N)
$N           Ns
1            # of NBs
760          NBs
0            PMAP process mapping (0=Row-,1=Column-major)
1            # of process grids (P x Q)
1            Ps
1            Qs
16.0         threshold
1            # of panel fact
0            PFACTs (0=left, 1=Crout, 2=Right)
1            # of recursive stopping criterium
1            NBMINs (>= 1)
1            # of panels in recursion
2            NDIVs
1            # of recursive panel fact.
2            RFACTs (0=left, 1=Crout, 2=Right)
1            # of broadcast
2            BCASTs (0=1rg,1=1rM,2=2rg,3=2rM,4=Lng,5=LnM)
1            # of lookahead depth
5            DEPTHs (>=0)
2            SWAP (0=bin-exch,1=long,2=mix)
64           swapping threshold
1            L1 in (0=transposed,1=no-transposed) form
0            U  in (0=transposed,1=no-transposed) form
1            Equilibration (0=no,1=yes)
8            memory alignment in double (> 0)
EOF

# Placement and Intel HPL knobs.
for v in $(compgen -e | grep '^I_MPI_PIN'); do unset "$v"; done
unset OMP_NUM_THREADS MKL_NUM_THREADS HPL_NUMTHREADS GLIBC_TUNABLES
export I_MPI_PIN=1 I_MPI_PIN_DOMAIN='[0xffffffffffffffff]' I_MPI_DEBUG=4   # rank 0 -> CPUs 0-63
export HPL_HOST_NODE=0-1
export HPL_LOG=2 HPL_LARGEPAGE=0 HPL_HT=0 HPL_PAIR=4 HPL_UMODE=2 HPL_SWAPWIDTH=192 HPL_SWAPTHRESHOLD=100

# Uncore cap for the run; restored on any exit.
UNC=(/sys/devices/system/cpu/intel_uncore_frequency/package_*_die_*)
UNC_FOUND=(); for d in "${UNC[@]}"; do UNC_FOUND+=("$(cat "$d/max_freq_khz")"); done
restore() {
    local i=0
    for d in "${UNC[@]}"; do echo "${UNC_FOUND[$i]}" | sudo -n tee "$d/max_freq_khz" >/dev/null; i=$((i + 1)); done
    echo "uncore max restored to ${UNC_FOUND[*]} kHz"
}
trap restore EXIT
trap 'exit 130' INT TERM HUP

exec 9>"$SCC/outputs/autotuning/.node.lock"
flock -n 9 || { echo "waiting for the node lock..."; flock 9; }
for d in "${UNC[@]}"; do echo 1000000 | sudo -n tee "$d/max_freq_khz" >/dev/null || { echo "needs passwordless sudo" >&2; exit 1; }; done
echo "Intel HPL N=$N, uncore max 1000 MHz -> $RUN"
cd "$RUN" && mpirun -np 1 "$XHPL" 2>&1 | tee hpl.log
grep -E "^W[RC]|PASSED|FAILED" "$RUN/hpl.log"
