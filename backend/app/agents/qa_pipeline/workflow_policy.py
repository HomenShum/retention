"""
Workflow Policy — retained workflow knowledge objects.

Each policy defines what a recurring workflow requires:
  - mandatory steps with evidence rules
  - optional steps
  - hard gates (must-not-fail checks)
  - escalation triggers
  - completion criteria
  - trigger phrases (natural language → workflow ID)

Policies are learned from observed behavior and user corrections,
not hardcoded in rules files.

Storage: JSON files in backend/data/workflow_policies/
"""

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_POLICY_DIR = Path(__file__).resolve().parents[3] / "data" / "workflow_policies"
_POLICY_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class StepStatus(str, Enum):
    DONE = "done"
    PARTIAL = "partial"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"
    AMBIGUOUS = "ambiguous"


class NudgeLevel(str, Enum):
    SOFT = "soft"       # "you usually do X before closing"
    STRONG = "strong"   # "required step missing; no evidence found"
    BLOCK = "block"     # "cannot mark complete — mandatory step has no evidence"


class JudgeVerdict(str, Enum):
    ACCEPTABLE = "acceptable_replay"
    MINOR_LOSS = "acceptable_replay_with_minor_loss"
    SHOULD_ESCALATE = "replay_should_have_escalated"
    FAILED = "failed_replay"
    FRONTIER_REQUIRED = "frontier_required"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class EvidenceRule(BaseModel):
    """What counts as evidence for a step."""
    evidence_type: str              # "tool_call", "file_write", "search", "screenshot", "test_run", "artifact"
    pattern: str = ""               # regex or keyword to match in tool/file name
    required: bool = True
    description: str = ""


class WorkflowStep(BaseModel):
    """A single step in a workflow policy."""
    step_id: str
    name: str
    description: str = ""
    required: bool = True           # mandatory vs optional
    evidence_rules: List[EvidenceRule] = Field(default_factory=list)
    common_misses: List[str] = Field(default_factory=list)  # what the model usually forgets
    order_hint: int = 0             # preferred execution order


class HardGate(BaseModel):
    """A must-not-fail check."""
    gate_id: str
    name: str
    description: str = ""
    check_type: str = "evidence"    # "evidence", "no_fabrication", "surface_coverage", "contract"


class EscalationTrigger(BaseModel):
    """When to escalate to a stronger model or human."""
    trigger_id: str
    condition: str                  # human-readable condition
    severity: NudgeLevel = NudgeLevel.STRONG


class WorkflowPolicy(BaseModel):
    """A retained workflow knowledge object.

    Learned from observed behavior, not hardcoded.
    """
    # Identity
    workflow_id: str                # e.g. "dev.flywheel.v3"
    name: str                       # e.g. "Development Flywheel"
    family: str = ""                # "dev", "qa", "drx", "csp", "ops"
    version: int = 1
    aliases: List[str] = Field(default_factory=list)
    trigger_phrases: List[str] = Field(default_factory=list)

    # Intent
    intent: str = ""                # what outcome this workflow achieves
    typical_scope: str = ""         # "single file", "multi-directory", "cross-stack", etc.

    # Steps
    required_steps: List[WorkflowStep] = Field(default_factory=list)
    optional_steps: List[WorkflowStep] = Field(default_factory=list)

    # Gates & escalation
    hard_gates: List[HardGate] = Field(default_factory=list)
    escalation_triggers: List[EscalationTrigger] = Field(default_factory=list)

    # Completion
    completion_policy: str = ""     # human-readable completion criteria
    min_required_steps_done: float = 1.0  # fraction of required steps that must be done

    # Style
    style_preferences: Dict[str, str] = Field(default_factory=dict)

    # Metadata
    created_at: str = ""
    updated_at: str = ""
    observed_runs: int = 0          # how many times this workflow has been observed
    correction_count: int = 0       # how many times user corrected missed steps
    confidence: float = 0.0         # how confident we are this is a real pattern


# ---------------------------------------------------------------------------
# Built-in policies (seed — these get refined from real observations)
# ---------------------------------------------------------------------------

def get_dev_flywheel_policy() -> WorkflowPolicy:
    """Seed policy for the development flywheel pattern."""
    return WorkflowPolicy(
        workflow_id="dev.flywheel.v3",
        name="Development Flywheel",
        family="dev",
        version=3,
        aliases=["flywheel", "full dev cycle", "ship it properly"],
        trigger_phrases=[
            "flywheel this",
            "do the full flywheel",
            "ship this the way I usually do",
            "full dev cycle",
        ],
        intent="Fully implement, validate, and package a change according to recurring development standard",
        typical_scope="multi-directory",
        required_steps=[
            WorkflowStep(
                step_id="understand_plan",
                name="Understand the plan",
                description="Read and comprehend the full scope of requested changes",
                evidence_rules=[
                    EvidenceRule(evidence_type="file_read", pattern=".*", description="Read relevant files"),
                ],
                order_hint=1,
            ),
            WorkflowStep(
                step_id="inspect_surfaces",
                name="Inspect impacted surfaces",
                description="Map all files, directories, and components affected by the change",
                evidence_rules=[
                    EvidenceRule(evidence_type="tool_call", pattern="glob|grep|read", description="Search/read impacted files"),
                ],
                common_misses=["only checked one directory", "missed dependent layer"],
                order_hint=2,
            ),
            WorkflowStep(
                step_id="latest_search",
                name="Latest industry/context refresh",
                description="Search for latest relevant information if external context matters",
                required=True,
                evidence_rules=[
                    EvidenceRule(evidence_type="search", pattern="web_search|fetch", description="External search performed"),
                ],
                common_misses=["skipped entirely", "used stale cached knowledge"],
                order_hint=3,
            ),
            WorkflowStep(
                step_id="implement_all_layers",
                name="Implement across all impacted layers",
                description="Make changes in every affected file/directory, not just the obvious ones",
                evidence_rules=[
                    EvidenceRule(evidence_type="file_write", pattern=".*", description="Files modified"),
                ],
                common_misses=["only changed frontend", "forgot backend schema", "missed test files"],
                order_hint=4,
            ),
            WorkflowStep(
                step_id="interactive_audit",
                name="Review all interactive/QA surfaces",
                description="Check all clickable components, forms, interactive elements",
                evidence_rules=[
                    EvidenceRule(evidence_type="tool_call", pattern="screenshot|preview|inspect|test", description="Visual/interactive verification"),
                ],
                common_misses=["skipped interactive review entirely", "only checked happy path"],
                order_hint=5,
            ),
            WorkflowStep(
                step_id="verification",
                name="Run verification",
                description="Run tests, type checks, linting, or other verification",
                evidence_rules=[
                    EvidenceRule(evidence_type="tool_call", pattern="test|pytest|lint|typecheck|build", description="Verification command ran"),
                ],
                order_hint=6,
            ),
            WorkflowStep(
                step_id="pr_summary",
                name="Produce PR-ready summary",
                description="Generate a summary of changes suitable for a pull request",
                evidence_rules=[
                    EvidenceRule(evidence_type="artifact", pattern="summary|pr|commit", description="Summary produced"),
                ],
                order_hint=7,
            ),
        ],
        hard_gates=[
            HardGate(gate_id="no_false_completion", name="No false completion", description="Agent must not claim done when required steps are missing"),
            HardGate(gate_id="no_fabrication", name="No fabricated results", description="All claimed changes must exist in actual file diffs"),
            HardGate(gate_id="all_surfaces_touched", name="All impacted surfaces touched", description="Every affected layer must have evidence of changes"),
        ],
        escalation_triggers=[
            EscalationTrigger(trigger_id="missing_search", condition="latest_search step has no evidence", severity=NudgeLevel.STRONG),
            EscalationTrigger(trigger_id="missing_audit", condition="interactive_audit step has no evidence", severity=NudgeLevel.STRONG),
            EscalationTrigger(trigger_id="partial_impl", condition="implement_all_layers is partial — some layers untouched", severity=NudgeLevel.BLOCK),
        ],
        completion_policy="Do not mark done unless all 7 required steps have evidence or are explicitly waived by the user.",
        min_required_steps_done=1.0,
        created_at=_now_iso(),
        confidence=0.9,
    )


def get_qa_surface_audit_policy() -> WorkflowPolicy:
    """Seed policy for QA interactive surface audit."""
    return WorkflowPolicy(
        workflow_id="qa.interactive_surface_audit.v2",
        name="QA Interactive Surface Audit",
        family="qa",
        version=2,
        aliases=["full QA", "check everything", "audit all interactives"],
        trigger_phrases=[
            "QA this",
            "check all interactive components",
            "full QA pass",
            "audit all clickable elements",
        ],
        intent="Verify all interactive surfaces, clickable elements, and user flows work correctly",
        typical_scope="full application",
        required_steps=[
            WorkflowStep(step_id="enumerate_surfaces", name="Enumerate all interactive surfaces", order_hint=1,
                         evidence_rules=[EvidenceRule(evidence_type="tool_call", pattern="crawl|dump_ui|read_page")]),
            WorkflowStep(step_id="click_test", name="Test all clickable elements", order_hint=2,
                         evidence_rules=[EvidenceRule(evidence_type="tool_call", pattern="click|tap|interact")]),
            WorkflowStep(step_id="form_test", name="Test all form inputs", order_hint=3,
                         evidence_rules=[EvidenceRule(evidence_type="tool_call", pattern="fill|type|input")]),
            WorkflowStep(step_id="error_check", name="Check for console errors", order_hint=4,
                         evidence_rules=[EvidenceRule(evidence_type="tool_call", pattern="console|log|error")]),
            WorkflowStep(step_id="evidence_bundle", name="Produce evidence bundle", order_hint=5,
                         evidence_rules=[EvidenceRule(evidence_type="artifact", pattern="screenshot|report|bundle")]),
        ],
        hard_gates=[
            HardGate(gate_id="no_unchecked_surface", name="No unchecked interactive surface"),
        ],
        completion_policy="All interactive surfaces must have click/tap evidence. Console errors must be checked.",
        created_at=_now_iso(),
        confidence=0.85,
    )


def get_drx_refresh_policy() -> WorkflowPolicy:
    """Seed policy for DRX delta refresh."""
    return WorkflowPolicy(
        workflow_id="drx.latest_industry_refresh.v1",
        name="Latest Industry Refresh",
        family="drx",
        version=1,
        aliases=["latest search", "industry update", "deep research refresh"],
        trigger_phrases=[
            "do a latest industry sweep",
            "refresh the research",
            "what's new in this space",
            "update the market data",
        ],
        intent="Update retained research with latest industry data while preserving valid prior claims",
        required_steps=[
            WorkflowStep(step_id="load_prior", name="Load prior research from retention", order_hint=1,
                         evidence_rules=[EvidenceRule(evidence_type="tool_call", pattern="read|load|memory")]),
            WorkflowStep(step_id="web_search", name="Search for latest information", order_hint=2,
                         evidence_rules=[EvidenceRule(evidence_type="search", pattern="web_search|fetch")]),
            WorkflowStep(step_id="compare_claims", name="Compare new findings against prior claims", order_hint=3,
                         evidence_rules=[EvidenceRule(evidence_type="artifact", pattern="compare|diff|claim")]),
            WorkflowStep(step_id="update_output", name="Produce updated research with citations", order_hint=4,
                         evidence_rules=[EvidenceRule(evidence_type="artifact", pattern="research|summary|report")]),
        ],
        hard_gates=[
            HardGate(gate_id="no_stale_claims", name="No unchecked stale claims presented as current"),
        ],
        completion_policy="Prior claims must be explicitly validated or marked stale. New sources must be cited.",
        created_at=_now_iso(),
        confidence=0.7,
    )


def get_pr_premerge_policy() -> WorkflowPolicy:
    """Seed policy for PR pre-merge check."""
    return WorkflowPolicy(
        workflow_id="pr.premerge.fullcheck.v2",
        name="PR Pre-Merge Full Check",
        family="dev",
        version=2,
        aliases=["prep for PR", "pre-merge check"],
        trigger_phrases=[
            "prep this for PR",
            "ready for merge",
            "pre-merge check",
        ],
        intent="Ensure code is ready to merge: tests pass, no regressions, summary written",
        required_steps=[
            WorkflowStep(step_id="diff_review", name="Review all changed files", order_hint=1,
                         evidence_rules=[EvidenceRule(evidence_type="tool_call", pattern="diff|read")]),
            WorkflowStep(step_id="test_run", name="Run test suite", order_hint=2,
                         evidence_rules=[EvidenceRule(evidence_type="tool_call", pattern="test|pytest|jest")]),
            WorkflowStep(step_id="typecheck", name="Run type checking", order_hint=3,
                         evidence_rules=[EvidenceRule(evidence_type="tool_call", pattern="typecheck|tsc|mypy")]),
            WorkflowStep(step_id="lint", name="Run linting", order_hint=4,
                         evidence_rules=[EvidenceRule(evidence_type="tool_call", pattern="lint|eslint|ruff")]),
            WorkflowStep(step_id="pr_description", name="Write PR description", order_hint=5,
                         evidence_rules=[EvidenceRule(evidence_type="artifact", pattern="pr|summary|description")]),
        ],
        hard_gates=[
            HardGate(gate_id="tests_pass", name="Tests must pass"),
            HardGate(gate_id="no_regressions", name="No regressions introduced"),
        ],
        completion_policy="All checks must pass. PR description must be written.",
        created_at=_now_iso(),
        confidence=0.9,
    )


# ---------------------------------------------------------------------------
# Policy storage & retrieval
# ---------------------------------------------------------------------------

_SEED_POLICIES = {
    "dev.flywheel.v3": get_dev_flywheel_policy,
    "qa.interactive_surface_audit.v2": get_qa_surface_audit_policy,
    "drx.latest_industry_refresh.v1": get_drx_refresh_policy,
    "pr.premerge.fullcheck.v2": get_pr_premerge_policy,
}


def load_policy(workflow_id: str) -> Optional[WorkflowPolicy]:
    """Load a workflow policy by ID. Checks disk first, then seed policies."""
    path = _POLICY_DIR / f"{workflow_id.replace('.', '_')}.json"
    if path.exists():
        try:
            return WorkflowPolicy(**json.loads(path.read_text()))
        except Exception as e:
            logger.warning(f"Failed to load policy {workflow_id}: {e}")

    # Fall back to seed
    if workflow_id in _SEED_POLICIES:
        return _SEED_POLICIES[workflow_id]()

    return None


def save_policy(policy: WorkflowPolicy) -> None:
    """Save a workflow policy to disk."""
    policy.updated_at = _now_iso()
    path = _POLICY_DIR / f"{policy.workflow_id.replace('.', '_')}.json"
    path.write_text(json.dumps(policy.model_dump(), indent=2, default=str))
    logger.info(f"Saved workflow policy: {policy.workflow_id}")


def list_policies() -> List[WorkflowPolicy]:
    """List all available policies (disk + seed)."""
    policies = {}

    # Seed policies first
    for wf_id, factory in _SEED_POLICIES.items():
        policies[wf_id] = factory()

    # Disk policies override seeds
    for path in _POLICY_DIR.glob("*.json"):
        try:
            p = WorkflowPolicy(**json.loads(path.read_text()))
            policies[p.workflow_id] = p
        except Exception:
            pass

    return list(policies.values())


def detect_workflow(prompt: str, context: Dict[str, Any] = None) -> Optional[WorkflowPolicy]:
    """Detect which workflow a natural language prompt maps to.

    Uses trigger phrase matching. For production, this should be
    backed by LightRAG retrieval or an LLM classifier.
    """
    prompt_lower = prompt.lower().strip()

    best_match = None
    best_score = 0.0

    for policy in list_policies():
        for phrase in policy.trigger_phrases + policy.aliases:
            phrase_lower = phrase.lower()
            # Exact substring match
            if phrase_lower in prompt_lower:
                score = len(phrase_lower) / len(prompt_lower)
                if score > best_score:
                    best_score = score
                    best_match = policy

    if best_match and best_score > 0.1:
        logger.info(f"Detected workflow: {best_match.workflow_id} (score={best_score:.2f})")
        return best_match

    return None
