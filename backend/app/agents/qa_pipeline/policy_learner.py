"""
Policy Learner — learns new workflow policies from observed corrections.

Attack angle: Replace static guidance with dynamically learned policies.

When a user says "you didn't do the flywheel" or "you forgot the search",
the learner records the correction and, after enough observations,
proposes new required steps or promotes an observed pattern into a policy.

Flow:
  1. Record correction (what was missing, what workflow, what context)
  2. Cluster corrections by workflow family
  3. When a pattern appears N+ times, propose a policy update
  4. User approves → policy is updated
  5. Policy confidence increases with successful enforcement
"""

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .workflow_policy import (
    EvidenceRule,
    WorkflowPolicy,
    WorkflowStep,
    list_policies,
    load_policy,
    save_policy,
)

logger = logging.getLogger(__name__)

_CORRECTIONS_DIR = Path(__file__).resolve().parents[3] / "data" / "correction_log"
_CORRECTIONS_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Correction tracking
# ---------------------------------------------------------------------------

class Correction(BaseModel):
    """A recorded user correction — 'you forgot X'."""
    correction_id: str = ""
    timestamp: str = ""
    workflow_id: str = ""         # which workflow was active (or "" if unknown)
    user_message: str = ""        # what the user actually said
    missing_step: str = ""        # inferred step that was missing
    missing_step_category: str = ""  # "search", "qa", "test", "review", "summary"
    context: Dict[str, Any] = Field(default_factory=dict)


class PolicyProposal(BaseModel):
    """A proposed update to a workflow policy based on observed corrections."""
    proposal_id: str = ""
    workflow_id: str = ""
    proposed_step: WorkflowStep = Field(default_factory=WorkflowStep)
    observation_count: int = 0     # how many times this correction was observed
    confidence: float = 0.0
    source_corrections: List[str] = Field(default_factory=list)  # correction IDs
    status: str = "pending"        # "pending", "approved", "rejected"
    created_at: str = ""


# ---------------------------------------------------------------------------
# Policy Learner
# ---------------------------------------------------------------------------

# Minimum observations before proposing a new step
MIN_OBSERVATIONS_FOR_PROPOSAL = 3

# Patterns that map correction language to step categories
CORRECTION_PATTERNS = {
    "search": ["search", "look up", "research", "latest", "industry", "refresh", "web search", "fetch"],
    "qa": ["qa", "interactive", "clickable", "audit", "surface", "component", "element", "click test"],
    "test": ["test", "verify", "lint", "typecheck", "build", "pytest", "jest", "check"],
    "review": ["review", "look at", "inspect", "examine", "diff"],
    "summary": ["summary", "summarize", "pr ", "description", "commit message", "changelog"],
    "implementation": ["implement", "change", "missed file", "incomplete", "partial", "forgot to add"],
}


class PolicyLearner:
    """Learns workflow policies from user corrections."""

    def __init__(self):
        self._corrections: List[Correction] = []
        self._load_corrections()

    def record_correction(
        self,
        user_message: str,
        workflow_id: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> Correction:
        """Record a user correction like 'you didn't do the flywheel step'.

        Automatically infers the missing step category.
        """
        import uuid

        category = self._classify_correction(user_message)
        missing_step = self._extract_step_name(user_message, category)

        correction = Correction(
            correction_id=f"corr-{uuid.uuid4().hex[:8]}",
            timestamp=_now_iso(),
            workflow_id=workflow_id,
            user_message=user_message,
            missing_step=missing_step,
            missing_step_category=category,
            context=context or {},
        )

        self._corrections.append(correction)
        self._save_correction(correction)

        logger.info(f"Recorded correction: '{user_message[:50]}' → category={category}, step={missing_step}")

        # Check if we should propose a policy update
        proposals = self.check_for_proposals(workflow_id)
        if proposals:
            logger.info(f"New policy proposals: {[p.proposed_step.name for p in proposals]}")

        return correction

    def check_for_proposals(self, workflow_id: str = "") -> List[PolicyProposal]:
        """Check if accumulated corrections justify new policy proposals."""
        import uuid

        # Group corrections by (workflow_id, category)
        groups: Dict[str, List[Correction]] = defaultdict(list)
        for c in self._corrections:
            key = f"{c.workflow_id or 'unknown'}:{c.missing_step_category}"
            groups[key].append(c)

        proposals = []
        for key, corrections in groups.items():
            if len(corrections) < MIN_OBSERVATIONS_FOR_PROPOSAL:
                continue

            wf_id, category = key.split(":", 1)
            if workflow_id and wf_id != workflow_id:
                continue

            # Check if this step already exists in the policy
            policy = load_policy(wf_id) if wf_id != "unknown" else None
            if policy:
                existing_ids = {s.step_id for s in policy.required_steps}
                proposed_id = f"learned_{category}"
                if proposed_id in existing_ids:
                    continue  # already in policy

            # Build the proposed step
            step = self._build_step_from_corrections(category, corrections)
            confidence = min(0.95, len(corrections) / 10.0)

            proposal = PolicyProposal(
                proposal_id=f"prop-{uuid.uuid4().hex[:8]}",
                workflow_id=wf_id,
                proposed_step=step,
                observation_count=len(corrections),
                confidence=confidence,
                source_corrections=[c.correction_id for c in corrections],
                created_at=_now_iso(),
            )
            proposals.append(proposal)

        return proposals

    def apply_proposal(self, proposal: PolicyProposal) -> bool:
        """Apply an approved proposal — add the step to the workflow policy."""
        policy = load_policy(proposal.workflow_id)
        if not policy:
            logger.warning(f"Cannot apply proposal: policy {proposal.workflow_id} not found")
            return False

        # Add the new required step
        policy.required_steps.append(proposal.proposed_step)
        policy.correction_count += proposal.observation_count
        policy.version += 1

        save_policy(policy)
        proposal.status = "approved"

        logger.info(f"Applied policy proposal: added '{proposal.proposed_step.name}' to {policy.workflow_id}")
        return True

    def get_correction_analysis(self, workflow_id: str = "") -> Dict[str, Any]:
        """Analyze correction patterns across all observations."""
        corrections = self._corrections
        if workflow_id:
            corrections = [c for c in corrections if c.workflow_id == workflow_id]

        category_counts = Counter(c.missing_step_category for c in corrections)
        workflow_counts = Counter(c.workflow_id or "unknown" for c in corrections)

        # Find most-missed steps
        step_counts = Counter(c.missing_step for c in corrections if c.missing_step)

        return {
            "total_corrections": len(corrections),
            "by_category": dict(category_counts.most_common()),
            "by_workflow": dict(workflow_counts.most_common()),
            "most_missed_steps": dict(step_counts.most_common(10)),
            "proposals_available": len(self.check_for_proposals(workflow_id)),
        }

    # -- Internal methods --

    def _classify_correction(self, message: str) -> str:
        """Classify a correction message into a step category."""
        msg_lower = message.lower()
        best_category = "other"
        best_score = 0

        for category, patterns in CORRECTION_PATTERNS.items():
            score = sum(1 for p in patterns if p in msg_lower)
            if score > best_score:
                best_score = score
                best_category = category

        return best_category

    def _extract_step_name(self, message: str, category: str) -> str:
        """Extract a human-readable step name from a correction message."""
        category_to_step = {
            "search": "Latest industry/context search",
            "qa": "Interactive surface audit",
            "test": "Run verification suite",
            "review": "Review all impacted files",
            "summary": "Produce summary/PR description",
            "implementation": "Complete implementation across all layers",
        }
        return category_to_step.get(category, f"Missing step ({category})")

    def _build_step_from_corrections(self, category: str, corrections: List[Correction]) -> WorkflowStep:
        """Build a WorkflowStep from clustered corrections."""
        step_name = self._extract_step_name("", category)

        # Build evidence rules based on category
        evidence_map = {
            "search": [EvidenceRule(evidence_type="search", pattern="web_search|fetch", description="External search performed")],
            "qa": [EvidenceRule(evidence_type="tool_call", pattern="screenshot|preview|inspect|crawl|dump_ui", description="Interactive audit")],
            "test": [EvidenceRule(evidence_type="tool_call", pattern="test|pytest|lint|typecheck|build", description="Verification run")],
            "review": [EvidenceRule(evidence_type="file_read", pattern=".*", description="File review")],
            "summary": [EvidenceRule(evidence_type="artifact", pattern="summary|pr|commit|report", description="Summary artifact")],
        }

        common_misses = list(set(c.user_message[:80] for c in corrections[:3]))

        return WorkflowStep(
            step_id=f"learned_{category}",
            name=step_name,
            description=f"Learned from {len(corrections)} user corrections",
            required=True,
            evidence_rules=evidence_map.get(category, []),
            common_misses=common_misses,
            order_hint=50 + len(corrections),  # sort after seed steps
        )

    def _save_correction(self, correction: Correction) -> None:
        path = _CORRECTIONS_DIR / f"{correction.correction_id}.json"
        path.write_text(json.dumps(correction.model_dump(), indent=2, default=str))

    def _load_corrections(self) -> None:
        for path in _CORRECTIONS_DIR.glob("corr-*.json"):
            try:
                data = json.loads(path.read_text())
                self._corrections.append(Correction(**data))
            except Exception:
                pass
