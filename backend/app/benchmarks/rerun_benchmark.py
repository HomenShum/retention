"""Rerun Benchmark — measures time/token savings from rerun-after-fix vs full re-run.

Executes:
  1. Full QA pipeline run (crawl → workflow → testcase → execution)
  2. Records baseline metrics
  3. Simulates a fix (no actual code change needed — just reruns)
  4. Runs ta.pipeline.rerun_failures (execution only, skip crawl/workflow/testcase)
  5. Measures delta: time saved, tokens saved, stages skipped

Usage:
  from app.benchmarks.rerun_benchmark import run_rerun_benchmark
  result = await run_rerun_benchmark(app_url="http://localhost:8878", app_name="QuickBook")
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BENCHMARK_DIR = Path(__file__).resolve().parents[2] / "data" / "benchmark_runs"
_BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)


async def _call_mcp(tool: str, args: dict, token: str = "sk-ret-de55f65c", base: str = "http://localhost:8000") -> dict:
    """Call an MCP tool via HTTP."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base}/mcp/tools/call",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            json={"tool": tool, "arguments": args},
        )
        raw = re.sub(r"[\x00-\x1f\x7f]", " ", resp.text)
        return json.loads(raw)


async def _poll_until_done(run_id: str, timeout_s: int = 3600) -> dict:
    """Poll pipeline status until complete or error."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await asyncio.sleep(10)
        try:
            result = await _call_mcp("ta.pipeline.status", {"run_id": run_id})
            status = result.get("result", {}).get("status", "")
            if status in ("complete", "error"):
                return result.get("result", {})
        except Exception:
            continue
    return {"status": "timeout", "error": f"Timed out after {timeout_s}s"}


async def run_rerun_benchmark(
    app_url: str,
    app_name: str = "Benchmark App",
    flow_type: str = "web",
    token: str = "sk-ret-de55f65c",
) -> dict:
    """Run the full rerun benchmark: full pipeline → rerun failures → measure delta."""

    benchmark_id = f"rerun-bench-{int(time.time())}"
    results: dict[str, Any] = {
        "benchmark_id": benchmark_id,
        "app_url": app_url,
        "app_name": app_name,
        "flow_type": flow_type,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    # ── STEP 1: Full pipeline run ────────────────────────────────────
    logger.info(f"[{benchmark_id}] Step 1: Starting full pipeline on {app_url}")
    t0 = time.monotonic()

    tool = f"ta.run_{flow_type}_flow"
    start_args = {"app_name": app_name, "timeout_seconds": 3600}
    if flow_type == "web":
        start_args["url"] = app_url
    else:
        start_args["app_package"] = app_url

    start_result = await _call_mcp(tool, start_args, token)
    full_run_id = start_result.get("result", {}).get("run_id", "")
    if not full_run_id:
        results["error"] = f"Failed to start full pipeline: {json.dumps(start_result)[:200]}"
        return results

    logger.info(f"[{benchmark_id}] Full pipeline started: {full_run_id}")

    # Poll until complete
    full_status = await _poll_until_done(full_run_id, timeout_s=3600)
    full_time = round(time.monotonic() - t0, 1)

    results["full_run"] = {
        "run_id": full_run_id,
        "wall_clock_s": full_time,
        "status": full_status.get("status", "unknown"),
        "event_count": full_status.get("event_count", 0),
        "error": full_status.get("error"),
    }

    if full_status.get("status") != "complete":
        results["error"] = f"Full pipeline did not complete: {full_status.get('error', 'unknown')}"
        _persist(benchmark_id, results)
        return results

    # Get failure bundle
    bundle_result = await _call_mcp("ta.pipeline.failure_bundle", {"run_id": full_run_id}, token)
    bundle = bundle_result.get("result", {})
    results["full_run"]["summary"] = bundle.get("summary", {})
    results["full_run"]["failure_count"] = len(bundle.get("failures", []))

    # ── STEP 2: Rerun failures only ──────────────────────────────────
    if not bundle.get("failures"):
        results["rerun"] = {"skipped": True, "reason": "No failures to rerun — all tests passed."}
        results["delta"] = {"time_saved_s": 0, "time_saved_pct": 0, "stages_skipped": []}
        _persist(benchmark_id, results)
        return results

    logger.info(f"[{benchmark_id}] Step 2: Rerunning {len(bundle['failures'])} failures from {full_run_id}")
    t1 = time.monotonic()

    rerun_result = await _call_mcp("ta.pipeline.rerun_failures", {
        "baseline_run_id": full_run_id,
        "failures_only": True,
    }, token)
    rerun_run_id = rerun_result.get("result", {}).get("run_id", "")

    if not rerun_run_id:
        results["rerun"] = {"error": f"Failed to start rerun: {json.dumps(rerun_result)[:200]}"}
        _persist(benchmark_id, results)
        return results

    # Poll rerun
    rerun_status = await _poll_until_done(rerun_run_id, timeout_s=3600)
    rerun_time = round(time.monotonic() - t1, 1)

    # Get rerun bundle
    rerun_bundle_result = await _call_mcp("ta.pipeline.failure_bundle", {"run_id": rerun_run_id}, token)
    rerun_bundle = rerun_bundle_result.get("result", {})

    results["rerun"] = {
        "run_id": rerun_run_id,
        "wall_clock_s": rerun_time,
        "status": rerun_status.get("status", "unknown"),
        "event_count": rerun_status.get("event_count", 0),
        "summary": rerun_bundle.get("summary", {}),
        "failure_count": len(rerun_bundle.get("failures", [])),
        "stages_skipped": ["CRAWL", "WORKFLOW", "TESTCASE"],
    }

    # ── STEP 3: Compute delta ────────────────────────────────────────
    time_saved = round(full_time - rerun_time, 1)
    time_saved_pct = round((time_saved / full_time * 100) if full_time > 0 else 0, 1)

    results["delta"] = {
        "full_run_time_s": full_time,
        "rerun_time_s": rerun_time,
        "time_saved_s": time_saved,
        "time_saved_pct": time_saved_pct,
        "stages_skipped": ["CRAWL", "WORKFLOW", "TESTCASE"],
        "verdict": f"Rerun saved {time_saved_pct}% ({time_saved}s) by skipping crawl/workflow/testcase stages.",
    }

    results["completed_at"] = datetime.now(timezone.utc).isoformat()

    # Record lineage
    try:
        from .lineage import record_lineage
        record_lineage(
            current_run_id=rerun_run_id,
            baseline_run_id=full_run_id,
            change_applied="Rerun benchmark — no actual fix applied (measuring rerun overhead)",
            change_type="rerun",
            thread_mode="continuous",
            app_name=app_name,
        )
    except Exception as e:
        logger.warning(f"Failed to record lineage: {e}")

    _persist(benchmark_id, results)
    return results


def _persist(benchmark_id: str, data: dict) -> None:
    """Save rerun benchmark results to disk."""
    bench_dir = _BENCHMARK_DIR / benchmark_id
    bench_dir.mkdir(parents=True, exist_ok=True)
    path = bench_dir / "results.json"
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info(f"Persisted rerun benchmark: {path}")
    except Exception as e:
        logger.warning(f"Failed to persist rerun benchmark {benchmark_id}: {e}")
