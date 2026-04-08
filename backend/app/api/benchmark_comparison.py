"""
Benchmark Comparison API Router.

Provides REST endpoints for running benchmark suites,
listing runs, retrieving scorecards, and getting per-task evidence.
"""

import asyncio
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from app.benchmarks.evidence_schema import AgentMode
from app.benchmarks.evidence_writer import EvidenceWriter
from app.benchmarks.scorecard import ScorecardAggregator
from app.benchmarks.web_tasks.runner import BenchmarkRunner
from app.benchmarks.web_tasks.task_registry import TaskBucket, WebTaskRegistry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/benchmarks/comparison", tags=["benchmark-comparison"])

# Shared instances
_writer = EvidenceWriter()
_registry = WebTaskRegistry()
_runner = BenchmarkRunner(evidence_writer=_writer, task_registry=_registry)

# In-memory run tracking (matches existing benchmarks.py pattern)
_active_runs: Dict[str, Dict[str, Any]] = {}


# ── Request / Response models ────────────────────────────────

class RunBenchmarkRequest(BaseModel):
    task_ids: Optional[List[str]] = Field(
        None, description="Specific task IDs to run. Omit for all tasks."
    )
    modes: Optional[List[str]] = Field(
        None,
        description="Modes to run: 'claude-baseline', 'test-assurance'. Default: both.",
    )
    parallel: int = Field(2, ge=1, le=8, description="Max parallel tasks")


class RunBenchmarkResponse(BaseModel):
    suite_id: str
    status: str
    message: str
    task_count: int
    modes: List[str]


class SuiteListItem(BaseModel):
    suite_id: str
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    task_count: int = 0


class TaskListItem(BaseModel):
    task_id: str
    app_id: str
    bucket: str
    prompt: str
    timeout_seconds: int


# ── Endpoints ────────────────────────────────────────────────

@router.get("/tasks")
async def list_benchmark_tasks(
    bucket: Optional[str] = None, app_id: Optional[str] = None
):
    """List available benchmark tasks with optional filters."""
    bucket_filter = TaskBucket(bucket) if bucket else None
    tasks = _registry.list_tasks(bucket=bucket_filter, app_id=app_id)
    return {
        "tasks": [t.to_dict() for t in tasks],
        "total_count": len(tasks),
        "buckets": _registry.list_buckets(),
        "apps": _registry.list_apps(),
    }


@router.post("/run", response_model=RunBenchmarkResponse)
async def run_benchmark_suite(
    request: RunBenchmarkRequest, background_tasks: BackgroundTasks
):
    """
    Start a benchmark comparison suite.

    Runs each task in both modes (or specified modes) and produces
    a scorecard with per-task evidence.
    """
    suite_id = str(uuid.uuid4())[:8]

    # Parse modes
    modes = []
    if request.modes:
        for m in request.modes:
            try:
                modes.append(AgentMode(m))
            except ValueError:
                raise HTTPException(400, f"Invalid mode: {m}")
    else:
        modes = [AgentMode.CLAUDE_BASELINE, AgentMode.TEST_ASSURANCE]

    # Validate task IDs
    task_count = 0
    if request.task_ids:
        for tid in request.task_ids:
            if not _registry.get(tid):
                raise HTTPException(404, f"Task not found: {tid}")
        task_count = len(request.task_ids)
    else:
        task_count = _registry.count

    # Track run
    _active_runs[suite_id] = {
        "suite_id": suite_id,
        "status": "pending",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "task_ids": request.task_ids,
        "modes": [m.value for m in modes],
        "task_count": task_count,
        "completed_tasks": 0,
        "total_work": task_count * len(modes),
        "error": None,
    }

    # Launch background
    background_tasks.add_task(
        _run_suite_background,
        suite_id,
        request.task_ids,
        modes,
        request.parallel,
    )

    return RunBenchmarkResponse(
        suite_id=suite_id,
        status="pending",
        message=f"Benchmark started. GET /runs/{suite_id} to check status.",
        task_count=task_count,
        modes=[m.value for m in modes],
    )


async def _run_suite_background(
    suite_id: str,
    task_ids: Optional[List[str]],
    modes: List[AgentMode],
    parallel: int,
):
    """Background coroutine to execute the full suite."""
    run = _active_runs.get(suite_id)
    if not run:
        return

    run["status"] = "running"

    def on_progress(label: str, done: int, total: int):
        run["completed_tasks"] = done

    try:
        scorecard = await asyncio.wait_for(
            _runner.run_suite(
                task_ids=task_ids,
                modes=modes,
                parallel=parallel,
                progress_callback=on_progress,
            ),
            timeout=300,  # 5 minute timeout per suite
        )
        run["status"] = "completed"
        run["completed_at"] = datetime.now(timezone.utc).isoformat()
        run["scorecard_suite_id"] = scorecard.suite_id
        logger.info(f"[BENCHMARK] Suite {suite_id} completed")
    except asyncio.TimeoutError:
        run["status"] = "failed"
        run["completed_at"] = datetime.now(timezone.utc).isoformat()
        run["error"] = "Suite timed out after 5 minutes"
        logger.error(f"[BENCHMARK] Suite {suite_id} timed out")
    except Exception as e:
        run["status"] = "failed"
        run["completed_at"] = datetime.now(timezone.utc).isoformat()
        run["error"] = str(e)
        logger.error(f"[BENCHMARK] Suite {suite_id} failed: {e}")


@router.get("/runs")
async def list_benchmark_runs():
    """List all benchmark comparison runs (in-memory + persisted)."""
    # Merge in-memory active runs with disk-persisted suites
    runs: List[Dict[str, Any]] = []

    # Active runs
    for r in _active_runs.values():
        runs.append({
            "suite_id": r["suite_id"],
            "status": r["status"],
            "started_at": r.get("started_at"),
            "completed_at": r.get("completed_at"),
            "task_count": r.get("task_count", 0),
            "completed_tasks": r.get("completed_tasks", 0),
        })

    # Persisted suites (not already in active runs)
    active_ids = {r["suite_id"] for r in runs}
    for sid in _writer.list_suites():
        if sid not in active_ids:
            manifest = _writer.load_suite_manifest(sid) or {}
            runs.append({
                "suite_id": sid,
                "status": manifest.get("status", "unknown"),
                "started_at": manifest.get("started_at"),
                "completed_at": manifest.get("completed_at"),
                "task_count": manifest.get("task_count", 0),
            })

    return {"runs": runs, "total_count": len(runs)}


@router.get("/runs/{suite_id}")
async def get_benchmark_run(suite_id: str):
    """Get detailed status and results for a benchmark run."""
    # Check active runs first
    run = _active_runs.get(suite_id)
    if run:
        result = dict(run)
        # If completed, load scorecard
        if run["status"] == "completed":
            scorecard = _writer.load_scorecard(
                run.get("scorecard_suite_id", suite_id)
            )
            if scorecard:
                result["scorecard"] = scorecard
        return result

    # Check disk
    manifest = _writer.load_suite_manifest(suite_id)
    if not manifest:
        raise HTTPException(404, f"Suite not found: {suite_id}")

    scorecard = _writer.load_scorecard(suite_id)
    return {
        **manifest,
        "scorecard": scorecard,
    }


@router.get("/runs/{suite_id}/scorecard")
async def get_scorecard(suite_id: str):
    """Get the aggregated scorecard for a completed benchmark run."""
    # Try active run first
    run = _active_runs.get(suite_id)
    if run:
        real_suite_id = run.get("scorecard_suite_id", suite_id)
        scorecard = _writer.load_scorecard(real_suite_id)
        if scorecard:
            return scorecard

    # Try disk
    scorecard = _writer.load_scorecard(suite_id)
    if not scorecard:
        raise HTTPException(404, f"Scorecard not found for suite: {suite_id}")
    return scorecard


@router.get("/runs/{suite_id}/evidence/{task_id}")
async def get_task_evidence(suite_id: str, task_id: str, mode: Optional[str] = None):
    """Get evidence for a specific task in a run."""
    run = _active_runs.get(suite_id)
    real_suite_id = run.get("scorecard_suite_id", suite_id) if run else suite_id

    results = {}
    modes_to_check = [AgentMode(mode)] if mode else list(AgentMode)
    for m in modes_to_check:
        ev = _writer.load_evidence(real_suite_id, task_id, m)
        if ev:
            results[m.value] = ev.model_dump()

    if not results:
        raise HTTPException(404, f"No evidence found for task {task_id} in suite {suite_id}")
    return results


# ── Discovery & Replay Ingestion ─────────────────────────────

class DiscoverTasksRequest(BaseModel):
    url: str = Field(..., description="Target URL to discover tests from")
    label: str = Field("", description="Optional label (e.g. hostname)")
    crawl_depth: int = Field(1, description="How many levels of internal links to follow (0=single page, 1=follow links once)")


class ReplayIngestRequest(BaseModel):
    format: str = Field(..., description="Replay format: posthog, har, or rrweb")
    data: dict = Field(..., description="Raw replay JSON data")


@router.post("/discover-tasks")
async def discover_tasks(request: DiscoverTasksRequest):
    """
    Discover testable tasks from a URL by loading it with Playwright
    and extracting interactive elements (links, buttons, forms, inputs).
    """
    tasks = await _discover_tasks_from_page(request.url, request.label, crawl_depth=request.crawl_depth)
    return {"tasks": [t.model_dump() for t in tasks], "count": len(tasks)}


@router.post("/ingest-replay", response_model=RunBenchmarkResponse)
async def ingest_replay(request: ReplayIngestRequest, background_tasks: BackgroundTasks):
    """
    Ingest a session replay (PostHog, HAR, or rrweb) and run the extracted tests.
    """
    from app.benchmarks.web_tasks.replay_ingestor import ReplayIngestor

    ingestor = ReplayIngestor()
    if request.format == "posthog":
        tasks = ingestor.ingest_posthog(request.data)
    elif request.format == "har":
        tasks = ingestor.ingest_har(request.data)
    elif request.format == "rrweb":
        tasks = ingestor.ingest_rrweb(request.data.get("events", []))
    else:
        raise HTTPException(400, f"Unsupported replay format: {request.format}")

    if not tasks:
        raise HTTPException(422, "No tasks could be extracted from the replay data")

    # Register discovered tasks temporarily and run them
    suite_id = str(uuid.uuid4())[:8]
    task_ids = []
    for t in tasks:
        _registry._tasks[t.task_id] = t
        task_ids.append(t.task_id)

    modes = [AgentMode.CLAUDE_BASELINE, AgentMode.TEST_ASSURANCE]
    _active_runs[suite_id] = {
        "suite_id": suite_id,
        "status": "pending",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "task_ids": task_ids,
        "modes": [m.value for m in modes],
        "task_count": len(task_ids),
        "completed_tasks": 0,
        "total_work": len(task_ids) * 2,
        "error": None,
    }

    background_tasks.add_task(_run_suite_background, suite_id, task_ids, modes, 2)

    return RunBenchmarkResponse(
        suite_id=suite_id,
        status="pending",
        message=f"Ingested {len(tasks)} tasks from {request.format} replay.",
        task_count=len(tasks),
        modes=[m.value for m in modes],
    )


async def _discover_tasks_from_page(url: str, label: str = "", crawl_depth: int = 1) -> list:
    """Load a page with Playwright, extract testable interactions, and optionally crawl internal links."""
    from app.benchmarks.web_tasks.task_registry import BenchmarkTask, TaskBucket
    from urllib.parse import urlparse

    tasks = []
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("playwright not installed — cannot discover tasks")
        return tasks

    _EXTRACT_JS = """() => {
        const results = [];
        // Links
        document.querySelectorAll('a[href]').forEach((el, i) => {
            if (el.textContent.trim() && el.href && !el.href.startsWith('javascript:')) {
                results.push({
                    type: 'link', text: el.textContent.trim().slice(0, 80),
                    href: el.href, index: i
                });
            }
        });
        // Buttons
        document.querySelectorAll('button, [role="button"], input[type="submit"]').forEach((el, i) => {
            const raw = el.textContent?.trim() || el.value || el.getAttribute('aria-label') || '';
            const text = raw.replace(/\\s+/g, ' ').slice(0, 80);
            if (text) results.push({ type: 'button', text, index: i });
        });
        // Forms
        document.querySelectorAll('form').forEach((el, i) => {
            const inputs = el.querySelectorAll('input, textarea, select');
            results.push({
                type: 'form', text: `Form with ${inputs.length} fields`,
                action: el.action, index: i, fieldCount: inputs.length
            });
        });
        // Input fields
        document.querySelectorAll('input[type="text"], input[type="email"], input[type="search"], textarea').forEach((el, i) => {
            const ph = el.placeholder || el.getAttribute('aria-label') || el.name || '';
            if (ph) results.push({ type: 'input', text: ph.slice(0, 80), index: i });
        });
        return results.slice(0, 50);
    }"""

    try:
        parsed_origin = urlparse(url)
        base_origin = f"{parsed_origin.scheme}://{parsed_origin.netloc}"
        app_id = label or parsed_origin.netloc

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--single-process"])
            page = await browser.new_page(viewport={"width": 1280, "height": 800})

            # Bypass demo gate by setting localStorage before first navigation
            await page.goto(f"{base_origin}/demo", wait_until="domcontentloaded", timeout=15000)
            await page.evaluate("localStorage.setItem('ta_trial_email', 'playwright-discovery@retention.ai')")

            visited: set[str] = set()
            all_elements: list[dict] = []
            pages_to_visit = [url]

            for depth in range(crawl_depth + 1):
                next_pages: list[str] = []
                for page_url in pages_to_visit:
                    norm = page_url.rstrip("/")
                    if norm in visited:
                        continue
                    visited.add(norm)

                    try:
                        await page.goto(page_url, wait_until="domcontentloaded", timeout=15000)
                        await page.wait_for_timeout(1500)
                    except Exception as nav_err:
                        logger.debug("Skip %s: %s", page_url, nav_err)
                        continue

                    page_path = urlparse(page.url).path or "/"
                    elements = await page.evaluate(_EXTRACT_JS)

                    # Tag each element with the page it was found on
                    for el in elements:
                        el["_page"] = page_path

                    all_elements.extend(elements)

                    # Collect same-origin links for next depth
                    if depth < crawl_depth:
                        for el in elements:
                            href = el.get("href", "")
                            if href and href.startswith(base_origin):
                                href_norm = href.rstrip("/")
                                if href_norm not in visited:
                                    next_pages.append(href)

                pages_to_visit = list(dict.fromkeys(next_pages))  # dedupe, preserve order

            await browser.close()

        # Deduplicate shared navigation elements (sidebar, header, footer)
        # If the same element text+type appears on 3+ pages, it's shared nav — keep only first occurrence
        from collections import Counter
        element_page_count: Counter = Counter()
        for el in all_elements:
            element_page_count[f"{el['type']}:{el.get('text', '')}"] += 1

        # Generate BenchmarkTask for each unique element
        seen: set[str] = set()
        idx = 0
        for el in all_elements:
            el_key = f"{el['type']}:{el.get('text', '')}"
            page_key = f"{el_key}:{el.get('_page', '')}"
            if page_key in seen:
                continue
            seen.add(page_key)

            # Skip shared nav elements (appear on 3+ pages) — keep only first occurrence
            if element_page_count[el_key] >= 3:
                global_key = f"shared:{el_key}"
                if global_key in seen:
                    continue
                seen.add(global_key)

            page_path = el.get("_page", "/")
            page_label = f" on {page_path}" if page_path != "/" else ""

            bucket = {
                'link': TaskBucket.NAVIGATION_STATE,
                'button': TaskBucket.NAVIGATION_STATE,
                'form': TaskBucket.FORM_SUBMIT,
                'input': TaskBucket.FORM_SUBMIT,
            }.get(el['type'], TaskBucket.VISUAL_UI)

            prompt = {
                'link': f"Navigate to the link '{el['text']}'{page_label}",
                'button': f"Click the button '{el['text']}'{page_label}",
                'form': f"Fill and submit the form ({el['text']}){page_label}",
                'input': f"Enter text in the '{el['text']}' field{page_label}",
            }.get(el['type'], f"Interact with {el['text']}{page_label}")

            page_base = f"{base_origin}{page_path}" if page_path != "/" else url

            tasks.append(BenchmarkTask(
                task_id=f"discover-{el['type']}-{idx:03d}",
                app_id=app_id,
                bucket=bucket,
                prompt=prompt,
                expected_outcome=f"Successfully {prompt.lower()}",
                pass_rule="no_console_errors",
                base_url=page_base,
                timeout_seconds=30,
                element_intents=[el.get('text', '')],
            ))
            idx += 1

    except Exception as e:
        logger.error(f"Task discovery failed for {url}: {e}")

    return tasks


# ── Rerun Eval Endpoints ────────────────────────────────────────

class RunRerunEvalRequest(BaseModel):
    replay_result_id: str = Field(..., description="Replay result to evaluate")
    task_name: str = Field(..., description="Workflow/task name")
    baseline_trajectory_id: str = Field("", description="Baseline trajectory ID")
    model_baseline: str = Field("claude-opus-4-6", description="Baseline model")
    model_replay: str = Field("claude-opus-4-6", description="Replay model")
    lane: str = Field("retained", description="Lane: frontier, retained, small_model")


@router.post("/rerun-eval/run")
async def run_rerun_eval_endpoint(request: RunRerunEvalRequest):
    """Run 10-metric rerun eval on a replay result."""
    from app.benchmarks.rerun_eval import run_rerun_eval

    scorecard = run_rerun_eval(
        replay_result_id=request.replay_result_id,
        task_name=request.task_name,
        baseline_trajectory_id=request.baseline_trajectory_id,
        model_baseline=request.model_baseline,
        model_replay=request.model_replay,
        lane=request.lane,
    )
    return scorecard.model_dump()


@router.get("/rerun-eval/{eval_id}")
async def get_rerun_eval(eval_id: str):
    """Get a saved rerun eval scorecard."""
    from app.benchmarks.rerun_eval import get_eval_result

    result = get_eval_result(eval_id)
    if not result:
        raise HTTPException(404, f"Eval not found: {eval_id}")
    return result.model_dump()


@router.get("/rerun-eval/{eval_id}/retention-analysis")
async def get_retention_analysis(eval_id: str):
    """Get retention error analysis for a rerun eval."""
    from app.benchmarks.rerun_eval import get_eval_result, analyze_retention_errors

    scorecard = get_eval_result(eval_id)
    if not scorecard:
        raise HTTPException(404, f"Eval not found: {eval_id}")
    analysis = analyze_retention_errors(scorecard)
    return analysis.model_dump()


@router.get("/rerun-eval/list")
async def list_rerun_evals():
    """List all saved rerun eval results."""
    from app.benchmarks.rerun_eval import list_eval_results

    return {"evals": list_eval_results()}


# ── Three-Lane Benchmark Endpoints ─────────────────────────────

class RunThreeLaneRequest(BaseModel):
    task_name: str = Field(..., description="Workflow to benchmark")
    frontier_model: str = Field("claude-opus-4-6", description="Frontier model")
    small_model: str = Field("claude-haiku-4-5", description="Small model for Lane 3")


class RunThreeLaneOfflineRequest(BaseModel):
    task_name: str = Field(..., description="Workflow name")
    lane1_replay_id: str = Field(..., description="Lane 1 replay result ID")
    lane2_replay_id: str = Field(..., description="Lane 2 replay result ID")
    lane3_replay_id: str = Field(..., description="Lane 3 replay result ID")
    baseline_trajectory_id: str = Field("", description="Baseline trajectory ID")
    frontier_model: str = Field("claude-opus-4-6")
    small_model: str = Field("claude-haiku-4-5")


@router.post("/three-lane/run")
async def run_three_lane_offline(request: RunThreeLaneOfflineRequest):
    """Run three-lane eval from existing replay results (no device needed)."""
    from app.benchmarks.three_lane_benchmark import run_three_lane_eval_offline

    result = run_three_lane_eval_offline(
        task_name=request.task_name,
        lane1_replay_id=request.lane1_replay_id,
        lane2_replay_id=request.lane2_replay_id,
        lane3_replay_id=request.lane3_replay_id,
        baseline_trajectory_id=request.baseline_trajectory_id,
        frontier_model=request.frontier_model,
        small_model=request.small_model,
    )
    return result.model_dump()


@router.get("/three-lane/{benchmark_id}")
async def get_three_lane_result(benchmark_id: str):
    """Get results for a three-lane benchmark."""
    from app.benchmarks.three_lane_benchmark import get_benchmark_result

    result = get_benchmark_result(benchmark_id)
    if not result:
        raise HTTPException(404, f"Benchmark not found: {benchmark_id}")
    return result.model_dump()


@router.get("/three-lane/list")
async def list_three_lane_benchmarks():
    """List all three-lane benchmark results."""
    from app.benchmarks.three_lane_benchmark import list_benchmark_results

    return {"benchmarks": list_benchmark_results()}


# ── Distillation Dataset Endpoints ──────────────────────────────

class GenerateDistillationRequest(BaseModel):
    task_name: str = Field("", description="Filter by task name (empty = all)")
    min_composite_score: float = Field(0.75, description="Minimum composite score threshold")
    formats: List[str] = Field(
        default=["sft", "dpo", "policy"],
        description="Dataset types: sft, dpo, policy",
    )


@router.post("/distillation/generate")
async def generate_distillation_dataset(request: GenerateDistillationRequest):
    """Generate training data from validated replay evals."""
    from app.benchmarks.distillation_dataset import generate_dataset

    result = generate_dataset(
        task_name=request.task_name,
        min_composite_score=request.min_composite_score,
        formats=request.formats,
    )
    return result


@router.get("/distillation/{dataset_id}")
async def get_distillation_dataset(dataset_id: str):
    """Get stats for a generated distillation dataset."""
    from app.benchmarks.distillation_dataset import get_dataset

    result = get_dataset(dataset_id)
    if not result:
        raise HTTPException(404, f"Dataset not found: {dataset_id}")
    return result


@router.get("/distillation/list")
async def list_distillation_datasets():
    """List all generated distillation datasets."""
    from app.benchmarks.distillation_dataset import list_datasets

    return {"datasets": list_datasets()}


# ── Multi-Model Benchmark Endpoints ─────────────────────────────

@router.get("/models")
async def list_available_models():
    """List all available models with labels and pricing."""
    from app.benchmarks.three_lane_benchmark import get_available_models

    return {"models": get_available_models()}


class RunMultiModelRequest(BaseModel):
    task_name: str = Field("", description="Workflow name (empty = all)")
    replay_result_ids: List[str] = Field(
        default_factory=list,
        description="Replay result IDs to evaluate. Empty = use all available.",
    )
    models: Optional[List[str]] = Field(
        None,
        description="Models to compare. None = all available models.",
    )
    baseline_trajectory_id: str = Field("", description="Baseline trajectory ID")


@router.post("/multi-model/run")
async def run_multi_model_benchmark(request: RunMultiModelRequest):
    """Run multi-model eval comparing same replays under different model pricing."""
    from app.benchmarks.three_lane_benchmark import run_multi_model_eval_offline
    from pathlib import Path

    replay_ids = request.replay_result_ids
    if not replay_ids:
        # Use all available replay results
        replay_dir = Path(__file__).resolve().parents[1] / "data" / "replay_results"
        if replay_dir.exists():
            replay_ids = [f.stem for f in sorted(replay_dir.glob("*.json"))[:5]]

    if not replay_ids:
        raise HTTPException(404, "No replay results available")

    result = run_multi_model_eval_offline(
        task_name=request.task_name or "multi_model",
        replay_result_ids=replay_ids,
        baseline_trajectory_id=request.baseline_trajectory_id,
        models=request.models,
    )
    return result.model_dump()


# ── CSP Flagship Benchmark Endpoints ────────────────────────────

class RunCSPBenchmarkRequest(BaseModel):
    n: int = Field(10, ge=1, le=100, description="Number of replay results to evaluate")
    workflow_family: str = Field("claude_code_csp_20260402", description="CSP workflow family")
    model_baseline: str = Field("gpt-5.4:xhigh", description="Frontier model")
    model_replay: str = Field("gpt-5.4-mini:high", description="Replay model")


@router.post("/csp/run")
async def run_csp_benchmark(request: RunCSPBenchmarkRequest):
    """Run the CSP flagship benchmark suite (N=10/N=25)."""
    from app.benchmarks.csp_benchmark import run_csp_benchmark_suite

    result = run_csp_benchmark_suite(
        n=request.n,
        workflow_family=request.workflow_family,
        model_baseline=request.model_baseline,
        model_replay=request.model_replay,
    )
    return {
        "benchmark_id": result.benchmark_id,
        "n": result.n,
        "final_verdict": result.final_verdict,
        "verdict_reason": result.verdict_reason,
        "stats": result.stats,
        "drift_results": result.drift_results,
        "escalation_tests": result.escalation_tests,
        "anatomy": result.anatomy,
    }


@router.get("/csp/{benchmark_id}")
async def get_csp_benchmark(benchmark_id: str):
    """Get a CSP benchmark result."""
    from app.benchmarks.csp_benchmark import get_csp_benchmark as _get

    result = _get(benchmark_id)
    if not result:
        raise HTTPException(404, f"CSP benchmark not found: {benchmark_id}")
    return result


@router.get("/csp/list")
async def list_csp_benchmarks():
    """List all CSP benchmark results."""
    from app.benchmarks.csp_benchmark import list_csp_benchmarks as _list

    return {"benchmarks": _list()}


@router.get("/cards")
async def list_benchmark_cards():
    """List all benchmark family cards."""
    from app.services.benchmark_card import list_cards, generate_all_cards

    cards = list_cards()
    if not cards:
        # Generate cards on first request
        generated = generate_all_cards()
        cards = list_cards()
    return {"cards": cards}


@router.get("/cards/{workflow_family}")
async def get_benchmark_card(workflow_family: str):
    """Get or generate a benchmark card for a workflow family."""
    from app.services.benchmark_card import generate_card

    card = generate_card(workflow_family)
    return asdict(card) if hasattr(card, '__dataclass_fields__') else card.__dict__


@router.get("/cards/{workflow_family}/compare/{eval_id}")
async def get_compare_pane(workflow_family: str, eval_id: str):
    """Get three-pane compare view for a single eval."""
    from app.services.benchmark_card import get_compare_pane as _get
    from dataclasses import asdict

    pane = _get(eval_id)
    if not pane:
        raise HTTPException(404, f"Eval not found: {eval_id}")
    return asdict(pane)
