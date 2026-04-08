"""
MCP Tools for Workflow Judge — exposed via retention.sh MCP server.

These tools let Claude Code self-check, or let the hook enforce completion.

Tools:
  ta.judge.check       — Judge current session against a workflow
  ta.judge.detect      — Detect which workflow a prompt maps to
  ta.judge.status      — Quick status: done/missing steps for current session
  ta.judge.workflows   — List available retained workflows
  ta.judge.correction  — Record a user correction ("you forgot X")
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .session_reader import read_current_session, list_sessions, SessionSummary
from .detector import detect_workflow
from .judge import judge_completion
from .nudge import generate_nudges, format_nudges
from .learner import detect_correction, record_correction, analyze_corrections
from .models import WorkflowKnowledge, JudgeVerdict, seed_builtin_workflows

logger = logging.getLogger(__name__)


def tool_judge_check(
    workflow_id: str = "",
    prompt: str = "",
    project_path: str = "",
) -> Dict[str, Any]:
    """MCP Tool: ta.judge.check — Judge the current session.

    If workflow_id is provided, uses that workflow.
    If prompt is provided, auto-detects the workflow.
    If neither, uses dev.flywheel.v3 as default.

    Returns the full judge verdict with done/missing steps and nudges.
    """
    # Ensure built-in workflows exist
    _ensure_workflows()

    # Read current session
    session = read_current_session(project_path)
    if not session:
        return {"error": "No Claude Code session found", "tool_calls": 0}

    # Detect or use provided workflow
    if not workflow_id and prompt:
        detection = detect_workflow(prompt)
        if detection:
            workflow_id = detection.workflow_id

    if not workflow_id:
        workflow_id = "dev.flywheel.v3"

    # Run judge
    verdict = judge_completion(workflow_id, session.tool_calls)

    # Generate nudges
    wf = WorkflowKnowledge.load(workflow_id)
    nudges = generate_nudges(verdict, wf)
    nudge_text = format_nudges(nudges)

    return {
        "workflow": verdict.workflow_name or workflow_id,
        "verdict": verdict.verdict,
        "nudge_level": verdict.nudge_level,
        "steps_done": verdict.steps_done,
        "steps_missing": verdict.steps_missing,
        "steps_partial": verdict.steps_partial,
        "missing_steps": verdict.missing_steps,
        "summary": verdict.summary,
        "nudges": nudge_text,
        "tool_calls_analyzed": session.total_tool_calls,
        "step_details": verdict.step_results,
    }


def tool_judge_detect(prompt: str) -> Dict[str, Any]:
    """MCP Tool: ta.judge.detect — Detect which workflow a prompt maps to."""
    _ensure_workflows()
    detection = detect_workflow(prompt)
    if not detection:
        return {"detected": False, "prompt": prompt[:100]}
    return {
        "detected": True,
        "workflow_id": detection.workflow_id,
        "workflow_name": detection.workflow_name,
        "confidence": detection.confidence,
        "method": detection.method,
        "alternatives": detection.alternatives,
    }


def tool_judge_status(project_path: str = "") -> Dict[str, Any]:
    """MCP Tool: ta.judge.status — Quick session status.

    Returns what tools were used, what's present, what's likely missing.
    No workflow detection — just raw session facts.
    """
    session = read_current_session(project_path)
    if not session:
        return {"error": "No session found"}

    return {
        "session_id": session.session_id[:12],
        "tool_calls": session.total_tool_calls,
        "files_touched": len(session.files_touched),
        "duration_minutes": session.duration_minutes,
        "has_web_search": session.has_web_search,
        "has_preview": session.has_preview,
        "has_tests": session.has_tests,
        "has_write": session.has_write,
        "tool_distribution": dict(
            sorted(session.tool_distribution.items(), key=lambda x: -x[1])[:10]
        ),
        "first_prompt": session.first_user_prompt[:200],
    }


def tool_judge_workflows() -> Dict[str, Any]:
    """MCP Tool: ta.judge.workflows — List available retained workflows."""
    _ensure_workflows()
    return {"workflows": WorkflowKnowledge.list_all()}


def tool_judge_correction(
    text: str,
    workflow_id: str = "",
) -> Dict[str, Any]:
    """MCP Tool: ta.judge.correction — Record a user correction.

    Detects the correction pattern, infers what step was missed,
    updates the workflow's common_misses.
    """
    corr = detect_correction(text)
    if not corr:
        return {"detected": False, "text": text[:100]}

    record_correction(corr, workflow_id)
    return {
        "detected": True,
        "inferred_step": corr.inferred_step,
        "confidence": corr.confidence,
        "workflow_id": workflow_id or "unassigned",
        "recorded": True,
    }


def tool_judge_analyze_corrections() -> Dict[str, Any]:
    """MCP Tool: ta.judge.analyze — Analyze correction patterns."""
    return analyze_corrections()


# ─── Hook entry point ───────────────────────────────────────────────────

def on_session_stop(
    project_path: str = "",
    prompt: str = "",
) -> Dict[str, Any]:
    """Called by Claude Code hook on session stop.

    Reads the session, detects the workflow, runs the judge,
    and returns nudges. This is the enforcement point.
    """
    result = tool_judge_check(
        prompt=prompt,
        project_path=project_path,
    )

    # Log the verdict
    logger.info(
        "Session stop judge: %s verdict=%s done=%s/%s missing=%s",
        result.get("workflow"),
        result.get("verdict"),
        result.get("steps_done"),
        result.get("steps_done", 0) + result.get("steps_missing", 0) + result.get("steps_partial", 0),
        result.get("missing_steps"),
    )

    return result


def _ensure_workflows():
    """Ensure built-in workflows exist on disk."""
    from .models import _WORKFLOW_DIR
    if not list(_WORKFLOW_DIR.glob("*.json")):
        seed_builtin_workflows()
