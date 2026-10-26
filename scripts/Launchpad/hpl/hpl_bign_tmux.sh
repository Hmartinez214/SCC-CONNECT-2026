#!/usr/bin/env bash
# Start the endless large-N Intel HPL sweep (hpl_bign.sh) in a detached tmux session.
#
#   scripts/Launchpad/hpl/hpl_bign_tmux.sh       # start
#   tmux attach -t hpl-bign                      # watch it
#   tmux kill-session -t hpl-bign                # stop (the running HPL is killed, system restored)

SESSION="${SESSION:-hpl-bign}"
SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
LOG="$SCC_DIR/outputs/hpl_bign/hpl_bign_tmux.log"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "tmux session '$SESSION' is already running: tmux attach -t $SESSION"
    exit 0
fi

mkdir -p "$(dirname "$LOG")"
tmux new-session -d -s "$SESSION" -c "$SCC_DIR" \
    "bash scripts/Launchpad/hpl/hpl_bign.sh 2>&1 | tee -a '$LOG'"
echo "Started tmux session '$SESSION' (log: $LOG). Attach with: tmux attach -t $SESSION"
