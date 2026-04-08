"""
Chef Integration API Router

Provides endpoints for Chef app generation, testing, and benchmarking:
- POST /api/chef/generate     — Start a new Chef app generation
- GET  /api/chef/runs          — List all runs
- GET  /api/chef/runs/{id}     — Get run status/results
- POST /api/chef/test          — Run E2E smoke tests against deployed URL
- GET  /api/chef/benchmarks    — Get aggregated benchmark stats
- POST /api/chef/retry         — Retry a failed run with improved prompt
"""

import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chef", tags=["Chef Integration"])

# ---------------------------------------------------------------------------
# Dependency injection (set by main.py at startup)
# ---------------------------------------------------------------------------

_chef_runner: Optional[Any] = None  # ChefRunner
_e2e_runner: Optional[Any] = None  # ChefE2ERunner
_benchmark_service: Optional[Any] = None  # BenchmarkService
_feedback_analyzer: Optional[Any] = None  # FeedbackAnalyzer


def set_chef_runner(runner: Any) -> None:
    """Inject the ChefRunner instance (called from main.py)."""
    global _chef_runner
    _chef_runner = runner


def set_e2e_runner(runner: Any) -> None:
    """Inject the ChefE2ERunner instance."""
    global _e2e_runner
    _e2e_runner = runner


def set_benchmark_service(service: Any) -> None:
    """Inject the BenchmarkService instance."""
    global _benchmark_service
    _benchmark_service = service


def set_feedback_analyzer(analyzer: Any) -> None:
    """Inject the FeedbackAnalyzer instance."""
    global _feedback_analyzer
    _feedback_analyzer = analyzer


def _get_runner() -> Any:
    if _chef_runner is None:
        raise HTTPException(
            status_code=503,
            detail="Chef runner not configured. Ensure OPENAI_API_KEY is set.",
        )
    return _chef_runner


# ---------------------------------------------------------------------------
# In-memory run store  (TODO: persist to Convex)
# ---------------------------------------------------------------------------

chef_runs: Dict[str, Dict[str, Any]] = {}
test_results: Dict[str, Dict[str, Any]] = {}  # run_id -> E2E test results

# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    """Request body for POST /generate."""

    prompt: str = Field(..., min_length=3, description="App description prompt")
    model: str = Field("gpt-5.4", description="LLM model to use")
    enable_deploy: bool = Field(False, description="Deploy to Convex/Vercel after generation")


class GenerateResponse(BaseModel):
    """Response for POST /generate."""

    run_id: str
    status: str


class RunStatusResponse(BaseModel):
    """Response for GET /runs/{run_id}."""

    run_id: str
    status: str
    prompt: str
    model: str
    started_at: str
    completed_at: Optional[str] = None
    success: Optional[bool] = None
    num_deploys: Optional[int] = None
    files_count: Optional[int] = None
    error: Optional[str] = None


class TestRequest(BaseModel):
    """Request body for POST /test."""

    url: str = Field(..., description="Deployed app URL to test")
    run_id: Optional[str] = Field(None, description="Associated Chef run ID")


class TestResponse(BaseModel):
    """Response for POST /test."""

    url: str
    passed: bool
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    checks: List[Dict[str, Any]] = []
    console_errors: List[str] = []
    duration_ms: int = 0
    error: Optional[str] = None


class BenchmarkResponse(BaseModel):
    """Response for GET /benchmarks."""

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
    by_model: Dict[str, Any] = {}


class RetryRequest(BaseModel):
    """Request body for POST /retry."""

    run_id: str = Field(..., description="The failed run ID to retry")


class RetryResponse(BaseModel):
    """Response for POST /retry."""

    original_run_id: str
    new_run_id: str
    attempt: int
    improved_prompt: str
    status: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/generate", response_model=GenerateResponse)
async def generate_app(
    request: GenerateRequest,
    background_tasks: BackgroundTasks,
) -> GenerateResponse:
    """Start a new Chef app generation run.

    The heavy work runs in the background; poll GET /runs/{run_id} for status.
    """
    runner = _get_runner()
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    chef_runs[run_id] = {
        "run_id": run_id,
        "status": "pending",
        "prompt": request.prompt,
        "model": request.model,
        "started_at": now,
        "completed_at": None,
        "success": None,
        "num_deploys": None,
        "files_count": None,
        "error": None,
    }

    background_tasks.add_task(_run_chef_async, run_id, request.prompt, request.model)
    logger.info("Queued Chef run %s (model=%s)", run_id, request.model)

    return GenerateResponse(run_id=run_id, status="pending")


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
async def get_run(run_id: str) -> RunStatusResponse:
    """Get status and results of a Chef run."""
    run = chef_runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return RunStatusResponse(**run)


@router.get("/runs")
async def list_runs() -> Dict[str, Any]:
    """List all Chef runs."""
    return {
        "runs": [
            {k: v for k, v in r.items() if k != "files"}
            for r in chef_runs.values()
        ],
        "total_count": len(chef_runs),
    }


@router.post("/test", response_model=TestResponse)
async def run_tests(request: TestRequest) -> TestResponse:
    """Run E2E smoke tests against a deployed app URL.

    Executes Playwright-based smoke tests (page loads, no console errors,
    interactive elements, responsive check, screenshot capture).
    """
    if _e2e_runner is None:
        raise HTTPException(status_code=503, detail="E2E runner not configured")

    result = await _e2e_runner.run_smoke_tests(request.url)

    # Store result for the run
    if request.run_id:
        test_results[request.run_id] = asdict(result)

    return TestResponse(
        url=result.url,
        passed=result.passed,
        total_checks=result.total_checks,
        passed_checks=result.passed_checks,
        failed_checks=result.failed_checks,
        checks=result.checks,
        console_errors=result.console_errors,
        duration_ms=result.duration_ms,
        error=result.error,
    )


@router.get("/benchmarks", response_model=BenchmarkResponse)
async def get_benchmarks() -> BenchmarkResponse:
    """Get aggregated benchmark stats across all Chef runs."""
    if _benchmark_service is None:
        raise HTTPException(status_code=503, detail="Benchmark service not configured")

    # Aggregate from in-memory run store
    runs_data = list(chef_runs.values())
    agg = _benchmark_service.aggregate(runs_data)

    return BenchmarkResponse(
        total_runs=agg.total_runs,
        successful_runs=agg.successful_runs,
        failed_runs=agg.failed_runs,
        success_rate=agg.success_rate,
        avg_score=agg.avg_score,
        avg_deploy_score=agg.avg_deploy_score,
        avg_deploys=agg.avg_deploys,
        avg_duration_ms=agg.avg_duration_ms,
        best_score=agg.best_score,
        worst_score=agg.worst_score,
        by_model={k: asdict(v) for k, v in agg.by_model.items()},
    )


@router.post("/retry", response_model=RetryResponse)
async def retry_run(
    request: RetryRequest,
    background_tasks: BackgroundTasks,
) -> RetryResponse:
    """Retry a failed run with an improved prompt.

    Analyzes the failure, generates an improved prompt,
    and starts a new run (up to 3 retries per original run).
    """
    runner = _get_runner()
    original = chef_runs.get(request.run_id)
    if not original:
        raise HTTPException(status_code=404, detail=f"Run {request.run_id} not found")

    if original.get("success"):
        raise HTTPException(status_code=400, detail="Run already succeeded — no retry needed")

    if _feedback_analyzer is None:
        raise HTTPException(status_code=503, detail="Feedback analyzer not configured")

    # Analyze failure and check retry eligibility
    from app.integrations.chef.types import ChefResult

    result = ChefResult(
        success=False,
        num_deploys=original.get("num_deploys", 0),
        usage={},
        files={},
    )
    analysis = _feedback_analyzer.analyze_failure(
        request.run_id, result, original.get("error", "")
    )
    attempt = _feedback_analyzer.increment_retry(request.run_id)
    should_retry, reason = _feedback_analyzer.should_retry(
        request.run_id, attempt, analysis
    )

    if not should_retry:
        raise HTTPException(status_code=400, detail=f"Retry declined: {reason}")

    improved_prompt = _feedback_analyzer.generate_improved_prompt(
        original["prompt"], analysis
    )

    # Start new run with improved prompt
    new_run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    chef_runs[new_run_id] = {
        "run_id": new_run_id,
        "status": "pending",
        "prompt": improved_prompt,
        "model": original["model"],
        "started_at": now,
        "completed_at": None,
        "success": None,
        "num_deploys": None,
        "files_count": None,
        "error": None,
    }

    background_tasks.add_task(
        _run_chef_async, new_run_id, improved_prompt, original["model"]
    )
    logger.info(
        "Retry #%d for run %s → new run %s", attempt, request.run_id, new_run_id
    )

    return RetryResponse(
        original_run_id=request.run_id,
        new_run_id=new_run_id,
        attempt=attempt,
        improved_prompt=improved_prompt,
        status="pending",
    )


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------


async def _run_chef_async(run_id: str, prompt: str, model: str) -> None:
    """Execute Chef generation in the background."""
    try:
        chef_runs[run_id]["status"] = "running"
        runner = _get_runner()
        result = await runner.run(prompt=prompt, run_id=run_id, model=model)

        chef_runs[run_id].update(
            {
                "status": "completed" if result.success else "failed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "success": result.success,
                "num_deploys": result.num_deploys,
                "files_count": len(result.files),
            }
        )
        logger.info("Chef run %s finished: success=%s", run_id, result.success)

    except Exception as exc:
        logger.exception("Chef run %s error: %s", run_id, exc)
        chef_runs[run_id].update(
            {
                "status": "error",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
            }
        )

