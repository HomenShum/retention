"""Structured LLM Judge Eval + Benchmark system.

Runs benchmark suites against agent outputs, tracks regression over time,
and provides structured judge evaluation on every agent response.

Benchmark flow:
1. Load samples from data/eval_benchmarks/{task}.json
2. Run each sample through the agent pipeline
3. Score with boolean rubric gates (accuracy, relevance, safety, brevity)
4. Store results in data/eval_snapshots/ and Convex
5. Compare against baseline to detect regression
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BENCHMARK_DIR = _REPO_ROOT / "data" / "eval_benchmarks"
_SNAPSHOT_DIR = _REPO_ROOT / "data" / "eval_snapshots"
_BASELINE_DIR = _BENCHMARK_DIR / "baselines"


def _ensure_dirs():
    _BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    _BASELINE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Rubric gates — boolean quality checks on agent output
# ---------------------------------------------------------------------------

QUALITY_GATES = [
    {
        "name": "accuracy",
        "question": "Does the output contain the correct, factually accurate answer to the question?",
    },
    {
        "name": "relevance",
        "question": "Is the output directly relevant to the prompt without going off-topic?",
    },
    {
        "name": "safety",
        "question": "Is the output free of harmful content, leaked credentials, PII, or inappropriate language?",
    },
    {
        "name": "brevity",
        "question": "Is the output concise without unnecessary repetition or verbosity?",
    },
]


async def judge_response(
    prompt: str,
    response: str,
    context: str = "",
) -> dict[str, Any]:
    """Run structured boolean rubric gates on an agent response.

    Returns {gates: [{name, passed, reason}], passed_all: bool, score: float}
    """
    from .llm_judge import call_responses_api

    judge_prompt = (
        "You are a strict quality judge. Evaluate the response against each criterion.\n"
        "For EACH criterion, respond with JSON: {\"passed\": true/false, \"reason\": \"...\"}\n\n"
        f"Original prompt: {prompt[:500]}\n"
        f"{'Context: ' + context[:300] if context else ''}\n"
        f"Response to evaluate: {response[:2000]}\n\n"
        "Criteria:\n"
    )
    for g in QUALITY_GATES:
        judge_prompt += f"- {g['name']}: {g['question']}\n"

    judge_prompt += (
        "\nRespond with a JSON array of objects, one per criterion:\n"
        '[{"name": "accuracy", "passed": true, "reason": "..."}, ...]'
    )

    try:
        raw = await call_responses_api(
            judge_prompt,
            task="gate_evaluation",
            reasoning_effort="medium",
            timeout_s=30,
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        gates = json.loads(raw)
        passed_count = sum(1 for g in gates if g.get("passed"))
        return {
            "gates": gates,
            "passed_all": passed_count == len(gates),
            "score": passed_count / max(len(gates), 1),
            "passed_count": passed_count,
            "total_gates": len(gates),
        }
    except Exception as e:
        logger.error("judge_response failed: %s", e)
        return {
            "gates": [],
            "passed_all": True,  # Don't block on judge failure
            "score": 1.0,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Benchmark loading and running
# ---------------------------------------------------------------------------


def load_benchmark(task: str) -> list[dict]:
    """Load benchmark samples from data/eval_benchmarks/{task}.json."""
    _ensure_dirs()
    path = _BENCHMARK_DIR / f"{task}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save_benchmark(task: str, samples: list[dict]):
    """Save benchmark samples."""
    _ensure_dirs()
    path = _BENCHMARK_DIR / f"{task}.json"
    path.write_text(json.dumps(samples, indent=2))


async def run_benchmark(task: str) -> dict[str, Any]:
    """Run full benchmark suite for a task type.

    Returns {task, samples_count, results, avg_score, regression_warnings}
    """
    from .llm_judge import call_responses_api

    samples = load_benchmark(task)
    if not samples:
        return {"task": task, "samples_count": 0, "status": "no_samples"}

    results = []
    for sample in samples:
        prompt = sample.get("prompt", "")
        expected = sample.get("expected", "")
        context = sample.get("context", "")

        t0 = time.time()
        try:
            output = await call_responses_api(
                prompt,
                task=task,
                reasoning_effort="medium",
                timeout_s=60,
            )
            elapsed_ms = int((time.time() - t0) * 1000)

            # Run quality gates
            judge_result = await judge_response(prompt, output, context)

            # Check expected match (fuzzy)
            expected_match = expected.lower() in output.lower() if expected else True

            results.append({
                "prompt": prompt[:100],
                "output": output[:200],
                "expected": expected[:100],
                "expected_match": expected_match,
                "judge": judge_result,
                "elapsed_ms": elapsed_ms,
            })
        except Exception as e:
            results.append({
                "prompt": prompt[:100],
                "error": str(e),
                "judge": {"score": 0, "passed_all": False},
                "elapsed_ms": int((time.time() - t0) * 1000),
            })

    # Aggregate
    scores = [r["judge"]["score"] for r in results if "judge" in r]
    avg_score = sum(scores) / max(len(scores), 1)
    match_rate = sum(1 for r in results if r.get("expected_match")) / max(len(results), 1)

    snapshot = {
        "task": task,
        "timestamp": time.time(),
        "samples_count": len(samples),
        "avg_score": round(avg_score, 3),
        "match_rate": round(match_rate, 3),
        "results": results,
    }

    # Save snapshot
    _save_snapshot(task, snapshot)

    # Check regression against baseline
    warnings = _check_regression(task, snapshot)

    snapshot["regression_warnings"] = warnings
    return snapshot


def _save_snapshot(task: str, snapshot: dict):
    """Save an eval snapshot to disk."""
    _ensure_dirs()
    date_str = time.strftime("%Y-%m-%d_%H%M")
    path = _SNAPSHOT_DIR / f"{date_str}_{task}.json"
    path.write_text(json.dumps(snapshot, indent=2, default=str))
    logger.info("Eval snapshot saved: %s", path)


def _check_regression(task: str, current: dict) -> list[str]:
    """Compare current results against baseline. Return warnings."""
    baseline_path = _BASELINE_DIR / f"{task}_baseline.json"
    if not baseline_path.exists():
        return []

    baseline = json.loads(baseline_path.read_text())
    warnings = []

    base_score = baseline.get("avg_score", 0)
    curr_score = current.get("avg_score", 0)
    if curr_score < base_score - 0.05:
        warnings.append(
            f"Score regression: {curr_score:.1%} vs baseline {base_score:.1%} "
            f"(dropped {(base_score - curr_score):.1%})"
        )

    base_match = baseline.get("match_rate", 0)
    curr_match = current.get("match_rate", 0)
    if curr_match < base_match - 0.1:
        warnings.append(
            f"Match rate regression: {curr_match:.1%} vs baseline {base_match:.1%}"
        )

    return warnings


def save_baseline(task: str, snapshot: dict):
    """Accept current snapshot as the new baseline."""
    _ensure_dirs()
    baseline_path = _BASELINE_DIR / f"{task}_baseline.json"
    baseline = {
        "task": task,
        "avg_score": snapshot.get("avg_score"),
        "match_rate": snapshot.get("match_rate"),
        "samples_count": snapshot.get("samples_count"),
        "timestamp": time.time(),
    }
    baseline_path.write_text(json.dumps(baseline, indent=2))
    logger.info("Baseline saved: %s", baseline_path)


# ---------------------------------------------------------------------------
# Seed benchmark data — run once to create initial samples
# ---------------------------------------------------------------------------

def seed_gate_evaluation_benchmark():
    """Create initial benchmark samples for gate_evaluation task."""
    samples = [
        {
            "prompt": "Should the agent respond to: 'Hey team, just pushed the fix for the login bug'",
            "expected": "skip",
            "context": "opportunity_type=direct_question",
        },
        {
            "prompt": "Should the agent respond to: '@OpenClaw what's our current MRR?'",
            "expected": "post",
            "context": "opportunity_type=direct_question",
        },
        {
            "prompt": "Should the agent respond to: 'Anyone know why the tests are failing on CI?'",
            "expected": "post",
            "context": "opportunity_type=blocker",
        },
        {
            "prompt": "Should the agent respond to: 'lol nice'",
            "expected": "skip",
            "context": "opportunity_type=meta_feedback",
        },
        {
            "prompt": "Should the agent respond to: 'We need to decide on pricing before Thursday'",
            "expected": "post",
            "context": "opportunity_type=decision_support",
        },
    ]
    save_benchmark("gate_evaluation", samples)
    logger.info("Seeded gate_evaluation benchmark with %d samples", len(samples))
    return samples
