#!/bin/bash
# Autonomous test runner — runs backend + frontend tests
PROJ="/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"
NOTIFY="$PROJ/scripts/retention-notify.sh"

cd "$PROJ" || exit 1

RESULTS=""
FAILED=0

# Backend pytest
echo "[tests] Running backend tests..."
cd "$PROJ/backend"
if "$PROJ/backend/.venv/bin/python" -m pytest --tb=short -q 2>&1; then
    RESULTS="$RESULTS Backend: PASS."
else
    RESULTS="$RESULTS Backend: FAIL!"
    FAILED=1
fi

# Frontend typecheck + lint
echo "[tests] Running frontend checks..."
cd "$PROJ/frontend/test-studio"
if npm run typecheck 2>&1 && npm run lint 2>&1; then
    RESULTS="$RESULTS Frontend: PASS."
else
    RESULTS="$RESULTS Frontend: FAIL!"
    FAILED=1
fi

if [ "$FAILED" -eq 1 ]; then
    "$NOTIFY" "TEST FAILURE" "$RESULTS Fix immediately."
else
    "$NOTIFY" "Tests" "$RESULTS All green."
fi
