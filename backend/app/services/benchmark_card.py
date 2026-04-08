"""
Standardized Benchmark Card — every benchmark family uses the same schema.

Top-level card fields:
  workflow_family, frontier_model, replay_model, scaffold_source, judge_type,
  validator_type, completion, final_verdict, escalation_rate, cost_savings,
  time_savings, tool_calls_reduced, trace_link

Three-pane compare view:
  1. Frontier run (full cost, full quality)
  2. Replay run (reduced cost, measured quality)
  3. Judge verdict (structured evaluation + limitations)

Aggregates eval files from backend/data/rerun_eval/ into per-family cards.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_EVAL_DIR = _DATA_DIR / "rerun_eval"
_CARD_DIR = _DATA_DIR / "benchmark_cards"
_CARD_DIR.mkdir(parents=True, exist_ok=True)


# ─── Benchmark card schema ───────────────────────────────────────────────

@dataclass
class BenchmarkCard:
    """Standardized benchmark card — one per workflow family."""
    # Identity
    workflow_family: str
    lane: str = ""  # "csp", "drx", "qa_manifest", "qa_evidence", "multi_agent", "temporal"
    version: str = "1.0"

    # Models
    frontier_model: str = ""
    replay_model: str = ""

    # Source
    scaffold_source: str = ""  # "auto_extracted" | "manual" | "hardcoded"
    judge_type: str = ""       # "strict_llm" | "keyword" | "composite"
    validator_type: str = ""   # "llm_strict" | "llm_permissive" | "keyword" | "none"

    # Results — aggregated from eval files
    total_runs: int = 0
    completion_rate: float = 0.0  # % of runs that completed
    acceptable_rate: float = 0.0  # % that passed strict judge
    escalation_rate: float = 0.0  # % that needed escalation
    failure_rate: float = 0.0     # % hard failures

    # Savings
    avg_cost_savings_pct: float = 0.0
    avg_time_savings_pct: float = 0.0
    avg_token_savings_pct: float = 0.0
    total_cost_saved_usd: float = 0.0
    tool_calls_reduced_pct: float = 0.0

    # Quality
    avg_composite_score: float = 0.0
    avg_completion_score: float = 0.0
    avg_artifact_completeness: float = 0.0
    grade_distribution: dict[str, int] = field(default_factory=dict)  # {"A": 5, "B": 8, ...}

    # Verdict
    final_verdict: str = ""  # "production_ready" | "needs_escalation" | "not_ready" | "insufficient_data"
    verdict_reason: str = ""
    limitations: list[str] = field(default_factory=list)

    # Trace
    trace_link: str = ""
    eval_ids: list[str] = field(default_factory=list)
    generated_at: str = ""

    # Model breakdown (for multi-model benchmarks)
    by_model: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class ComparePane:
    """Three-pane compare view for a single run."""
    # Pane 1: Frontier
    frontier_tokens: int = 0
    frontier_cost_usd: float = 0.0
    frontier_time_s: float = 0.0
    frontier_tool_calls: int = 0

    # Pane 2: Replay
    replay_tokens: int = 0
    replay_cost_usd: float = 0.0
    replay_time_s: float = 0.0
    replay_tool_calls: int = 0

    # Pane 3: Judge
    composite_score: float = 0.0
    grade: str = ""
    completion_score: float = 0.0
    outcome_equivalence: bool = False
    escalation_count: int = 0
    artifacts_present: dict[str, bool] = field(default_factory=dict)
    limitation: str = ""


# ─── Card generation ─────────────────────────────────────────────────────

def generate_card(workflow_family: str) -> BenchmarkCard:
    """Generate a benchmark card by aggregating eval files for a workflow family."""
    evals = _load_evals_for_workflow(workflow_family)
    if not evals:
        return BenchmarkCard(
            workflow_family=workflow_family,
            final_verdict="insufficient_data",
            verdict_reason=f"No eval files found for '{workflow_family}'",
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    card = BenchmarkCard(workflow_family=workflow_family)
    card.total_runs = len(evals)
    card.generated_at = datetime.now(timezone.utc).isoformat()

    # Aggregate models
    models = set()
    for e in evals:
        m = e.get("model", "")
        if m:
            models.add(m)
    card.frontier_model = ", ".join(sorted(models)) if models else "unknown"
    card.replay_model = card.frontier_model  # same for now

    # Aggregate scores
    card.avg_composite_score = _avg(evals, "composite_score")
    card.avg_completion_score = _avg(evals, "completion_score")
    card.avg_artifact_completeness = _avg(evals, "artifact_completeness")
    card.avg_cost_savings_pct = _avg(evals, "cost_savings_pct")
    card.avg_time_savings_pct = _avg(evals, "time_savings_pct")
    card.avg_token_savings_pct = _avg(evals, "token_savings_pct")
    card.total_cost_saved_usd = sum(
        max(0, e.get("cost_baseline_usd", 0) - e.get("cost_replay_usd", 0))
        for e in evals
    )

    # Escalation
    escalations = sum(1 for e in evals if e.get("escalation_count", 0) > 0)
    card.escalation_rate = round(escalations / max(len(evals), 1), 3)

    # Grade distribution
    grades: dict[str, int] = defaultdict(int)
    for e in evals:
        g = e.get("grade", "?")
        grades[g] += 1
    card.grade_distribution = dict(grades)

    # Completion and acceptance rates
    completed = sum(1 for e in evals if e.get("completion_score", 0) >= 0.5)
    card.completion_rate = round(completed / max(len(evals), 1), 3)

    # ── Truth governance: detect actual judge provenance ──────────────
    # Eval files from the old pipeline lack judge_type/judge_model fields
    # and use keyword validators (known 100% false positive rate per
    # calibration v1). Only evals with explicit judge_type="strict_llm"
    # can be counted as "acceptable" for verdict purposes.
    has_strict_judge = any(e.get("judge_type") == "strict_llm" for e in evals)
    has_judge_model = any(e.get("judge_model") for e in evals)

    if has_strict_judge:
        # Real strict judge — trust composite scores
        card.judge_type = "strict_llm"
        acceptable = sum(1 for e in evals if e.get("composite_score", 0) >= 0.7)
        card.acceptable_rate = round(acceptable / max(len(evals), 1), 3)
    else:
        # Keyword validator / synthetic composite — DO NOT claim strict acceptance
        card.judge_type = "keyword_validator_unverified"
        # Report the composite threshold rate but label it honestly
        above_threshold = sum(1 for e in evals if e.get("composite_score", 0) >= 0.7)
        card.acceptable_rate = round(above_threshold / max(len(evals), 1), 3)

    card.failure_rate = round(max(0, 1.0 - card.acceptable_rate - card.escalation_rate), 3)
    card.scaffold_source = "auto_extracted"

    # Eval IDs
    card.eval_ids = [e.get("eval_id", "") for e in evals[:20]]

    # Per-model breakdown
    by_model: dict[str, list[dict]] = defaultdict(list)
    for e in evals:
        m = e.get("model", "unknown")
        by_model[m].append(e)

    for model, model_evals in by_model.items():
        card.by_model[model] = {
            "runs": len(model_evals),
            "avg_composite": round(_avg(model_evals, "composite_score"), 3),
            "avg_cost_savings_pct": round(_avg(model_evals, "cost_savings_pct"), 1),
            "acceptable_rate": round(
                sum(1 for e in model_evals if e.get("composite_score", 0) >= 0.7) / max(len(model_evals), 1), 3
            ),
            "grade_dist": dict(defaultdict(int, {e.get("grade", "?"): 1 for e in model_evals})),
        }

    # Lane detection
    wf_lower = workflow_family.lower()
    if "csp" in wf_lower or "cross_stack" in wf_lower:
        card.lane = "csp"
    elif "drx" in wf_lower or "research" in wf_lower:
        card.lane = "drx"
    elif "qa" in wf_lower or "bug" in wf_lower:
        card.lane = "qa_manifest"
    else:
        card.lane = "general"

    # ── Final verdict — truth governance enforced ─────────────────────
    # RULE: Only evals verified by strict LLM judge can claim "production_ready".
    # Keyword-validator evals are "unverified" — the rate is reported but the
    # verdict is capped at "unverified_promising" until strict judge confirms.
    if card.total_runs < 5:
        card.final_verdict = "insufficient_data"
        card.verdict_reason = f"Only {card.total_runs} runs — need at least 5 for verdict"
    elif card.judge_type == "keyword_validator_unverified":
        # Cannot claim production_ready without strict judge
        card.final_verdict = "unverified_promising"
        card.verdict_reason = (
            f"{card.acceptable_rate:.0%} above composite threshold at {card.avg_cost_savings_pct:.0f}% cost savings, "
            f"BUT scored by keyword validators (known false positive risk). "
            f"Requires strict LLM judge re-evaluation before production claims."
        )
    elif card.acceptable_rate >= 0.9:
        card.final_verdict = "production_ready"
        card.verdict_reason = (
            f"{card.acceptable_rate:.0%} acceptable under strict LLM judge "
            f"at {card.avg_cost_savings_pct:.0f}% cost savings"
        )
    elif card.acceptable_rate >= 0.7:
        card.final_verdict = "needs_escalation"
        card.verdict_reason = (
            f"{card.acceptable_rate:.0%} acceptable under strict judge — "
            f"escalation policy needed for {card.escalation_rate:.0%} edge cases"
        )
    else:
        card.final_verdict = "not_ready"
        card.verdict_reason = (
            f"Only {card.acceptable_rate:.0%} acceptable under strict judge — "
            f"needs stronger replay or more training data"
        )

    # Limitations
    card.limitations = _detect_limitations(evals, card)

    # Persist
    _save_card(card)

    return card


def generate_all_cards() -> list[BenchmarkCard]:
    """Generate benchmark cards for all workflow families found in eval data."""
    workflows = _discover_workflows()
    cards = []
    for wf in workflows:
        card = generate_card(wf)
        cards.append(card)
    return cards


def get_compare_pane(eval_id: str) -> Optional[ComparePane]:
    """Build a three-pane compare view from a single eval."""
    eval_data = _load_eval(eval_id)
    if not eval_data:
        return None

    return ComparePane(
        frontier_tokens=eval_data.get("tokens_baseline", 0),
        frontier_cost_usd=eval_data.get("cost_baseline_usd", 0),
        replay_tokens=eval_data.get("tokens_replay", 0),
        replay_cost_usd=eval_data.get("cost_replay_usd", 0),
        composite_score=eval_data.get("composite_score", 0),
        grade=eval_data.get("grade", "?"),
        completion_score=eval_data.get("completion_score", 0),
        outcome_equivalence=eval_data.get("outcome_equivalence", False),
        escalation_count=eval_data.get("escalation_count", 0),
        artifacts_present=eval_data.get("artifacts_present", {}),
    )


# ─── Helpers ─────────────────────────────────────────────────────────────

def _load_evals_for_workflow(workflow: str) -> list[dict[str, Any]]:
    """Load all eval files matching a workflow family."""
    if not _EVAL_DIR.exists():
        return []
    evals = []
    for f in _EVAL_DIR.glob("*.json"):
        try:
            e = json.loads(f.read_text())
            if e.get("workflow", "") == workflow or e.get("task_name", "") == workflow:
                evals.append(e)
        except (json.JSONDecodeError, OSError):
            continue
    return evals


def _load_eval(eval_id: str) -> Optional[dict[str, Any]]:
    """Load a single eval by ID."""
    path = _EVAL_DIR / f"{eval_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _discover_workflows() -> list[str]:
    """Discover all unique workflow families from eval files."""
    if not _EVAL_DIR.exists():
        return []
    workflows = set()
    for f in _EVAL_DIR.glob("*.json"):
        try:
            e = json.loads(f.read_text())
            wf = e.get("workflow") or e.get("task_name", "")
            if wf:
                workflows.add(wf)
        except (json.JSONDecodeError, OSError):
            continue
    return sorted(workflows)


def _avg(evals: list[dict], key: str) -> float:
    """Average a numeric field across evals."""
    vals = [e.get(key, 0) for e in evals if isinstance(e.get(key), (int, float))]
    return round(sum(vals) / max(len(vals), 1), 3) if vals else 0.0


def _detect_limitations(evals: list[dict], card: BenchmarkCard) -> list[str]:
    """Auto-detect limitations from eval data. Truth governance enforced."""
    limitations = []

    # ── CRITICAL: Judge provenance warning ────────────────────────────
    if card.judge_type == "keyword_validator_unverified":
        limitations.insert(0,
            "UNVERIFIED: Scores are from keyword validators, NOT a strict LLM judge. "
            "Keyword validators had 100% false positive rate in calibration v1. "
            "All acceptance rates should be treated as upper bounds until strict judge re-evaluation."
        )

    # Check if precision/recall are all 1.0 (hallmark of keyword validator)
    perfect_targeting = sum(
        1 for e in evals
        if e.get("targeting", {}).get("precision", 0) == 1.0
        and e.get("targeting", {}).get("recall", 0) == 1.0
    )
    if perfect_targeting == len(evals) and len(evals) > 5:
        limitations.append(
            f"All {len(evals)} evals report perfect precision=1.0 and recall=1.0 — "
            f"this is characteristic of keyword validators, not real quality measurement."
        )

    if card.total_runs < 10:
        limitations.append(f"Small sample size ({card.total_runs} runs) — results may not generalize")

    if card.avg_artifact_completeness < 0.7:
        limitations.append(f"Artifact completeness low ({card.avg_artifact_completeness:.0%}) — missing screenshots or failure bundles")

    if card.escalation_rate > 0.2:
        limitations.append(f"High escalation rate ({card.escalation_rate:.0%}) — replay model struggles with {card.escalation_rate * card.total_runs:.0f} cases")

    if len(card.by_model) == 1:
        limitations.append("Single model tested — cross-model validation needed")

    oe_fails = sum(1 for e in evals if not e.get("outcome_equivalence", True))
    if oe_fails > 0:
        limitations.append(f"{oe_fails}/{card.total_runs} runs had outcome equivalence failures")

    return limitations


def _save_card(card: BenchmarkCard) -> None:
    """Persist a benchmark card to disk."""
    path = _CARD_DIR / f"{card.workflow_family}.json"
    path.write_text(json.dumps(asdict(card), indent=2, default=str))


def load_card(workflow_family: str) -> Optional[BenchmarkCard]:
    """Load a persisted benchmark card."""
    path = _CARD_DIR / f"{workflow_family}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return BenchmarkCard(**{k: v for k, v in data.items() if k in BenchmarkCard.__dataclass_fields__})
    except (json.JSONDecodeError, TypeError):
        return None


def list_cards() -> list[dict[str, Any]]:
    """List all persisted benchmark cards (summary view)."""
    if not _CARD_DIR.exists():
        return []
    cards = []
    for f in _CARD_DIR.glob("*.json"):
        if f.name.startswith("_"):
            continue
        try:
            data = json.loads(f.read_text())
            cards.append({
                "workflow_family": data.get("workflow_family", ""),
                "lane": data.get("lane", ""),
                "total_runs": data.get("total_runs", 0),
                "acceptable_rate": data.get("acceptable_rate", 0),
                "avg_cost_savings_pct": data.get("avg_cost_savings_pct", 0),
                "final_verdict": data.get("final_verdict", ""),
                "grade_distribution": data.get("grade_distribution", {}),
                "generated_at": data.get("generated_at", ""),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return sorted(cards, key=lambda c: c.get("total_runs", 0), reverse=True)
