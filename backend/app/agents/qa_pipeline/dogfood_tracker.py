"""Dogfood tracker — records and aggregates QA runs against our own apps.

Persists run results to backend/data/dogfood/runs/ and provides trend
aggregation for the DogfoodProof landing page component.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DOGFOOD_DIR = Path(__file__).resolve().parents[3] / "data" / "dogfood"
_RUNS_DIR = _DOGFOOD_DIR / "runs"


def _ensure_dirs() -> None:
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)


def record_run(run_result: dict[str, Any], app_url: str) -> Path:
    """Persist a dogfood run result to disk."""
    _ensure_dirs()

    run_id = run_result.get("run_id", "unknown")
    ts = datetime.now(timezone.utc)
    date_str = ts.strftime("%Y-%m-%d")

    entry = {
        "run_id": run_id,
        "app_url": app_url,
        "timestamp": ts.isoformat(),
        "date": date_str,
        "duration_s": run_result.get("duration_seconds", 0),
        "tests_total": run_result.get("total_tests", 0),
        "tests_passed": run_result.get("passed_tests", 0),
        "pass_rate": run_result.get("pass_rate", 0.0),
        "token_usage": run_result.get("token_usage", {}),
        "memory_cache_hit": run_result.get("memory_cache_hit", False),
        "tokens_saved": run_result.get("tokens_saved", 0),
    }

    path = _RUNS_DIR / f"{date_str}_{run_id}.json"
    path.write_text(json.dumps(entry, indent=2))
    logger.info(f"Dogfood run recorded: {path}")
    return path


def _load_runs(days: int = 30) -> list[dict[str, Any]]:
    """Load all runs within the specified window."""
    _ensure_dirs()

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    runs: list[dict[str, Any]] = []

    for f in sorted(_RUNS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            ts = datetime.fromisoformat(data["timestamp"])
            if ts >= cutoff:
                runs.append(data)
        except Exception:
            continue

    return runs


def get_trends(days: int = 30) -> dict[str, Any]:
    """Aggregate trends across dogfood runs."""
    runs = _load_runs(days)

    if not runs:
        return {
            "total_runs": 0,
            "days": days,
            "daily": [],
            "avg_pass_rate": 0.0,
            "total_tokens_saved": 0,
        }

    # Aggregate by date
    daily: dict[str, dict[str, Any]] = {}
    for run in runs:
        date = run["date"]
        if date not in daily:
            daily[date] = {
                "date": date,
                "runs": 0,
                "tests_total": 0,
                "tests_passed": 0,
                "tokens_saved": 0,
                "cost_usd": 0.0,
            }
        d = daily[date]
        d["runs"] += 1
        d["tests_total"] += run.get("tests_total", 0)
        d["tests_passed"] += run.get("tests_passed", 0)
        d["tokens_saved"] += run.get("tokens_saved", 0)
        token_usage = run.get("token_usage", {})
        d["cost_usd"] += token_usage.get("estimated_cost_usd", 0.0)

    # Compute pass rates per day
    daily_list = []
    for d in sorted(daily.values(), key=lambda x: x["date"]):
        d["pass_rate"] = (
            d["tests_passed"] / d["tests_total"] if d["tests_total"] > 0 else 0.0
        )
        daily_list.append(d)

    total_passed = sum(r.get("tests_passed", 0) for r in runs)
    total_tests = sum(r.get("tests_total", 0) for r in runs)
    total_tokens_saved = sum(r.get("tokens_saved", 0) for r in runs)

    return {
        "total_runs": len(runs),
        "days": days,
        "daily": daily_list,
        "avg_pass_rate": total_passed / total_tests if total_tests > 0 else 0.0,
        "total_tokens_saved": total_tokens_saved,
        "total_tests": total_tests,
        "total_passed": total_passed,
    }


def get_savings_proof() -> dict[str, Any]:
    """Curated proof bundle for the landing page DogfoodProof component."""
    trends = get_trends(days=30)
    runs = _load_runs(days=30)

    # Count cache hits
    cache_hits = sum(1 for r in runs if r.get("memory_cache_hit", False))
    cache_hit_rate = cache_hits / len(runs) if runs else 0.0

    # Build savings curve (cost per run over time)
    savings_curve: list[dict[str, Any]] = []
    for i, run in enumerate(runs):
        token_usage = run.get("token_usage", {})
        savings_curve.append({
            "run_index": i + 1,
            "cost_usd": token_usage.get("estimated_cost_usd", 0.0),
            "tokens_saved": run.get("tokens_saved", 0),
            "date": run["date"],
        })

    # Latest run
    latest = runs[-1] if runs else None

    return {
        "total_runs": trends["total_runs"],
        "total_tokens_saved": trends["total_tokens_saved"],
        "avg_pass_rate": trends["avg_pass_rate"],
        "cache_hit_rate": cache_hit_rate,
        "savings_curve": savings_curve,
        "latest_run": {
            "run_id": latest["run_id"],
            "pass_rate": latest.get("pass_rate", 0),
            "tests_total": latest.get("tests_total", 0),
            "date": latest["date"],
        }
        if latest
        else None,
    }
