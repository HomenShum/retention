"""
Benchmark API endpoints for AndroidWorld and other benchmark suites.

Provides REST API for:
- Loading benchmark task suites
- Executing benchmarks on device fleets
- Retrieving benchmark results
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from app.agents.device_testing.mobile_mcp_client import MobileMCPClient
from app.benchmarks.android_world.executor import AndroidWorldExecutor, BenchmarkResult
from app.benchmarks.android_world.task_registry import (
    AndroidWorldTaskRegistry,
    TaskDifficulty,
    TaskCategory,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])

# In-memory storage for benchmark runs (would use DB in production)
benchmark_runs: Dict[str, Dict[str, Any]] = {}


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class LoadBenchmarkRequest(BaseModel):
    """Request to load benchmark tasks with optional filters."""
    difficulty: Optional[str] = Field(None, description="Filter by difficulty: easy, medium, hard")
    category: Optional[str] = Field(None, description="Filter by category: data_entry, multi_app, etc.")
    task_names: Optional[List[str]] = Field(None, description="Specific task names to load")
    limit: int = Field(50, ge=1, le=200, description="Maximum number of tasks to return")


class LoadBenchmarkResponse(BaseModel):
    """Response with loaded benchmark tasks."""
    tasks: List[Dict[str, Any]]
    total_count: int
    filters_applied: Dict[str, Any]


class ExecuteBenchmarkRequest(BaseModel):
    """Request to execute benchmark tasks on devices."""
    task_names: List[str] = Field(..., min_length=1, description="List of task names to execute")
    device_ids: List[str] = Field(..., min_length=1, description="List of device IDs to run on")
    parallel: bool = Field(True, description="Run tasks in parallel across devices")


class ExecuteBenchmarkResponse(BaseModel):
    """Response with benchmark execution info."""
    run_id: str
    status: str
    message: str
    task_count: int
    device_count: int


class BenchmarkRunStatus(BaseModel):
    """Status of a benchmark run."""
    run_id: str
    status: str  # pending, running, completed, failed
    started_at: Optional[str]
    completed_at: Optional[str]
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    success_rate: Optional[float]
    duration_seconds: Optional[float]
    task_results: List[Dict[str, Any]]


# ============================================================================
# API ENDPOINTS
# ============================================================================

@router.post("/android-world/load", response_model=LoadBenchmarkResponse)
async def load_benchmark_tasks(request: LoadBenchmarkRequest):
    """
    Load AndroidWorld benchmark tasks with optional filters.

    Returns a list of tasks that can be executed on devices.
    """
    registry = AndroidWorldTaskRegistry()

    # Apply filters
    difficulty_filter = None
    category_filter = None

    if request.difficulty:
        try:
            difficulty_filter = TaskDifficulty(request.difficulty)
        except ValueError:
            raise HTTPException(400, f"Invalid difficulty: {request.difficulty}")

    if request.category:
        try:
            category_filter = TaskCategory(request.category)
        except ValueError:
            raise HTTPException(400, f"Invalid category: {request.category}")

    # Get tasks
    if request.task_names:
        tasks = [registry.get(name) for name in request.task_names if registry.get(name)]
    else:
        tasks = registry.list_tasks(difficulty=difficulty_filter, category=category_filter)

    # Limit results
    tasks = tasks[:request.limit]

    return LoadBenchmarkResponse(
        tasks=[t.to_dict() for t in tasks],
        total_count=len(tasks),
        filters_applied={
            "difficulty": request.difficulty,
            "category": request.category,
            "task_names": request.task_names,
            "limit": request.limit,
        }
    )


@router.get("/android-world/tasks")
async def list_all_tasks():
    """List all available AndroidWorld tasks."""
    registry = AndroidWorldTaskRegistry()
    return {
        "tasks": [t.to_dict() for t in registry.list_tasks()],
        "total_count": registry.count,
        "categories": [c.value for c in TaskCategory],
        "difficulties": [d.value for d in TaskDifficulty],
    }


@router.post("/android-world/execute", response_model=ExecuteBenchmarkResponse)
async def execute_benchmark(request: ExecuteBenchmarkRequest, background_tasks: BackgroundTasks):
    """
    Execute benchmark tasks on the specified devices.

    Returns a run_id that can be used to check status and results.
    Tasks are executed in the background.
    """
    run_id = str(uuid.uuid4())

    # Initialize run record
    benchmark_runs[run_id] = {
        "run_id": run_id,
        "status": "pending",
        "started_at": datetime.now().isoformat(),
        "completed_at": None,
        "task_names": request.task_names,
        "device_ids": request.device_ids,
        "parallel": request.parallel,
        "total_tasks": len(request.task_names) * len(request.device_ids),
        "completed_tasks": 0,
        "failed_tasks": 0,
        "success_rate": None,
        "duration_seconds": None,
        "task_results": [],
        "result": None,
    }

    # Start background execution
    background_tasks.add_task(
        _run_benchmark_async,
        run_id,
        request.task_names,
        request.device_ids,
        request.parallel,
    )

    logger.info(f"[BENCHMARK API] Started benchmark run {run_id}")

    return ExecuteBenchmarkResponse(
        run_id=run_id,
        status="pending",
        message=f"Benchmark started. Use GET /results/{run_id} to check status.",
        task_count=len(request.task_names),
        device_count=len(request.device_ids),
    )


async def _run_benchmark_async(
    run_id: str,
    task_names: List[str],
    device_ids: List[str],
    parallel: bool,
):
    """Background task to run benchmark."""
    run = benchmark_runs.get(run_id)
    if not run:
        return

    run["status"] = "running"
    mcp_client = MobileMCPClient()

    try:
        await mcp_client.start()
        executor = AndroidWorldExecutor(mcp_client)

        result: BenchmarkResult = await executor.run_benchmark(
            task_names=task_names,
            device_ids=device_ids,
            parallel=parallel,
        )

        # Update run record
        run["status"] = "completed"
        run["completed_at"] = datetime.now().isoformat()
        run["completed_tasks"] = result.completed_tasks
        run["failed_tasks"] = result.failed_tasks
        run["success_rate"] = result.success_rate
        run["duration_seconds"] = result.total_duration_seconds
        run["task_results"] = [
            {
                "task_name": tr.task_name,
                "device_id": tr.device_id,
                "status": tr.status.value,
                "steps_taken": tr.steps_taken,
                "duration_seconds": tr.duration_seconds,
                "error_message": tr.error_message,
                "actions": tr.actions,
            }
            for tr in result.task_results
        ]

        logger.info(f"[BENCHMARK API] Completed run {run_id}: {result.success_rate:.1%} success rate")

    except Exception as e:
        run["status"] = "failed"
        run["completed_at"] = datetime.now().isoformat()
        run["error"] = str(e)
        logger.error(f"[BENCHMARK API] Run {run_id} failed: {e}")

    finally:
        await mcp_client.stop()


@router.get("/android-world/results/{run_id}", response_model=BenchmarkRunStatus)
async def get_benchmark_results(run_id: str):
    """
    Get the status and results of a benchmark run.

    Returns detailed results once the run is completed.
    """
    run = benchmark_runs.get(run_id)
    if not run:
        raise HTTPException(404, f"Benchmark run {run_id} not found")

    return BenchmarkRunStatus(
        run_id=run["run_id"],
        status=run["status"],
        started_at=run.get("started_at"),
        completed_at=run.get("completed_at"),
        total_tasks=run["total_tasks"],
        completed_tasks=run.get("completed_tasks", 0),
        failed_tasks=run.get("failed_tasks", 0),
        success_rate=run.get("success_rate"),
        duration_seconds=run.get("duration_seconds"),
        task_results=run.get("task_results", []),
    )


@router.get("/android-world/runs")
async def list_benchmark_runs():
    """List all benchmark runs."""
    return {
        "runs": [
            {
                "run_id": r["run_id"],
                "status": r["status"],
                "started_at": r.get("started_at"),
                "total_tasks": r["total_tasks"],
                "success_rate": r.get("success_rate"),
            }
            for r in benchmark_runs.values()
        ],
        "total_count": len(benchmark_runs),
    }


@router.delete("/android-world/runs/{run_id}")
async def delete_benchmark_run(run_id: str):
    """Delete a benchmark run record."""
    if run_id not in benchmark_runs:
        raise HTTPException(404, f"Benchmark run {run_id} not found")

    del benchmark_runs[run_id]
    return {"message": f"Run {run_id} deleted"}

