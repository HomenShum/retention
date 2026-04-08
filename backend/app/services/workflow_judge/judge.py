"""
Completion Judge — evidence-backed step scoring + hard gates + verdict.

The judge does NOT ask "did the model say it finished?"
It asks:
1. What workflow was likely intended?
2. What steps are mandatory for that workflow?
3. What evidence exists that each step actually happened?
4. What was skipped, weak, or ambiguous?
5. Is the result good enough to accept, or should it escalate?

No evidence = no credit. The model cannot self-certify completion.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .models import (
    JudgeVerdict,
    NudgeLevel,
    StepEvidence,
    StepStatus,
    VerdictClass,
    WorkflowKnowledge,
    WorkflowStep,
)

logger = logging.getLogger(__name__)


# ─── Evidence collection ────────────────────────────────────────────────

def collect_evidence(
    tool_calls: List[Dict[str, Any]],
    step: WorkflowStep,
) -> List[StepEvidence]:
    """Match tool calls to a workflow step's evidence requirements.

    Each tool call is checked against the step's expected evidence types
    and common tool calls. Returns matched evidence items.
    """
    evidence = []

    for tc in tool_calls:
        tool_name = tc.get("tool", tc.get("name", ""))
        tool_input = tc.get("input", tc.get("params", {}))
        tool_type = _classify_tool_call(tool_name, tool_input)

        # Check if this tool call matches the step's evidence types
        if tool_type in step.evidence_types:
            evidence.append(StepEvidence(
                evidence_type=tool_type,
                evidence_ref=tc.get("id", tool_name),
                content_preview=str(tc.get("result", tc.get("output", "")))[:200],
                timestamp=tc.get("timestamp", ""),
                confidence=0.8,
            ))
        # Check if tool name matches common tool calls
        elif tool_name in step.common_tool_calls:
            evidence.append(StepEvidence(
                evidence_type=tool_type,
                evidence_ref=tc.get("id", tool_name),
                content_preview=str(tc.get("result", tc.get("output", "")))[:200],
                timestamp=tc.get("timestamp", ""),
                confidence=0.7,
            ))

    return evidence


def _classify_tool_call(tool_name: str, tool_input: dict = None) -> str:
    """Classify a tool call into an evidence type.

    Uses tool name + input content for better matching.
    """
    name = tool_name.lower()
    inp = tool_input or {}
    inp_str = str(inp).lower()

    # Check specific tool names BEFORE generic patterns
    if "todowrite" in name:
        return "plan_summary"
    if "askuserquestion" in name:
        return "plan_summary"
    if "enterplanmode" in name or "exitplanmode" in name:
        return "plan_summary"
    if any(k in name for k in ["read", "glob", "grep"]):
        return "file_read"
    if any(k in name for k in ["write", "edit"]):
        return "file_write"
    if "bash" in name:
        # Classify bash by command content
        cmd = str(inp.get("command", "")).lower()
        if any(k in cmd for k in ["git commit", "git add"]):
            return "commit_message"
        if any(k in cmd for k in ["git push", "vercel", "deploy", "npm run build"]):
            return "deploy_action"
        if any(k in cmd for k in ["test", "pytest", "jest", "vitest", "typecheck", "lint"]):
            return "bash_test"
        if any(k in cmd for k in ["curl", "wget", "http"]):
            return "api_test"
        return "bash_test"
    if any(k in name for k in ["websearch", "web_search"]):
        return "web_search"
    if any(k in name for k in ["webfetch", "web_fetch"]):
        return "web_fetch"
    if "preview_start" in name:
        return "preview_start"
    if "preview_screenshot" in name or "preview_snapshot" in name:
        return "preview_screenshot"
    if "preview_click" in name:
        return "preview_click"
    if "preview_console" in name:
        return "preview_console_logs"
    if "preview_fill" in name:
        return "preview_fill"
    if "todowrite" in name:
        return "plan_summary"
    if "agent" in name:
        return "agent_delegation"
    return "other"


# ─── Step scoring ───────────────────────────────────────────────────────

def score_step(
    step: WorkflowStep,
    tool_calls: List[Dict[str, Any]],
) -> WorkflowStep:
    """Score a single step against collected evidence.

    Returns the step with updated status, evidence, confidence, and notes.
    """
    evidence = collect_evidence(tool_calls, step)
    step.evidence = evidence

    if len(evidence) >= 2:
        step.status = StepStatus.DONE
        step.confidence = min(0.95, 0.7 + len(evidence) * 0.05)
        step.notes = f"{len(evidence)} evidence items found"
    elif len(evidence) == 1:
        step.status = StepStatus.PARTIAL
        step.confidence = 0.6
        step.notes = f"1 evidence item — may need more coverage"
    else:
        step.status = StepStatus.MISSING
        step.confidence = 0.9  # High confidence it's missing
        step.notes = "No evidence found for this step"

    return step


# ─── Hard gates ─────────────────────────────────────────────────────────

def evaluate_hard_gates(
    workflow: WorkflowKnowledge,
    scored_steps: List[WorkflowStep],
    tool_calls: List[Dict[str, Any]],
) -> Dict[str, bool]:
    """Evaluate hard gates. Any failure constrains the verdict."""
    required_steps = [s for s in scored_steps if s.mandatory]
    missing_required = [s for s in required_steps if s.status == StepStatus.MISSING]

    gates = {
        "gate_task_intent_met": len(missing_required) <= len(required_steps) * 0.3,
        "gate_no_fabricated_result": True,  # Would need output analysis
        "gate_output_usable": len(missing_required) < len(required_steps),
        "gate_escalation_needed": len(missing_required) > 0,
        "gate_replay_deployable": len(missing_required) == 0,
    }

    return gates


# ─── Verdict decision ───────────────────────────────────────────────────

def decide_verdict(
    scored_steps: List[WorkflowStep],
    hard_gates: Dict[str, bool],
) -> tuple:
    """Decide the verdict class and nudge level.

    Returns (verdict, nudge_level, summary).
    """
    required = [s for s in scored_steps if s.mandatory]
    done = [s for s in required if s.status == StepStatus.DONE]
    partial = [s for s in required if s.status == StepStatus.PARTIAL]
    missing = [s for s in required if s.status == StepStatus.MISSING]

    done_rate = len(done) / max(len(required), 1)
    missing_names = [s.name for s in missing]

    # Hard gate override
    if not hard_gates.get("gate_output_usable", True):
        return (
            VerdictClass.FAILED.value,
            NudgeLevel.BLOCK.value,
            f"Output not usable — {len(missing)}/{len(required)} required steps missing: {', '.join(missing_names)}",
        )

    if not hard_gates.get("gate_replay_deployable", True) and len(missing) > 0:
        if done_rate >= 0.7:
            return (
                VerdictClass.SHOULD_ESCALATE.value,
                NudgeLevel.STRONG.value,
                f"Most work done but {len(missing)} required steps missing: {', '.join(missing_names)}",
            )
        else:
            return (
                VerdictClass.FAILED.value,
                NudgeLevel.BLOCK.value,
                f"Incomplete — {len(missing)}/{len(required)} required steps missing: {', '.join(missing_names)}",
            )

    # Verdict based on completion rate
    if done_rate == 1.0 and not partial:
        return (
            VerdictClass.ACCEPTABLE.value,
            NudgeLevel.SOFT.value,  # No nudge needed but keep soft for awareness
            f"All {len(required)} required steps completed with evidence.",
        )
    elif done_rate >= 0.85:
        return (
            VerdictClass.MINOR_LOSS.value,
            NudgeLevel.SOFT.value,
            f"{len(done)}/{len(required)} done, {len(partial)} partial. Minor gaps: {', '.join(s.name for s in partial)}",
        )
    elif done_rate >= 0.5:
        return (
            VerdictClass.SHOULD_ESCALATE.value,
            NudgeLevel.STRONG.value,
            f"{len(done)}/{len(required)} done. Missing: {', '.join(missing_names)}",
        )
    else:
        return (
            VerdictClass.FAILED.value,
            NudgeLevel.BLOCK.value,
            f"Only {len(done)}/{len(required)} done. Missing: {', '.join(missing_names)}",
        )


# ─── Main judge function ───────────────────────────────────────────────

def judge_completion(
    workflow: WorkflowKnowledge | str,
    tool_calls: List[Dict[str, Any]],
) -> JudgeVerdict:
    """Judge whether a workflow run is complete.

    Args:
        workflow: Either a WorkflowKnowledge object OR a workflow_id string.
                  If a string, loads from disk.
        tool_calls: List of tool call dicts from the session.

    This is the core function. It:
    1. Scores each required step against collected evidence
    2. Evaluates hard gates
    3. Decides verdict and nudge level
    4. Returns a full JudgeVerdict

    No evidence = no credit. The model cannot self-certify.
    """
    # Accept workflow_id string — load from disk
    if isinstance(workflow, str):
        loaded = WorkflowKnowledge.load(workflow)
        if not loaded:
            return JudgeVerdict(
                workflow_id=workflow,
                verdict=VerdictClass.FRONTIER_REQUIRED.value,
                summary=f"Workflow '{workflow}' not found — cannot judge.",
                judge_source="workflow_judge",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        workflow = loaded
    t0 = time.time()

    # Score all steps
    scored_required = []
    for step in workflow.required_steps:
        scored = score_step(WorkflowStep(**asdict(step)), tool_calls)
        scored_required.append(scored)

    scored_optional = []
    for step in workflow.optional_steps:
        scored = score_step(WorkflowStep(**asdict(step)), tool_calls)
        scored_optional.append(scored)

    # Hard gates
    hard_gates = evaluate_hard_gates(workflow, scored_required, tool_calls)

    # Verdict
    verdict, nudge_level, summary = decide_verdict(scored_required, hard_gates)

    # Count statuses
    done = sum(1 for s in scored_required if s.status == StepStatus.DONE)
    partial = sum(1 for s in scored_required if s.status == StepStatus.PARTIAL)
    missing = sum(1 for s in scored_required if s.status == StepStatus.MISSING)
    missing_names = [s.name for s in scored_required if s.status == StepStatus.MISSING]

    # Build nudge message
    if nudge_level == NudgeLevel.BLOCK.value:
        nudge_msg = f"Cannot mark complete — {missing} mandatory steps have no evidence: {', '.join(missing_names)}"
    elif nudge_level == NudgeLevel.STRONG.value:
        nudge_msg = f"Required steps missing evidence: {', '.join(missing_names)}"
    else:
        nudge_msg = ""

    elapsed = int((time.time() - t0) * 1000)

    return JudgeVerdict(
        workflow_id=workflow.workflow_id,
        workflow_name=workflow.name,
        workflow_confidence=workflow.confidence,
        required_steps=[
            {
                "step_id": s.step_id,
                "name": s.name,
                "status": s.status.value if isinstance(s.status, StepStatus) else s.status,
                "confidence": s.confidence,
                "evidence_count": len(s.evidence),
                "notes": s.notes,
            }
            for s in scored_required
        ],
        steps_done=done,
        steps_partial=partial,
        steps_missing=missing,
        hard_gates=hard_gates,
        all_gates_pass=all(
            v for k, v in hard_gates.items()
            if k != "gate_escalation_needed"
        ),
        scores={},  # Populated by LLM judge if available
        verdict=verdict,
        confidence=0.85,
        summary=summary,
        nudge_level=nudge_level,
        nudge_message=nudge_msg,
        missing_steps=missing_names,
        judge_model="deterministic",
        judge_source="workflow_judge",
        timestamp=datetime.now(timezone.utc).isoformat(),
        duration_ms=elapsed,
    )
