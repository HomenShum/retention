"""
Workflow Judge — unified interface delegating to the canonical service layer.

This module provides:
  - RunEvidence + collect_evidence_from_events: evidence extraction from events
  - JudgeOutput + output models: Pydantic schemas for judge results
  - WorkflowJudge: facade that delegates to services.workflow_judge for
    deterministic judging, and adds LLM mode via llm_workflow_classifier

The canonical judge logic lives in backend/app/services/workflow_judge/.
This file is the adapter layer so qa_pipeline code (completion_gate,
dogfood_harness) can call one interface regardless of mode.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Shared enums (re-exported from both systems for compat)
# ---------------------------------------------------------------------------

from .workflow_policy import (
    JudgeVerdict,
    NudgeLevel,
    StepStatus,
    WorkflowPolicy,
    WorkflowStep as PolicyStep,
    detect_workflow as detect_workflow_heuristic,
    load_policy,
)


# ---------------------------------------------------------------------------
# Judge output models (Peer B schema — used by completion_gate + dogfood)
# ---------------------------------------------------------------------------

class StepEvaluation(BaseModel):
    step_id: str
    step_name: str
    status: StepStatus
    confidence: float = 0.0
    evidence_refs: List[str] = Field(default_factory=list)
    notes: str = ""


class HardGateResult(BaseModel):
    gate_id: str
    gate_name: str
    passed: bool
    reason: str = ""


class NudgeMessage(BaseModel):
    level: NudgeLevel
    step_id: str = ""
    message: str
    action_suggested: str = ""


class PairwiseResult(BaseModel):
    winner: str = ""
    strength: str = ""
    reason: str = ""


class JudgeOutput(BaseModel):
    workflow_id: str = ""
    workflow_name: str = ""
    workflow_confidence: float = 0.0
    required_steps: List[StepEvaluation] = Field(default_factory=list)
    hard_gates: List[HardGateResult] = Field(default_factory=list)
    scores: Dict[str, int] = Field(default_factory=dict)
    pairwise: Optional[PairwiseResult] = None
    nudges: List[NudgeMessage] = Field(default_factory=list)
    final_verdict: JudgeVerdict = JudgeVerdict.SHOULD_ESCALATE
    confidence: float = 0.0
    summary: str = ""
    human_readable: str = ""
    judged_at: str = ""
    judge_model: str = "deterministic"


# ---------------------------------------------------------------------------
# Evidence collection
# ---------------------------------------------------------------------------

class RunEvidence(BaseModel):
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    file_reads: List[str] = Field(default_factory=list)
    file_writes: List[str] = Field(default_factory=list)
    searches: List[Dict[str, Any]] = Field(default_factory=list)
    screenshots: List[str] = Field(default_factory=list)
    test_runs: List[Dict[str, Any]] = Field(default_factory=list)
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    user_corrections: List[str] = Field(default_factory=list)
    raw_events: List[Dict[str, Any]] = Field(default_factory=list)


def collect_evidence_from_events(events: List[Dict[str, Any]]) -> RunEvidence:
    """Extract structured evidence from a stream of run events."""
    evidence = RunEvidence(raw_events=events)

    for event in events:
        evt_type = event.get("type", "")
        tool_name = str(event.get("tool_name", "")).lower()
        event_str = str(event).lower()

        if "tool" in evt_type or event.get("tool_name"):
            evidence.tool_calls.append(event)
        if evt_type in ("file_read", "read"):
            evidence.file_reads.append(event.get("path", event.get("file", "")))
        if evt_type in ("file_write", "write", "edit"):
            evidence.file_writes.append(event.get("path", event.get("file", "")))
        if "search" in evt_type or "web" in evt_type or "fetch" in evt_type:
            evidence.searches.append(event)
        if "screenshot" in evt_type or "preview" in evt_type:
            evidence.screenshots.append(event.get("path", str(event)))
        if ("test" in evt_type or "pytest" in evt_type or "lint" in evt_type
                or "typecheck" in evt_type or "build" in evt_type
                or any(kw in tool_name for kw in ("test", "pytest", "lint", "tsc", "mypy", "ruff"))
                or any(kw in event_str for kw in ("syntax ok", "all tests pass", "pass", "import ast"))):
            evidence.test_runs.append(event)
        if "artifact" in evt_type or "summary" in evt_type or "report" in evt_type:
            evidence.artifacts.append(event)

    return evidence


# ---------------------------------------------------------------------------
# WorkflowJudge — unified facade
# ---------------------------------------------------------------------------

class WorkflowJudge:
    """Unified workflow judge.

    Modes:
      use_llm=False → delegates to services.workflow_judge (deterministic, fast)
      use_llm=True  → uses llm_workflow_classifier (LLM API calls, accurate)
    """

    def judge(
        self,
        prompt: str,
        evidence: RunEvidence,
        policy_override: Optional[WorkflowPolicy] = None,
        context: Optional[Dict[str, Any]] = None,
        use_llm: bool = False,
        llm_model: str = "gpt-5.4-mini",
    ) -> JudgeOutput:
        output = JudgeOutput(judged_at=_now_iso())

        if use_llm:
            return self._judge_llm(prompt, evidence, policy_override, llm_model, output)

        return self._judge_deterministic(prompt, evidence, policy_override, output)

    # ── Deterministic mode: delegate to services.workflow_judge ──

    def _judge_deterministic(
        self,
        prompt: str,
        evidence: RunEvidence,
        policy_override: Optional[WorkflowPolicy],
        output: JudgeOutput,
    ) -> JudgeOutput:
        """Use the canonical service layer (services/workflow_judge/)."""
        try:
            from ...services.workflow_judge import detect_workflow, judge_completion
            from ...services.workflow_judge.models import WorkflowKnowledge

            # Detect workflow
            if policy_override:
                wf_id = policy_override.workflow_id
                output.workflow_confidence = 1.0
            else:
                detection = detect_workflow(prompt)
                if not detection or detection.confidence < 0.3:
                    output.workflow_id = "unknown"
                    output.workflow_name = "Unrecognized workflow"
                    output.final_verdict = JudgeVerdict.FRONTIER_REQUIRED
                    output.summary = "No workflow pattern detected."
                    output.human_readable = "Detected workflow: Unknown\nVerdict: Cannot judge."
                    return output
                wf_id = detection.workflow_id
                output.workflow_confidence = detection.confidence

            # Build tool_calls list for service judge
            tool_calls = []
            for tc in evidence.tool_calls:
                tool_calls.append({
                    "tool": tc.get("tool_name", tc.get("type", "")),
                    "name": tc.get("tool_name", tc.get("type", "")),
                    "result": str(tc)[:200],
                })
            # Also include typed events as pseudo tool-calls
            for path in evidence.file_reads:
                tool_calls.append({"tool": "Read", "name": "Read", "result": path})
            for path in evidence.file_writes:
                tool_calls.append({"tool": "Write", "name": "Write", "result": path})
            for s in evidence.searches:
                tool_calls.append({"tool": "WebSearch", "name": "WebSearch", "result": str(s)[:200]})
            for t in evidence.test_runs:
                tool_calls.append({"tool": "Bash", "name": "Bash", "result": f"test: {str(t)[:200]}"})
            for ss in evidence.screenshots:
                tool_calls.append({"tool": "preview_screenshot", "name": "preview_screenshot", "result": ss})
            for a in evidence.artifacts:
                tool_calls.append({"tool": "TodoWrite", "name": "TodoWrite", "result": str(a)[:200]})

            # Run service judge
            verdict = judge_completion(wf_id, tool_calls)

            # Map service output → JudgeOutput
            output.workflow_id = verdict.workflow_id
            output.workflow_name = verdict.workflow_name or wf_id
            output.judge_model = verdict.judge_model

            for step_data in verdict.required_steps:
                status_str = step_data.get("status", "missing")
                try:
                    status = StepStatus(status_str)
                except ValueError:
                    status = StepStatus.MISSING
                output.required_steps.append(StepEvaluation(
                    step_id=step_data.get("step_id", ""),
                    step_name=step_data.get("name", ""),
                    status=status,
                    confidence=step_data.get("confidence", 0.5),
                    evidence_refs=[],
                    notes=step_data.get("notes", ""),
                ))

            for gate_id, passed in verdict.hard_gates.items():
                output.hard_gates.append(HardGateResult(
                    gate_id=gate_id, gate_name=gate_id.replace("_", " ").title(), passed=bool(passed),
                ))

            # Map verdict string
            verdict_str = verdict.verdict
            verdict_map = {
                "acceptable_replay": JudgeVerdict.ACCEPTABLE,
                "acceptable_replay_with_minor_loss": JudgeVerdict.MINOR_LOSS,
                "replay_should_have_escalated": JudgeVerdict.SHOULD_ESCALATE,
                "failed_replay": JudgeVerdict.FAILED,
                "frontier_required": JudgeVerdict.FRONTIER_REQUIRED,
            }
            output.final_verdict = verdict_map.get(verdict_str, JudgeVerdict.SHOULD_ESCALATE)
            output.confidence = verdict.confidence
            output.summary = verdict.summary

            # Nudges
            if verdict.nudge_message:
                for line in verdict.nudge_message.split("\n"):
                    line = line.strip()
                    if line:
                        level = NudgeLevel.BLOCK if "BLOCK" in line else NudgeLevel.STRONG if "MISSING" in line else NudgeLevel.SOFT
                        output.nudges.append(NudgeMessage(level=level, message=line))

            # Human readable
            done = [s.step_name for s in output.required_steps if s.status == StepStatus.DONE]
            missing = [s.step_name for s in output.required_steps if s.status == StepStatus.MISSING]
            lines = [f"Detected workflow: {output.workflow_name}"]
            if done:
                lines.append(f"Done: {', '.join(done)}")
            if missing:
                lines.append(f"Missing: {', '.join(missing)}")
            lines.append(f"Verdict: {output.final_verdict.value}")
            if output.nudges:
                lines.append("Nudges:")
                for n in output.nudges:
                    lines.append(f"  [{n.level.value}] {n.message}")
            output.human_readable = "\n".join(lines)

            return output

        except ImportError:
            logger.warning("services.workflow_judge not available — falling back to policy-based judge")
            return self._judge_policy_fallback(prompt, evidence, policy_override, output)

    def _judge_policy_fallback(
        self,
        prompt: str,
        evidence: RunEvidence,
        policy_override: Optional[WorkflowPolicy],
        output: JudgeOutput,
    ) -> JudgeOutput:
        """Fallback: use Peer B's workflow_policy.py when service layer unavailable."""
        if policy_override:
            policy = policy_override
            output.workflow_confidence = 1.0
        else:
            policy = detect_workflow_heuristic(prompt)
            if not policy:
                output.workflow_id = "unknown"
                output.final_verdict = JudgeVerdict.FRONTIER_REQUIRED
                output.summary = "No workflow pattern detected."
                output.human_readable = "Detected workflow: Unknown\nVerdict: Cannot judge."
                return output
            output.workflow_confidence = 0.8

        output.workflow_id = policy.workflow_id
        output.workflow_name = policy.name

        # Simple evidence matching per step
        for step in policy.required_steps:
            has_evidence = False
            for rule in step.evidence_rules:
                pattern = rule.pattern.lower() if rule.pattern else ""
                if rule.evidence_type == "search" and evidence.searches:
                    has_evidence = True
                elif rule.evidence_type == "file_read" and evidence.file_reads:
                    has_evidence = True
                elif rule.evidence_type == "file_write" and evidence.file_writes:
                    has_evidence = True
                elif rule.evidence_type == "tool_call" and pattern:
                    for tc in evidence.tool_calls:
                        if re.search(pattern, str(tc).lower()):
                            has_evidence = True
                            break
                elif rule.evidence_type == "artifact" and evidence.artifacts:
                    has_evidence = True

            output.required_steps.append(StepEvaluation(
                step_id=step.step_id,
                step_name=step.name,
                status=StepStatus.DONE if has_evidence else StepStatus.MISSING,
                confidence=0.7 if has_evidence else 0.85,
            ))

        done = sum(1 for s in output.required_steps if s.status == StepStatus.DONE)
        total = len(output.required_steps) or 1
        ratio = done / total
        if ratio >= 1.0:
            output.final_verdict = JudgeVerdict.ACCEPTABLE
        elif ratio >= 0.8:
            output.final_verdict = JudgeVerdict.MINOR_LOSS
        elif ratio >= 0.5:
            output.final_verdict = JudgeVerdict.SHOULD_ESCALATE
        else:
            output.final_verdict = JudgeVerdict.FAILED
        output.confidence = ratio
        output.summary = f"{done}/{total} steps done. Verdict: {output.final_verdict.value}"
        output.human_readable = output.summary
        return output

    # ── LLM mode: use llm_workflow_classifier ──

    def _judge_llm(
        self,
        prompt: str,
        evidence: RunEvidence,
        policy_override: Optional[WorkflowPolicy],
        model: str,
        output: JudgeOutput,
    ) -> JudgeOutput:
        """Full LLM judge — uses real API calls for detection + evaluation."""
        from .llm_workflow_classifier import classify_workflow, judge_completion

        # Detect workflow
        if policy_override:
            wf_id = policy_override.workflow_id
            wf_name = policy_override.name
            output.workflow_confidence = 1.0
            required_steps = [
                {"step_id": s.step_id, "name": s.name, "description": s.description}
                for s in policy_override.required_steps
            ]
        else:
            detection = classify_workflow(prompt, model=model.replace("mini", "nano"))
            wf_id = detection.get("workflow_id", "unknown")
            output.workflow_confidence = detection.get("confidence", 0.0)
            if wf_id == "unknown" or output.workflow_confidence < 0.3:
                output.workflow_id = "unknown"
                output.final_verdict = JudgeVerdict.FRONTIER_REQUIRED
                output.summary = "LLM classifier could not match a workflow."
                output.human_readable = "Detected workflow: Unknown (LLM)\nVerdict: Cannot judge."
                output.judge_model = model
                return output
            policy = load_policy(wf_id)
            wf_name = policy.name if policy else wf_id
            required_steps = [
                {"step_id": s.step_id, "name": s.name, "description": s.description}
                for s in policy.required_steps
            ] if policy else []

        output.workflow_id = wf_id
        output.workflow_name = wf_name

        # Run LLM completion judge
        result = judge_completion(
            workflow_id=wf_id,
            workflow_name=wf_name,
            required_steps=required_steps,
            evidence_events=evidence.raw_events,
            model=model,
        )

        output.judge_model = result.get("judge_model", model)
        for step_data in result.get("steps", []):
            try:
                status = StepStatus(step_data.get("status", "missing"))
            except ValueError:
                status = StepStatus.MISSING
            output.required_steps.append(StepEvaluation(
                step_id=step_data.get("step_id", ""),
                step_name=step_data.get("step_name", ""),
                status=status,
                confidence=step_data.get("confidence", 0.5),
                evidence_refs=[step_data.get("evidence", "")],
                notes=step_data.get("reasoning", ""),
            ))

        for gate_id, passed in result.get("hard_gates", {}).items():
            output.hard_gates.append(HardGateResult(
                gate_id=gate_id, gate_name=gate_id.replace("_", " ").title(), passed=bool(passed),
            ))

        output.scores = result.get("scores", {})
        verdict_str = result.get("verdict", "replay_should_have_escalated")
        try:
            output.final_verdict = JudgeVerdict(verdict_str)
        except ValueError:
            output.final_verdict = JudgeVerdict.SHOULD_ESCALATE
        output.confidence = result.get("confidence", 0.5)
        output.summary = result.get("summary", "")

        for nudge_data in result.get("nudges", []):
            try:
                level = NudgeLevel(nudge_data.get("level", "strong"))
            except ValueError:
                level = NudgeLevel.STRONG
            output.nudges.append(NudgeMessage(level=level, message=nudge_data.get("message", "")))

        done = [s.step_name for s in output.required_steps if s.status == StepStatus.DONE]
        missing = [s.step_name for s in output.required_steps if s.status == StepStatus.MISSING]
        lines = [f"Detected workflow: {wf_name} [LLM judge: {model}]"]
        if done:
            lines.append(f"Done: {', '.join(done)}")
        if missing:
            lines.append(f"Missing: {', '.join(missing)}")
        lines.append(f"Verdict: {output.final_verdict.value}")
        if output.nudges:
            lines.append("Nudges:")
            for n in output.nudges:
                lines.append(f"  [{n.level.value}] {n.message}")
        output.human_readable = "\n".join(lines)

        return output
