"""
Persistent Trajectory Logger for Device Testing Agent.

Saves full action trajectories to disk for:
- Ground truth verification (expected vs actual trajectory comparison)
- Regression testing (replay known-good trajectories)
- Training data collection (successful trajectories as demonstrations)

Integrates with SessionMemory to persist session data at session end.
"""

import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# Default storage directory (relative to backend/)
DEFAULT_TRAJECTORIES_DIR = "data/trajectories"


@dataclass
class TrajectoryStep:
    """A single step in a trajectory."""
    step_index: int
    timestamp: str
    action: str
    state_before: Dict[str, Any]
    state_after: Optional[Dict[str, Any]] = None
    success: bool = True
    error: Optional[str] = None
    failure_type: Optional[str] = None
    recovery_strategy: Optional[str] = None
    recovery_successful: Optional[bool] = None
    notes: Optional[str] = None
    # Replay extensions
    semantic_label: Optional[str] = None
    screen_fingerprint_before: Optional[str] = None
    screen_fingerprint_after: Optional[str] = None
    coordinates: Optional[Dict[str, Any]] = None
    duration_ms: Optional[int] = None
    # Exact MCP tool calls for deterministic replay (avoids re-parsing action text)
    # Each entry: {"tool": str, "params": dict}
    mcp_tool_calls: Optional[List[Dict[str, Any]]] = None


@dataclass
class TrajectoryLog:
    """Complete trajectory for a single task execution."""
    trajectory_id: str
    task_name: str
    task_goal: str
    device_id: str
    started_at: str
    completed_at: Optional[str] = None
    steps: List[TrajectoryStep] = field(default_factory=list)
    success: bool = False
    total_actions: int = 0
    total_failures: int = 0
    recovery_success_rate: float = 0.0
    evaluation_score: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Replay extensions
    workflow_family: str = ""
    surface: str = "web"  # "web" | "android" | "desktop"
    drift_score: float = 0.0  # 0.0 = perfect match, 1.0 = fully drifted
    replay_count: int = 0
    last_validated_at: Optional[str] = None
    avg_token_savings: float = 0.0  # rolling average % savings
    avg_time_savings: float = 0.0  # rolling average % savings
    source_run_id: str = ""
    success_conditions: List[str] = field(default_factory=list)
    failure_conditions: List[str] = field(default_factory=list)
    # Actual token/time counts from the SOURCE run — used for correct A/B comparison
    # (same test: full LLM pipeline tokens vs replay tokens)
    source_tokens_actual: int = 0
    source_time_actual_s: float = 0.0
    # Git linkage — ties trajectory to code version
    source_git_commit: str = ""  # SHA of the commit when trajectory was recorded
    source_git_branch: str = ""  # branch name
    source_git_dirty: bool = False  # True if working tree had uncommitted changes


@dataclass
class RollupSummary:
    """Pre-computed aggregate for a time period — canonical truth object."""
    period: str  # "daily" | "weekly" | "monthly" | "quarterly" | "yearly"
    period_key: str  # "2026-03-29" | "2026-W13" | "2026-03" | "2026-Q1" | "2026"
    workflow_family: str  # "" = global aggregate
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    total_tokens_used: int = 0
    total_tokens_saved: int = 0
    total_time_s: float = 0.0
    total_time_saved_s: float = 0.0
    avg_drift_score: float = 0.0
    replay_count: int = 0
    trajectory_count: int = 0
    durability_score: float = 0.0  # 0-100
    created_at: str = ""


@dataclass
class ExecutionPacket:
    """Runtime handoff object — canonical truth object.

    A Packet encapsulates everything needed to execute a workflow
    on any runtime (Claude Code, OpenClaw, custom SDK, device farm).
    It separates WHAT to do from HOW to do it.
    """
    packet_id: str
    workflow_id: str
    run_mode: str  # "replay" | "explore" | "replay_with_fallback" | "checkpoint_only"
    surface: str  # "browser" | "android" | "desktop" | "hybrid"
    target_url: str = ""
    target_app: str = ""
    trajectory_id: Optional[str] = None
    success_criteria: List[str] = field(default_factory=list)
    failure_criteria: List[str] = field(default_factory=list)
    memory_context: Dict[str, Any] = field(default_factory=dict)
    # memory_context: { prior_runs, drift_points, preferred_entry_path, known_blockers }
    budget: Dict[str, Any] = field(default_factory=dict)
    # budget: { max_requests, max_cost_usd, max_duration_s, max_tokens }
    runtime_target: str = "auto"  # "claude_code" | "openclaw" | "custom_sdk" | "auto"
    created_at: str = ""
    created_by: str = ""


@dataclass
class TrajectoryReplayResult:
    """Result of replaying a saved trajectory on device."""
    trajectory_id: str
    replay_run_id: str
    success: bool
    total_steps: int
    steps_executed: int
    steps_matched: int
    steps_drifted: int
    drift_point: Optional[int] = None
    fallback_to_exploration: bool = False
    token_usage: Dict[str, int] = field(default_factory=dict)
    time_seconds: float = 0.0
    comparison_with_full: Optional[Dict[str, Any]] = None
    per_step_results: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class TrajectoryComparison:
    """Result of comparing expected vs actual trajectories."""
    expected_id: str
    actual_id: str
    match_score: float  # 0.0 to 1.0
    total_expected_steps: int
    total_actual_steps: int
    matched_steps: int
    divergence_point: Optional[int] = None  # Step index where trajectories diverge
    extra_steps: int = 0
    missing_steps: int = 0
    mismatched_actions: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""


class TrajectoryLogger:
    """
    Persistent trajectory logger that saves/loads/compares trajectories.

    Storage layout:
        data/trajectories/
            {task_name}/
                {trajectory_id}.json
            _ground_truth/
                {task_name}.json
    """

    def __init__(self, base_dir: Optional[str] = None):
        """
        Initialize the trajectory logger.

        Args:
            base_dir: Base directory for trajectory storage.
                      Defaults to backend/data/trajectories/
        """
        if base_dir:
            self._base_dir = Path(base_dir)
        else:
            # Find backend directory
            current = Path(__file__).parent
            while current.name != "backend" and current.parent != current:
                current = current.parent
            self._base_dir = current / DEFAULT_TRAJECTORIES_DIR

    def save_trajectory(self, trajectory: TrajectoryLog) -> str:
        """
        Save a trajectory to disk.

        Args:
            trajectory: The trajectory to save

        Returns:
            File path where trajectory was saved
        """
        task_dir = self._base_dir / trajectory.task_name
        task_dir.mkdir(parents=True, exist_ok=True)

        file_path = task_dir / f"{trajectory.trajectory_id}.json"
        with open(file_path, "w") as f:
            json.dump(asdict(trajectory), f, indent=2, default=str)

        logger.info(
            f"Saved trajectory {trajectory.trajectory_id} "
            f"({len(trajectory.steps)} steps) to {file_path}"
        )
        return str(file_path)

    def load_trajectory(self, task_name: str, trajectory_id: str) -> Optional[TrajectoryLog]:
        """
        Load a trajectory from disk.

        Args:
            task_name: Name of the task
            trajectory_id: Trajectory identifier

        Returns:
            TrajectoryLog or None if not found
        """
        file_path = self._base_dir / task_name / f"{trajectory_id}.json"
        if not file_path.exists():
            logger.warning(f"Trajectory not found: {file_path}")
            return None

        with open(file_path, "r") as f:
            data = json.load(f)

        # Reconstruct dataclass from dict
        steps = [
            TrajectoryStep(**{k: v for k, v in s.items() if k in TrajectoryStep.__dataclass_fields__})
            for s in data.pop("steps", [])
        ]
        log_fields = {k: v for k, v in data.items() if k in TrajectoryLog.__dataclass_fields__}
        return TrajectoryLog(**log_fields, steps=steps)

    def list_trajectories(self, task_name: str) -> List[str]:
        """List all trajectory IDs for a task."""
        task_dir = self._base_dir / task_name
        if not task_dir.exists():
            return []
        return [f.stem for f in task_dir.glob("*.json")]

    def compare_trajectories(
        self, expected: TrajectoryLog, actual: TrajectoryLog
    ) -> TrajectoryComparison:
        """
        Compare expected vs actual trajectories.

        Compares action sequences step-by-step to find divergence points
        and compute a match score.

        Args:
            expected: The ground truth / expected trajectory
            actual: The actual trajectory from execution

        Returns:
            TrajectoryComparison with match details
        """
        matched = 0
        mismatched = []
        divergence_point = None

        min_len = min(len(expected.steps), len(actual.steps))

        for i in range(min_len):
            exp_action = expected.steps[i].action.lower().strip()
            act_action = actual.steps[i].action.lower().strip()

            if exp_action == act_action:
                matched += 1
            else:
                if divergence_point is None:
                    divergence_point = i
                mismatched.append({
                    "step": i,
                    "expected": expected.steps[i].action,
                    "actual": actual.steps[i].action,
                })

        extra = max(0, len(actual.steps) - len(expected.steps))
        missing = max(0, len(expected.steps) - len(actual.steps))

        total = max(len(expected.steps), len(actual.steps), 1)
        score = matched / total

        summary_parts = [f"Match score: {score:.1%} ({matched}/{total} steps)"]
        if divergence_point is not None:
            summary_parts.append(f"First divergence at step {divergence_point}")
        if extra:
            summary_parts.append(f"{extra} extra steps in actual")
        if missing:
            summary_parts.append(f"{missing} missing steps in actual")

        return TrajectoryComparison(
            expected_id=expected.trajectory_id,
            actual_id=actual.trajectory_id,
            match_score=score,
            total_expected_steps=len(expected.steps),
            total_actual_steps=len(actual.steps),
            matched_steps=matched,
            divergence_point=divergence_point,
            extra_steps=extra,
            missing_steps=missing,
            mismatched_actions=mismatched,
            summary=". ".join(summary_parts),
        )

    def save_ground_truth(self, task_name: str, trajectory: TrajectoryLog) -> str:
        """
        Save a trajectory as ground truth for a task.

        Args:
            task_name: Task name
            trajectory: The reference trajectory

        Returns:
            File path
        """
        gt_dir = self._base_dir / "_ground_truth"
        gt_dir.mkdir(parents=True, exist_ok=True)

        file_path = gt_dir / f"{task_name}.json"
        with open(file_path, "w") as f:
            json.dump(asdict(trajectory), f, indent=2, default=str)

        logger.info(f"Saved ground truth for {task_name} to {file_path}")
        return str(file_path)

    def get_ground_truth(self, task_name: str) -> Optional[TrajectoryLog]:
        """
        Get the ground truth trajectory for a task.

        Args:
            task_name: Task name

        Returns:
            TrajectoryLog or None
        """
        file_path = self._base_dir / "_ground_truth" / f"{task_name}.json"
        if not file_path.exists():
            return None

        with open(file_path, "r") as f:
            data = json.load(f)

        steps = [
            TrajectoryStep(**{k: v for k, v in s.items() if k in TrajectoryStep.__dataclass_fields__})
            for s in data.pop("steps", [])
        ]
        log_fields = {k: v for k, v in data.items() if k in TrajectoryLog.__dataclass_fields__}
        return TrajectoryLog(**log_fields, steps=steps)

    def list_all_trajectories(self) -> List[Dict[str, Any]]:
        """List all trajectories across all tasks with summary metadata."""
        results = []
        if not self._base_dir.exists():
            return results
        for task_dir in self._base_dir.iterdir():
            if not task_dir.is_dir() or task_dir.name.startswith("_"):
                continue
            for f in task_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text())
                    results.append({
                        "trajectory_id": data.get("trajectory_id", f.stem),
                        "task_name": data.get("task_name", task_dir.name),
                        "task_goal": data.get("task_goal", ""),
                        "surface": data.get("surface", "web"),
                        "workflow_family": data.get("workflow_family", ""),
                        "success": data.get("success", False),
                        "total_actions": data.get("total_actions", 0),
                        "replay_count": data.get("replay_count", 0),
                        "drift_score": data.get("drift_score", 0.0),
                        "last_validated_at": data.get("last_validated_at"),
                        "avg_token_savings": data.get("avg_token_savings", 0.0),
                        "avg_time_savings": data.get("avg_time_savings", 0.0),
                        "source_run_id": data.get("source_run_id", ""),
                        "started_at": data.get("started_at", ""),
                        "created_by": data.get("metadata", {}).get("created_by", ""),
                    })
                except Exception:
                    continue
        return sorted(results, key=lambda x: x.get("started_at", ""), reverse=True)

    def update_replay_stats(
        self, task_name: str, trajectory_id: str,
        token_savings: float, time_savings: float, drift_score: float,
    ) -> None:
        """Update trajectory file with replay metrics (rolling average)."""
        traj = self.load_trajectory(task_name, trajectory_id)
        if not traj:
            return
        traj.replay_count += 1
        traj.last_validated_at = datetime.now(timezone.utc).isoformat()
        n = traj.replay_count
        traj.avg_token_savings = ((traj.avg_token_savings * (n - 1)) + token_savings) / n
        traj.avg_time_savings = ((traj.avg_time_savings * (n - 1)) + time_savings) / n
        traj.drift_score = drift_score
        self.save_trajectory(traj)

    @staticmethod
    def from_session(session, task_name: str = "unknown") -> TrajectoryLog:
        """
        Create a TrajectoryLog from a SessionMemory instance.

        Args:
            session: SessionMemory instance
            task_name: Name of the task (for file organization)

        Returns:
            TrajectoryLog populated from session data
        """
        steps = []
        step_idx = 0

        # Convert actions to trajectory steps
        for action in session.actions:
            steps.append(TrajectoryStep(
                step_index=step_idx,
                timestamp=action.timestamp,
                action=action.action,
                state_before=action.state_before,
                state_after=action.state_after,
                success=action.success,
                notes=action.notes,
                mcp_tool_calls=getattr(action, "mcp_tool_calls", None),
            ))
            step_idx += 1

        # Merge failure info into matching steps (by timestamp proximity)
        failure_map = {f.action: f for f in session.failures}
        for step in steps:
            if not step.success and step.action in failure_map:
                f = failure_map[step.action]
                step.error = f.error
                step.failure_type = f.failure_type
                step.recovery_strategy = f.recovery_strategy
                step.recovery_successful = f.recovery_successful

        summary = session.get_summary()

        # Capture git state at trajectory creation time
        git_commit, git_branch, git_dirty = "", "", False
        try:
            import subprocess
            git_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
            ).strip()
            git_branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL, text=True
            ).strip()
            git_dirty = bool(subprocess.check_output(
                ["git", "status", "--porcelain"], stderr=subprocess.DEVNULL, text=True
            ).strip())
        except Exception:
            pass  # Not in a git repo or git not available

        return TrajectoryLog(
            trajectory_id=str(uuid.uuid4())[:12],
            task_name=task_name,
            task_goal=session.task_goal,
            device_id=session.device_id,
            started_at=session.started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
            steps=steps,
            success=summary.get("total_failures", 0) == 0
                or summary.get("recovery_success_rate", 0) > 0.5,
            total_actions=summary.get("total_actions", 0),
            total_failures=summary.get("total_failures", 0),
            recovery_success_rate=summary.get("recovery_success_rate", 0),
            source_git_commit=git_commit,
            source_git_branch=git_branch,
            source_git_dirty=git_dirty,
        )


# Global trajectory logger instance
_trajectory_logger: Optional[TrajectoryLogger] = None


def get_trajectory_logger() -> TrajectoryLogger:
    """Get the global trajectory logger instance."""
    global _trajectory_logger
    if _trajectory_logger is None:
        _trajectory_logger = TrajectoryLogger()
    return _trajectory_logger


__all__ = [
    "TrajectoryStep",
    "TrajectoryLog",
    "TrajectoryReplayResult",
    "TrajectoryComparison",
    "TrajectoryLogger",
    "get_trajectory_logger",
]
