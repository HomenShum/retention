#!/bin/bash
# Autonomous changelog updater — runs daily, appends new entries
PROJ="/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"
NOTIFY="$PROJ/scripts/retention-notify.sh"
CHANGELOG="$PROJ/CHANGELOG.md"
TODAY=$(date +%Y-%m-%d)

cd "$PROJ" || exit 1

# Get commits since yesterday
COMMITS=$(git log --since="yesterday" --pretty=format:"- %s (%h)" --no-merges 2>/dev/null)

if [ -z "$COMMITS" ]; then
    echo "[changelog] No new commits since yesterday"
    exit 0
fi

# Categorize commits
ADDED=$(echo "$COMMITS" | grep -i "^- feat" || true)
FIXED=$(echo "$COMMITS" | grep -i "^- fix" || true)
CHANGED=$(echo "$COMMITS" | grep -i "^- \(refactor\|chore\|perf\|style\)" || true)
OTHER=$(echo "$COMMITS" | grep -iv "^- \(feat\|fix\|refactor\|chore\|perf\|style\)" || true)

# Build entry
ENTRY="\n## [$TODAY]\n"
[ -n "$ADDED" ] && ENTRY="$ENTRY\n### Added\n$ADDED\n"
[ -n "$FIXED" ] && ENTRY="$ENTRY\n### Fixed\n$FIXED\n"
[ -n "$CHANGED" ] && ENTRY="$ENTRY\n### Changed\n$CHANGED\n"
[ -n "$OTHER" ] && ENTRY="$ENTRY\n### Other\n$OTHER\n"

# Prepend to changelog (after first line)
if [ -f "$CHANGELOG" ]; then
    HEADER=$(head -1 "$CHANGELOG")
    REST=$(tail -n +2 "$CHANGELOG")
    echo -e "$HEADER\n$ENTRY$REST" > "$CHANGELOG"
else
    echo -e "# Changelog\n$ENTRY" > "$CHANGELOG"
fi

COUNT=$(echo "$COMMITS" | wc -l | tr -d ' ')
"$NOTIFY" "Changelog" "Updated with $COUNT new entries for $TODAY"
echo "[changelog] Added $COUNT entries for $TODAY"
