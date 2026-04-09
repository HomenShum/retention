"""Savings forecast and ROI MCP tools.

Extends the existing retention.savings.compare with forecast, ROI calculation,
and per-stage cost breakdown capabilities.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dispatch_savings(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle retention.savings.* extended tools."""

    if tool == "retention.savings.forecast":
        return _handle_forecast(args)
    if tool == "retention.savings.roi":
        return _handle_roi(args)
    if tool == "retention.savings.breakdown":
        return _handle_breakdown(args)

    return {"error": f"Unknown savings tool: {tool}"}


def _handle_forecast(args: Dict[str, Any]) -> Dict[str, Any]:
    """Predict savings for future runs based on trajectory history.

    Args:
        trajectory_id: Trajectory to forecast for
        task_name: Task name for lookup
        runs_ahead: How many future runs to forecast (default 10)
    """
    trajectory_id = args.get("trajectory_id", "")
    runs_ahead = int(args.get("runs_ahead", 10))

    if not trajectory_id:
        return {"error": "trajectory_id is required"}

    task_name = args.get("task_name", "")

    try:
        from ..agents.device_testing.trajectory_logger import get_trajectory_logger
        tl = get_trajectory_logger()
        traj = None
        if task_name:
            traj = tl.load_trajectory(task_name, trajectory_id)
        else:
            base = tl._base_dir
            if base.exists():
                for task_dir in base.iterdir():
                    if task_dir.is_dir() and not task_dir.name.startswith("_"):
                        traj = tl.load_trajectory(task_dir.name, trajectory_id)
                        if traj:
                            task_name = task_dir.name
                            break

        if not traj:
            return {"error": f"Trajectory {trajectory_id} not found"}
    except Exception as e:
        return {"error": f"Failed to load trajectory: {e}"}

    replay_count = getattr(traj, "replay_count", 0)
    avg_savings = getattr(traj, "avg_token_savings", 0)
    total_tokens = sum(getattr(s, "tokens_used", 0) for s in getattr(traj, "steps", []))
    drift_score = getattr(traj, "drift_score", 0)

    # Simple forecast model: savings stabilize after ~5 replays, slight improvement after
    forecasts = []
    for i in range(1, runs_ahead + 1):
        run_number = replay_count + i
        # Savings improve with log curve, cap at ~85%
        projected_savings = min(0.85, avg_savings + (1 - avg_savings) * 0.05 * (1 / (1 + run_number * 0.1)))
        # Drift probability increases slowly
        drift_probability = min(0.5, drift_score + 0.01 * i)

        tokens_saved = int(total_tokens * projected_savings)
        cost_per_token = 0.000004  # ~$4/M tokens average
        cost_saved = round(tokens_saved * cost_per_token, 4)

        forecasts.append({
            "run_number": run_number,
            "projected_token_savings_pct": round(projected_savings * 100, 1),
            "projected_tokens_saved": tokens_saved,
            "projected_cost_saved_usd": cost_saved,
            "drift_probability": round(drift_probability, 3),
        })

    total_projected_savings = sum(f["projected_cost_saved_usd"] for f in forecasts)

    return {
        "tool": "retention.savings.forecast",
        "status": "ok",
        "trajectory_id": trajectory_id,
        "task_name": task_name,
        "current_replay_count": replay_count,
        "current_avg_savings_pct": round(avg_savings * 100, 1),
        "forecast_runs": runs_ahead,
        "forecasts": forecasts,
        "total_projected_cost_saved_usd": round(total_projected_savings, 4),
        "generated_at": _now_iso(),
    }


def _handle_roi(args: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate ROI of trajectory investment.

    Args:
        trajectory_id: Trajectory to calculate ROI for
        task_name: Task name for lookup
        full_crawl_cost_usd: Cost of one full crawl (optional, estimated if not provided)
    """
    trajectory_id = args.get("trajectory_id", "")
    task_name = args.get("task_name", "")
    full_crawl_cost = float(args.get("full_crawl_cost_usd", 0))

    if not trajectory_id:
        return {"error": "trajectory_id is required"}

    try:
        from ..agents.device_testing.trajectory_logger import get_trajectory_logger
        tl = get_trajectory_logger()
        traj = None
        if task_name:
            traj = tl.load_trajectory(task_name, trajectory_id)
        else:
            base = tl._base_dir
            if base.exists():
                for task_dir in base.iterdir():
                    if task_dir.is_dir() and not task_dir.name.startswith("_"):
                        traj = tl.load_trajectory(task_dir.name, trajectory_id)
                        if traj:
                            task_name = task_dir.name
                            break

        if not traj:
            return {"error": f"Trajectory {trajectory_id} not found"}
    except Exception as e:
        return {"error": f"Failed to load trajectory: {e}"}

    replay_count = getattr(traj, "replay_count", 0)
    avg_savings = getattr(traj, "avg_token_savings", 0)
    total_tokens = sum(getattr(s, "tokens_used", 0) for s in getattr(traj, "steps", []))

    cost_per_token = 0.000004
    if not full_crawl_cost:
        full_crawl_cost = round(total_tokens * cost_per_token, 4)

    replay_cost = round(total_tokens * (1 - avg_savings) * cost_per_token, 4)

    # Investment: cost of initial crawl + trajectory capture overhead (~10%)
    investment = round(full_crawl_cost * 1.1, 4)
    # Savings: (full_crawl - replay) * replay_count
    total_savings = round((full_crawl_cost - replay_cost) * replay_count, 4)
    roi_pct = round((total_savings / investment) * 100, 1) if investment > 0 else 0

    # Breakeven: how many replays to recoup investment
    savings_per_replay = full_crawl_cost - replay_cost
    breakeven_runs = max(1, round(investment / savings_per_replay)) if savings_per_replay > 0 else float("inf")

    return {
        "tool": "retention.savings.roi",
        "status": "ok",
        "trajectory_id": trajectory_id,
        "task_name": task_name,
        "full_crawl_cost_usd": full_crawl_cost,
        "replay_cost_usd": replay_cost,
        "savings_per_replay_usd": round(savings_per_replay, 4),
        "total_replays": replay_count,
        "total_savings_usd": total_savings,
        "investment_usd": investment,
        "roi_pct": roi_pct,
        "breakeven_runs": breakeven_runs,
        "already_profitable": replay_count >= breakeven_runs,
        "generated_at": _now_iso(),
    }


def _handle_breakdown(args: Dict[str, Any]) -> Dict[str, Any]:
    """Break down savings by pipeline stage.

    Args:
        trajectory_id: Trajectory to break down
        task_name: Task name for lookup
    """
    trajectory_id = args.get("trajectory_id", "")
    task_name = args.get("task_name", "")

    if not trajectory_id:
        return {"error": "trajectory_id is required"}

    try:
        from ..agents.device_testing.trajectory_logger import get_trajectory_logger
        tl = get_trajectory_logger()
        traj = None
        if task_name:
            traj = tl.load_trajectory(task_name, trajectory_id)
        else:
            base = tl._base_dir
            if base.exists():
                for task_dir in base.iterdir():
                    if task_dir.is_dir() and not task_dir.name.startswith("_"):
                        traj = tl.load_trajectory(task_dir.name, trajectory_id)
                        if traj:
                            task_name = task_dir.name
                            break

        if not traj:
            return {"error": f"Trajectory {trajectory_id} not found"}
    except Exception as e:
        return {"error": f"Failed to load trajectory: {e}"}

    steps = getattr(traj, "steps", [])
    total_tokens = sum(getattr(s, "tokens_used", 0) for s in steps)
    total_ms = sum(getattr(s, "duration_ms", 0) for s in steps)

    # Categorize steps into stages
    stages = {
        "navigation": {"tokens": 0, "ms": 0, "steps": 0},
        "interaction": {"tokens": 0, "ms": 0, "steps": 0},
        "verification": {"tokens": 0, "ms": 0, "steps": 0},
        "wait": {"tokens": 0, "ms": 0, "steps": 0},
        "other": {"tokens": 0, "ms": 0, "steps": 0},
    }

    for step in steps:
        action = getattr(step, "action", "").lower()
        tokens = getattr(step, "tokens_used", 0)
        ms = getattr(step, "duration_ms", 0)

        if action in ("navigate", "tap", "click", "scroll", "swipe", "launch_app"):
            stage = "navigation"
        elif action in ("type", "fill", "select", "triple_click", "set_text"):
            stage = "interaction"
        elif action in ("assert", "verify", "checkpoint", "check", "screenshot"):
            stage = "verification"
        elif action in ("wait", "sleep", "delay"):
            stage = "wait"
        else:
            stage = "other"

        stages[stage]["tokens"] += tokens
        stages[stage]["ms"] += ms
        stages[stage]["steps"] += 1

    # Add percentages
    for stage_name, data in stages.items():
        data["tokens_pct"] = round(data["tokens"] / total_tokens * 100, 1) if total_tokens > 0 else 0
        data["time_pct"] = round(data["ms"] / total_ms * 100, 1) if total_ms > 0 else 0

    return {
        "tool": "retention.savings.breakdown",
        "status": "ok",
        "trajectory_id": trajectory_id,
        "task_name": task_name,
        "total_tokens": total_tokens,
        "total_duration_ms": total_ms,
        "total_steps": len(steps),
        "stages": stages,
        "most_expensive_stage": max(stages, key=lambda s: stages[s]["tokens"]),
        "generated_at": _now_iso(),
    }
