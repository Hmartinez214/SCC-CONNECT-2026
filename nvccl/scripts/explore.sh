#!/usr/bin/env bash
# Dump what NCCL decides on THIS machine: full debug log + topology/graph XML.
# Fast (~10s); no benchmark numbers - the point is the log.
set -euo pipefail
cd "$(dirname "$0")/.."
set +e; source ./env.sh; set -e   # probe script, must not abort us

OUT="explore"; mkdir -p "$OUT"
BIN=nccl-tests/build/all_reduce_perf
[ -x "$BIN" ] || { echo "build first: scripts/setup.sh && scripts/build.sh"; exit 1; }

echo "==> hardware / topology  (-> $OUT/hardware.txt)"
{ nvidia-smi
  echo; echo "### nvidia-smi topo -m"; nvidia-smi topo -m
  echo; echo "### PCIe / NVLink link info"
  nvidia-smi -q | grep -iE "Link Width|Link Speed|GPU Link Info" -A2 || true
} > "$OUT/hardware.txt" 2>&1
sed -n '1,15p' "$OUT/hardware.txt"

GG="${NGPUS:-1}"; [ "$GG" -lt 1 ] && GG=1
echo
echo "==> ${GG}-rank NCCL job with debug logging + topology/graph dump"
NCCL_DEBUG=INFO \
NCCL_DEBUG_SUBSYS=INIT,ENV,GRAPH,TUNING,NET \
NCCL_TOPO_DUMP_FILE="$OUT/topo.xml" \
NCCL_GRAPH_DUMP_FILE="$OUT/graph.xml" \
  "$BIN" -b 8 -e 8 -f 2 -g "$GG" -n 1 -w 0 > "$OUT/nccl_debug.txt" 2>&1 || true
echo "   wrote $OUT/{nccl_debug.txt,topo.xml,graph.xml}"

cat <<'GUIDE'

==> how to read explore/nccl_debug.txt
    grep 'NCCL INFO' explore/nccl_debug.txt | less

  pattern                          tells you
  -------------------------------  ------------------------------------------
  NCCL version 2.x                 which NCCL actually loaded
  NET/IB : ... | NET/Socket : ...  network transport available
  Setting affinity for GPU         CPU cores NCCL pinned (NUMA locality)
  Ring 00 : 0 -> 1 -> 0            the ring(s) across the GPUs
  Trees [0] ...                    the tree(s) for latency-bound sizes
  P2P Chunksize / Buffer size      transfer chunking chosen
  via P2P/CUMEM / via NVLINK       whether the GPU-GPU path is NVLink (good)
  algo Ring proto LL128 / tuner    algo + protocol the cost model picked
                                   per size range  <-- what you tune

  topo.xml   feed back with NCCL_TOPO_FILE= ; graph.xml with NCCL_GRAPH_FILE=
GUIDE
