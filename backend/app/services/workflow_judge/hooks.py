"""
Claude Code Hook Integration — the insertion surface for TA's always-on judge.

Hooks into Claude Code's event system:
  - UserPromptSubmit → detect workflow, inject required steps
  - PostToolUse → update evidence, check step progress
  - Stop → run completion judge, block if evidence missing
  - SessionStart → hydrate retained workflow state

These hooks are configured in .claude/settings.json and execute
as shell commands that call TA's judge API.

This module generates the hook configurations and handles the
hook callback logic.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .detector import detect_workflow
from .judge import judge_completion
from .models import (
    JudgeVerdict,
    NudgeLevel,
    VerdictClass,
    WorkflowKnowledge,
    seed_builtin_workflows,
)
from .nudge import NudgeEngine

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_SESSION_DIR = _DATA_DIR / "workflow_sessions"
_SESSION_DIR.mkdir(parents=True, exist_ok=True)


class WorkflowSession:
    """Tracks a single workflow session — from prompt to completion."""

    def __init__(self, session_id: str = ""):
        self.session_id = session_id or f"ws-{int(datetime.now().timestamp())}"
        self.workflow_id: Optional[str] = None
        self.workflow: Optional[WorkflowKnowledge] = None
        self.tool_calls: List[Dict[str, Any]] = []
        self.prompt: str = ""
        self.started_at: str = datetime.now(timezone.utc).isoformat()
        self.verdict: Optional[JudgeVerdict] = None
        self.nudges_emitted: List[Dict[str, Any]] = []

    def save(self) -> None:
        path = _SESSION_DIR / f"{self.session_id}.json"
        path.write_text(json.dumps({
            "session_id": self.session_id,
            "workflow_id": self.workflow_id,
            "prompt": self.prompt[:500],
            "tool_call_count": len(self.tool_calls),
            "started_at": self.started_at,
            "verdict": self.verdict.verdict if self.verdict else None,
            "nudge_count": len(self.nudges_emitted),
        }, indent=2))


# Global session (one per process — in production, keyed by session ID)
_active_session: Optional[WorkflowSession] = None


def get_or_create_session(session_id: str = "") -> WorkflowSession:
    global _active_session
    if _active_session is None or (session_id and _active_session.session_id != session_id):
        _active_session = WorkflowSession(session_id)
    return _active_session


# ═══════════════════════════════════════════════════════════════
# Hook handlers — called by Claude Code's event system
# ═══════════════════════════════════════════════════════════════

def on_prompt_submit(prompt: str, session_id: str = "") -> Dict[str, Any]:
    """Called on UserPromptSubmit — detect workflow and inject context.

    Returns:
        {
            "workflow_detected": str or null,
            "confidence": float,
            "required_steps": [...],
            "inject_context": str  # Text to prepend to the prompt
        }
    """
    session = get_or_create_session(session_id)
    session.prompt = prompt

    # Detect workflow from natural language
    detection = detect_workflow(prompt)
    if not detection:
        return {"workflow_detected": None, "confidence": 0, "required_steps": [], "inject_context": ""}

    session.workflow_id = detection.workflow_id

    # Load the full workflow
    workflow = WorkflowKnowledge.load(detection.workflow_id)
    if not workflow:
        # Try builtins
        for wf in seed_builtin_workflows():
            if wf.workflow_id == detection.workflow_id:
                workflow = wf
                break

    session.workflow = workflow

    if not workflow:
        return {
            "workflow_detected": detection.workflow_id,
            "confidence": detection.confidence,
            "required_steps": [],
            "inject_context": "",
        }

    # Build context injection — the required steps the agent should know about
    steps = [s.name for s in workflow.required_steps]
    inject = (
        f"\n[TA Workflow Judge] Detected workflow: {workflow.name}\n"
        f"Required steps ({len(steps)}):\n"
        + "\n".join(f"  - {s}" for s in steps)
        + "\nAll steps must have evidence (tool calls, file changes, or artifacts) to pass completion.\n"
    )

    return {
        "workflow_detected": detection.workflow_id,
        "confidence": detection.confidence,
        "required_steps": steps,
        "inject_context": inject,
    }


def on_tool_use(tool_name: str, tool_input: Dict[str, Any], session_id: str = "") -> Dict[str, Any]:
    """Called on PostToolUse — update evidence and check progress.

    Returns:
        {
            "steps_done": int,
            "steps_remaining": int,
            "nudge": str or null  # Nudge message if a strong/block nudge is warranted
        }
    """
    session = get_or_create_session(session_id)
    session.tool_calls.append({"name": tool_name, "input": tool_input})

    if not session.workflow:
        return {"steps_done": 0, "steps_remaining": 0, "nudge": None}

    # Quick progress check (don't run full judge on every tool call — too expensive)
    # Just count evidence matches
    required = session.workflow.required_steps
    done_count = 0
    for step in required:
        for tc in session.tool_calls:
            name = tc.get("name", "").lower()
            # Simple matching: check if any tool call matches the step's evidence types
            for ev_type in step.evidence_types:
                if ev_type.lower() in name:
                    done_count += 1
                    break
            else:
                continue
            break

    remaining = len(required) - done_count

    # Only nudge if we're far into the session and still missing critical steps
    nudge = None
    if len(session.tool_calls) > 20 and remaining > len(required) // 2:
        missing = [s.name for s in required if not any(
            any(ev.lower() in tc.get("name", "").lower() for ev in s.evidence_types)
            for tc in session.tool_calls
        )]
        if missing:
            nudge = f"[TA] {remaining} required steps still missing: {', '.join(missing[:3])}"

    return {
        "steps_done": done_count,
        "steps_remaining": remaining,
        "nudge": nudge,
    }


def on_stop(session_id: str = "") -> Dict[str, Any]:
    """Called on Stop — run completion judge and decide whether to block.

    Returns:
        {
            "verdict": str,
            "allow_stop": bool,
            "summary": str,
            "missing_steps": [...],
            "nudge_level": str
        }
    """
    session = get_or_create_session(session_id)

    if not session.workflow:
        return {
            "verdict": "no_workflow",
            "allow_stop": True,
            "summary": "No workflow detected — allowing stop.",
            "missing_steps": [],
            "nudge_level": "none",
        }

    # Run the full judge
    verdict = judge_completion(session.workflow, session.tool_calls)
    session.verdict = verdict

    # Determine if we should block
    allow_stop = verdict.verdict in (
        VerdictClass.ACCEPTABLE.value,
        VerdictClass.MINOR_LOSS.value,
    )

    missing_steps = []
    if hasattr(verdict, 'scored_required'):
        missing_steps = [
            s.name for s in verdict.scored_required
            if hasattr(s, 'status') and s.status == 'missing'
        ]

    # Parse missing steps from summary if not available from scored_required
    if not missing_steps and "missing:" in verdict.summary.lower():
        # Extract from summary text
        parts = verdict.summary.split("missing:")
        if len(parts) > 1:
            missing_steps = [s.strip() for s in parts[1].split(",")]

    # Save session
    session.save()

    return {
        "verdict": verdict.verdict,
        "allow_stop": allow_stop,
        "summary": verdict.summary,
        "missing_steps": missing_steps,
        "nudge_level": verdict.nudge_level,
    }


def on_session_start(session_id: str = "") -> Dict[str, Any]:
    """Called on SessionStart — hydrate any retained workflow state.

    Returns:
        {
            "prior_session": str or null,
            "pending_steps": [...],
            "inject_context": str
        }
    """
    session = get_or_create_session(session_id)

    # Check for prior sessions with the same workflow that were incomplete
    prior = None
    for f in sorted(_SESSION_DIR.glob("ws-*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            if data.get("verdict") in ("failed_replay", "replay_should_have_escalated"):
                prior = data
                break
        except Exception:
            continue

    if not prior:
        return {"prior_session": None, "pending_steps": [], "inject_context": ""}

    inject = (
        f"\n[TA] Prior incomplete session: {prior.get('workflow_id', '?')}\n"
        f"Verdict was: {prior.get('verdict', '?')}\n"
        f"Consider resuming the missing steps.\n"
    )

    return {
        "prior_session": prior.get("session_id"),
        "pending_steps": [],  # Would need to load full verdict to get missing steps
        "inject_context": inject,
    }


# ═══════════════════════════════════════════════════════════════
# Settings generator — creates the .claude/settings.json hooks
# ═══════════════════════════════════════════════════════════════

def generate_hook_settings(api_base: str = "http://localhost:8000") -> Dict[str, Any]:
    """Generate Claude Code settings.json hook configuration.

    Claude Code pipes JSON to hook commands via stdin.
    We use `curl -d @-` to forward stdin as the POST body.
    """
    return {
        "hooks": {
            "UserPromptSubmit": [
                {
                    "matcher": "",
                    "hooks": [{
                        "type": "command",
                        "command": f"curl -s -X POST {api_base}/api/judge/on-prompt -H 'Content-Type: application/json' -d @-",
                    }],
                },
            ],
            "PostToolUse": [
                {
                    "matcher": "",
                    "hooks": [{
                        "type": "command",
                        "command": f"curl -s -X POST {api_base}/api/judge/on-tool-use -H 'Content-Type: application/json' -d @-",
                    }],
                },
            ],
            "Stop": [
                {
                    "matcher": "",
                    "hooks": [{
                        "type": "command",
                        "command": f"curl -s -X POST {api_base}/api/judge/on-stop -H 'Content-Type: application/json' -d '{{}}'",
                    }],
                },
            ],
        },
    }
