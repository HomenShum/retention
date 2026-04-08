#!/bin/bash
# Autonomous code review using Claude Code
# Analyzes recent commits, finds bugs/issues, reports via Slack

PROJ="/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"
MARKER="$PROJ/.claude/last-review-hash"

cd "$PROJ" || exit 1
git fetch origin main 2>/dev/null

CURRENT=$(git rev-parse HEAD 2>/dev/null)
LAST=""
[ -f "$MARKER" ] && LAST=$(cat "$MARKER")

if [ "$LAST" = "$CURRENT" ]; then
    echo "[review] No new commits"
    exit 0
fi

# Build diff context
if [ -n "$LAST" ]; then
    DIFF_RANGE="$LAST..HEAD"
    COMMIT_LOG=$(git log --oneline "$DIFF_RANGE" 2>/dev/null)
    CHANGED_FILES=$(git diff --name-only "$DIFF_RANGE" 2>/dev/null)
else
    DIFF_RANGE="HEAD~3..HEAD"
    COMMIT_LOG=$(git log --oneline -3 2>/dev/null)
    CHANGED_FILES=$(git diff --name-only HEAD~3 2>/dev/null)
fi

PROMPT="You are the retention.sh code review agent. Review the following recent changes and provide a concise report.

Recent commits:
$COMMIT_LOG

Changed files:
$CHANGED_FILES

Instructions:
1. Read each changed file using the Read tool
2. Look for: bugs, security issues, missing error handling, type errors, broken imports, test coverage gaps
3. Rate overall code health: GOOD / NEEDS ATTENTION / CRITICAL
4. Output a structured review summary (max 15 lines) with:
   - Overall rating
   - Key findings (bullet points)
   - Suggested improvements
   - Files that need tests
Do NOT make any changes. Read-only review."

bash "$PROJ/scripts/agent-run-task.sh" "Code Review" "$PROMPT"

# Save marker
echo "$CURRENT" > "$MARKER"
