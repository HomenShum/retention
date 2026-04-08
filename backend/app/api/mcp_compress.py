"""Workflow compression MCP tools.

Exposes the existing workflow_compression.py module as user-facing MCP tools
for step elimination, shortcut generation, and compression stats.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_COMPRESS_DIR = Path(__file__).resolve().parents[3] / "data" / "compression_results"


def _ensure_dir():
    _COMPRESS_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dispatch_compress(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle ta.compress.* tools."""
    _ensure_dir()

    if tool == "ta.compress.workflow":
        return _handle_compress(args)
    if tool == "ta.compress.list":
        return _handle_list(args)
    if tool == "ta.compress.stats":
        return _handle_stats(args)
    if tool == "ta.compress.rollback":
        return _handle_rollback(args)

    return {"error": f"Unknown compression tool: {tool}"}


def _handle_compress(args: Dict[str, Any]) -> Dict[str, Any]:
    """Compress a workflow trajectory by removing redundant steps.

    Uses the existing workflow_compression module to analyze trajectories
    and propose optimized paths.

    Args:
        trajectory_id: Trajectory to compress
        task_name: Task name for lookup
        strategy: Compression strategy (auto, step_elimination, shortcut_generation)
    """
    trajectory_id = args.get("trajectory_id", "")
    task_name = args.get("task_name", "")
    strategy = args.get("strategy", "auto")

    if not trajectory_id:
        return {"error": "trajectory_id is required"}

    # Load trajectory
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
    total_steps = len(steps)
    total_tokens = sum(getattr(s, "tokens_used", 0) for s in steps)

    # Try to use the actual compression module
    try:
        from ..agents.qa_pipeline.workflow_compression import compress_trajectory
        from dataclasses import asdict

        result = compress_trajectory(traj)
        compressed_steps = result.compressed_steps if hasattr(result, "compressed_steps") else total_steps
        shortcuts = [asdict(s) for s in result.shortcuts] if hasattr(result, "shortcuts") else []

        compression_result = {
            "trajectory_id": trajectory_id,
            "task_name": task_name,
            "strategy": strategy,
            "original_steps": total_steps,
            "compressed_steps": compressed_steps,
            "compression_ratio": round(compressed_steps / total_steps, 3) if total_steps > 0 else 1.0,
            "steps_eliminated": total_steps - compressed_steps,
            "original_tokens": total_tokens,
            "estimated_compressed_tokens": int(total_tokens * (compressed_steps / total_steps)) if total_steps > 0 else total_tokens,
            "token_savings_pct": round((1 - compressed_steps / total_steps) * 100, 1) if total_steps > 0 else 0,
            "shortcuts_generated": len(shortcuts),
            "shortcuts": shortcuts[:5],  # Limit to first 5
            "confidence": getattr(result, "confidence", 0.8),
            "audit_status": "pending",
            "compressed_at": _now_iso(),
        }
    except Exception as e:
        logger.warning("Compression module not available, using heuristic: %s", e)

        # Heuristic compression: identify duplicate consecutive states
        compressed_steps = total_steps
        eliminated = []
        for i, step in enumerate(steps):
            if i > 0:
                prev = steps[i - 1]
                if (getattr(step, "screen_after", "") == getattr(prev, "screen_after", "") and
                        getattr(step, "screen_after", "") != ""):
                    compressed_steps -= 1
                    eliminated.append(f"s{i+1}")

        compression_result = {
            "trajectory_id": trajectory_id,
            "task_name": task_name,
            "strategy": "heuristic_dedup",
            "original_steps": total_steps,
            "compressed_steps": compressed_steps,
            "compression_ratio": round(compressed_steps / total_steps, 3) if total_steps > 0 else 1.0,
            "steps_eliminated": len(eliminated),
            "eliminated_step_ids": eliminated,
            "original_tokens": total_tokens,
            "estimated_compressed_tokens": int(total_tokens * (compressed_steps / total_steps)) if total_steps > 0 else total_tokens,
            "token_savings_pct": round((1 - compressed_steps / total_steps) * 100, 1) if total_steps > 0 else 0,
            "confidence": 0.7,
            "audit_status": "pending",
            "compressed_at": _now_iso(),
        }

    # Persist result
    result_path = _COMPRESS_DIR / f"compress_{trajectory_id}_{_now_iso().replace(':', '-')}.json"
    result_path.write_text(json.dumps(compression_result, indent=2))

    return {"tool": "ta.compress.workflow", "status": "ok", **compression_result}


def _handle_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List all compression results."""
    results = []
    if _COMPRESS_DIR.exists():
        for f in sorted(_COMPRESS_DIR.iterdir(), reverse=True):
            if f.suffix == ".json":
                try:
                    data = json.loads(f.read_text())
                    results.append({
                        "trajectory_id": data.get("trajectory_id", ""),
                        "task_name": data.get("task_name", ""),
                        "original_steps": data.get("original_steps", 0),
                        "compressed_steps": data.get("compressed_steps", 0),
                        "token_savings_pct": data.get("token_savings_pct", 0),
                        "compressed_at": data.get("compressed_at", ""),
                    })
                except Exception:
                    pass

    return {"tool": "ta.compress.list", "status": "ok", "compressions": results, "total": len(results)}


def _handle_stats(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get aggregate compression statistics.

    Args:
        task_name: Filter stats by task name (optional)
    """
    task_filter = args.get("task_name", "")
    results = []

    if _COMPRESS_DIR.exists():
        for f in _COMPRESS_DIR.iterdir():
            if f.suffix == ".json":
                try:
                    data = json.loads(f.read_text())
                    if task_filter and data.get("task_name", "") != task_filter:
                        continue
                    results.append(data)
                except Exception:
                    pass

    if not results:
        return {"tool": "ta.compress.stats", "status": "ok", "message": "No compression results found", "total": 0}

    avg_savings = sum(r.get("token_savings_pct", 0) for r in results) / len(results)
    total_steps_saved = sum(r.get("steps_eliminated", 0) for r in results)
    avg_ratio = sum(r.get("compression_ratio", 1) for r in results) / len(results)

    return {
        "tool": "ta.compress.stats",
        "status": "ok",
        "total_compressions": len(results),
        "avg_token_savings_pct": round(avg_savings, 1),
        "total_steps_saved": total_steps_saved,
        "avg_compression_ratio": round(avg_ratio, 3),
        "task_names": list(set(r.get("task_name", "") for r in results)),
    }


def _handle_rollback(args: Dict[str, Any]) -> Dict[str, Any]:
    """Rollback to uncompressed trajectory.

    Args:
        trajectory_id: Trajectory to rollback
    """
    trajectory_id = args.get("trajectory_id", "")
    if not trajectory_id:
        return {"error": "trajectory_id is required"}

    return {
        "tool": "ta.compress.rollback",
        "status": "ok",
        "trajectory_id": trajectory_id,
        "message": "Rollback support is available — the original uncompressed trajectory is always preserved. Use ta.trajectory.replay with the original trajectory_id.",
    }
