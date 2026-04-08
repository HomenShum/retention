"""
Completion Gate — hook enforcement that blocks false completion.

Attack angle: Claude Code can stop. TA decides whether it is ALLOWED to stop.

Integrates with Claude Code hook events:
  - UserPromptSubmit: detect workflow, retrieve policy, inject checklist
  - PostToolUse: update evidence graph, check nudges
  - Stop: run completion judge, BLOCK if critical evidence missing
  - SessionStart: hydrate retained workflow state

This module provides the enforcement logic that hooks call.
The actual hook wiring depends on the runtime (Claude Code hooks,
MCP tool wrappers, or FastAPI middleware).
"""

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .workflow_judge import JudgeOutput, JudgeVerdict, RunEvidence, WorkflowJudge, collect_evidence_from_events
from .workflow_policy import NudgeLevel, WorkflowPolicy, detect_workflow, load_policy

logger = logging.getLogger(__name__)

_GATE_DIR = Path(__file__).resolve().parents[3] / "data" / "completion_gates"
_GATE_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Gate decision
# ---------------------------------------------------------------------------

class GateDecision(str, Enum):
    ALLOW = "allow"           # All required steps evidenced — agent may stop
    NUDGE = "nudge"           # Missing steps detected — warn but allow
    BLOCK = "block"           # Critical steps missing — do not allow stop
    ESCALATE = "escalate"     # Needs stronger model or human review


class CompletionGateResult(BaseModel):
    """Result of a completion gate check."""
    decision: GateDecision = GateDecision.ALLOW
    workflow_id: str = ""
    workflow_name: str = ""
    judge_output: Optional[JudgeOutput] = None
    missing_steps: List[str] = Field(default_factory=list)
    nudge_messages: List[str] = Field(default_factory=list)
    block_reason: str = ""
    human_readable: str = ""
    checked_at: str = ""


# ---------------------------------------------------------------------------
# Side-effect registry (Attack angle 1)
# ---------------------------------------------------------------------------

class SideEffect(BaseModel):
    """A tracked side effect from agent execution."""
    effect_id: str = ""
    effect_type: str = ""       # "file_write", "api_call", "message_send", "deploy", "git_push"
    target: str = ""            # file path, URL, recipient, etc.
    reversible: bool = True
    executed: bool = False
    timestamp: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)


class WorkflowState(BaseModel):
    """Workflow state tracked separately from conversation state.

    This is the canonical answer to: "what has actually been DONE,
    what is PENDING, and what must NOT be repeated?"
    """
    session_id: str = ""
    workflow_id: str = ""
    workflow_name: str = ""

    # Step tracking
    steps_done: List[str] = Field(default_factory=list)
    steps_pending: List[str] = Field(default_factory=list)
    steps_skipped: List[str] = Field(default_factory=list)

    # Side effects — what the agent actually DID in the world
    side_effects: List[SideEffect] = Field(default_factory=list)

    # Evidence collected so far
    evidence_events: List[Dict[str, Any]] = Field(default_factory=list)

    # Nudges issued
    nudges_issued: List[str] = Field(default_factory=list)

    # Gate decisions
    gate_checks: int = 0
    last_gate_decision: str = ""

    # Timestamps
    started_at: str = ""
    last_updated: str = ""

    # Completion
    completed: bool = False
    final_verdict: str = ""


# ---------------------------------------------------------------------------
# Completion Gate Engine
# ---------------------------------------------------------------------------

class CompletionGate:
    """The stop-gate that decides if an agent is allowed to stop.

    Usage in hook integration:

        gate = CompletionGate()

        # On UserPromptSubmit
        state = gate.on_prompt_submit(prompt, session_id)

        # On PostToolUse (called after every tool)
        gate.on_tool_use(state, tool_name, tool_result)

        # On Stop (called when agent tries to stop)
        result = gate.on_stop(state)
        if result.decision == GateDecision.BLOCK:
            # Inject nudge message back into agent context
            return {"block": True, "message": result.human_readable}
    """

    def __init__(self, use_llm: bool = False, llm_model: str = "gpt-5.4-mini"):
        """
        Args:
            use_llm: If True, use LLM for workflow detection and completion judging.
                     If False, use fast heuristic mode (for tests and offline).
            llm_model: Model for LLM judge calls.
        """
        self._judge = WorkflowJudge()
        self._use_llm = use_llm
        self._llm_model = llm_model
        self._active_states: Dict[str, WorkflowState] = {}

    def on_prompt_submit(self, prompt: str, session_id: str = "") -> WorkflowState:
        """Hook: UserPromptSubmit — detect workflow, initialize state."""
        if self._use_llm:
            from .llm_workflow_classifier import classify_workflow
            result = classify_workflow(prompt, model=self._llm_model.replace("mini", "nano"))
            wf_id = result.get("workflow_id", "unknown")
            policy = load_policy(wf_id) if wf_id != "unknown" else None
        else:
            policy = detect_workflow(prompt)

        state = WorkflowState(
            session_id=session_id,
            started_at=_now_iso(),
            last_updated=_now_iso(),
        )

        if policy:
            state.workflow_id = policy.workflow_id
            state.workflow_name = policy.name
            state.steps_pending = [s.step_id for s in policy.required_steps]
            logger.info(f"CompletionGate: detected {policy.workflow_id}, {len(policy.required_steps)} required steps")
        else:
            logger.debug("CompletionGate: no workflow detected, running in passthrough mode")

        self._active_states[session_id] = state
        return state

    def on_tool_use(
        self,
        state: WorkflowState,
        tool_name: str,
        tool_result: Optional[Dict[str, Any]] = None,
        file_path: str = "",
    ) -> Optional[str]:
        """Hook: PostToolUse — update evidence, track side effects, check for nudges.

        Returns a nudge message string if a soft nudge should be shown, else None.
        """
        # Emit the right event type for evidence routing
        tool_lower = tool_name.lower()
        if tool_lower in ("read", "cat", "head"):
            evt_type = "file_read"
        elif tool_lower in ("write", "edit"):
            evt_type = "file_write"
        elif tool_lower in ("web_search", "websearch"):
            evt_type = "web_search"
        elif tool_lower in ("fetch", "webfetch"):
            evt_type = "fetch"
        elif "screenshot" in tool_lower or "preview" in tool_lower:
            evt_type = tool_lower
        elif "test" in tool_lower or "pytest" in tool_lower or "lint" in tool_lower:
            evt_type = "test"
        elif tool_lower in ("artifact", "summary", "report"):
            evt_type = "artifact"
        else:
            evt_type = "tool_call"

        event = {
            "type": evt_type,
            "tool_name": tool_name,
            "timestamp": _now_iso(),
        }
        if tool_result:
            event.update(tool_result)
        if file_path:
            event["path"] = file_path
            event["file"] = file_path

        state.evidence_events.append(event)

        # Track side effects for destructive operations
        if tool_name in ("write", "edit", "bash") and file_path:
            state.side_effects.append(SideEffect(
                effect_id=f"se-{len(state.side_effects)}",
                effect_type="file_write",
                target=file_path,
                reversible=True,
                executed=True,
                timestamp=_now_iso(),
            ))

        state.last_updated = _now_iso()

        # Quick check: if workflow detected and we're past step 3, nudge if search missing
        if state.workflow_id and len(state.evidence_events) > 5:
            evidence = collect_evidence_from_events(state.evidence_events)
            if not evidence.searches and "latest_search" in state.steps_pending:
                msg = f"[soft nudge] You usually do a latest-industry search in {state.workflow_name}. None detected yet."
                if msg not in state.nudges_issued:
                    state.nudges_issued.append(msg)
                    return msg

        return None

    def on_stop(self, state: WorkflowState) -> CompletionGateResult:
        """Hook: Stop — run the full completion judge and decide allow/block.

        This is the critical gate. If required steps are missing,
        the agent should NOT be allowed to stop.
        """
        state.gate_checks += 1
        result = CompletionGateResult(checked_at=_now_iso())

        # No workflow detected — passthrough
        if not state.workflow_id:
            result.decision = GateDecision.ALLOW
            result.human_readable = "No workflow policy detected — allowing completion."
            state.last_gate_decision = "allow"
            return result

        # Run the full judge
        policy = load_policy(state.workflow_id)
        if not policy:
            result.decision = GateDecision.ALLOW
            result.human_readable = f"Policy {state.workflow_id} not found — allowing completion."
            state.last_gate_decision = "allow"
            return result

        evidence = collect_evidence_from_events(state.evidence_events)
        judge_output = self._judge.judge(
            prompt=f"[completion gate check #{state.gate_checks}]",
            evidence=evidence,
            policy_override=policy,
            use_llm=self._use_llm,
            llm_model=self._llm_model,
        )

        result.workflow_id = state.workflow_id
        result.workflow_name = state.workflow_name
        result.judge_output = judge_output

        # Collect missing steps
        missing = [s.step_name for s in judge_output.required_steps if s.status.value == "missing"]
        result.missing_steps = missing

        # Collect nudge messages
        result.nudge_messages = [n.message for n in judge_output.nudges]

        # Decide gate
        verdict = judge_output.final_verdict

        if verdict == JudgeVerdict.ACCEPTABLE:
            result.decision = GateDecision.ALLOW
            result.human_readable = f"All required steps for {state.workflow_name} are evidenced. Completion allowed."
            state.completed = True
            state.final_verdict = "acceptable"

        elif verdict == JudgeVerdict.MINOR_LOSS:
            result.decision = GateDecision.NUDGE
            result.human_readable = (
                f"Nearly complete, but minor gaps in {state.workflow_name}:\n"
                + "\n".join(f"  - {m}" for m in missing)
                + "\nCompletion allowed with warning."
            )
            state.final_verdict = "minor_loss"

        elif verdict == JudgeVerdict.SHOULD_ESCALATE:
            result.decision = GateDecision.BLOCK
            result.block_reason = f"{len(missing)} required steps missing"
            result.human_readable = (
                f"BLOCKED: Cannot mark {state.workflow_name} as complete.\n"
                f"Missing required steps:\n"
                + "\n".join(f"  - {m}" for m in missing)
                + "\n\nComplete these steps before stopping."
            )
            state.last_gate_decision = "block"

        elif verdict in (JudgeVerdict.FAILED, JudgeVerdict.FRONTIER_REQUIRED):
            result.decision = GateDecision.ESCALATE
            result.block_reason = f"Workflow {state.workflow_name} needs escalation"
            result.human_readable = (
                f"ESCALATION REQUIRED: {state.workflow_name} cannot be completed at current tier.\n"
                f"Missing: {', '.join(missing)}\n"
                f"Recommend: escalate to frontier model or request human review."
            )
            state.last_gate_decision = "escalate"

        # Persist state
        self._save_state(state)
        return result

    def get_state(self, session_id: str) -> Optional[WorkflowState]:
        """Get the current workflow state for a session."""
        return self._active_states.get(session_id)

    def on_session_start(self, session_id: str) -> Optional[WorkflowState]:
        """Hook: SessionStart — hydrate retained workflow state from disk."""
        path = _GATE_DIR / f"{session_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                state = WorkflowState(**data)
                self._active_states[session_id] = state
                logger.info(f"Hydrated workflow state for {session_id}: {state.workflow_id}, {len(state.steps_pending)} steps pending")
                return state
            except Exception as e:
                logger.warning(f"Failed to hydrate state for {session_id}: {e}")
        return None

    def _save_state(self, state: WorkflowState) -> None:
        """Persist workflow state to disk for cross-session continuity."""
        if state.session_id:
            path = _GATE_DIR / f"{state.session_id}.json"
            path.write_text(json.dumps(state.model_dump(), indent=2, default=str))


# ---------------------------------------------------------------------------
# ROP Export/Import for cross-session portability (Attack angle 8)
# ---------------------------------------------------------------------------

class ROPPackage(BaseModel):
    """Portable workflow package that survives session boundaries.

    Contains everything needed to resume a workflow in a new session:
    workflow state, policy, evidence, trajectory, and side effects.
    """
    package_version: str = "1.0"
    exported_at: str = ""
    exported_from_session: str = ""

    # Workflow identity
    workflow_id: str = ""
    workflow_name: str = ""
    rop_id: str = ""

    # State
    workflow_state: Optional[WorkflowState] = None
    policy_snapshot: Optional[Dict[str, Any]] = None

    # Trajectory
    trajectory_id: str = ""
    trajectory_steps: List[Dict[str, Any]] = Field(default_factory=list)

    # Checkpoints
    checkpoints: List[Dict[str, Any]] = Field(default_factory=list)

    # Evidence summary (not full events — too large)
    evidence_summary: Dict[str, Any] = Field(default_factory=dict)

    # Cost metrics
    cost_metrics: Dict[str, Any] = Field(default_factory=dict)

    # Metadata
    repo_url: str = ""
    branch: str = ""
    app_key: str = ""


def export_rop_package(
    rop_id: str,
    session_id: str = "",
    include_trajectory: bool = True,
) -> Optional[ROPPackage]:
    """Export an ROP as a portable package for cross-session use."""
    from .rop_manager import ROPManager

    mgr = ROPManager()
    rop = mgr.get_rop(rop_id)
    if not rop:
        return None

    # Load policy
    policy = load_policy(rop.workflow_id)
    policy_snapshot = policy.model_dump() if policy else {}

    # Load workflow state if available
    gate = CompletionGate()
    state = gate.on_session_start(session_id) if session_id else None

    # Load trajectory steps if requested
    traj_steps = []
    if include_trajectory and rop.origin_trajectory_id:
        try:
            from ..device_testing.trajectory_logger import get_trajectory_logger
            tl = get_trajectory_logger()
            traj = tl.load_trajectory(rop.workflow_id, rop.origin_trajectory_id)
            if traj:
                from dataclasses import asdict
                traj_steps = [asdict(s) for s in traj.steps]
        except Exception as e:
            logger.warning(f"Could not load trajectory for export: {e}")

    package = ROPPackage(
        exported_at=_now_iso(),
        exported_from_session=session_id,
        workflow_id=rop.workflow_id,
        workflow_name=rop.workflow_name,
        rop_id=rop.rop_id,
        workflow_state=state,
        policy_snapshot=policy_snapshot,
        trajectory_id=rop.origin_trajectory_id,
        trajectory_steps=traj_steps,
        checkpoints=[cp.model_dump() for cp in rop.checkpoints],
        evidence_summary={
            "replay_count": rop.replay_count,
            "replay_success_count": rop.replay_success_count,
            "escalation_count": rop.escalation_count,
        },
        cost_metrics=rop.cost_metrics.model_dump(),
        app_key=rop.app_key,
    )

    # Save to disk
    export_dir = _GATE_DIR / "exports"
    export_dir.mkdir(exist_ok=True)
    path = export_dir / f"{rop_id}_package.json"
    path.write_text(json.dumps(package.model_dump(), indent=2, default=str))
    logger.info(f"Exported ROP package: {path.name}")

    return package


def import_rop_package(package_path: str) -> Optional[ROPPackage]:
    """Import an ROP package from a file."""
    path = Path(package_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return ROPPackage(**data)
    except Exception as e:
        logger.error(f"Failed to import ROP package: {e}")
        return None
