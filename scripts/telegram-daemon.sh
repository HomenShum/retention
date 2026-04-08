#!/usr/bin/env bash
# telegram-daemon.sh — Long-poll Telegram for commands, execute, reply
#
# Mirrors remote-control-daemon.sh but for Telegram.
# Supports: /screenshot, /shell, /status, /health, /pipeline, natural language.
#
# Usage: nohup bash scripts/telegram-daemon.sh &

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Load env
for envfile in .env backend/.env; do
    [ -f "$envfile" ] && { set -a; source "$envfile"; set +a; }
done

: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN not set}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID not set}"

BASE_URL="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}"
OFFSET_FILE="/tmp/telegram_daemon_offset"
AUTHORIZED_USER="${TELEGRAM_AUTHORIZED_USER_ID:-}"
LOG_PREFIX="[tg-daemon]"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_PREFIX $*"; }

# ── Helpers ───────────────────────────────────────────────────────────

send_message() {
    local chat_id="$1"
    local text="$2"
    local reply_to="${3:-}"

    local data="{\"chat_id\":\"$chat_id\",\"text\":$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$text"),\"parse_mode\":\"Markdown\",\"disable_web_page_preview\":true"
    [ -n "$reply_to" ] && data="$data,\"reply_to_message_id\":$reply_to"
    data="$data}"

    curl -s -X POST "${BASE_URL}/sendMessage" \
        -H "Content-Type: application/json" \
        -d "$data" > /dev/null
}

send_photo() {
    local chat_id="$1"
    local photo_path="$2"
    local caption="${3:-}"
    local reply_to="${4:-}"

    local args=(-s -X POST "${BASE_URL}/sendPhoto"
        -F "chat_id=$chat_id"
        -F "photo=@$photo_path")
    [ -n "$caption" ] && args+=(-F "caption=$caption")
    [ -n "$reply_to" ] && args+=(-F "reply_to_message_id=$reply_to")

    curl "${args[@]}" > /dev/null
}

send_video() {
    local chat_id="$1"
    local video_path="$2"
    local caption="${3:-}"

    curl -s -X POST "${BASE_URL}/sendVideo" \
        -F "chat_id=$chat_id" \
        -F "video=@$video_path" \
        -F "caption=${caption:0:1024}" > /dev/null
}

take_screenshot() {
    local path="/tmp/tg_screenshot_$(date +%s).png"
    if command -v screencapture &>/dev/null; then
        screencapture -x "$path" 2>/dev/null
    elif command -v nircmd &>/dev/null; then
        nircmd savescreenshot "$path" 2>/dev/null
    elif python3 -c "import pyautogui" 2>/dev/null; then
        python3 -c "import pyautogui; pyautogui.screenshot('$path')" 2>/dev/null
    else
        echo ""
        return 1
    fi
    echo "$path"
}

# ── Command router ────────────────────────────────────────────────────

handle_message() {
    local chat_id="$1"
    local text="$2"
    local message_id="$3"
    local user_id="$4"

    # Auth check
    if [ -n "$AUTHORIZED_USER" ] && [ "$user_id" != "$AUTHORIZED_USER" ]; then
        log "Unauthorized user: $user_id"
        return
    fi

    log "Command from $user_id: ${text:0:80}"

    case "$text" in
        /start)
            send_message "$chat_id" "OpenClaw Remote Agent connected.

Commands:
/status — system status
/screenshot — capture screen
/shell <cmd> — run shell command
/health — check backend + frontend
/pipeline <url> — start QA pipeline
/git — git status
Or just type naturally." "$message_id"
            ;;

        /screenshot)
            local path
            path=$(take_screenshot)
            if [ -n "$path" ] && [ -f "$path" ]; then
                send_photo "$chat_id" "$path" "Current screen" "$message_id"
                rm -f "$path"
            else
                send_message "$chat_id" "Screenshot failed — no capture tool available" "$message_id"
            fi
            ;;

        /status)
            local uptime
            uptime=$(uptime 2>/dev/null || echo "unknown")
            send_message "$chat_id" "*System Status*
\`$uptime\`" "$message_id"
            ;;

        /health)
            local backend frontend
            backend=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/health 2>/dev/null || echo "down")
            frontend=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5173 2>/dev/null || echo "down")
            send_message "$chat_id" "*Health Check*
Backend (8000): $backend
Frontend (5173): $frontend" "$message_id"
            ;;

        /git)
            local status
            status=$(cd "$PROJECT_ROOT" && git status --short 2>&1 | head -20)
            send_message "$chat_id" "\`\`\`
$status
\`\`\`" "$message_id"
            ;;

        /shell\ *)
            local cmd="${text#/shell }"
            # Blocklist
            if echo "$cmd" | grep -qiE 'rm -rf|sudo|passwd|mkfs|dd if='; then
                send_message "$chat_id" "Blocked: potentially dangerous command." "$message_id"
                return
            fi
            local output
            output=$(timeout 30 bash -c "$cmd" 2>&1 | head -c 3900)
            if [ -n "$output" ]; then
                send_message "$chat_id" "\`\`\`
\$ $cmd
$output
\`\`\`" "$message_id"
            else
                send_message "$chat_id" "Executed (no output): \`$cmd\`" "$message_id"
            fi
            ;;

        /pipeline\ *)
            local url="${text#/pipeline }"
            send_message "$chat_id" "Starting QA pipeline on $url..." "$message_id"
            local result
            result=$(curl -s -X POST "http://localhost:8000/mcp/tools/call" \
                -H "Authorization: Bearer ${RETENTION_MCP_TOKEN:-sk-ret-de55f65c}" \
                -H "Content-Type: application/json" \
                -d "{\"tool\":\"ta.run_web_flow\",\"arguments\":{\"url\":\"$url\",\"app_name\":\"Telegram QA\"}}")
            local run_id
            run_id=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('result',{}).get('run_id','unknown'))" 2>/dev/null)
            send_message "$chat_id" "Pipeline started: \`$run_id\`" "$message_id"
            ;;

        /do\ *)
            # Vision control — see screen, click, type like a human
            local task="${text#/do }"
            send_message "$chat_id" "👁 Starting vision control: _${task}_" "$message_id"

            # Take before screenshot
            local before_path
            before_path=$(take_screenshot)
            [ -n "$before_path" ] && [ -f "$before_path" ] && send_photo "$chat_id" "$before_path" "Before" "$message_id" && rm -f "$before_path"

            # Run vision control via API
            local encoded_task
            encoded_task=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$task")
            local vision_result
            vision_result=$(curl -s -X POST "http://localhost:8000/api/remote/vision?task=${encoded_task}&max_steps=5" \
                -H "Authorization: Bearer ${CRON_AUTH_TOKEN}" --max-time 120 2>/dev/null)

            local steps
            steps=$(echo "$vision_result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{d.get(\"steps\",0)} steps, success={d.get(\"success\",False)}')" 2>/dev/null || echo "failed")
            send_message "$chat_id" "Vision control done: $steps" "$message_id"

            # Take after screenshot
            local after_path
            after_path=$(take_screenshot)
            [ -n "$after_path" ] && [ -f "$after_path" ] && send_photo "$chat_id" "$after_path" "After" "$message_id" && rm -f "$after_path"
            ;;

        *)
            # Natural language — classify intent then route
            local encoded_msg
            encoded_msg=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$text")

            # Check if it's a visual task (mentions screen, click, open, look, show, UI)
            if echo "$text" | grep -qiE 'screenshot|screen|click|open |look|show me|what.s on|see my|ui|window|tab'; then
                # Visual task — take screenshot and send
                local path
                path=$(take_screenshot)
                if [ -n "$path" ] && [ -f "$path" ]; then
                    send_photo "$chat_id" "$path" "Current screen" "$message_id"
                    rm -f "$path"
                else
                    send_message "$chat_id" "Screenshot failed" "$message_id"
                fi
            else
                # Everything else → vision control (see screen, think, click/type)
                send_message "$chat_id" "👁 On it..." "$message_id"

                local encoded_task
                encoded_task=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$text")
                local vision_result
                vision_result=$(curl -s -X POST "http://localhost:8000/api/remote/vision?task=${encoded_task}&max_steps=5" \
                    -H "Authorization: Bearer ${CRON_AUTH_TOKEN}" --max-time 120 2>/dev/null)

                local summary
                summary=$(echo "$vision_result" | python3 -c "
import sys,json
d = json.load(sys.stdin)
actions = d.get('actions',[])
lines = []
for a in actions:
    status = '✅' if a.get('success') else '❌'
    lines.append(f\"{status} {a.get('action','?')}: {a.get('output','') or a.get('error','')}\")
if not lines:
    lines = ['No actions taken']
print('\n'.join(lines[:10]))
" 2>/dev/null || echo "Vision control unavailable — is backend running?")

                # Send after screenshot
                local after_path
                after_path=$(take_screenshot)
                if [ -n "$after_path" ] && [ -f "$after_path" ]; then
                    send_photo "$chat_id" "$after_path" "$summary" "$message_id"
                    rm -f "$after_path"
                else
                    send_message "$chat_id" "$summary" "$message_id"
                fi
            fi
            ;;
    esac
}

# ── Main loop (long polling) ─────────────────────────────────────────

log "Starting Telegram daemon (chat: $TELEGRAM_CHAT_ID)"

# Read last offset
OFFSET=0
[ -f "$OFFSET_FILE" ] && OFFSET=$(cat "$OFFSET_FILE")

# Send startup message
send_message "$TELEGRAM_CHAT_ID" "🤖 OpenClaw Telegram daemon started.
Type /start for commands."

while true; do
    # Long poll with 30s timeout
    UPDATES=$(curl -s -X POST "${BASE_URL}/getUpdates" \
        -H "Content-Type: application/json" \
        -d "{\"offset\":$OFFSET,\"timeout\":30,\"allowed_updates\":[\"message\"]}" \
        --max-time 35 2>/dev/null)

    if [ -z "$UPDATES" ]; then
        continue
    fi

    # Parse updates into a temp file (avoid subshell pipe problem)
    PARSED_FILE="/tmp/tg_parsed_$$"
    echo "$UPDATES" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if not data.get('ok'):
        sys.exit(0)
    for update in data.get('result', []):
        msg = update.get('message', {})
        if not msg:
            continue
        uid = update['update_id']
        chat_id = msg.get('chat', {}).get('id', '')
        text = msg.get('text', '')
        user_id = msg.get('from', {}).get('id', '')
        is_bot = msg.get('from', {}).get('is_bot', False)
        msg_id = msg.get('message_id', 0)
        if is_bot:
            continue
        print(f'{uid}|{chat_id}|{text}|{msg_id}|{user_id}')
except:
    pass
" > "$PARSED_FILE"

    while IFS='|' read -r update_id chat_id text message_id user_id; do
        # Update offset — this now runs in the MAIN shell, not a subshell
        OFFSET=$((update_id + 1))
        echo "$OFFSET" > "$OFFSET_FILE"

        # Handle message
        handle_message "$chat_id" "$text" "$message_id" "$user_id"
    done < "$PARSED_FILE"
    rm -f "$PARSED_FILE"

done
