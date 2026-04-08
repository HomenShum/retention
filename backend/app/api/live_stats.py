"""
Live Stats API — scans actual data files and returns verified aggregated stats.

Every number returned is traceable to a source file on disk.
No hardcoded demo data. No Math.random(). No fabrication.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stats", tags=["live-stats"])

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _load_json_files(subdir: str) -> List[Dict[str, Any]]:
    """Load all JSON files from a data subdirectory."""
    d = _DATA_DIR / subdir
    if not d.exists():
        return []
    results = []
    for f in sorted(d.glob("*.json")):
        try:
            results.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return results


def _aggregate_replay_results() -> Dict[str, Any]:
    """Aggregate stats from replay_results/*.json — 100% real data."""
    files = _load_json_files("replay_results")
    if not files:
        return {"total": 0, "source_files": 0}

    total = len(files)
    successes = sum(1 for f in files if f.get("success", False))
    token_savings = [f.get("comparison_with_full", {}).get("token_savings_pct", 0) for f in files]
    time_savings = [f.get("comparison_with_full", {}).get("time_savings_pct", 0) for f in files]
    tokens_full = sum(f.get("comparison_with_full", {}).get("tokens_full", 0) for f in files)
    tokens_replay = sum(f.get("comparison_with_full", {}).get("tokens_replay", 0) for f in files)
    total_time_s = sum(f.get("time_seconds", 0) for f in files)

    # Group by workflow
    by_workflow: Dict[str, List[Dict]] = defaultdict(list)
    for f in files:
        wf = f.get("workflow", "unknown")
        by_workflow[wf].append(f)

    workflow_stats = {}
    for wf, runs in by_workflow.items():
        wf_savings = [r.get("comparison_with_full", {}).get("token_savings_pct", 0) for r in runs]
        wf_successes = sum(1 for r in runs if r.get("success", False))
        workflow_stats[wf] = {
            "runs": len(runs),
            "success_rate": round(wf_successes / len(runs), 3) if runs else 0,
            "avg_token_savings": round(sum(wf_savings) / len(wf_savings), 1) if wf_savings else 0,
        }

    # Daily breakdown from actual timestamps
    daily: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"runs": 0, "tokens_saved": 0})
    for f in files:
        ts = f.get("timestamp", "")
        if ts:
            day = ts[:10]  # YYYY-MM-DD
            comp = f.get("comparison_with_full", {})
            daily[day]["runs"] += 1
            daily[day]["tokens_saved"] += comp.get("tokens_full", 0) - comp.get("tokens_replay", 0)

    daily_breakdown = [
        {"date": d, "runs": v["runs"], "tokens_saved": v["tokens_saved"]}
        for d, v in sorted(daily.items())
    ]

    return {
        "total": total,
        "source_files": total,
        "success_rate": round(successes / total, 3) if total else 0,
        "avg_token_savings_pct": round(sum(token_savings) / len(token_savings), 1) if token_savings else 0,
        "avg_time_savings_pct": round(sum(time_savings) / len(time_savings), 1) if time_savings else 0,
        "total_tokens_saved": tokens_full - tokens_replay,
        "total_time_s": round(total_time_s, 1),
        "by_workflow": workflow_stats,
        "daily_breakdown": daily_breakdown,
    }


def _aggregate_eval_results() -> Dict[str, Any]:
    """Aggregate stats from rerun_eval/*.json — 100% real data."""
    files = _load_json_files("rerun_eval")
    if not files:
        return {"total": 0, "source_files": 0}

    total = len(files)
    composites = [f.get("composite_score", 0) for f in files]
    grades = defaultdict(int)
    for f in files:
        grades[f.get("grade", "?")] += 1

    token_savings = [f.get("token_savings_pct", 0) for f in files]
    cost_savings = [f.get("cost_savings_pct", 0) for f in files]
    total_cost_baseline = sum(f.get("cost_baseline_usd", 0) for f in files)
    total_cost_replay = sum(f.get("cost_replay_usd", 0) for f in files)

    return {
        "total": total,
        "source_files": total,
        "avg_composite": round(sum(composites) / len(composites), 3) if composites else 0,
        "grade_distribution": dict(grades),
        "avg_token_savings_pct": round(sum(token_savings) / len(token_savings), 1) if token_savings else 0,
        "avg_cost_savings_pct": round(sum(cost_savings) / len(cost_savings), 1) if cost_savings else 0,
        "total_cost_baseline_usd": round(total_cost_baseline, 2),
        "total_cost_replay_usd": round(total_cost_replay, 2),
        "total_cost_saved_usd": round(total_cost_baseline - total_cost_replay, 2),
    }


def _count_data_assets() -> Dict[str, Any]:
    """Count all data assets on disk."""
    def _count(subdir: str, ext: str = "*.json") -> int:
        p = _DATA_DIR / subdir
        return len(list(p.glob(ext))) if p.exists() else 0

    # List trajectory workflows
    traj_dir = _DATA_DIR / "trajectories"
    workflows = []
    if traj_dir.exists():
        for d in traj_dir.iterdir():
            if d.is_dir():
                count = len(list(d.glob("*.json")))
                if count > 0:
                    workflows.append({"name": d.name, "trajectories": count})

    # List manifests
    manifests = []
    manifest_dir = _DATA_DIR / "rop_manifests"
    if manifest_dir.exists():
        for f in manifest_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                manifests.append({
                    "id": data.get("id", f.stem),
                    "name": data.get("name", f.stem),
                    "short_name": data.get("short_name", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue

    return {
        "replay_results": _count("replay_results"),
        "rerun_evals": _count("rerun_eval"),
        "three_lane_benchmarks": _count("three_lane_benchmarks"),
        "trajectories": sum(w["trajectories"] for w in workflows),
        "trajectory_workflows": workflows,
        "rop_manifests": len(manifests),
        "manifest_list": manifests,
        "rop_patterns": _count("rop_patterns"),
        "savings_records": _count("rop_savings", "*.jsonl"),
        "suggestion_logs": _count("rop_suggestions", "*.jsonl"),
    }


@router.get("/live")
async def get_live_stats() -> Dict[str, Any]:
    """Return verified stats computed from actual data files.

    Every number is traceable to source files on disk.
    No hardcoded values. No random generation. No fabrication.
    """
    replay = _aggregate_replay_results()
    evals = _aggregate_eval_results()
    assets = _count_data_assets()

    return {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(_DATA_DIR),
        "replay_results": replay,
        "eval_results": evals,
        "data_assets": assets,
        "summary": {
            "total_runs": replay["total"] + evals["total"],
            "total_cost_saved_usd": evals.get("total_cost_saved_usd", 0),
            "avg_token_savings_pct": replay.get("avg_token_savings_pct", 0),
            "replay_success_rate": replay.get("success_rate", 0),
            "avg_eval_composite": evals.get("avg_composite", 0),
            "manifests_count": assets.get("rop_manifests", 0),
        },
    }
