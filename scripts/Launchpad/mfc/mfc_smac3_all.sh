#!/usr/bin/env bash
# Tune every case in cases.yaml (or the ones given), one after another, each
# in its own run group <run_group>_<slug> with the space from $CONFIG
# (default mfc_smac3.yaml: SMAC; CONFIG=.../mfc_grid.yaml: all 24 combinations).
#
#   scripts/Launchpad/mfc/mfc_smac3_all.sh                  # all cases.yaml cases
#   scripts/Launchpad/mfc/mfc_smac3_all.sh igr ibm          # just these
#   scripts/Launchpad/mfc/mfc_smac3_all.sh --examples --shuffle 1 --prune-builds
#                                                           # all of MFC's examples/, random order
#   tmux new-session -d -s mfc-all -c "$PWD" \
#       "scripts/Launchpad/mfc/mfc_smac3_all.sh 2>&1 | tee -a outputs/autotuning/mfc_smac3_all.log"
#
#   --examples        add every directory of MFC's examples/ to the cases
#   --shuffle SEED    random order (same SEED = same order, so a restart resumes it)
#   --prune-builds    after each case, delete the MFC builds that case created
#                     (its case-optimized ones, ~1.3 GB each); generic builds stay
#
# BETWEEN, if set, is a shell command run after each case (e.g. a time-limited
# slot of another tuner, so it shares the node without overlapping runs):
#   BETWEEN="timeout --signal=INT 1800 venv/bin/python scripts/Launchpad/mfc/mfc_autotune.py \
#            --config scripts/Launchpad/mfc/mfc_igr_extra.yaml" mfc_smac3_all.sh --examples ...
#
# Before tuning a case it times one plain run (outputs/mfc_runs/cases/<slug>,
# limited to PLAIN_TIMEOUT seconds, default 600), which seeds SMAC and skips a
# case that fails or is too slow to tune. Re-running resumes: finished run
# groups end straight away. Ctrl-C stops the whole loop.

SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$SCC_DIR" || exit 1
MFC_SCRIPTS=scripts/Launchpad/mfc
MFC_BUILD=libs/clone/MFC/build
CONFIG="${CONFIG:-$MFC_SCRIPTS/mfc_smac3.yaml}"
PLAIN_TIMEOUT="${PLAIN_TIMEOUT:-600}"
BETWEEN="${BETWEEN:-}"
PY="$SCC_DIR/venv/bin/python"

trap 'echo "stopped"; exit 130' INT TERM HUP

EXAMPLES=0
SHUFFLE=""
PRUNE=0
CASES=()
while [ $# -gt 0 ]; do
    case "$1" in
        --examples)     EXAMPLES=1 ;;
        --shuffle)      shift; SHUFFLE="${1:?--shuffle needs a seed}" ;;
        --prune-builds) PRUNE=1 ;;
        -*)             echo "unknown option $1" >&2; exit 2 ;;
        *)              CASES+=("$1") ;;
    esac
    shift
done

# GBPP / STEPS the tuner uses, so the plain run matches its problem, and the
# run-group prefix (each case's group is <prefix>_<slug>).
read -r GBPP STEPS RUN_BASE < <(python3 -c "
import yaml, sys
y = yaml.safe_load(open(sys.argv[1]))
f = y['application'].get('fixed') or {}
print(f.get('GBPP', 16), f.get('STEPS', 50), (y.get('experiment') or {}).get('run_group', 'mfc_smac3'))" "$CONFIG")

if [ "$EXAMPLES" -eq 1 ]; then
    mapfile -t -O "${#CASES[@]}" CASES < <(python3 "$MFC_SCRIPTS/mfclib.py" examples)
elif [ "${#CASES[@]}" -eq 0 ]; then
    mapfile -t CASES < <(python3 -c "
import yaml; print('\n'.join(e['slug'] for e in yaml.safe_load(open('$MFC_SCRIPTS/cases.yaml'))))")
fi
if [ -n "$SHUFFLE" ]; then
    mapfile -t CASES < <(python3 -c "
import random, sys
cases = list(dict.fromkeys(sys.argv[2:]))
random.Random(int(sys.argv[1])).shuffle(cases)
print('\n'.join(cases))" "$SHUFFLE" "${CASES[@]}")
fi
echo "${#CASES[@]} cases: ${CASES[*]}"

# Build directories (staging and install share names) that exist now.
builds() { ls "$MFC_BUILD/staging" 2>/dev/null | sort; }

declare -A STATE
n=0
for c in "${CASES[@]}"; do
    n=$((n + 1))
    echo "===== $c [$n/${#CASES[@]}] ($(date)) ====="
    before="$(builds)"
    base="outputs/mfc_runs/cases/$c"
    if [ ! -f "$base/result.json" ]; then
        rm -rf "$base"
        timeout -k 30 "$PLAIN_TIMEOUT" "$MFC_SCRIPTS/runmfc.sh" --out outputs/mfc_runs/cases --label "$c" \
            --gbpp "$GBPP" --steps "$STEPS" "$c" | tail -n 3
        if [ ! -f "$base/result.json" ]; then
            mkdir -p "$base"
            echo '{"status": "timeout_or_error"}' > "$base/result.json"
        fi
    fi
    if ! grep -q '"status": "ok"' "$base/result.json" 2>/dev/null; then
        echo "plain run of $c failed or took over ${PLAIN_TIMEOUT}s (see $base/); not tuning it"
        STATE[$c]="plain run failed"
    else
        "$PY" "$MFC_SCRIPTS/mfc_autotune.py" --config "$CONFIG" --case "$c" 2>&1 \
            | grep --line-buffered -v '^\[INFO\]'
        # Exit 1 with ConfigurationSpaceExhaustedException is the normal end.
        STATE[$c]="tuner exit ${PIPESTATUS[0]}, $(python3 -c "
import json, sys
recs = {}
for l in open(sys.argv[1]):
    r = json.loads(l)
    if r.get('status') != 'SUBMITTED':
        recs[r['attempt']] = r
ok = [r for r in recs.values() if r.get('success')]
best = min(ok, key=lambda r: r['metrics']['grind_ns']) if ok else None
print(f'{len(recs)} runs, {len(recs) - len(ok)} failed' + (f', best grind {best[\"metrics\"][\"grind_ns\"]:.4f} ns' if best else ''))
" "outputs/autotuning/${RUN_BASE}_$c/evaluations.jsonl" 2>/dev/null || echo 'no runs')"
    fi
    echo "$c: ${STATE[$c]}"
    if [ "$PRUNE" -eq 1 ]; then
        for b in $(comm -13 <(echo "$before") <(builds)); do
            rm -rf "${MFC_BUILD:?}/staging/$b" "${MFC_BUILD:?}/install/$b"
            echo "pruned build $b"
        done
    fi
    if [ -n "$BETWEEN" ]; then
        echo "----- between cases: $BETWEEN ($(date)) -----"
        bash -c "$BETWEEN" 2>&1 | grep --line-buffered -v '^\[INFO\]'
        echo "----- between cases: exit ${PIPESTATUS[0]} -----"
    fi
done

echo "===== summary ====="
for c in "${CASES[@]}"; do echo "$c: ${STATE[$c]}"; done
