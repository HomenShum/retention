#!/bin/bash
# Start the full autonomous retention.sh agent daemon
# Launches: iMessage listener + health monitor

PROJ="/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"
LOG_DIR="$PROJ/.claude/logs"
mkdir -p "$LOG_DIR"

echo "=== retention.sh Autonomous Agent ==="
echo "Starting all daemon processes..."

# Kill any existing instances
pkill -f "imessage-listener.sh" 2>/dev/null
pkill -f "autonomous-health-loop.sh" 2>/dev/null

# Start iMessage listener
echo "[daemon] Starting iMessage listener..."
nohup bash "$PROJ/scripts/imessage-listener.sh" > "$LOG_DIR/imessage-listener.log" 2>&1 &
IMSG_PID=$!
echo "[daemon] iMessage listener PID: $IMSG_PID"

# Start health check loop (every 30 min)
echo "[daemon] Starting health monitor..."
nohup bash -c "while true; do bash '$PROJ/scripts/autonomous-health-check.sh' >> '$LOG_DIR/health.log' 2>&1; sleep 1800; done" > /dev/null 2>&1 &
HEALTH_PID=$!
echo "[daemon] Health monitor PID: $HEALTH_PID"

# Save PIDs for management
echo "$IMSG_PID" > "$PROJ/.claude/imessage-listener.pid"
echo "$HEALTH_PID" > "$PROJ/.claude/health-monitor.pid"

# Send startup notification
"$PROJ/scripts/retention-notify.sh" "Agent Started" "retention.sh autonomous agent is now running 24/7. iMessage listener active. Health monitoring active. Text 'help' for commands."

echo ""
echo "=== Agent Running ==="
echo "iMessage listener: PID $IMSG_PID (log: $LOG_DIR/imessage-listener.log)"
echo "Health monitor:    PID $HEALTH_PID (log: $LOG_DIR/health.log)"
echo ""
echo "Stop with: pkill -f imessage-listener.sh; pkill -f autonomous-health"
echo "Or: kill $IMSG_PID $HEALTH_PID"
