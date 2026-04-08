"""
Unified Workflow Judge Interface — single entry point for both judge systems.

Reconciles:
  System A (agents/qa_pipeline/): workflow_policy.py + workflow_judge.py + completion_gate.py
  System B (services/workflow_judge/): models.py + detector.py + judge.py + hooks.py

This module provides the canonical API. Both systems delegate here.
Data is stored in ONE location: data/workflow_knowledge/

The unified interface:
  detect_workflow(prompt) → DetectionResult
  judge_completion(prompt_or_workflow_id, tool_calls) → JudgeVerdict
  on_prompt_submit(prompt) → hook response
  on_tool_use(tool_name, tool_input) → hook response
  on_stop() → gate decision
  learn_correction(text, workflow_id) → learned step
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .detector import detect_workflow as _detect, DetectionResult
from .judge import judge_completion as _judge
from .hooks import (
    on_prompt_submit as _on_prompt,
    on_tool_use as _on_tool,
    on_stop as _on_stop,
    on_session_start as _on_session_start,
    get_or_create_session,
    WorkflowSession,
)
from .learner import detect_correction, record_correction, analyze_corrections
from .nudge import NudgeEngine, Nudge
from .models import (
    WorkflowKnowledge,
    WorkflowStep,
    StepEvidence,
    JudgeVerdict,
    VerdictClass,
    NudgeLevel,
    StepStatus,
    seed_builtin_workflows,
    _WORKFLOW_DIR,
)

logger = logging.getLogger(__name__)


# ─── Unified API ─────────────────────────────────────────────────────────

def detect(prompt: str, context: str = "") -> Optional[DetectionResult]:
    """Detect workflow from natural language prompt."""
    return _detect(prompt, context)


def judge(
    prompt: str = "",
    workflow_id: str = "",
    tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> JudgeVerdict:
    """Judge workflow completion. Auto-detects workflow if not specified."""
    tool_calls = tool_calls or []

    # Resolve workflow
    workflow = None
    if workflow_id:
        workflow = WorkflowKnowledge.load(workflow_id)
    if not workflow and prompt:
        detection = _detect(prompt)
        if detection:
            workflow = WorkflowKnowledge.load(detection.workflow_id)

    if not workflow:
        return JudgeVerdict(
            workflow_id="unknown",
            workflow_name="Unknown",
            verdict=VerdictClass.FRONTIER_REQUIRED.value,
            summary="No workflow detected. Cannot judge completion.",
        )

    return _judge(workflow, tool_calls)


def judge_with_nudges(
    prompt: str = "",
    workflow_id: str = "",
    tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Judge + generate nudges in one call."""
    from dataclasses import asdict

    tool_calls = tool_calls or []

    # Resolve workflow
    workflow = None
    if workflow_id:
        workflow = WorkflowKnowledge.load(workflow_id)
    if not workflow and prompt:
        detection = _detect(prompt)
        if detection:
            workflow = WorkflowKnowledge.load(detection.workflow_id)

    if not workflow:
        return {
            "verdict": {"verdict": "frontier_required", "summary": "No workflow detected"},
            "nudges": [],
            "nudge_summary": "",
        }

    verdict = _judge(workflow, tool_calls)
    engine = NudgeEngine()
    nudges = engine.generate_nudges(verdict, workflow)
    engine.log_nudges(nudges, verdict)

    return {
        "verdict": asdict(verdict),
        "nudges": [asdict(n) for n in nudges],
        "nudge_summary": engine.format_nudges_for_user(nudges),
    }


# Hook interface (delegates to hooks.py)
on_prompt_submit = _on_prompt
on_tool_use = _on_tool
on_stop = _on_stop
on_session_start = _on_session_start

# Learning interface (delegates to learner.py)
learn = detect_correction
record = record_correction
analyze = analyze_corrections

# Seeding
seed = seed_builtin_workflows
