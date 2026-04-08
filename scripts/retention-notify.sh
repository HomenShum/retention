#!/bin/bash
# Unified notification — sends to Slack (DM + #general)
# Usage: ./retention-notify.sh "action_type" "message"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ACTION="${1:-update}"
MESSAGE="${2:-No details}"
FULL_MSG="*[TA Agent]* $ACTION: $MESSAGE"

"$SCRIPT_DIR/notify-slack.sh" "$FULL_MSG"

echo "[retention-notify] Notified via Slack"
