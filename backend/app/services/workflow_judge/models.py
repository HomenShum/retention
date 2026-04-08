"""
Workflow Knowledge Object — the first-class retained workflow.

Each workflow stores identity, mandatory steps, evidence requirements,
common misses, completion policy, and escalation rules. This replaces
static rules files with executable, evidence-backed policy.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_WORKFLOW_DIR = _DATA_DIR / "workflow_knowledge"
_WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)


# ─── Enums ──────────────────────────────────────────────────────────────

class StepStatus(str, Enum):
    DONE = "done"
    PARTIAL = "partial"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"
    AMBIGUOUS = "ambiguous"


class VerdictClass(str, Enum):
    ACCEPTABLE = "acceptable_replay"
    MINOR_LOSS = "acceptable_replay_with_minor_loss"
    SHOULD_ESCALATE = "replay_should_have_escalated"
    FAILED = "failed_replay"
    FRONTIER_REQUIRED = "frontier_required"


class NudgeLevel(str, Enum):
    SOFT = "soft"       # "you usually do X before closing"
    STRONG = "strong"   # "required step missing — no evidence found"
    BLOCK = "block"     # "cannot mark complete — mandatory step has no evidence"


# ─── Step and Evidence ──────────────────────────────────────────────────

@dataclass
class StepEvidence:
    """Proof that a workflow step actually happened."""
    evidence_type: str = ""  # "tool_call", "file_write", "search_result", "screenshot", "test_run", "artifact"
    evidence_ref: str = ""   # ID or path to the evidence
    content_preview: str = ""  # First 200 chars of evidence content
    timestamp: str = ""
    confidence: float = 0.0


@dataclass
class WorkflowStep:
    """A single step in a retained workflow."""
    step_id: str
    name: str
    description: str = ""
    mandatory: bool = True
    evidence_types: List[str] = field(default_factory=list)  # What counts as evidence
    common_tool_calls: List[str] = field(default_factory=list)  # Tools usually used
    common_misses: List[str] = field(default_factory=list)  # What the model usually forgets
    order: int = 0  # Preferred execution order

    # Runtime state (populated during judging)
    status: StepStatus = StepStatus.MISSING
    evidence: List[StepEvidence] = field(default_factory=list)
    confidence: float = 0.0
    notes: str = ""


# ─── Workflow Knowledge ─────────────────────────────────────────────────

@dataclass
class WorkflowKnowledge:
    """First-class retained workflow object.

    Replaces static rules files with executable, evidence-backed policy.
    """
    # Identity
    workflow_id: str = ""
    name: str = ""
    family: str = ""  # "dev", "qa", "research", "pr", "deploy"
    aliases: List[str] = field(default_factory=list)  # "flywheel", "full pass", etc.
    trigger_phrases: List[str] = field(default_factory=list)  # Natural language triggers
    version: int = 1

    # Intent
    description: str = ""
    outcome: str = ""  # What success looks like

    # Steps
    required_steps: List[WorkflowStep] = field(default_factory=list)
    optional_steps: List[WorkflowStep] = field(default_factory=list)

    # Policy
    completion_policy: str = ""  # Human-readable completion criteria
    escalation_policy: str = ""  # When to escalate
    style_preferences: str = ""  # How summaries/PRs should be written

    # Learning
    total_runs: int = 0
    total_corrections: int = 0  # Times user said "you forgot X"
    correction_rate: float = 0.0
    last_run_at: str = ""
    promoted_from: str = ""  # "manual" or "auto_detected"
    confidence: float = 0.0  # 0-1, how well-established this workflow is

    # Metadata
    created_at: str = ""
    updated_at: str = ""
    created_by: str = ""

    def save(self) -> None:
        """Persist to disk."""
        self.updated_at = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = self.updated_at
        if not self.workflow_id:
            self.workflow_id = f"wf-{uuid.uuid4().hex[:8]}"
        path = _WORKFLOW_DIR / f"{self.workflow_id}.json"
        path.write_text(json.dumps(asdict(self), indent=2, default=str))

    @classmethod
    def load(cls, workflow_id: str) -> Optional[WorkflowKnowledge]:
        path = _WORKFLOW_DIR / f"{workflow_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            wf = cls()
            for k, v in data.items():
                if hasattr(wf, k):
                    if k == "required_steps":
                        wf.required_steps = [WorkflowStep(**s) if isinstance(s, dict) else s for s in v]
                    elif k == "optional_steps":
                        wf.optional_steps = [WorkflowStep(**s) if isinstance(s, dict) else s for s in v]
                    else:
                        setattr(wf, k, v)
            return wf
        except Exception:
            return None

    @classmethod
    def list_all(cls) -> List[Dict[str, Any]]:
        results = []
        for f in _WORKFLOW_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                results.append({
                    "workflow_id": data.get("workflow_id"),
                    "name": data.get("name"),
                    "family": data.get("family"),
                    "aliases": data.get("aliases", []),
                    "total_runs": data.get("total_runs", 0),
                    "confidence": data.get("confidence", 0),
                    "version": data.get("version", 1),
                })
            except Exception:
                continue
        return sorted(results, key=lambda x: x.get("total_runs", 0), reverse=True)


# ─── Judge Verdict ──────────────────────────────────────────────────────

@dataclass
class JudgeVerdict:
    """Complete verdict from the workflow judge."""
    # Workflow identification
    workflow_id: str = ""
    workflow_name: str = ""
    workflow_confidence: float = 0.0

    # Step-level scoring (list of dicts with step_id, name, status, confidence, evidence_count, notes)
    required_steps: List[Dict[str, Any]] = field(default_factory=list)
    steps_done: int = 0
    steps_partial: int = 0
    steps_missing: int = 0

    # Hard gates
    hard_gates: Dict[str, bool] = field(default_factory=dict)
    all_gates_pass: bool = False

    # Soft scores (1-5)
    scores: Dict[str, int] = field(default_factory=dict)

    # Verdict
    verdict: str = ""  # VerdictClass value
    confidence: float = 0.0
    summary: str = ""

    # Nudge
    nudge_level: str = ""  # NudgeLevel value
    nudge_message: str = ""
    missing_steps: List[str] = field(default_factory=list)

    # Metadata
    judge_model: str = ""
    judge_source: str = ""  # "workflow_judge", "strict_llm", "formula"
    timestamp: str = ""
    duration_ms: int = 0

    @property
    def step_results(self) -> List[Dict[str, Any]]:
        """Alias for required_steps — consistent field access."""
        return self.required_steps


# ─── Built-in workflow templates ────────────────────────────────────────

def create_dev_flywheel() -> WorkflowKnowledge:
    """The dev.flywheel workflow — the canonical recurring dev workflow."""
    return WorkflowKnowledge(
        workflow_id="dev.flywheel.v3",
        name="Development Flywheel",
        family="dev",
        aliases=["flywheel", "full pass", "do everything", "the whole thing"],
        trigger_phrases=[
            "flywheel this",
            "do the full flywheel",
            "full dev pass",
            "do everything",
            "ship this properly",
            "the whole thing",
        ],
        description="Fully implement, validate, and package a change according to recurring development standards.",
        outcome="All impacted surfaces touched, verified, QA'd, and packaged for PR/deploy.",
        required_steps=[
            WorkflowStep(
                step_id="understand_plan",
                name="Understand the plan",
                description="Read and internalize all requirements before coding",
                evidence_types=["plan_summary", "todo_list"],
                order=1,
            ),
            WorkflowStep(
                step_id="inspect_surfaces",
                name="Inspect impacted surfaces",
                description="Map all files, directories, and surfaces that will be affected",
                evidence_types=["file_read", "grep_search", "glob_search"],
                common_tool_calls=["Read", "Grep", "Glob"],
                common_misses=["missed a dependent file", "forgot to check tests directory"],
                order=2,
            ),
            WorkflowStep(
                step_id="latest_search",
                name="Latest industry/context search",
                description="Search for latest relevant info if external context matters",
                evidence_types=["web_search", "web_fetch"],
                common_tool_calls=["WebSearch", "WebFetch"],
                common_misses=["skipped search entirely", "searched but didn't use results"],
                order=3,
            ),
            WorkflowStep(
                step_id="implement",
                name="Implement across all layers",
                description="Write code across frontend, backend, schema, tests — all impacted layers",
                evidence_types=["file_write", "file_edit"],
                common_tool_calls=["Write", "Edit"],
                common_misses=["forgot one layer", "didn't update types", "missed schema migration"],
                order=4,
            ),
            WorkflowStep(
                step_id="interactive_audit",
                name="Review all interactive components",
                description="Check all clickable, interactive, and user-facing elements",
                evidence_types=["preview_screenshot", "preview_snapshot", "preview_click"],
                common_tool_calls=["preview_start", "preview_screenshot", "preview_snapshot", "preview_click"],
                common_misses=["skipped preview entirely", "only checked one page"],
                order=5,
            ),
            WorkflowStep(
                step_id="verify",
                name="Run verification",
                description="Run tests, typecheck, lint, or manual verification",
                evidence_types=["bash_test", "bash_lint", "bash_typecheck"],
                common_tool_calls=["Bash"],
                common_misses=["forgot to run tests", "ran tests but ignored failures"],
                order=6,
            ),
            WorkflowStep(
                step_id="pr_summary",
                name="Produce PR-ready summary",
                description="Generate a summary of what changed, why, and what to test",
                evidence_types=["summary_text", "commit_message"],
                common_misses=["no summary provided", "summary too vague"],
                order=7,
            ),
        ],
        optional_steps=[
            WorkflowStep(
                step_id="benchmark",
                name="Run benchmarks",
                description="Run performance or quality benchmarks if applicable",
                mandatory=False,
                evidence_types=["benchmark_result"],
                order=8,
            ),
        ],
        completion_policy="Do NOT mark done unless steps 1-7 all have evidence or are explicitly waived by user.",
        escalation_policy="If any required step has no evidence after implementation, emit L2 nudge. If user says 'done' but evidence is missing, emit L3 block.",
        style_preferences="Summaries should be concise with bullet points. PRs should follow conventional commits.",
    )


def create_qa_audit() -> WorkflowKnowledge:
    """The qa.interactive_surface_audit workflow."""
    return WorkflowKnowledge(
        workflow_id="qa.interactive_surface_audit.v2",
        name="Interactive Surface QA Audit",
        family="qa",
        aliases=["qa pass", "full QA", "check everything", "audit the UI"],
        trigger_phrases=[
            "QA this",
            "do a full QA pass",
            "check all interactive elements",
            "audit the UI",
            "test all clickable components",
        ],
        description="Systematically review all interactive UI elements across all pages.",
        outcome="Every clickable, interactive component verified with screenshots and error checks.",
        required_steps=[
            WorkflowStep(
                step_id="start_preview",
                name="Start dev server",
                evidence_types=["preview_start"],
                common_tool_calls=["preview_start"],
                order=1,
            ),
            WorkflowStep(
                step_id="navigate_all_pages",
                name="Navigate to all pages",
                evidence_types=["preview_screenshot", "navigate"],
                order=2,
            ),
            WorkflowStep(
                step_id="check_interactive",
                name="Click/interact with all components",
                evidence_types=["preview_click", "preview_fill"],
                common_misses=["only checked main page", "didn't test form submissions"],
                order=3,
            ),
            WorkflowStep(
                step_id="check_console",
                name="Check for console errors",
                evidence_types=["preview_console_logs"],
                common_tool_calls=["preview_console_logs"],
                order=4,
            ),
            WorkflowStep(
                step_id="report_findings",
                name="Report findings",
                evidence_types=["summary_text"],
                order=5,
            ),
        ],
        completion_policy="Must have screenshots from all pages and console error check.",
    )


def create_research_refresh() -> WorkflowKnowledge:
    """The drx.latest_industry_refresh workflow."""
    return WorkflowKnowledge(
        workflow_id="drx.latest_industry_refresh.v1",
        name="Latest Industry Research Refresh",
        family="research",
        aliases=["latest search", "industry update", "research refresh"],
        trigger_phrases=[
            "search latest",
            "do a latest industry sweep",
            "research update",
            "what's new in",
            "latest on",
        ],
        description="Perform a comprehensive search for the latest developments in a topic area.",
        outcome="Structured summary of latest findings with sources, key claims, and actionable insights.",
        required_steps=[
            WorkflowStep(
                step_id="define_scope",
                name="Define search scope",
                evidence_types=["plan_summary"],
                order=1,
            ),
            WorkflowStep(
                step_id="web_search",
                name="Execute broad web search",
                evidence_types=["web_search"],
                common_tool_calls=["WebSearch"],
                common_misses=["too narrow search", "only one query"],
                order=2,
            ),
            WorkflowStep(
                step_id="deep_read",
                name="Read and extract from top sources",
                evidence_types=["web_fetch"],
                common_tool_calls=["WebFetch"],
                common_misses=["skipped reading actual sources"],
                order=3,
            ),
            WorkflowStep(
                step_id="synthesize",
                name="Synthesize findings",
                evidence_types=["summary_text"],
                common_misses=["just listed links, no synthesis"],
                order=4,
            ),
        ],
        completion_policy="Must have at least 3 sources read and a structured synthesis.",
    )


def seed_builtin_workflows() -> List[WorkflowKnowledge]:
    """Create and save all built-in workflow templates."""
    workflows = [
        create_dev_flywheel(),
        create_qa_audit(),
        create_research_refresh(),
    ]
    for wf in workflows:
        wf.save()
    return workflows
