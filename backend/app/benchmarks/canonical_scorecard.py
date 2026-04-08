"""
Canonical Benchmark Scorecard — the ONE scorecard used everywhere.

Every workflow, every benchmark page, every pitch, every report
uses this exact same structure. No exceptions.

7 metrics, 3 categories:
  CORRECTNESS (did it work?)
    1. Completion Score     — % of required checkpoints passed
    2. Outcome Equivalence  — did replay produce the same result as baseline?

  EFFICIENCY (how much cheaper?)
    3. Token Savings %      — tokens avoided vs baseline
    4. Cost Savings $       — USD saved (real model pricing)
    5. Time Savings %       — wall-clock time reduction

  RELIABILITY (can you trust it?)
    6. Replay Success Rate  — % of replays that completed without escalation
    7. Escalation Rate      — % of replays that needed a stronger model

One composite grade: A/B/C/D/F
One composite score: 0.0-1.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CanonicalScorecard:
    """The one scorecard to rule them all."""

    # Identity
    workflow_name: str = ""
    workflow_family: str = ""  # "QA", "CSP", "DRX", "OPS"
    model_baseline: str = ""
    model_replay: str = ""
    run_count: int = 1

    # CORRECTNESS
    completion_score: float = 0.0       # 0.0-1.0, % checkpoints passed
    outcome_equivalent: bool = False    # same result as baseline?

    # EFFICIENCY
    token_savings_pct: float = 0.0      # 0-100
    cost_savings_usd: float = 0.0       # absolute dollars saved
    cost_baseline_usd: float = 0.0      # what baseline costs
    cost_replay_usd: float = 0.0        # what replay costs
    time_savings_pct: float = 0.0       # 0-100

    # RELIABILITY
    replay_success_rate: float = 0.0    # 0.0-1.0
    escalation_rate: float = 0.0        # 0.0-1.0

    # Composite
    composite_score: float = 0.0        # 0.0-1.0
    grade: str = ""                     # A/B/C/D/F

    # Traceability
    source_file: str = ""               # which data file this came from
    verified_at: str = ""               # timestamp of last verification

    def compute_composite(self) -> None:
        """Compute composite score and grade from the 7 metrics.

        Weights:
          Correctness: 40% (completion 20% + equivalence 20%)
          Efficiency:  35% (token 15% + cost 10% + time 10%)
          Reliability: 25% (success 15% + escalation 10%)
        """
        equiv_score = 1.0 if self.outcome_equivalent else 0.0
        esc_score = 1.0 - self.escalation_rate

        self.composite_score = round(
            0.20 * self.completion_score
            + 0.20 * equiv_score
            + 0.15 * (self.token_savings_pct / 100.0)
            + 0.10 * (min(self.cost_savings_usd, self.cost_baseline_usd) / max(self.cost_baseline_usd, 0.01))
            + 0.10 * (self.time_savings_pct / 100.0)
            + 0.15 * self.replay_success_rate
            + 0.10 * esc_score,
            4,
        )

        if self.composite_score >= 0.90:
            self.grade = "A"
        elif self.composite_score >= 0.75:
            self.grade = "B"
        elif self.composite_score >= 0.60:
            self.grade = "C"
        elif self.composite_score >= 0.40:
            self.grade = "D"
        else:
            self.grade = "F"


def score_replay_result(replay: dict, baseline_cost_usd: float = 0.0) -> CanonicalScorecard:
    """Score a replay result using the canonical scorecard.

    Works for ANY workflow type — QA, CSP, DRX, OPS.
    """
    comp = replay.get("comparison_with_full", {})
    meta = replay.get("metadata", {})
    costs = meta.get("cost_by_model", {})

    total_steps = replay.get("total_steps", 0)
    matched = replay.get("steps_matched", 0)
    drifted = replay.get("steps_drifted", 0)

    # Completion: steps matched / total steps
    completion = matched / total_steps if total_steps > 0 else 1.0

    # Outcome: success flag
    outcome_eq = replay.get("success", False)

    # Token savings
    token_savings = comp.get("token_savings_pct", 0.0)
    tokens_full = comp.get("tokens_full", 0)
    tokens_replay = comp.get("tokens_replay", 0)

    # Cost: use model pricing if available
    if costs:
        # Baseline = most expensive model cost
        cost_baseline = max(costs.values()) if costs else 0.0
        # Replay = cheapest model cost
        cost_replay = min(costs.values()) if costs else 0.0
    elif baseline_cost_usd > 0:
        cost_baseline = baseline_cost_usd
        cost_replay = baseline_cost_usd * (1 - token_savings / 100)
    else:
        cost_baseline = 0.0
        cost_replay = 0.0

    # Time savings
    time_savings = comp.get("time_savings_pct", 0.0)

    # Reliability
    success = 1.0 if replay.get("success", False) else 0.0
    escalated = 1.0 if replay.get("fallback_to_exploration", False) else 0.0

    sc = CanonicalScorecard(
        workflow_name=replay.get("workflow", ""),
        workflow_family=meta.get("workflow_family", ""),
        model_baseline=meta.get("model", ""),
        model_replay=meta.get("model", ""),
        run_count=1,
        completion_score=round(completion, 3),
        outcome_equivalent=outcome_eq,
        token_savings_pct=round(token_savings, 1),
        cost_savings_usd=round(cost_baseline - cost_replay, 4),
        cost_baseline_usd=round(cost_baseline, 4),
        cost_replay_usd=round(cost_replay, 4),
        time_savings_pct=round(time_savings, 1),
        replay_success_rate=success,
        escalation_rate=escalated,
        source_file=replay.get("replay_run_id", ""),
    )
    sc.compute_composite()
    return sc


def aggregate_scorecards(cards: list[CanonicalScorecard]) -> CanonicalScorecard:
    """Aggregate multiple scorecards into one summary."""
    if not cards:
        return CanonicalScorecard()

    n = len(cards)
    agg = CanonicalScorecard(
        workflow_name=f"{n} workflows aggregated",
        run_count=n,
        completion_score=round(sum(c.completion_score for c in cards) / n, 3),
        outcome_equivalent=all(c.outcome_equivalent for c in cards),
        token_savings_pct=round(sum(c.token_savings_pct for c in cards) / n, 1),
        cost_savings_usd=round(sum(c.cost_savings_usd for c in cards), 4),
        cost_baseline_usd=round(sum(c.cost_baseline_usd for c in cards), 4),
        cost_replay_usd=round(sum(c.cost_replay_usd for c in cards), 4),
        time_savings_pct=round(sum(c.time_savings_pct for c in cards) / n, 1),
        replay_success_rate=round(sum(c.replay_success_rate for c in cards) / n, 3),
        escalation_rate=round(sum(c.escalation_rate for c in cards) / n, 3),
    )
    agg.compute_composite()
    return agg
