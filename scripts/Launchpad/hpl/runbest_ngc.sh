#!/usr/bin/env bash
# NVIDIA HPL (NGC hpc-benchmarks 26.02.01) on both H100 NVLs with the best
# configuration found on Launchpad: the top entry of outputs/best/ngc_hpl.json
# (N, NB, P, Q; the other HPL.dat lines are the container's 2-GPU sample).
#
#   scripts/Launchpad/hpl/runbest_ngc.sh
#   scripts/Launchpad/hpl/runbest_ngc.sh N=120000 NB=1024   # override configuration keys
#   scripts/Launchpad/hpl/runbest_ngc.sh --dry-run          # show HPL.dat, run nothing
#
# One rank per GPU; both GPUs hang off socket 0, so rank 0 gets CPUs 0-15 and
# rank 1 CPUs 16-31, memory on NUMA node 0. GPUs via CDI (management spec:
# the default nvidia.yaml spec is stale, so --gpus all fails on this node);
# SYS_NICE lets numactl bind memory inside the container.
# Waits for the node lock. Run directory: outputs/ngc/runbest/ngc_hpl_<time>/
# (HPL.dat, hpl.log, result.json); one line per run in outputs/ngc/runbest/results.tsv.

set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/node.sh"
parse_args "" "$@"
IMAGE=nvcr.io/nvidia/hpc-benchmarks:26.02.01

# ---- best configuration -> BEST_* variables (every key of the top entry) ----
eval "$(python3 "$SCC/scripts/Launchpad/lib/best.py" "$SCC/outputs/best/ngc_hpl.json" "${OVERRIDES[@]}")"
[[ -n ${BEST_SOURCE:-} ]] || exit 1
(( BEST_P * BEST_Q == 2 )) || { echo "P x Q = ${BEST_P}x$BEST_Q: this script runs one rank on each of the 2 GPUs" >&2; exit 1; }
echo "best: $BEST_SOURCE ($BEST_OBJECTIVE GFLOPS)"

run_dir ngc ngc_hpl
cat > "$RUN/HPL.dat" <<EOF
HPLinpack benchmark input file
Innovative Computing Laboratory, University of Tennessee
HPL.out      output file name (if any)
6            device out (6=stdout,7=stderr,file)
1            # of problems sizes (N)
$BEST_N       Ns
1            # of NBs
$BEST_NB         NBs
1            PMAP process mapping (0=Row-,1=Column-major)
1            # of process grids (P x Q)
$BEST_P            Ps
$BEST_Q            Qs
16.0         threshold
1            # of panel fact
0 1 2        PFACTs (0=left, 1=Crout, 2=Right)
1            # of recursive stopping criterium
2 8          NBMINs (>= 1)
1            # of panels in recursion
2            NDIVs
1            # of recursive panel fact.
0 1 2        RFACTs (0=left, 1=Crout, 2=Right)
1            # of broadcast
3 2          BCASTs (0=1rg,1=1rM,2=2rg,3=2rM,4=Lng,5=LnM)
1            # of lookahead depth
1 0          DEPTHs (>=0)
1            SWAP (0=bin-exch,1=long,2=mix)
192          swapping threshold
1            L1 in (0=transposed,1=no-transposed) form
0            U  in (0=transposed,1=no-transposed) form
0            Equilibration (0=no,1=yes)
8            memory alignment in double (> 0)
EOF

echo "NGC HPL N=$BEST_N NB=$BEST_NB, 2x H100 NVL -> ${RUN#"$SCC"/}"
dry_run_exit
node_lock
docker run --rm --device management.nvidia.com/gpu=all --cap-add SYS_NICE --ipc=host \
    --ulimit memlock=-1 --ulimit stack=67108864 -v "$RUN/HPL.dat:/run/HPL.dat:ro" "$IMAGE" \
    mpirun --allow-run-as-root --bind-to none -np 2 \
    /workspace/hpl-linux-x86_64/hpl.sh --dat /run/HPL.dat --no-multinode \
    --gpu-affinity 0:1 --cpu-affinity 0-15:16-31 --mem-affinity 0:0 \
    2>&1 | tee "$RUN/hpl.log"
grep -E "^W[RC]|PASSED|FAILED" "$RUN/hpl.log"

read -r secs gflops < <(awk '/^W[RC]/ {printf "%s %.2f\n", $6, $7; exit}' "$RUN/hpl.log")
valid=no; grep -q PASSED "$RUN/hpl.log" && valid=yes
record_result gflops "${gflops:-}" "$valid" "N=$BEST_N" "seconds=${secs:-}"
echo "result: ${gflops:-?} GFLOPS, residual check: $valid  (run: ${RUN#"$SCC"/})"
