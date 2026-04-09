"""QA Pipeline Benchmark — measure time and token cost for consecutive QA flows.

Runs 1, 2, 5, 10 consecutive QA flows against a frozen app with planted bugs.
Tracks: wall clock time, tool calls, pass rate, precision, recall.

Usage (MCP):
  retention.benchmark.qa_pipeline  { "app_url": "http://localhost:3000", "consecutive_counts": "1,2,5,10" }

Usage (API):
  POST /api/benchmarks/qa-pipeline/run
  GET  /api/benchmarks/qa-pipeline/runs/{run_id}
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_BENCHMARK_DIR = Path(__file__).resolve().parents[2] / "data" / "benchmark_runs"
_BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)

# In-memory registry of running/completed benchmarks
_running_benchmarks: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PlantedBug:
    """A known bug planted in the frozen app for precision/recall measurement."""
    bug_id: str
    description: str
    category: str             # "crash", "ui", "logic", "navigation", "validation"
    match_keywords: list[str]  # Keywords to match in test failure names/descriptions


@dataclass
class QARunResult:
    """Result of a single QA pipeline execution."""
    run_index: int
    run_id: str
    wall_clock_s: float
    tool_calls: int
    tests_total: int
    tests_passed: int
    tests_failed: int
    pass_rate: float
    failures: list[dict] = field(default_factory=list)
    bugs_matched: list[str] = field(default_factory=list)  # bug_ids that were detected
    precision: float = 0.0    # bugs_matched / total_failures
    recall: float = 0.0       # bugs_matched / total_planted_bugs


@dataclass
class QABenchmarkResult:
    """Aggregate result across all consecutive run batches."""
    benchmark_id: str
    app_url: str
    app_name: str
    started_at: str
    completed_at: str = ""
    status: str = "running"
    planted_bugs_count: int = 0
    batches: list[dict] = field(default_factory=list)  # Per-batch (N consecutive runs) results
    error: str = ""


@dataclass
class BatchResult:
    """Result of running N consecutive QA flows."""
    consecutive_count: int
    runs: list[QARunResult]
    total_wall_clock_s: float
    avg_wall_clock_s: float
    total_tool_calls: int
    avg_tests_per_run: float
    avg_pass_rate: float
    aggregate_precision: float
    aggregate_recall: float
    unique_bugs_found: list[str]


# ---------------------------------------------------------------------------
# Core benchmark runner
# ---------------------------------------------------------------------------

def _match_bug(failure: dict, planted_bugs: list[PlantedBug]) -> Optional[str]:
    """Check if a test failure matches any planted bug. Returns bug_id or None."""
    failure_text = " ".join([
        failure.get("name", ""),
        failure.get("test_id", ""),
        str(failure.get("failing_step", {}).get("action", "")),
        str(failure.get("failing_step", {}).get("actual", "")),
        failure.get("error", ""),
    ]).lower()

    for bug in planted_bugs:
        if any(kw.lower() in failure_text for kw in bug.match_keywords):
            return bug.bug_id
    return None


async def run_qa_benchmark(
    app_url: str,
    app_name: str = "Benchmark App",
    consecutive_counts: list[int] | None = None,
    planted_bugs: list[PlantedBug] | None = None,
    device_id: str | None = None,
    flow_type: str = "web",
) -> QABenchmarkResult:
    """Run QA pipeline benchmark: execute N consecutive flows and measure performance.

    Args:
        app_url: URL of the frozen app to test
        app_name: Display name for the app
        consecutive_counts: List of batch sizes, e.g. [1, 2, 5, 10]
        planted_bugs: Known bugs for precision/recall calculation
        device_id: ADB device ID (auto-detect if None)
        flow_type: "web" or "android"
    """
    if consecutive_counts is None:
        consecutive_counts = [1, 2, 5, 10]
    if planted_bugs is None:
        planted_bugs = []

    benchmark_id = f"qa-bench-{uuid.uuid4().hex[:8]}"
    result = QABenchmarkResult(
        benchmark_id=benchmark_id,
        app_url=app_url,
        app_name=app_name,
        started_at=datetime.now(timezone.utc).isoformat(),
        planted_bugs_count=len(planted_bugs),
    )
    _running_benchmarks[benchmark_id] = asdict(result)

    try:
        from ..api.mcp_pipeline import dispatch_pipeline, dispatch_qa_verification, format_compact_bundle

        for count in consecutive_counts:
            logger.info(f"Benchmark {benchmark_id}: starting batch of {count} consecutive runs")
            runs: list[QARunResult] = []
            batch_start = time.monotonic()

            for i in range(count):
                run_start = time.monotonic()

                # Start pipeline
                tool = f"ta.run_{flow_type}_flow"
                run_args: dict[str, Any] = {"app_name": app_name, "timeout_seconds": 3600}
                if flow_type == "web":
                    run_args["url"] = app_url
                else:
                    run_args["app_package"] = app_url  # For android, app_url is the package name

                if device_id:
                    run_args["device_id"] = device_id

                start_result = await dispatch_qa_verification(tool, run_args)
                run_id = start_result.get("run_id", "")
                if not run_id:
                    logger.warning(f"Benchmark batch {count} run {i}: failed to start - {start_result}")
                    continue

                # Poll until complete (max 5 min)
                for _ in range(60):
                    await asyncio.sleep(5)
                    status = await dispatch_pipeline("retention.pipeline.status", {"run_id": run_id})
                    if status.get("status") in ("complete", "error"):
                        break

                wall_clock = round(time.monotonic() - run_start, 1)

                # Get compact bundle
                bundle = format_compact_bundle(run_id)
                summary = bundle.get("summary", {})
                failures = bundle.get("failures", [])

                # Match against planted bugs
                matched_bugs = set()
                for f in failures:
                    bug_id = _match_bug(f, planted_bugs)
                    if bug_id:
                        matched_bugs.add(bug_id)

                total_failures = summary.get("failed", len(failures))
                precision = len(matched_bugs) / total_failures if total_failures > 0 else 1.0
                recall = len(matched_bugs) / len(planted_bugs) if planted_bugs else 1.0

                run_result = QARunResult(
                    run_index=i,
                    run_id=run_id,
                    wall_clock_s=wall_clock,
                    tool_calls=0,  # TODO: extract from pipeline entry
                    tests_total=summary.get("total", 0),
                    tests_passed=summary.get("passed", 0),
                    tests_failed=summary.get("failed", 0),
                    pass_rate=summary.get("pass_rate", 0.0),
                    failures=failures,
                    bugs_matched=list(matched_bugs),
                    precision=round(precision, 4),
                    recall=round(recall, 4),
                )
                runs.append(run_result)
                logger.info(
                    f"Benchmark {benchmark_id} batch {count} run {i}: "
                    f"{wall_clock}s, {summary.get('total', 0)} tests, "
                    f"P={precision:.2f} R={recall:.2f}"
                )

            total_wall = round(time.monotonic() - batch_start, 1)
            all_bugs = set()
            for r in runs:
                all_bugs.update(r.bugs_matched)

            batch = BatchResult(
                consecutive_count=count,
                runs=[asdict(r) for r in runs],
                total_wall_clock_s=total_wall,
                avg_wall_clock_s=round(total_wall / max(len(runs), 1), 1),
                total_tool_calls=sum(r.tool_calls for r in runs),
                avg_tests_per_run=round(sum(r.tests_total for r in runs) / max(len(runs), 1), 1),
                avg_pass_rate=round(sum(r.pass_rate for r in runs) / max(len(runs), 1), 4),
                aggregate_precision=round(
                    sum(r.precision for r in runs) / max(len(runs), 1), 4
                ),
                aggregate_recall=round(len(all_bugs) / max(len(planted_bugs), 1), 4),
                unique_bugs_found=sorted(all_bugs),
            )
            result.batches.append(asdict(batch))

        result.status = "complete"
        result.completed_at = datetime.now(timezone.utc).isoformat()

    except Exception as exc:
        logger.exception(f"Benchmark {benchmark_id} failed")
        result.status = "error"
        result.error = str(exc)
    finally:
        # Persist to disk
        _persist_benchmark(benchmark_id, asdict(result))
        _running_benchmarks[benchmark_id] = asdict(result)

    return result


def _persist_benchmark(benchmark_id: str, data: dict) -> None:
    """Save benchmark results to disk."""
    bench_dir = _BENCHMARK_DIR / benchmark_id
    bench_dir.mkdir(parents=True, exist_ok=True)
    path = bench_dir / "results.json"
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info(f"Persisted QA benchmark: {path}")
    except Exception as e:
        logger.warning(f"Failed to persist benchmark {benchmark_id}: {e}")


def get_benchmark_result(benchmark_id: str) -> dict | None:
    """Retrieve a benchmark result by ID."""
    if benchmark_id in _running_benchmarks:
        return _running_benchmarks[benchmark_id]
    path = _BENCHMARK_DIR / benchmark_id / "results.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def list_benchmarks() -> list[dict]:
    """List all QA pipeline benchmark runs."""
    results = []
    for d in sorted(_BENCHMARK_DIR.iterdir(), reverse=True):
        if d.is_dir() and d.name.startswith("qa-bench-"):
            result_path = d / "results.json"
            if result_path.exists():
                try:
                    with open(result_path) as f:
                        data = json.load(f)
                    results.append({
                        "benchmark_id": data.get("benchmark_id", d.name),
                        "app_name": data.get("app_name", ""),
                        "status": data.get("status", ""),
                        "started_at": data.get("started_at", ""),
                        "batch_count": len(data.get("batches", [])),
                    })
                except Exception:
                    pass
    return results[:20]


# ---------------------------------------------------------------------------
# MCP dispatcher (called from mcp_server.py)
# ---------------------------------------------------------------------------

async def dispatch_qa_benchmark(tool: str, args: dict) -> Any:
    """Handle retention.benchmark.qa_pipeline tool calls."""
    if tool == "retention.benchmark.qa_pipeline":
        app_url = args.get("app_url")
        if not app_url:
            return {"error": "app_url is required"}

        app_name = args.get("app_name", "Benchmark App")
        counts_str = args.get("consecutive_counts", "1,2,5,10")
        try:
            counts = [int(c.strip()) for c in counts_str.split(",")]
        except ValueError:
            return {"error": f"Invalid consecutive_counts format: {counts_str}. Use comma-separated ints like '1,2,5,10'"}

        flow_type = args.get("flow_type", "web")
        device_id = args.get("device_id")

        # Load planted bugs from file if provided
        planted_bugs = []
        bugs_file = args.get("planted_bugs_file")
        if bugs_file:
            try:
                with open(bugs_file) as f:
                    bugs_data = json.load(f)
                planted_bugs = [PlantedBug(**b) for b in bugs_data]
            except Exception as e:
                return {"error": f"Failed to load planted bugs file: {e}"}

        # Run async in background
        benchmark_id = f"qa-bench-{uuid.uuid4().hex[:8]}"

        async def _run():
            return await run_qa_benchmark(
                app_url=app_url,
                app_name=app_name,
                consecutive_counts=counts,
                planted_bugs=planted_bugs,
                device_id=device_id,
                flow_type=flow_type,
            )

        asyncio.create_task(_run())

        return {
            "benchmark_id": benchmark_id,
            "status": "running",
            "consecutive_counts": counts,
            "planted_bugs_count": len(planted_bugs),
            "message": f"QA benchmark started. Will run {counts} consecutive batches against {app_url}.",
        }

    if tool == "retention.benchmark.qa_pipeline.status":
        benchmark_id = args.get("benchmark_id")
        if not benchmark_id:
            return {"benchmarks": list_benchmarks()}
        result = get_benchmark_result(benchmark_id)
        if not result:
            return {"error": f"No benchmark found: {benchmark_id}"}
        return result

    return {"error": f"Unknown benchmark tool: {tool}"}


# ---------------------------------------------------------------------------
# LLM Judge — structured evaluation of bug report quality
# ---------------------------------------------------------------------------

JUDGE_CRITERIA = [
    ("actionability", "Can a developer reproduce and fix the bug from this report alone? (1=vague, 5=immediately actionable)"),
    ("root_cause", "Does the report correctly identify what's broken, not just a symptom? (1=symptom only, 5=precise root cause)"),
    ("severity_accuracy", "Is the assigned priority/severity appropriate? (1=way off, 5=perfectly calibrated)"),
    ("evidence_quality", "Does the report include concrete evidence (screenshots, steps, expected vs actual)? (1=none, 5=complete)"),
    ("false_positive_risk", "How confident are you this is a real bug, not a test artifact? (1=likely false positive, 5=definitely real)"),
]


async def judge_bug_report(failure: dict, app_context: str = "") -> dict:
    """Use LLM to evaluate a single bug report's quality. Returns scores 1-5 per criterion."""
    import os
    try:
        import httpx
    except ImportError:
        return {"error": "httpx not installed"}

    report_text = json.dumps(failure, indent=2, default=str)

    prompt = f"""You are a QA engineering judge. Evaluate this bug report on 5 criteria.

App context: {app_context or 'A web/mobile application under QA testing.'}

Bug report:
{report_text}

Score each criterion from 1 (worst) to 5 (best). Return ONLY a JSON object:
{{
{chr(10).join(f'  "{name}": <1-5>,' for name, _ in JUDGE_CRITERIA)}
  "overall": <1-5>,
  "reasoning": "<1 sentence justification>"
}}

Criteria:
{chr(10).join(f'- {name}: {desc}' for name, desc in JUDGE_CRITERIA)}
"""

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return {"error": "No OPENAI_API_KEY set for judge"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "gpt-5.4-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            scores = json.loads(content)
            scores["_model"] = "gpt-5.4-mini"
            return scores
    except Exception as e:
        logger.warning(f"Judge evaluation failed: {e}")
        return {"error": str(e)}


async def judge_pipeline_run(run_id: str) -> dict:
    """Judge all failures from a pipeline run. Returns per-failure scores + aggregate."""
    from ..api.mcp_pipeline import format_compact_bundle

    bundle = format_compact_bundle(run_id)
    if "error" in bundle:
        return bundle

    failures = bundle.get("failures", [])
    if not failures:
        return {"run_id": run_id, "verdict": "no_failures", "message": "All tests passed — nothing to judge."}

    app_context = f"{bundle.get('app', 'Unknown app')} — {bundle.get('flow_type', 'unknown')} flow"

    scored_failures = []
    total_scores = {name: 0 for name, _ in JUDGE_CRITERIA}
    total_scores["overall"] = 0

    for failure in failures[:10]:  # Cap at 10 to control cost
        scores = await judge_bug_report(failure, app_context)
        if "error" not in scores:
            for name, _ in JUDGE_CRITERIA:
                total_scores[name] += scores.get(name, 0)
            total_scores["overall"] += scores.get("overall", 0)
        scored_failures.append({
            "test_id": failure.get("test_id", ""),
            "name": failure.get("name", ""),
            "scores": scores,
        })

    n = len([f for f in scored_failures if "error" not in f["scores"]])
    avg_scores = {k: round(v / max(n, 1), 2) for k, v in total_scores.items()} if n > 0 else {}

    return {
        "run_id": run_id,
        "failures_judged": len(scored_failures),
        "average_scores": avg_scores,
        "per_failure": scored_failures,
        "judge_model": "gpt-5.4-mini",
    }
