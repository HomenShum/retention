#!/usr/bin/env bash
# notify-telegram.sh — Send a notification to Telegram
# Usage: ./notify-telegram.sh "Your message here"
#        ./notify-telegram.sh "Message" /path/to/screenshot.png

set -uo pipefail

# Load env
for envfile in .env backend/.env; do
    [ -f "$envfile" ] && { set -a; source "$envfile"; set +a; }
done

: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN not set}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID not set}"

MESSAGE="${1:-}"
PHOTO="${2:-}"

if [ -z "$MESSAGE" ] && [ -z "$PHOTO" ]; then
    echo "Usage: $0 <message> [photo_path]"
    exit 1
fi

BASE_URL="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}"

# Send photo if provided
if [ -n "$PHOTO" ] && [ -f "$PHOTO" ]; then
    curl -s -X POST "${BASE_URL}/sendPhoto" \
        -F "chat_id=${TELEGRAM_CHAT_ID}" \
        -F "photo=@${PHOTO}" \
        -F "caption=${MESSAGE:0:1024}" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK' if d.get('ok') else d.get('description','fail'))"
    exit $?
fi

# Send text message
curl -s -X POST "${BASE_URL}/sendMessage" \
    -H "Content-Type: application/json" \
    -d "{\"chat_id\":\"${TELEGRAM_CHAT_ID}\",\"text\":$(python3 -c "import json; print(json.dumps('$MESSAGE'))"),\"parse_mode\":\"Markdown\",\"disable_web_page_preview\":true}" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK' if d.get('ok') else d.get('description','fail'))"
