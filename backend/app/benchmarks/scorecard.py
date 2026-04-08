"""
Benchmark Scorecard — fixed metrics and comparison aggregator.

Scores every run on 4 dimensions:
1. Completed correctly
2. Caught failure correctly
3. Left enough evidence
4. Can be replayed / debugged quickly
"""

from typing import Dict, List, Optional, Any

from pydantic import BaseModel, Field

from .evidence_schema import (
    AgentMode,
    BenchmarkRunEvidence,
    BenchmarkVerdictLabel,
    RunStatus,
)


class TaskScore(BaseModel):
    """Scorecard row for a single task."""
    task_id: str
    app_id: str
    environment: str
    mode: AgentMode
    success: bool
    time_to_verdict: float = 0.0
    reruns: int = 0
    human_intervention_needed: bool = False
    artifact_completeness: float = 0.0
    root_cause_label: str = ""
    token_cost_usd: float = 0.0
    runtime_cost_usd: float = 0.0

    # 4 scoring dimensions
    completed_correctly: bool = False
    caught_failure_correctly: bool = False
    left_enough_evidence: bool = False
    can_replay: bool = False

    @classmethod
    def from_evidence(cls, ev: BenchmarkRunEvidence) -> "TaskScore":
        completed = (
            ev.status == RunStatus.PASS
            and ev.verdict.label == BenchmarkVerdictLabel.SUCCESS
        )
        caught = (
            ev.status == RunStatus.FAIL
            and ev.verdict.label
            in (BenchmarkVerdictLabel.BUG_FOUND, BenchmarkVerdictLabel.WRONG_OUTPUT)
        )
        enough_evidence = ev.task_metrics.artifact_completeness_score >= 0.5
        replayable = bool(ev.artifacts.trace_path and ev.artifacts.video_path)

        return cls(
            task_id=ev.task_id,
            app_id=ev.app_id,
            environment=ev.environment,
            mode=ev.agent_mode,
            success=ev.status == RunStatus.PASS,
            time_to_verdict=ev.task_metrics.duration_seconds,
            reruns=ev.task_metrics.reruns,
            human_intervention_needed=ev.task_metrics.manual_interventions > 0,
            artifact_completeness=ev.task_metrics.artifact_completeness_score,
            root_cause_label=ev.verdict.reason,
            token_cost_usd=ev.cost.token_cost_usd,
            runtime_cost_usd=ev.cost.compute_cost_usd,
            completed_correctly=completed,
            caught_failure_correctly=caught,
            left_enough_evidence=enough_evidence,
            can_replay=replayable,
        )


class AggregateScore(BaseModel):
    """Aggregate metrics across tasks for a single mode."""
    mode: AgentMode
    total_tasks: int = 0
    success_count: int = 0
    success_rate: float = 0.0
    avg_time_to_verdict: float = 0.0
    avg_reruns: float = 0.0
    avg_artifact_completeness: float = 0.0
    avg_token_cost: float = 0.0
    total_token_cost: float = 0.0
    completed_correctly_count: int = 0
    caught_failure_count: int = 0
    left_evidence_count: int = 0
    can_replay_count: int = 0


class ModeComparison(BaseModel):
    """Side-by-side comparison of baseline vs test-assurance."""
    baseline: AggregateScore
    test_assurance: AggregateScore
    success_rate_delta: float = 0.0
    avg_time_delta: float = 0.0
    avg_rerun_delta: float = 0.0
    avg_evidence_delta: float = 0.0
    avg_cost_delta: float = 0.0
    per_task: List[Dict[str, Any]] = Field(default_factory=list)


class RerunTaskScore(BaseModel):
    """Extended scorecard row for rerun eval — 10 metrics."""
    task_id: str
    lane: str  # "frontier", "retained", "small_model"
    model: str
    completion_score: float = 0.0
    outcome_equivalence: bool = False
    targeting_f1: float = 0.0
    targeting_precision: float = 0.0
    targeting_recall: float = 0.0
    shortcut_validity_rate: float = 0.0
    token_savings_pct: float = 0.0
    time_savings_pct: float = 0.0
    cost_savings_pct: float = 0.0
    artifact_completeness: float = 0.0
    composite_score: float = 0.0
    grade: str = "F"
    escalation_count: int = 0
    cost_usd: float = 0.0


class ThreeLaneComparison(BaseModel):
    """Side-by-side comparison of three benchmark lanes."""
    benchmark_id: str
    task_name: str
    frontier: Optional[RerunTaskScore] = None
    retained: Optional[RerunTaskScore] = None
    small_model: Optional[RerunTaskScore] = None
    cost_per_quality_point: Dict[str, float] = Field(default_factory=dict)
    summary: str = ""


class BenchmarkScorecard(BaseModel):
    """Full scorecard for a benchmark suite run."""
    suite_id: str
    task_scores: List[TaskScore] = Field(default_factory=list)
    baseline_aggregate: Optional[AggregateScore] = None
    ta_aggregate: Optional[AggregateScore] = None
    comparison: Optional[ModeComparison] = None
    # Three-lane rerun eval extension
    three_lane: Optional[ThreeLaneComparison] = None


class ScorecardAggregator:
    """Compute scorecards and mode comparisons from evidence lists."""

    @staticmethod
    def _aggregate(scores: List[TaskScore], mode: AgentMode) -> AggregateScore:
        n = len(scores) or 1
        return AggregateScore(
            mode=mode,
            total_tasks=len(scores),
            success_count=sum(1 for s in scores if s.success),
            success_rate=sum(1 for s in scores if s.success) / n,
            avg_time_to_verdict=sum(s.time_to_verdict for s in scores) / n,
            avg_reruns=sum(s.reruns for s in scores) / n,
            avg_artifact_completeness=sum(s.artifact_completeness for s in scores) / n,
            avg_token_cost=sum(s.token_cost_usd for s in scores) / n,
            total_token_cost=sum(s.token_cost_usd for s in scores),
            completed_correctly_count=sum(1 for s in scores if s.completed_correctly),
            caught_failure_count=sum(1 for s in scores if s.caught_failure_correctly),
            left_evidence_count=sum(1 for s in scores if s.left_enough_evidence),
            can_replay_count=sum(1 for s in scores if s.can_replay),
        )

    def compute_scorecard(
        self, suite_id: str, evidences: List[BenchmarkRunEvidence]
    ) -> BenchmarkScorecard:
        all_scores = [TaskScore.from_evidence(ev) for ev in evidences]

        baseline_scores = [s for s in all_scores if s.mode == AgentMode.CLAUDE_BASELINE]
        ta_scores = [s for s in all_scores if s.mode == AgentMode.TEST_ASSURANCE]

        baseline_agg = self._aggregate(baseline_scores, AgentMode.CLAUDE_BASELINE) if baseline_scores else None
        ta_agg = self._aggregate(ta_scores, AgentMode.TEST_ASSURANCE) if ta_scores else None

        comparison = None
        if baseline_agg and ta_agg:
            # Build per-task comparison
            per_task = []
            baseline_by_task = {s.task_id: s for s in baseline_scores}
            ta_by_task = {s.task_id: s for s in ta_scores}
            for tid in sorted(set(baseline_by_task) | set(ta_by_task)):
                b = baseline_by_task.get(tid)
                t = ta_by_task.get(tid)
                per_task.append({
                    "task_id": tid,
                    "baseline_success": b.success if b else None,
                    "ta_success": t.success if t else None,
                    "baseline_time": b.time_to_verdict if b else None,
                    "ta_time": t.time_to_verdict if t else None,
                    "baseline_evidence": b.artifact_completeness if b else None,
                    "ta_evidence": t.artifact_completeness if t else None,
                    "baseline_cost": b.token_cost_usd if b else None,
                    "ta_cost": t.token_cost_usd if t else None,
                })

            comparison = ModeComparison(
                baseline=baseline_agg,
                test_assurance=ta_agg,
                success_rate_delta=ta_agg.success_rate - baseline_agg.success_rate,
                avg_time_delta=ta_agg.avg_time_to_verdict - baseline_agg.avg_time_to_verdict,
                avg_rerun_delta=ta_agg.avg_reruns - baseline_agg.avg_reruns,
                avg_evidence_delta=ta_agg.avg_artifact_completeness - baseline_agg.avg_artifact_completeness,
                avg_cost_delta=ta_agg.avg_token_cost - baseline_agg.avg_token_cost,
                per_task=per_task,
            )

        return BenchmarkScorecard(
            suite_id=suite_id,
            task_scores=all_scores,
            baseline_aggregate=baseline_agg,
            ta_aggregate=ta_agg,
            comparison=comparison,
        )
