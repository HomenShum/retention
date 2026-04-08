#!/bin/bash
# full-stack-health.sh — Comprehensive health check with auto-recovery
#
# Checks: backend, frontend, emulator, WebSocket relay, Slack observer, OpenClaw
# Auto-recovers: restarts crashed services before alerting
# Only notifies Slack if auto-recovery also fails.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
STATE_DIR="$PROJECT_DIR/.claude"
LOG_DIR="$STATE_DIR/logs"
LOG_FILE="$LOG_DIR/full-health.log"

mkdir -p "$LOG_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }

ISSUES=()
RECOVERIES=()
HEALTHY=()

check() {
  local name="$1"
  local url="$2"
  local timeout="${3:-5}"

  local status
  status=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$timeout" "$url" 2>/dev/null || echo "000")

  if [ "$status" = "200" ] || [ "$status" = "307" ] || [ "$status" = "301" ]; then
    HEALTHY+=("$name")
    return 0
  else
    return 1
  fi
}

# ─── 1. Backend API ──────────────────────────────────────────────────────

if ! check "Backend API" "http://localhost:8000/api/health"; then
  log "Backend down. Attempting recovery..."
  # Try to restart backend
  cd "$PROJECT_DIR/backend"
  if [ -f ".venv/bin/python" ]; then
    pkill -f "uvicorn app.main:app" 2>/dev/null || true
    sleep 2
    nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >> "$LOG_DIR/backend-recovery.log" 2>&1 &
    sleep 5
    if check "Backend API (recovered)" "http://localhost:8000/api/health"; then
      RECOVERIES+=("Backend API (auto-restarted)")
    else
      ISSUES+=("Backend API: DOWN (recovery failed)")
    fi
  else
    ISSUES+=("Backend API: DOWN (no venv found)")
  fi
fi

# ─── 2. Frontend Dev Server ──────────────────────────────────────────────

if ! check "Frontend" "http://localhost:5173" 3; then
  # Frontend might not be running in production — just log, don't recover
  log "Frontend dev server not running (may be deployed to Vercel)"
  # Not an issue if deployed
fi

# ─── 3. Android Emulator ─────────────────────────────────────────────────

ADB_PATH="${ANDROID_HOME:-$HOME/Library/Android/sdk}/platform-tools/adb"
if [ -x "$ADB_PATH" ]; then
  EMULATOR_STATUS=$("$ADB_PATH" devices 2>/dev/null | grep -c "device$" || echo "0")
  if [ "$EMULATOR_STATUS" -gt 0 ]; then
    HEALTHY+=("Emulator ($EMULATOR_STATUS device(s))")
  else
    log "Emulator not connected. Attempting recovery..."
    EMULATOR_BIN="${ANDROID_HOME:-$HOME/Library/Android/sdk}/emulator/emulator"
    if [ -x "$EMULATOR_BIN" ]; then
      # Try to launch the default AVD
      AVD_NAME=$("$EMULATOR_BIN" -list-avds 2>/dev/null | head -1)
      if [ -n "$AVD_NAME" ]; then
        nohup "$EMULATOR_BIN" -avd "$AVD_NAME" -no-snapshot -gpu swiftshader_indirect -no-boot-anim -port 5554 >> "$LOG_DIR/emulator-recovery.log" 2>&1 &
        sleep 15
        EMULATOR_STATUS=$("$ADB_PATH" devices 2>/dev/null | grep -c "device$" || echo "0")
        if [ "$EMULATOR_STATUS" -gt 0 ]; then
          RECOVERIES+=("Emulator (auto-launched $AVD_NAME)")
        else
          ISSUES+=("Emulator: no devices (recovery pending, may need more boot time)")
        fi
      else
        ISSUES+=("Emulator: no AVDs available")
      fi
    else
      ISSUES+=("Emulator: emulator binary not found")
    fi
  fi
else
  ISSUES+=("ADB: not found at $ADB_PATH")
fi

# ─── 4. WebSocket Relay Status ────────────────────────────────────────────

if check "Relay Status API" "http://localhost:8000/api/relay/status" 3; then
  HEALTHY+=("Agent Relay endpoint available")
else
  log "Relay status API not responding (backend may be down)"
fi

# ─── 5. Slack Observer ───────────────────────────────────────────────────

if pgrep -f "slack-channel-observer" > /dev/null 2>&1; then
  HEALTHY+=("Slack Observer")
else
  log "Slack observer not running. Restarting..."
  nohup bash "$SCRIPT_DIR/slack-channel-observer.sh" >> "$LOG_DIR/slack-observer-recovery.log" 2>&1 &
  sleep 2
  if pgrep -f "slack-channel-observer" > /dev/null 2>&1; then
    RECOVERIES+=("Slack Observer (auto-restarted)")
  else
    ISSUES+=("Slack Observer: failed to restart")
  fi
fi

# ─── 6. OpenClaw Gateway ─────────────────────────────────────────────────

if check "OpenClaw Gateway" "http://localhost:18789" 3; then
  HEALTHY+=("OpenClaw Gateway")
else
  ISSUES+=("OpenClaw Gateway: not responding on port 18789")
fi

# ─── 7. ActionSpan Endpoint ──────────────────────────────────────────────

if check "ActionSpan API" "http://localhost:8000/api/action-spans/" 3; then
  HEALTHY+=("ActionSpan API")
fi

# ─── Report ──────────────────────────────────────────────────────────────

TOTAL_HEALTHY=${#HEALTHY[@]}
TOTAL_ISSUES=${#ISSUES[@]}
TOTAL_RECOVERIES=${#RECOVERIES[@]}

log "Health check complete: $TOTAL_HEALTHY healthy, $TOTAL_RECOVERIES recovered, $TOTAL_ISSUES issues"

# Build report
REPORT=""

if [ $TOTAL_ISSUES -gt 0 ] || [ $TOTAL_RECOVERIES -gt 0 ]; then
  REPORT=":stethoscope: *retention.sh Health Report*\n\n"

  if [ $TOTAL_HEALTHY -gt 0 ]; then
    REPORT+=":white_check_mark: *Healthy:* $(IFS=', '; echo "${HEALTHY[*]}")\n"
  fi

  if [ $TOTAL_RECOVERIES -gt 0 ]; then
    REPORT+=":wrench: *Auto-Recovered:*\n"
    for r in "${RECOVERIES[@]}"; do
      REPORT+="  - $r\n"
    done
  fi

  if [ $TOTAL_ISSUES -gt 0 ]; then
    REPORT+=":x: *Issues (need attention):*\n"
    for i in "${ISSUES[@]}"; do
      REPORT+="  - $i\n"
    done
  fi

  # Only notify Slack if there are real issues or recoveries worth knowing about
  if [ -f "$SCRIPT_DIR/notify-slack.sh" ]; then
    bash "$SCRIPT_DIR/notify-slack.sh" "$REPORT" 2>/dev/null || true
  fi
fi

# Always output for CLI / cron capture
echo "=== retention.sh Health Check ==="
echo "Healthy: ${HEALTHY[*]:-none}"
echo "Recovered: ${RECOVERIES[*]:-none}"
echo "Issues: ${ISSUES[*]:-none}"
