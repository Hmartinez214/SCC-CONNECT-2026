#!/usr/bin/env bash
# SCC Connect 26 NCCL exercise on one LaunchPad node: the organizers' sweep on
# the NVLink pair, everything the report asks for in one directory.
#   scripts/launchpad_sweep.sh                 # GPUs 0,1, 3 repeats
#   PAIR=0,1 REPEATS=5 scripts/launchpad_sweep.sh
#   TUNED=../outputs/nccl_launchpad/tune_<stamp>/best.env scripts/launchpad_sweep.sh
#
# TUNED: lines "<collective> NCCL_X=v ...", applied to that collective's runs
# (from scripts/launchpad_tune.py); copied into the output dir.
#
# Per collective (all_reduce all_gather reduce_scatter broadcast), one process
# driving both GPUs (-g 2), NCCL defaults unless NCCL_* is exported:
#   <coll>_check.log      -b 8 -e 1G, -c 1 (correctness), NCCL_DEBUG=INFO
#                         INIT,GRAPH: the transport lines proving the path
#   <coll>_sweep_rN.log   -b 8 -e 8G -n 50 -w 10 -c 0   (the curve)
#   <coll>_lat_rN.log     -b 8 -e 16K -n 1000 -w 200 -c 0   (latency floor)
#   <coll>_bw_rN.log      -b 64M -e 8G -n 20 -w 5 -c 0   (peak busbw)
# plus topology / NVLink / version files. Holds the node lock shared with the
# autotuners. Then: scripts/launchpad_summary.py <dir>
set -euo pipefail
cd "$(dirname "$0")/.."
set +e; source ./env.sh >/dev/null; set -e

PAIR="${PAIR:-0,1}"
REPEATS="${REPEATS:-3}"
COLLS=(all_reduce all_gather reduce_scatter broadcast)
OUT="${OUT:-$(cd .. && pwd)/outputs/nccl_launchpad/sweep_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "$OUT"
declare -A TUNE=()
if [ -n "${TUNED:-}" ]; then
  cp "$TUNED" "$OUT/tuned.env"
  while read -r c settings; do
    [[ -z "$c" || "$c" == \#* ]] || TUNE[$c]="$settings"
  done < "$TUNED"
fi
for c in "${COLLS[@]}"; do
  [ -x "nccl-tests/build/${c}_perf" ] || { echo "no nccl-tests/build/${c}_perf - run scripts/setup.sh && scripts/build.sh"; exit 1; }
done

# --- what the hardware can do -------------------------------------------
nvidia-smi topo -m > "$OUT/topology.txt"
nvidia-smi -L > "$OUT/gpus_list.txt"
nvidia-smi nvlink --status > "$OUT/nvlink_status.txt"
nvidia-smi nvlink --capabilities > "$OUT/nvlink_capabilities.txt"
nvidia-smi --query-gpu=index,name,pci.bus_id,driver_version,clocks.max.sm,clocks.max.mem,power.limit,persistence_mode \
  --format=csv > "$OUT/gpus.csv"
{
  echo "date:        $(date -Is)"
  echo "host:        $(hostname)"
  echo "kernel:      $(uname -r)"
  echo "os:          $(. /etc/os-release && echo "$PRETTY_NAME")"
  echo "driver:      $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)"
  echo "cuda driver: $(nvidia-smi | sed -n 's/.*CUDA Version: *\([0-9.]*\).*/\1/p')"
  echo "cuda toolkit: $("$CUDA_HOME/bin/nvcc" --version | sed -n 's/.*release \([0-9.]*\).*/\1/p') ($CUDA_HOME)"
  echo "nccl:        $(awk '/define NCCL_MAJOR/{a=$3}/define NCCL_MINOR/{b=$3}/define NCCL_PATCH/{c=$3}END{print a"."b"."c}' "$NCCL_HOME/include/nccl.h") ($NCCL_HOME, git $(git -C nccl describe --tags 2>/dev/null || echo n/a))"
  echo "nccl-tests:  $(git -C nccl-tests describe --tags 2>/dev/null || git -C nccl-tests rev-parse --short HEAD)"
  echo "pair:        CUDA_VISIBLE_DEVICES=$PAIR"
  echo "NCCL_* set:  $(env | grep "^NCCL_" | grep -v "^NCCL_HOME=" | sort | xargs || true)"
  for c in "${COLLS[@]}"; do if [ -n "${TUNE[$c]:-}" ]; then echo "tuned $c: ${TUNE[$c]}"; fi; done
} > "$OUT/versions.txt"
cat "$OUT/versions.txt"

run() {  # run <log> <coll> <args...>
  local log=$1 coll=$2; shift 2
  echo "== $(basename "$log")"
  local -a tuned
  read -ra tuned <<< "${TUNE[$coll]:-}"
  { echo "# ${tuned[*]} CUDA_VISIBLE_DEVICES=$PAIR nccl-tests/build/${coll}_perf $*"
    env "${tuned[@]}" CUDA_VISIBLE_DEVICES="$PAIR" "nccl-tests/build/${coll}_perf" "$@"; } > "$log" 2>&1
  grep -E "Avg bus bandwidth|Out of bounds" "$log" | sed 's/^/   /'
}

exec 9>"$(cd .. && pwd)/outputs/autotuning/.node.lock"
flock 9

for c in "${COLLS[@]}"; do
  NCCL_DEBUG=INFO NCCL_DEBUG_SUBSYS=INIT,GRAPH \
    run "$OUT/${c}_check.log" "$c" -b 8 -e 1G -f 2 -g 2 -n 20 -w 5 -c 1
done
for r in $(seq 1 "$REPEATS"); do
  for c in "${COLLS[@]}"; do
    NCCL_DEBUG=WARN run "$OUT/${c}_sweep_r$r.log" "$c" -b 8 -e 8G -f 2 -g 2 -n 50 -w 10 -c 0
    NCCL_DEBUG=WARN run "$OUT/${c}_lat_r$r.log" "$c" -b 8 -e 16K -f 2 -g 2 -n 1000 -w 200 -c 0
    NCCL_DEBUG=WARN run "$OUT/${c}_bw_r$r.log" "$c" -b 64M -e 8G -f 2 -g 2 -n 20 -w 5 -c 0
  done
done
echo "results: $OUT"
