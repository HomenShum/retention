"""Benchmark Run Lineage — tracks before/after comparisons across runs.

Records the relationship between sequential benchmark runs:
  - Which run was the baseline
  - What change was applied (bug fix, feature CR)
  - Delta metrics (time, tokens, pass rate, bugs found)
  - Thread mode (fresh vs continuous)

Usage:
  from app.benchmarks.lineage import record_lineage, get_lineage, compare_runs

  record_lineage(
      current_run_id="web-abc123",
      baseline_run_id="web-xyz789",
      change_applied="Fixed BOOK-001: search filtering",
      thread_mode="continuous",
  )

  lineage = get_lineage("web-abc123")
  comparison = compare_runs("web-xyz789", "web-abc123")
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_LINEAGE_DIR = Path(__file__).resolve().parents[2] / "data" / "benchmark_lineage"
_LINEAGE_DIR.mkdir(parents=True, exist_ok=True)


def record_lineage(
    *,
    current_run_id: str,
    baseline_run_id: Optional[str] = None,
    change_applied: str = "",
    change_type: str = "bug_fix",  # bug_fix | feature_cr | config_change | rerun
    thread_mode: str = "continuous",  # fresh | continuous
    app_name: str = "",
    notes: str = "",
) -> dict:
    """Record lineage for a benchmark run."""
    entry = {
        "current_run_id": current_run_id,
        "baseline_run_id": baseline_run_id,
        "change_applied": change_applied,
        "change_type": change_type,
        "thread_mode": thread_mode,
        "app_name": app_name,
        "notes": notes,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }

    path = _LINEAGE_DIR / f"{current_run_id}.json"
    try:
        with open(path, "w") as f:
            json.dump(entry, f, indent=2, default=str)
        logger.info(f"Recorded lineage for {current_run_id} (baseline: {baseline_run_id})")
    except Exception as e:
        logger.warning(f"Failed to record lineage for {current_run_id}: {e}")

    return entry


def get_lineage(run_id: str) -> Optional[dict]:
    """Get lineage entry for a run."""
    path = _LINEAGE_DIR / f"{run_id}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def get_chain(run_id: str, max_depth: int = 20) -> list[dict]:
    """Follow the lineage chain backwards from a run to its root."""
    chain = []
    current = run_id
    seen = set()
    for _ in range(max_depth):
        if current in seen:
            break
        seen.add(current)
        entry = get_lineage(current)
        if not entry:
            break
        chain.append(entry)
        current = entry.get("baseline_run_id")
        if not current:
            break
    return chain


def compare_runs(baseline_run_id: str, current_run_id: str) -> dict:
    """Compare two pipeline runs and return delta metrics."""
    from ..api.mcp_pipeline import format_compact_bundle

    baseline = format_compact_bundle(baseline_run_id)
    current = format_compact_bundle(current_run_id)

    if "error" in baseline:
        return {"error": f"Baseline not found: {baseline_run_id}"}
    if "error" in current:
        return {"error": f"Current not found: {current_run_id}"}

    b_summary = baseline.get("summary", {})
    c_summary = current.get("summary", {})

    b_failures = {f.get("test_id", ""): f for f in baseline.get("failures", [])}
    c_failures = {f.get("test_id", ""): f for f in current.get("failures", [])}

    # Classify changes
    fixed = sorted(set(b_failures.keys()) - set(c_failures.keys()))
    regressed = sorted(set(c_failures.keys()) - set(b_failures.keys()))
    still_failing = sorted(set(b_failures.keys()) & set(c_failures.keys()))

    return {
        "baseline_run_id": baseline_run_id,
        "current_run_id": current_run_id,
        "baseline": {
            "total": b_summary.get("total", 0),
            "passed": b_summary.get("passed", 0),
            "failed": b_summary.get("failed", 0),
            "pass_rate": b_summary.get("pass_rate", 0),
            "duration_s": baseline.get("duration_s", 0),
        },
        "current": {
            "total": c_summary.get("total", 0),
            "passed": c_summary.get("passed", 0),
            "failed": c_summary.get("failed", 0),
            "pass_rate": c_summary.get("pass_rate", 0),
            "duration_s": current.get("duration_s", 0),
        },
        "delta": {
            "pass_rate_change": round(c_summary.get("pass_rate", 0) - b_summary.get("pass_rate", 0), 4),
            "time_change_s": round(current.get("duration_s", 0) - baseline.get("duration_s", 0), 1),
            "fixed_count": len(fixed),
            "regressed_count": len(regressed),
            "still_failing_count": len(still_failing),
        },
        "fixed": fixed,
        "regressed": regressed,
        "still_failing": still_failing,
        "lineage": get_lineage(current_run_id),
    }


def list_lineage(limit: int = 20) -> list[dict]:
    """List all lineage entries, most recent first."""
    entries = []
    for p in sorted(_LINEAGE_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            with open(p) as f:
                entries.append(json.load(f))
        except Exception:
            pass
    return entries
