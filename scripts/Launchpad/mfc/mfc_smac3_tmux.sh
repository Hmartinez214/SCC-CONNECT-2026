#!/usr/bin/env bash
# Start (or resume) SMAC3 MFC autotuning in a detached tmux session.
#
#   scripts/Launchpad/mfc/mfc_smac3_tmux.sh                  # start / resume
#   DURATION=7200 scripts/Launchpad/mfc/mfc_smac3_tmux.sh    # stop after 2 h
#   tmux attach -t mfc-smac3                                 # watch it
#   tmux kill-session -t mfc-smac3                           # stop (resumable)

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/node.sh"
[[ ${1:-} == -h || ${1:-} == --help ]] && { usage; exit 0; }
exec "$SCC/scripts/Launchpad/lib/tmuxrun.sh" "${SESSION:-mfc-smac3}" "$OUTPUTS/mfc/autotuning/mfc_smac3_tmux.log" env DURATION="${DURATION:-}" bash scripts/Launchpad/mfc/mfc_smac3.sh "$@"
