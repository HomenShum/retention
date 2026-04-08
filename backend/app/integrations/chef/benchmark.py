"""
Chef Benchmark Service

Computes benchmark scores from Chef run results, aggregates across runs,
and provides model comparison analytics.

Scoring logic (from chefScorer.ts):
    1/Deploys: success ? 1/max(1, numDeploys) : 0
    isSuccess: success ? 1 : 0
    totalScore: (scoreDeploys + scoreSuccess) / 2
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .types import ChefBenchmarkResult, ChefResult

logger = logging.getLogger(__name__)


@dataclass
class AggregatedBenchmark:
    """Aggregated benchmark statistics across multiple runs."""

    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    success_rate: float = 0.0
    avg_score: float = 0.0
    avg_deploy_score: float = 0.0
    avg_deploys: float = 0.0
    avg_duration_ms: float = 0.0
    best_score: float = 0.0
    worst_score: float = 0.0
    by_model: Dict[str, "ModelBenchmark"] = field(default_factory=dict)


@dataclass
class ModelBenchmark:
    """Benchmark stats for a specific model."""

    model: str
    total_runs: int = 0
    successful_runs: int = 0
    success_rate: float = 0.0
    avg_score: float = 0.0
    avg_deploys: float = 0.0


class BenchmarkService:
    """Compute and aggregate Chef benchmark scores.

    Usage:
        service = BenchmarkService()
        score = service.compute_score(run_id, chef_result)
        stats = service.aggregate(run_data_list)
    """

    def compute_score(self, run_id: str, result: ChefResult) -> ChefBenchmarkResult:
        """Compute benchmark score for a single run.

        Args:
            run_id: The run identifier.
            result: The ChefResult from the run.

        Returns:
            ChefBenchmarkResult with deploy score, success score, total.
        """
        return ChefBenchmarkResult.from_chef_result(run_id, result)

    def aggregate(self, runs: List[Dict]) -> AggregatedBenchmark:
        """Aggregate benchmark stats from a list of run records.

        Args:
            runs: List of dicts with keys: runId, model, status, success,
                  numDeploys, scoreDeploys, scoreSuccess, totalScore,
                  startedAt, completedAt.

        Returns:
            AggregatedBenchmark with overall and per-model stats.
        """
        agg = AggregatedBenchmark()
        if not runs:
            return agg

        scores: List[float] = []
        deploy_scores: List[float] = []
        deploy_counts: List[int] = []
        durations: List[float] = []
        model_data: Dict[str, List[Dict]] = {}

        for run in runs:
            agg.total_runs += 1
            success = run.get("success", False)

            if success:
                agg.successful_runs += 1
            else:
                agg.failed_runs += 1

            total_score = run.get("totalScore", 0.0)
            scores.append(total_score)
            deploy_scores.append(run.get("scoreDeploys", 0.0))

            num_deploys = run.get("numDeploys", 0)
            if num_deploys:
                deploy_counts.append(num_deploys)

            started = run.get("startedAt", 0)
            completed = run.get("completedAt", 0)
            if started and completed:
                durations.append(completed - started)

            # Group by model
            model = run.get("model", "unknown")
            model_data.setdefault(model, []).append(run)

        agg.success_rate = agg.successful_runs / agg.total_runs if agg.total_runs else 0
        agg.avg_score = sum(scores) / len(scores) if scores else 0
        agg.avg_deploy_score = sum(deploy_scores) / len(deploy_scores) if deploy_scores else 0
        agg.avg_deploys = sum(deploy_counts) / len(deploy_counts) if deploy_counts else 0
        agg.avg_duration_ms = sum(durations) / len(durations) if durations else 0
        agg.best_score = max(scores) if scores else 0
        agg.worst_score = min(scores) if scores else 0

        # Per-model breakdown
        for model, model_runs in model_data.items():
            mb = self._compute_model_benchmark(model, model_runs)
            agg.by_model[model] = mb

        return agg

    def _compute_model_benchmark(
        self, model: str, runs: List[Dict]
    ) -> ModelBenchmark:
        """Compute benchmark stats for a single model."""
        mb = ModelBenchmark(model=model, total_runs=len(runs))
        mb.successful_runs = sum(1 for r in runs if r.get("success", False))
        mb.success_rate = mb.successful_runs / mb.total_runs if mb.total_runs else 0

        scores = [r.get("totalScore", 0.0) for r in runs]
        mb.avg_score = sum(scores) / len(scores) if scores else 0

        deploys = [r.get("numDeploys", 0) for r in runs if r.get("numDeploys")]
        mb.avg_deploys = sum(deploys) / len(deploys) if deploys else 0

        return mb

