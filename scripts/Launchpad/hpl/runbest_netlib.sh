#!/usr/bin/env bash
# netlib HPL (spack hpl/zqzkvyu: gcc + MKL + Intel MPI, CPU) with the best
# configuration found on Launchpad: the top entry of outputs/best/hpl_netlib.json.
#
#   scripts/Launchpad/hpl/runbest_netlib.sh
#
# Single-threaded ranks bound to physical cores in order (block mapping);
# with more ranks than cores, consecutive ranks share a core and its
# hyperthread sibling. HUGEPAGES=thp gives malloc transparent huge pages.
# Waits for the node lock. Log and HPL.dat: outputs/runbest/hpl_netlib_<time>/

set -uo pipefail
SCC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC" || exit 1

# ---- best configuration -> BEST_* variables (every key of the top entry) ----
eval "$(python3 - "$SCC/outputs/best/hpl_netlib.json" <<'EOF'
import json, shlex, sys
top = json.load(open(sys.argv[1]))["top"][0]
v = {**top["configuration"], "OBJECTIVE": top["objective"], "SOURCE": f"{top['run_group']} attempt {top['attempt']}"}
for key, value in v.items():
    print(f"BEST_{key.upper()}={shlex.quote(str(value))}")
EOF
)"
[[ -n ${BEST_SOURCE:-} ]] || { echo "could not read outputs/best/hpl_netlib.json" >&2; exit 1; }
[[ $BEST_MPI_BIND == core && $BEST_MPI_MAP == block ]] || {
    echo "MPI_BIND=$BEST_MPI_BIND MPI_MAP=$BEST_MPI_MAP: this script implements core binding with block mapping" >&2; exit 1; }
echo "best: $BEST_SOURCE ($BEST_OBJECTIVE GFLOPS)"

RUN="$SCC/outputs/runbest/hpl_netlib_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN"

source libs/spack/share/spack/setup-env.sh >/dev/null
spack load hpl/zqzkvyu intel-oneapi-mpi || exit 1
PATH="$(spack location -i intel-oneapi-mpi)/mpi/latest/bin:$PATH"
mpirun --version 2>/dev/null | grep -q "Intel(R) MPI" || { echo "mpirun is not Intel MPI" >&2; exit 1; }
XHPL="$(spack location -i hpl/zqzkvyu)/bin/xhpl"
taskset -cp 0-"$(( $(nproc --all) - 1 ))" $$ >/dev/null    # undo any inherited CPU restriction (e.g. tmux)

cat > "$RUN/HPL.dat" <<EOF
HPLinpack benchmark input file
Innovative Computing Laboratory, University of Tennessee
HPL.out      output file name (if any)
6            device out (6=stdout,7=stderr,file)
1            # of problems sizes (N)
$BEST_N        Ns
1            # of NBs
$BEST_NB          NBs
$BEST_PMAP            PMAP process mapping (0=Row-,1=Column-major)
1            # of process grids (P x Q)
$BEST_P           Ps
$BEST_Q            Qs
16.0         threshold
1            # of panel fact
$BEST_PFACT            PFACTs (0=left, 1=Crout, 2=Right)
1            # of recursive stopping criterium
$BEST_NBMIN            NBMINs (>= 1)
1            # of panels in recursion
$BEST_NDIV            NDIVs
1            # of recursive panel fact.
$BEST_RFACT            RFACTs (0=left, 1=Crout, 2=Right)
1            # of broadcast
$BEST_BCAST            BCASTs (0=1rg,1=1rM,2=2rg,3=2rM,4=Lng,5=LnM)
1            # of lookahead depth
$BEST_DEPTH            DEPTHs (>=0)
$BEST_SWAP            SWAP (0=bin-exch,1=long,2=mix)
$BEST_SWAP_THRESH           swapping threshold
$BEST_L1            L1 in (0=transposed,1=no-transposed) form
$BEST_U            U  in (0=transposed,1=no-transposed) form
$BEST_EQUIL            Equilibration (0=no,1=yes)
$BEST_ALIGN            memory alignment in double (> 0)
EOF

# Rank r -> physical core r / (ranks per core), bound with its hyperthread
# sibling (core c's sibling is CPU c + cores).
CORES=$(( $(nproc --all) / 2 ))
PER_CORE=$(( (BEST_RANKS + CORES - 1) / CORES ))
cat > "$RUN/bind_rank.sh" <<EOF
#!/bin/bash
core=\$(( MPI_LOCALRANKID / $PER_CORE ))
exec numactl --physcpubind="\$core,\$(( core + $CORES ))" "\$@"
EOF
chmod +x "$RUN/bind_rank.sh"

for v in $(compgen -e | grep '^I_MPI_PIN'); do unset "$v"; done
export I_MPI_PIN=0 I_MPI_DEBUG=4            # bind_rank.sh does the pinning
export MKL_NUM_THREADS=1 OMP_NUM_THREADS=1
unset GLIBC_TUNABLES
[[ $BEST_HUGEPAGES == thp ]] && export GLIBC_TUNABLES=glibc.malloc.hugetlb=1
NUMA=(); [[ $BEST_MEM_POLICY == interleave ]] && NUMA=(numactl --interleave=all)

exec 9>"$SCC/outputs/autotuning/.node.lock"
flock -n 9 || { echo "waiting for the node lock..."; flock 9; }
echo "netlib HPL N=$BEST_N, ${BEST_RANKS} ranks (${BEST_P}x$BEST_Q) -> $RUN"
cd "$RUN" && mpirun -np "$BEST_RANKS" "$RUN/bind_rank.sh" "${NUMA[@]}" "$XHPL" 2>&1 | tee hpl.log
grep -E "^W[RC]|PASSED|FAILED" "$RUN/hpl.log"
