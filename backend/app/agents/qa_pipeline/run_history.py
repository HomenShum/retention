"""Run History — time-series aggregation across all QA pipeline runs.

Scans pipeline_results/ and run_logs/ to build aggregated views:
- Per-app trends (pass rate, bugs, cost over time)
- Daily/weekly/monthly rollups
- Action pathing persistence
- Before/after state tracking

Storage: Reads from existing data/pipeline_results/*.json and data/run_logs/*.json
         Writes aggregated index to data/run_history_index.json
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_RESULTS_DIR = _DATA_DIR / "pipeline_results"
_LOGS_DIR = _DATA_DIR / "run_logs"
_INDEX_PATH = _DATA_DIR / "run_history_index.json"


def _normalize_execution(result: dict) -> dict:
    """Same normalization as mcp_pipeline — handles list vs dict execution."""
    raw = result.get("execution")
    if raw is None:
        return {"results": [], "passed": 0, "failed": 0, "total": 0, "pass_rate": 0.0}
    if isinstance(raw, list):
        results = raw
        passed = sum(1 for r in results if r.get("status") in ("passed", "pass"))
        total = len(results)
        return {"results": results, "passed": passed, "failed": total - passed,
                "total": total, "pass_rate": passed / total if total else 0.0}
    if isinstance(raw, dict):
        return raw
    return {"results": [], "passed": 0, "failed": 0, "total": 0, "pass_rate": 0.0}


def _parse_run_file(path: Path) -> Optional[Dict[str, Any]]:
    """Parse a single pipeline result file into a normalized run record."""
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return None

    result = data.get("result", data)
    execution = _normalize_execution(result)
    token_usage = result.get("token_usage", {})

    # Parse timestamps
    started_at = data.get("started_at", "")
    completed_at = data.get("completed_at", "")
    duration_s = data.get("duration_s", 0)

    if not duration_s and started_at and completed_at:
        try:
            t0 = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            duration_s = round((t1 - t0).total_seconds(), 1)
        except Exception:
            pass

    # Extract date for aggregation
    run_date = ""
    if started_at:
        try:
            run_date = started_at[:10]  # YYYY-MM-DD
        except Exception:
            pass

    run_id = data.get("run_id", path.stem)
    app_name = data.get("app_name", result.get("app_name", "Unknown"))
    app_url = data.get("app_url", result.get("app_url", ""))

    # Collect failure details
    failures = []
    for r in execution.get("results", []):
        if r.get("status") not in ("passed", "pass"):
            failures.append({
                "test_id": r.get("test_id", ""),
                "name": r.get("name", ""),
                "error": (r.get("error") or "")[:100],
            })

    return {
        "run_id": run_id,
        "app_name": app_name,
        "app_url": app_url,
        "run_date": run_date,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_s": duration_s,
        "total_tests": execution.get("total", 0),
        "passed": execution.get("passed", 0),
        "failed": execution.get("failed", 0),
        "pass_rate": round(execution.get("pass_rate", 0.0), 4),
        "total_tokens": token_usage.get("total_tokens", 0),
        "estimated_cost_usd": token_usage.get("estimated_cost_usd", 0.0),
        "flow_type": data.get("flow_type", "unknown"),
        "is_rerun": "rerun" in run_id or data.get("baseline_run_id") is not None,
        "baseline_run_id": data.get("baseline_run_id"),
        "failure_count": len(failures),
        "failures": failures[:5],  # Cap at 5 for index size
    }


def build_index(force: bool = False) -> Dict[str, Any]:
    """Scan all pipeline results and build a time-series index.

    Returns the full index dict with per-app and per-day aggregations.
    """
    if not force and _INDEX_PATH.exists():
        try:
            with open(_INDEX_PATH) as f:
                existing = json.load(f)
            # Check if results dir has new files
            result_files = list(_RESULTS_DIR.glob("*.json"))
            if len(result_files) <= existing.get("total_runs", 0):
                return existing
        except Exception:
            pass

    runs = []
    for p in sorted(_RESULTS_DIR.glob("*.json")):
        record = _parse_run_file(p)
        if record:
            runs.append(record)

    # Sort by start time
    runs.sort(key=lambda r: r.get("started_at", ""))

    # Per-app aggregation
    by_app: Dict[str, List] = defaultdict(list)
    for r in runs:
        by_app[r["app_name"]].append(r)

    app_summaries = {}
    for app_name, app_runs in by_app.items():
        full_runs = [r for r in app_runs if not r["is_rerun"]]
        reruns = [r for r in app_runs if r["is_rerun"]]

        avg_pass_rate = sum(r["pass_rate"] for r in full_runs) / len(full_runs) if full_runs else 0
        avg_duration = sum(r["duration_s"] for r in full_runs) / len(full_runs) if full_runs else 0
        total_cost = sum(r["estimated_cost_usd"] for r in app_runs)
        total_tokens = sum(r["total_tokens"] for r in app_runs)

        # Trend: compare first half vs second half of runs
        trend = "stable"
        if len(full_runs) >= 4:
            mid = len(full_runs) // 2
            first_half_avg = sum(r["pass_rate"] for r in full_runs[:mid]) / mid
            second_half_avg = sum(r["pass_rate"] for r in full_runs[mid:]) / (len(full_runs) - mid)
            if second_half_avg > first_half_avg + 0.05:
                trend = "improving"
            elif second_half_avg < first_half_avg - 0.05:
                trend = "regressing"

        app_summaries[app_name] = {
            "total_runs": len(app_runs),
            "full_runs": len(full_runs),
            "reruns": len(reruns),
            "avg_pass_rate": round(avg_pass_rate, 4),
            "avg_duration_s": round(avg_duration, 1),
            "total_cost_usd": round(total_cost, 4),
            "total_tokens": total_tokens,
            "trend": trend,
            "first_run": full_runs[0]["started_at"] if full_runs else "",
            "last_run": full_runs[-1]["started_at"] if full_runs else "",
            "best_pass_rate": max((r["pass_rate"] for r in full_runs), default=0),
            "worst_pass_rate": min((r["pass_rate"] for r in full_runs), default=0),
        }

    # Per-day aggregation
    by_day: Dict[str, List] = defaultdict(list)
    for r in runs:
        if r["run_date"]:
            by_day[r["run_date"]].append(r)

    daily_rollups = {}
    for day, day_runs in sorted(by_day.items()):
        daily_rollups[day] = {
            "runs": len(day_runs),
            "avg_pass_rate": round(sum(r["pass_rate"] for r in day_runs) / len(day_runs), 4),
            "total_tokens": sum(r["total_tokens"] for r in day_runs),
            "total_cost_usd": round(sum(r["estimated_cost_usd"] for r in day_runs), 4),
            "apps_tested": list(set(r["app_name"] for r in day_runs)),
            "total_failures": sum(r["failure_count"] for r in day_runs),
        }

    # Per-week aggregation
    weekly_rollups = {}
    for r in runs:
        if r["run_date"]:
            try:
                dt = datetime.strptime(r["run_date"], "%Y-%m-%d")
                week_start = dt - timedelta(days=dt.weekday())
                week_key = week_start.strftime("%Y-W%W")
            except Exception:
                continue
            if week_key not in weekly_rollups:
                weekly_rollups[week_key] = {"runs": 0, "pass_rates": [], "tokens": 0, "cost": 0.0, "failures": 0}
            weekly_rollups[week_key]["runs"] += 1
            weekly_rollups[week_key]["pass_rates"].append(r["pass_rate"])
            weekly_rollups[week_key]["tokens"] += r["total_tokens"]
            weekly_rollups[week_key]["cost"] += r["estimated_cost_usd"]
            weekly_rollups[week_key]["failures"] += r["failure_count"]

    for wk, wdata in weekly_rollups.items():
        rates = wdata.pop("pass_rates", [])
        wdata["avg_pass_rate"] = round(sum(rates) / len(rates), 4) if rates else 0
        wdata["cost"] = round(wdata["cost"], 4)

    # ── Monthly rollups ───────────────────────────────────────────────────
    monthly_rollups: Dict[str, dict] = {}
    for r in runs:
        month_key = r["run_date"][:7]  # YYYY-MM
        if month_key not in monthly_rollups:
            monthly_rollups[month_key] = {"runs": 0, "pass_rates": [], "tokens": 0, "cost": 0.0, "failures": 0, "apps": set()}
        monthly_rollups[month_key]["runs"] += 1
        monthly_rollups[month_key]["pass_rates"].append(r["pass_rate"])
        monthly_rollups[month_key]["tokens"] += r["total_tokens"]
        monthly_rollups[month_key]["cost"] += r["estimated_cost_usd"]
        monthly_rollups[month_key]["failures"] += r["failure_count"]
        monthly_rollups[month_key]["apps"].add(r["app_name"])

    for mk, mdata in monthly_rollups.items():
        rates = mdata.pop("pass_rates", [])
        mdata["avg_pass_rate"] = round(sum(rates) / len(rates), 4) if rates else 0
        mdata["cost"] = round(mdata["cost"], 4)
        mdata["unique_apps"] = len(mdata.pop("apps", set()))

    # ── Quarterly rollups ─────────────────────────────────────────────────
    quarterly_rollups: Dict[str, dict] = {}
    for r in runs:
        try:
            dt = datetime.strptime(r["run_date"], "%Y-%m-%d")
            q = (dt.month - 1) // 3 + 1
            q_key = f"{dt.year}-Q{q}"
        except Exception:
            continue
        if q_key not in quarterly_rollups:
            quarterly_rollups[q_key] = {"runs": 0, "pass_rates": [], "tokens": 0, "cost": 0.0, "failures": 0, "apps": set()}
        quarterly_rollups[q_key]["runs"] += 1
        quarterly_rollups[q_key]["pass_rates"].append(r["pass_rate"])
        quarterly_rollups[q_key]["tokens"] += r["total_tokens"]
        quarterly_rollups[q_key]["cost"] += r["estimated_cost_usd"]
        quarterly_rollups[q_key]["failures"] += r["failure_count"]
        quarterly_rollups[q_key]["apps"].add(r["app_name"])

    for qk, qdata in quarterly_rollups.items():
        rates = qdata.pop("pass_rates", [])
        qdata["avg_pass_rate"] = round(sum(rates) / len(rates), 4) if rates else 0
        qdata["cost"] = round(qdata["cost"], 4)
        qdata["unique_apps"] = len(qdata.pop("apps", set()))

    # ── Yearly rollups ────────────────────────────────────────────────────
    yearly_rollups: Dict[str, dict] = {}
    for r in runs:
        year_key = r["run_date"][:4]
        if year_key not in yearly_rollups:
            yearly_rollups[year_key] = {"runs": 0, "pass_rates": [], "tokens": 0, "cost": 0.0, "failures": 0, "apps": set()}
        yearly_rollups[year_key]["runs"] += 1
        yearly_rollups[year_key]["pass_rates"].append(r["pass_rate"])
        yearly_rollups[year_key]["tokens"] += r["total_tokens"]
        yearly_rollups[year_key]["cost"] += r["estimated_cost_usd"]
        yearly_rollups[year_key]["failures"] += r["failure_count"]
        yearly_rollups[year_key]["apps"].add(r["app_name"])

    for yk, ydata in yearly_rollups.items():
        rates = ydata.pop("pass_rates", [])
        ydata["avg_pass_rate"] = round(sum(rates) / len(rates), 4) if rates else 0
        ydata["cost"] = round(ydata["cost"], 4)
        ydata["unique_apps"] = len(ydata.pop("apps", set()))

    index = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "total_runs": len(runs),
        "total_apps": len(app_summaries),
        "date_range": {
            "first": runs[0]["run_date"] if runs else "",
            "last": runs[-1]["run_date"] if runs else "",
        },
        "totals": {
            "tokens": sum(r["total_tokens"] for r in runs),
            "cost_usd": round(sum(r["estimated_cost_usd"] for r in runs), 4),
            "tests_executed": sum(r["total_tests"] for r in runs),
            "failures_found": sum(r["failure_count"] for r in runs),
        },
        "by_app": app_summaries,
        "by_day": daily_rollups,
        "by_week": weekly_rollups,
        "by_month": monthly_rollups,
        "by_quarter": quarterly_rollups,
        "by_year": yearly_rollups,
        "runs": runs,  # Full run list for detailed queries
    }

    # Persist
    try:
        _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_INDEX_PATH, "w") as f:
            json.dump(index, f, indent=2, default=str)
        logger.info(f"Run history index built: {len(runs)} runs, {len(app_summaries)} apps")
    except Exception as e:
        logger.warning(f"Failed to persist run history index: {e}")

    return index


def get_app_trend(app_name: str, days: int = 30) -> Dict[str, Any]:
    """Get pass rate trend for a specific app over N days."""
    index = build_index()
    runs = [r for r in index.get("runs", []) if r["app_name"] == app_name and not r["is_rerun"]]

    if not runs:
        return {"app_name": app_name, "runs": 0, "trend": "no_data"}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    recent = [r for r in runs if r.get("started_at", "") >= cutoff]

    return {
        "app_name": app_name,
        "total_runs": len(runs),
        "recent_runs": len(recent),
        "days": days,
        "pass_rates": [{"date": r["run_date"], "rate": r["pass_rate"]} for r in recent],
        "avg_pass_rate": round(sum(r["pass_rate"] for r in recent) / len(recent), 4) if recent else 0,
        "trend": index.get("by_app", {}).get(app_name, {}).get("trend", "unknown"),
    }


def get_health_summary() -> Dict[str, Any]:
    """Overall health summary — is the product getting better or worse?"""
    index = build_index()

    by_week = index.get("by_week", {})
    weeks = sorted(by_week.keys())

    if len(weeks) < 2:
        direction = "insufficient_data"
    else:
        recent = by_week[weeks[-1]]["avg_pass_rate"]
        previous = by_week[weeks[-2]]["avg_pass_rate"]
        if recent > previous + 0.05:
            direction = "improving"
        elif recent < previous - 0.05:
            direction = "regressing"
        else:
            direction = "stable"

    return {
        "total_runs": index.get("total_runs", 0),
        "total_apps": index.get("total_apps", 0),
        "total_cost_usd": index.get("totals", {}).get("cost_usd", 0),
        "total_tests_executed": index.get("totals", {}).get("tests_executed", 0),
        "total_failures_found": index.get("totals", {}).get("failures_found", 0),
        "direction": direction,
        "weekly_trend": [
            {"week": w, **by_week[w]} for w in weeks[-8:]  # Last 8 weeks
        ],
    }
