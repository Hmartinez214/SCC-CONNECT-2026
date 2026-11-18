#!/usr/bin/env bash
# Start the endless large-N Intel HPL sweep (hpl_bign.sh) in a detached tmux session.
#
#   scripts/Launchpad/hpl/hpl_bign_tmux.sh       # start
#   tmux attach -t hpl-bign                      # watch it
#   tmux kill-session -t hpl-bign                # stop (the running HPL is killed, system restored)

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/node.sh"
[[ ${1:-} == -h || ${1:-} == --help ]] && { usage; exit 0; }
exec "$SCC/scripts/Launchpad/lib/tmuxrun.sh" "${SESSION:-hpl-bign}" "$OUTPUTS/hpl/bign/hpl_bign_tmux.log" bash scripts/Launchpad/hpl/hpl_bign.sh
