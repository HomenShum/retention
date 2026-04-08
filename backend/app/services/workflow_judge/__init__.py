"""
Workflow Judge — always-on LLM judge that learns recurring dev patterns,
enforces mandatory meta-steps with evidence, and blocks false completion.

This is the UNIFIED core product layer of retention.sh / retention.sh.
Both agents/qa_pipeline/ and services/workflow_judge/ delegate here.
Data store: data/workflow_knowledge/ (single source of truth).
"""

from .models import WorkflowKnowledge, WorkflowStep, StepEvidence, JudgeVerdict
from .detector import detect_workflow, DetectionResult
from .judge import judge_completion
from .nudge import NudgeEngine, NudgeLevel, generate_nudges, format_nudges
from .learner import detect_correction, record_correction, analyze_corrections
from .hooks import on_prompt_submit, on_tool_use, on_stop, on_session_start

__all__ = [
    # Models
    "WorkflowKnowledge",
    "WorkflowStep",
    "StepEvidence",
    "JudgeVerdict",
    # Detection
    "detect_workflow",
    "DetectionResult",
    # Judge
    "judge_completion",
    # Nudge
    "NudgeEngine",
    "NudgeLevel",
    "generate_nudges",
    "format_nudges",
    # Learning
    "detect_correction",
    "record_correction",
    "analyze_corrections",
    # Hooks
    "on_prompt_submit",
    "on_tool_use",
    "on_stop",
    "on_session_start",
]


def migrate_policies_to_unified_store() -> int:
    """Migrate data from data/workflow_policies/ to data/workflow_knowledge/.

    This reconciles System A's data into the unified store.
    Returns number of policies migrated.
    """
    import json
    from pathlib import Path

    policy_dir = Path(__file__).resolve().parents[3] / "data" / "workflow_policies"
    knowledge_dir = Path(__file__).resolve().parents[3] / "data" / "workflow_knowledge"

    if not policy_dir.exists():
        return 0

    migrated = 0
    for f in policy_dir.glob("*.json"):
        try:
            policy = json.loads(f.read_text())
            wf_id = policy.get("workflow_id", "")
            if not wf_id:
                continue

            # Check if already exists in unified store
            target = knowledge_dir / f"{wf_id}.json"
            if target.exists():
                continue  # Already migrated or created natively

            # Convert policy format to knowledge format
            knowledge = {
                "workflow_id": wf_id,
                "name": policy.get("name", ""),
                "family": policy.get("family", ""),
                "aliases": policy.get("aliases", []),
                "trigger_phrases": policy.get("trigger_phrases", []),
                "version": policy.get("version", 1),
                "description": policy.get("intent", ""),
                "outcome": "",
                "required_steps": [
                    {
                        "step_id": s.get("step_id", ""),
                        "name": s.get("name", ""),
                        "description": s.get("description", ""),
                        "mandatory": s.get("required", True),
                        "evidence_types": [
                            r.get("evidence_type", "") for r in s.get("evidence_rules", [])
                        ],
                        "common_tool_calls": [],
                        "common_misses": s.get("common_misses", []),
                        "order": s.get("order_hint", 0),
                    }
                    for s in policy.get("required_steps", [])
                ],
                "optional_steps": [
                    {
                        "step_id": s.get("step_id", ""),
                        "name": s.get("name", ""),
                        "mandatory": False,
                        "evidence_types": [
                            r.get("evidence_type", "") for r in s.get("evidence_rules", [])
                        ],
                        "order": s.get("order_hint", 0),
                    }
                    for s in policy.get("optional_steps", [])
                ],
                "completion_policy": policy.get("completion_policy", ""),
                "escalation_policy": "",
                "style_preferences": json.dumps(policy.get("style_preferences", {})),
                "total_runs": policy.get("observed_runs", 0),
                "total_corrections": policy.get("correction_count", 0),
                "confidence": policy.get("confidence", 0),
                "created_at": policy.get("created_at", ""),
                "updated_at": policy.get("updated_at", ""),
                "promoted_from": "migrated_from_workflow_policies",
            }

            target.write_text(json.dumps(knowledge, indent=2))
            migrated += 1

        except Exception:
            continue

    return migrated
