#!/bin/bash
# Send Slack notification via OpenClaw bot token
# Sends to DM (homen) + #claw-communications (NOT #general)
# Usage: ./notify-slack.sh "Your message here"

BOT_TOKEN="${SLACK_BOT_TOKEN:-}"
if [ -z "$BOT_TOKEN" ]; then
  echo "ERROR: SLACK_BOT_TOKEN is not set. Export it before running this script." >&2
  exit 1
fi
DM_CHANNEL="D0ALLBZ2ZKM"          # homen's DM
CLAW_CHANNEL="${CLAW_CHANNEL:-}"   # #claw-communications (auto-discovered)
MESSAGE="${1:-[TA Agent] No message provided}"

# Auto-discover #claw-communications if not set
if [ -z "$CLAW_CHANNEL" ]; then
  CLAW_CHANNEL=$(curl -s "https://slack.com/api/conversations.list" \
    -H "Authorization: Bearer $BOT_TOKEN" \
    -d "types=public_channel" \
    -d "limit=200" 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
for ch in data.get('channels', []):
    if 'claw' in ch.get('name', '').lower():
        print(ch['id'])
        break
" 2>/dev/null || echo "")
fi

# Escape for JSON safely
ESCAPED_MSG=$(echo "$MESSAGE" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))" 2>/dev/null)
# Fallback if python fails
if [ -z "$ESCAPED_MSG" ]; then
  ESCAPED_MSG=$(echo "$MESSAGE" | sed 's/\\/\\\\/g; s/"/\\"/g')
  ESCAPED_MSG="\"$ESCAPED_MSG\""
fi

# Send DM to homen
curl -s -X POST https://slack.com/api/chat.postMessage \
  -H "Authorization: Bearer $BOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"channel\":\"$DM_CHANNEL\",\"text\":$ESCAPED_MSG,\"unfurl_links\":false}" > /dev/null 2>&1

# Post to #claw-communications (not #general)
if [ -n "$CLAW_CHANNEL" ]; then
  curl -s -X POST https://slack.com/api/chat.postMessage \
    -H "Authorization: Bearer $BOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"channel\":\"$CLAW_CHANNEL\",\"text\":$ESCAPED_MSG,\"unfurl_links\":false}" > /dev/null 2>&1
  echo "[notify-slack] Sent to DM + #claw-communications"
else
  echo "[notify-slack] Sent to DM only (no claw channel found)"
fi
