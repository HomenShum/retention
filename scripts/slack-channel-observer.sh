#!/bin/bash
# slack-channel-observer.sh — Polls Slack channels for new messages and routes them
#
# Reads recent messages from configured channels. When someone @mentions the bot
# or asks a direct question, routes the query to the deep agent backend and posts
# the response as a THREADED reply (not a new message in the channel).
#
# SAFETY: Only responds to @mentions by default. Questions without @mention
# are logged but not auto-replied to (opt-in via RESPOND_TO_QUESTIONS=true).
#
# Usage: ./slack-channel-observer.sh [--once]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
STATE_DIR="$PROJECT_DIR/.claude"
LOG_DIR="$STATE_DIR/logs"
LOG_FILE="$LOG_DIR/slack-observer.log"
LOCK_DIR="$STATE_DIR/slack-observer.lock"
LOCK_PID_FILE="$LOCK_DIR/pid"
PROCESSED_DIR="$STATE_DIR/processed-events"
PROCESSED_TTL_MINUTES="${PROCESSED_TTL_MINUTES:-1440}"

mkdir -p "$LOG_DIR" "$PROCESSED_DIR"

# ─── Load .env ────────────────────────────────────────────────────────────
for envfile in "$PROJECT_DIR/.env" "$PROJECT_DIR/backend/.env"; do
    [ -f "$envfile" ] && { set -a; source "$envfile"; set +a; }
done
# ─── Slack Config ─────────────────────────────────────────────────────────
SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN:-}"
if [ -z "$SLACK_BOT_TOKEN" ]; then
  echo "ERROR: SLACK_BOT_TOKEN is not set. Export it before running this script." >&2
  exit 1
fi
BOT_USER_ID="U0ALSPANA1G"  # openclaw_retention bot

# Channels to observe — #claw-communications (not #general!)
OBSERVE_CHANNELS="${OBSERVE_CHANNELS:-}"  # Will be discovered below

# Safety: only respond to @mentions by default
RESPOND_TO_QUESTIONS="${RESPOND_TO_QUESTIONS:-false}"

# Backend API for deep agent
BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"

POLL_INTERVAL="${POLL_INTERVAL:-5}"  # 60 seconds between polls (not 30)
LAST_TS_PREFIX="$STATE_DIR/slack-observer-last-ts"

# Rate limiting: max 5 replies per 10 minutes
RATE_FILE="$STATE_DIR/slack-observer-rate"
MAX_REPLIES_PER_WINDOW=5
RATE_WINDOW_SECONDS=600

# Command-word gating file (written by the autonomous monitor via Convex)
COMMAND_WORD_FILE="$STATE_DIR/slack-observer-command-word"

# ─── Helpers ──────────────────────────────────────────────────────────────

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }

cleanup_lock() {
  if [ -f "$LOCK_PID_FILE" ] && [ "$(cat "$LOCK_PID_FILE" 2>/dev/null)" = "$$" ]; then
    rm -rf "$LOCK_DIR" 2>/dev/null || true
  fi
}

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$$" > "$LOCK_PID_FILE"
    return 0
  fi

  local existing_pid=""
  if [ -f "$LOCK_PID_FILE" ]; then
    existing_pid=$(cat "$LOCK_PID_FILE" 2>/dev/null || echo "")
  fi

  if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null; then
    log "Another slack observer is already running (pid=$existing_pid). Exiting."
    exit 0
  fi

  log "Found stale slack observer lock${existing_pid:+ (pid=$existing_pid)} — recovering."
  rm -rf "$LOCK_DIR" 2>/dev/null || true
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$$" > "$LOCK_PID_FILE"
    return 0
  fi

  log "Failed to acquire slack observer lock. Exiting."
  exit 1
}

mark_event_once() {
  local raw_key="$1"
  local ttl_minutes="${2:-$PROCESSED_TTL_MINUTES}"
  local safe_key
  safe_key=$(printf '%s' "$raw_key" | tr -cs '[:alnum:]._-' '_')
  [ -z "$safe_key" ] && return 1

  mkdir -p "$PROCESSED_DIR"
  local marker_dir="$PROCESSED_DIR/$safe_key"
  if mkdir "$marker_dir" 2>/dev/null; then
    date +%s > "$marker_dir/created_at"
    find "$PROCESSED_DIR" -mindepth 1 -maxdepth 1 -type d -mmin +"$ttl_minutes" -exec rm -rf {} + 2>/dev/null || true
    return 0
  fi

  return 1
}

trap cleanup_lock EXIT INT TERM

slack_api() {
  local method="$1"
  shift
  curl -s -X POST "https://slack.com/api/$method" \
    -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
    -H "Content-Type: application/json" \
    "$@"
}

# Discover #claw-communications channel
discover_channel() {
  if [ -n "$OBSERVE_CHANNELS" ]; then
    return
  fi

  local result
  result=$(curl -s "https://slack.com/api/conversations.list" \
    -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
    -d "types=public_channel" \
    -d "limit=200" 2>/dev/null)

  OBSERVE_CHANNELS=$(echo "$result" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for ch in data.get('channels', []):
    if 'claw' in ch.get('name', '').lower():
        print(ch['id'])
        break
" 2>/dev/null || echo "")

  if [ -z "$OBSERVE_CHANNELS" ]; then
    log "WARNING: Could not find #claw-communications channel. Observer will idle."
  else
    log "Discovered #claw-communications: $OBSERVE_CHANNELS"
    # Join the channel
    curl -s -X POST "https://slack.com/api/conversations.join" \
      -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"channel\":\"$OBSERVE_CHANNELS\"}" > /dev/null 2>&1 || true
  fi
}

check_rate_limit() {
  local now
  now=$(date +%s)
  local cutoff=$((now - RATE_WINDOW_SECONDS))

  # Read timestamps, filter to window
  if [ -f "$RATE_FILE" ]; then
    local count
    count=$(awk -v cutoff="$cutoff" '$1 > cutoff' "$RATE_FILE" 2>/dev/null | wc -l | tr -d ' ')
    if [ "$count" -ge "$MAX_REPLIES_PER_WINDOW" ]; then
      log "Rate limited: $count replies in last ${RATE_WINDOW_SECONDS}s"
      return 1
    fi
  fi
  echo "$now" >> "$RATE_FILE"

  # Trim old entries
  if [ -f "$RATE_FILE" ]; then
    awk -v cutoff="$cutoff" '$1 > cutoff' "$RATE_FILE" > "$RATE_FILE.tmp" 2>/dev/null
    mv "$RATE_FILE.tmp" "$RATE_FILE" 2>/dev/null || true
  fi
  return 0
}

get_last_ts() {
  local channel="$1"
  local ts_file="${LAST_TS_PREFIX}_${channel}"
  if [ -f "$ts_file" ]; then
    cat "$ts_file"
  else
    # Default: now (don't process old messages on first run)
    python3 -c "import time; print(f'{time.time():.6f}')"
  fi
}

set_last_ts() {
  local channel="$1"
  local ts="$2"
  # Guard: only write if ts looks like a valid Slack timestamp (digits.digits)
  if echo "$ts" | grep -qE '^[0-9]+\.[0-9]+$'; then
    echo "$ts" > "${LAST_TS_PREFIX}_${channel}"
  else
    log "WARNING: rejecting invalid timestamp for $channel: $(echo "$ts" | head -c 40)"
  fi
}

# ─── Intent Detection ─────────────────────────────────────────────────────

# ─── Command-Word Gating ─────────────────────────────────────────────────

# Sync command word from Convex task state (called once per poll cycle)
sync_command_word() {
  local convex_url="${CONVEX_SITE_URL:-}"
  local cron_token="${CRON_AUTH_TOKEN:-}"
  if [ -z "$convex_url" ] || [ -z "$cron_token" ]; then
    return
  fi

  local result
  result=$(curl -s --max-time 5 \
    "${convex_url}/api/slack/task-state?taskName=monitor" \
    -H "Authorization: Bearer $cron_token" 2>/dev/null) || return

  local cw
  cw=$(echo "$result" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if data and data.get('commandWord'):
        print(data['commandWord'])
    else:
        print('')
except: print('')
" 2>/dev/null)

  if [ -n "$cw" ]; then
    echo "$cw" > "$COMMAND_WORD_FILE"
  else
    rm -f "$COMMAND_WORD_FILE" 2>/dev/null || true
  fi
}

# Check if a message passes the command-word gate
# Returns 0 (pass) if no command word is set, or if the message contains the word
# Returns 1 (blocked) if a command word is set but not found in the message
check_command_word() {
  local text="$1"
  if [ ! -f "$COMMAND_WORD_FILE" ]; then
    return 0  # No command word set — allow all
  fi
  local cw
  cw=$(cat "$COMMAND_WORD_FILE" 2>/dev/null)
  [ -z "$cw" ] && return 0

  # Check if the message contains the command word (case-insensitive, word boundary)
  if echo "$text" | grep -qiw "$cw"; then
    return 0  # Command word found
  fi

  # Also check for command-word setup/clear patterns (always let through)
  if echo "$text" | grep -qiE 'only respond (if|when)|set (trigger|command) word|clear (trigger|command) word|respond to everything'; then
    return 0  # Meta-command about the word itself
  fi

  log "Command-word gate blocked message (word='$cw')"
  return 1
}

# ─── Intent Detection ─────────────────────────────────────────────────────

is_brief_question() {
  local text="$1"
  echo "$text" | grep -qiE 'brief|report|investor|strategy|where are we|burn|cost|scenario|section|evidence|tie.*to.*report|codebase.*report' && return 0
  return 1
}

is_actionable_message() {
  local text="$1"
  local user="$2"

  # Skip bot's own messages
  [ "$user" = "$BOT_USER_ID" ] && return 1

  # Skip empty
  [ -z "$text" ] && return 1

  # Always respond to @mentions
  echo "$text" | grep -q "<@${BOT_USER_ID}>" && return 0

  # Only respond to bare questions if explicitly opted in
  if [ "$RESPOND_TO_QUESTIONS" = "true" ]; then
    echo "$text" | grep -qiE '\?$' && return 0
  fi

  return 1
}

query_deep_agent() {
  local question="$1"

  # Strip bot mention
  local clean_q
  clean_q=$(echo "$question" | sed "s/<@${BOT_USER_ID}>//g" | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*$//')
  [ -z "$clean_q" ] && clean_q="What's the current status of retention.sh?"

  log "Querying deep agent: $clean_q"

  # Build JSON payload safely via python3 stdin
  local payload
  payload=$(echo "$clean_q" | python3 -c "
import sys, json
q = sys.stdin.read().strip()
print(json.dumps({
    'prompt': 'You are the retention.sh AI assistant answering a Slack question. Be concise (under 300 words). Question: ' + q,
    'tools': ['retention.codebase.search', 'retention.codebase.read_file', 'retention.codebase.recent_commits', 'retention.codebase.git_status'],
    'max_turns': 3
}))
" 2>/dev/null)

  if [ -z "$payload" ]; then
    log "Failed to build JSON payload"
    echo "I had trouble processing that question."
    return
  fi

  # Try deep agent subagent endpoint
  local response
  response=$(curl -s --max-time 30 -X POST "$BACKEND_URL/api/deep-agent/subagent" \
    -H "Content-Type: application/json" \
    -d "$payload" 2>/dev/null) || response=""

  # Check if response is valid JSON with content
  local answer
  answer=$(echo "$response" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if data.get('error'):
        raise Exception(data['error'])
    text = data.get('text', data.get('result', data.get('answer', data.get('output', ''))))
    if isinstance(text, dict):
        text = text.get('text', str(text))
    text = str(text).strip()
    if text:
        print(text[:1500])
    else:
        raise Exception('empty response')
except Exception as e:
    print('')
" 2>/dev/null)

  if [ -z "$answer" ]; then
    log "Deep agent returned empty or error. Raw response: ${response:0:200}"
    echo "I wasn't able to process that right now. The backend may be busy — try again in a moment."
    return
  fi

  echo "$answer"
}

query_strategy_brief() {
  local question="$1"

  # Strip bot mention
  local clean_q
  clean_q=$(echo "$question" | sed "s/<@${BOT_USER_ID}>//g" | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*$//')
  [ -z "$clean_q" ] && clean_q="Where are we given our latest codebase changes? Tie it to the report."

  log "Querying strategy-brief agent: $clean_q"

  local payload
  payload=$(echo "$clean_q" | python3 -c "
import sys, json
q = sys.stdin.read().strip()
print(json.dumps({'question': q, 'max_turns': 200}))
" 2>/dev/null)

  if [ -z "$payload" ]; then
    log "Failed to build strategy-brief JSON payload"
    echo "I had trouble processing that question."
    return
  fi

  local response
  response=$(curl -s --max-time 600 -X POST "$BACKEND_URL/api/agents/strategy-brief" \
    -H "Content-Type: application/json" \
    -d "$payload" 2>/dev/null) || response=""

  local answer
  answer=$(echo "$response" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if data.get('error'):
        raise Exception(data['error'])
    parts = []
    text = data.get('text', '').strip()
    if text:
        parts.append(text[:3000])

    # Evidence section
    evidence = data.get('evidence', [])
    if evidence:
        parts.append('')
        parts.append('───────────────────')
        parts.append('*:mag: Evidence & Traceability:*')
        for e in evidence[:15]:
            label = e.get('label', '')
            value = e.get('value', '')
            status = e.get('status', '')
            section = e.get('sectionId', '')
            dot = ':white_check_mark:' if status == 'shipped' else ':large_blue_circle:' if status == 'in_progress' else ':white_circle:'
            line = f'  {dot} *{label}*: {value}'
            if section:
                line += f'  _→ {section}_'
            parts.append(line)

    # Telemetry footer
    turns = data.get('turns', 0)
    tool_calls = data.get('tool_calls', [])
    duration = data.get('duration_ms', 0)
    confidence = data.get('confidence', '')
    strategy = data.get('strategy', {})
    strategy_name = strategy.get('strategy', '') if isinstance(strategy, dict) else ''
    skill = strategy.get('skill', '') if isinstance(strategy, dict) else ''

    meta = []
    if confidence:
        meta.append(f'Confidence: *{confidence}*')
    if strategy_name:
        meta.append(f'Strategy: {strategy_name}')
    if skill:
        meta.append(f'Skill: {skill}')
    if turns:
        meta.append(f'Turns: {turns}')
    if tool_calls:
        meta.append(f'Tool calls: {len(tool_calls)}')
    if duration:
        meta.append(f'Duration: {duration/1000:.1f}s')
    estimated_cost = data.get('estimated_cost_usd', 0)
    if estimated_cost:
        meta.append(f'Cost: ${estimated_cost:,.4f}')

    if meta:
        parts.append('')
        parts.append('───────────────────')
        parts.append('_' + ' · '.join(meta) + '_')

    print('\n'.join(parts) if parts else '')
except Exception as e:
    print('')
" 2>/dev/null)

  if [ -z "$answer" ]; then
    log "Strategy-brief agent returned empty. Raw: ${response:0:200}"
    echo "I wasn't able to process that right now. The backend may be busy — try again in a moment."
    return
  fi

  echo "$answer"
}

post_thread_reply() {
  local channel="$1"
  local thread_ts="$2"
  local text="$3"

  local escaped_text
  escaped_text=$(echo "$text" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))")

  slack_api "chat.postMessage" \
    -d "{\"channel\":\"$channel\",\"thread_ts\":\"$thread_ts\",\"text\":$escaped_text}" > /dev/null
}

# Post initial message and return its ts for later updates
# ─── Streaming Agent Query (via Python) ─────────────────────────────────

query_agent_streaming() {
  local agent_name="$1"
  local question="$2"
  local channel="$3"
  local thread_ts="$4"
  local source_ts="$5"
  local include_context="${6:-false}"

  # Strip bot mention
  local clean_q
  clean_q=$(echo "$question" | sed "s/<@${BOT_USER_ID}>//g" | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*$//')
  [ -z "$clean_q" ] && clean_q="What's the current status of retention.sh?"

  log "Streaming $agent_name agent via Python: $clean_q (context=$include_context)"

  local context_flag=""
  if [ "$include_context" = "true" ]; then
    context_flag="--include-context"
  fi

  python3 "$SCRIPT_DIR/stream-agent-to-slack.py" \
    --backend-url "$BACKEND_URL" \
    --agent "$agent_name" \
    --question "$clean_q" \
    --channel "$channel" \
    --thread-ts "$thread_ts" \
    --source-ts "$source_ts" \
    --slack-token "$SLACK_BOT_TOKEN" \
    $context_flag 2>>"$LOG_FILE"

  local exit_code=$?
  if [ $exit_code -ne 0 ]; then
    log "stream-agent-to-slack.py exited with code $exit_code"
  fi
}

post_initial_reply() {
  local channel="$1"
  local thread_ts="$2"
  local text="$3"

  local escaped_text
  escaped_text=$(echo "$text" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))")

  local result
  result=$(slack_api "chat.postMessage" \
    -d "{\"channel\":\"$channel\",\"thread_ts\":\"$thread_ts\",\"text\":$escaped_text}")

  echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ts',''))" 2>/dev/null
}

# Update an existing message in place
update_slack_message() {
  local channel="$1"
  local msg_ts="$2"
  local text="$3"

  local escaped_text
  escaped_text=$(echo "$text" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))")

  slack_api "chat.update" \
    -d "{\"channel\":\"$channel\",\"ts\":\"$msg_ts\",\"text\":$escaped_text}" > /dev/null
}

# ─── Streaming Agent Query ──────────────────────────────────────────────

# ─── Main Poll ────────────────────────────────────────────────────────────

poll_channel() {
  local channel="$1"
  local oldest
  oldest=$(get_last_ts "$channel")

  local result
  result=$(curl -s -G "https://slack.com/api/conversations.history" \
    -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
    -d "channel=$channel" \
    -d "oldest=$oldest" \
    -d "limit=5" 2>/dev/null)

  local ok
  ok=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok', False))" 2>/dev/null)

  if [ "$ok" != "True" ]; then
    local err
    err=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error', 'unknown'))" 2>/dev/null)
    log "API error for $channel: $err"
    return
  fi

  # ── Process top-level messages ──
  local thread_parents=""
  echo "$result" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in reversed(data.get('messages', [])):
    ts = m.get('ts', '')
    user = m.get('user', '')
    text = m.get('text', '')
    reply_count = m.get('reply_count', 0)
    if m.get('subtype'):
        continue
    # Extract file URLs from attachments
    file_urls = []
    for f in m.get('files', []):
        url = f.get('url_private_download') or f.get('url_private', '')
        if url:
            file_urls.append(f'{f.get(\"name\",\"file\")}:::{url}')
    files_str = '|||'.join(file_urls) if file_urls else ''
    print(f'{ts}|{user}|{text}|{reply_count}|{files_str}')
" 2>/dev/null | while IFS='|' read -r msg_ts msg_user msg_text msg_replies msg_files_raw; do
    [ -z "$msg_ts" ] && continue

    # Always update the timestamp so we don't re-process
    set_last_ts "$channel" "$msg_ts"

    # Track threads with replies for follow-up checking
    if [ "${msg_replies:-0}" -gt 0 ]; then
      # Save thread_ts for thread polling below
      echo "$msg_ts" >> "$STATE_DIR/active-threads-$channel"
    fi

    if is_actionable_message "$msg_text" "$msg_user"; then
      if ! check_command_word "$msg_text"; then
        continue
      fi

      # ── Request dedup ──
      # Prevent the same message from triggering multiple agent runs,
      # even if two observer processes briefly overlap.
      local dedup_key="top_${channel}_${msg_ts}"
      if ! mark_event_once "$dedup_key"; then
        log "Skipping duplicate top-level message: $msg_ts already processed"
        continue
      fi

      if ! check_rate_limit; then
        log "Skipping reply (rate limited)"
        continue
      fi

      log "Actionable: [$msg_user] $msg_text"

      # ── YouTube URL enrichment ──
      # If the message contains a YouTube link, pre-fetch the transcript
      # and include it in the query context so the agent can analyze the actual content.
      enriched_text="$msg_text"
      yt_url=$(echo "$msg_text" | grep -oE 'https?://(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[^ |>]+' | head -1)
      if [ -n "$yt_url" ]; then
        log "YouTube URL detected: $yt_url — fetching transcript"
        yt_transcript=$(python3 -c "
import sys
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    import re
    url = '$yt_url'
    match = re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
    if not match:
        sys.exit(0)
    vid_id = match.group(1)
    api = YouTubeTranscriptApi()
    transcript = api.fetch(vid_id)
    segments = list(transcript)
    full = ' '.join(s.text for s in segments)
    dur = segments[-1].start / 60 if segments else 0
    # Truncate to ~12K chars to fit in context
    if len(full) > 12000:
        full = full[:12000] + '... [truncated]'
    print(f'[VIDEO TRANSCRIPT ({dur:.0f} min): {full}]')
except Exception as e:
    print(f'[Could not fetch transcript: {e}]', file=sys.stderr)
" 2>/dev/null)
        if [ -n "$yt_transcript" ]; then
          log "Transcript fetched: $(echo "$yt_transcript" | wc -c | tr -d ' ') chars"
          enriched_text="$msg_text

$yt_transcript

Analyze the video transcript above. Cite specific claims with timestamps."
        fi
      fi

      # ── File attachment enrichment ──
      # If the message has attached files, download them to /tmp and add to context
      local file_context=""
      if [ -n "$msg_files_raw" ]; then
        log "File attachments detected in message"
        file_context=$(echo "$msg_files_raw" | python3 -c "
import sys, urllib.request, os, tempfile
raw = sys.stdin.read().strip()
if not raw:
    sys.exit(0)
parts = raw.split('|||')
results = []
token = os.environ.get('SLACK_BOT_TOKEN','')
for part in parts:
    if ':::' not in part:
        continue
    name, url = part.split(':::', 1)
    try:
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
        ext = os.path.splitext(name)[1] or '.bin'
        dest = os.path.join(tempfile.gettempdir(), f'slack_file_{name}')
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        with open(dest, 'wb') as f:
            f.write(data)
        size_kb = len(data) // 1024
        results.append(f'[ATTACHED FILE: {name} ({size_kb}KB) saved to {dest}]')
        # For text/code/md files, include content inline
        if ext in ('.txt','.md','.py','.js','.ts','.json','.csv','.log','.sh'):
            content = data.decode('utf-8', errors='replace')[:8000]
            results.append(f'[FILE CONTENT ({name}):\\n{content}]')
        elif ext in ('.mp4','.mov','.webm','.avi'):
            results.append(f'[VIDEO FILE: {name} — extract frames with: ffmpeg -i {dest} -vf fps=1 /tmp/frame_%03d.png]')
        elif ext in ('.png','.jpg','.jpeg','.gif','.webp'):
            results.append(f'[IMAGE FILE: {name} at {dest} — analyze with vision model]')
    except Exception as e:
        results.append(f'[Failed to download {name}: {e}]')
print('\\n'.join(results))
" 2>/dev/null)
        if [ -n "$file_context" ]; then
          log "File context: $(echo "$file_context" | wc -l | tr -d ' ') items"
          enriched_text="$enriched_text

$file_context

The user attached the above file(s) to their message. Process them as requested."
        fi
      fi

      # ── LLM-based intent classification (replaces regex routing) ──
      # Calls /api/slack/classify with the user's original message.
      # The LLM (gpt-5.4-nano) classifies intent in ~50ms, ~$0.001/call.
      # Falls back to "direct" (strategy-brief) if classification fails.
      local has_yt="false"
      [ -n "$yt_url" ] && has_yt="true"
      local has_code="false"
      echo "$msg_text" | grep -q '```' && has_code="true"

      local clean_msg
      clean_msg=$(echo "$msg_text" | sed "s/<@${BOT_USER_ID}>//g" | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*$//')
      local encoded_msg
      encoded_msg=$(python3 -c "import urllib.parse; print(urllib.parse.quote('''$clean_msg'''[:500]))")

      local classify_resp
      classify_resp=$(curl -s -X POST "$BACKEND_URL/api/slack/classify?message=$encoded_msg&has_youtube_url=$has_yt&has_code_block=$has_code" \
        -H "Authorization: Bearer ${CRON_AUTH_TOKEN:-}" \
        --max-time 10 2>/dev/null)

      local intent
      intent=$(echo "$classify_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('intent','direct'))" 2>/dev/null)
      [ -z "$intent" ] && intent="direct"
      local confidence
      confidence=$(echo "$classify_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('confidence',0.5))" 2>/dev/null)

      log "Intent classified: $intent (confidence=$confidence) for: $clean_msg"

      case "$intent" in
        deep_sim)
          log "Deep Sim triggered via intent classifier"
          local deep_topic
          deep_topic=$(echo "$clean_msg" | sed -E 's/(deep sim|simulate this|swarm on|all roles)//gi' | xargs)
          [ -z "$deep_topic" ] && deep_topic="$clean_msg"
          # Also include image/file context if present
          local sim_context="$deep_topic"
          if [ -n "$enriched_text" ] && [ "$enriched_text" != "$clean_msg" ]; then
            sim_context="$enriched_text"
          fi
          local encoded_topic encoded_channel encoded_ts
          encoded_topic=$(python3 -c "import urllib.parse; print(urllib.parse.quote('''$sim_context'''))")
          encoded_channel=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$channel'))")
          encoded_ts=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$msg_ts'))")
          curl -s -X POST "$BACKEND_URL/api/slack/swarm/deep-sim?topic=$encoded_topic&channel=$encoded_channel&thread_ts=$encoded_ts&max_rounds=2" \
            -H "Authorization: Bearer ${CRON_AUTH_TOKEN:-}" \
            --max-time 5 > /dev/null 2>&1 &
          log "Deep Sim fired for: $deep_topic (thread=$msg_ts)"
          ;;
        retention_install)
          # ── Retention.sh installer — privileged shell command ──────────────
          # Runs: RETENTION_EMAIL=... curl -sL https://retention.sh/install.sh | bash
          # Posts acknowledgment immediately, then posts captured output when done.
          log "retention_install intent — running retention.sh installer"
          local ack_ts
          ack_ts=$(post_initial_reply "$channel" "$msg_ts" \
            ":arrows_counterclockwise: Running \`retention.sh\` installer… (this takes ~30s)")

          local install_out install_exit
          install_out=$(RETENTION_EMAIL="${RETENTION_EMAIL:-homen@retention.com}" \
            bash -c 'curl -sL https://retention.sh/install.sh | bash' 2>&1) \
            && install_exit=0 || install_exit=$?

          # Truncate to 2500 chars to stay within Slack message limits
          local truncated_out
          truncated_out=$(echo "$install_out" | tail -c 2500)

          local status_emoji=":white_check_mark:"
          local status_line="Retention installer finished successfully."
          if [ "$install_exit" -ne 0 ]; then
            status_emoji=":x:"
            status_line="Retention installer exited with code $install_exit."
          fi

          # Verify: check if retention appears in .mcp.json
          local mcp_json="$PROJECT_DIR/.mcp.json"
          local verify_line=""
          if [ -f "$mcp_json" ] && grep -q "retention" "$mcp_json" 2>/dev/null; then
            verify_line=$'\n':white_check_mark:' `retention` found in `.mcp.json` — MCP wired.'
          elif [ "$install_exit" -eq 0 ]; then
            verify_line=$'\n':warning:' Could not confirm `retention` in `.mcp.json` — check manually.'
          fi

          local reply_text
          reply_text="$status_emoji $status_line$verify_line"$'\n\n'"Output:\`\`\`\n${truncated_out}\n\`\`\`"

          if [ -n "$ack_ts" ]; then
            update_slack_message "$channel" "$ack_ts" "$reply_text"
          else
            post_initial_reply "$channel" "$msg_ts" "$reply_text"
          fi
          log "retention_install done (exit=$install_exit)"
          ;;
        retention_install_clean)
          # ── Clean-room installer test — isolated HOME + CWD ───────────────
          # Simulates a brand-new user with no prior retention.sh state.
          # Uses a temp HOME and temp CWD so no existing .mcp.json taints results.
          log "retention_install_clean intent — running clean-room test"
          local ack_ts
          ack_ts=$(post_initial_reply "$channel" "$msg_ts" \
            ":test_tube: Running clean-room installer test… (isolated HOME + CWD, ~30s)")

          local clean_home clean_workdir
          clean_home=$(mktemp -d)
          clean_workdir=$(mktemp -d)

          local clean_out clean_exit
          clean_out=$(
            cd "$clean_workdir"
            HOME="$clean_home" RETENTION_EMAIL="${RETENTION_EMAIL:-homen@retention.com}" \
              bash -c 'curl -sL https://retention.sh/install.sh | bash' 2>&1
          ) && clean_exit=0 || clean_exit=$?

          # Collect artifacts
          local mcp_contents=""
          if [ -f "$clean_workdir/.mcp.json" ]; then
            mcp_contents=$(cat "$clean_workdir/.mcp.json")
          fi
          local proxy_status="missing"
          [ -f "$clean_home/.retention/proxy.py" ] && proxy_status="present ($(wc -l < "$clean_home/.retention/proxy.py") lines)"

          # Cleanup temp dirs
          rm -rf "$clean_home" "$clean_workdir"

          local truncated_out
          truncated_out=$(echo "$clean_out" | tail -c 2000)

          local status_emoji=":white_check_mark:"
          local status_line="Clean-room install *passed*."
          if [ "$clean_exit" -ne 0 ]; then
            status_emoji=":x:"
            status_line="Clean-room install *failed* (exit=$clean_exit)."
          elif [ -z "$mcp_contents" ]; then
            status_emoji=":warning:"
            status_line="Install exited 0 but .mcp.json was not written."
          fi

          local reply_text
          reply_text="$status_emoji $status_line
• proxy.py: \`$proxy_status\`
• .mcp.json: \`$([ -n "$mcp_contents" ] && echo "written" || echo "missing")\`

Output:\`\`\`
${truncated_out}
\`\`\`"

          if [ -n "$ack_ts" ]; then
            update_slack_message "$channel" "$ack_ts" "$reply_text"
          else
            post_initial_reply "$channel" "$msg_ts" "$reply_text"
          fi
          log "retention_install_clean done (exit=$clean_exit)"
          ;;
        *)
          # All other intents (direct, transcribe, code_review, build, status)
          # route to the strategy-brief agent which self-selects tools
          log "Routing to strategy-brief agent (intent=$intent, streaming)"
          query_agent_streaming "strategy-brief" "$enriched_text" "$channel" "$msg_ts" "$msg_ts"
          log "Replied in thread $msg_ts"
          ;;
      esac
    fi
  done

  # ── Check threads for follow-up replies ──
  poll_threads "$channel"
}

poll_threads() {
  local channel="$1"
  local threads_file="$STATE_DIR/active-threads-$channel"

  # Also check for any threads the bot has participated in recently
  # by looking at conversations.history for messages with reply_count > 0
  # that contain bot replies
  local recent_threads
  recent_threads=$(curl -s -G "https://slack.com/api/conversations.history" \
    -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
    -d "channel=$channel" \
    -d "limit=10" 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
if not data.get('ok'):
    sys.exit()
for m in data.get('messages', []):
    if m.get('reply_count', 0) > 0:
        # Only check threads that have @bot mention in the parent
        text = m.get('text', '')
        if '<@U0ALSPANA1G>' in text:
            print(m['ts'])
" 2>/dev/null)

  [ -z "$recent_threads" ] && return

  for thread_ts in $recent_threads; do
    local thread_last_ts_file="${LAST_TS_PREFIX}_thread_${thread_ts}"
    local thread_oldest=""
    if [ -f "$thread_last_ts_file" ]; then
      thread_oldest=$(cat "$thread_last_ts_file")
    else
      # First time seeing this thread — set to parent ts so we don't replay old messages
      thread_oldest="$thread_ts"
      echo "$thread_oldest" > "$thread_last_ts_file"
      continue
    fi

    # Get new replies in this thread
    local thread_result
    thread_result=$(curl -s -G "https://slack.com/api/conversations.replies" \
      -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
      -d "channel=$channel" \
      -d "ts=$thread_ts" \
      -d "oldest=$thread_oldest" \
      -d "limit=5" 2>/dev/null)

    local thread_ok
    thread_ok=$(echo "$thread_result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok', False))" 2>/dev/null)
    [ "$thread_ok" != "True" ] && continue

    echo "$thread_result" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('messages', []):
    ts = m.get('ts', '')
    user = m.get('user', '')
    text = m.get('text', '')
    bot_id = m.get('bot_id', '')
    if m.get('subtype'):
        continue
    if ts == '$thread_ts':
        continue
    # Extract file URLs from thread replies too
    file_urls = []
    for f in m.get('files', []):
        url = f.get('url_private_download') or f.get('url_private', '')
        if url:
            file_urls.append(f'{f.get(\"name\",\"file\")}:::{url}')
    files_str = '|||'.join(file_urls) if file_urls else ''
    print(f'{ts}|{user}|{bot_id}|{text}|{files_str}')
" 2>/dev/null | while IFS='|' read -r reply_ts reply_user reply_bot_id reply_text reply_files_raw; do
      [ -z "$reply_ts" ] && continue
      # Validate reply_ts looks like a Slack timestamp (digits.digits)
      if ! echo "$reply_ts" | grep -qE '^[0-9]+\.[0-9]+$'; then
        continue
      fi
      echo "$reply_ts" > "$thread_last_ts_file"

      # In threads, respond to any non-bot message (they're already in context)
      # Guard: skip if reply_user is empty (no user field — likely a bot/system message),
      # or if reply is from the bot itself (by user ID or bot_id field)
      if [ -z "$reply_user" ] || [ "$reply_user" = "$BOT_USER_ID" ] || [ -n "$reply_bot_id" ]; then
        continue
      fi
      if [ -n "$reply_text" ]; then
        if ! check_command_word "$reply_text"; then
          continue
        fi

        local reply_dedup_key="thread_${channel}_${thread_ts}_${reply_ts}"
        if ! mark_event_once "$reply_dedup_key"; then
          log "Skipping duplicate thread follow-up: $reply_ts already processed"
          continue
        fi

        if ! check_rate_limit; then
          log "Skipping thread reply (rate limited)"
          continue
        fi

        log "Thread follow-up: [$reply_user] $reply_text (thread $thread_ts)"

        # ── Context enrichment for short follow-ups ──
        # If the reply is short (< 30 chars) and doesn't contain a URL,
        # check the PARENT message for URLs/content to carry forward.
        enriched_reply="$reply_text"
        if [ ${#reply_text} -lt 30 ]; then
          # Fetch parent message text for context
          local parent_text
          parent_text=$(curl -s -G "https://slack.com/api/conversations.replies" \
            -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
            -d "channel=$channel" \
            -d "ts=$thread_ts" \
            -d "limit=1" 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
msgs = data.get('messages', [])
if msgs:
    print(msgs[0].get('text', ''))
" 2>/dev/null)

          # Check if parent has a YouTube URL the user might be referring to
          local parent_yt_url
          parent_yt_url=$(echo "$parent_text" | grep -oE 'https?://(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[^ |>]+' | head -1)
          if [ -n "$parent_yt_url" ] && ! echo "$reply_text" | grep -q 'http'; then
            log "Short follow-up — carrying YouTube URL from parent: $parent_yt_url"
            enriched_reply="$reply_text (referring to: $parent_yt_url from the parent message: $parent_text)"
          fi
        fi

        # ── YouTube URL enrichment (thread replies) ──
        yt_reply_url=$(echo "$enriched_reply" | grep -oE 'https?://(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[^ |>]+' | head -1)
        if [ -n "$yt_reply_url" ]; then
          log "YouTube URL in thread reply: $yt_reply_url"
          yt_reply_transcript=$(python3 -c "
import sys
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    import re
    url = '$yt_reply_url'
    match = re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
    if not match: sys.exit(0)
    api = YouTubeTranscriptApi()
    transcript = api.fetch(match.group(1))
    segments = list(transcript)
    full = ' '.join(s.text for s in segments)
    dur = segments[-1].start / 60 if segments else 0
    if len(full) > 12000: full = full[:12000] + '... [truncated]'
    print(f'[VIDEO TRANSCRIPT ({dur:.0f} min): {full}]')
except: pass
" 2>/dev/null)
          if [ -n "$yt_reply_transcript" ]; then
            enriched_reply="$reply_text

$yt_reply_transcript

Analyze the video transcript above. Cite specific claims with timestamps."
          fi
        fi

        # ── File attachment enrichment for thread replies ──
        if [ -n "$reply_files_raw" ]; then
          log "File attachments in thread reply"
          local reply_file_context
          reply_file_context=$(echo "$reply_files_raw" | python3 -c "
import sys, urllib.request, os, tempfile
raw = sys.stdin.read().strip()
if not raw: sys.exit(0)
token = os.environ.get('SLACK_BOT_TOKEN','')
for part in raw.split('|||'):
    if ':::' not in part: continue
    name, url = part.split(':::', 1)
    try:
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
        dest = os.path.join(tempfile.gettempdir(), f'slack_file_{name}')
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        with open(dest, 'wb') as f: f.write(data)
        ext = os.path.splitext(name)[1]
        print(f'[ATTACHED FILE: {name} ({len(data)//1024}KB) saved to {dest}]')
        if ext in ('.txt','.md','.py','.js','.ts','.json','.csv','.log','.sh'):
            print(f'[FILE CONTENT ({name}):\n{data.decode(\"utf-8\",errors=\"replace\")[:8000]}]')
        elif ext in ('.mp4','.mov','.webm','.avi'):
            print(f'[VIDEO FILE: {name} at {dest} — extract frames with: ffmpeg -i {dest} -vf fps=1 /tmp/frame_%03d.png]')
        elif ext in ('.png','.jpg','.jpeg','.gif','.webp'):
            print(f'[IMAGE FILE: {name} at {dest}]')
    except Exception as e:
        print(f'[Failed to download {name}: {e}]')
" 2>/dev/null)
          if [ -n "$reply_file_context" ]; then
            enriched_reply="$enriched_reply

$reply_file_context

The user attached the above file(s). Process them as requested."
          fi
        fi

        query_agent_streaming "strategy-brief" "$enriched_reply" "$channel" "$thread_ts" "$reply_ts" "true"
        log "Replied to thread follow-up $thread_ts"
      fi
    done
  done

  # Cleanup old thread tracking files (older than 24 hours)
  find "$STATE_DIR" -name "${LAST_TS_PREFIX##*/}_thread_*" -mmin +1440 -delete 2>/dev/null || true
}

# ─── Entry Point ──────────────────────────────────────────────────────────

# Verify token works
TOKEN_OK=$(curl -s "https://slack.com/api/auth.test" \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok', False))" 2>/dev/null)

if [ "$TOKEN_OK" != "True" ]; then
  log "ERROR: Slack bot token is invalid or inactive. Observer cannot start."
  echo "ERROR: Slack bot token is invalid. Check SLACK_BOT_TOKEN." >&2
  # Sleep and retry rather than exit (LaunchAgent will keep restarting)
  sleep 300
  exit 1
fi

discover_channel

if [ -z "$OBSERVE_CHANNELS" ]; then
  log "No channels to observe. Exiting."
  sleep 300
  exit 1
fi

acquire_lock

log "Observer starting. Channel: $OBSERVE_CHANNELS, poll interval: ${POLL_INTERVAL}s"
log "Respond to bare questions: $RESPOND_TO_QUESTIONS (only @mentions by default)"

if [ "${1:-}" = "--once" ]; then
  for ch in $OBSERVE_CHANNELS; do
    poll_channel "$ch"
  done
  exit 0
fi

while true; do
  # Sync command-word state from Convex (so observer respects "only respond to Claw")
  sync_command_word 2>/dev/null || true

  for ch in $OBSERVE_CHANNELS; do
    # Wrap in subshell so set -e failures in poll don't kill the main loop
    ( poll_channel "$ch" ) || log "poll_channel $ch failed (exit $?), continuing..."
  done
  sleep "$POLL_INTERVAL"
done
