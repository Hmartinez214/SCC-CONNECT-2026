# Sourced by the other scripts. Defines a best-effort `notify` that posts a
# notification via ntfy (https://ntfy.sh).
#
# SILENT NO-OP unless both are set in your environment:
#     export NTFY_TOPIC=your-private-topic
#     export NTFY_TOKEN="$(cat ~/.ntfy-token)"
# Optional: export NTFY_HOST=https://your.server   (default: ntfy.sh)
#
#   notify "message" [title] [priority] [tags]
# Never changes the caller's exit status. Keep Title/Tags ASCII.

: "${NTFY_HOST:=https://ntfy.sh}"

notify() {
  [ -n "${NTFY_TOPIC:-}" ] && [ -n "${NTFY_TOKEN:-}" ] || return 0
  command -v curl >/dev/null 2>&1 || return 0
  curl -sS -m 10 -o /dev/null \
    -H "Authorization: Bearer $NTFY_TOKEN" \
    -H "Title: ${2:-nvccl @ $(hostname -s)}" \
    -H "Priority: ${3:-default}" \
    -H "Tags: ${4:-white_check_mark}" \
    --data-binary "${1:-done}" \
    "$NTFY_HOST/$NTFY_TOPIC" 2>/dev/null || true
  return 0
}

fmt_dur() { s=${1:-0}; printf '%dh %02dm %02ds' $((s/3600)) $(((s%3600)/60)) $((s%60)); }
