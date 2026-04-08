"""
Replay Correctness Policy — the doctrine that governs when replay is valid.

This is the answer to:
  - When is replay considered correct?
  - When is shortcutting valid?
  - When is partial replay acceptable?
  - What forces escalation?
  - What counts as a false success?

Used by suggest_next(), tier_replay_engine, and the canonical scorecard.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class ReplayVerdict(Enum):
    """Outcome of a replay correctness check."""
    CORRECT = "correct"              # Replay produced equivalent outcome
    PARTIAL = "partial"              # Some checkpoints passed, final outcome uncertain
    ESCALATE = "escalate"            # Must hand off to stronger model
    FALSE_SUCCESS = "false_success"  # Replay claims success but checkpoints disagree
    FAILED = "failed"                # Replay failed outright


class EscalationTrigger(Enum):
    """What forces escalation to a stronger model."""
    CHECKPOINT_FAILURE = "checkpoint_failure"           # Required checkpoint didn't pass
    CONSECUTIVE_DRIFT = "consecutive_drift"             # 3+ steps drifted in a row
    OUTCOME_MISMATCH = "outcome_mismatch"               # Final state doesn't match expected
    CONFIDENCE_BELOW_THRESHOLD = "confidence_below"     # suggest_next() confidence too low
    NEW_BRANCH_DETECTED = "new_branch_detected"         # Path diverged into unknown territory
    TOOL_MISSING = "tool_missing"                       # Required tool not available
    CONTEXT_CHANGED = "context_changed"                 # Files/env changed since trajectory recorded


@dataclass
class ReplayPolicy:
    """Configuration for replay correctness decisions."""

    # Completion thresholds
    min_completion_for_correct: float = 0.95    # 95% of checkpoints must pass
    min_completion_for_partial: float = 0.70    # 70%+ = partial, below = failed

    # Drift thresholds
    max_consecutive_drifts: int = 3             # Escalate after 3 consecutive mismatches
    max_total_drift_ratio: float = 0.30         # Escalate if >30% of steps drift

    # Confidence thresholds
    min_suggest_confidence: float = 0.65        # Below this, don't suggest (let model reason)
    min_replay_confidence: float = 0.80         # Below this, escalate instead of replaying

    # Shortcut rules
    shortcut_requires_checkpoint: bool = True   # Shortcuts must have a validation checkpoint
    shortcut_max_steps_skipped: int = 5         # Don't skip more than 5 steps at once

    # False success detection
    require_final_checkpoint: bool = True       # Must validate final state, not just step count
    require_outcome_equivalence: bool = True    # Final output must match baseline


def evaluate_replay(
    total_steps: int,
    steps_completed: int,
    steps_matched: int,
    steps_drifted: int,
    consecutive_drifts: int,
    final_checkpoint_passed: bool,
    outcome_equivalent: bool,
    suggest_confidence: float = 1.0,
    policy: Optional[ReplayPolicy] = None,
) -> Dict[str, Any]:
    """Evaluate whether a replay is correct according to the policy.

    Returns:
        {
            "verdict": ReplayVerdict,
            "reason": str,
            "escalation_trigger": Optional[EscalationTrigger],
            "completion_ratio": float,
            "drift_ratio": float,
            "is_false_success": bool,
        }
    """
    p = policy or ReplayPolicy()

    completion = steps_completed / total_steps if total_steps > 0 else 0
    match_ratio = steps_matched / steps_completed if steps_completed > 0 else 0
    drift_ratio = steps_drifted / steps_completed if steps_completed > 0 else 0

    result: Dict[str, Any] = {
        "completion_ratio": round(completion, 3),
        "match_ratio": round(match_ratio, 3),
        "drift_ratio": round(drift_ratio, 3),
        "is_false_success": False,
        "escalation_trigger": None,
    }

    # Check for false success first
    if steps_completed == total_steps and not final_checkpoint_passed and p.require_final_checkpoint:
        result["verdict"] = ReplayVerdict.FALSE_SUCCESS
        result["reason"] = "All steps completed but final checkpoint failed — false success"
        result["is_false_success"] = True
        result["escalation_trigger"] = EscalationTrigger.CHECKPOINT_FAILURE
        return result

    if steps_completed == total_steps and not outcome_equivalent and p.require_outcome_equivalence:
        result["verdict"] = ReplayVerdict.FALSE_SUCCESS
        result["reason"] = "All steps completed but outcome differs from baseline — false success"
        result["is_false_success"] = True
        result["escalation_trigger"] = EscalationTrigger.OUTCOME_MISMATCH
        return result

    # Check escalation triggers
    if consecutive_drifts >= p.max_consecutive_drifts:
        result["verdict"] = ReplayVerdict.ESCALATE
        result["reason"] = f"{consecutive_drifts} consecutive drifts exceeded threshold ({p.max_consecutive_drifts})"
        result["escalation_trigger"] = EscalationTrigger.CONSECUTIVE_DRIFT
        return result

    if drift_ratio > p.max_total_drift_ratio:
        result["verdict"] = ReplayVerdict.ESCALATE
        result["reason"] = f"Drift ratio {drift_ratio:.1%} exceeds threshold ({p.max_total_drift_ratio:.0%})"
        result["escalation_trigger"] = EscalationTrigger.CONSECUTIVE_DRIFT
        return result

    if suggest_confidence < p.min_replay_confidence and suggest_confidence > 0:
        result["verdict"] = ReplayVerdict.ESCALATE
        result["reason"] = f"Confidence {suggest_confidence:.2f} below replay threshold ({p.min_replay_confidence})"
        result["escalation_trigger"] = EscalationTrigger.CONFIDENCE_BELOW_THRESHOLD
        return result

    # Check completion
    if completion >= p.min_completion_for_correct and final_checkpoint_passed and outcome_equivalent:
        result["verdict"] = ReplayVerdict.CORRECT
        result["reason"] = f"Replay correct: {completion:.0%} complete, outcome equivalent, final checkpoint passed"
        return result

    if completion >= p.min_completion_for_partial:
        result["verdict"] = ReplayVerdict.PARTIAL
        result["reason"] = f"Partial replay: {completion:.0%} complete"
        if not final_checkpoint_passed:
            result["reason"] += ", final checkpoint not passed"
        if not outcome_equivalent:
            result["reason"] += ", outcome not equivalent"
        return result

    result["verdict"] = ReplayVerdict.FAILED
    result["reason"] = f"Replay failed: only {completion:.0%} complete"
    return result


def evaluate_shortcut(
    steps_skipped: int,
    has_checkpoint_after: bool,
    checkpoint_passed: bool,
    policy: Optional[ReplayPolicy] = None,
) -> Dict[str, Any]:
    """Evaluate whether a shortcut (skipping steps) is valid.

    Returns:
        {
            "valid": bool,
            "reason": str,
        }
    """
    p = policy or ReplayPolicy()

    if steps_skipped > p.shortcut_max_steps_skipped:
        return {
            "valid": False,
            "reason": f"Skipped {steps_skipped} steps, max allowed is {p.shortcut_max_steps_skipped}",
        }

    if p.shortcut_requires_checkpoint and not has_checkpoint_after:
        return {
            "valid": False,
            "reason": "Shortcut requires a validation checkpoint after skipped steps",
        }

    if has_checkpoint_after and not checkpoint_passed:
        return {
            "valid": False,
            "reason": "Post-shortcut checkpoint failed — shortcut is not valid",
        }

    return {
        "valid": True,
        "reason": f"Shortcut valid: skipped {steps_skipped} steps, checkpoint passed",
    }
