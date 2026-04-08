#!/bin/bash
# Stop the retention.sh autonomous agent daemon
PROJ="/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"
NOTIFY="$PROJ/scripts/retention-notify.sh"

echo "Stopping retention.sh Agent..."

pkill -f "imessage-listener.sh" 2>/dev/null && echo "Stopped iMessage listener" || echo "iMessage listener not running"
pkill -f "autonomous-health" 2>/dev/null && echo "Stopped health monitor" || echo "Health monitor not running"

rm -f "$PROJ/.claude/imessage-listener.pid" "$PROJ/.claude/health-monitor.pid"

"$NOTIFY" "Agent Stopped" "retention.sh autonomous agent has been shut down."
echo "Agent stopped."
