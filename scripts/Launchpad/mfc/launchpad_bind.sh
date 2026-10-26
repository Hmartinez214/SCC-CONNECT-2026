#!/usr/bin/env bash
# Per-rank wrapper used by launchpad.mako (mpirun starts it once per rank).
# Pins the rank near its GPU according to MFC_BIND, then execs the MFC binary.
# MFC itself picks GPU = local rank mod number of GPUs; this uses the same rule.
#
#   none   no pinning (the OS places the rank)
#   numa   the GPU's whole NUMA node (CPUs and memory)
#   cores  an even share of the physical cores on the GPU's NUMA node (ranks
#          whose GPUs share a node split its cores), memory on that node
#
# On Launchpad both H100s hang off NUMA node 0 (CPUs 0-31,64-95).
set -euo pipefail

rank="${OMPI_COMM_WORLD_LOCAL_RANK:-0}"
nranks="${OMPI_COMM_WORLD_LOCAL_SIZE:-1}"
mode="${MFC_BIND:-none}"

[ "$mode" = none ] && exec "$@"

mapfile -t buses < <(nvidia-smi --query-gpu=pci.bus_id --format=csv,noheader)
ngpu="${#buses[@]}"
[ "$ngpu" -gt 0 ] || { echo "launchpad_bind: no GPUs found" >&2; exit 1; }

# NUMA node of GPU $1 (nvidia-smi prints 00000000:17:00.0; sysfs wants 0000:17:00.0).
gpu_node() {
    local bus="${buses[$1]}"
    bus="${bus: -12}"
    local node
    node="$(cat "/sys/bus/pci/devices/${bus,,}/numa_node" 2>/dev/null || echo 0)"
    [ "$node" -lt 0 ] && node=0
    echo "$node"
}

node="$(gpu_node $((rank % ngpu)))"

case "$mode" in
    numa)
        exec numactl --cpunodebind="$node" --membind="$node" "$@"
        ;;
    cores)
        peers=()
        for ((r = 0; r < nranks; r++)); do
            [ "$(gpu_node $((r % ngpu)))" = "$node" ] && peers+=("$r")
        done
        idx=0
        for i in "${!peers[@]}"; do
            [ "${peers[$i]}" = "$rank" ] && idx="$i"
        done
        # First hardware thread of each physical core on the node.
        mapfile -t cores < <(lscpu -p=CPU,CORE,NODE | grep -v '^#' \
                             | awk -F, -v n="$node" '$3 == n && !seen[$2]++ { print $1 }')
        per=$(( ${#cores[@]} / ${#peers[@]} ))
        [ "$per" -gt 0 ] || per=1
        slice="$(IFS=,; echo "${cores[*]:$((idx * per)):$per}")"
        exec numactl --physcpubind="$slice" --membind="$node" "$@"
        ;;
    *)
        echo "launchpad_bind: unknown MFC_BIND '$mode' (none|numa|cores)" >&2
        exit 1
        ;;
esac
