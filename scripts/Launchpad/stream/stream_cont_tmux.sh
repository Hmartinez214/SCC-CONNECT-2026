#!/usr/bin/env bash
# Start the endless STREAM best-vs-variation loop (stream_cont.sh) in a detached tmux session.
#
#   scripts/Launchpad/stream/stream_cont_tmux.sh   # start
#   tmux attach -t stream-cont                     # watch it
#   tmux kill-session -t stream-cont               # stop (settings of the running STREAM restored)

SESSION="${SESSION:-stream-cont}"
SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
LOG="$SCC_DIR/outputs/stream_cont/stream_cont_tmux.log"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "tmux session '$SESSION' is already running: tmux attach -t $SESSION"
    exit 0
fi

mkdir -p "$(dirname "$LOG")"
tmux new-session -d -s "$SESSION" -c "$SCC_DIR" \
    "bash scripts/Launchpad/stream/stream_cont.sh 2>&1 | tee -a '$LOG'"
echo "Started tmux session '$SESSION' (log: $LOG). Attach with: tmux attach -t $SESSION"
