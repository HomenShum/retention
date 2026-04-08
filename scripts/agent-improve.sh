#!/bin/bash
# Autonomous improvement agent — finds and makes small improvements using Claude Code
# This is the "builder" — it proactively improves the codebase

PROJ="/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"

cd "$PROJ" || exit 1

PROMPT="You are the retention.sh improvement agent. Your job is to find ONE small improvement to make and implement it.

Scan the codebase and pick ONE task from this priority list (pick the first one that applies):
1. Missing error handling in API endpoints (backend/app/api/)
2. Missing TypeScript types (frontend/test-studio/src/)
3. Missing docstrings on Python functions (backend/app/)
4. Missing loading states or error states in React components
5. Performance improvement (unnecessary re-renders, missing memoization)
6. Missing input validation on API endpoints
7. Dead code removal (unused imports, unreachable code)

Instructions:
1. Search the codebase for the first applicable improvement
2. Make the change using the Edit tool (keep it small — 1 file, < 30 lines changed)
3. Run the relevant test to make sure nothing breaks
4. Create a branch and commit:
   git checkout -b improve/\$(date +%Y%m%d-%H%M) && git add -A && git commit -m 'improve: <what you improved>

Co-Authored-By: OpenClaw TA Agent <agent@retentions.ai>'
5. Output: what you improved, which file, why it matters (3 lines max)

SAFETY: Never touch golden_bugs.json, auth code, or API contracts. Keep changes small and safe."

bash "$PROJ/scripts/agent-run-task.sh" "Auto-Improve" "$PROMPT"
