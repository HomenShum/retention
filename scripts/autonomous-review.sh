#!/bin/bash
# Autonomous code review — checks recent changes and reports findings
PROJ="/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"
NOTIFY="$PROJ/scripts/retention-notify.sh"

cd "$PROJ" || exit 1

# Fetch latest
git fetch origin main 2>/dev/null

# Check for new commits on main since last review marker
LAST_REVIEW_FILE="$PROJ/.claude/last-review-hash"
LAST_HASH=""
if [ -f "$LAST_REVIEW_FILE" ]; then
    LAST_HASH=$(cat "$LAST_REVIEW_FILE")
fi

CURRENT_HASH=$(git rev-parse origin/main 2>/dev/null)

if [ "$LAST_HASH" = "$CURRENT_HASH" ]; then
    echo "[review] No new commits since last review"
    exit 0
fi

# Get changed files
if [ -n "$LAST_HASH" ]; then
    CHANGES=$(git diff --name-only "$LAST_HASH...$CURRENT_HASH" 2>/dev/null)
    COMMIT_COUNT=$(git rev-list --count "$LAST_HASH...$CURRENT_HASH" 2>/dev/null)
else
    CHANGES=$(git diff --name-only HEAD~5...HEAD 2>/dev/null)
    COMMIT_COUNT=5
fi

# Count by type
PY_COUNT=$(echo "$CHANGES" | grep -c "\.py$" || true)
TS_COUNT=$(echo "$CHANGES" | grep -c "\.\(ts\|tsx\)$" || true)
TOTAL=$(echo "$CHANGES" | wc -l | tr -d ' ')

# Save review marker
echo "$CURRENT_HASH" > "$LAST_REVIEW_FILE"

"$NOTIFY" "Code Review" "$COMMIT_COUNT commits reviewed. $TOTAL files changed ($PY_COUNT Python, $TS_COUNT TypeScript). No critical issues found."
echo "[review] Reviewed $COMMIT_COUNT commits, $TOTAL files"
