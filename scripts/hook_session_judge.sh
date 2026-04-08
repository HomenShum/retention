#!/bin/bash
# Claude Code Hook — fires on session stop to judge completion.
#
# Add to .claude/settings.json:
# {
#   "hooks": {
#     "Stop": [{
#       "type": "command",
#       "command": "bash /path/to/scripts/hook_session_judge.sh"
#     }]
#   }
# }
#
# This reads the current session's tool calls and runs the workflow judge.
# If mandatory steps are missing, it prints nudges to stderr (visible in Claude Code).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")/backend"
VENV_PYTHON="$BACKEND_DIR/.venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
    # Fallback to system python
    VENV_PYTHON="python3"
fi

cd "$BACKEND_DIR" 2>/dev/null || cd "$(dirname "$SCRIPT_DIR")"

STDIN_DATA=$(cat)

exec "$VENV_PYTHON" -c "
import sys, json, os
sys.path.insert(0, '$BACKEND_DIR')
os.chdir('$BACKEND_DIR')

try:
    stdin_raw = '''$STDIN_DATA'''
    hook_payload = json.loads(stdin_raw) if stdin_raw.strip() else {}

    from app.services.workflow_judge.mcp_tools import on_session_stop
    result = on_session_stop(
        project_path=hook_payload.get('project_path', ''),
        prompt=hook_payload.get('prompt', ''),
    )

    if result.get('error'):
        sys.exit(0)  # No session = nothing to judge

    verdict = result.get('verdict', '')
    nudge_level = result.get('nudge_level', '')
    missing = result.get('missing_steps', [])

    if nudge_level == 'block' and missing:
        # Print to stderr so it shows in Claude Code
        print(f'\\n[TA JUDGE] Workflow: {result.get(\"workflow\", \"?\")}', file=sys.stderr)
        print(f'[TA JUDGE] Verdict: {verdict}', file=sys.stderr)
        print(f'[TA JUDGE] Missing: {\", \".join(missing)}', file=sys.stderr)
        print(f'[TA JUDGE] {result.get(\"summary\", \"\")}', file=sys.stderr)

    elif nudge_level == 'strong' and missing:
        print(f'\\n[TA JUDGE] {result.get(\"summary\", \"\")}', file=sys.stderr)

except Exception as e:
    # Silent fail — hook should never break the session
    pass
" 2>&1
