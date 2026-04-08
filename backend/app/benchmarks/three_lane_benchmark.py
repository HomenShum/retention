"""
Three-Lane Benchmark — proves cheaper reruns are still correct.

Lane 1: Frontier Discovery  — large model, full exploration, ground truth
Lane 2: TA Retained Replay  — same model, saved trajectory, lower cost
Lane 3: TA + Small Model    — cheap model replays, escalates on drift

The comparison table is the investor/user proof:
  expensive model discovers → TA compresses → cheap model replays safely

Usage:
    result = await run_three_lane_benchmark(
        task_name="login_flow",
        mobile_client=client,
        device_id="emulator-5554",
        app_url="http://localhost:3000",
    )
    print(result.comparison_table)
"""

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from .evidence_schema import BENCHMARK_MODEL_PRICING
from .rerun_eval import (
    WorkflowScorecard,
    run_rerun_eval,
    analyze_retention_errors,
    RetentionErrorAnalysis,
)

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_BENCHMARK_DIR = _DATA_DIR / "three_lane_benchmarks"
_BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)


# ─── Models ─────────────────────────────────────────────────────────────

class LaneConfig(BaseModel):
    """Configuration for a single benchmark lane."""
    lane_id: str  # "frontier", "retained", "small_model"
    label: str  # "Lane 1: Frontier Discovery"
    model: str  # "claude-opus-4-6", "gpt-5.4:high", etc.
    mode: str  # "explore", "replay", "replay_with_escalation"
    effort: str = ""  # "high", "xhigh", "" — reasoning effort level
    escalation_model: str = ""  # For Lane 3: model to escalate to
    trajectory_id: str = ""  # For Lanes 2/3: trajectory from Lane 1


class LaneResult(BaseModel):
    """Result from running a single lane."""
    lane_id: str
    label: str
    model: str
    mode: str
    effort: str = ""
    run_id: str = ""
    trajectory_id: str = ""
    success: bool = False
    total_steps: int = 0
    steps_executed: int = 0
    steps_matched: int = 0
    steps_drifted: int = 0
    escalation_count: int = 0
    time_seconds: float = 0.0
    tokens_used: int = 0
    cost_usd: float = 0.0
    scorecard: Optional[WorkflowScorecard] = None
    error: str = ""


class LaneComparisonRow(BaseModel):
    """Single row in the comparison table."""
    metric: str
    lane_1: str  # Frontier
    lane_2: str  # Retained
    lane_3: str  # Small model
    delta_2_vs_1: str = ""
    delta_3_vs_1: str = ""


class ThreeLaneBenchmarkResult(BaseModel):
    """Complete result from a three-lane benchmark run."""
    benchmark_id: str = Field(default_factory=lambda: f"3lane-{uuid.uuid4().hex[:8]}")
    task_name: str = ""
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    lanes: List[LaneResult] = Field(default_factory=list)
    comparison_table: List[LaneComparisonRow] = Field(default_factory=list)
    retention_analysis: Optional[RetentionErrorAnalysis] = None
    cost_per_quality_point: Dict[str, float] = Field(default_factory=dict)
    summary: str = ""


# ─── Default lane configurations ────────────────────────────────────────

def default_lane_configs(
    frontier_model: str = "claude-opus-4-6",
    small_model: str = "claude-haiku-4-5",
) -> List[LaneConfig]:
    return [
        LaneConfig(
            lane_id="frontier",
            label="Lane 1: Frontier Discovery",
            model=frontier_model,
            mode="explore",
        ),
        LaneConfig(
            lane_id="retained",
            label="Lane 2: TA Retained Replay",
            model=frontier_model,
            mode="replay",
        ),
        LaneConfig(
            lane_id="small_model",
            label="Lane 3: TA + Small Model",
            model=small_model,
            mode="replay_with_escalation",
            escalation_model=frontier_model,
        ),
    ]


# ─── Lane execution ────────────────────────────────────────────────────

async def _run_lane_explore(
    config: LaneConfig,
    task_name: str,
    mobile_client,
    device_id: str,
    app_url: str,
) -> LaneResult:
    """Lane 1: Full exploration with frontier model."""
    from ..agents.qa_pipeline.execution_agent import execute_test_suite

    run_id = f"lane1-{uuid.uuid4().hex[:8]}"
    start = time.time()
    result = LaneResult(
        lane_id=config.lane_id,
        label=config.label,
        model=config.model,
        mode=config.mode,
        run_id=run_id,
    )

    try:
        steps_executed = 0
        last_trajectory_id = ""

        async for event in execute_test_suite(
            mobile_client=mobile_client,
            device_id=device_id,
            app_url=app_url,
            task_name=task_name,
        ):
            if event.get("type") == "step_complete":
                steps_executed += 1
            if event.get("trajectory_id"):
                last_trajectory_id = event["trajectory_id"]
            if event.get("type") in ("suite_complete", "execution_complete"):
                result.success = event.get("success", True)

        result.trajectory_id = last_trajectory_id
        result.steps_executed = steps_executed
        result.total_steps = steps_executed
        result.time_seconds = round(time.time() - start, 1)

    except Exception as e:
        result.error = str(e)
        logger.error(f"Lane 1 explore failed: {e}")

    return result


async def _run_lane_replay(
    config: LaneConfig,
    task_name: str,
    mobile_client,
    device_id: str,
    app_url: str,
) -> LaneResult:
    """Lane 2: Replay with same model using Lane 1's trajectory."""
    from ..agents.qa_pipeline.trajectory_replay import replay_trajectory

    run_id = f"lane2-{uuid.uuid4().hex[:8]}"
    start = time.time()
    result = LaneResult(
        lane_id=config.lane_id,
        label=config.label,
        model=config.model,
        mode=config.mode,
        run_id=run_id,
        trajectory_id=config.trajectory_id,
    )

    try:
        async for event in replay_trajectory(
            trajectory_id=config.trajectory_id,
            task_name=task_name,
            mobile_client=mobile_client,
            device_id=device_id,
            app_url=app_url,
            run_id=run_id,
        ):
            if event.get("type") == "replay_complete":
                result.success = event.get("success", False)
                result.steps_executed = event.get("steps_executed", 0)
                result.steps_matched = event.get("steps_matched", 0)
                result.steps_drifted = event.get("steps_drifted", 0)
                result.total_steps = event.get("steps_executed", 0)

        result.time_seconds = round(time.time() - start, 1)

    except Exception as e:
        result.error = str(e)
        logger.error(f"Lane 2 replay failed: {e}")

    return result


async def _run_lane_escalation(
    config: LaneConfig,
    task_name: str,
    mobile_client,
    device_id: str,
    app_url: str,
) -> LaneResult:
    """Lane 3: Replay with small model, escalating drifted steps to frontier.

    Key difference from Lane 2: instead of falling back entirely on drift,
    re-executes JUST the drifted step with the frontier model, then continues
    the replay loop with the small model.
    """
    from ..agents.qa_pipeline.trajectory_replay import replay_trajectory

    run_id = f"lane3-{uuid.uuid4().hex[:8]}"
    start = time.time()
    result = LaneResult(
        lane_id=config.lane_id,
        label=config.label,
        model=config.model,
        mode=config.mode,
        run_id=run_id,
        trajectory_id=config.trajectory_id,
    )

    escalation_count = 0

    async def _escalation_fn():
        """Per-step escalation: re-execute drifted step with frontier model.

        Unlike fallback_fn which terminates the replay loop, this yields
        a single re-execution event and returns control to the caller.
        """
        nonlocal escalation_count
        escalation_count += 1
        yield {
            "type": "escalation",
            "escalation_model": config.escalation_model,
            "escalation_count": escalation_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    try:
        async for event in replay_trajectory(
            trajectory_id=config.trajectory_id,
            task_name=task_name,
            mobile_client=mobile_client,
            device_id=device_id,
            app_url=app_url,
            run_id=run_id,
            fallback_fn=_escalation_fn,
        ):
            if event.get("type") == "replay_complete":
                result.success = event.get("success", False)
                result.steps_executed = event.get("steps_executed", 0)
                result.steps_matched = event.get("steps_matched", 0)
                result.steps_drifted = event.get("steps_drifted", 0)
                result.total_steps = event.get("steps_executed", 0)

        result.escalation_count = escalation_count
        result.time_seconds = round(time.time() - start, 1)

    except Exception as e:
        result.error = str(e)
        logger.error(f"Lane 3 escalation failed: {e}")

    return result


# ─── Cost computation ───────────────────────────────────────────────────

def _compute_lane_cost(lane: LaneResult) -> float:
    """Compute USD cost for a lane result."""
    pricing = BENCHMARK_MODEL_PRICING.get(
        lane.model, {"input": 15.0, "output": 75.0}
    )
    tokens = lane.tokens_used or (lane.steps_executed * 200)
    cost = (
        tokens * 0.7 * pricing["input"] / 1_000_000
        + tokens * 0.3 * pricing["output"] / 1_000_000
    )
    return round(cost, 6)


def _compute_cost_per_quality_point(
    lanes: List[LaneResult],
) -> Dict[str, float]:
    """Cost per composite quality point for each lane."""
    result = {}
    for lane in lanes:
        cost = _compute_lane_cost(lane)
        lane.cost_usd = cost
        quality = lane.scorecard.composite_score if lane.scorecard else 0.5
        result[lane.lane_id] = round(cost / max(quality, 0.01), 6)
    return result


# ─── Comparison table ───────────────────────────────────────────────────

def _fmt(v: Any, fmt: str = ".1f") -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, float):
        return f"{v:{fmt}}"
    return str(v)


def _delta(a: Optional[float], b: Optional[float], fmt: str = "+.1f") -> str:
    if a is None or b is None:
        return ""
    d = b - a
    return f"{d:{fmt}}"


def build_comparison_table(
    lanes: List[LaneResult],
) -> List[LaneComparisonRow]:
    """Build side-by-side comparison table from lane results."""
    l1 = next((l for l in lanes if l.lane_id == "frontier"), None)
    l2 = next((l for l in lanes if l.lane_id == "retained"), None)
    l3 = next((l for l in lanes if l.lane_id == "small_model"), None)

    s1 = l1.scorecard if l1 else None
    s2 = l2.scorecard if l2 else None
    s3 = l3.scorecard if l3 else None

    def row(metric, v1, v2, v3, fmt=".2f"):
        return LaneComparisonRow(
            metric=metric,
            lane_1=_fmt(v1, fmt),
            lane_2=_fmt(v2, fmt),
            lane_3=_fmt(v3, fmt),
            delta_2_vs_1=_delta(v1, v2, "+.2f") if isinstance(v1, (int, float)) else "",
            delta_3_vs_1=_delta(v1, v3, "+.2f") if isinstance(v1, (int, float)) else "",
        )

    rows = [
        row("Completion Score",
            s1.completion_score if s1 else None,
            s2.completion_score if s2 else None,
            s3.completion_score if s3 else None),
        row("Outcome Equivalence",
            s1.outcome_equivalence_rate if s1 else None,
            s2.outcome_equivalence_rate if s2 else None,
            s3.outcome_equivalence_rate if s3 else None),
        row("Rerun Targeting F1",
            None,  # N/A for Lane 1
            s2.targeting.f1 if s2 else None,
            s3.targeting.f1 if s3 else None),
        row("Shortcut Validity",
            None,
            s2.shortcut_validity_rate if s2 else None,
            s3.shortcut_validity_rate if s3 else None),
        row("Token Savings %",
            0.0,
            s2.token_savings_pct if s2 else None,
            s3.token_savings_pct if s3 else None,
            fmt=".1f"),
        row("Time Savings %",
            0.0,
            s2.time_savings_pct if s2 else None,
            s3.time_savings_pct if s3 else None,
            fmt=".1f"),
        row("Cost Savings %",
            0.0,
            s2.cost_savings_pct if s2 else None,
            s3.cost_savings_pct if s3 else None,
            fmt=".1f"),
        row("Composite Score",
            s1.composite_score if s1 else None,
            s2.composite_score if s2 else None,
            s3.composite_score if s3 else None),
        row("Grade",
            s1.grade if s1 else None,
            s2.grade if s2 else None,
            s3.grade if s3 else None,
            fmt="s"),
        row("Cost (USD)",
            l1.cost_usd if l1 else None,
            l2.cost_usd if l2 else None,
            l3.cost_usd if l3 else None,
            fmt=".6f"),
        row("Escalations",
            0,
            0,
            l3.escalation_count if l3 else 0,
            fmt="d"),
    ]
    return rows


# ─── Main benchmark orchestrator ────────────────────────────────────────

async def run_three_lane_benchmark(
    task_name: str,
    mobile_client,
    device_id: str,
    app_url: str = "",
    frontier_model: str = "claude-opus-4-6",
    small_model: str = "claude-haiku-4-5",
    lane_configs: Optional[List[LaneConfig]] = None,
) -> ThreeLaneBenchmarkResult:
    """Run the full three-lane benchmark.

    Executes Lane 1 (explore), then Lane 2 (replay) and Lane 3 (escalation)
    using Lane 1's trajectory.
    """
    configs = lane_configs or default_lane_configs(frontier_model, small_model)
    result = ThreeLaneBenchmarkResult(task_name=task_name)

    # ── Lane 1: Frontier Discovery ──
    lane1_config = next(c for c in configs if c.lane_id == "frontier")
    logger.info(f"Starting Lane 1: {lane1_config.label}")
    lane1_result = await _run_lane_explore(
        lane1_config, task_name, mobile_client, device_id, app_url,
    )

    # Score Lane 1
    if lane1_result.run_id:
        lane1_result.scorecard = run_rerun_eval(
            replay_result_id=lane1_result.run_id,
            task_name=task_name,
            model_baseline=frontier_model,
            model_replay=frontier_model,
            lane="frontier",
        )
    result.lanes.append(lane1_result)

    # Wire Lane 1's trajectory to Lanes 2 and 3
    for config in configs:
        if config.lane_id in ("retained", "small_model"):
            config.trajectory_id = lane1_result.trajectory_id

    # ── Lane 2: TA Retained Replay ──
    lane2_config = next(c for c in configs if c.lane_id == "retained")
    logger.info(f"Starting Lane 2: {lane2_config.label}")
    lane2_result = await _run_lane_replay(
        lane2_config, task_name, mobile_client, device_id, app_url,
    )

    if lane2_result.run_id:
        lane2_result.scorecard = run_rerun_eval(
            replay_result_id=lane2_result.run_id,
            task_name=task_name,
            baseline_trajectory_id=lane1_result.trajectory_id,
            model_baseline=frontier_model,
            model_replay=frontier_model,
            lane="retained",
        )
    result.lanes.append(lane2_result)

    # ── Lane 3: TA + Small Model ──
    lane3_config = next(c for c in configs if c.lane_id == "small_model")
    logger.info(f"Starting Lane 3: {lane3_config.label}")
    lane3_result = await _run_lane_escalation(
        lane3_config, task_name, mobile_client, device_id, app_url,
    )

    if lane3_result.run_id:
        lane3_result.scorecard = run_rerun_eval(
            replay_result_id=lane3_result.run_id,
            task_name=task_name,
            baseline_trajectory_id=lane1_result.trajectory_id,
            model_baseline=frontier_model,
            model_replay=small_model,
            lane="small_model",
        )
    result.lanes.append(lane3_result)

    # ── Comparison ──
    result.cost_per_quality_point = _compute_cost_per_quality_point(result.lanes)
    result.comparison_table = build_comparison_table(result.lanes)

    # ── Retention analysis (from Lane 2, most representative) ──
    if lane2_result.scorecard:
        result.retention_analysis = analyze_retention_errors(lane2_result.scorecard)

    # ── Summary ──
    l2_savings = lane2_result.scorecard.cost_savings_pct if lane2_result.scorecard else 0
    l3_savings = lane3_result.scorecard.cost_savings_pct if lane3_result.scorecard else 0
    l2_grade = lane2_result.scorecard.grade if lane2_result.scorecard else "?"
    l3_grade = lane3_result.scorecard.grade if lane3_result.scorecard else "?"
    result.summary = (
        f"Lane 2 ({frontier_model} replay): {l2_savings:.1f}% cost savings, grade {l2_grade}. "
        f"Lane 3 ({small_model} + escalation): {l3_savings:.1f}% cost savings, grade {l3_grade}. "
        f"Escalations: {lane3_result.escalation_count}."
    )

    # Persist
    benchmark_path = _BENCHMARK_DIR / f"{result.benchmark_id}.json"
    benchmark_path.write_text(result.model_dump_json(indent=2))
    logger.info(f"Three-lane benchmark saved: {result.benchmark_id}")

    return result


# ─── Offline eval (no device needed) ───────────────────────────────────

def run_three_lane_eval_offline(
    task_name: str,
    lane1_replay_id: str,
    lane2_replay_id: str,
    lane3_replay_id: str,
    baseline_trajectory_id: str = "",
    frontier_model: str = "claude-opus-4-6",
    small_model: str = "claude-haiku-4-5",
) -> ThreeLaneBenchmarkResult:
    """Run three-lane eval from existing replay results (no device needed).

    Use this when you already have replay results from all three lanes
    and just want to compute scorecards and comparison tables.
    """
    result = ThreeLaneBenchmarkResult(task_name=task_name)

    for lane_id, replay_id, model, label in [
        ("frontier", lane1_replay_id, frontier_model, "Lane 1: Frontier Discovery"),
        ("retained", lane2_replay_id, frontier_model, "Lane 2: TA Retained Replay"),
        ("small_model", lane3_replay_id, small_model, "Lane 3: TA + Small Model"),
    ]:
        scorecard = run_rerun_eval(
            replay_result_id=replay_id,
            task_name=task_name,
            baseline_trajectory_id=baseline_trajectory_id,
            model_baseline=frontier_model,
            model_replay=model,
            lane=lane_id,
        )
        lane_result = LaneResult(
            lane_id=lane_id,
            label=label,
            model=model,
            mode="offline_eval",
            run_id=replay_id,
            scorecard=scorecard,
        )
        result.lanes.append(lane_result)

    result.cost_per_quality_point = _compute_cost_per_quality_point(result.lanes)
    result.comparison_table = build_comparison_table(result.lanes)

    if result.lanes[1].scorecard:
        result.retention_analysis = analyze_retention_errors(result.lanes[1].scorecard)

    # Persist
    benchmark_path = _BENCHMARK_DIR / f"{result.benchmark_id}.json"
    benchmark_path.write_text(result.model_dump_json(indent=2))

    return result


# ─── Loaders ────────────────────────────────────────────────────────────

def get_benchmark_result(benchmark_id: str) -> Optional[ThreeLaneBenchmarkResult]:
    path = _BENCHMARK_DIR / f"{benchmark_id}.json"
    if not path.exists():
        return None
    try:
        return ThreeLaneBenchmarkResult.model_validate_json(path.read_text())
    except Exception:
        return None


def list_benchmark_results() -> List[Dict[str, Any]]:
    results = []
    if not _BENCHMARK_DIR.exists():
        return results
    for f in _BENCHMARK_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            results.append({
                "benchmark_id": data.get("benchmark_id"),
                "task_name": data.get("task_name"),
                "timestamp": data.get("timestamp"),
                "summary": data.get("summary"),
                "lanes": len(data.get("lanes", [])),
            })
        except Exception:
            continue
    return sorted(results, key=lambda x: x.get("timestamp", ""), reverse=True)


# ─── Multi-model support ───────────────────────────────────────────────

class ModelBenchmarkRow(BaseModel):
    """Single model's results in a multi-model comparison."""
    model: str
    effort: str = ""
    label: str = ""
    role: str = ""  # "frontier", "replay", "small_replay"
    completion_score: float = 0.0
    outcome_equivalence_rate: float = 0.0
    targeting_f1: float = 0.0
    token_savings_pct: float = 0.0
    cost_savings_pct: float = 0.0
    cost_per_run_usd: float = 0.0
    composite_score: float = 0.0
    grade: str = "F"


class MultiModelBenchmarkResult(BaseModel):
    """Full multi-model comparison across all available models."""
    benchmark_id: str = Field(default_factory=lambda: f"multi-{uuid.uuid4().hex[:8]}")
    task_name: str = ""
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    models: List[ModelBenchmarkRow] = Field(default_factory=list)
    three_lane_results: List[ThreeLaneBenchmarkResult] = Field(default_factory=list)
    summary: str = ""


def _parse_model_effort(model_str: str) -> tuple:
    """Parse 'gpt-5.4:high' into ('gpt-5.4', 'high')."""
    if ":" in model_str:
        parts = model_str.split(":", 1)
        return (parts[0], parts[1])
    return (model_str, "")


def multi_model_lane_configs(
    frontier_models: Optional[List[str]] = None,
    replay_models: Optional[List[str]] = None,
) -> List[List[LaneConfig]]:
    """Generate lane configs for multi-model benchmarking.

    Each frontier model gets its own three-lane config set.
    Each replay model gets paired with each frontier model.

    Args:
        frontier_models: Models for Lane 1 discovery (defaults to all frontier-tier)
        replay_models: Models for Lane 3 cheap replay (defaults to all replay-tier)
    """
    from .evidence_schema import MODEL_LABELS

    if frontier_models is None:
        frontier_models = [
            "gpt-5.4:xhigh", "gpt-5.4:high", "gpt-5.4",
            "claude-opus-4-6", "claude-sonnet-4-6",
        ]
    if replay_models is None:
        replay_models = [
            "gpt-5.4-mini:high", "gpt-5.4-mini",
            "gpt-5.4-nano",
            "claude-haiku-4-5",
        ]

    config_sets = []
    for frontier in frontier_models:
        base_model, effort = _parse_model_effort(frontier)
        frontier_label = MODEL_LABELS.get(frontier, frontier)

        for replay in replay_models:
            replay_base, replay_effort = _parse_model_effort(replay)
            replay_label = MODEL_LABELS.get(replay, replay)

            config_sets.append([
                LaneConfig(
                    lane_id="frontier",
                    label=f"L1: {frontier_label}",
                    model=frontier,
                    mode="explore",
                    effort=effort,
                ),
                LaneConfig(
                    lane_id="retained",
                    label=f"L2: {frontier_label} replay",
                    model=frontier,
                    mode="replay",
                    effort=effort,
                ),
                LaneConfig(
                    lane_id="small_model",
                    label=f"L3: {replay_label}",
                    model=replay,
                    mode="replay_with_escalation",
                    effort=replay_effort,
                    escalation_model=frontier,
                ),
            ])

    return config_sets


def run_multi_model_eval_offline(
    task_name: str,
    replay_result_ids: List[str],
    baseline_trajectory_id: str = "",
    models: Optional[List[str]] = None,
) -> MultiModelBenchmarkResult:
    """Run multi-model eval from existing replay results.

    Evaluates the same replay data under different model pricing
    to show cost comparison across models.
    """
    from .evidence_schema import BENCHMARK_MODEL_PRICING, MODEL_LABELS

    if models is None:
        models = list(BENCHMARK_MODEL_PRICING.keys())

    result = MultiModelBenchmarkResult(task_name=task_name)

    for model in models:
        label = MODEL_LABELS.get(model, model)
        base_model, effort = _parse_model_effort(model)
        pricing = BENCHMARK_MODEL_PRICING.get(model, BENCHMARK_MODEL_PRICING.get(base_model, {}))

        # Evaluate each replay under this model's pricing
        scorecards = []
        for rid in replay_result_ids:
            sc = run_rerun_eval(
                replay_result_id=rid,
                task_name=task_name,
                baseline_trajectory_id=baseline_trajectory_id,
                model_baseline=model,
                model_replay=model,
                lane="multi_model",
            )
            scorecards.append(sc)

        if not scorecards:
            continue

        # Average metrics across replays
        avg_completion = sum(s.completion_score for s in scorecards) / len(scorecards)
        avg_equiv = sum(s.outcome_equivalence_rate for s in scorecards) / len(scorecards)
        avg_f1 = sum(s.targeting.f1 for s in scorecards) / len(scorecards)
        avg_token = sum(s.token_savings_pct for s in scorecards) / len(scorecards)
        avg_cost_sav = sum(s.cost_savings_pct for s in scorecards) / len(scorecards)
        avg_cost = sum(s.cost_replay_usd for s in scorecards) / len(scorecards)
        avg_composite = sum(s.composite_score for s in scorecards) / len(scorecards)

        # Grade from average composite
        if avg_composite >= 0.9:
            grade = "A"
        elif avg_composite >= 0.75:
            grade = "B"
        elif avg_composite >= 0.5:
            grade = "C"
        elif avg_composite >= 0.25:
            grade = "D"
        else:
            grade = "F"

        # Determine role tier
        input_price = pricing.get("input", 0)
        if input_price >= 10:
            role = "frontier"
        elif input_price >= 2:
            role = "replay"
        else:
            role = "small_replay"

        result.models.append(ModelBenchmarkRow(
            model=model,
            effort=effort,
            label=label,
            role=role,
            completion_score=round(avg_completion, 3),
            outcome_equivalence_rate=round(avg_equiv, 3),
            targeting_f1=round(avg_f1, 3),
            token_savings_pct=round(avg_token, 1),
            cost_savings_pct=round(avg_cost_sav, 1),
            cost_per_run_usd=round(avg_cost, 6),
            composite_score=round(avg_composite, 3),
            grade=grade,
        ))

    # Sort by cost ascending
    result.models.sort(key=lambda m: m.cost_per_run_usd)

    # Summary
    if result.models:
        cheapest = result.models[0]
        best_grade = max(result.models, key=lambda m: m.composite_score)
        result.summary = (
            f"Cheapest: {cheapest.label} at ${cheapest.cost_per_run_usd:.4f}/run (grade {cheapest.grade}). "
            f"Best quality: {best_grade.label} (grade {best_grade.grade}, composite {best_grade.composite_score:.2f})."
        )

    # Persist
    benchmark_path = _BENCHMARK_DIR / f"{result.benchmark_id}.json"
    benchmark_path.write_text(result.model_dump_json(indent=2))

    return result


def get_available_models() -> List[Dict[str, Any]]:
    """Return all available models with labels and pricing for UI dropdowns."""
    from .evidence_schema import BENCHMARK_MODEL_PRICING, MODEL_LABELS

    models = []
    for model_id, pricing in BENCHMARK_MODEL_PRICING.items():
        base_model, effort = _parse_model_effort(model_id)
        models.append({
            "id": model_id,
            "label": MODEL_LABELS.get(model_id, model_id),
            "base_model": base_model,
            "effort": effort,
            "input_price": pricing["input"],
            "output_price": pricing["output"],
        })
    return models
