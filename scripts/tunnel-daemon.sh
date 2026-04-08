#!/bin/bash
# relay-daemon.sh — Persistent outbound WebSocket relay to retention.sh server
#
# Replaces the old Cloudflare Tunnel daemon. Instead of exposing a port,
# this connects OUT from the local machine to the retention.sh server via WSS.
# No ports opened, nothing to scan, nothing to attack.
#
# The relay executes ADB commands sent by the server-side TA agent and
# streams emulator frames back over the same connection.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Source .env if available
if [ -f "$PROJECT_DIR/backend/.env" ]; then
  export $(grep -v '^#' "$PROJECT_DIR/backend/.env" | xargs)
elif [ -f "$HOME/.env" ]; then
  source "$HOME/.env"
fi

LOG_DIR="$PROJECT_DIR/.claude/logs"
RELAY_LOG="$LOG_DIR/relay.log"
PID_FILE="$PROJECT_DIR/.claude/relay.pid"

mkdir -p "$LOG_DIR"

# ─── Notification (to #claw-communications) ─────────────────────────
SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN:-}"
CLAW_CHANNEL="${CLAW_CHANNEL:-}"

notify() {
  local msg="$1"
  if [ -n "$SLACK_BOT_TOKEN" ] && [ -n "$CLAW_CHANNEL" ]; then
    local escaped
    escaped=$(echo "$msg" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))" 2>/dev/null || echo "\"$msg\"")
    curl -s -X POST "https://slack.com/api/chat.postMessage" \
      -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"channel\":\"$CLAW_CHANNEL\",\"text\":$escaped}" > /dev/null 2>&1 || true
  fi
}

cleanup() {
  echo "[$(date)] Relay daemon shutting down..." >> "$RELAY_LOG"
  if [ -f "$PID_FILE" ]; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    rm -f "$PID_FILE"
  fi
  exit 0
}

trap cleanup SIGTERM SIGINT

start_relay() {
  echo "[$(date)] Starting outbound WebSocket relay..." >> "$RELAY_LOG"

  # Find Python with the relay package
  PYTHON="${PROJECT_DIR}/backend/.venv/bin/python"
  if [ ! -x "$PYTHON" ]; then
    PYTHON="python3"
  fi

  # Start the thin relay — connects OUT to retention.sh server
  $PYTHON -m retention-mcp >> "$RELAY_LOG" 2>&1 &
  local relay_pid=$!
  echo "$relay_pid" > "$PID_FILE"

  echo "[$(date)] Relay started (PID: $relay_pid)" >> "$RELAY_LOG"
  notify ":white_check_mark: Outbound relay connected to retention.sh server"

  # Wait for relay to exit (blocks here until crash/stop)
  wait "$relay_pid" 2>/dev/null || true
}

# ─── Main Loop ────────────────────────────────────────────────────────────

RESTART_COUNT=0
MAX_RESTARTS=100
BACKOFF=5

while [ $RESTART_COUNT -lt $MAX_RESTARTS ]; do
  # Truncate log if > 1MB
  if [ -f "$RELAY_LOG" ] && [ "$(stat -f%z "$RELAY_LOG" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    tail -200 "$RELAY_LOG" > "$RELAY_LOG.tmp" && mv "$RELAY_LOG.tmp" "$RELAY_LOG"
  fi

  start_relay

  RESTART_COUNT=$((RESTART_COUNT + 1))
  echo "[$(date)] Relay exited. Restart #$RESTART_COUNT in ${BACKOFF}s..." >> "$RELAY_LOG"

  if [ $RESTART_COUNT -eq 11 ]; then
    echo "[$(date)] WARNING: Restart count exceeded 10 — relay is unstable" >> "$RELAY_LOG"
    notify ":warning: *WARNING: Outbound relay unstable* — restart count exceeded 10."
  fi

  # Exponential backoff: 5s, 10s, 20s, 30s max
  sleep "$BACKOFF"
  BACKOFF=$((BACKOFF * 2))
  if [ $BACKOFF -gt 30 ]; then BACKOFF=30; fi
done

echo "[$(date)] Max restarts ($MAX_RESTARTS) exceeded." >> "$RELAY_LOG"
notify ":rotating_light: *CRITICAL: Outbound relay permanently down* — exhausted all restart attempts."
