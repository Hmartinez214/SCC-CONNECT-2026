#!/usr/bin/env bash
# Start the endless STREAM best-vs-variation loop (stream_cont.sh) in a detached tmux session.
#
#   scripts/Launchpad/stream/stream_cont_tmux.sh   # start
#   tmux attach -t stream-cont                     # watch it
#   tmux kill-session -t stream-cont               # stop (settings of the running STREAM restored)

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/node.sh"
[[ ${1:-} == -h || ${1:-} == --help ]] && { usage; exit 0; }
exec "$SCC/scripts/Launchpad/lib/tmuxrun.sh" "${SESSION:-stream-cont}" "$OUTPUTS/stream/cont/stream_cont_tmux.log" bash scripts/Launchpad/stream/stream_cont.sh
