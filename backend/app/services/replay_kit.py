"""
TA Replay Kit — the free/open adoption product layer.

User flow:
  1. CAPTURE: Run a frontier workflow → TA records trajectory + tool calls
  2. EXTRACT: Auto-extract scaffold (ROP manifest) from trajectory
  3. REPLAY: Rerun cheaper with suggest_next() advisory + checkpoint validation
  4. COMPARE: Three-pane view (frontier / replay / judge verdict)
  5. ESCALATE: Issue escalation when replay diverges beyond threshold

This is the habit product — what developers use daily.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


# ─── Replay Kit types ────────────────────────────────────────────────────

@dataclass
class CaptureResult:
    """Result of capturing a frontier workflow."""
    trajectory_id: str
    workflow_family: str
    total_steps: int
    total_tokens: int
    total_cost_usd: float
    total_time_s: float
    scaffold_extracted: bool = False
    rop_id: str = ""
    captured_at: str = ""


@dataclass
class ReplayResult:
    """Result of replaying a captured workflow."""
    replay_id: str
    trajectory_id: str
    success: bool
    steps_matched: int
    steps_drifted: int
    tokens_used: int
    cost_usd: float
    time_s: float
    escalation_triggered: bool = False
    escalation_reason: str = ""
    replayed_at: str = ""


@dataclass
class CompareResult:
    """Three-pane compare: frontier vs replay vs judge."""
    # Frontier
    frontier_tokens: int = 0
    frontier_cost_usd: float = 0.0
    frontier_time_s: float = 0.0

    # Replay
    replay_tokens: int = 0
    replay_cost_usd: float = 0.0
    replay_time_s: float = 0.0

    # Delta
    token_savings_pct: float = 0.0
    cost_savings_pct: float = 0.0
    time_savings_pct: float = 0.0

    # Judge
    verdict: str = ""  # "acceptable" | "needs_escalation" | "failed"
    confidence: float = 0.0
    grade: str = ""
    limitations: list[str] = field(default_factory=list)


@dataclass
class EscalationEvent:
    """Record of when replay was escalated to frontier."""
    step_index: int
    action: str
    reason: str  # "drift_threshold" | "checkpoint_failure" | "consecutive_drifts"
    severity: str  # "mild" | "significant" | "critical"
    escalated_to: str = ""  # model name
    resolved: bool = False


# ─── Replay Kit service ──────────────────────────────────────────────────

class ReplayKit:
    """The TA Replay Kit product — capture, extract, replay, compare, escalate."""

    def __init__(self):
        self._captures_dir = _DATA_DIR / "replay_kit" / "captures"
        self._replays_dir = _DATA_DIR / "replay_kit" / "replays"
        self._captures_dir.mkdir(parents=True, exist_ok=True)
        self._replays_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 1: CAPTURE ──────────────────────────────────────────────

    def record_capture(
        self,
        trajectory_id: str,
        workflow_family: str,
        total_steps: int,
        total_tokens: int,
        total_cost_usd: float,
        total_time_s: float,
    ) -> CaptureResult:
        """Record a frontier workflow capture."""
        result = CaptureResult(
            trajectory_id=trajectory_id,
            workflow_family=workflow_family,
            total_steps=total_steps,
            total_tokens=total_tokens,
            total_cost_usd=total_cost_usd,
            total_time_s=total_time_s,
            captured_at=datetime.now(timezone.utc).isoformat(),
        )

        # Auto-extract scaffold
        rop_id = self._extract_scaffold(trajectory_id, workflow_family)
        if rop_id:
            result.scaffold_extracted = True
            result.rop_id = rop_id

        # Persist
        path = self._captures_dir / f"{trajectory_id}.json"
        path.write_text(json.dumps(asdict(result), indent=2))

        logger.info(
            f"[ReplayKit] Captured {workflow_family}: {total_steps} steps, "
            f"${total_cost_usd:.4f}, scaffold={'yes' if rop_id else 'no'}"
        )
        return result

    # ── Step 2: EXTRACT ──────────────────────────────────────────────

    def _extract_scaffold(self, trajectory_id: str, workflow_family: str) -> str:
        """Auto-extract a scaffold (ROP manifest) from a trajectory.

        Delegates to the dream engine's promotion logic.
        """
        try:
            from .rop_dream_engine import _promote_to_rop, _scan_existing_rops
            health = {
                "trajectory_id": trajectory_id,
                "workflow": workflow_family,
                "surface": "web",
                "replay_count": 1,  # first capture counts as 1
                "success_rate": 1.0,
                "avg_divergence": 0.0,
                "avg_token_savings": 0.0,
                "total_steps": 0,
                "_path": "",
            }
            existing = _scan_existing_rops()
            return _promote_to_rop(trajectory_id, health, existing) or ""
        except Exception as e:
            logger.debug(f"Scaffold extraction skipped: {e}")
            return ""

    # ── Step 3: REPLAY ───────────────────────────────────────────────

    def build_replay_plan(self, trajectory_id: str) -> dict[str, Any]:
        """Build a replay plan from a captured trajectory.

        Returns the plan (not execution) — caller decides when to run.
        """
        # Find the trajectory
        traj = self._find_trajectory(trajectory_id)
        if not traj:
            return {"error": f"Trajectory '{trajectory_id}' not found"}

        steps = traj.get("steps", [])
        return {
            "trajectory_id": trajectory_id,
            "workflow": traj.get("task_name", ""),
            "total_steps": len(steps),
            "steps_preview": [
                {"index": s.get("step_index", i), "action": s.get("action", "")[:80]}
                for i, s in enumerate(steps[:10])
            ],
            "has_mcp_tool_calls": any(s.get("mcp_tool_calls") for s in steps),
            "suggest_next_available": True,
            "escalation_policy": {
                "max_drift": 0.4,
                "max_consecutive_drifts": 3,
                "escalation_model": "frontier",
            },
        }

    def record_replay(self, replay_data: dict[str, Any]) -> ReplayResult:
        """Record a replay result."""
        result = ReplayResult(
            replay_id=replay_data.get("run_id", ""),
            trajectory_id=replay_data.get("trajectory_id", ""),
            success=replay_data.get("success", False),
            steps_matched=replay_data.get("steps_matched", 0),
            steps_drifted=replay_data.get("steps_drifted", 0),
            tokens_used=replay_data.get("token_usage", {}).get("estimated_replay_tokens", 0),
            cost_usd=0.0,
            time_s=replay_data.get("time_seconds", 0),
            escalation_triggered=replay_data.get("fallback_to_exploration", False),
            replayed_at=datetime.now(timezone.utc).isoformat(),
        )

        path = self._replays_dir / f"{result.replay_id}.json"
        path.write_text(json.dumps(asdict(result), indent=2))

        return result

    # ── Step 4: COMPARE ──────────────────────────────────────────────

    def compare(self, trajectory_id: str, replay_id: str = "") -> CompareResult:
        """Build a three-pane compare view."""
        # Load capture
        capture_path = self._captures_dir / f"{trajectory_id}.json"
        capture = {}
        if capture_path.exists():
            capture = json.loads(capture_path.read_text())

        # Load replay (latest if no specific ID)
        replay = {}
        if replay_id:
            replay_path = self._replays_dir / f"{replay_id}.json"
            if replay_path.exists():
                replay = json.loads(replay_path.read_text())
        else:
            # Find latest replay for this trajectory
            for f in sorted(self._replays_dir.glob("*.json"), reverse=True):
                try:
                    r = json.loads(f.read_text())
                    if r.get("trajectory_id") == trajectory_id:
                        replay = r
                        break
                except (json.JSONDecodeError, OSError):
                    continue

        frontier_tokens = capture.get("total_tokens", 0)
        frontier_cost = capture.get("total_cost_usd", 0)
        frontier_time = capture.get("total_time_s", 0)
        replay_tokens = replay.get("tokens_used", 0)
        replay_cost = replay.get("cost_usd", 0)
        replay_time = replay.get("time_s", 0)

        result = CompareResult(
            frontier_tokens=frontier_tokens,
            frontier_cost_usd=frontier_cost,
            frontier_time_s=frontier_time,
            replay_tokens=replay_tokens,
            replay_cost_usd=replay_cost,
            replay_time_s=replay_time,
        )

        if frontier_tokens > 0:
            result.token_savings_pct = round((frontier_tokens - replay_tokens) / frontier_tokens * 100, 1)
        if frontier_cost > 0:
            result.cost_savings_pct = round((frontier_cost - replay_cost) / frontier_cost * 100, 1)
        if frontier_time > 0:
            result.time_savings_pct = round((frontier_time - replay_time) / frontier_time * 100, 1)

        # Judge verdict
        if replay.get("success"):
            result.verdict = "acceptable"
            result.confidence = 0.85
            result.grade = "B"
        elif replay.get("escalation_triggered"):
            result.verdict = "needs_escalation"
            result.confidence = 0.5
            result.grade = "D"
            result.limitations = ["Replay drifted beyond threshold — escalation was triggered"]
        else:
            result.verdict = "failed"
            result.confidence = 0.3
            result.grade = "F"

        return result

    # ── Step 5: ESCALATE ─────────────────────────────────────────────

    def build_escalation_event(
        self, step_index: int, action: str, reason: str, severity: str
    ) -> EscalationEvent:
        """Create an escalation event when replay should stop or upgrade."""
        return EscalationEvent(
            step_index=step_index,
            action=action,
            reason=reason,
            severity=severity,
        )

    # ── Kit stats ────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Overall Replay Kit stats."""
        captures = list(self._captures_dir.glob("*.json"))
        replays = list(self._replays_dir.glob("*.json"))

        total_saved_usd = 0.0
        for f in replays:
            try:
                r = json.loads(f.read_text())
                # Find matching capture
                tid = r.get("trajectory_id", "")
                cap_path = self._captures_dir / f"{tid}.json"
                if cap_path.exists():
                    cap = json.loads(cap_path.read_text())
                    total_saved_usd += max(0, cap.get("total_cost_usd", 0) - r.get("cost_usd", 0))
            except (json.JSONDecodeError, OSError):
                continue

        return {
            "total_captures": len(captures),
            "total_replays": len(replays),
            "total_cost_saved_usd": round(total_saved_usd, 4),
            "workflows_covered": len(set(
                json.loads(f.read_text()).get("workflow_family", "")
                for f in captures
                if f.exists()
            )),
        }

    # ── Internal helpers ─────────────────────────────────────────────

    def _find_trajectory(self, trajectory_id: str) -> Optional[dict]:
        """Find a trajectory by ID across all task dirs."""
        traj_dir = _DATA_DIR / "trajectories"
        if not traj_dir.exists():
            return None
        for task_dir in traj_dir.iterdir():
            if not task_dir.is_dir():
                continue
            for f in task_dir.glob("*.json"):
                try:
                    t = json.loads(f.read_text())
                    if t.get("trajectory_id") == trajectory_id:
                        return t
                except (json.JSONDecodeError, OSError):
                    continue
        return None


# ─── Module singleton ────────────────────────────────────────────────────

_kit: Optional[ReplayKit] = None


def get_replay_kit() -> ReplayKit:
    global _kit
    if _kit is None:
        _kit = ReplayKit()
    return _kit
