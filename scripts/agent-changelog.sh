#!/bin/bash
# Autonomous changelog generator using Claude Code
# Reads git log, generates proper changelog entries, commits the update

PROJ="/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"
TODAY=$(date +%Y-%m-%d)

cd "$PROJ" || exit 1

COMMITS=$(git log --since="yesterday" --pretty=format:"%h %s" --no-merges 2>/dev/null)
if [ -z "$COMMITS" ]; then
    echo "[changelog] No new commits since yesterday"
    exit 0
fi

PROMPT="You are the retention.sh changelog agent. Update the CHANGELOG.md with today's changes.

Today's date: $TODAY
Recent commits since yesterday:
$COMMITS

Instructions:
1. Read the current CHANGELOG.md
2. Categorize each commit into: Added, Changed, Fixed, Removed, Security
3. Write clear, user-friendly descriptions (not raw commit messages)
4. Add a new section at the top under the header for [$TODAY]
5. Use the Edit tool to update CHANGELOG.md
6. Then run: git add CHANGELOG.md && git commit -m 'chore(changelog): update for $TODAY

Co-Authored-By: OpenClaw TA Agent <agent@retentions.ai>'
7. Output a 3-line summary of what was added to the changelog"

bash "$PROJ/scripts/agent-run-task.sh" "Changelog" "$PROMPT"
