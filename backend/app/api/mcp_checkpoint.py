"""Checkpoint validation MCP tools.

Standalone checkpoint management and verification for trajectories.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_CHECKPOINT_DIR = Path(__file__).resolve().parents[3] / "data" / "checkpoints"


def _ensure_dir():
    _CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dispatch_checkpoint(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle ta.checkpoint.* tools."""
    _ensure_dir()

    if tool == "ta.checkpoint.list":
        return _handle_list(args)
    if tool == "ta.checkpoint.set":
        return _handle_set(args)
    if tool == "ta.checkpoint.verify":
        return _handle_verify(args)
    if tool == "ta.checkpoint.drift_report":
        return _handle_drift_report(args)

    return {"error": f"Unknown checkpoint tool: {tool}"}


def _handle_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List all checkpoints for a trajectory.

    Args:
        trajectory_id: Trajectory to list checkpoints for
        task_name: Task name for lookup
    """
    trajectory_id = args.get("trajectory_id", "")
    task_name = args.get("task_name", "")

    if not trajectory_id:
        # List all saved checkpoints
        checkpoints = []
        if _CHECKPOINT_DIR.exists():
            for f in sorted(_CHECKPOINT_DIR.iterdir(), reverse=True):
                if f.suffix == ".json":
                    try:
                        data = json.loads(f.read_text())
                        checkpoints.append({
                            "checkpoint_id": data.get("checkpoint_id", f.stem),
                            "trajectory_id": data.get("trajectory_id", ""),
                            "step_id": data.get("step_id", ""),
                            "label": data.get("label", ""),
                            "created_at": data.get("created_at", ""),
                        })
                    except Exception:
                        pass
        return {"tool": "ta.checkpoint.list", "status": "ok", "checkpoints": checkpoints, "total": len(checkpoints)}

    # Load trajectory and extract checkpoints
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
                            break

        if not traj:
            return {"error": f"Trajectory {trajectory_id} not found"}

        checkpoints = []
        for i, step in enumerate(getattr(traj, "steps", [])):
            has_assertion = getattr(step, "action", "").lower() in ("assert", "verify", "checkpoint", "check")
            checkpoints.append({
                "checkpoint_id": f"cp_{i+1:02d}",
                "step_id": f"s{i+1}",
                "label": getattr(step, "action", f"Step {i+1}"),
                "state_fingerprint": getattr(step, "screen_after", ""),
                "is_assertion": has_assertion,
                "success": getattr(step, "success", True),
            })

        return {
            "tool": "ta.checkpoint.list",
            "status": "ok",
            "trajectory_id": trajectory_id,
            "checkpoints": checkpoints,
            "total": len(checkpoints),
            "assertions": sum(1 for c in checkpoints if c["is_assertion"]),
        }
    except Exception as e:
        return {"error": f"Failed to load trajectory: {e}"}


def _handle_set(args: Dict[str, Any]) -> Dict[str, Any]:
    """Set a checkpoint at a specific step.

    Args:
        trajectory_id: Trajectory the checkpoint belongs to
        step_id: Step ID where checkpoint is placed
        label: Human-readable checkpoint label
        expected_state: Expected state fingerprint or description
    """
    trajectory_id = args.get("trajectory_id", "")
    step_id = args.get("step_id", "")
    label = args.get("label", "")
    expected_state = args.get("expected_state", "")

    if not trajectory_id or not step_id:
        return {"error": "trajectory_id and step_id are required"}

    checkpoint = {
        "checkpoint_id": f"cp_{trajectory_id}_{step_id}",
        "trajectory_id": trajectory_id,
        "step_id": step_id,
        "label": label or f"Checkpoint at {step_id}",
        "expected_state": expected_state,
        "result": "pending",
        "created_at": _now_iso(),
    }

    cp_path = _CHECKPOINT_DIR / f"{checkpoint['checkpoint_id']}.json"
    cp_path.write_text(json.dumps(checkpoint, indent=2))

    return {"tool": "ta.checkpoint.set", "status": "ok", **checkpoint}


def _handle_verify(args: Dict[str, Any]) -> Dict[str, Any]:
    """Verify a checkpoint against the current device state.

    Args:
        checkpoint_id: Checkpoint to verify
        current_state: Current state fingerprint to compare against
    """
    checkpoint_id = args.get("checkpoint_id", "")
    current_state = args.get("current_state", "")

    if not checkpoint_id:
        return {"error": "checkpoint_id is required"}

    cp_path = _CHECKPOINT_DIR / f"{checkpoint_id}.json"
    if not cp_path.exists():
        return {"error": f"Checkpoint {checkpoint_id} not found"}

    checkpoint = json.loads(cp_path.read_text())
    expected = checkpoint.get("expected_state", "")

    if current_state and expected:
        match = current_state == expected
        drift_score = 0.0 if match else 1.0
    else:
        match = None
        drift_score = None

    checkpoint["result"] = "passed" if match else ("failed" if match is not None else "pending")
    checkpoint["actual_state"] = current_state
    checkpoint["drift_score"] = drift_score
    checkpoint["verified_at"] = _now_iso()

    cp_path.write_text(json.dumps(checkpoint, indent=2))

    return {"tool": "ta.checkpoint.verify", "status": "ok", **checkpoint}


def _handle_drift_report(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate drift report for all checkpoints of a trajectory.

    Args:
        trajectory_id: Trajectory to report on
    """
    trajectory_id = args.get("trajectory_id", "")
    if not trajectory_id:
        return {"error": "trajectory_id is required"}

    checkpoints = []
    if _CHECKPOINT_DIR.exists():
        for f in _CHECKPOINT_DIR.iterdir():
            if f.suffix == ".json":
                try:
                    data = json.loads(f.read_text())
                    if data.get("trajectory_id") == trajectory_id:
                        checkpoints.append(data)
                except Exception:
                    pass

    passed = sum(1 for c in checkpoints if c.get("result") == "passed")
    failed = sum(1 for c in checkpoints if c.get("result") == "failed")
    pending = sum(1 for c in checkpoints if c.get("result") == "pending")

    return {
        "tool": "ta.checkpoint.drift_report",
        "status": "ok",
        "trajectory_id": trajectory_id,
        "total": len(checkpoints),
        "passed": passed,
        "failed": failed,
        "pending": pending,
        "pass_rate": round(passed / len(checkpoints), 2) if checkpoints else 0,
        "checkpoints": checkpoints,
    }
