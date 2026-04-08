"""
Benchmark Research Agent — DeerFlow-style planning→execution→reflection loop.

Inspired by:
  - bytedance/deer-flow: multi-agent research with planning, execution, reflection
  - instructkr/claw-code: code generation with iterative refinement

This agent orchestrates benchmark experiments:
  1. PLAN: Analyze prior results, decide which models/tasks to benchmark next
  2. EXECUTE: Run three-lane benchmarks via existing infrastructure
  3. REFLECT: Analyze results, identify underperformers, propose improvements
  4. PERSIST: Store findings to agent memory for future reference

Uses OpenAI Responses API via AgentRunner pattern.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_RESEARCH_DIR = _DATA_DIR / "benchmark_research"
_RESEARCH_DIR.mkdir(parents=True, exist_ok=True)


# ─── Research State ─────────────────────────────────────────────────────

class BenchmarkResearchState:
    """Tracks the research agent's plan, findings, and next steps."""

    def __init__(self, research_id: str = ""):
        self.research_id = research_id or f"research-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.plan: Dict[str, Any] = {}
        self.findings: List[Dict[str, Any]] = []
        self.reflections: List[str] = []
        self.next_experiments: List[Dict[str, Any]] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "research_id": self.research_id,
            "plan": self.plan,
            "findings": self.findings,
            "reflections": self.reflections,
            "next_experiments": self.next_experiments,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def save(self):
        path = _RESEARCH_DIR / f"{self.research_id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, research_id: str) -> Optional["BenchmarkResearchState"]:
        path = _RESEARCH_DIR / f"{research_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        state = cls(research_id=data["research_id"])
        state.plan = data.get("plan", {})
        state.findings = data.get("findings", [])
        state.reflections = data.get("reflections", [])
        state.next_experiments = data.get("next_experiments", [])
        return state


# ─── Phase 1: PLAN ──────────────────────────────────────────────────────

def plan_experiments(
    prior_results: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Analyze prior benchmark results and propose next experiments.

    DeerFlow pattern: the planner looks at what we already know and
    identifies gaps in our understanding.
    """
    from ...benchmarks.three_lane_benchmark import (
        list_benchmark_results,
        get_available_models,
    )
    from ...benchmarks.rerun_eval import list_eval_results

    # Gather current knowledge
    existing_benchmarks = prior_results or list_benchmark_results()
    existing_evals = list_eval_results()
    available_models = get_available_models()

    # Identify which models have been tested
    tested_models = set()
    for ev in existing_evals:
        model = ev.get("model", "")
        if model:
            tested_models.add(model)

    # Models not yet tested
    untested_models = [
        m for m in available_models
        if m["id"] not in tested_models
    ]

    # Identify weak spots (low composite scores)
    weak_evals = [
        ev for ev in existing_evals
        if ev.get("composite_score", 1.0) < 0.7
    ]

    # Build experiment plan
    experiments = []

    # Priority 1: Test untested models
    for model in untested_models[:5]:
        experiments.append({
            "type": "model_eval",
            "model": model["id"],
            "label": model["label"],
            "reason": f"Not yet benchmarked — {model['label']} at ${model['input_price']}/1M input",
            "priority": "high",
        })

    # Priority 2: Re-test weak performers with different effort levels
    for ev in weak_evals[:3]:
        base_model = ev.get("model", "").split(":")[0]
        experiments.append({
            "type": "effort_comparison",
            "model": base_model,
            "efforts": ["high", "xhigh"],
            "reason": f"Grade {ev.get('grade', '?')} on {ev.get('task_name', '?')} — try higher effort",
            "priority": "medium",
        })

    # Priority 3: Cross-provider comparison
    if not any("claude" in m for m in tested_models) or not any("gpt" in m for m in tested_models):
        experiments.append({
            "type": "cross_provider",
            "models": ["gpt-5.4:high", "claude-opus-4-6", "claude-sonnet-4-6"],
            "reason": "Need cross-provider comparison for investor deck",
            "priority": "high",
        })

    plan = {
        "total_existing_benchmarks": len(existing_benchmarks),
        "total_existing_evals": len(existing_evals),
        "tested_models": sorted(tested_models),
        "untested_models": [m["id"] for m in untested_models],
        "weak_evals_count": len(weak_evals),
        "proposed_experiments": experiments,
        "planned_at": datetime.now(timezone.utc).isoformat(),
    }

    return plan


# ─── Phase 2: EXECUTE ───────────────────────────────────────────────────

def execute_experiment(
    experiment: Dict[str, Any],
    replay_result_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute a single benchmark experiment.

    Uses existing replay data (offline mode) for speed.
    """
    from ...benchmarks.three_lane_benchmark import run_multi_model_eval_offline
    from ...benchmarks.rerun_eval import run_rerun_eval

    exp_type = experiment.get("type", "model_eval")

    # Get replay IDs
    if not replay_result_ids:
        replay_dir = _DATA_DIR / "replay_results"
        if replay_dir.exists():
            replay_result_ids = [f.stem for f in sorted(replay_dir.glob("*.json"))[:5]]
    if not replay_result_ids:
        return {"error": "No replay results available", "experiment": experiment}

    if exp_type == "model_eval":
        model = experiment.get("model", "gpt-5.4-mini")
        result = run_multi_model_eval_offline(
            task_name=f"research_{model}",
            replay_result_ids=replay_result_ids,
            models=[model],
        )
        return {
            "type": exp_type,
            "model": model,
            "benchmark_id": result.benchmark_id,
            "models_evaluated": len(result.models),
            "summary": result.summary,
            "results": [m.model_dump() if hasattr(m, 'model_dump') else {} for m in result.models],
        }

    elif exp_type == "effort_comparison":
        base_model = experiment.get("model", "gpt-5.4")
        efforts = experiment.get("efforts", ["high", "xhigh"])
        models = [f"{base_model}:{e}" for e in efforts]
        result = run_multi_model_eval_offline(
            task_name=f"research_effort_{base_model}",
            replay_result_ids=replay_result_ids,
            models=models,
        )
        return {
            "type": exp_type,
            "base_model": base_model,
            "efforts": efforts,
            "benchmark_id": result.benchmark_id,
            "summary": result.summary,
            "results": [m.model_dump() if hasattr(m, 'model_dump') else {} for m in result.models],
        }

    elif exp_type == "cross_provider":
        models = experiment.get("models", [])
        result = run_multi_model_eval_offline(
            task_name="research_cross_provider",
            replay_result_ids=replay_result_ids,
            models=models,
        )
        return {
            "type": exp_type,
            "models": models,
            "benchmark_id": result.benchmark_id,
            "summary": result.summary,
            "results": [m.model_dump() if hasattr(m, 'model_dump') else {} for m in result.models],
        }

    return {"error": f"Unknown experiment type: {exp_type}"}


# ─── Phase 3: REFLECT ───────────────────────────────────────────────────

def reflect_on_findings(
    findings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Analyze experiment findings and generate insights.

    DeerFlow pattern: the reflector identifies patterns, surprises,
    and proposes next experiments.
    """
    insights = []
    surprises = []
    next_steps = []

    for finding in findings:
        results = finding.get("results", [])
        for r in results:
            grade = r.get("grade", "F")
            model = r.get("model", "unknown")
            label = r.get("label", model)
            cost = r.get("cost_per_run_usd", 0)
            composite = r.get("composite_score", 0)

            if grade in ("A", "B") and cost < 0.005:
                insights.append(f"{label} achieves grade {grade} at only ${cost:.4f}/run — strong distillation candidate")
            if grade == "F":
                insights.append(f"{label} failed (grade F) — may need higher effort or different task")
            if composite > 0.8 and cost < 0.001:
                surprises.append(f"{label} is surprisingly strong: composite {composite:.2f} at ${cost:.4f}/run")

    # Propose next experiments based on findings
    if any("distillation candidate" in i for i in insights):
        next_steps.append({
            "type": "distillation_eval",
            "reason": "Strong cheap models found — generate distillation dataset",
            "priority": "high",
        })

    if surprises:
        next_steps.append({
            "type": "deep_eval",
            "reason": "Surprising results found — run with more replay data for statistical significance",
            "priority": "medium",
        })

    return {
        "insights": insights,
        "surprises": surprises,
        "next_steps": next_steps,
        "reflected_at": datetime.now(timezone.utc).isoformat(),
    }


# ─── Full research loop ────────────────────────────────────────────────

def run_research_loop(
    max_experiments: int = 5,
    replay_result_ids: Optional[List[str]] = None,
) -> BenchmarkResearchState:
    """Run the full plan→execute→reflect loop.

    This is the DeerFlow/Claw-Code pattern:
    1. Plan experiments based on prior knowledge
    2. Execute top-priority experiments
    3. Reflect on findings
    4. Persist state for future loops
    """
    state = BenchmarkResearchState()

    # Phase 1: Plan
    logger.info("Phase 1: Planning experiments...")
    state.plan = plan_experiments()
    experiments = state.plan.get("proposed_experiments", [])
    logger.info(f"Proposed {len(experiments)} experiments")

    # Phase 2: Execute top-priority experiments
    to_run = sorted(experiments, key=lambda e: {"high": 0, "medium": 1, "low": 2}.get(e.get("priority", "low"), 2))
    to_run = to_run[:max_experiments]

    for exp in to_run:
        logger.info(f"Phase 2: Executing {exp.get('type')}: {exp.get('reason', '')[:60]}")
        finding = execute_experiment(exp, replay_result_ids)
        state.findings.append(finding)

    # Phase 3: Reflect
    logger.info("Phase 3: Reflecting on findings...")
    reflection = reflect_on_findings(state.findings)
    state.reflections = reflection.get("insights", []) + reflection.get("surprises", [])
    state.next_experiments = reflection.get("next_steps", [])

    # Phase 4: Persist
    state.save()
    logger.info(f"Research loop complete: {state.research_id}")
    logger.info(f"  Findings: {len(state.findings)}")
    logger.info(f"  Insights: {len(state.reflections)}")
    logger.info(f"  Next experiments: {len(state.next_experiments)}")

    return state


def list_research_runs() -> List[Dict[str, Any]]:
    """List all saved research runs."""
    results = []
    for f in _RESEARCH_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            results.append({
                "research_id": data.get("research_id"),
                "timestamp": data.get("timestamp"),
                "findings_count": len(data.get("findings", [])),
                "insights_count": len(data.get("reflections", [])),
            })
        except Exception:
            continue
    return sorted(results, key=lambda x: x.get("timestamp", ""), reverse=True)
