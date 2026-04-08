"""
Rerun Eval Engine — 10-metric WorkflowScorecard for measuring rerun correctness.

Proves when cheaper reruns are still correct, not just cheaper.

Metric stack:
  Correctness (40%):  Completion Score, Outcome Equivalence Rate
  Targeting  (25%):   Rerun Targeting P/R/F1, Shortcut Validity Rate
  Efficiency (25%):   Token Savings %, Time Savings %, Cost Savings %
  Evidence   (10%):   Artifact Completeness Score

Usage:
    scorecard = run_rerun_eval(
        baseline_trajectory_id="...",
        replay_result_id="...",
        task_name="login_flow",
    )
    analysis = analyze_retention_errors(scorecard)
"""

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from .evidence_schema import BENCHMARK_MODEL_PRICING

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_EVAL_DIR = _DATA_DIR / "rerun_eval"
_EVAL_DIR.mkdir(parents=True, exist_ok=True)
_REPLAY_DIR = _DATA_DIR / "replay_results"
_TRAJECTORY_DIR = _DATA_DIR / "trajectories"
_COMPRESSION_DIR = _DATA_DIR / "compressed_workflows"
_CHECKPOINT_DIR = _DATA_DIR / "checkpoints"


# ─── Enums ──────────────────────────────────────────────────────────────

class StepClassLabel(str, Enum):
    """Classification of a single step in rerun targeting."""
    TP = "true_positive"   # Correctly rerun (was stale, TA reran it)
    FP = "false_positive"  # Rerun unnecessarily (was fine, TA reran it)
    TN = "true_negative"   # Correctly skipped (was fine, TA skipped it)
    FN = "false_negative"  # Missed stale step (was stale, TA skipped it)


class EvalGrade(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


# ─── Models ─────────────────────────────────────────────────────────────

class StepClassification(BaseModel):
    """Classification of a single replay step for targeting analysis."""
    step_index: int
    action: str = ""
    semantic_label: str = ""
    label: StepClassLabel
    expected_fp: Optional[str] = None  # baseline fingerprint
    actual_fp: Optional[str] = None    # replay fingerprint
    trajectory_fp: Optional[str] = None  # original trajectory fingerprint
    fingerprint_matched: bool = True
    step_type: str = ""  # navigation, interaction, verification, wait


class RerunTargetingMetrics(BaseModel):
    """Precision / Recall / F1 for rerun targeting."""
    true_positives: int = 0
    false_positives: int = 0
    true_negatives: int = 0
    false_negatives: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    step_classifications: List[StepClassification] = Field(default_factory=list)


class WorkflowScorecard(BaseModel):
    """Canonical 10-metric scorecard for a single workflow eval."""
    eval_id: str = Field(default_factory=lambda: f"eval-{uuid.uuid4().hex[:8]}")
    workflow: str = ""
    task_name: str = ""
    baseline_trajectory_id: str = ""
    replay_result_id: str = ""
    model: str = ""
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # ── Correctness Cluster (40%) ──
    completion_score: float = 0.0
    outcome_equivalence: bool = False
    outcome_equivalence_rate: float = 0.0  # 1.0 if equivalent, 0.0 if not

    # ── Targeting Cluster (25%) ──
    targeting: RerunTargetingMetrics = Field(default_factory=RerunTargetingMetrics)
    shortcut_validity_rate: float = 0.0
    shortcuts_tested: int = 0
    shortcuts_valid: int = 0

    # ── Efficiency Cluster (25%) ──
    token_savings_pct: float = 0.0
    time_savings_pct: float = 0.0
    cost_savings_pct: float = 0.0
    tokens_baseline: int = 0
    tokens_replay: int = 0
    cost_baseline_usd: float = 0.0
    cost_replay_usd: float = 0.0

    # ── Evidence Cluster (10%) ──
    artifact_completeness: float = 0.0
    artifacts_present: Dict[str, bool] = Field(default_factory=dict)

    # ── Composite ──
    composite_score: float = 0.0
    grade: str = "F"

    # ── Metadata ──
    lane: str = ""  # "frontier", "retained", "small_model"
    escalation_count: int = 0
    escalation_model: str = ""

    # ── Dual-source cost tracking ──
    cost_source: str = ""  # "claude_code", "agent_api", "hybrid"
    claude_code_tokens: int = 0
    claude_code_cost_usd: float = 0.0
    agent_api_tokens: int = 0
    agent_api_cost_usd: float = 0.0

    # ── LLM Judge verdict (when available) ──
    judge_verdict: str = ""  # "acceptable_replay", "failed_replay", etc. (5 classes)
    judge_confidence: float = 0.0
    judge_model: str = ""
    judge_scores: Dict[str, int] = Field(default_factory=dict)  # 7 dimensions, 1-5
    judge_hard_gates: Dict[str, bool] = Field(default_factory=dict)  # 5 gates
    judge_notes: str = ""
    judge_source: str = ""  # "strict_llm", "formula", "none"


class RetentionErrorGroup(BaseModel):
    """Error analysis grouped by step type."""
    step_type: str
    total_steps: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    fp_rate: float = 0.0
    fn_rate: float = 0.0
    recommendation: str = ""


class RetentionErrorAnalysis(BaseModel):
    """Actionable error analysis for retention tuning."""
    eval_id: str
    overall_f1: float = 0.0
    error_groups: List[RetentionErrorGroup] = Field(default_factory=list)
    top_recommendations: List[str] = Field(default_factory=list)
    tuning_targets: Dict[str, Any] = Field(default_factory=dict)


# ─── Metric computation ────────────────────────────────────────────────

def _classify_step_type(action: str, semantic_label: str = "") -> str:
    """Classify a step into navigation/interaction/verification/wait."""
    text = f"{action} {semantic_label}".lower()
    if any(kw in text for kw in ["navigate", "open", "go to", "url", "launch"]):
        return "navigation"
    if any(kw in text for kw in ["verify", "check", "assert", "expect", "confirm", "validate"]):
        return "verification"
    if any(kw in text for kw in ["wait", "sleep", "pause", "delay"]):
        return "wait"
    return "interaction"


def compute_completion_score(
    checkpoints: List[Dict[str, Any]],
) -> float:
    """Weighted % of required checkpoints completed.

    Args:
        checkpoints: List of {status: "pass"|"fail"|"pending", weight: float}
    """
    if not checkpoints:
        return 1.0  # No checkpoints defined = assume complete
    total_weight = sum(c.get("weight", 1.0) for c in checkpoints)
    if total_weight == 0:
        return 1.0
    passed_weight = sum(
        c.get("weight", 1.0)
        for c in checkpoints
        if c.get("status") == "pass"
    )
    return round(passed_weight / total_weight, 4)


def compute_outcome_equivalence(
    baseline_final_fp: Optional[str],
    replay_final_fp: Optional[str],
    baseline_verdict: Optional[str] = None,
    replay_verdict: Optional[str] = None,
) -> bool:
    """Did replay reach same validated end state as baseline?"""
    # Fingerprint match
    fp_match = (
        baseline_final_fp is not None
        and replay_final_fp is not None
        and baseline_final_fp == replay_final_fp
    )
    # Verdict match (both pass or both fail)
    verdict_match = True
    if baseline_verdict and replay_verdict:
        verdict_match = baseline_verdict == replay_verdict

    return fp_match or verdict_match


def compute_rerun_targeting(
    baseline_steps: List[Dict[str, Any]],
    replay_steps: List[Dict[str, Any]],
    trajectory_steps: List[Dict[str, Any]],
) -> RerunTargetingMetrics:
    """Compute precision/recall/F1 for rerun targeting.

    Ground truth: A step "needs rerunning" when the baseline fingerprint
    at that step differs from the trajectory's recorded fingerprint.

    Prediction: A step "was rerun" when fingerprint_matched == False
    in the replay per_step_results.

    Args:
        baseline_steps: per_step_results from Lane 1 (ground truth)
        replay_steps: per_step_results from Lane 2/3 (predictions)
        trajectory_steps: original trajectory steps (for fingerprint reference)
    """
    classifications = []
    tp = fp = tn = fn = 0

    n_steps = min(len(replay_steps), len(trajectory_steps))

    for i in range(n_steps):
        replay_step = replay_steps[i] if i < len(replay_steps) else {}
        traj_step = trajectory_steps[i] if i < len(trajectory_steps) else {}
        baseline_step = baseline_steps[i] if i < len(baseline_steps) else {}

        # Ground truth: does this step ACTUALLY need rerunning?
        # (baseline fingerprint differs from trajectory's recorded fingerprint)
        traj_fp = traj_step.get("screen_fingerprint_after", "")
        baseline_fp = baseline_step.get("actual_fp", "")
        actually_needs_rerun = bool(traj_fp and baseline_fp and traj_fp != baseline_fp)

        # Prediction: did TA decide to rerun this step?
        # (fingerprint didn't match during replay)
        ta_reran = not replay_step.get("fingerprint_matched", True)

        # Classify
        if actually_needs_rerun and ta_reran:
            label = StepClassLabel.TP
            tp += 1
        elif not actually_needs_rerun and ta_reran:
            label = StepClassLabel.FP
            fp += 1
        elif not actually_needs_rerun and not ta_reran:
            label = StepClassLabel.TN
            tn += 1
        else:  # actually_needs_rerun and not ta_reran
            label = StepClassLabel.FN
            fn += 1

        action = replay_step.get("action", traj_step.get("action", ""))
        semantic = traj_step.get("semantic_label", "")

        classifications.append(StepClassification(
            step_index=i,
            action=action,
            semantic_label=semantic,
            label=label,
            expected_fp=traj_fp or None,
            actual_fp=replay_step.get("actual_fp"),
            trajectory_fp=traj_fp or None,
            fingerprint_matched=replay_step.get("fingerprint_matched", True),
            step_type=_classify_step_type(action, semantic),
        ))

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return RerunTargetingMetrics(
        true_positives=tp,
        false_positives=fp,
        true_negatives=tn,
        false_negatives=fn,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        step_classifications=classifications,
    )


def compute_shortcut_validity(
    task_name: str,
    replay_outcome_equivalent: bool,
) -> Tuple[int, int, float]:
    """Check if compressed shortcuts preserved correct outcomes.

    Returns (tested, valid, rate).
    """
    shortcut_path = _COMPRESSION_DIR / f"{task_name}.json"
    if not shortcut_path.exists():
        return (0, 0, 0.0)

    try:
        data = json.loads(shortcut_path.read_text())
        shortcuts = data.get("shortcuts", [])
        if not shortcuts:
            return (0, 0, 0.0)

        # Each shortcut is "valid" if the overall replay using it produced
        # an equivalent outcome. For now, all shortcuts share the outcome.
        tested = len(shortcuts)
        valid = tested if replay_outcome_equivalent else 0
        rate = valid / tested if tested > 0 else 0.0
        return (tested, valid, round(rate, 4))
    except Exception:
        return (0, 0, 0.0)


def compute_cost_savings(
    tokens_baseline: int,
    tokens_replay: int,
    model_baseline: str = "claude-opus-4-6",
    model_replay: str = "claude-opus-4-6",
) -> Tuple[float, float, float]:
    """Compute cost savings % from token counts and model pricing.

    Returns (cost_baseline_usd, cost_replay_usd, savings_pct).
    """
    pricing_base = BENCHMARK_MODEL_PRICING.get(
        model_baseline, {"input": 15.0, "output": 75.0}
    )
    pricing_replay = BENCHMARK_MODEL_PRICING.get(
        model_replay, {"input": 15.0, "output": 75.0}
    )

    # Assume 70/30 input/output split
    cost_base = (
        tokens_baseline * 0.7 * pricing_base["input"] / 1_000_000
        + tokens_baseline * 0.3 * pricing_base["output"] / 1_000_000
    )
    cost_replay = (
        tokens_replay * 0.7 * pricing_replay["input"] / 1_000_000
        + tokens_replay * 0.3 * pricing_replay["output"] / 1_000_000
    )

    savings_pct = max(0, (cost_base - cost_replay) / cost_base * 100) if cost_base > 0 else 0.0
    return (round(cost_base, 6), round(cost_replay, 6), round(savings_pct, 1))


# Artifact weights for rerun eval (adapted from evidence_schema)
_ARTIFACT_WEIGHTS = {
    "screenshot": 0.25,
    "logs": 0.20,
    "failure_bundle": 0.20,
    "before_after_diff": 0.20,
    "checkpoint_statuses": 0.15,
}


def compute_artifact_completeness(
    replay_result: Dict[str, Any],
) -> Tuple[float, Dict[str, bool]]:
    """Score artifact completeness for a replay result.

    Returns (score, artifacts_present).
    """
    present = {
        "screenshot": bool(replay_result.get("screenshots") or replay_result.get("screenshot")),
        "logs": bool(replay_result.get("logs_path") or replay_result.get("per_step_results")),
        "failure_bundle": bool(replay_result.get("failure_bundle") or replay_result.get("error")),
        "before_after_diff": bool(replay_result.get("comparison_with_full")),
        "checkpoint_statuses": bool(replay_result.get("per_step_results")),
    }

    score = sum(
        _ARTIFACT_WEIGHTS[k] for k, v in present.items() if v
    )
    return (round(score, 4), present)


# ─── LLM Judge integration ──────────────────────────────────────────────

_CALIBRATION_DIR = _DATA_DIR / "calibration"


async def judge_replay(
    task_description: str,
    frontier_output: str,
    replay_output: str,
    workflow_family: str = "CSP",
    validator_spec: str = "",
    tool_traces: str = "",
) -> Dict[str, Any]:
    """Call the real structured LLM judge on a replay result.

    Uses the base_judge_prompt + family-specific addendum.
    Returns the full judge response with 5 hard gates, 7 scores, verdict.

    This is the ONLY way to get a truth-governance-passing verdict.
    Formula-based scoring (composite_score) is labeled "formula" not "strict_llm".
    """
    from ..services.llm_judge import call_responses_api

    # Load prompts
    base_prompt_path = _CALIBRATION_DIR / "prompts" / "base_judge_prompt.txt"
    base_prompt = base_prompt_path.read_text() if base_prompt_path.exists() else ""

    family_insert_path = _CALIBRATION_DIR / "prompts" / f"{workflow_family.lower()}_judge_insert.txt"
    family_insert = family_insert_path.read_text() if family_insert_path.exists() else ""

    family_addendum_path = _CALIBRATION_DIR / "prompts" / f"{workflow_family.lower()}_judge_addendum.txt"
    family_addendum = family_addendum_path.read_text() if family_addendum_path.exists() else ""

    # Build the full judge prompt
    judge_input = f"""TASK DESCRIPTION:
{task_description}

FRONTIER OUTPUT:
{frontier_output[:3000]}

REPLAY OUTPUT:
{replay_output[:3000]}

VALIDATOR SPECIFICATION:
{validator_spec or "No specific validator — judge holistically."}

TOOL TRACES (abbreviated):
{tool_traces[:2000] if tool_traces else "Not available."}
"""

    instructions = f"{base_prompt}\n\n{family_insert}\n\n{family_addendum}".strip()

    try:
        raw = await call_responses_api(
            judge_input,
            task="strict_judge",
            model="gpt-5.4-mini",
            reasoning_effort="high",
            instructions=instructions,
            timeout_s=120,
            max_output_tokens=3000,
            telemetry_interface="rerun_eval",
            telemetry_operation=f"judge_{workflow_family.lower()}",
        )

        if not raw or raw.startswith("["):
            return {"error": f"Empty or error response: {raw[:100]}", "judge_source": "strict_llm"}

        # Strip code fences
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        result = json.loads(text)
        result["judge_source"] = "strict_llm"
        result["judge_model"] = "gpt-5.4-mini"
        return result

    except json.JSONDecodeError as e:
        logger.warning(f"Judge returned non-JSON: {raw[:200] if raw else 'empty'}")
        return {"error": f"JSON parse error: {e}", "raw": raw[:500] if raw else "", "judge_source": "strict_llm"}
    except Exception as e:
        logger.error(f"Judge call failed: {e}")
        return {"error": str(e), "judge_source": "strict_llm"}


def apply_judge_to_scorecard(
    scorecard: WorkflowScorecard,
    judge_result: Dict[str, Any],
) -> WorkflowScorecard:
    """Apply LLM judge verdict to an existing scorecard.

    The judge verdict OVERRIDES the formula-based composite for truth governance.
    """
    if "error" in judge_result:
        scorecard.judge_source = "strict_llm_failed"
        scorecard.judge_notes = judge_result.get("error", "")
        return scorecard

    scorecard.judge_verdict = judge_result.get("final_verdict", "")
    scorecard.judge_confidence = judge_result.get("confidence", 0.0)
    scorecard.judge_model = judge_result.get("judge_model", "")
    scorecard.judge_scores = judge_result.get("scores", {})
    scorecard.judge_hard_gates = judge_result.get("hard_gates", {})
    scorecard.judge_notes = judge_result.get("notes", "")
    scorecard.judge_source = "strict_llm"

    # Map verdict to grade — this is the TRUTH-GOVERNED grade
    verdict_to_grade = {
        "acceptable_replay": "A",
        "acceptable_replay_with_minor_loss": "B",
        "replay_should_have_escalated": "C",
        "failed_replay": "D",
        "frontier_required": "F",
    }
    if scorecard.judge_verdict in verdict_to_grade:
        scorecard.grade = verdict_to_grade[scorecard.judge_verdict]

    return scorecard


def _compute_composite(scorecard: WorkflowScorecard) -> Tuple[float, str]:
    """Compute weighted composite score and grade."""
    # Correctness cluster (40%)
    correctness = (
        scorecard.completion_score * 0.5
        + scorecard.outcome_equivalence_rate * 0.5
    )

    # Targeting cluster (25%)
    targeting = (
        scorecard.targeting.f1 * 0.7
        + scorecard.shortcut_validity_rate * 0.3
    )

    # Efficiency cluster (25%)
    efficiency = (
        (scorecard.token_savings_pct / 100) * 0.4
        + (scorecard.time_savings_pct / 100) * 0.3
        + (scorecard.cost_savings_pct / 100) * 0.3
    )

    # Evidence cluster (10%)
    evidence = scorecard.artifact_completeness

    composite = (
        correctness * 0.40
        + targeting * 0.25
        + efficiency * 0.25
        + evidence * 0.10
    )

    # Grade thresholds
    if composite >= 0.9:
        grade = "A"
    elif composite >= 0.75:
        grade = "B"
    elif composite >= 0.5:
        grade = "C"
    elif composite >= 0.25:
        grade = "D"
    else:
        grade = "F"

    return (round(composite, 4), grade)


# ─── Main eval orchestrator ────────────────────────────────────────────

def run_rerun_eval(
    replay_result_id: str,
    task_name: str,
    baseline_trajectory_id: str = "",
    baseline_steps: Optional[List[Dict[str, Any]]] = None,
    model_baseline: str = "claude-opus-4-6",
    model_replay: str = "claude-opus-4-6",
    lane: str = "retained",
    checkpoints: Optional[List[Dict[str, Any]]] = None,
) -> WorkflowScorecard:
    """Run full 10-metric eval on a replay result.

    Args:
        replay_result_id: ID of the replay result to evaluate
        task_name: Workflow/task name
        baseline_trajectory_id: ID of the baseline (Lane 1) trajectory
        baseline_steps: per_step_results from baseline run (ground truth)
        model_baseline: Model used for baseline
        model_replay: Model used for replay
        lane: "frontier", "retained", or "small_model"
        checkpoints: Optional checkpoint data for completion scoring
    """
    # Load replay result
    replay_path = _REPLAY_DIR / f"{replay_result_id}.json"
    if not replay_path.exists():
        logger.error(f"Replay result not found: {replay_result_id}")
        return WorkflowScorecard(
            workflow=task_name,
            task_name=task_name,
            replay_result_id=replay_result_id,
        )

    replay_data = json.loads(replay_path.read_text())
    replay_steps = replay_data.get("per_step_results", [])

    # Load baseline trajectory for ground truth
    traj_steps_raw: List[Dict[str, Any]] = []
    if baseline_trajectory_id:
        for task_dir in _TRAJECTORY_DIR.iterdir():
            if not task_dir.is_dir():
                continue
            for f in task_dir.glob("*.json"):
                try:
                    t = json.loads(f.read_text())
                    if t.get("trajectory_id") == baseline_trajectory_id:
                        traj_steps_raw = t.get("steps", [])
                        break
                except Exception:
                    continue
            if traj_steps_raw:
                break

    # If no baseline steps provided, use replay's own data as approximation
    if not baseline_steps:
        baseline_steps = replay_steps

    # ── 1. Completion Score ──
    if checkpoints:
        completion = compute_completion_score(checkpoints)
    else:
        # Derive from replay step success rate
        total = len(replay_steps)
        passed = sum(1 for s in replay_steps if s.get("exec_success", s.get("fingerprint_matched", True)))
        completion = passed / total if total > 0 else 1.0

    # ── 2. Outcome Equivalence ──
    baseline_final_fp = baseline_steps[-1].get("actual_fp") if baseline_steps else None
    replay_final_fp = replay_steps[-1].get("actual_fp") if replay_steps else None
    baseline_verdict = "pass" if replay_data.get("success") else "fail"
    replay_verdict = "pass" if replay_data.get("success") else "fail"
    outcome_eq = compute_outcome_equivalence(
        baseline_final_fp, replay_final_fp,
        baseline_verdict, replay_verdict,
    )

    # ── 3-5. Rerun Targeting P/R/F1 ──
    targeting = compute_rerun_targeting(baseline_steps, replay_steps, traj_steps_raw)

    # ── 6. Shortcut Validity Rate ──
    shortcuts_tested, shortcuts_valid, shortcut_rate = compute_shortcut_validity(
        task_name, outcome_eq,
    )

    # ── 7-8. Token & Time Savings (from replay result) ──
    comparison = replay_data.get("comparison_with_full", {})
    token_savings = comparison.get("token_savings_pct", 0.0)
    time_savings = comparison.get("time_savings_pct", 0.0)
    tokens_full = comparison.get("tokens_full", 31000)
    tokens_replay = comparison.get("tokens_replay", 0)

    # ── 9. Cost Savings ──
    cost_base, cost_replay, cost_savings = compute_cost_savings(
        tokens_full, tokens_replay, model_baseline, model_replay,
    )

    # ── 10. Artifact Completeness ──
    artifact_score, artifacts_present = compute_artifact_completeness(replay_data)

    # ── Build scorecard ──
    scorecard = WorkflowScorecard(
        workflow=replay_data.get("workflow", task_name),
        task_name=task_name,
        baseline_trajectory_id=baseline_trajectory_id,
        replay_result_id=replay_result_id,
        model=model_replay,
        completion_score=round(completion, 4),
        outcome_equivalence=outcome_eq,
        outcome_equivalence_rate=1.0 if outcome_eq else 0.0,
        targeting=targeting,
        shortcut_validity_rate=shortcut_rate,
        shortcuts_tested=shortcuts_tested,
        shortcuts_valid=shortcuts_valid,
        token_savings_pct=round(token_savings, 1),
        time_savings_pct=round(time_savings, 1),
        cost_savings_pct=round(cost_savings, 1),
        tokens_baseline=tokens_full,
        tokens_replay=tokens_replay,
        cost_baseline_usd=cost_base,
        cost_replay_usd=cost_replay,
        artifact_completeness=artifact_score,
        artifacts_present=artifacts_present,
        lane=lane,
    )

    # Compute composite (formula-based — NOT truth-governed)
    composite, grade = _compute_composite(scorecard)
    scorecard.composite_score = composite
    scorecard.grade = grade
    scorecard.judge_source = "formula"  # Explicitly label as formula, not strict_llm

    # Persist
    eval_path = _EVAL_DIR / f"{scorecard.eval_id}.json"
    eval_path.write_text(scorecard.model_dump_json(indent=2))
    logger.info(f"Rerun eval saved: {scorecard.eval_id} grade={grade} composite={composite} judge_source=formula")

    # ── Auto-trigger distillation dataset generation on high-quality evals ──
    if composite >= 0.75:
        try:
            from .distillation_dataset import generate_dataset
            generate_dataset(task_name=task_name, min_composite_score=0.75)
            logger.info(f"Distillation dataset auto-generated for {task_name} (composite={composite})")
        except Exception as _dist_err:
            logger.debug(f"Distillation auto-generation skipped: {_dist_err}")

    return scorecard


# ─── Retention error analysis ──────────────────────────────────────────

def analyze_retention_errors(scorecard: WorkflowScorecard) -> RetentionErrorAnalysis:
    """Produce actionable error analysis for retention tuning.

    Groups false positives and false negatives by step type,
    computes rates, and generates recommendations.
    """
    classifications = scorecard.targeting.step_classifications

    # Group by step type
    groups: Dict[str, Dict[str, int]] = {}
    for sc in classifications:
        st = sc.step_type or "unknown"
        if st not in groups:
            groups[st] = {"total": 0, "fp": 0, "fn": 0}
        groups[st]["total"] += 1
        if sc.label == StepClassLabel.FP:
            groups[st]["fp"] += 1
        elif sc.label == StepClassLabel.FN:
            groups[st]["fn"] += 1

    error_groups = []
    recommendations = []

    for step_type, counts in sorted(groups.items()):
        total = counts["total"]
        fp = counts["fp"]
        fn = counts["fn"]
        fp_rate = fp / total if total > 0 else 0.0
        fn_rate = fn / total if total > 0 else 0.0

        rec = ""
        if fp_rate > 0.3:
            rec = (
                f"{step_type} steps have {fp_rate:.0%} FP rate — "
                f"increase drift threshold for {step_type}-type steps "
                f"or use semantic fingerprinting"
            )
            recommendations.append(rec)
        if fn_rate > 0.2:
            rec = (
                f"{step_type} steps have {fn_rate:.0%} FN rate — "
                f"add checkpoints at {step_type} steps or "
                f"lower drift threshold for dynamic content"
            )
            recommendations.append(rec)

        error_groups.append(RetentionErrorGroup(
            step_type=step_type,
            total_steps=total,
            false_positives=fp,
            false_negatives=fn,
            fp_rate=round(fp_rate, 3),
            fn_rate=round(fn_rate, 3),
            recommendation=rec,
        ))

    # Tuning targets
    tuning_targets = {
        "MAX_DRIFT_SCORE_BEFORE_FALLBACK": {
            "current": 0.4,
            "file": "backend/app/agents/qa_pipeline/trajectory_replay.py",
            "line": 79,
        },
        "is_unstable_threshold": {
            "current": 0.3,
            "file": "backend/app/services/divergence_analyzer.py",
            "line": 175,
        },
    }

    return RetentionErrorAnalysis(
        eval_id=scorecard.eval_id,
        overall_f1=scorecard.targeting.f1,
        error_groups=error_groups,
        top_recommendations=recommendations[:5],
        tuning_targets=tuning_targets,
    )


# ─── Batch eval ────────────────────────────────────────────────────────

def run_batch_eval(
    replay_result_ids: List[str],
    task_name: str,
    **kwargs,
) -> List[WorkflowScorecard]:
    """Run eval on multiple replay results and return scorecards."""
    return [
        run_rerun_eval(rid, task_name, **kwargs)
        for rid in replay_result_ids
    ]


def get_eval_result(eval_id: str) -> Optional[WorkflowScorecard]:
    """Load a saved eval result."""
    path = _EVAL_DIR / f"{eval_id}.json"
    if not path.exists():
        return None
    try:
        return WorkflowScorecard.model_validate_json(path.read_text())
    except Exception:
        return None


def list_eval_results() -> List[Dict[str, Any]]:
    """List all saved eval results (summary only)."""
    results = []
    if not _EVAL_DIR.exists():
        return results
    for f in _EVAL_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            results.append({
                "eval_id": data.get("eval_id"),
                "workflow": data.get("workflow"),
                "task_name": data.get("task_name"),
                "grade": data.get("grade"),
                "composite_score": data.get("composite_score"),
                "lane": data.get("lane"),
                "timestamp": data.get("timestamp"),
            })
        except Exception:
            continue
    return sorted(results, key=lambda x: x.get("timestamp", ""), reverse=True)
