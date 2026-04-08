"""
Strict LLM Replay Judge — uses an actual LLM call to evaluate replay quality.

The old eval pipeline (rerun_eval.py) uses deterministic metrics only:
fingerprint matching, step success rates, cost ratios. These are necessary
but NOT sufficient — they measure mechanical fidelity, not semantic quality.

This module adds a real LLM-as-judge layer that evaluates:
  1. Did the replay produce the SAME functional outcome?
  2. Were any steps semantically wrong (even if fingerprints matched)?
  3. Would a human developer accept this replay as equivalent to frontier?

Truth governance rules:
  - Every eval must record judge_type, judge_model, and the raw LLM response
  - Keyword-validator scores are NEVER mixed with strict judge scores
  - The judge prompt is frozen and versioned — changes require a new version
  - "Acceptable" requires both deterministic metrics AND LLM judge agreement

Uses the shared call_responses_api() from llm_judge.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_EVAL_DIR = _DATA_DIR / "rerun_eval"
_REPLAY_DIR = _DATA_DIR / "replay_results"
_STRICT_DIR = _DATA_DIR / "strict_judge_results"
_STRICT_DIR.mkdir(parents=True, exist_ok=True)

# Judge prompt version — frozen; bump version for any change
JUDGE_PROMPT_VERSION = "strict-replay-v2-calibrated"

# Valid 5-class verdict labels (aligned with Master's calibration rubric)
VALID_VERDICTS = {
    "acceptable_replay",
    "acceptable_replay_with_minor_loss",
    "replay_should_have_escalated",
    "failed_replay",
    "frontier_required",
}


# ─── Types ───────────────────────────────────────────────────────────────

@dataclass
class StrictJudgeVerdict:
    """Result of a strict LLM judge evaluation — calibrated 5-class system."""
    # Identity
    verdict_id: str = ""
    eval_id: str = ""
    replay_result_id: str = ""
    workflow: str = ""

    # Judge metadata — REQUIRED for truth governance
    judge_type: str = "strict_llm_calibrated"
    judge_model: str = ""
    judge_prompt_version: str = JUDGE_PROMPT_VERSION
    judge_raw_response: str = ""

    # 5-class verdict (aligned with Master's run_calibration.py)
    final_verdict: str = ""  # one of VALID_VERDICTS
    confidence: float = 0.0

    # Hard gates (5 boolean gates)
    gate_task_intent_met: bool = False
    gate_no_fabricated_result: bool = False
    gate_output_usable: bool = False
    gate_escalation_needed: bool = False
    gate_replay_deployable: bool = False

    # 7 scored dimensions (1-5 scale)
    score_task_success: int = 0
    score_completeness: int = 0
    score_faithfulness_to_frontier: int = 0
    score_efficiency_of_path: int = 0
    score_artifact_quality: int = 0
    score_safety_or_lossiness: int = 0
    score_overall_quality: int = 0

    # Pairwise comparison
    pairwise_winner: str = ""  # "frontier" | "replay" | "tie"
    pairwise_strength: str = ""  # "small" | "medium" | "large"
    pairwise_reason: str = ""

    # Notes
    notes: str = ""

    # Derived — is this "acceptable" under the rubric?
    acceptable: bool = False  # True if verdict in {acceptable_replay, acceptable_replay_with_minor_loss}

    # Deterministic metrics (cross-reference)
    deterministic_composite: float = 0.0
    deterministic_grade: str = ""
    cost_savings_pct: float = 0.0
    token_savings_pct: float = 0.0

    # Agreement
    judge_agrees_with_deterministic: bool = False

    timestamp: str = ""


@dataclass
class StrictJudgeBatchResult:
    """Aggregated results from the calibrated 5-class judge."""
    batch_id: str = ""
    workflow: str = ""
    total_judged: int = 0

    # 5-class distribution
    verdict_distribution: dict[str, int] = field(default_factory=dict)
    acceptable_count: int = 0  # acceptable_replay + acceptable_with_minor_loss
    acceptable_rate: float = 0.0
    escalation_count: int = 0
    failure_count: int = 0
    frontier_required_count: int = 0

    # Scores
    avg_confidence: float = 0.0
    avg_overall_quality: float = 0.0
    agreement_rate: float = 0.0

    verdicts: list[StrictJudgeVerdict] = field(default_factory=list)
    judge_model: str = ""
    judge_prompt_version: str = JUDGE_PROMPT_VERSION
    timestamp: str = ""


# ─── Judge prompt (frozen, versioned) ────────────────────────────────────

STRICT_JUDGE_SYSTEM = """You are evaluating whether a replayed workflow result is good enough compared with the frontier result.

You must judge:
1. task success
2. completeness
3. faithfulness to frontier
4. efficiency of path
5. artifact quality
6. safety or lossiness
7. whether escalation should have happened

You will receive:
- task/workflow description
- frontier trajectory (original steps)
- replay results (cheaper replay steps + metrics)
- deterministic evaluation metrics

CRITICAL: final_verdict MUST be exactly one of these 5 strings:
- "acceptable_replay"
- "acceptable_replay_with_minor_loss"
- "replay_should_have_escalated"
- "failed_replay"
- "frontier_required"

CRITICAL: pairwise.winner MUST be exactly one of: "frontier", "replay", "tie"
CRITICAL: All scores MUST be integers 1-5.
CRITICAL: Do not invent other verdict names.

Return strict JSON only with this schema:
{
  "hard_gates": {
    "gate_task_intent_met": bool,
    "gate_no_fabricated_result": bool,
    "gate_output_usable": bool,
    "gate_escalation_needed": bool,
    "gate_replay_deployable": bool
  },
  "scores": {
    "task_success": 1-5,
    "completeness": 1-5,
    "faithfulness_to_frontier": 1-5,
    "efficiency_of_path": 1-5,
    "artifact_quality": 1-5,
    "safety_or_lossiness": 1-5,
    "overall_quality": 1-5
  },
  "pairwise": {
    "winner": "frontier" or "replay" or "tie",
    "strength": "small" or "medium" or "large",
    "reason": "one sentence"
  },
  "final_verdict": one of the 5 verdict strings above,
  "confidence": 0.0-1.0,
  "notes": "one sentence"
}

Be STRICT. When in doubt, choose a more conservative verdict. False positives
(claiming acceptable when it's not) are much worse than false negatives."""


def _build_judge_prompt(
    replay_data: dict[str, Any],
    eval_data: Optional[dict[str, Any]] = None,
    trajectory_data: Optional[dict[str, Any]] = None,
) -> str:
    """Build the evaluation prompt from replay + eval + trajectory data."""
    parts = []

    # Replay summary
    parts.append("## Replay Result")
    parts.append(f"- Success: {replay_data.get('success', 'unknown')}")
    parts.append(f"- Steps executed: {replay_data.get('steps_executed', 0)}")
    parts.append(f"- Steps matched (fingerprint): {replay_data.get('steps_matched', 0)}")
    parts.append(f"- Steps drifted: {replay_data.get('steps_drifted', 0)}")
    parts.append(f"- Drift score: {replay_data.get('drift_score', 0)}")
    parts.append(f"- Fallback triggered: {replay_data.get('fallback_to_exploration', False)}")

    comp = replay_data.get("comparison_with_full", {})
    parts.append(f"- Token savings: {comp.get('token_savings_pct', 0):.1f}%")
    parts.append(f"- Time savings: {comp.get('time_savings_pct', 0):.1f}%")
    parts.append(f"- Tokens (frontier): {comp.get('tokens_full', 0):,}")
    parts.append(f"- Tokens (replay): {comp.get('tokens_replay', 0):,}")

    # Per-step results
    per_step = replay_data.get("per_step_results", [])
    if per_step:
        parts.append("\n## Per-Step Results")
        for s in per_step[:20]:  # cap at 20 steps to stay within context
            fp_match = "MATCH" if s.get("fingerprint_matched") else "DRIFT"
            exec_ok = "OK" if s.get("exec_success") else "FAIL"
            parts.append(
                f"  Step {s.get('step_index', '?')}: {s.get('action', '?')[:80]} "
                f"[exec:{exec_ok} fp:{fp_match}]"
            )

    # Deterministic eval if available
    if eval_data:
        parts.append("\n## Deterministic Eval (rerun_eval)")
        parts.append(f"- Composite score: {eval_data.get('composite_score', 0):.3f}")
        parts.append(f"- Grade: {eval_data.get('grade', '?')}")
        parts.append(f"- Completion score: {eval_data.get('completion_score', 0):.3f}")
        parts.append(f"- Outcome equivalence: {eval_data.get('outcome_equivalence', '?')}")
        parts.append(f"- Cost savings: {eval_data.get('cost_savings_pct', 0):.1f}%")
        targeting = eval_data.get("targeting", {})
        parts.append(f"- Targeting P/R/F1: {targeting.get('precision', 0):.2f}/{targeting.get('recall', 0):.2f}/{targeting.get('f1', 0):.2f}")

    # Original trajectory steps if available
    if trajectory_data:
        steps = trajectory_data.get("steps", [])
        if steps:
            parts.append(f"\n## Original Trajectory ({len(steps)} steps)")
            for s in steps[:20]:
                parts.append(f"  Step {s.get('step_index', '?')}: {s.get('action', '?')[:80]}")

    return "\n".join(parts)


# ─── Core judge function ─────────────────────────────────────────────────

async def judge_replay(
    replay_result_id: str,
    eval_id: str = "",
    model: str = "gpt-5.4-mini",
) -> StrictJudgeVerdict:
    """Run strict LLM judge on a single replay result.

    Makes a REAL API call to evaluate replay quality.

    Args:
        replay_result_id: ID of the replay to judge
        eval_id: Optional link to existing rerun_eval scorecard
        model: LLM model for judging (default gpt-5.4-mini for cost)
    """
    verdict = StrictJudgeVerdict(
        verdict_id=f"judge-{uuid.uuid4().hex[:8]}",
        replay_result_id=replay_result_id,
        eval_id=eval_id,
        judge_model=model,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # Load replay data
    replay_path = _REPLAY_DIR / f"{replay_result_id}.json"
    if not replay_path.exists():
        verdict.reasoning = f"Replay result not found: {replay_result_id}"
        return verdict

    replay_data = json.loads(replay_path.read_text())
    verdict.workflow = replay_data.get("workflow", "")

    # Load eval data if available
    eval_data = None
    if eval_id:
        eval_path = _EVAL_DIR / f"{eval_id}.json"
        if eval_path.exists():
            eval_data = json.loads(eval_path.read_text())
            verdict.deterministic_composite = eval_data.get("composite_score", 0)
            verdict.deterministic_grade = eval_data.get("grade", "")
            verdict.cost_savings_pct = eval_data.get("cost_savings_pct", 0)
            verdict.token_savings_pct = eval_data.get("token_savings_pct", 0)

    # Load trajectory if referenced
    trajectory_data = None
    traj_id = replay_data.get("trajectory_id", "")
    if traj_id:
        trajectory_data = _find_trajectory(traj_id)

    # Build prompt
    user_prompt = _build_judge_prompt(replay_data, eval_data, trajectory_data)

    # ── REAL LLM CALL ──
    # Token budget: 2000 minimum for the v2-calibrated JSON schema.
    # The model needs ~500-800 reasoning tokens + ~400-800 output tokens.
    # If incomplete, call_responses_api auto-retries with doubled budget.
    try:
        from ..services.llm_judge import call_responses_api
        raw_response = await call_responses_api(
            prompt=user_prompt,
            task="strict_replay_judge",
            model=model,
            reasoning_effort="high",
            instructions=STRICT_JUDGE_SYSTEM,
            max_output_tokens=2000,
            telemetry_interface="benchmark",
            telemetry_operation="strict_judge",
        )
        verdict.judge_raw_response = raw_response

        # Parse JSON response (calibrated 5-class format)
        parsed = _parse_judge_response(raw_response)

        # Final verdict (5-class)
        fv = parsed.get("final_verdict", "failed_replay")
        verdict.final_verdict = fv if fv in VALID_VERDICTS else "failed_replay"
        verdict.confidence = parsed.get("confidence", 0.0)
        verdict.notes = parsed.get("notes", "")

        # Hard gates
        gates = parsed.get("hard_gates", {})
        verdict.gate_task_intent_met = gates.get("gate_task_intent_met", False)
        verdict.gate_no_fabricated_result = gates.get("gate_no_fabricated_result", False)
        verdict.gate_output_usable = gates.get("gate_output_usable", False)
        verdict.gate_escalation_needed = gates.get("gate_escalation_needed", False)
        verdict.gate_replay_deployable = gates.get("gate_replay_deployable", False)

        # 7 scored dimensions
        scores = parsed.get("scores", {})
        verdict.score_task_success = int(scores.get("task_success", 0))
        verdict.score_completeness = int(scores.get("completeness", 0))
        verdict.score_faithfulness_to_frontier = int(scores.get("faithfulness_to_frontier", 0))
        verdict.score_efficiency_of_path = int(scores.get("efficiency_of_path", 0))
        verdict.score_artifact_quality = int(scores.get("artifact_quality", 0))
        verdict.score_safety_or_lossiness = int(scores.get("safety_or_lossiness", 0))
        verdict.score_overall_quality = int(scores.get("overall_quality", 0))

        # Pairwise comparison
        pw = parsed.get("pairwise", {})
        verdict.pairwise_winner = pw.get("winner", "")
        verdict.pairwise_strength = pw.get("strength", "")
        verdict.pairwise_reason = pw.get("reason", "")

        # Derived: acceptable = verdict in the two acceptable classes
        verdict.acceptable = verdict.final_verdict in {
            "acceptable_replay", "acceptable_replay_with_minor_loss"
        }

        # Check agreement with deterministic eval
        if eval_data:
            det_acceptable = eval_data.get("composite_score", 0) >= 0.7
            verdict.judge_agrees_with_deterministic = (verdict.acceptable == det_acceptable)

    except Exception as e:
        logger.error(f"Strict judge API call failed: {e}")
        verdict.reasoning = f"Judge API call failed: {e}"
        verdict.confidence = 0.0

    # Persist
    path = _STRICT_DIR / f"{verdict.verdict_id}.json"
    path.write_text(json.dumps(asdict(verdict), indent=2))

    logger.info(
        f"Strict judge: {verdict.verdict_id} replay={replay_result_id} "
        f"acceptable={verdict.acceptable} confidence={verdict.confidence:.2f} "
        f"model={model}"
    )

    return verdict


async def judge_batch(
    workflow: str,
    n: int = 10,
    model: str = "gpt-5.4-mini",
) -> StrictJudgeBatchResult:
    """Run strict judge on N replays for a workflow family.

    This is the proper way to generate benchmark numbers.
    Each replay gets a REAL LLM judge call.
    """
    batch = StrictJudgeBatchResult(
        batch_id=f"batch-{uuid.uuid4().hex[:8]}",
        workflow=workflow,
        judge_model=model,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # Find replay files for this workflow
    replay_ids = _find_replays_for_workflow(workflow, n)
    if not replay_ids:
        logger.warning(f"No replay files found for workflow '{workflow}'")
        return batch

    # Find matching eval IDs
    eval_map = _find_evals_for_replays(replay_ids)

    # Judge each replay
    for replay_id in replay_ids:
        eval_id = eval_map.get(replay_id, "")
        verdict = await judge_replay(replay_id, eval_id=eval_id, model=model)
        batch.verdicts.append(verdict)

    # Aggregate — 5-class distribution
    batch.total_judged = len(batch.verdicts)

    dist: dict[str, int] = {}
    for v in batch.verdicts:
        dist[v.final_verdict] = dist.get(v.final_verdict, 0) + 1
    batch.verdict_distribution = dist

    batch.acceptable_count = sum(1 for v in batch.verdicts if v.acceptable)
    batch.acceptable_rate = round(
        batch.acceptable_count / max(batch.total_judged, 1), 3
    )
    batch.escalation_count = dist.get("replay_should_have_escalated", 0)
    batch.failure_count = dist.get("failed_replay", 0)
    batch.frontier_required_count = dist.get("frontier_required", 0)

    batch.avg_confidence = round(
        sum(v.confidence for v in batch.verdicts) / max(batch.total_judged, 1), 3
    )
    quality_scores = [v.score_overall_quality for v in batch.verdicts if v.score_overall_quality > 0]
    batch.avg_overall_quality = round(
        sum(quality_scores) / max(len(quality_scores), 1), 2
    )
    agreements = sum(1 for v in batch.verdicts if v.judge_agrees_with_deterministic)
    batch.agreement_rate = round(
        agreements / max(batch.total_judged, 1), 3
    )

    # Persist
    path = _STRICT_DIR / f"{batch.batch_id}.json"
    path.write_text(json.dumps(asdict(batch), indent=2))

    logger.info(
        f"Strict judge batch: {batch.batch_id} workflow={workflow} "
        f"n={batch.total_judged} acceptable={batch.acceptable_rate:.0%} "
        f"agreement={batch.agreement_rate:.0%}"
    )

    return batch


# ─── Helpers ─────────────────────────────────────────────────────────────

def _parse_judge_response(raw: str) -> dict[str, Any]:
    """Parse the LLM judge's JSON response, handling all failure modes."""
    if not raw or not raw.strip():
        return {"final_verdict": "failed_replay", "confidence": 0,
                "notes": "Empty response from judge API — no output produced"}

    text = raw.strip()

    # Handle incomplete response (reasoning exhausted token budget)
    if text.startswith("[INCOMPLETE:"):
        return {"final_verdict": "frontier_required", "confidence": 0,
                "notes": f"Judge could not produce verdict: {text}"}

    # Strip code fences
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
    if text.endswith("```"):
        text = text[:-3].strip()

    # Direct JSON parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract nested JSON (model sometimes wraps in explanation text)
    import re
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    logger.warning(f"Failed to parse judge response: {raw[:200]}")
    return {"final_verdict": "failed_replay", "confidence": 0,
            "notes": f"Failed to parse judge JSON: {raw[:100]}"}


def _find_trajectory(traj_id: str) -> Optional[dict[str, Any]]:
    """Find a trajectory by ID."""
    traj_dir = _DATA_DIR / "trajectories"
    if not traj_dir.exists():
        return None
    for task_dir in traj_dir.iterdir():
        if not task_dir.is_dir():
            continue
        for f in task_dir.glob("*.json"):
            try:
                t = json.loads(f.read_text())
                if t.get("trajectory_id") == traj_id:
                    return t
            except (json.JSONDecodeError, OSError):
                continue
    return None


def _find_replays_for_workflow(workflow: str, n: int) -> list[str]:
    """Find up to N replay result IDs for a workflow."""
    if not _REPLAY_DIR.exists():
        return []
    ids = []
    for f in sorted(_REPLAY_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            wf = data.get("workflow", "")
            if workflow in wf or workflow.lower() in wf.lower():
                ids.append(f.stem)
        except (json.JSONDecodeError, OSError):
            continue
        if len(ids) >= n:
            break
    return ids


def _find_evals_for_replays(replay_ids: list[str]) -> dict[str, str]:
    """Map replay IDs to their eval IDs."""
    if not _EVAL_DIR.exists():
        return {}
    mapping = {}
    for f in _EVAL_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            rid = data.get("replay_result_id", "")
            if rid in replay_ids:
                mapping[rid] = data.get("eval_id", f.stem)
        except (json.JSONDecodeError, OSError):
            continue
    return mapping


# ─── Convenience for API endpoints ───────────────────────────────────────

def run_strict_judge_sync(
    replay_result_id: str,
    eval_id: str = "",
    model: str = "gpt-5.4-mini",
) -> StrictJudgeVerdict:
    """Synchronous wrapper for judge_replay (for use in non-async contexts)."""
    return asyncio.run(judge_replay(replay_result_id, eval_id, model))


def run_strict_batch_sync(
    workflow: str,
    n: int = 10,
    model: str = "gpt-5.4-mini",
) -> StrictJudgeBatchResult:
    """Synchronous wrapper for judge_batch."""
    return asyncio.run(judge_batch(workflow, n, model))
