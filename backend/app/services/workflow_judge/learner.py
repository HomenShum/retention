"""
Retention Learner — promotes repeated correction patterns into workflow knowledge.

When the user says "you forgot the flywheel" or "you didn't search latest",
the learner records the correction, updates the workflow's common_misses,
and raises confidence for that step being mandatory.

Over time, this turns user frustration into durable workflow memory.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import WorkflowKnowledge, WorkflowStep, _WORKFLOW_DIR

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_CORRECTION_LOG = _DATA_DIR / "correction_log"
_CORRECTION_LOG.mkdir(parents=True, exist_ok=True)


@dataclass
class Correction:
    """A user correction — "you forgot X"."""
    correction_id: str = ""
    timestamp: str = ""
    user_text: str = ""  # The raw correction text
    inferred_step: str = ""  # What step was likely missed
    workflow_id: str = ""  # Which workflow this relates to
    confidence: float = 0.0


@dataclass
class WorkflowCandidate:
    """A candidate for promotion to a retained workflow."""
    name: str = ""
    trigger_phrases: List[str] = field(default_factory=list)
    inferred_steps: List[str] = field(default_factory=list)
    correction_count: int = 0
    confidence: float = 0.0


# ─── Correction detection ───────────────────────────────────────────────

# Patterns that indicate a user correction
CORRECTION_PATTERNS = [
    r"you (?:forgot|didn'?t|did not|missed|skipped|omitted)\s+(?:to\s+)?(.+)",
    r"(?:where|what about)\s+(?:is|are)?\s*(?:the\s+)?(.+?)(?:\?|$)",
    r"you (?:haven'?t|have not)\s+(?:done\s+)?(.+)",
    r"(?:still need|also need|don'?t forget)\s+(?:to\s+)?(.+)",
    r"dude you (?:didn'?t|did not)\s+(.+)",
    r"(?:hey|wait) .+(?:forgot|missed|skipped)\s+(.+)",
    r"(?:ain'?t|aint|haven'?t|didnt) done?\s+(.+)",
]

# Map correction text to likely step types
STEP_INFERENCE_MAP = {
    "search": "latest_search",
    "latest": "latest_search",
    "industry": "latest_search",
    "research": "latest_search",
    "qa": "interactive_audit",
    "interactive": "interactive_audit",
    "clickable": "interactive_audit",
    "preview": "interactive_audit",
    "screenshot": "interactive_audit",
    "test": "verify",
    "lint": "verify",
    "typecheck": "verify",
    "pr": "pr_summary",
    "summary": "pr_summary",
    "commit": "pr_summary",
    "flywheel": "flywheel_full",
    "everything": "flywheel_full",
    "all": "flywheel_full",
    "plan": "understand_plan",
    "surface": "inspect_surfaces",
    "files": "inspect_surfaces",
    "implement": "implement",
    "code": "implement",
    "build": "implement",
}


def detect_correction(user_text: str) -> Optional[Correction]:
    """Detect if user text is a correction and extract what was missed."""
    text_lower = user_text.lower().strip()

    for pattern in CORRECTION_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            missed_text = match.group(1).strip()
            inferred_step = _infer_step(missed_text)

            return Correction(
                correction_id=f"corr-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                timestamp=datetime.now(timezone.utc).isoformat(),
                user_text=user_text,
                inferred_step=inferred_step,
                confidence=0.7 if inferred_step else 0.4,
            )

    return None


def _infer_step(missed_text: str) -> str:
    """Infer which workflow step was likely missed from correction text."""
    words = missed_text.lower().split()
    for word in words:
        if word in STEP_INFERENCE_MAP:
            return STEP_INFERENCE_MAP[word]
    return missed_text[:50]


# ─── Correction recording ──────────────────────────────────────────────

def record_correction(
    correction: Correction,
    workflow_id: str = "",
) -> None:
    """Record a correction for learning."""
    correction.workflow_id = workflow_id

    path = _CORRECTION_LOG / f"corrections-{datetime.now().strftime('%Y%m%d')}.jsonl"
    with open(path, "a") as f:
        f.write(json.dumps(asdict(correction)) + "\n")

    # If we know the workflow, update its common_misses
    if workflow_id:
        _update_workflow_misses(workflow_id, correction.inferred_step)


def _update_workflow_misses(workflow_id: str, missed_step: str) -> None:
    """Update a workflow's common_misses based on a correction."""
    wf = WorkflowKnowledge.load(workflow_id)
    if not wf:
        return

    wf.total_corrections += 1
    wf.correction_rate = wf.total_corrections / max(wf.total_runs + wf.total_corrections, 1)

    # Find matching step and update common_misses
    for step in wf.required_steps:
        if step.step_id == missed_step or missed_step in step.name.lower():
            if missed_step not in step.common_misses:
                step.common_misses.append(missed_step)
            break

    wf.save()
    logger.info(f"Updated workflow {workflow_id}: correction #{wf.total_corrections} for '{missed_step}'")


# ─── Pattern analysis ───────────────────────────────────────────────────

def analyze_corrections(days: int = 30) -> Dict[str, Any]:
    """Analyze correction patterns to find systematic gaps."""
    corrections = _load_corrections(days)
    if not corrections:
        return {"total": 0, "patterns": [], "candidates": []}

    # Count by inferred step
    step_counts = Counter(c.get("inferred_step", "") for c in corrections)

    # Count by workflow
    workflow_counts = Counter(c.get("workflow_id", "") for c in corrections)

    # Find repeated patterns (potential new workflow steps)
    patterns = [
        {"step": step, "count": count, "is_recurring": count >= 3}
        for step, count in step_counts.most_common(10)
        if step
    ]

    # Suggest workflow candidates from uncategorized corrections
    uncategorized = [c for c in corrections if not c.get("workflow_id")]
    candidates = _suggest_workflow_candidates(uncategorized)

    return {
        "total": len(corrections),
        "by_step": dict(step_counts.most_common(10)),
        "by_workflow": dict(workflow_counts.most_common(5)),
        "patterns": patterns,
        "candidates": [asdict(c) for c in candidates],
        "recommendation": _generate_recommendation(patterns),
    }


def _suggest_workflow_candidates(
    corrections: List[Dict[str, Any]],
) -> List[WorkflowCandidate]:
    """Suggest new workflow candidates from uncategorized corrections."""
    # Group corrections by similar text
    groups: Dict[str, List[Dict]] = defaultdict(list)
    for c in corrections:
        step = c.get("inferred_step", "")
        if step:
            groups[step].append(c)

    candidates = []
    for step, corrs in groups.items():
        if len(corrs) >= 2:  # Need at least 2 occurrences
            candidates.append(WorkflowCandidate(
                name=f"Auto-detected: {step}",
                trigger_phrases=[c.get("user_text", "")[:50] for c in corrs[:3]],
                inferred_steps=[step],
                correction_count=len(corrs),
                confidence=min(len(corrs) / 5, 1.0),
            ))

    return sorted(candidates, key=lambda c: c.correction_count, reverse=True)


def _generate_recommendation(patterns: List[Dict]) -> str:
    """Generate a human-readable recommendation from patterns."""
    recurring = [p for p in patterns if p.get("is_recurring")]
    if not recurring:
        return "No recurring correction patterns detected yet."

    steps = [p["step"] for p in recurring]
    return (
        f"Recurring correction patterns detected for: {', '.join(steps)}. "
        f"Consider promoting these to mandatory workflow steps."
    )


def _load_corrections(days: int) -> List[Dict[str, Any]]:
    """Load corrections from the last N days."""
    corrections = []
    if not _CORRECTION_LOG.exists():
        return corrections

    for f in sorted(_CORRECTION_LOG.glob("*.jsonl")):
        try:
            for line in f.read_text().strip().split("\n"):
                if line.strip():
                    corrections.append(json.loads(line))
        except Exception:
            continue

    return corrections
