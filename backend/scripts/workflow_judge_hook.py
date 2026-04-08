#!/usr/bin/env python3
"""
Always-on Workflow Judge Hook — runs on Claude Code Stop event.

Reads the hook input (JSON from stdin with tool_call history),
detects the workflow, scores steps against evidence, and outputs
nudges if required steps are missing.

Hook type: command
Event: Stop
Output: JSON to stdout (Claude reads it as hook feedback)

Usage in .claude/settings.json or settings.local.json:
{
  "hooks": {
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "python3 backend/scripts/workflow_judge_hook.py",
        "timeout": 10
      }]
    }]
  }
}
"""

import json
import os
import sys
from pathlib import Path

# Add backend to path
backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))


def main():
    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    # Extract the stop reason and transcript info
    stop_reason = hook_input.get("stop_reason", "")
    transcript = hook_input.get("transcript", [])

    # Extract tool calls from transcript
    tool_calls = []
    user_prompt = ""
    for msg in transcript:
        role = msg.get("role", "")
        if role == "user" and not user_prompt:
            content = msg.get("content", "")
            if isinstance(content, str):
                user_prompt = content[:500]
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        user_prompt = c.get("text", "")[:500]
                        break
        elif role == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "tool_use":
                        tool_calls.append({
                            "tool": c.get("name", ""),
                            "result": str(c.get("input", ""))[:200],
                        })

    if not tool_calls or not user_prompt:
        # No tool calls or no prompt — nothing to judge
        return

    # Use the unified interface — single entry point for all judge logic
    try:
        from app.services.workflow_judge.unified import judge_with_nudges, detect

        # First inject tool calls into the session for the hooks module
        from app.services.workflow_judge.hooks import get_or_create_session
        session = get_or_create_session()
        session.prompt = user_prompt
        for tc in tool_calls:
            session.tool_calls.append({"name": tc.get("tool", ""), "input": tc.get("result", "")})

        # Detect + judge + nudge in one call
        result = judge_with_nudges(prompt=user_prompt, tool_calls=tool_calls)

        verdict = result.get("verdict", {})
        if verdict.get("steps_missing", 0) == 0:
            return  # All good

        nudge_text = result.get("nudge_summary", "")
        if nudge_text:
            output = {
                "workflow": verdict.get("workflow_name", ""),
                "verdict": verdict.get("verdict", ""),
                "steps_done": verdict.get("steps_done", 0),
                "steps_missing": verdict.get("steps_missing", 0),
                "missing": verdict.get("missing_steps", []),
                "nudge": nudge_text,
            }
            print(json.dumps(output), file=sys.stderr)

        # Save session for continuity
        session.save()

    except Exception as e:
        # Hook must never crash — silent fail
        pass


if __name__ == "__main__":
    main()
