#!/usr/bin/env bash
# Run a command from the repo root in a detached tmux session, appending its
# output to a log. Does nothing if the session already exists.
#
#   scripts/Launchpad/lib/tmuxrun.sh SESSION LOG COMMAND [ARG...]
#   tmux attach -t SESSION          # watch it
#   tmux kill-session -t SESSION    # stop it (the scripts restore what they changed)
#
# Usually reached through `scripts/Launchpad/bench start <loop>`.

source "$(dirname -- "${BASH_SOURCE[0]}")/node.sh"
[[ $# -ge 3 && $1 != -h && $1 != --help ]] || { usage; exit 2; }
SESSION=$1 LOG=$2; shift 2

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "tmux session '$SESSION' is already running: tmux attach -t $SESSION"
    exit 0
fi
mkdir -p "$(dirname -- "$LOG")"
tmux new-session -d -s "$SESSION" -c "$SCC" "$(printf '%q ' "$@") 2>&1 | tee -a $(printf '%q' "$LOG")"
echo "Started tmux session '$SESSION' (log: ${LOG#"$SCC"/}). Attach with: tmux attach -t $SESSION"
