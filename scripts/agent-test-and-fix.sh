#!/bin/bash
# Autonomous test runner + auto-fixer using Claude Code
# Runs tests, if failures found, Claude Code attempts to fix them

PROJ="/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"

cd "$PROJ" || exit 1

PROMPT="You are the retention.sh test agent. Run the test suite and fix any failures.

Instructions:
1. Run backend tests: cd backend && .venv/bin/python -m pytest --tb=short -q
2. Run frontend checks: cd frontend/test-studio && npm run typecheck && npm run lint
3. If ALL pass: output a 2-line summary 'All tests passing. Backend: X passed. Frontend: clean.'
4. If any FAIL:
   a. Read the failing test and the source code it tests
   b. Determine if the fix is in the test or the source
   c. Fix it using the Edit tool
   d. Re-run the specific failing test to confirm
   e. If fixed, commit on a new branch: git checkout -b fix/auto-\$(date +%Y%m%d-%H%M) && git add -A && git commit -m 'fix: auto-fix test failure

Co-Authored-By: OpenClaw TA Agent <agent@retentions.ai>'
   f. Output: what failed, what you fixed, branch name
5. SAFETY: Never modify golden_bugs.json or security/auth code. If the failure is in those areas, just report it."

bash "$PROJ/scripts/agent-run-task.sh" "Test & Fix" "$PROMPT"
