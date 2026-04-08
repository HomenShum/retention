#!/usr/bin/env bash
# Remote Control Daemon — watches Slack for !remote commands
# and executes them on this Mac via the backend API.
#
# Usage: ./scripts/remote-control-daemon.sh
#
# Commands recognized (in Slack messages starting with !remote):
#   !remote screenshot         — take and upload a screenshot
#   !remote click 100 200      — click at coordinates
#   !remote type "hello"       — type text
#   !remote key cmd+c          — press key combo
#   !remote open Safari        — open an app
#   !remote shell ls -la       — run a shell command
#   !remote claude "fix the bug in app.py"  — run Claude Code
#   !remote status             — show running apps, screen info
#   !remote <natural language> — LLM interprets and executes
#
# The daemon creates a dedicated "Remote Control" thread and posts
# all results there.

set -uo pipefail
# Note: NOT using -e because Python JSON parsing may fail on empty responses

# Load env
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

for envfile in "$PROJECT_ROOT/.env" "$PROJECT_ROOT/backend/.env"; do
    if [ -f "$envfile" ]; then
        set -a; source "$envfile"; set +a
    fi
done

# Required env vars
SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN:?SLACK_BOT_TOKEN not set}"
CLAW_CHANNEL="${CLAW_CHANNEL:-C0AM2J4G6S0}"
BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
CRON_AUTH_TOKEN="${CRON_AUTH_TOKEN:-}"
REMOTE_CONTROL_USER_ID="${REMOTE_CONTROL_USER_ID:-}"
POLL_INTERVAL="${REMOTE_POLL_INTERVAL:-5}"  # Faster polling for remote control

LOG_PREFIX="[remote-ctrl]"
LAST_TS_FILE="/tmp/openclaw_remote_last_ts"
REMOTE_THREAD_FILE="/tmp/openclaw_remote_thread_ts"

# Initialize last timestamp (now, in Slack format: epoch.microseconds)
if [ ! -f "$LAST_TS_FILE" ]; then
    echo "$(date +%s).000000" > "$LAST_TS_FILE"
fi

log() { echo "$(date '+%H:%M:%S') $LOG_PREFIX $*"; }

# ------------------------------------------------------------------
# Create or get the dedicated Remote Control thread
# ------------------------------------------------------------------
get_or_create_remote_thread() {
    # Check cached thread
    if [ -f "$REMOTE_THREAD_FILE" ]; then
        local cached_ts
        cached_ts=$(cat "$REMOTE_THREAD_FILE")
        if [ -n "$cached_ts" ]; then
            echo "$cached_ts"
            return
        fi
    fi

    # Scan channel for existing "Remote Control" thread
    local scan_resp
    scan_resp=$(curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
        "https://slack.com/api/conversations.history?channel=$CLAW_CHANNEL&limit=15")

    local existing_ts
    existing_ts=$(echo "$scan_resp" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for msg in data.get('messages', []):
    if 'Remote Control' in msg.get('text', '') and 'Station' in msg.get('text', ''):
        print(msg.get('ts', ''))
        break
" 2>/dev/null)

    if [ -n "$existing_ts" ]; then
        echo "$existing_ts" > "$REMOTE_THREAD_FILE"
        echo "$existing_ts"
        return
    fi

    # Create new thread
    local create_resp
    create_resp=$(curl -s -X POST "https://slack.com/api/chat.postMessage" \
        -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$(cat <<PAYLOAD
{
    "channel": "$CLAW_CHANNEL",
    "text": "*Remote Control Station*\n_Send \`!remote <command>\` in this thread to operate this Mac remotely._\n\n*Commands:*\n\u2022 \`!remote screenshot\` \u2014 capture screen\n\u2022 \`!remote click X Y\` \u2014 click at coords\n\u2022 \`!remote type \"text\"\` \u2014 type text\n\u2022 \`!remote key cmd+c\` \u2014 key press\n\u2022 \`!remote open AppName\` \u2014 open app\n\u2022 \`!remote shell <cmd>\` \u2014 run command\n\u2022 \`!remote claude \"prompt\"\` \u2014 run Claude Code\n\u2022 \`!remote status\` \u2014 system info\n\u2022 \`!remote <anything>\` \u2014 LLM auto-interprets\n\n_Only authorized user can control. All actions logged._",
    "unfurl_links": false
}
PAYLOAD
)")

    local thread_ts
    thread_ts=$(echo "$create_resp" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('ts', '') or data.get('message', {}).get('ts', ''))
" 2>/dev/null)

    if [ -n "$thread_ts" ]; then
        echo "$thread_ts" > "$REMOTE_THREAD_FILE"
        log "Created Remote Control thread: $thread_ts"
    fi

    echo "$thread_ts"
}

# ------------------------------------------------------------------
# Post a reply to the remote thread
# ------------------------------------------------------------------
post_reply() {
    local thread_ts="$1"
    local text="$2"

    curl -s -X POST "https://slack.com/api/chat.postMessage" \
        -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "
import json
print(json.dumps({
    'channel': '$CLAW_CHANNEL',
    'thread_ts': '$thread_ts',
    'text': $(python3 -c "import json; print(json.dumps('''$text'''))" 2>/dev/null || echo '\"$text\"'),
    'unfurl_links': False,
}))
")" > /dev/null 2>&1
}

# Simpler post_reply that handles JSON properly — uses env vars + heredoc
post_reply_safe() {
    local thread_ts="$1"
    local text="$2"

    REPLY_CHANNEL="$CLAW_CHANNEL" REPLY_TS="$thread_ts" REPLY_TEXT="$text" REPLY_TOKEN="$SLACK_BOT_TOKEN" \
    python3 << 'PYEOF' 2>/dev/null
import json, urllib.request, os
data = json.dumps({
    "channel": os.environ["REPLY_CHANNEL"],
    "thread_ts": os.environ["REPLY_TS"],
    "text": os.environ["REPLY_TEXT"],
    "unfurl_links": False,
}).encode()
req = urllib.request.Request(
    "https://slack.com/api/chat.postMessage",
    data=data,
    headers={
        "Authorization": f"Bearer {os.environ['REPLY_TOKEN']}",
        "Content-Type": "application/json",
    },
)
urllib.request.urlopen(req)
PYEOF
}

# ------------------------------------------------------------------
# Screen capture modes:
#   SLIM (default) — 3 burst screenshots: before → during → after
#                    stitched into a short mp4 (~20-50KB). Fast, tiny.
#   FULL           — continuous ffmpeg recording at 10fps.
#                    For demo walkthroughs, video requests.
# ------------------------------------------------------------------
RECORDING_PID=""
RECORDING_FILE=""
SLIM_FRAMES_DIR=""

# ---- SLIM MODE: burst screenshots → stitched mp4 ----

slim_capture_before() {
    SLIM_FRAMES_DIR="/tmp/openclaw_slim_$(date +%s)"
    mkdir -p "$SLIM_FRAMES_DIR"
    screencapture -x "$SLIM_FRAMES_DIR/01_before.png" 2>/dev/null
    log "Slim: captured before frame"
}

slim_capture_during() {
    if [ -n "$SLIM_FRAMES_DIR" ]; then
        screencapture -x "$SLIM_FRAMES_DIR/02_during.png" 2>/dev/null
        log "Slim: captured during frame"
    fi
}

slim_capture_after() {
    if [ -n "$SLIM_FRAMES_DIR" ]; then
        screencapture -x "$SLIM_FRAMES_DIR/03_after.png" 2>/dev/null
        log "Slim: captured after frame"
    fi
}

slim_stitch_and_upload() {
    local thread_ts="$1"
    if [ -z "$SLIM_FRAMES_DIR" ] || [ ! -d "$SLIM_FRAMES_DIR" ]; then
        return
    fi

    local frame_count
    frame_count=$(ls "$SLIM_FRAMES_DIR"/*.png 2>/dev/null | wc -l | xargs)

    if [ "$frame_count" -gt 0 ]; then
        RECORDING_FILE="$SLIM_FRAMES_DIR/actionspan.mp4"
        ffmpeg -y -framerate 1 -pattern_type glob -i "$SLIM_FRAMES_DIR/*.png" \
            -c:v libx264 -preset ultrafast -crf 23 -pix_fmt yuv420p \
            -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" \
            "$RECORDING_FILE" > /dev/null 2>&1

        if [ -f "$RECORDING_FILE" ]; then
            upload_file_to_slack "$thread_ts" "$RECORDING_FILE" "ActionSpan $(date '+%H:%M:%S')"
            log "Slim: uploaded actionspan ($frame_count frames)"
        fi
    fi

    rm -rf "$SLIM_FRAMES_DIR"
    SLIM_FRAMES_DIR=""
    RECORDING_FILE=""
}

# ---- FULL MODE: continuous ffmpeg recording ----

start_recording_full() {
    RECORDING_FILE="/tmp/openclaw_remote_rec_$(date +%s).mp4"
    local screen_dev="${FFMPEG_SCREEN_DEVICE:-2}"
    ffmpeg -y -f avfoundation -framerate 10 -capture_cursor 1 -capture_mouse_clicks 1 \
        -i "${screen_dev}:none" -c:v libx264 -preset ultrafast -crf 28 -pix_fmt yuv420p \
        "$RECORDING_FILE" > /dev/null 2>&1 &
    RECORDING_PID=$!
    log "Full recording started (PID $RECORDING_PID)"
    sleep 0.5
}

stop_recording_full() {
    if [ -n "$RECORDING_PID" ]; then
        kill -INT "$RECORDING_PID" 2>/dev/null
        wait "$RECORDING_PID" 2>/dev/null
        log "Full recording stopped"
        RECORDING_PID=""
    fi
}

upload_recording_to_slack() {
    local thread_ts="$1"
    if [ -n "$RECORDING_FILE" ] && [ -f "$RECORDING_FILE" ]; then
        upload_file_to_slack "$thread_ts" "$RECORDING_FILE" "Recording $(date '+%H:%M:%S')"
        rm -f "$RECORDING_FILE"
        RECORDING_FILE=""
    fi
}

# ---- Convenience aliases used by command handlers ----
# Default: slim mode
start_recording() { slim_capture_before; }
mid_recording()   { slim_capture_during; }
stop_recording()  { slim_capture_after; }
finish_recording() {
    local thread_ts="$1"
    slim_stitch_and_upload "$thread_ts"
}

# Full mode overrides (for demo/walkthrough commands)
start_recording_mode() {
    local mode="$1"  # "slim" or "full"
    if [ "$mode" = "full" ]; then
        start_recording_full
    else
        slim_capture_before
    fi
}
stop_recording_mode() {
    local mode="$1"
    local thread_ts="$2"
    if [ "$mode" = "full" ]; then
        stop_recording_full
        upload_recording_to_slack "$thread_ts"
    else
        slim_capture_after
        slim_stitch_and_upload "$thread_ts"
    fi
}

# Wrap a command: record → execute → stop → upload
record_and_execute() {
    local thread_ts="$1"
    shift
    slim_capture_before
    "$@"
    sleep 0.3  # Brief pause to capture final state
    stop_recording
    upload_recording_to_slack "$thread_ts"
}

# ------------------------------------------------------------------
# File upload helpers (new Slack API — files.upload is deprecated)
# ------------------------------------------------------------------
upload_file_to_slack() {
    local thread_ts="$1"
    local filepath="$2"
    local title="${3:-File $(date '+%H:%M:%S')}"

    local file_size
    file_size=$(stat -f%z "$filepath" 2>/dev/null || echo "0")
    [ "$file_size" -lt 100 ] && return

    local filename
    filename=$(basename "$filepath")

    # Step 1: Get upload URL
    local upload_resp
    upload_resp=$(curl -s -X POST "https://slack.com/api/files.getUploadURLExternal" \
        -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "filename=$filename&length=$file_size")

    local upload_url file_id
    upload_url=$(echo "$upload_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('upload_url',''))" 2>/dev/null)
    file_id=$(echo "$upload_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('file_id',''))" 2>/dev/null)

    [ -z "$upload_url" ] || [ -z "$file_id" ] && { log "Upload URL fetch failed"; return; }

    # Step 2: Upload the file
    curl -s -X POST "$upload_url" -F "file=@$filepath" > /dev/null

    # Step 3: Complete and share
    curl -s -X POST "https://slack.com/api/files.completeUploadExternal" \
        -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"files\":[{\"id\":\"$file_id\",\"title\":\"$title\"}],\"channel_id\":\"$CLAW_CHANNEL\",\"thread_ts\":\"$thread_ts\"}" > /dev/null 2>&1
}

upload_screenshot_to_slack() {
    upload_file_to_slack "$1" "$2" "Screenshot $(date '+%H:%M:%S')"
}

# ------------------------------------------------------------------
# Evidence strategy classifier
# Determines what visual evidence (if any) to capture for a command.
#
# Returns one of:
#   none      — text-only response, no visual (status, git log, etc.)
#   after     — single screenshot after completion (open app, navigate)
#   slim      — before/after actionspan (click, type, visual changes)
#   full      — continuous video recording (demo, walkthrough)
# ------------------------------------------------------------------
classify_evidence_strategy() {
    local command="$1"

    # Fast-path: explicit user overrides
    case "$command" in
        screenshot|ss|screen) echo "screenshot"; return ;;
        demo\ *|walkthrough\ *|record\ *) echo "full"; return ;;
    esac

    # Heuristic classifier (instant, no LLM cost)
    # Visual actions → need visual evidence
    # Text-only results → no visual needed
    EVIDENCE_CMD="$command" python3 << 'PYEOF' 2>/dev/null || echo "none"
import os
cmd = os.environ.get("EVIDENCE_CMD", "").lower().strip()

# NONE: purely text output, no screen change
none_patterns = [
    "status", "info", "git status", "git log", "git diff",
    "ls", "pwd", "cat ", "head ", "tail ", "grep ",
    "echo ", "which ", "whoami", "date", "uptime",
    "ps ", "top", "df ", "du ", "free",
    "pip ", "npm ", "node -", "python -",
    "what time", "what day", "how much",
    "check ", "list ",
]

# AFTER: opens/changes something visible, single screenshot captures it
after_patterns = [
    "open ", "launch ", "start ",
    "switch to", "go to", "navigate",
    "close ", "quit ", "minimize", "maximize",
    "resize", "move window",
    "scroll", "zoom",
]

# SLIM: interactive actions where before/after diff matters
slim_patterns = [
    "click", "tap", "press",
    "type ", "enter ", "fill ",
    "drag", "drop", "select",
    "delete ", "remove ", "clear",
    "paste", "copy", "cut",
    "key ", "shortcut",
    "install", "deploy", "build",
]

for p in none_patterns:
    if cmd.startswith(p) or p in cmd:
        print("none")
        exit()

for p in slim_patterns:
    if cmd.startswith(p) or p in cmd:
        print("slim")
        exit()

for p in after_patterns:
    if cmd.startswith(p) or p in cmd:
        print("after")
        exit()

# Default: if it's a shell command or text question → none
# If it seems visual or unclear → after (cheap single screenshot)
shell_prefixes = ["shell ", "sh ", "run ", "exec ", "claude "]
for p in shell_prefixes:
    if cmd.startswith(p):
        print("none")
        exit()

# Natural language: if it mentions visual words → after, else none
visual_words = ["show", "look", "see", "screen", "window", "app", "browser", "page", "site"]
if any(w in cmd for w in visual_words):
    print("after")
else:
    print("none")
PYEOF
}

execute_remote_command() {
    local thread_ts="$1"
    local raw_command="$2"
    local user_id="$3"

    # Auth check
    if [ -n "$REMOTE_CONTROL_USER_ID" ] && [ "$user_id" != "$REMOTE_CONTROL_USER_ID" ]; then
        post_reply_safe "$thread_ts" "Unauthorized. Only the configured user can control this Mac."
        return
    fi

    # Strip !remote prefix if present
    local command
    command=$(echo "$raw_command" | sed 's/^!remote[[:space:]]*//')

    log "Executing: $command (user: $user_id)"

    # Classify evidence strategy
    local evidence
    evidence=$(classify_evidence_strategy "$command")
    log "Evidence strategy: $evidence"

    # ---- Execute the command ----
    local output=""

    case "$command" in
        screenshot|ss|screen)
            post_reply_safe "$thread_ts" "📸"
            local ss_file="/tmp/openclaw_remote_ss_$(date +%s).png"
            screencapture -x "$ss_file" 2>/dev/null
            if [ -f "$ss_file" ]; then
                upload_screenshot_to_slack "$thread_ts" "$ss_file"
                rm -f "$ss_file"
            else
                post_reply_safe "$thread_ts" "Screenshot failed"
            fi
            return  # Screenshot is its own evidence, skip the evidence phase
            ;;

        status|info)
            local frontmost
            frontmost=$(osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true' 2>/dev/null || echo "unknown")
            local running_apps
            running_apps=$(osascript -e 'tell application "System Events" to get name of every process whose background only is false' 2>/dev/null | head -c 500 || echo "unknown")
            local clipboard_preview
            clipboard_preview=$(pbpaste 2>/dev/null | head -c 100 || echo "")
            output="*Active:* $frontmost
*Apps:* $running_apps
*Clipboard:* ${clipboard_preview:0:100}"
            ;;

        click\ *)
            local coords
            coords=$(echo "$command" | sed 's/^click[[:space:]]*//')
            local cx cy
            cx=$(echo "$coords" | awk '{print $1}')
            cy=$(echo "$coords" | awk '{print $2}')
            [ "$evidence" = "slim" ] && slim_capture_before
            cliclick c:"$cx","$cy" 2>/dev/null
            sleep 0.3
            output="Clicked ($cx, $cy)"
            ;;

        type\ *)
            local text_to_type
            text_to_type=$(echo "$command" | sed 's/^type[[:space:]]*//' | sed 's/^"//;s/"$//')
            [ "$evidence" = "slim" ] && slim_capture_before
            cliclick t:"$text_to_type" 2>/dev/null
            output="Typed: $text_to_type"
            ;;

        key\ *)
            local key_combo
            key_combo=$(echo "$command" | sed 's/^key[[:space:]]*//')
            [ "$evidence" = "slim" ] && slim_capture_before
            cliclick kp:"$key_combo" 2>/dev/null || cliclick kd:"$key_combo" ku:"$key_combo" 2>/dev/null
            output="Pressed: $key_combo"
            ;;

        open\ *)
            local app_name
            app_name=$(echo "$command" | sed 's/^open[[:space:]]*//')
            [ "$evidence" = "slim" ] && slim_capture_before
            open -a "$app_name" 2>/dev/null
            sleep 1.5
            output="Opened: $app_name"
            ;;

        shell\ *)
            local shell_cmd
            shell_cmd=$(echo "$command" | sed 's/^shell[[:space:]]*//')
            [ "$evidence" = "slim" ] && slim_capture_before
            output=$(eval "$shell_cmd" 2>&1 | head -c 3000 || echo "(command failed)")
            ;;

        claude\ *)
            local claude_prompt
            claude_prompt=$(echo "$command" | sed 's/^claude[[:space:]]*//' | sed 's/^"//;s/"$//')
            post_reply_safe "$thread_ts" "Running Claude Code..."
            [ "$evidence" = "slim" ] && slim_capture_before
            output=$(cd "$PROJECT_ROOT" && claude --print "$claude_prompt" 2>&1 | head -c 3500 || echo "Claude Code failed")
            ;;

        demo\ *|walkthrough\ *|record\ *)
            local full_prompt
            full_prompt=$(echo "$command" | sed 's/^demo[[:space:]]*//;s/^walkthrough[[:space:]]*//;s/^record[[:space:]]*//')
            post_reply_safe "$thread_ts" "🎬 Recording..."
            start_recording_full
            output=$(cd "$PROJECT_ROOT" && claude --print "You are controlling a Mac remotely and being recorded for a demo. The user asked: $full_prompt. Execute visually — open apps, navigate, pause between steps. Use osascript, cliclick, screencapture, open." 2>&1 | head -c 3500 || echo "Demo failed")
            stop_recording_full
            post_reply_safe "$thread_ts" "$output"
            upload_recording_to_slack "$thread_ts"
            return  # Full mode handles its own evidence
            ;;

        *)
            # Natural language — Claude interprets
            [ "$evidence" = "slim" ] && slim_capture_before
            output=$(cd "$PROJECT_ROOT" && claude --print "You are controlling a Mac remotely. The user asked: $command. Execute using shell commands, osascript, cliclick, or screencapture. Be concise. Reply with what you did." 2>&1 | head -c 3500 || echo "Failed")
            ;;
    esac

    # ---- Post text result ----
    if [ -n "$output" ]; then
        # Shell output gets code block formatting
        case "$command" in
            shell\ *)
                post_reply_safe "$thread_ts" "\`\`\`
$output
\`\`\`" ;;
            *)
                post_reply_safe "$thread_ts" "$output" ;;
        esac
    fi

    # ---- Capture evidence based on strategy ----
    case "$evidence" in
        none)
            # No visual evidence needed
            log "Evidence: none (text-only)"
            ;;
        after)
            # Single screenshot after action
            local ss_file="/tmp/openclaw_remote_ss_$(date +%s).png"
            screencapture -x "$ss_file" 2>/dev/null
            if [ -f "$ss_file" ]; then
                upload_screenshot_to_slack "$thread_ts" "$ss_file"
                rm -f "$ss_file"
            fi
            log "Evidence: after screenshot"
            ;;
        slim)
            # Before was already captured, now capture after and stitch
            slim_capture_after
            slim_stitch_and_upload "$thread_ts"
            log "Evidence: slim actionspan"
            ;;
        # full is handled inline above
    esac
}

# ------------------------------------------------------------------
# Main polling loop
# ------------------------------------------------------------------
main() {
    log "Starting Remote Control daemon"
    log "Channel: $CLAW_CHANNEL | Backend: $BACKEND_URL | Poll: ${POLL_INTERVAL}s"

    # Create the dedicated thread
    local thread_ts
    thread_ts=$(get_or_create_remote_thread)

    if [ -z "$thread_ts" ]; then
        log "ERROR: Could not create Remote Control thread"
        exit 1
    fi

    log "Remote Control thread: $thread_ts"
    post_reply_safe "$thread_ts" "Remote Control daemon online. Mac is ready for commands."

    local last_ts
    last_ts=$(cat "$LAST_TS_FILE")

    while true; do
        sleep "$POLL_INTERVAL"

        # Poll thread replies for new !remote commands
        local replies_resp
        replies_resp=$(curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
            "https://slack.com/api/conversations.replies?channel=$CLAW_CHANNEL&ts=$thread_ts&oldest=$last_ts&limit=10" 2>/dev/null)

        if [ -z "$replies_resp" ]; then
            continue
        fi

        # Also check channel for !remote messages not in thread
        local channel_resp
        channel_resp=$(curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
            "https://slack.com/api/conversations.history?channel=$CLAW_CHANNEL&oldest=$last_ts&limit=10" 2>/dev/null)

        # Process messages — write JSON to temp files to avoid quoting hell
        local tmp_thread="/tmp/openclaw_remote_thread_resp.json"
        local tmp_channel="/tmp/openclaw_remote_channel_resp.json"
        echo "$replies_resp" > "$tmp_thread" 2>/dev/null
        echo "$channel_resp" > "$tmp_channel" 2>/dev/null

        python3 << 'PYEOF' 2>/dev/null | while IFS=$'\t' read -r msg_ts user_id msg_text; do
import json, pathlib

msgs = []
for f in ["/tmp/openclaw_remote_thread_resp.json", "/tmp/openclaw_remote_channel_resp.json"]:
    try:
        data = json.loads(pathlib.Path(f).read_text())
        msgs.extend(data.get("messages", []))
    except Exception:
        pass

thread_ts_file = "/tmp/openclaw_remote_thread_ts"
try:
    remote_thread = pathlib.Path(thread_ts_file).read_text().strip()
except Exception:
    remote_thread = ""

for msg in msgs:
    text = msg.get("text", "").strip()
    ts = msg.get("ts", "")
    user = msg.get("user", "")
    thread = msg.get("thread_ts", "")
    if msg.get("bot_id") or msg.get("subtype") == "bot_message":
        continue
    if not text:
        continue
    # Accept: (a) any message in the Remote Control thread, or (b) !remote anywhere
    in_thread = (thread == remote_thread and ts != remote_thread)
    has_prefix = text.startswith("!remote")
    if in_thread or has_prefix:
        print(f"{ts}\t{user}\t{text}")
PYEOF
            if [ -n "$msg_ts" ] && [ -n "$msg_text" ]; then
                # Update last_ts to skip this message next time
                echo "$msg_ts" > "$LAST_TS_FILE"
                last_ts="$msg_ts"

                execute_remote_command "$thread_ts" "$msg_text" "$user_id"
            fi
        done

        # Update timestamp (Slack format)
        local new_ts
        new_ts="$(date +%s).000000"
        echo "$new_ts" > "$LAST_TS_FILE"
        last_ts="$new_ts"
    done
}

main "$@"
