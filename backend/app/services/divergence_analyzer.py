"""Divergence Analyzer — aggregate replay results to surface trajectory health.

Reads replay_results/*.json to compute:
- Per-trajectory divergence rate, fallback rate, avg savings
- Per-step instability (which steps drift most often)
- Confidence scores based on replay history
- Trajectory health grades (A/B/C/D/F)
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_REPLAY_DIR = _DATA_DIR / "replay_results"
_TRAJECTORY_DIR = _DATA_DIR / "trajectories"

# Cache
_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 60


# ─── Data types ──────────────────────────────────────────────────────────

@dataclass
class StepDivergence:
    """Per-step divergence stats across all replays of a trajectory."""
    step_index: int
    action_label: str
    total_replays: int
    times_drifted: int
    drift_rate: float  # times_drifted / total_replays
    drift_reasons: list[str]  # observed reasons
    is_unstable: bool  # drift_rate > 0.3


@dataclass
class TrajectoryHealth:
    """Aggregate health metrics for a single trajectory."""
    trajectory_id: str
    workflow: str
    total_replays: int
    successful_replays: int  # no fallback
    fallback_replays: int
    clean_replays: int  # zero drift
    avg_drift_score: float
    avg_token_savings: float
    avg_time_savings: float
    fallback_rate: float  # fallback_replays / total_replays
    success_rate: float  # successful_replays / total_replays
    health_grade: str  # A/B/C/D/F
    confidence_score: float  # 0-1, for guided replay decisions
    unstable_steps: list[StepDivergence]
    total_steps: int
    surface: str
    last_replay_at: str


# ─── Analysis ────────────────────────────────────────────────────────────

def analyze_divergence() -> dict[str, Any]:
    """Aggregate all replay results into divergence insights."""
    now = time.time()
    if "all" in _cache and _cache["all"][0] > now:
        return _cache["all"][1]

    replay_files = list(_REPLAY_DIR.glob("*.json")) if _REPLAY_DIR.exists() else []
    if not replay_files:
        result = _demo_divergence()
        _cache["all"] = (now + _CACHE_TTL, result)
        return result

    # Load all replays
    replays: list[dict] = []
    for f in replay_files:
        try:
            replays.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue

    # Group by trajectory
    by_traj: dict[str, list[dict]] = defaultdict(list)
    for r in replays:
        tid = r.get("trajectory_id", "unknown")
        by_traj[tid].append(r)

    # Load trajectory metadata for step labels
    traj_meta: dict[str, dict] = {}
    if _TRAJECTORY_DIR.exists():
        for task_dir in _TRAJECTORY_DIR.iterdir():
            if not task_dir.is_dir():
                continue
            for f in task_dir.glob("*.json"):
                try:
                    t = json.loads(f.read_text())
                    traj_meta[t.get("trajectory_id", "")] = t
                except (json.JSONDecodeError, OSError):
                    continue

    # Compute per-trajectory health
    trajectories: list[TrajectoryHealth] = []
    all_unstable_steps: list[dict] = []

    for tid, runs in by_traj.items():
        total = len(runs)
        fallbacks = sum(1 for r in runs if r.get("fallback_to_exploration"))
        clean = sum(1 for r in runs if r.get("steps_drifted", 0) == 0 and not r.get("fallback_to_exploration"))
        successful = total - fallbacks

        avg_drift = sum(r.get("drift_score", 0) for r in runs) / max(total, 1)
        avg_tokens = sum(
            r.get("comparison_with_full", {}).get("token_savings_pct", 0) for r in runs
        ) / max(total, 1)
        avg_time = sum(
            r.get("comparison_with_full", {}).get("time_savings_pct", 0) for r in runs
        ) / max(total, 1)

        fallback_rate = fallbacks / max(total, 1)
        success_rate = successful / max(total, 1)

        # Health grade
        if success_rate >= 0.9 and avg_drift < 0.1:
            grade = "A"
        elif success_rate >= 0.75 and avg_drift < 0.2:
            grade = "B"
        elif success_rate >= 0.5 and avg_drift < 0.35:
            grade = "C"
        elif success_rate >= 0.25:
            grade = "D"
        else:
            grade = "F"

        # Confidence score for guided replay
        freshness = 1.0  # TODO: decay based on last_replay_at
        confidence = (
            0.4 * (1.0 - avg_drift) +
            0.3 * success_rate +
            0.2 * (1.0 if total >= 5 else total / 5) +  # more replays = more confidence
            0.1 * freshness
        )

        # Per-step divergence analysis
        meta = traj_meta.get(tid, {})
        steps = meta.get("steps", [])
        total_steps = max(
            len(steps),
            max((r.get("total_steps", 0) for r in runs), default=0),
        )

        step_divergences: list[StepDivergence] = []
        for step_idx in range(total_steps):
            step_label = steps[step_idx].get("action", f"step_{step_idx}")[:60] if step_idx < len(steps) else f"step_{step_idx}"

            # Count how many replays drifted AT or BEFORE this step
            times_drifted = 0
            reasons: list[str] = []
            for r in runs:
                dp = r.get("drift_point")
                if dp is not None and dp == step_idx:
                    times_drifted += 1
                    reasons.append("drift_at_step")
                elif r.get("steps_drifted", 0) > 0 and dp is not None and dp <= step_idx:
                    # This run had drift that affected this step
                    pass  # Only count the exact drift point

            drift_rate = times_drifted / max(total, 1)
            is_unstable = drift_rate > 0.3

            sd = StepDivergence(
                step_index=step_idx,
                action_label=step_label,
                total_replays=total,
                times_drifted=times_drifted,
                drift_rate=round(drift_rate, 3),
                drift_reasons=reasons[:5],
                is_unstable=is_unstable,
            )
            step_divergences.append(sd)
            if is_unstable:
                all_unstable_steps.append({
                    "trajectory_id": tid,
                    "workflow": runs[0].get("workflow", ""),
                    "step_index": step_idx,
                    "action": step_label,
                    "drift_rate": round(drift_rate, 3),
                })

        workflow = runs[0].get("workflow", "")
        surface = meta.get("surface", "unknown")
        last_ts = max((r.get("timestamp", "") for r in runs), default="")

        trajectories.append(TrajectoryHealth(
            trajectory_id=tid,
            workflow=workflow,
            total_replays=total,
            successful_replays=successful,
            fallback_replays=fallbacks,
            clean_replays=clean,
            avg_drift_score=round(avg_drift, 3),
            avg_token_savings=round(avg_tokens, 1),
            avg_time_savings=round(avg_time, 1),
            fallback_rate=round(fallback_rate, 3),
            success_rate=round(success_rate, 3),
            health_grade=grade,
            confidence_score=round(confidence, 3),
            unstable_steps=[s for s in step_divergences if s.is_unstable],
            total_steps=total_steps,
            surface=surface,
            last_replay_at=last_ts,
        ))

    # Sort by health (worst first for attention)
    trajectories.sort(key=lambda t: t.confidence_score)

    # Aggregate stats
    total_replays = sum(t.total_replays for t in trajectories)
    total_fallbacks = sum(t.fallback_replays for t in trajectories)
    total_clean = sum(t.clean_replays for t in trajectories)
    avg_savings = sum(t.avg_token_savings * t.total_replays for t in trajectories) / max(total_replays, 1)

    result = {
        "trajectories": [_health_to_dict(t) for t in trajectories],
        "totals": {
            "total_trajectories": len(trajectories),
            "total_replays": total_replays,
            "total_fallbacks": total_fallbacks,
            "total_clean": total_clean,
            "overall_success_rate": round((total_replays - total_fallbacks) / max(total_replays, 1), 3),
            "avg_token_savings": round(avg_savings, 1),
        },
        "unstable_steps": all_unstable_steps,
        "is_demo": False,
    }

    _cache["all"] = (now + _CACHE_TTL, result)
    return result


def _health_to_dict(h: TrajectoryHealth) -> dict:
    return {
        "trajectory_id": h.trajectory_id,
        "workflow": h.workflow,
        "total_replays": h.total_replays,
        "successful_replays": h.successful_replays,
        "fallback_replays": h.fallback_replays,
        "clean_replays": h.clean_replays,
        "avg_drift_score": h.avg_drift_score,
        "avg_token_savings": h.avg_token_savings,
        "avg_time_savings": h.avg_time_savings,
        "fallback_rate": h.fallback_rate,
        "success_rate": h.success_rate,
        "health_grade": h.health_grade,
        "confidence_score": h.confidence_score,
        "total_steps": h.total_steps,
        "surface": h.surface,
        "last_replay_at": h.last_replay_at,
        "unstable_steps": [
            {
                "step_index": s.step_index,
                "action": s.action_label,
                "drift_rate": s.drift_rate,
                "times_drifted": s.times_drifted,
                "total_replays": s.total_replays,
            }
            for s in h.unstable_steps
        ],
    }


# ─── Demo fallback ───────────────────────────────────────────────────────

def _demo_divergence() -> dict:
    """Realistic demo data when no replay results exist."""
    return {
        "trajectories": [
            {
                "trajectory_id": "traj_gov_alice_001",
                "workflow": "gov_data_retrieval",
                "total_replays": 10,
                "successful_replays": 8,
                "fallback_replays": 2,
                "clean_replays": 7,
                "avg_drift_score": 0.05,
                "avg_token_savings": 81.0,
                "avg_time_savings": 68.0,
                "fallback_rate": 0.2,
                "success_rate": 0.8,
                "health_grade": "B",
                "confidence_score": 0.78,
                "total_steps": 10,
                "surface": "web",
                "last_replay_at": "2026-04-01T19:00:00Z",
                "unstable_steps": [
                    {"step_index": 3, "action": "Click 'Population Statistics' link", "drift_rate": 0.3, "times_drifted": 3, "total_replays": 10},
                ],
            },
            {
                "trajectory_id": "traj_yt_alice_001",
                "workflow": "youtube_search_claude_updates",
                "total_replays": 10,
                "successful_replays": 7,
                "fallback_replays": 3,
                "clean_replays": 6,
                "avg_drift_score": 0.08,
                "avg_token_savings": 85.0,
                "avg_time_savings": 72.0,
                "fallback_rate": 0.3,
                "success_rate": 0.7,
                "health_grade": "C",
                "confidence_score": 0.65,
                "total_steps": 8,
                "surface": "web",
                "last_replay_at": "2026-04-01T18:30:00Z",
                "unstable_steps": [
                    {"step_index": 2, "action": "Click search result #1", "drift_rate": 0.4, "times_drifted": 4, "total_replays": 10},
                    {"step_index": 5, "action": "Verify video player loaded", "drift_rate": 0.3, "times_drifted": 3, "total_replays": 10},
                ],
            },
        ],
        "totals": {
            "total_trajectories": 2,
            "total_replays": 20,
            "total_fallbacks": 5,
            "total_clean": 13,
            "overall_success_rate": 0.75,
            "avg_token_savings": 83.0,
        },
        "unstable_steps": [
            {"trajectory_id": "traj_yt_alice_001", "workflow": "youtube_search", "step_index": 2, "action": "Click search result #1", "drift_rate": 0.4},
            {"trajectory_id": "traj_yt_alice_001", "workflow": "youtube_search", "step_index": 5, "action": "Verify video player loaded", "drift_rate": 0.3},
            {"trajectory_id": "traj_gov_alice_001", "workflow": "gov_data_retrieval", "step_index": 3, "action": "Click 'Population Statistics' link", "drift_rate": 0.3},
        ],
        "is_demo": True,
    }
