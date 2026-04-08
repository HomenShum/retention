#!/bin/bash
# Autonomous health check — checks services, reports only on issues
PROJ="/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"
NOTIFY="$PROJ/scripts/notify-slack.sh"

BACKEND=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "http://localhost:8000/api/health" 2>/dev/null)
FRONTEND=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "http://localhost:5173" 2>/dev/null)

ISSUES=""
[ "$BACKEND" != "200" ] && ISSUES="$ISSUES Backend DOWN (HTTP $BACKEND)."
[ "$FRONTEND" != "200" ] && ISSUES="$ISSUES Frontend DOWN (HTTP $FRONTEND)."

if [ -n "$ISSUES" ]; then
    "$NOTIFY" "*[TA Agent — HEALTH ALERT]* 🚨$ISSUES
Check servers: http://localhost:8000/api/health | http://localhost:5173"
    echo "[health] ALERT:$ISSUES"
else
    echo "[health] All OK — backend:$BACKEND frontend:$FRONTEND"
fi
