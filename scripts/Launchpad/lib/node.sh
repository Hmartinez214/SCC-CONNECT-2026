# Shared helpers for the Launchpad shell scripts. Source it, don't run it:
#
#   source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/node.sh"
#
# Sets SCC (repo root), OUTPUTS and NODE_LOCK, and provides:
#
#   parse_args "OPTS" "$@"     -h/--help, --dry-run and KEY=VALUE arguments: keys
#                              named in OPTS become shell variables, any other
#                              KEY=VALUE goes to OVERRIDES (configuration keys)
#   usage                      print the calling script's header comment
#   node_lock [WHAT]           take the node-wide lock; while waiting, say who has it
#   lock_holder                who holds the node lock ("free" if nobody)
#   sys_set VALUE FILE...      write sysfs/procfs files with sudo, remembering the
#                              old values; sys_restore puts them back, newest first
#   run_dir BENCH NAME         RUN=outputs/BENCH/runbest/NAME_<time> (a temp dir
#                              with --dry-run)
#   dry_run_exit               with --dry-run: say so and exit 0 (before any change)
#   record_result METRIC VALUE VALID [KEY=VALUE...]
#                              write $RUN/result.json ($RESULT_JSON) and append a line
#                              to outputs/BENCH/runbest/results.tsv (RESULTS_TSV=none: skip)

SCC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUTPUTS="$SCC/outputs"
NODE_LOCK="$OUTPUTS/.node.lock"
DRY_RUN=0
OVERRIDES=()

usage() {    # the header comment of the script that was run, without the shebang
    awk 'NR == 1 && /^#!/ {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "${BASH_SOURCE[-1]}"
}

parse_args() {    # parse_args "OPT1 OPT2 ..." "$@"
    local opts=" $1 " arg key; shift
    for arg in "$@"; do
        case $arg in
            -h|--help) usage; exit 0 ;;
            --dry-run) DRY_RUN=1 ;;
            [A-Za-z_]*=*)
                key=${arg%%=*}
                if [[ $opts == *" $key "* ]]; then printf -v "$key" '%s' "${arg#*=}"
                else OVERRIDES+=("$arg"); fi ;;
            *) echo "unknown argument '$arg' (see -h)" >&2; exit 2 ;;
        esac
    done
}

lock_holder() {
    if flock -n "$NODE_LOCK" true 2>/dev/null; then echo free; return; fi
    local pid since what
    IFS=$'\t' read -r pid since what < "$NODE_LOCK.holder" 2>/dev/null
    if [[ -n ${pid:-} ]] && kill -0 "$pid" 2>/dev/null; then echo "$what (pid $pid, since $since)"
    else echo "unknown (a tuner or script that predates the holder file)"; fi
}

node_lock() {    # node_lock [what]: hold fd 9 on the node lock until the script exits
    mkdir -p "$OUTPUTS"
    exec 9>>"$NODE_LOCK"
    if ! flock -n 9; then
        echo "waiting for the node lock, held by: $(lock_holder)"
        flock 9
    fi
    printf '%s\t%s\t%s\n' "$$" "$(date +%FT%T)" "${1:-${BASH_SOURCE[-1]#"$SCC"/}}" > "$NODE_LOCK.holder"
}

SYS_FILES=(); SYS_OLD=()
sys_set() {    # sys_set value file...
    local value=$1 f old; shift
    (( $# )) || return 0
    for f in "$@"; do
        old=$(<"$f")
        [[ $old == *\[*\]* ]] && old=$(sed -E 's/.*\[([^]]+)\].*/\1/' <<<"$old")   # "always [madvise] never"
        SYS_FILES+=("$f"); SYS_OLD+=("$old")
    done
    echo "$value" | sudo -n tee "$@" >/dev/null
}
sys_restore() {    # newest first; consecutive files with the same old value share one sudo call
    local i batch=() value=
    for (( i = ${#SYS_FILES[@]} - 1; i >= 0; i-- )); do
        if (( ${#batch[@]} )) && [[ ${SYS_OLD[i]} != "$value" ]]; then
            echo "$value" | sudo -n tee "${batch[@]}" >/dev/null; batch=()
        fi
        value=${SYS_OLD[i]}; batch+=("${SYS_FILES[i]}")
    done
    (( ${#batch[@]} )) && echo "$value" | sudo -n tee "${batch[@]}" >/dev/null
    SYS_FILES=(); SYS_OLD=()
}

run_dir() {    # run_dir bench name
    RUN_BENCH=$1
    if (( DRY_RUN )); then RUN=$(mktemp -d "${TMPDIR:-/tmp}/$2.dry.XXXXXX")
    else RUN="$OUTPUTS/$1/runbest/$2_$(date +%Y%m%d_%H%M%S)"; mkdir -p "$RUN"; fi
}

dry_run_exit() {
    (( DRY_RUN )) || return 0
    echo "dry run: nothing was run and no setting was changed; rendered files are in $RUN"
    exit 0
}

record_result() {    # record_result metric value valid [key=value...]
    local tsv=${RESULTS_TSV:-$OUTPUTS/$RUN_BENCH/runbest/results.tsv}
    python3 - "$SCC" "$RUN/${RESULT_JSON:-result.json}" "$tsv" "${BASH_SOURCE[-1]#"$SCC"/}" "${BEST_SOURCE:-}" "${OVERRIDES[*]}" "$@" <<'EOF'
import json, os, sys, time
scc, out, tsv, script, source, overrides, metric, value, valid, *extra = sys.argv[1:]
try:
    value = float(value)
except ValueError:
    value = None
rec = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "run": os.path.relpath(os.path.dirname(out), scc), "script": script,
       "source": source, "overrides": overrides, "metric": metric, "value": value, "valid": valid == "yes",
       **dict(kv.split("=", 1) for kv in extra)}
json.dump(rec, open(out, "w"), indent=1)
if tsv != "none":
    cols = ["time", "run", "script", "source", "overrides", "metric", "value", "valid"]
    new = not os.path.exists(tsv)
    with open(tsv, "a") as f:
        if new:
            f.write("\t".join(cols) + "\n")
        f.write("\t".join("" if rec[c] is None else str(rec[c]) for c in cols) + "\n")
EOF
}
