#!/usr/bin/env bash
# NVIDIA HPL (NGC hpc-benchmarks 26.02.01) on both H100 NVLs with the best
# configuration found on Launchpad: 69.15 TFLOPS at N=136192 (34.6 per GPU), ~30 s.
# outputs/best/ngc_hpl.json. Larger N (144384) was no faster: both GPUs sit at 400 W.
#
#   scripts/Launchpad/hpl/runbest_ngc.sh
#
# One rank per GPU; both GPUs hang off socket 0, so rank 0 gets CPUs 0-15 and
# rank 1 CPUs 16-31, memory on NUMA node 0. GPUs via CDI (management spec:
# the default nvidia.yaml spec is stale, so --gpus all fails on this node);
# SYS_NICE lets numactl bind memory inside the container.
# Waits for the node lock. Log and HPL.dat: outputs/runbest/ngc_hpl_<time>/

set -uo pipefail
SCC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
IMAGE=nvcr.io/nvidia/hpc-benchmarks:26.02.01
RUN="$SCC/outputs/runbest/ngc_hpl_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN"

# The container's HPL-2GPUs.dat sample (N=136608; HPL-NVIDIA runs it as 136192).
cat > "$RUN/HPL.dat" <<'EOF'
HPLinpack benchmark input file
Innovative Computing Laboratory, University of Tennessee
HPL.out      output file name (if any)
6            device out (6=stdout,7=stderr,file)
1            # of problems sizes (N)
136608       Ns
1            # of NBs
1024         NBs
1            PMAP process mapping (0=Row-,1=Column-major)
1            # of process grids (P x Q)
2            Ps
1            Qs
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

exec 9>"$SCC/outputs/autotuning/.node.lock"
flock -n 9 || { echo "waiting for the node lock..."; flock 9; }
echo "NGC HPL, 2x H100 NVL -> $RUN"
docker run --rm --device management.nvidia.com/gpu=all --cap-add SYS_NICE --ipc=host \
    --ulimit memlock=-1 --ulimit stack=67108864 -v "$RUN/HPL.dat:/run/HPL.dat:ro" "$IMAGE" \
    mpirun --allow-run-as-root --bind-to none -np 2 \
    /workspace/hpl-linux-x86_64/hpl.sh --dat /run/HPL.dat --no-multinode \
    --gpu-affinity 0:1 --cpu-affinity 0-15:16-31 --mem-affinity 0:0 \
    2>&1 | tee "$RUN/hpl.log"
grep -E "^W[RC]|PASSED|FAILED" "$RUN/hpl.log"
