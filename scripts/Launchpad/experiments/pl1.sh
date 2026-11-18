#!/usr/bin/env bash
# Intel HPL (best configuration) at each package power limit PL1 given, with
# clocks, power and temperature from turbostat.
#
#   scripts/Launchpad/experiments/pl1.sh [N=90482] [LIMIT_C=90] WATTS...
#   bench exp pl1 250 275 300 250
#
# PL1 is written to MSR 0x610 on both packages (PL2 raised to PL1 if lower).
# A watchdog drops PL1 back to 250 W if either package reaches LIMIT_C. After
# every run, and on any exit, MSR 0x610 is restored to its original value.
# On this node HPL is held by the voltage regulator's current limit (VR_TDC),
# not PL1: 275 and 300 W changed nothing (2026-10-29).
# Results: outputs/experiments/pl1/results.tsv; turbostat and HPL logs per run.

set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/node.sh"
N=90482 LIMIT_C=90
WATTS=(); OPTS=(); for a in "$@"; do if [[ $a =~ ^[0-9]+$ ]]; then WATTS+=("$a"); else OPTS+=("$a"); fi; done
parse_args "N LIMIT_C" "${OPTS[@]}"
(( ${#OVERRIDES[@]} == 0 )) || { echo "unknown setting: ${OVERRIDES[*]} (only N=, LIMIT_C=)" >&2; exit 2; }
(( ${#WATTS[@]} )) || { usage >&2; exit 2; }
OUT="$OUTPUTS/experiments/pl1"; mkdir -p "$OUT"
TSV=$OUT/results.tsv
[[ -f $TSV ]] || printf "time\tpl1_w\tN\tgflops\tsecs\tvalid\tcore_mhz_s0\tcore_mhz_s1\tpkg_w_s0\tpkg_w_s1\tram_w\ttmax_c\twatchdog\n" > "$TSV"
HWMON=(); for h in /sys/class/hwmon/hwmon*; do [[ $(<"$h/name") == coretemp ]] && HWMON+=("$h"); done
FIRST=(); while IFS=, read -r cpu pkg; do [[ -n ${FIRST[pkg]:-} ]] || FIRST[pkg]=$cpu; done \
    < <(lscpu -p=CPU,SOCKET | grep -v '^#')                                            # one CPU per package

msr() {    # msr get | msr set <PL1 W> | msr raw <hex>
    sudo -n python3 - "${FIRST[*]}" "$@" <<'PY'
import os, struct, sys
cpus = [int(c) for c in sys.argv[1].split()]
def rd(c, r):
    fd = os.open(f"/dev/cpu/{c}/msr", os.O_RDONLY); v = struct.unpack("<Q", os.pread(fd, 8, r))[0]; os.close(fd); return v
def wr(c, r, v):
    fd = os.open(f"/dev/cpu/{c}/msr", os.O_WRONLY); os.pwrite(fd, struct.pack("<Q", v), r); os.close(fd)
unit = 1 / 2 ** (rd(cpus[0], 0x606) & 0xF)
for c in cpus:
    v = rd(c, 0x610)
    if sys.argv[2] == "set":
        pl1 = round(float(sys.argv[3]) / unit)
        pl2 = max((v >> 32) & 0x7FFF, pl1)
        wr(c, 0x610, (v & ~0x7FFF & ~(0x7FFF << 32)) | pl1 | (pl2 << 32))
    elif sys.argv[2] == "raw":
        wr(c, 0x610, int(sys.argv[3], 16))
    v = rd(c, 0x610)
    print(f"cpu{c}: {v:#x} PL1 {(v & 0x7FFF) * unit:.0f}W PL2 {((v >> 32) & 0x7FFF) * unit:.0f}W", end="  ")
print()
PY
}
ORIG=$(msr get | awk '{print $2; exit}') || exit 1
[[ $ORIG == 0x* ]] || { echo "cannot read MSR 0x610 (needs passwordless sudo and the msr module)" >&2; exit 1; }
WD=""
restore() { echo "restore: $(msr raw "$ORIG")"; }
trap '[[ -n $WD ]] && kill $WD 2>/dev/null; sudo -n pkill -x turbostat; restore' EXIT
trap 'exit 130' INT TERM HUP
tmax() { local m=0 t h; for h in "${HWMON[@]}"; do t=$(( $(<"$h/temp1_input") / 1000 )); (( t > m )) && m=$t; done; echo $m; }

for w in "${WATTS[@]}"; do
    stamp=$(date +%Y%m%d_%H%M%S)
    echo "== PL1 $w W, N=$N: $(msr set "$w")"
    : > "$OUT/watchdog.flag"
    ( while :; do t=$(tmax); if (( t >= LIMIT_C )); then msr set 250 >/dev/null; echo "tripped at ${t}C" > "$OUT/watchdog.flag"; fi; sleep 1; done ) &
    WD=$!
    sudo -n turbostat --quiet --interval 5 --show Package,CPU,Bzy_MHz,PkgWatt,RAMWatt,PkgTmp > "$OUT/turbo_${stamp}_$w.txt" 2>&1 &
    "$SCC/scripts/Launchpad/hpl/runbest.sh" N="$N" EXCLUDE_CPUS=none > "$OUT/hpl_${stamp}_$w.log" 2>&1
    sudo -n pkill -x turbostat; kill $WD; wait $WD 2>/dev/null; WD=""
    restore
    run=$(grep -oE 'run: [^)]+' "$OUT/hpl_${stamp}_$w.log" | tail -1 | cut -c6-)
    read -r g t valid < <(python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r["value"], r["seconds"], r["valid"])' \
                            "$SCC/$run/result.json" 2>/dev/null || echo "- - False")
    # turbostat samples 3..last-1; core MHz over physical CPUs; package W/temp from each package's first row
    stats=$(awk -v ncore="$(( $(nproc --all) / 2 ))" 'NR==1{for(i=1;i<=NF;i++)h[$i]=i; next}
        $1=="-" {s++; next}  $1=="Package" {next}
        ($1=="0"||$1=="1") && $2 ~ /^[0-9]+$/ { p=$1
            if ($2+0 < ncore) { m[s,p]+=$h["Bzy_MHz"]; n[s,p]++ }
            if (NF >= h["PkgWatt"]) { w[s,p]=$h["PkgWatt"]; r[s]+=$h["RAMWatt"]; if ($h["PkgTmp"]>t[s]) t[s]=$h["PkgTmp"] } }
        END { for(i=3;i<s;i++){ c++; for(p=0;p<2;p++){ M[p]+=m[i,p]/n[i,p]; W[p]+=w[i,p] } R+=r[i]; if(t[i]>mx)mx=t[i] }
              if (c) printf "%.0f\t%.0f\t%.1f\t%.1f\t%.1f\t%d", M[0]/c, M[1]/c, W[0]/c, W[1]/c, R/c, mx
              else printf "-\t-\t-\t-\t-\t-" }' "$OUT/turbo_${stamp}_$w.txt")
    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$stamp" "$w" "$N" "$g" "$t" "$valid" "$stats" "$(<"$OUT/watchdog.flag")" | tee -a "$TSV"
done
