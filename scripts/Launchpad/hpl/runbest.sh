#!/usr/bin/env bash
# Intel HPL (MKL mp_linpack, CPU) with the best configuration found on
# Launchpad: the top entry of outputs/best/hpl_intel.json, at the N of its
# best large-N run (hpl_bign.sh runs that same configuration).
#
#   scripts/Launchpad/hpl/runbest.sh                 # large N (~1.5 h)
#   N=90482 scripts/Launchpad/hpl/runbest.sh         # any other N
#
# Ranks: at most one per NUMA node (HPL_HOST_NODE), each pinned to its nodes'
# cores (plus hyperthreads with HPL_HT=1). The uncore cap is set for the run
# and restored after. Waits for the node lock.
# Log and HPL.dat: outputs/runbest/hpl_intel_<time>/

set -uo pipefail
SCC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC" || exit 1

# ---- best configuration -> BEST_* variables ---------------------------------
# Every key of the top entry's configuration, plus values derived from it for
# this node: P, Q, RANKS, NUMA_PER_RANK, PIN_DOMAIN (one CPU mask per rank)
# and NUMTHREADS (HPL_NUMTHREADS resolved per rank; empty = Intel's default).
eval "$(python3 - "$SCC/outputs/best/hpl_intel.json" <<'EOF'
import json, shlex, subprocess, sys
best = json.load(open(sys.argv[1]))
top, large = best["top"][0], best["large_n"][0]
v = {**top["configuration"], "OBJECTIVE": top["objective"],
     "SOURCE": f"{top['run_group']} attempt {top['attempt']}",
     "N_LARGE": large["N"], "OBJECTIVE_LARGE": large["objective"]}
p, q = map(int, str(v["GRID"]).split("x"))
cpus = [tuple(map(int, l.split(","))) for l in subprocess.run(["lscpu", "-p=CPU,CORE,NODE"], capture_output=True,
        text=True).stdout.splitlines() if l[:1].isdigit()]
nodes = sorted({n for _, _, n in cpus})
if len(nodes) % (p * q):
    print(f"echo 'GRID {v['GRID']}: this script places at most one rank per NUMA node' >&2; exit 1"); sys.exit()
per = len(nodes) // (p * q)
first = {}
for cpu, core, _ in cpus:
    first.setdefault(core, cpu)                      # a core's first hardware thread
masks, owned = [], 0
for r in range(p * q):
    mine = [c for c, core, n in cpus if n in nodes[r * per:(r + 1) * per] and (int(v["HPL_HT"]) or first[core] == c)]
    masks.append(hex(sum(1 << c for c in mine)))
    owned = len(mine)
threads = {"all": "", "-1": owned - 1, "-2": owned - 2, "-4": owned - 4,
           "3/4": owned * 3 // 4, "1/2": owned // 2}[str(v["HPL_NUMTHREADS"])]
v.update(P=p, Q=q, RANKS=p * q, NUMA_PER_RANK=per, PIN_DOMAIN="[" + ",".join(masks) + "]", NUMTHREADS=threads)
for key, value in v.items():
    print(f"BEST_{key.upper()}={shlex.quote(str(value))}")
EOF
)"
[[ -n ${BEST_SOURCE:-} ]] || { echo "could not read outputs/best/hpl_intel.json" >&2; exit 1; }
N="${N:-$BEST_N_LARGE}"
echo "best: $BEST_SOURCE ($BEST_OBJECTIVE GFLOPS at N=$BEST_N; $BEST_OBJECTIVE_LARGE at N=$BEST_N_LARGE)"

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
$BEST_NB          NBs
$BEST_PMAP            PMAP process mapping (0=Row-,1=Column-major)
1            # of process grids (P x Q)
$BEST_P            Ps
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

# Placement and Intel HPL knobs.
for v in $(compgen -e | grep '^I_MPI_PIN'); do unset "$v"; done
unset OMP_NUM_THREADS MKL_NUM_THREADS HPL_NUMTHREADS GLIBC_TUNABLES
export I_MPI_PIN=1 I_MPI_PIN_DOMAIN="$BEST_PIN_DOMAIN" I_MPI_DEBUG=4
export HPL_LOG=$BEST_HPL_LOG HPL_LARGEPAGE=$BEST_HPL_LARGEPAGE HPL_HT=$BEST_HPL_HT HPL_PAIR=$BEST_HPL_PAIR \
       HPL_UMODE=$BEST_HPL_UMODE HPL_SWAPWIDTH=$BEST_HPL_SWAPWIDTH HPL_SWAPTHRESHOLD=$BEST_HPL_SWAPTHRESHOLD
[[ -n $BEST_NUMTHREADS ]] && export HPL_NUMTHREADS=$BEST_NUMTHREADS
# Rank r owns NUMA nodes r*NUMA_PER_RANK ... (r+1)*NUMA_PER_RANK-1.
cat > "$RUN/intel_rank.sh" <<EOF
#!/bin/bash
r=\${MPI_LOCALRANKID:-0}
export HPL_HOST_NODE=\$((r * $BEST_NUMA_PER_RANK))-\$(((r + 1) * $BEST_NUMA_PER_RANK - 1))
exec "\$@"
EOF
chmod +x "$RUN/intel_rank.sh"
NUMA=(); [[ $BEST_MEM_POLICY == interleave ]] && NUMA=(numactl --interleave=all)
[[ $BEST_HPL_LARGEPAGE == 1 ]] && echo "note: HPL_LARGEPAGE=1 needs hugetlbfs pages reserved (vm.nr_hugepages); this script does not reserve them" >&2

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
for d in "${UNC[@]}"; do
    echo $(( BEST_UNCORE_MAX_MHZ * 1000 )) | sudo -n tee "$d/max_freq_khz" >/dev/null || { echo "needs passwordless sudo" >&2; exit 1; }
done
echo "Intel HPL N=$N, grid ${BEST_P}x$BEST_Q, uncore max $BEST_UNCORE_MAX_MHZ MHz -> $RUN"
cd "$RUN" && mpirun -np "$BEST_RANKS" -perhost "$BEST_RANKS" "$RUN/intel_rank.sh" "${NUMA[@]}" "$XHPL" 2>&1 | tee hpl.log
grep -E "^W[RC]|PASSED|FAILED" "$RUN/hpl.log"
