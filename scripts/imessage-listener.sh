#!/bin/bash
# iMessage listener — polls Messages.app for new messages from user
# Routes commands to OpenClaw agent or Claude Code
# Runs as a background daemon

PROJ="/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"
NOTIFY="$PROJ/scripts/notify-imessage.sh"
PHONE="4083335386"
LAST_MSG_FILE="$PROJ/.claude/last-imessage-id"
POLL_INTERVAL=15  # seconds

mkdir -p "$PROJ/.claude"

# Get last processed message ID
LAST_ID=0
if [ -f "$LAST_MSG_FILE" ]; then
    LAST_ID=$(cat "$LAST_MSG_FILE")
fi

echo "[imessage-listener] Starting. Polling every ${POLL_INTERVAL}s for messages from $PHONE"

while true; do
    # Query Messages database for new messages from the user's phone
    NEW_MSGS=$(sqlite3 ~/Library/Messages/chat.db "
        SELECT m.ROWID, m.text
        FROM message m
        JOIN handle h ON m.handle_id = h.ROWID
        WHERE h.id LIKE '%$PHONE%'
        AND m.is_from_me = 0
        AND m.ROWID > $LAST_ID
        AND m.text IS NOT NULL
        ORDER BY m.ROWID ASC
        LIMIT 5;
    " 2>/dev/null)

    if [ -n "$NEW_MSGS" ]; then
        while IFS='|' read -r msg_id msg_text; do
            echo "[imessage-listener] New message #$msg_id: $msg_text"

            # Route based on message content
            case "$msg_text" in
                status|Status|STATUS)
                    "$NOTIFY" "$(bash "$PROJ/scripts/autonomous-health-check.sh" 2>&1 | tail -3)"
                    ;;
                tests|Tests|TESTS|"run tests")
                    "$NOTIFY" "Running tests now..."
                    bash "$PROJ/scripts/autonomous-tests.sh" &
                    ;;
                changelog|Changelog|CHANGELOG)
                    bash "$PROJ/scripts/autonomous-changelog.sh"
                    ;;
                review|Review|REVIEW|"code review")
                    "$NOTIFY" "Starting code review..."
                    bash "$PROJ/scripts/autonomous-review.sh" &
                    ;;
                help|Help|HELP)
                    "$NOTIFY" "Commands: status, tests, changelog, review, help. Or type anything to ask the agent."
                    ;;
                *)
                    # Forward to OpenClaw agent
                    RESPONSE=$(openclaw agent --message "$msg_text" --agent main --format text 2>&1 | head -20)
                    if [ -n "$RESPONSE" ]; then
                        "$NOTIFY" "$RESPONSE"
                    else
                        "$NOTIFY" "Received: $msg_text — processing with agent..."
                        # Fallback: use Claude Code
                        cd "$PROJ" && claude --print "$msg_text" 2>/dev/null | head -10 | while read -r line; do
                            "$NOTIFY" "$line"
                        done
                    fi
                    ;;
            esac

            LAST_ID=$msg_id
            echo "$LAST_ID" > "$LAST_MSG_FILE"
        done <<< "$NEW_MSGS"
    fi

    sleep $POLL_INTERVAL
done
