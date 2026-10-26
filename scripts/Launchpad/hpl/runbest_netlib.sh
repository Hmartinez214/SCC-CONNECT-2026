#!/usr/bin/env bash
# netlib HPL (spack hpl/zqzkvyu: gcc + MKL + Intel MPI, CPU) with the best
# configuration found on Launchpad: 3171.3 GFLOPS at N=93233, ~3 min.
# Config: smac3_launchpad_ranks attempt 110 (outputs/best/hpl_netlib.json).
#
#   scripts/Launchpad/hpl/runbest_netlib.sh
#
# 128 single-threaded ranks (P=16 x Q=8), two per physical core (rank 2k and
# 2k+1 share core k and its hyperthread sibling), transparent huge pages via
# glibc. Waits for the node lock. Log and HPL.dat: outputs/runbest/hpl_netlib_<time>/

set -uo pipefail
SCC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC" || exit 1
RUN="$SCC/outputs/runbest/hpl_netlib_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN"

source libs/spack/share/spack/setup-env.sh >/dev/null
spack load hpl/zqzkvyu intel-oneapi-mpi || exit 1
PATH="$(spack location -i intel-oneapi-mpi)/mpi/latest/bin:$PATH"
mpirun --version 2>/dev/null | grep -q "Intel(R) MPI" || { echo "mpirun is not Intel MPI" >&2; exit 1; }
XHPL="$(spack location -i hpl/zqzkvyu)/bin/xhpl"
taskset -cp 0-"$(( $(nproc --all) - 1 ))" $$ >/dev/null    # undo any inherited CPU restriction (e.g. tmux)

cat > "$RUN/HPL.dat" <<'EOF'
HPLinpack benchmark input file
Innovative Computing Laboratory, University of Tennessee
HPL.out      output file name (if any)
6            device out (6=stdout,7=stderr,file)
1            # of problems sizes (N)
93233        Ns
1            # of NBs
172          NBs
1            PMAP process mapping (0=Row-,1=Column-major)
1            # of process grids (P x Q)
16           Ps
8            Qs
16.0         threshold
1            # of panel fact
1            PFACTs (0=left, 1=Crout, 2=Right)
1            # of recursive stopping criterium
7            NBMINs (>= 1)
1            # of panels in recursion
5            NDIVs
1            # of recursive panel fact.
1            RFACTs (0=left, 1=Crout, 2=Right)
1            # of broadcast
2            BCASTs (0=1rg,1=1rM,2=2rg,3=2rM,4=Lng,5=LnM)
1            # of lookahead depth
2            DEPTHs (>=0)
2            SWAP (0=bin-exch,1=long,2=mix)
64           swapping threshold
1            L1 in (0=transposed,1=no-transposed) form
0            U  in (0=transposed,1=no-transposed) form
0            Equilibration (0=no,1=yes)
8            memory alignment in double (> 0)
EOF

# Rank r -> physical core r/2 and its sibling (core c's sibling is CPU c+64).
cat > "$RUN/bind_rank.sh" <<'EOF'
#!/bin/bash
core=$(( MPI_LOCALRANKID / 2 ))
exec numactl --physcpubind="$core,$(( core + 64 ))" "$@"
EOF
chmod +x "$RUN/bind_rank.sh"

for v in $(compgen -e | grep '^I_MPI_PIN'); do unset "$v"; done
export I_MPI_PIN=0 I_MPI_DEBUG=4            # bind_rank.sh does the pinning
export MKL_NUM_THREADS=1 OMP_NUM_THREADS=1
export GLIBC_TUNABLES=glibc.malloc.hugetlb=1  # malloc uses transparent huge pages

exec 9>"$SCC/outputs/autotuning/.node.lock"
flock -n 9 || { echo "waiting for the node lock..."; flock 9; }
echo "netlib HPL N=93233, 128 ranks -> $RUN"
cd "$RUN" && mpirun -np 128 "$RUN/bind_rank.sh" "$XHPL" 2>&1 | tee hpl.log
grep -E "^W[RC]|PASSED|FAILED" "$RUN/hpl.log"
