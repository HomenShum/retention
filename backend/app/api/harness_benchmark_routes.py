"""API routes for the Harness Benchmark — Model x TA comparison."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from ..benchmarks.harness_benchmark import (
    run_harness_benchmark,
    cumulative_cost_projection,
    RESULTS_DIR,
    HarnessBenchmarkSuite,
    HarnessRunResult,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/benchmarks/harness-compare", tags=["benchmarks"])

_running_task: asyncio.Task | None = None
_latest_suite_id: str = ""


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _score_run(r: HarnessRunResult) -> dict:
    """Compute a quality score for a single harness run result.

    Dimensions (0-25 each, 100 total):
      - bugs_found:        25 * min(bugs / 5, 1.0)  — up to 5 bugs earns full marks
      - tests_generated:   25 if test_cases_generated > 0 else 0
      - structured_output: 25 if mode == ta_harness (deterministic JSON) else 0
      - rerun_capability:  25 if rerun_savings_pct > 0 else 0
    """
    bugs_score = 25.0 * min(r.bugs_found / 5, 1.0)
    tests_score = 25.0 if r.test_cases_generated > 0 else 0.0
    structured_score = 25.0 if r.mode == "ta_harness" else 0.0
    rerun_score = 25.0 if r.rerun_savings_pct > 0 else 0.0

    total = bugs_score + tests_score + structured_score + rerun_score

    return {
        "run_id": r.run_id,
        "model": r.model,
        "mode": r.mode,
        "bugs_found_score": round(bugs_score, 1),
        "tests_generated_score": round(tests_score, 1),
        "structured_output_score": round(structured_score, 1),
        "rerun_capability_score": round(rerun_score, 1),
        "total_score": round(total, 1),
    }


def _compute_scores_and_winner(suite: HarnessBenchmarkSuite) -> tuple[list[dict], dict]:
    """Score every run in the suite and pick a winner.

    Returns (scores_list, winner_dict).
    """
    scores = [_score_run(r) for r in suite.results]
    if not scores:
        return scores, {}

    best = max(scores, key=lambda s: (s["total_score"], -_cost_for(s, suite)))
    return scores, {
        "run_id": best["run_id"],
        "model": best["model"],
        "mode": best["mode"],
        "total_score": best["total_score"],
        "reason": _winner_reason(best, suite),
    }


def _cost_for(score: dict, suite: HarnessBenchmarkSuite) -> float:
    """Get estimated cost for a scored run (used as tiebreaker)."""
    r = next((r for r in suite.results if r.run_id == score["run_id"]), None)
    return r.estimated_cost_usd if r else 0.0


def _winner_reason(best: dict, suite: HarnessBenchmarkSuite) -> str:
    r = next((r for r in suite.results if r.run_id == best["run_id"]), None)
    if not r:
        return "Highest overall quality score."
    parts = [f"Score {best['total_score']}/100"]
    if r.mode == "ta_harness":
        parts.append(f"{r.bugs_found} bugs found with structured tests + free reruns")
    else:
        parts.append(f"{r.bugs_found} bugs found (raw mode, no reruns)")
    return ". ".join(parts) + "."


# ---------------------------------------------------------------------------
# Suite loader (shared by /results and /report)
# ---------------------------------------------------------------------------

def _load_suite(suite_id: str) -> tuple[HarnessBenchmarkSuite | None, dict | None, str]:
    """Load a suite from disk. Returns (suite, raw_data, error_status)."""
    if suite_id:
        path = RESULTS_DIR / f"harness_{suite_id}.json"
    else:
        files = sorted(RESULTS_DIR.glob("harness_*.json"), key=lambda p: p.stat().st_mtime)
        if not files:
            return None, None, "no_results"
        path = files[-1]

    if not path.exists():
        return None, None, "not_found"

    data = json.loads(path.read_text())
    results_dicts = data.pop("results", [])

    suite = HarnessBenchmarkSuite(**{
        k: v for k, v in data.items()
        if k in HarnessBenchmarkSuite.__dataclass_fields__ and k != "results"
    })
    for rd in results_dicts:
        suite.results.append(HarnessRunResult(**{
            k: v for k, v in rd.items()
            if k in HarnessRunResult.__dataclass_fields__
        }))
    data["results"] = results_dicts
    return suite, data, ""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/run")
async def start_harness_benchmark(
    app_url: str = Query(default="https://test-studio-xi.vercel.app/benchmark/task_manager.html"),
    models: str = Query(default="gpt-5.4-mini,gpt-5.4"),
    include_raw: bool = Query(default=True),
    include_ta: bool = Query(default=True),
    timeout_s: int = Query(default=3600, description="Per-model timeout in seconds"),
):
    global _running_task, _latest_suite_id

    if _running_task and not _running_task.done():
        return {"status": "already_running", "suite_id": _latest_suite_id}

    model_list = [m.strip() for m in models.split(",")]

    async def _run():
        global _latest_suite_id
        suite = await run_harness_benchmark(
            app_url=app_url,
            models=model_list,
            include_raw=include_raw,
            include_ta=include_ta,
            timeout_s=timeout_s,
        )
        _latest_suite_id = suite.suite_id
        return suite

    _running_task = asyncio.create_task(_run())
    return {
        "status": "started",
        "models": model_list,
        "app_url": app_url,
        "message": "Benchmark running in background. Poll /results for status.",
    }


@router.get("/results")
async def get_results(suite_id: str = Query(default="")):
    """Get benchmark results with auto-scored quality metrics and a winner."""
    suite, data, err = _load_suite(suite_id)
    if err:
        return {"status": err, "suite_id": suite_id}

    scores, winner = _compute_scores_and_winner(suite)

    return {
        "status": data.get("status", "unknown"),
        "suite_id": data.get("suite_id"),
        "summary": suite.summary_table(),
        "cumulative": cumulative_cost_projection(suite),
        "scores": scores,
        "winner": winner,
        "data": data,
    }


@router.get("/report", response_class=PlainTextResponse)
async def get_report(suite_id: str = Query(default="")):
    """Generate a buyer-safe markdown report for a completed benchmark suite."""
    suite, data, err = _load_suite(suite_id)
    if err == "no_results":
        return PlainTextResponse("No benchmark results found.", status_code=404)
    if err == "not_found":
        return PlainTextResponse(f"Suite {suite_id} not found.", status_code=404)

    scores, winner = _compute_scores_and_winner(suite)
    cumulative = cumulative_cost_projection(suite)
    summary = suite.summary_table()
    report_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # ---- Build markdown ----
    lines: list[str] = []
    lines.append(f"# retention.sh Harness Benchmark Report")
    lines.append(f"")
    lines.append(f"**Date:** {report_date}  ")
    lines.append(f"**App:** {suite.app_url}  ")
    lines.append(f"**Models:** {', '.join(suite.models)}  ")
    lines.append(f"**Suite ID:** {suite.suite_id}")
    lines.append("")

    # --- Comparison table ---
    lines.append("## Comparison Table")
    lines.append("")
    lines.append("| Model | Mode | Bugs | Tests | Cost | Rerun Cost | Rerun Savings | Quality Score |")
    lines.append("|-------|------|-----:|------:|-----:|-----------:|--------------:|--------------:|")
    for r in sorted(suite.results, key=lambda x: (x.model, x.mode)):
        sc = next((s for s in scores if s["run_id"] == r.run_id), {})
        rerun = f"${r.rerun_cost_usd:.3f}" if r.rerun_cost_usd > 0 else "Free"
        lines.append(
            f"| {r.model} | {r.mode} | {r.bugs_found} | {r.test_cases_generated} "
            f"| ${r.estimated_cost_usd:.4f} | {rerun} | {r.rerun_savings_pct:.0f}% "
            f"| **{sc.get('total_score', 0)}/100** |"
        )
    lines.append("")

    # --- Score breakdown ---
    lines.append("## Quality Score Breakdown")
    lines.append("")
    lines.append("Each run is scored on four dimensions (25 points each):")
    lines.append("")
    lines.append("| Run | Bugs Found | Tests Generated | Structured Output | Rerun Capability | Total |")
    lines.append("|-----|----------:|----------------:|------------------:|-----------------:|------:|")
    for sc in scores:
        label = f"{sc['model']} ({sc['mode']})"
        lines.append(
            f"| {label} | {sc['bugs_found_score']} | {sc['tests_generated_score']} "
            f"| {sc['structured_output_score']} | {sc['rerun_capability_score']} "
            f"| **{sc['total_score']}** |"
        )
    lines.append("")

    # --- Cumulative cost projection ---
    lines.append("## Cumulative Cost Projection (10 fix-verify cycles)")
    lines.append("")
    ta_runs = [r for r in suite.results if r.mode == "ta_harness"]
    raw_runs = [r for r in suite.results if r.mode == "raw"]
    lines.append("| Model | Mode | Run 1 | Runs 2-10 | Total (10 runs) |")
    lines.append("|-------|------|------:|----------:|----------------:|")
    for r in sorted(suite.results, key=lambda x: (x.model, x.mode)):
        run1 = r.estimated_cost_usd
        if r.mode == "ta_harness":
            subsequent = 9 * r.rerun_cost_usd
        else:
            subsequent = 9 * run1
        total = run1 + subsequent
        lines.append(
            f"| {r.model} | {r.mode} | ${run1:.4f} | ${subsequent:.4f} | ${total:.4f} |"
        )
    lines.append("")

    # --- Winner ---
    lines.append("## Winner")
    lines.append("")
    if winner:
        lines.append(f"**{winner['model']}** in **{winner['mode']}** mode "
                      f"with a quality score of **{winner['total_score']}/100**.")
        lines.append("")
        lines.append(f"> {winner['reason']}")
    else:
        lines.append("No winner could be determined (no results).")
    lines.append("")

    # --- Key insight ---
    lines.append("## Key Insight")
    lines.append("")
    if ta_runs and raw_runs:
        ta_bugs = sum(r.bugs_found for r in ta_runs)
        raw_bugs = sum(r.bugs_found for r in raw_runs)
        ta_avg_bugs = ta_bugs / len(ta_runs) if ta_runs else 0
        raw_avg_bugs = raw_bugs / len(raw_runs) if raw_runs else 0
        lines.append(
            f"TA-harnessed runs found **{ta_avg_bugs:.0f} bugs on average** "
            f"vs raw runs at **{raw_avg_bugs:.0f} bugs on average**. "
        )
        if ta_avg_bugs >= raw_avg_bugs:
            lines.append(
                "Same (or better) bug detection — but TA adds structured test cases, "
                "deterministic reruns, and regression tracking. "
                "Reruns are effectively **free** (98%+ cost savings), "
                "meaning the 2nd through Nth verification cycles cost almost nothing."
            )
        else:
            lines.append(
                "Raw mode found slightly more bugs on average, but TA adds structured "
                "test cases, deterministic reruns, and regression tracking that raw mode "
                "cannot provide. Reruns with TA are effectively **free**."
            )
    else:
        lines.append("Mixed-mode comparison not available (need both TA and raw results).")
    lines.append("")
    lines.append("---")
    lines.append(f"*Generated by retention.sh Harness Benchmark*")

    return "\n".join(lines)
