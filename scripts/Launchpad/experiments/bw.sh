#!/usr/bin/env bash
# Memory-bandwidth ceilings (bw.c): read-only, write-only (streaming stores),
# copy and triad, under the best STREAM machine settings (THP always, C1E/C6
# off, uncore pinned at 2500 MHz), all restored after.
#
#   scripts/Launchpad/experiments/bw.sh [THREADS=64] [VARIANT...]
#   bench exp bw                     # base
#   bench exp bw base ht imc pf=0x1  # several
#
# Variants: base; ht (128 threads, both hyperthreads); imc (base under
# `perf stat` IMC CAS counters, to check bytes moved); pf=<hex> (prefetcher
# MSR 0x1a4 set to <hex> on every CPU for the run, then 0).
# Measured 2026-10-29: read 543, write ~395 GB/s of 665.6 nameplate; STREAM
# Triad (474-479) is 98-99% of the 2R:1W mix ceiling. No prefetcher setting helped.
# Takes the node lock. Results: outputs/experiments/bw/results.tsv.

set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/node.sh"
THREADS=64
VARIANTS=(); OPTS=(); for a in "$@"; do if [[ $a =~ ^(base|ht|imc|pf=0x[0-9a-fA-F]+)$ ]]; then VARIANTS+=("$a"); else OPTS+=("$a"); fi; done
parse_args "THREADS" "${OPTS[@]}"
(( ${#OVERRIDES[@]} == 0 )) || { echo "unknown setting: ${OVERRIDES[*]} (only THREADS=, variants base ht imc pf=0x..)" >&2; exit 2; }
(( ${#VARIANTS[@]} )) || VARIANTS=(base)
OUT="$OUTPUTS/experiments/bw"; mkdir -p "$OUT"
TSV=$OUT/results.tsv
[[ -f $TSV ]] || printf "time\tvariant\tthreads\tread_gb_s\twrite_gb_s\tcopy_gb_s\ttriad_gb_s\n" > "$TSV"

cd "$SCC" || exit 1
source libs/spack/share/spack/setup-env.sh >/dev/null
spack load intel-oneapi-compilers || exit 1
BIN="$OUT/bw"
[[ $BIN -nt scripts/Launchpad/experiments/bw.c ]] ||
    icx -O3 -qopenmp -xCORE-AVX512 -mcmodel=medium scripts/Launchpad/experiments/bw.c -o "$BIN" || exit 1

pf() {    # pf <hex>: prefetcher control MSR 0x1a4 on every CPU
    sudo -n python3 -c '
import glob, os, struct, sys
for p in glob.glob("/dev/cpu/*/msr"):
    fd = os.open(p, os.O_WRONLY); os.pwrite(fd, struct.pack("<Q", int(sys.argv[1], 16)), 0x1a4); os.close(fd)' "$1"
}
UNC=(/sys/devices/system/cpu/intel_uncore_frequency/package_*_die_*)
CST=(); for s in /sys/devices/system/cpu/cpu*/cpuidle/state*; do [[ $(<"$s/name") =~ ^(C1E|C6)$ ]] && CST+=("$s/disable"); done
PF_SET=0
restore() { (( PF_SET )) && pf 0; sys_restore; echo "restored: THP, uncore, C-states$( (( PF_SET )) && echo ", MSR 0x1a4 = 0")"; }
trap restore EXIT
trap 'exit 130' INT TERM HUP
node_lock "bw.sh"
sys_set always /sys/kernel/mm/transparent_hugepage/enabled || { echo "needs passwordless sudo" >&2; exit 1; }
sys_set 2500000 "${UNC[@]/%//max_freq_khz}"
sys_set 2500000 "${UNC[@]/%//min_freq_khz}"
sys_set 1 "${CST[@]}"
taskset -cp 0-"$(( $(nproc --all) - 1 ))" $$ >/dev/null
export OMP_DYNAMIC=FALSE KMP_AFFINITY=granularity=core,compact,1,0 OMP_NUM_THREADS=$THREADS

for v in "${VARIANTS[@]}"; do
    case $v in
        base) line=$("$BIN") ;;
        ht)   line=$(OMP_NUM_THREADS=128 KMP_AFFINITY=granularity=fine,compact,0,0 "$BIN") ;;
        imc)  line=$(sudo -n --preserve-env=OMP_DYNAMIC,KMP_AFFINITY,OMP_NUM_THREADS,LD_LIBRARY_PATH perf stat -a \
                     -e uncore_imc/cas_count_read/,uncore_imc/cas_count_write/ -o "$OUT/imc.txt" -- "$BIN")
              grep -E 'cas|elapsed' "$OUT/imc.txt" ;;
        pf=*) PF_SET=1; pf "${v#pf=}"; line=$("$BIN"); pf 0 ;;
    esac
    echo "$v: $line"
    read -r rd wr cp tr < <(awk '{print $2, $4, $6, $8}' <<<"$line")
    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$(date +%FT%T)" "$v" "$( [[ $v == ht ]] && echo 128 || echo "$THREADS")" "$rd" "$wr" "$cp" "$tr" >> "$TSV"
done
