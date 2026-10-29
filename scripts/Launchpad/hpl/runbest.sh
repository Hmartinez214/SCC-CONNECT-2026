#!/usr/bin/env bash
# Intel HPL (MKL mp_linpack, CPU) with the best configuration found on
# Launchpad: the top entry of outputs/best/hpl_intel.json, at the N of its
# best large-N run (hpl_bign.sh runs that same configuration).
#
#   scripts/Launchpad/hpl/runbest.sh                 # large N (~1.5 h)
#   N=90482 scripts/Launchpad/hpl/runbest.sh         # any other N
#   EXCLUDE_CPUS=0,64 scripts/Launchpad/hpl/runbest.sh  # leave these CPUs out (one thread per CPU left)
#   EXCLUDE_CPUS=none ...                               # same pinning path, nothing left out
#   HUGEPAGES=2m|1g scripts/Launchpad/hpl/runbest.sh     # HPL_LARGEPAGE=1 on reserved hugetlbfs pages:
#       2m: the default 2 MB pool; 1g: 1 GB pages for allocations >= 256 MB
#       via the hugetlb1g.c LD_PRELOAD shim. Pools are released after the run.
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
# NUMTHREADS (HPL_NUMTHREADS resolved per rank; empty = Intel's default) and,
# with EXCLUDE_CPUS, HOST_CORES (per rank, ';'-separated CPU ranges): Intel
# HPL pins its threads itself, ignoring the MPI mask, and HPL_HOST_NODE takes
# precedence over HPL_HOST_CORE, so those ranks get HPL_HOST_CORE only.
eval "$(python3 - "$SCC/outputs/best/hpl_intel.json" "${EXCLUDE_CPUS:-}" <<'EOF'
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
skip = {int(c) for c in sys.argv[2].split(",") if c.isdigit()}
def ranges(cs):
    out, cs = [], sorted(cs)
    for c in cs:
        if out and c == out[-1][1] + 1: out[-1][1] = c
        else: out.append([c, c])
    return ",".join(f"{a}-{b}" if a != b else f"{a}" for a, b in out)
masks, owned, host = [], 0, []
for r in range(p * q):
    mine = [c for c, core, n in cpus if n in nodes[r * per:(r + 1) * per] and (int(v["HPL_HT"]) or first[core] == c) and c not in skip]
    masks.append(hex(sum(1 << c for c in mine)))
    host.append(ranges(mine) if sys.argv[2] else "")
    owned = len(mine)
threads = {"all": "", "-1": owned - 1, "-2": owned - 2, "-4": owned - 4,
           "3/4": owned * 3 // 4, "1/2": owned // 2}[str(v["HPL_NUMTHREADS"])]
if sys.argv[2] and threads == "":
    threads = owned                                  # Intel's default would use every core of the node
v.update(P=p, Q=q, RANKS=p * q, NUMA_PER_RANK=per, PIN_DOMAIN="[" + ",".join(masks) + "]", NUMTHREADS=threads, HOST_CORES=";".join(host))
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
IFS=';' read -ra cores <<< "$BEST_HOST_CORES"
[[ -f "$RUN/hugetlb1g.so" ]] && export LD_PRELOAD="$RUN/hugetlb1g.so"
if [[ -n \${cores[r]:-} ]]; then export HPL_HOST_CORE=\${cores[r]}
else export HPL_HOST_NODE=\$((r * $BEST_NUMA_PER_RANK))-\$(((r + 1) * $BEST_NUMA_PER_RANK - 1)); fi
exec "\$@"
EOF
chmod +x "$RUN/intel_rank.sh"
NUMA=(); [[ $BEST_MEM_POLICY == interleave ]] && NUMA=(numactl --interleave=all)
[[ $BEST_HPL_LARGEPAGE == 1 ]] && echo "note: HPL_LARGEPAGE=1 needs hugetlbfs pages reserved (vm.nr_hugepages); this script does not reserve them" >&2

# Huge-page pools (HUGEPAGES=2m|1g): matrix + 5%, plus 8 GB of 2 MB pages
# for Intel HPL's smaller buffers (as the tuner reserves them).
HP2=/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
HP1G=/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages
HP2_FOUND=$(cat $HP2); HP1G_FOUND=$(cat $HP1G)
MATRIX_MB=$(( N * N * 8 / 2**20 * 105 / 100 ))
case ${HUGEPAGES:-} in
    "") ;;
    2m) HP2_WANT=$(( MATRIX_MB / 2 + 4096 )); HP1G_WANT=0 ;;
    1g) HP2_WANT=4096; HP1G_WANT=$(( MATRIX_MB / 1024 + 8 ))
        gcc -O2 -shared -fPIC "$SCC/scripts/Launchpad/hpl/hugetlb1g.c" -o "$RUN/hugetlb1g.so" -ldl || exit 1 ;;
    *) echo "HUGEPAGES=$HUGEPAGES: use 2m or 1g" >&2; exit 1 ;;
esac
[[ -n ${HUGEPAGES:-} ]] && export HPL_LARGEPAGE=1

# Uncore cap for the run; restored on any exit (as are the huge-page pools).
UNC=(/sys/devices/system/cpu/intel_uncore_frequency/package_*_die_*)
UNC_FOUND=(); for d in "${UNC[@]}"; do UNC_FOUND+=("$(cat "$d/max_freq_khz")"); done
restore() {
    local i=0
    for d in "${UNC[@]}"; do echo "${UNC_FOUND[$i]}" | sudo -n tee "$d/max_freq_khz" >/dev/null; i=$((i + 1)); done
    echo "uncore max restored to ${UNC_FOUND[*]} kHz"
    if [[ -n ${HUGEPAGES:-} ]]; then
        echo "$HP1G_FOUND" | sudo -n tee $HP1G >/dev/null; echo "$HP2_FOUND" | sudo -n tee $HP2 >/dev/null
        echo "huge pages released: 1G pool $(cat $HP1G), 2M pool $(cat $HP2)"
    fi
}
trap restore EXIT
trap 'exit 130' INT TERM HUP

exec 9>"$SCC/outputs/autotuning/.node.lock"
flock -n 9 || { echo "waiting for the node lock..."; flock 9; }
for d in "${UNC[@]}"; do
    echo $(( BEST_UNCORE_MAX_MHZ * 1000 )) | sudo -n tee "$d/max_freq_khz" >/dev/null || { echo "needs passwordless sudo" >&2; exit 1; }
done
if [[ -n ${HUGEPAGES:-} ]]; then
    # 1 GB pages need contiguous free memory: compact and retry if the pool comes up short.
    for try in 1 2 3; do
        echo "$HP1G_WANT" | sudo -n tee $HP1G >/dev/null; echo "$HP2_WANT" | sudo -n tee $HP2 >/dev/null
        (( $(cat $HP1G) >= HP1G_WANT )) && break
        echo "1G pool $(cat $HP1G)/$HP1G_WANT: compacting memory (try $try)"
        echo 1 | sudo -n tee /proc/sys/vm/compact_memory >/dev/null
    done
    (( $(cat $HP1G) >= HP1G_WANT && $(cat $HP2) >= HP2_WANT )) ||
        { echo "could not reserve huge pages: 1G $(cat $HP1G)/$HP1G_WANT, 2M $(cat $HP2)/$HP2_WANT" >&2; exit 1; }
    echo "huge pages ($HUGEPAGES): 1G pool $HP1G_WANT, 2M pool $HP2_WANT, HPL_LARGEPAGE=1"
fi
echo "Intel HPL N=$N, grid ${BEST_P}x$BEST_Q, pin $BEST_PIN_DOMAIN${BEST_HOST_CORES:+ (HPL_HOST_CORE $BEST_HOST_CORES)}, threads ${BEST_NUMTHREADS:-all}, uncore max $BEST_UNCORE_MAX_MHZ MHz -> $RUN"
cd "$RUN" && mpirun -np "$BEST_RANKS" -perhost "$BEST_RANKS" "$RUN/intel_rank.sh" "${NUMA[@]}" "$XHPL" 2>&1 | tee hpl.log
grep -E "^W[RC]|PASSED|FAILED" "$RUN/hpl.log"
