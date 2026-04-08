"""
API routes for the Comprehensive Benchmark (5-phase).

POST /api/benchmarks/comprehensive/run       — start a run (async background)
GET  /api/benchmarks/comprehensive/runs      — list past runs
GET  /api/benchmarks/comprehensive/latest    — latest summary

GET  /api/benchmarks/apps                    — list app registry
POST /api/benchmarks/suite/run               — run multi-app benchmark suite
GET  /api/benchmarks/suite/runs/{suite_id}   — get suite run status
GET  /api/benchmarks/apps/{app_id}/latest    — latest result for a specific app
"""

import asyncio
import json
import logging
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["benchmarks-comprehensive"])

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
REPORTS_DIR = BACKEND_DIR / "data" / "benchmark_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

APP_REGISTRY_PATH = BACKEND_DIR / "data" / "benchmark_apps" / "app_registry.json"

# In-memory run registry for the current process
_runs: dict[str, dict] = {}

# In-memory suite run registry
_suite_runs: dict[str, dict] = {}


class ComprehensiveBenchmarkRequest(BaseModel):
    app_name: str = "task_manager"
    clean_app_name: str = "task_manager_clean"
    max_interactions: int = 30
    skip_phases: list[int] = []


class ComprehensiveBenchmarkStatus(BaseModel):
    run_id: str
    status: str  # pending | running | done | error
    started_at: str
    completed_at: Optional[str] = None
    summary: Optional[dict] = None
    error: Optional[str] = None


async def _run_benchmark_bg(run_id: str, req: ComprehensiveBenchmarkRequest):
    """Background task: runs the 5-phase benchmark and stores results."""
    _runs[run_id]["status"] = "running"
    try:
        from scripts.run_comprehensive_benchmark import run_comprehensive_benchmark
        result = await run_comprehensive_benchmark(
            app_name=req.app_name,
            clean_app_name=req.clean_app_name,
            max_interactions=req.max_interactions,
            skip_phases=req.skip_phases,
        )
        _runs[run_id]["status"] = "done"
        _runs[run_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        _runs[run_id]["summary"] = result.get("summary", {})
        _runs[run_id]["full_result"] = result
    except Exception as e:
        logger.exception(f"Comprehensive benchmark {run_id} failed")
        _runs[run_id]["status"] = "error"
        _runs[run_id]["error"] = str(e)
        _runs[run_id]["completed_at"] = datetime.now(timezone.utc).isoformat()


@router.post("/benchmarks/comprehensive/run", response_model=ComprehensiveBenchmarkStatus)
async def start_comprehensive_benchmark(
    req: ComprehensiveBenchmarkRequest,
    background_tasks: BackgroundTasks,
):
    """Launch all 5 benchmark phases in the background."""
    import uuid
    run_id = f"comp-{uuid.uuid4().hex[:8]}"
    started_at = datetime.now(timezone.utc).isoformat()
    _runs[run_id] = {
        "run_id": run_id,
        "status": "pending",
        "started_at": started_at,
        "request": req.model_dump(),
    }
    background_tasks.add_task(_run_benchmark_bg, run_id, req)
    return ComprehensiveBenchmarkStatus(run_id=run_id, status="pending", started_at=started_at)


@router.get("/benchmarks/comprehensive/runs/{run_id}", response_model=ComprehensiveBenchmarkStatus)
async def get_comprehensive_run(run_id: str):
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id} not found")
    return ComprehensiveBenchmarkStatus(
        run_id=run["run_id"],
        status=run["status"],
        started_at=run["started_at"],
        completed_at=run.get("completed_at"),
        summary=run.get("summary"),
        error=run.get("error"),
    )


@router.get("/benchmarks/comprehensive/runs")
async def list_comprehensive_runs():
    """List in-memory runs (current process) + recent persisted reports."""
    runs_list = [
        {
            "run_id": r["run_id"],
            "status": r["status"],
            "started_at": r["started_at"],
            "completed_at": r.get("completed_at"),
            "summary": r.get("summary"),
        }
        for r in _runs.values()
    ]
    return {"runs": runs_list, "total": len(runs_list)}


@router.get("/benchmarks/comprehensive/latest")
async def get_latest_comprehensive():
    """Return the latest comprehensive benchmark summary from disk."""
    latest_path = REPORTS_DIR / "latest.json"
    if not latest_path.exists():
        raise HTTPException(404, "No benchmark runs found yet")
    with open(latest_path) as f:
        latest = json.load(f)
    comp = latest.get("comprehensive_benchmark")
    if not comp:
        raise HTTPException(404, "No comprehensive benchmark results in latest.json")
    return comp


@router.get("/benchmarks/comprehensive/scorecard")
async def get_scorecard():
    """
    Return a human-readable scorecard comparing all benchmark phases
    with pass/fail thresholds.
    """
    latest_path = REPORTS_DIR / "latest.json"
    if not latest_path.exists():
        raise HTTPException(404, "No benchmark data available")
    with open(latest_path) as f:
        latest = json.load(f)

    comp = latest.get("comprehensive_benchmark", {})

    def grade(value, threshold, higher_is_better=True):
        if value is None:
            return "N/A"
        ok = value >= threshold if higher_is_better else value <= threshold
        return "PASS" if ok else "FAIL"

    scorecard = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": [
            {
                "name": "Bug Detection F1",
                "value": comp.get("f1"),
                "threshold": 0.60,
                "status": grade(comp.get("f1"), 0.60),
                "note": "Target: F1 ≥ 0.80 (current baseline 0.60)",
            },
            {
                "name": "Precision",
                "value": comp.get("precision"),
                "threshold": 0.60,
                "status": grade(comp.get("precision"), 0.60),
                "note": "Of bugs retention.sh flags, % that are real",
            },
            {
                "name": "Recall",
                "value": comp.get("recall"),
                "threshold": 0.60,
                "status": grade(comp.get("recall"), 0.60),
                "note": "Of real bugs, % retention.sh catches",
            },
            {
                "name": "False Discovery Rate",
                "value": comp.get("false_discovery_rate"),
                "threshold": 0.10,
                "status": grade(comp.get("false_discovery_rate"), 0.10, higher_is_better=False),
                "note": "FP rate on clean builds — target < 10%",
            },
            {
                "name": "Fix Verification Accuracy",
                "value": comp.get("fix_verification_accuracy"),
                "threshold": 0.95,
                "status": grade(comp.get("fix_verification_accuracy"), 0.95),
                "note": "After engineer fix, % correctly verified as PASS",
            },
            {
                "name": "Branch Classification Accuracy",
                "value": comp.get("branch_classification_accuracy"),
                "threshold": 0.90,
                "status": grade(comp.get("branch_classification_accuracy"), 0.90),
                "note": "Correct routing to Bug Found / No Bug branches",
            },
            {
                "name": "Cost per Confirmed Bug (USD)",
                "value": comp.get("cost_per_confirmed_bug_usd"),
                "threshold": 0.50,
                "status": grade(comp.get("cost_per_confirmed_bug_usd"), 0.50, higher_is_better=False),
                "note": "vs $25 manual QA (30 min @ $50/hr)",
            },
            {
                "name": "Cost Savings vs Manual",
                "value": comp.get("savings_pct"),
                "threshold": 90.0,
                "status": grade(comp.get("savings_pct"), 90.0),
                "note": "% cheaper than manual QA per confirmed bug",
            },
        ],
    }

    passing = sum(1 for m in scorecard["metrics"] if m["status"] == "PASS")
    total = sum(1 for m in scorecard["metrics"] if m["status"] != "N/A")
    scorecard["overall"] = f"{passing}/{total} metrics passing"

    return scorecard


# ---------------------------------------------------------------------------
# App registry endpoints
# ---------------------------------------------------------------------------

@router.get("/benchmarks/apps")
async def list_benchmark_apps():
    """Return the app registry with availability counts."""
    if not APP_REGISTRY_PATH.exists():
        raise HTTPException(404, "App registry not found")
    with open(APP_REGISTRY_PATH) as f:
        registry = json.load(f)
    apps = registry.get("apps", [])
    available = sum(1 for a in apps if a.get("available", False))
    return {"apps": apps, "total": len(apps), "available": available}


@router.get("/benchmarks/apps/{app_id}/latest")
async def get_app_latest(app_id: str):
    """Return the latest benchmark result for a specific app from benchmark_reports/."""
    # Search for the most recent report file matching this app_id
    pattern = f"*{app_id}*.json"
    candidates = sorted(
        REPORTS_DIR.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        # Also try generic latest.json and filter by app_id
        latest_path = REPORTS_DIR / "latest.json"
        if latest_path.exists():
            with open(latest_path) as f:
                data = json.load(f)
            # Return any top-level key that matches the app_id
            if app_id in data:
                return data[app_id]
            raise HTTPException(404, f"No benchmark results found for app: {app_id}")
        raise HTTPException(404, f"No benchmark results found for app: {app_id}")
    with open(candidates[0]) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Suite run models and endpoints
# ---------------------------------------------------------------------------

class SuiteRunRequest(BaseModel):
    app_ids: Optional[List[str]] = None
    max_interactions: int = 30


class SuiteRunStatus(BaseModel):
    suite_id: str
    status: str  # pending | running | done | error
    apps: List[str]
    started_at: str
    completed_at: Optional[str] = None
    error: Optional[str] = None
    results: Optional[dict] = None


async def _run_suite_bg(suite_id: str, app_ids: List[str], max_interactions: int):
    """Background task: launches run_benchmark_suite.py as a subprocess."""
    _suite_runs[suite_id]["status"] = "running"
    try:
        script_path = BACKEND_DIR / "scripts" / "run_benchmark_suite.py"
        if not script_path.exists():
            raise FileNotFoundError(f"run_benchmark_suite.py not found at {script_path}")

        cmd = [
            sys.executable, str(script_path),
            "--app-ids", ",".join(app_ids),
            "--max-interactions", str(max_interactions),
            "--suite-id", suite_id,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(BACKEND_DIR),
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(
                f"run_benchmark_suite.py exited with code {proc.returncode}: "
                f"{stderr.decode(errors='replace')[:500]}"
            )

        # Attempt to parse JSON output from the script
        output = stdout.decode(errors="replace").strip()
        results = None
        if output:
            try:
                results = json.loads(output)
            except json.JSONDecodeError:
                results = {"raw_output": output[:2000]}

        _suite_runs[suite_id]["status"] = "done"
        _suite_runs[suite_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        _suite_runs[suite_id]["results"] = results

    except Exception as e:
        logger.exception(f"Suite run {suite_id} failed")
        _suite_runs[suite_id]["status"] = "error"
        _suite_runs[suite_id]["error"] = str(e)
        _suite_runs[suite_id]["completed_at"] = datetime.now(timezone.utc).isoformat()


@router.post("/benchmarks/suite/run", response_model=SuiteRunStatus)
async def start_suite_run(
    req: SuiteRunRequest,
    background_tasks: BackgroundTasks,
):
    """
    Launch a multi-app benchmark suite in the background.

    If app_ids is empty or null, runs all available apps from the registry.
    """
    # Resolve app IDs
    if req.app_ids:
        app_ids = req.app_ids
    else:
        # Load all available apps from registry
        if not APP_REGISTRY_PATH.exists():
            raise HTTPException(404, "App registry not found — cannot determine apps to run")
        with open(APP_REGISTRY_PATH) as f:
            registry = json.load(f)
        app_ids = [a["app_id"] for a in registry.get("apps", []) if a.get("available", False)]
        if not app_ids:
            raise HTTPException(422, "No available apps found in registry")

    suite_id = f"suite-{uuid.uuid4().hex[:8]}"
    started_at = datetime.now(timezone.utc).isoformat()
    _suite_runs[suite_id] = {
        "suite_id": suite_id,
        "status": "pending",
        "apps": app_ids,
        "started_at": started_at,
    }
    background_tasks.add_task(_run_suite_bg, suite_id, app_ids, req.max_interactions)
    return SuiteRunStatus(
        suite_id=suite_id,
        status="pending",
        apps=app_ids,
        started_at=started_at,
    )


@router.get("/benchmarks/suite/runs/{suite_id}", response_model=SuiteRunStatus)
async def get_suite_run(suite_id: str):
    """Return status of a suite run from the in-memory registry."""
    run = _suite_runs.get(suite_id)
    if not run:
        raise HTTPException(404, f"Suite run {suite_id} not found")
    return SuiteRunStatus(
        suite_id=run["suite_id"],
        status=run["status"],
        apps=run["apps"],
        started_at=run["started_at"],
        completed_at=run.get("completed_at"),
        error=run.get("error"),
        results=run.get("results"),
    )
