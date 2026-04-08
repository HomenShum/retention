"""Audit engine MCP tools for shortcut validation.

Validates optimization candidates, checks trajectory drift,
and compares shortcuts against baselines.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_AUDIT_DIR = Path(__file__).resolve().parents[3] / "data" / "audit_results"


def _ensure_dir():
    _AUDIT_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dispatch_audit(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle ta.audit.* tools."""
    _ensure_dir()

    if tool == "ta.audit.validate_shortcut":
        return _handle_validate_shortcut(args)
    if tool == "ta.audit.compare":
        return _handle_compare(args)
    if tool == "ta.audit.drift_report":
        return _handle_drift_report(args)
    if tool == "ta.audit.list":
        return _handle_list(args)

    return {"error": f"Unknown audit tool: {tool}"}


def _handle_validate_shortcut(args: Dict[str, Any]) -> Dict[str, Any]:
    """Validate an optimization candidate against a baseline trajectory.

    Compares the end state, checkpoint results, and cost metrics of a
    proposed shortcut against the baseline full-crawl trajectory.

    Args:
        trajectory_id: The baseline trajectory ID
        candidate_id: The optimization candidate ID
        task_name: Task name for trajectory lookup
        shortcut_steps: List of step IDs that form the shortcut
    """
    trajectory_id = args.get("trajectory_id", "")
    candidate_id = args.get("candidate_id", "")
    task_name = args.get("task_name", "")

    if not trajectory_id:
        return {"error": "trajectory_id is required"}
    if not candidate_id:
        return {"error": "candidate_id is required"}

    # Load baseline trajectory
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
            return {"error": f"Baseline trajectory {trajectory_id} not found"}
    except Exception as e:
        return {"error": f"Failed to load trajectory: {e}"}

    # Analyze shortcut
    total_steps = len(getattr(traj, "steps", []))
    shortcut_steps = args.get("shortcut_steps", [])
    steps_removed = total_steps - len(shortcut_steps) if shortcut_steps else 0

    # Compute expected savings
    total_tokens = sum(getattr(s, "tokens_used", 0) for s in getattr(traj, "steps", []))
    shortcut_tokens = 0
    if shortcut_steps:
        for i, step in enumerate(getattr(traj, "steps", [])):
            if f"s{i+1}" in shortcut_steps:
                shortcut_tokens += getattr(step, "tokens_used", 0)

    token_savings_pct = ((total_tokens - shortcut_tokens) / total_tokens * 100) if total_tokens > 0 else 0

    # Build audit result
    audit_result = {
        "audit_id": f"audit_{candidate_id}_{_now_iso().replace(':', '-')}",
        "candidate_id": candidate_id,
        "trajectory_id": trajectory_id,
        "task_name": task_name,
        "baseline_steps": total_steps,
        "shortcut_steps": len(shortcut_steps) if shortcut_steps else total_steps,
        "steps_removed": steps_removed,
        "baseline_tokens": total_tokens,
        "shortcut_tokens": shortcut_tokens if shortcut_steps else total_tokens,
        "expected_savings": {
            "tokens_pct": round(token_savings_pct, 1),
            "steps_pct": round(steps_removed / total_steps * 100, 1) if total_steps > 0 else 0,
        },
        "verdict": "pending",
        "audit_checks": {
            "end_state_match": "pending",
            "checkpoints_pass": "pending",
            "no_side_effects": "pending",
            "cost_within_bounds": "pending",
        },
        "risk_assessment": "low_risk" if token_savings_pct < 30 else ("medium_risk" if token_savings_pct < 60 else "high_risk"),
        "notes": "Audit generated from trajectory analysis. Run the shortcut on a live device to verify end state match.",
        "created_at": _now_iso(),
    }

    # Persist audit result
    audit_path = _AUDIT_DIR / f"{audit_result['audit_id']}.json"
    audit_path.write_text(json.dumps(audit_result, indent=2))

    return {"tool": "ta.audit.validate_shortcut", "status": "ok", **audit_result}


def _handle_compare(args: Dict[str, Any]) -> Dict[str, Any]:
    """Compare a shortcut run against a baseline run.

    Args:
        baseline_run_id: The original full-crawl run ID
        shortcut_run_id: The shortcut/optimized run ID
        package_id: TCWP package ID (alternative lookup)
    """
    from .mcp_tcwp import _TCWP_DIR

    pkg_id = args.get("package_id", "")
    baseline_id = args.get("baseline_run_id", "")
    shortcut_id = args.get("shortcut_run_id", "")

    if pkg_id:
        bundle_dir = _TCWP_DIR / pkg_id
        if not bundle_dir.exists():
            return {"error": f"TCWP bundle {pkg_id} not found"}

        run_path = bundle_dir / "run.json"
        if run_path.exists():
            run = json.loads(run_path.read_text())
            traj_path = bundle_dir / "trajectory.json"
            traj = json.loads(traj_path.read_text()) if traj_path.exists() else {}

            return {
                "tool": "ta.audit.compare",
                "status": "ok",
                "package_id": pkg_id,
                "run": run,
                "trajectory_replay_stats": traj.get("replay_stats", {}),
                "compression_history": traj.get("compression_history", []),
            }

    if not baseline_id or not shortcut_id:
        return {"error": "baseline_run_id and shortcut_run_id required (or package_id)"}

    # Compare from run logs
    from .mcp_pipeline import _persisted_results
    baseline = _persisted_results.get(baseline_id, {})
    shortcut = _persisted_results.get(shortcut_id, {})

    if not baseline:
        return {"error": f"Baseline run {baseline_id} not found in run history"}
    if not shortcut:
        return {"error": f"Shortcut run {shortcut_id} not found in run history"}

    return {
        "tool": "ta.audit.compare",
        "status": "ok",
        "baseline": {"run_id": baseline_id, **baseline},
        "shortcut": {"run_id": shortcut_id, **shortcut},
    }


def _handle_drift_report(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a drift report for a trajectory.

    Args:
        trajectory_id: Trajectory to check drift for
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

    drift_score = getattr(traj, "drift_score", 0)
    replay_count = getattr(traj, "replay_count", 0)

    # Analyze per-step drift
    step_analysis = []
    for i, step in enumerate(getattr(traj, "steps", [])):
        step_drift = getattr(step, "drift_score", 0) if hasattr(step, "drift_score") else 0
        step_analysis.append({
            "step_id": f"s{i+1}",
            "action": getattr(step, "action", "unknown"),
            "drift_score": step_drift,
            "stable": step_drift < 0.2,
            "needs_attention": step_drift > 0.4,
        })

    health = "healthy" if drift_score < 0.2 else ("degraded" if drift_score < 0.5 else "critical")

    return {
        "tool": "ta.audit.drift_report",
        "status": "ok",
        "trajectory_id": trajectory_id,
        "task_name": task_name,
        "overall_drift_score": drift_score,
        "health": health,
        "replay_count": replay_count,
        "total_steps": len(step_analysis),
        "stable_steps": sum(1 for s in step_analysis if s["stable"]),
        "attention_needed": sum(1 for s in step_analysis if s["needs_attention"]),
        "step_analysis": step_analysis,
        "recommendation": (
            "Trajectory is stable — continue using." if health == "healthy"
            else "Some drift detected — consider re-exploring drifted steps." if health == "degraded"
            else "Significant drift — recommend full re-crawl and trajectory refresh."
        ),
        "generated_at": _now_iso(),
    }


def _handle_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List all audit results."""
    results = []
    if _AUDIT_DIR.exists():
        for f in sorted(_AUDIT_DIR.iterdir(), reverse=True):
            if f.suffix == ".json":
                try:
                    data = json.loads(f.read_text())
                    results.append({
                        "audit_id": data.get("audit_id", f.stem),
                        "candidate_id": data.get("candidate_id", ""),
                        "trajectory_id": data.get("trajectory_id", ""),
                        "verdict": data.get("verdict", "unknown"),
                        "risk_assessment": data.get("risk_assessment", ""),
                        "created_at": data.get("created_at", ""),
                    })
                except Exception:
                    pass

    return {"tool": "ta.audit.list", "status": "ok", "audits": results, "total": len(results)}
