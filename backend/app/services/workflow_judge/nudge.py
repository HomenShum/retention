"""
Nudge Engine — 3-level intervention system for false completion prevention.

L1 Soft:  "You usually do X before closing this workflow"
L2 Strong: "Required step missing — no evidence found"
L3 Block:  "Cannot mark complete — mandatory step has no evidence"

Nudges are retrieval-backed: they cite the user's own workflow history,
not static rules. "In your last 7 accepted dev.flywheel runs, a latest-industry
search was present; I found none in this run."
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import JudgeVerdict, NudgeLevel, WorkflowKnowledge

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_NUDGE_LOG = _DATA_DIR / "nudge_log"
_NUDGE_LOG.mkdir(parents=True, exist_ok=True)


@dataclass
class Nudge:
    """A single nudge intervention."""
    level: str  # NudgeLevel value
    message: str
    step_id: str = ""
    step_name: str = ""
    workflow_id: str = ""
    evidence_gap: str = ""  # What evidence is missing
    historical_context: str = ""  # "In your last N runs, this step was present"
    timestamp: str = ""


def generate_nudges(
    verdict: JudgeVerdict,
    workflow: Optional[WorkflowKnowledge] = None,
) -> List[Nudge]:
    """Convenience function — create engine and generate nudges."""
    engine = NudgeEngine()
    return engine.generate_nudges(verdict, workflow)


def format_nudges(nudges: List[Nudge]) -> str:
    """Convenience function — format nudges as human-readable text."""
    engine = NudgeEngine()
    return engine.format_nudges_for_user(nudges)


class NudgeEngine:
    """Generates and delivers nudges based on judge verdicts."""

    def __init__(self):
        self._history: List[Dict[str, Any]] = []

    def generate_nudges(
        self,
        verdict: JudgeVerdict,
        workflow: Optional[WorkflowKnowledge] = None,
    ) -> List[Nudge]:
        """Generate nudges from a judge verdict.

        Returns a list of nudges ordered by severity (block first).
        """
        nudges = []
        now = datetime.now(timezone.utc).isoformat()

        for step in verdict.required_steps:
            status = step.get("status", "done")
            step_id = step.get("step_id", "")
            step_name = step.get("name", step_id)

            if status == "missing":
                # Check workflow history for this step
                historical = ""
                if workflow and workflow.total_runs > 0:
                    historical = (
                        f"In your last {workflow.total_runs} "
                        f"{workflow.name} runs, '{step_name}' was present."
                    )

                # Determine nudge level based on step importance
                common_misses = []
                if workflow:
                    for ws in workflow.required_steps:
                        if ws.step_id == step_id:
                            common_misses = ws.common_misses
                            break

                nudge = Nudge(
                    level=NudgeLevel.STRONG.value if not verdict.all_gates_pass else NudgeLevel.SOFT.value,
                    message=f"Required step '{step_name}' has no evidence.",
                    step_id=step_id,
                    step_name=step_name,
                    workflow_id=verdict.workflow_id,
                    evidence_gap=f"Expected evidence types: {', '.join(common_misses) if common_misses else 'any tool call matching this step'}",
                    historical_context=historical,
                    timestamp=now,
                )
                nudges.append(nudge)

            elif status == "partial":
                nudges.append(Nudge(
                    level=NudgeLevel.SOFT.value,
                    message=f"Step '{step_name}' has partial evidence — may need more coverage.",
                    step_id=step_id,
                    step_name=step_name,
                    workflow_id=verdict.workflow_id,
                    timestamp=now,
                ))

        # If verdict is block-level, add a summary block nudge
        if verdict.nudge_level == NudgeLevel.BLOCK.value:
            nudges.insert(0, Nudge(
                level=NudgeLevel.BLOCK.value,
                message=verdict.nudge_message,
                workflow_id=verdict.workflow_id,
                timestamp=now,
            ))

        # Sort: block > strong > soft
        level_order = {NudgeLevel.BLOCK.value: 0, NudgeLevel.STRONG.value: 1, NudgeLevel.SOFT.value: 2}
        nudges.sort(key=lambda n: level_order.get(n.level, 3))

        return nudges

    def format_nudges_for_user(self, nudges: List[Nudge]) -> str:
        """Format nudges as a human-readable message."""
        if not nudges:
            return ""

        lines = []
        for nudge in nudges:
            prefix = {
                NudgeLevel.BLOCK.value: "BLOCKED",
                NudgeLevel.STRONG.value: "MISSING",
                NudgeLevel.SOFT.value: "NOTE",
            }.get(nudge.level, "INFO")

            line = f"[{prefix}] {nudge.message}"
            if nudge.historical_context:
                line += f"\n  Context: {nudge.historical_context}"
            lines.append(line)

        return "\n".join(lines)

    def log_nudges(self, nudges: List[Nudge], verdict: JudgeVerdict) -> None:
        """Persist nudge events for learning."""
        if not nudges:
            return
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "workflow_id": verdict.workflow_id,
            "verdict": verdict.verdict,
            "nudge_count": len(nudges),
            "nudges": [
                {
                    "level": n.level,
                    "step_id": n.step_id,
                    "message": n.message,
                }
                for n in nudges
            ],
        }
        path = _NUDGE_LOG / f"nudge-{datetime.now().strftime('%Y%m%d')}.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(log_entry) + "\n")
