#!/bin/bash
# Core agent runner — executes a task using Claude Code in non-interactive mode
# Usage: ./agent-run-task.sh "task-name" "prompt"
# Outputs: task result to stdout, sends Slack notification

PROJ="/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"
NOTIFY="$PROJ/scripts/notify-slack.sh"
LOG_DIR="$PROJ/.claude/logs"
TASK_NAME="${1:-unnamed}"
PROMPT="${2:-No prompt provided}"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M')

mkdir -p "$LOG_DIR"

TASK_LOG="$LOG_DIR/${TASK_NAME}-$(date +%Y%m%d-%H%M%S).log"

echo "[$TIMESTAMP] Starting task: $TASK_NAME" | tee "$TASK_LOG"

# Run Claude Code in print mode with allowed tools for autonomous operation
RESULT=$(cd "$PROJ" && echo "$PROMPT" | npx @anthropic-ai/claude-code --print \
  --allowedTools "Read" "Edit" "Write" "Glob" "Grep" \
  "Bash(git:*)" "Bash(cd:*)" "Bash(curl:*)" "Bash(npm:*)" \
  "Bash(npx:*)" "Bash(python*)" "Bash(cat:*)" "Bash(ls:*)" \
  "Bash(lsof:*)" "Bash(bash:*)" "Bash(chmod:*)" "Bash(mkdir:*)" \
  2>>"$TASK_LOG")
EXIT_CODE=$?

echo "$RESULT" >> "$TASK_LOG"

if [ $EXIT_CODE -eq 0 ] && [ -n "$RESULT" ]; then
    # Truncate result for Slack (max 2000 chars)
    SLACK_RESULT=$(echo "$RESULT" | head -c 2000)
    "$NOTIFY" "*[TA Agent — $TASK_NAME]* ✅ Completed at $TIMESTAMP
$SLACK_RESULT"
    echo "[$TIMESTAMP] Task $TASK_NAME completed successfully" >> "$TASK_LOG"
else
    "$NOTIFY" "*[TA Agent — $TASK_NAME]* ❌ Failed at $TIMESTAMP
Exit code: $EXIT_CODE
$(tail -5 "$TASK_LOG" | head -c 500)"
    echo "[$TIMESTAMP] Task $TASK_NAME FAILED (exit $EXIT_CODE)" >> "$TASK_LOG"
fi

echo "$RESULT"
