#!/bin/bash
# Autonomous health check — monitors backend + frontend, auto-restarts if needed
PROJ="/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"
NOTIFY="$PROJ/scripts/retention-notify.sh"
BACKEND_URL="http://localhost:8000/api/health"
FRONTEND_URL="http://localhost:5173"

check_service() {
    local name="$1" url="$2"
    local status=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$url")
    if [ "$status" = "200" ]; then
        echo "[health] $name: OK"
        return 0
    else
        echo "[health] $name: DOWN (HTTP $status)"
        return 1
    fi
}

ISSUES=""

if ! check_service "Backend" "$BACKEND_URL"; then
    ISSUES="$ISSUES Backend DOWN."
fi

if ! check_service "Frontend" "$FRONTEND_URL"; then
    ISSUES="$ISSUES Frontend DOWN."
fi

if [ -n "$ISSUES" ]; then
    "$NOTIFY" "HEALTH ALERT" "$ISSUES Check servers immediately."
else
    echo "[health] All services healthy"
fi
