#!/bin/bash
# web-intel-digest.sh — Automated competitive intelligence + tech updates via OpenClaw
#
# Uses OpenClaw agent (GPT-5.1-Codex with web access) to research competitor
# updates, relevant tech news, and synthesize a digest for Slack.
#
# Designed to run as a weekly OpenClaw cron or Claude Code scheduled task.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/.claude/logs"
DATA_DIR="$PROJECT_DIR/backend/data/intel"
LOG_FILE="$LOG_DIR/web-intel.log"

mkdir -p "$LOG_DIR" "$DATA_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }

notify_slack() {
  if [ -f "$SCRIPT_DIR/notify-slack.sh" ]; then
    bash "$SCRIPT_DIR/notify-slack.sh" "$1" 2>/dev/null || true
  fi
}

log "Starting web intelligence digest..."

# ─── Research Prompt ──────────────────────────────────────────────────────

RESEARCH_PROMPT='You are a competitive intelligence analyst for retention.sh (retention.sh), an AI-powered mobile testing platform.

Research the following and provide a CONCISE digest (max 500 words):

1. **Competitor Updates** (last 7 days):
   - BrowserStack, Sauce Labs, LambdaTest, Kobiton, Perfecto — any new features, pricing changes, or AI testing announcements?
   - Any new entrants in AI-powered mobile testing?

2. **Tech Stack Updates** (last 7 days):
   - Appium releases or breaking changes
   - Android emulator updates
   - OpenAI API changes affecting agents
   - Convex database updates

3. **Market Signals**:
   - Any funding rounds in mobile testing / QA automation space?
   - Enterprise demand signals for AI testing tools?

Format your response as a Slack-friendly message with sections using *bold* for headers and bullet points.
End with a "Key Takeaway" that is actionable for retention.sh strategy.'

# ─── Run via OpenClaw Agent ───────────────────────────────────────────────

DIGEST=""

# Try OpenClaw first (has web access via GPT-5.1-Codex)
if command -v openclaw &>/dev/null; then
  log "Using OpenClaw agent for research..."
  DIGEST=$(openclaw agent --message "$RESEARCH_PROMPT" --timeout 60 2>/dev/null || echo "")
fi

# Fallback: use Claude Code
if [ -z "$DIGEST" ] || [ ${#DIGEST} -lt 50 ]; then
  log "OpenClaw failed or returned empty. Trying Claude Code..."
  DIGEST=$(echo "$RESEARCH_PROMPT" | npx @anthropic-ai/claude-code --print 2>/dev/null || echo "")
fi

if [ -z "$DIGEST" ] || [ ${#DIGEST} -lt 50 ]; then
  log "Both agents failed to produce digest"
  notify_slack ":warning: Web intel digest failed — both OpenClaw and Claude Code returned empty results."
  exit 1
fi

# ─── Save & Notify ───────────────────────────────────────────────────────

TIMESTAMP=$(date '+%Y-%m-%d')
DIGEST_FILE="$DATA_DIR/digest_${TIMESTAMP}.md"
echo "$DIGEST" > "$DIGEST_FILE"
log "Saved digest to $DIGEST_FILE"

# Post to Slack (truncate if too long)
SLACK_MSG=":newspaper: *Weekly Intelligence Digest — $TIMESTAMP*\n\n$(echo "$DIGEST" | head -c 2500)"
notify_slack "$SLACK_MSG"

# ─── Sync to Convex (best-effort) ────────────────────────────────────────

CONVEX_URL="${CONVEX_URL:-$(grep VITE_CONVEX_URL "$PROJECT_DIR/frontend/test-studio/.env.local" 2>/dev/null | cut -d= -f2 || echo "")}"

if [ -n "$CONVEX_URL" ]; then
  log "Syncing digest to Convex..."
  python3 -c "
import json, time, sys
try:
    import httpx
    digest = open('$DIGEST_FILE').read()
    payload = {
        'path': 'competitiveIntel:addEntry',
        'args': {
            'source': 'automated-digest',
            'title': 'Weekly Intelligence Digest $TIMESTAMP',
            'content': digest[:4000],
            'category': 'digest',
            'createdAt': int(time.time() * 1000),
        }
    }
    resp = httpx.post('$CONVEX_URL/api/mutation', json=payload, timeout=10)
    print(f'Convex sync: {resp.status_code}')
except Exception as e:
    print(f'Convex sync failed (best-effort): {e}', file=sys.stderr)
" 2>>"$LOG_FILE" || true
fi

log "Web intelligence digest complete."
