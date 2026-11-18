#!/usr/bin/env bash
# Start (or resume) SMAC3 HPL autotuning in a detached tmux session.
#
#   scripts/Launchpad/hpl/hpl_smac3_tmux.sh      # start / resume
#   tmux attach -t hpl-smac3                     # watch it
#   tmux kill-session -t hpl-smac3               # stop (resumable)

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/node.sh"
[[ ${1:-} == -h || ${1:-} == --help ]] && { usage; exit 0; }
exec "$SCC/scripts/Launchpad/lib/tmuxrun.sh" "${SESSION:-hpl-smac3}" "$OUTPUTS/hpl/autotuning/hpl_smac3_tmux.log" bash scripts/Launchpad/hpl/hpl_smac3.sh "$@"
