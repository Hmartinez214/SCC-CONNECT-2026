#!/usr/bin/env bash
# A/B runtime system tweaks (SYSTEM_TUNING.md section 1) with a fixed Intel
# HPL configuration. Every arm starts from the state the system was in when
# the script started, applies one tweak, runs HPL once, and restores that
# state; the state is also restored on any exit (Ctrl-C included).
#
#   scripts/Launchpad/hpl/system_ab.sh [options] ARM [ARM ...]
#     -n N          problem size (default 150000, ~9 min per run)
#     -a ATTEMPT    take the HPL config from this attempt of the run group
#                   (default: best run of smac3_intel_hpl_v2)
#     -u KHZ        uncore max to use for the non-uncore arms (default: as found)
#     -o FILE       results file (default outputs/system_ab/results.tsv)
#
# Arms: found | uncore25 | uncore20 | uncore16 | uncore12 | numad_on |
#       numabal_on | numabal_off | kubepods_confined | largepage
# Runs take the tuners' node lock, so they never overlap a tuner's job.

set -uo pipefail
SCC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC" || exit 1
N=150000; ATTEMPT=""; UNCORE=""; OUT="$SCC/outputs/system_ab/results.tsv"
while getopts "n:a:u:o:" opt; do
    case $opt in n) N=$OPTARG ;; a) ATTEMPT=$OPTARG ;; u) UNCORE=$OPTARG ;; o) OUT=$OPTARG ;; *) exit 2 ;; esac
done
shift $((OPTIND - 1))
[[ $# -gt 0 ]] || { sed -n '2,16p' "$0"; exit 2; }
WORK="$(dirname "$OUT")/runs"; mkdir -p "$WORK"

UNC=(/sys/devices/system/cpu/intel_uncore_frequency/package_0*_die_00)
KUBE=kubepods.slice

# ---- state as found (restored after every run and on exit)
FOUND_UNCORE=(); for d in "${UNC[@]}"; do FOUND_UNCORE+=("$(cat "$d/max_freq_khz")"); done
FOUND_NUMABAL=$(cat /proc/sys/kernel/numa_balancing)
FOUND_NUMAD=$(systemctl is-active numad 2>/dev/null || true)
FOUND_KUBE=$(systemctl show $KUBE -p AllowedCPUs --value)
FOUND_HUGE=$(awk '/HugePages_Total/{print $2}' /proc/meminfo)
echo "# found: uncore_max=${FOUND_UNCORE[*]} numa_balancing=$FOUND_NUMABAL numad=$FOUND_NUMAD kubepods=$FOUND_KUBE hugepages=$FOUND_HUGE"

set_uncore() { for d in "${UNC[@]}"; do echo "$1" | sudo tee "$d/max_freq_khz" >/dev/null; done; }
restore() {
    local i=0; for d in "${UNC[@]}"; do echo "${FOUND_UNCORE[$i]}" | sudo tee "$d/max_freq_khz" >/dev/null; i=$((i + 1)); done
    sudo sysctl -qw kernel.numa_balancing="$FOUND_NUMABAL"
    if [[ $FOUND_NUMAD == active ]]; then sudo systemctl start numad; else sudo systemctl stop numad 2>/dev/null || true; fi
    # kubepods.slice is the kubelet's transient unit: put back the exact value
    # (never `systemctl revert` it -- that drops the kubelet's own limits).
    [[ "$(systemctl show $KUBE -p AllowedCPUs --value)" == "$FOUND_KUBE" ]] || sudo systemctl set-property --runtime $KUBE AllowedCPUs="$FOUND_KUBE"
    [[ "$(awk '/HugePages_Total/{print $2}' /proc/meminfo)" == "$FOUND_HUGE" ]] || sudo sysctl -qw vm.nr_hugepages="$FOUND_HUGE"
}
HPL_PID=""
cleanup() {
    if [[ -n $HPL_PID ]]; then
        for sig in TERM KILL; do
            m=$(ps -eo pid=,sid= | awk -v s="$HPL_PID" '$2==s{print $1}'); [[ -z $m ]] && break
            kill -"$sig" $m 2>/dev/null; sleep 5
        done
    fi
    sudo pkill -x turbostat 2>/dev/null || true
    restore
    echo "# restored: uncore_max=$(cat "${UNC[0]}/max_freq_khz") numa_balancing=$(cat /proc/sys/kernel/numa_balancing) numad=$(systemctl is-active numad) kubepods=$(systemctl show $KUBE -p AllowedCPUs --value) hugepages=$(awk '/HugePages_Total/{print $2}' /proc/meminfo)"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

# ---- environment: Intel MPI + Intel HPL (same as hpl_smac3.sh)
source libs/spack/share/spack/setup-env.sh >/dev/null
spack load intel-oneapi-mpi intel-oneapi-mkl || exit 1
INTEL_MPI="$(spack location -i intel-oneapi-mpi)"
export PATH="$INTEL_MPI/mpi/latest/bin:$PATH"
mpirun --version 2>/dev/null | grep -q "Intel(R) MPI" || { echo "mpirun is not Intel MPI" >&2; exit 1; }
INTEL_HPL_EXE="$(spack location -i intel-oneapi-mkl)/mkl/latest/share/mkl/benchmarks/mp_linpack/xhpl_intel64_dynamic"
export INTEL_HPL_EXE
unset OMP_NUM_THREADS MKL_NUM_THREADS
taskset -cp 0-"$(( $(nproc --all) - 1 ))" $$ >/dev/null

# ---- HPL.dat + launch lines for the chosen config, at fixed N
venv/bin/python - "$WORK" "$N" "${ATTEMPT}" <<'EOF' || exit 1
import json, sys
sys.path.insert(0, "scripts/Launchpad/hpl")
import hpl_autotune  # noqa: F401  (registers hpl_intel)
from hpc_autotuner.experiments import common
from hpc_autotuner.experiments.config import ExperimentConfig
work, n, attempt = sys.argv[1], int(sys.argv[2]), sys.argv[3]
import os, yaml
cfg = ExperimentConfig.from_yaml("scripts/Launchpad/hpl/hpl_smac3.yaml")
app = common.build_application(cfg)
# The harness controls the uncore cap itself (-u / uncore arms).
app.manage_uncore = False
log = f"outputs/autotuning/{cfg.run_group}/evaluations.jsonl"
if not os.path.exists(log):  # new run group not started yet: use its priors
    log = yaml.safe_load(open("scripts/Launchpad/hpl/hpl_smac3.yaml"))["experiment"]["priors"]
recs = {}
for line in open(log):
    r = json.loads(line)
    if r.get("status") == "COMPLETED" and r.get("success"):
        recs[r["attempt"]] = r
src = recs[int(attempt)] if attempt else max(recs.values(), key=lambda r: r["objective"])
conf = dict(src["configuration"]); conf["N"] = n
for lp in (0, 1):
    c = app.resolve_configuration({**conf, "HPL_LARGEPAGE": lp})
    cmd = app.command(c)
    open(f"{work}/HPL.dat", "w").write(cmd.split("<<'HPLDAT'\n")[1].split("\nHPLDAT")[0] + "\n")
    open(f"{work}/launch_lp{lp}.inc", "w").write("\n".join(app.launch_lines(c)) + "\n")
keys = ("N", "NB", "GRID", "MEM_POLICY", "PMAP", "PFACT", "RFACT", "BCAST", "DEPTH", "SWAP", "NBMIN", "NDIV")
print(f"# config from attempt {src['attempt']} ({src['objective']:.1f} GFLOPS at N={src['configuration']['N']}):",
      json.dumps({k: c[k] for k in keys}))
EOF

[[ -s $OUT ]] || printf "time\tarm\tN\tgflops\tsecs\tcheck\tcore_mhz_s0\tcore_mhz_s1\tunc_mhz_s0\tunc_mhz_s1\tpkg_w_s0\tpkg_w_s1\tram_w_s0\tram_w_s1\ttmax_c\n" > "$OUT"

apply_arm() {
    [[ -n $UNCORE ]] && set_uncore "$UNCORE"
    case $1 in
        found) ;;
        uncore25) set_uncore 2500000 ;; uncore20) set_uncore 2000000 ;;
        uncore16) set_uncore 1600000 ;; uncore12) set_uncore 1200000 ;;
        numad_on) sudo systemctl start numad ;;
        numabal_on) sudo sysctl -qw kernel.numa_balancing=1 ;;
        numabal_off) sudo sysctl -qw kernel.numa_balancing=0 ;;
        kubepods_confined) sudo systemctl set-property --runtime $KUBE AllowedCPUs=0,64 ;;
        largepage)
            local pages=$(( N * N * 8 / 2097152 * 105 / 100 + 64 ))
            sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null; echo 1 | sudo tee /proc/sys/vm/compact_memory >/dev/null
            sudo sysctl -qw vm.nr_hugepages=$pages
            echo "#   largepage: asked $pages huge pages, got $(awk '/HugePages_Total/{print $2}' /proc/meminfo)" ;;
        *) echo "unknown arm $1" >&2; return 1 ;;
    esac
}

for arm in "$@"; do
    lp=0; [[ $arm == largepage ]] && lp=1
    exec 9>"$SCC/outputs/autotuning/.node.lock"; flock 9          # wait for any tuner job
    apply_arm "$arm" || { flock -u 9; continue; }
    [[ $arm == kubepods_confined ]] && echo "#   kubepods effective cpuset: $(cat /sys/fs/cgroup/kubepods.slice/cpuset.cpus.effective)"
    RUN="$WORK/$(date -u +%Y%m%dT%H%M%S)_$arm"; mkdir -p "$RUN"; cp "$WORK/HPL.dat" "$RUN/"
    export RUNDIR="$RUN"
    # shellcheck disable=SC2024  # the log is ours; only turbostat needs root
    sudo turbostat --quiet --interval 10 --show Package,CPU,Bzy_MHz,UncMHz,PkgWatt,RAMWatt,PkgTmp > "$RUN/turbostat.txt" 2>&1 &
    setsid bash -c 'cd "$RUNDIR" && source "$1"' _ "$WORK/launch_lp$lp.inc" > "$RUN/hpl.log" 2>&1 &
    HPL_PID=$!; wait "$HPL_PID"; HPL_PID=""
    sudo pkill -x turbostat; sleep 1
    [[ $arm == kubepods_confined ]] && echo "#   kubepods after run: $(systemctl show $KUBE -p AllowedCPUs --value)"
    restore
    flock -u 9
    gf=$(awk '/^W[RC]/{print $7}' "$RUN/hpl.log"); secs=$(awk '/^W[RC]/{print $6}' "$RUN/hpl.log")
    chk=$(grep -oE 'PASSED|FAILED' "$RUN/hpl.log" | head -1); [[ -z $chk ]] && chk=NO_RESULT
    # Average over samples 3..last-1 (skip matrix setup / teardown); core MHz
    # over physical cores only (first HW thread per core: CPUs < 64).
    stats=$(awk 'NR==1{for(i=1;i<=NF;i++)h[$i]=i; next}
        $1=="-" {s++; next}
        $1=="Package" {next}
        ($1=="0"||$1=="1") && $2 ~ /^[0-9]+$/ {
            p=$1
            if ($2+0 < 64) { m[s,p]+=$h["Bzy_MHz"]; n[s,p]++ }
            if (NF >= h["PkgWatt"]) { w[s,p]=$h["PkgWatt"]; r[s,p]=$h["RAMWatt"]; u[s,p]=$h["UncMHz"]; if ($h["PkgTmp"]>t[s]) t[s]=$h["PkgTmp"] } }
        END { for(i=3;i<s;i++){ c++; for(p=0;p<2;p++){ M[p]+=m[i,p]/n[i,p]; W[p]+=w[i,p]; R[p]+=r[i,p]; U[p]+=u[i,p] } if(t[i]>mx)mx=t[i] }
              if (!c) { print "NA NA NA NA NA NA NA NA NA"; exit }
              printf "%.0f %.0f %.0f %.0f %.1f %.1f %.1f %.1f %d", M[0]/c, M[1]/c, U[0]/c, U[1]/c, W[0]/c, W[1]/c, R[0]/c, R[1]/c, mx }' "$RUN/turbostat.txt")
    line="$(date -u +%H:%M:%S)	$arm	$N	${gf:-NA}	${secs:-NA}	$chk	$(echo $stats | tr ' ' '\t')"
    echo "$line" >> "$OUT"; echo "RESULT $line"
done
