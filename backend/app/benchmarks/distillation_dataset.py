"""
Distillation Dataset Generator — converts validated replay evals into training data.

From validated evals (composite_score >= threshold), generates three types:

1. SFT Examples: (screen_state, task_goal, prior_actions) -> correct_action + mcp_tool_calls
2. DPO Preference Pairs: (chosen=validated step, rejected=failed replay step)
3. Policy Labels: Per-step labels (rerun / skip / escalate) for retention tuning

Output: JSONL files compatible with OpenAI, Anthropic, and HuggingFace training pipelines.

Usage:
    result = generate_dataset(task_name="login_flow", min_composite_score=0.75)
    # -> {"dataset_id": "dist-...", "stats": {...}, "paths": {...}}
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_EVAL_DIR = _DATA_DIR / "rerun_eval"
_TRAJECTORY_DIR = _DATA_DIR / "trajectories"
_REPLAY_DIR = _DATA_DIR / "replay_results"
_DATASET_DIR = _DATA_DIR / "distillation_datasets"
_DATASET_DIR.mkdir(parents=True, exist_ok=True)


# ─── Example types ──────────────────────────────────────────────────────

def _build_sft_example(
    step: Dict[str, Any],
    task_goal: str,
    prior_actions: List[str],
    trajectory_step: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a supervised fine-tuning example from a validated step.

    Format: messages-style (OpenAI/Anthropic compatible)
    """
    screen_state = step.get("state_before", {})
    screen_desc = screen_state.get("ui_elements", "unknown screen state")
    if isinstance(screen_desc, dict):
        elements = screen_desc.get("elements", [])
        screen_desc = ", ".join(
            e.get("text", "")[:30] for e in elements[:10] if isinstance(e, dict)
        )

    system_msg = (
        "You are a mobile testing agent. Given the current screen state "
        "and task goal, determine the correct next action."
    )

    user_msg = (
        f"Task goal: {task_goal}\n"
        f"Current screen: {str(screen_desc)[:500]}\n"
        f"Prior actions: {', '.join(prior_actions[-5:]) if prior_actions else 'none'}\n"
        f"What is the correct next action?"
    )

    action = trajectory_step.get("action", step.get("action", ""))
    tool_calls = trajectory_step.get("mcp_tool_calls", [])

    assistant_msg = action
    if tool_calls:
        assistant_msg += f"\n\nTool calls: {json.dumps(tool_calls)}"

    return {
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": assistant_msg},
        ],
        "metadata": {
            "type": "sft",
            "step_index": step.get("step_index", 0),
            "task_goal": task_goal,
            "fingerprint_matched": step.get("fingerprint_matched", True),
        },
    }


def _build_dpo_pair(
    chosen_step: Dict[str, Any],
    rejected_step: Dict[str, Any],
    task_goal: str,
    prior_actions: List[str],
) -> Dict[str, Any]:
    """Build a DPO preference pair from chosen (validated) vs rejected (failed) step.

    Format: chosen/rejected messages (HuggingFace TRL compatible)
    """
    screen_desc = str(chosen_step.get("state_before", ""))[:300]

    prompt = (
        f"Task goal: {task_goal}\n"
        f"Current screen: {screen_desc}\n"
        f"Prior actions: {', '.join(prior_actions[-5:]) if prior_actions else 'none'}\n"
        f"What is the correct next action?"
    )

    chosen_action = chosen_step.get("action", "")
    rejected_action = rejected_step.get("action", "unknown action")

    return {
        "prompt": prompt,
        "chosen": chosen_action,
        "rejected": rejected_action,
        "metadata": {
            "type": "dpo",
            "step_index": chosen_step.get("step_index", 0),
            "chosen_matched": chosen_step.get("fingerprint_matched", True),
            "rejected_matched": rejected_step.get("fingerprint_matched", False),
        },
    }


def _build_policy_label(
    step_classification: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a policy label for retention tuning.

    Labels: rerun / skip / escalate
    """
    label_map = {
        "true_positive": "rerun",    # Was stale, correctly reran
        "true_negative": "skip",     # Was fine, correctly skipped
        "false_positive": "skip",    # Was fine but reran — should have skipped
        "false_negative": "rerun",   # Was stale but missed — should have rerun
    }

    classification = step_classification.get("label", "true_negative")
    target_label = label_map.get(classification, "skip")

    # If the step was escalated, label it as escalate
    if step_classification.get("escalated", False):
        target_label = "escalate"

    return {
        "step_index": step_classification.get("step_index", 0),
        "action": step_classification.get("action", ""),
        "step_type": step_classification.get("step_type", ""),
        "fingerprint_matched": step_classification.get("fingerprint_matched", True),
        "expected_fp": step_classification.get("expected_fp", ""),
        "actual_fp": step_classification.get("actual_fp", ""),
        "label": target_label,
        "original_classification": classification,
        "metadata": {"type": "policy"},
    }


# ─── Dataset generation ─────────────────────────────────────────────────

def _load_validated_evals(
    task_name: str = "",
    min_composite_score: float = 0.75,
) -> List[Dict[str, Any]]:
    """Load eval results that meet the quality threshold."""
    if not _EVAL_DIR.exists():
        return []

    evals = []
    for f in _EVAL_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            if data.get("composite_score", 0) < min_composite_score:
                continue
            if task_name and data.get("task_name") != task_name:
                continue
            evals.append(data)
        except Exception:
            continue
    return evals


def _load_trajectory_steps(trajectory_id: str) -> List[Dict[str, Any]]:
    """Load trajectory steps for a given trajectory ID."""
    if not _TRAJECTORY_DIR.exists():
        return []
    for task_dir in _TRAJECTORY_DIR.iterdir():
        if not task_dir.is_dir():
            continue
        for f in task_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                if data.get("trajectory_id") == trajectory_id:
                    return data.get("steps", [])
            except Exception:
                continue
    return []


def generate_dataset(
    task_name: str = "",
    min_composite_score: float = 0.75,
    formats: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Generate distillation training data from validated replay evals.

    Args:
        task_name: Filter by task name (empty = all validated evals)
        min_composite_score: Minimum composite score to include
        formats: List of format types to generate: "sft", "dpo", "policy"

    Returns:
        {"dataset_id": str, "stats": dict, "paths": dict}
    """
    if formats is None:
        formats = ["sft", "dpo", "policy"]

    dataset_id = f"dist-{uuid.uuid4().hex[:8]}"
    dataset_dir = _DATASET_DIR / dataset_id
    dataset_dir.mkdir(parents=True, exist_ok=True)

    evals = _load_validated_evals(task_name, min_composite_score)

    stats = {
        "dataset_id": dataset_id,
        "evals_used": len(evals),
        "min_composite_score": min_composite_score,
        "task_name": task_name or "all",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sft_examples": 0,
        "dpo_pairs": 0,
        "policy_labels": 0,
    }
    paths: Dict[str, str] = {}

    for eval_data in evals:
        trajectory_id = eval_data.get("baseline_trajectory_id", "")
        replay_id = eval_data.get("replay_result_id", "")
        task_goal = eval_data.get("workflow", eval_data.get("task_name", ""))

        # Load trajectory steps
        traj_steps = _load_trajectory_steps(trajectory_id) if trajectory_id else []

        # Load replay result
        replay_data = {}
        replay_path = _REPLAY_DIR / f"{replay_id}.json"
        if replay_path.exists():
            try:
                replay_data = json.loads(replay_path.read_text())
            except Exception:
                pass

        replay_steps = replay_data.get("per_step_results", [])
        targeting = eval_data.get("targeting", {})
        step_classifications = targeting.get("step_classifications", [])

        # ── SFT examples ──
        if "sft" in formats:
            sft_path = dataset_dir / "sft.jsonl"
            prior_actions: List[str] = []
            with open(sft_path, "a") as f:
                for i, traj_step in enumerate(traj_steps):
                    replay_step = replay_steps[i] if i < len(replay_steps) else {}
                    # Only include steps that were successful
                    if not replay_step.get("exec_success", traj_step.get("success", True)):
                        continue
                    example = _build_sft_example(
                        step=replay_step,
                        task_goal=task_goal,
                        prior_actions=prior_actions,
                        trajectory_step=traj_step,
                    )
                    f.write(json.dumps(example) + "\n")
                    stats["sft_examples"] += 1
                    prior_actions.append(traj_step.get("action", "")[:60])
            paths["sft"] = str(sft_path)

        # ── DPO preference pairs ──
        if "dpo" in formats:
            dpo_path = dataset_dir / "dpo.jsonl"
            prior_actions = []
            with open(dpo_path, "a") as f:
                for i, traj_step in enumerate(traj_steps):
                    replay_step = replay_steps[i] if i < len(replay_steps) else {}
                    # Create pair when replay diverged (chosen=trajectory, rejected=replay)
                    if not replay_step.get("fingerprint_matched", True):
                        pair = _build_dpo_pair(
                            chosen_step={**traj_step, "step_index": i},
                            rejected_step={**replay_step, "step_index": i},
                            task_goal=task_goal,
                            prior_actions=prior_actions,
                        )
                        f.write(json.dumps(pair) + "\n")
                        stats["dpo_pairs"] += 1
                    prior_actions.append(traj_step.get("action", "")[:60])
            paths["dpo"] = str(dpo_path)

        # ── Policy labels ──
        if "policy" in formats:
            policy_path = dataset_dir / "policy.jsonl"
            with open(policy_path, "a") as f:
                for sc in step_classifications:
                    label = _build_policy_label(sc if isinstance(sc, dict) else sc.model_dump() if hasattr(sc, 'model_dump') else {})
                    f.write(json.dumps(label) + "\n")
                    stats["policy_labels"] += 1
            paths["policy"] = str(policy_path)

    # Save manifest
    manifest = {**stats, "paths": paths, "formats": formats}
    manifest_path = dataset_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    logger.info(
        f"Distillation dataset {dataset_id}: "
        f"{stats['sft_examples']} SFT, {stats['dpo_pairs']} DPO, "
        f"{stats['policy_labels']} policy labels"
    )

    return manifest


# ─── Loaders ────────────────────────────────────────────────────────────

def get_dataset(dataset_id: str) -> Optional[Dict[str, Any]]:
    """Load a dataset manifest."""
    manifest_path = _DATASET_DIR / dataset_id / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text())
    except Exception:
        return None


def list_datasets() -> List[Dict[str, Any]]:
    """List all generated distillation datasets."""
    results = []
    if not _DATASET_DIR.exists():
        return results
    for d in _DATASET_DIR.iterdir():
        if not d.is_dir():
            continue
        manifest_path = d / "manifest.json"
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text())
                results.append({
                    "dataset_id": data.get("dataset_id"),
                    "task_name": data.get("task_name"),
                    "timestamp": data.get("timestamp"),
                    "sft_examples": data.get("sft_examples", 0),
                    "dpo_pairs": data.get("dpo_pairs", 0),
                    "policy_labels": data.get("policy_labels", 0),
                })
            except Exception:
                continue
    return sorted(results, key=lambda x: x.get("timestamp", ""), reverse=True)
