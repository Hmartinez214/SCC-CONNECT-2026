#!/usr/bin/env bash
# Start (or resume) SMAC3 MFC autotuning in a detached tmux session.
#
#   scripts/Launchpad/mfc/mfc_smac3_tmux.sh                  # start / resume
#   DURATION=7200 scripts/Launchpad/mfc/mfc_smac3_tmux.sh    # stop after 2 h
#   tmux attach -t mfc-smac3                                 # watch it
#   tmux kill-session -t mfc-smac3                           # stop (resumable)

SESSION="${SESSION:-mfc-smac3}"
SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
LOG="$SCC_DIR/outputs/autotuning/mfc_smac3_tmux.log"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "tmux session '$SESSION' is already running: tmux attach -t $SESSION"
    exit 0
fi

mkdir -p "$(dirname "$LOG")"
tmux new-session -d -s "$SESSION" -c "$SCC_DIR" \
    "DURATION='${DURATION:-}' bash scripts/Launchpad/mfc/mfc_smac3.sh $* 2>&1 | tee -a '$LOG'"
echo "Started tmux session '$SESSION' (log: $LOG). Attach with: tmux attach -t $SESSION"
