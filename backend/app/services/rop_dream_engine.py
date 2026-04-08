"""
ROP Dream Engine — KAIROS-style memory consolidation for Retained Operation Patterns.

Applies the autoDream pattern from KAIROS to trajectory/ROP data:
  - Runs during idle periods (dual-gate: time + session triggers)
  - Four-phase consolidation: Orient → Gather Signal → Consolidate → Prune
  - Converts raw trajectories into optimized ROP manifests
  - Promotes healthy trajectories, demotes/archives unhealthy ones
  - Runs in isolation (forked-style) — never blocks the pipeline

Key KAIROS mechanisms applied:
  1. Heartbeat loop → periodic suggest_next() readiness check
  2. autoDream 4-phase consolidation → trajectory-to-ROP promotion
  3. Dual-gate trigger → time + run count before consolidation fires
  4. Contradiction resolution → stale trajectories pruned by divergence analyzer
  5. Progressive disclosure pruning → keep ROP index lean
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_DREAM_DIR = _DATA_DIR / "rop_dreams"
_DREAM_DIR.mkdir(parents=True, exist_ok=True)

_STATE_FILE = _DREAM_DIR / "dream_state.json"
_DREAM_LOG = _DREAM_DIR / "dream_log.jsonl"


# ─── Configuration (maps to KAIROS GrowthBook feature flags) ────────────

@dataclass
class DreamConfig:
    """Dual-gate trigger configuration."""
    min_hours_since_last: float = 24.0  # Time gate: hours since last consolidation
    min_runs_since_last: int = 5        # Session gate: pipeline runs since last consolidation
    max_consolidation_time_s: float = 300.0  # Safety timeout (5 min)
    prune_stale_days: int = 30          # Remove trajectories older than N days with no replays
    promote_min_replays: int = 3        # Min replays before promoting to ROP
    promote_min_success_rate: float = 0.7  # Min success rate for promotion
    demote_max_success_rate: float = 0.3   # Below this → archive/demote
    archive_max_divergence: float = 0.5    # Above this → archive trajectory


# ─── Dream state (maps to KAIROS closure-scoped autoDream state) ────────

@dataclass
class DreamState:
    """Persistent state tracking across dream cycles."""
    last_consolidated_at: str = ""
    last_run_count_at_consolidation: int = 0
    current_run_count: int = 0
    total_consolidations: int = 0
    total_trajectories_promoted: int = 0
    total_trajectories_pruned: int = 0
    total_contradictions_resolved: int = 0
    last_dream_duration_s: float = 0.0


def _load_state() -> DreamState:
    if _STATE_FILE.exists():
        try:
            data = json.loads(_STATE_FILE.read_text())
            return DreamState(**{k: v for k, v in data.items() if k in DreamState.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError):
            pass
    return DreamState()


def _save_state(state: DreamState) -> None:
    _STATE_FILE.write_text(json.dumps(asdict(state), indent=2))


# ─── Dual-gate trigger (maps to KAIROS time + session gates) ────────────

def should_dream(config: Optional[DreamConfig] = None) -> dict[str, Any]:
    """Check if consolidation should fire. Both gates must pass.

    Returns: { should_run, reason, time_gate, session_gate, state }
    """
    cfg = config or DreamConfig()
    state = _load_state()

    # Time gate
    time_gate_pass = True
    if state.last_consolidated_at:
        last = datetime.fromisoformat(state.last_consolidated_at)
        hours_elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        time_gate_pass = hours_elapsed >= cfg.min_hours_since_last
    # else: never consolidated → pass

    # Session/run gate
    runs_since = state.current_run_count - state.last_run_count_at_consolidation
    session_gate_pass = runs_since >= cfg.min_runs_since_last

    should_run = time_gate_pass and session_gate_pass

    return {
        "should_run": should_run,
        "reason": (
            f"Both gates pass: {hours_elapsed if state.last_consolidated_at else '∞'}h elapsed, {runs_since} runs"
            if should_run
            else f"Gates: time={'PASS' if time_gate_pass else 'WAIT'}, runs={'PASS' if session_gate_pass else f'WAIT ({runs_since}/{cfg.min_runs_since_last})'}"
        ),
        "time_gate": time_gate_pass,
        "session_gate": session_gate_pass,
        "runs_since_last": runs_since,
        "state": asdict(state),
    }


def increment_run_count() -> None:
    """Called after each pipeline run to bump the session gate counter."""
    state = _load_state()
    state.current_run_count += 1
    _save_state(state)


# ─── Four-phase consolidation (maps to KAIROS autoDream) ────────────────

@dataclass
class DreamResult:
    """Output of a consolidation cycle."""
    duration_s: float = 0.0
    trajectories_analyzed: int = 0
    trajectories_promoted: int = 0
    trajectories_pruned: int = 0
    trajectories_archived: int = 0
    contradictions_resolved: int = 0
    rops_created: list[str] = field(default_factory=list)
    rops_updated: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def run_dream(config: Optional[DreamConfig] = None) -> DreamResult:
    """Execute the four-phase consolidation cycle.

    Phase 1: Orient — scan trajectories, replay results, existing ROPs
    Phase 2: Gather Signal — identify healthy, stale, contradicted trajectories
    Phase 3: Consolidate — promote healthy → ROP, merge overlapping, resolve contradictions
    Phase 4: Prune & Index — archive stale, update manifest index
    """
    cfg = config or DreamConfig()
    result = DreamResult()
    start = time.time()

    try:
        # ── Phase 1: Orient ──────────────────────────────────────────
        logger.info("[DREAM Phase 1] Orienting — scanning trajectories and replay results")

        trajectories = _scan_trajectories()
        replay_results = _scan_replay_results()
        existing_rops = _scan_existing_rops()

        result.trajectories_analyzed = len(trajectories)
        logger.info(
            f"[DREAM Phase 1] Found {len(trajectories)} trajectories, "
            f"{len(replay_results)} replay results, {len(existing_rops)} ROPs"
        )

        # ── Phase 2: Gather Signal ───────────────────────────────────
        logger.info("[DREAM Phase 2] Gathering signal — analyzing trajectory health")

        health_map = _analyze_health(trajectories, replay_results)
        promotable = []
        archivable = []
        prunable = []

        for traj_id, health in health_map.items():
            if health["replay_count"] >= cfg.promote_min_replays and health["success_rate"] >= cfg.promote_min_success_rate:
                promotable.append((traj_id, health))
            elif health["success_rate"] <= cfg.demote_max_success_rate and health["replay_count"] >= 2:
                archivable.append((traj_id, health))
            elif health["avg_divergence"] >= cfg.archive_max_divergence:
                archivable.append((traj_id, health))
            elif health["stale_days"] >= cfg.prune_stale_days and health["replay_count"] == 0:
                prunable.append((traj_id, health))

        logger.info(
            f"[DREAM Phase 2] Signal: {len(promotable)} promotable, "
            f"{len(archivable)} archivable, {len(prunable)} prunable"
        )

        # ── Phase 3: Consolidate ─────────────────────────────────────
        logger.info("[DREAM Phase 3] Consolidating — promoting and resolving")

        # Promote healthy trajectories to ROP manifests
        for traj_id, health in promotable:
            try:
                rop_id = _promote_to_rop(traj_id, health, existing_rops)
                if rop_id:
                    result.trajectories_promoted += 1
                    if rop_id in [r["id"] for r in existing_rops]:
                        result.rops_updated.append(rop_id)
                    else:
                        result.rops_created.append(rop_id)
            except Exception as e:
                result.errors.append(f"Promote {traj_id}: {e}")

        # Resolve contradictions: trajectories that cover the same workflow
        # but have divergent paths → keep the healthier one
        contradictions = _find_contradictions(trajectories, health_map)
        for winner_id, loser_id, reason in contradictions:
            try:
                _archive_trajectory(loser_id, reason=f"Contradicted by {winner_id}: {reason}")
                result.contradictions_resolved += 1
            except Exception as e:
                result.errors.append(f"Contradiction {loser_id}: {e}")

        # ── Phase 4: Prune & Index ───────────────────────────────────
        logger.info("[DREAM Phase 4] Pruning stale trajectories and updating index")

        for traj_id, health in archivable:
            try:
                _archive_trajectory(traj_id, reason=f"Low success rate ({health['success_rate']:.2f})")
                result.trajectories_archived += 1
            except Exception as e:
                result.errors.append(f"Archive {traj_id}: {e}")

        for traj_id, health in prunable:
            try:
                _prune_trajectory(traj_id)
                result.trajectories_pruned += 1
            except Exception as e:
                result.errors.append(f"Prune {traj_id}: {e}")

        # Update manifest index
        _update_manifest_index()

    except Exception as e:
        logger.error(f"[DREAM] Consolidation failed: {e}")
        result.errors.append(str(e))

    result.duration_s = round(time.time() - start, 1)

    # Update state
    state = _load_state()
    state.last_consolidated_at = datetime.now(timezone.utc).isoformat()
    state.last_run_count_at_consolidation = state.current_run_count
    state.total_consolidations += 1
    state.total_trajectories_promoted += result.trajectories_promoted
    state.total_trajectories_pruned += result.trajectories_pruned
    state.total_contradictions_resolved += result.contradictions_resolved
    state.last_dream_duration_s = result.duration_s
    _save_state(state)

    # Log the dream
    _log_dream(result)

    logger.info(
        f"[DREAM] Complete in {result.duration_s}s — "
        f"promoted:{result.trajectories_promoted} archived:{result.trajectories_archived} "
        f"pruned:{result.trajectories_pruned} contradictions:{result.contradictions_resolved}"
    )

    return result


# ─── Phase helpers ───────────────────────────────────────────────────────

def _scan_trajectories() -> list[dict[str, Any]]:
    """Phase 1: Load all trajectories from disk."""
    traj_dir = _DATA_DIR / "trajectories"
    trajectories = []
    if not traj_dir.exists():
        return trajectories
    for task_dir in traj_dir.iterdir():
        if not task_dir.is_dir():
            continue
        for f in task_dir.glob("*.json"):
            try:
                t = json.loads(f.read_text())
                t["_path"] = str(f)
                t["_task_dir"] = task_dir.name
                trajectories.append(t)
            except (json.JSONDecodeError, OSError):
                continue
    return trajectories


def _scan_replay_results() -> list[dict[str, Any]]:
    """Phase 1: Load all replay results."""
    replay_dir = _DATA_DIR / "replay_results"
    results = []
    if not replay_dir.exists():
        return results
    for f in replay_dir.glob("*.json"):
        try:
            results.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return results


def _scan_existing_rops() -> list[dict[str, Any]]:
    """Phase 1: Load existing ROP manifests."""
    rop_dir = _DATA_DIR / "rop_manifests"
    rops = []
    if not rop_dir.exists():
        return rops
    for f in rop_dir.glob("*.json"):
        try:
            rops.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return rops


def _analyze_health(
    trajectories: list[dict], replay_results: list[dict]
) -> dict[str, dict[str, Any]]:
    """Phase 2: Compute health metrics per trajectory."""
    # Group replays by trajectory_id
    replays_by_traj: dict[str, list[dict]] = defaultdict(list)
    for r in replay_results:
        tid = r.get("trajectory_id", "")
        if tid:
            replays_by_traj[tid].append(r)

    health_map = {}
    now = datetime.now(timezone.utc)

    for traj in trajectories:
        traj_id = traj.get("trajectory_id", "")
        replays = replays_by_traj.get(traj_id, [])

        # Age
        created = traj.get("started_at", "")
        stale_days = 999
        if created:
            try:
                stale_days = (now - datetime.fromisoformat(created)).days
            except ValueError:
                pass

        # Replay stats
        replay_count = len(replays)
        successes = sum(1 for r in replays if r.get("success"))
        success_rate = successes / max(replay_count, 1)
        avg_drift = sum(r.get("drift_score", 0) for r in replays) / max(replay_count, 1)
        avg_token_savings = sum(
            r.get("comparison_with_full", {}).get("token_savings_pct", 0) for r in replays
        ) / max(replay_count, 1)

        # Workflow family
        workflow = traj.get("task_name", traj.get("_task_dir", ""))
        surface = traj.get("surface", "web")

        health_map[traj_id] = {
            "trajectory_id": traj_id,
            "workflow": workflow,
            "surface": surface,
            "replay_count": replay_count,
            "success_rate": round(success_rate, 3),
            "avg_divergence": round(avg_drift, 3),
            "avg_token_savings": round(avg_token_savings, 1),
            "stale_days": stale_days,
            "total_steps": len(traj.get("steps", [])),
            "_path": traj.get("_path", ""),
        }

    return health_map


def _find_contradictions(
    trajectories: list[dict], health_map: dict[str, dict]
) -> list[tuple[str, str, str]]:
    """Phase 3: Find trajectories covering same workflow with divergent paths."""
    # Group by workflow family
    by_workflow: dict[str, list[str]] = defaultdict(list)
    for traj in trajectories:
        tid = traj.get("trajectory_id", "")
        wf = traj.get("task_name", traj.get("_task_dir", ""))
        if tid and wf:
            by_workflow[wf].append(tid)

    contradictions = []
    for wf, traj_ids in by_workflow.items():
        if len(traj_ids) < 2:
            continue
        # Sort by health (best first)
        sorted_ids = sorted(
            traj_ids,
            key=lambda t: (
                health_map.get(t, {}).get("success_rate", 0),
                -health_map.get(t, {}).get("avg_divergence", 1),
            ),
            reverse=True,
        )
        # Keep the best, mark others as contradicted if significantly worse
        best = sorted_ids[0]
        best_health = health_map.get(best, {})
        for other in sorted_ids[1:]:
            other_health = health_map.get(other, {})
            # Only mark as contradiction if significant gap
            if (
                best_health.get("success_rate", 0) - other_health.get("success_rate", 0) > 0.3
                and other_health.get("replay_count", 0) >= 2
            ):
                contradictions.append((
                    best, other,
                    f"Same workflow '{wf}': winner {best_health.get('success_rate', 0):.0%} vs loser {other_health.get('success_rate', 0):.0%}"
                ))

    return contradictions


def _promote_to_rop(
    traj_id: str, health: dict[str, Any], existing_rops: list[dict]
) -> Optional[str]:
    """Phase 3: Promote a healthy trajectory to an ROP manifest."""
    # Check if already covered by an existing ROP
    workflow = health.get("workflow", "")
    for rop in existing_rops:
        if workflow in rop.get("triggers", []):
            # Already has an ROP — update its stats instead
            logger.debug(f"Trajectory {traj_id} already covered by ROP {rop['id']}")
            return rop["id"]

    # Create a new auto-generated ROP manifest
    rop_id = f"rop.auto.{workflow.replace(' ', '_').lower()[:30]}.v1"
    surface = health.get("surface", "web")

    manifest = {
        "id": rop_id,
        "name": f"Auto: {workflow}",
        "short_name": workflow[:8].upper(),
        "category": "retained_operation_pattern",
        "version": "1.0.0-auto",
        "purpose": f"Auto-promoted from trajectory {traj_id} ({health['replay_count']} replays, {health['success_rate']:.0%} success)",
        "triggers": [workflow],
        "surfaces": [surface],
        "subagent_roles": {},
        "retrieval_strategy": {"clustering": "by_trajectory", "source_trajectory": traj_id},
        "progressive_disclosure": {
            "layer_0_card": {
                "when_to_use": f"Repeat of workflow: {workflow}",
                "expected_output": "Trajectory replay with checkpoint validation",
                "risk_level": "low" if health["success_rate"] >= 0.9 else "medium",
                "typical_savings": f"{health['avg_token_savings']:.0f}% token savings",
            },
            "layer_1_skeleton": [f"Step {i}" for i in range(health.get("total_steps", 0))],
        },
        "prefix_signature": ["workflow_start", "trajectory_steps"],
        "suggest_next_policy": {
            "method": "prefix_match_plus_divergence_guard",
            "min_confidence": 0.72 if health["success_rate"] >= 0.8 else 0.80,
        },
        "divergence_policy": {
            "stop_on": ["drift_threshold_exceeded", "fallback_triggered"],
            "max_drift": health["avg_divergence"] * 2,  # 2x observed drift as threshold
        },
        "outputs": ["replay_result", "savings_report"],
        "audit": {
            "required": True,
            "verify": ["steps_matched", "no_excessive_drift"],
        },
        "kpis": {
            "replay_success_rate": f"{health['success_rate']:.0%}",
            "avg_token_savings": f"{health['avg_token_savings']:.0f}%",
            "total_replays": str(health["replay_count"]),
        },
        "_auto_promoted": True,
        "_source_trajectory": traj_id,
        "_promoted_at": datetime.now(timezone.utc).isoformat(),
    }

    # Write to disk
    rop_path = _DATA_DIR / "rop_manifests" / f"{rop_id}.json"
    rop_path.write_text(json.dumps(manifest, indent=2))
    logger.info(f"[DREAM] Promoted trajectory {traj_id} → ROP {rop_id}")
    return rop_id


def _archive_trajectory(traj_id: str, reason: str = "") -> None:
    """Phase 4: Move a trajectory to the archive directory."""
    traj_dir = _DATA_DIR / "trajectories"
    archive_dir = _DATA_DIR / "trajectories_archived"
    archive_dir.mkdir(parents=True, exist_ok=True)

    for task_dir in traj_dir.iterdir():
        if not task_dir.is_dir():
            continue
        for f in task_dir.glob("*.json"):
            try:
                t = json.loads(f.read_text())
                if t.get("trajectory_id") == traj_id:
                    # Add archive metadata
                    t["_archived_at"] = datetime.now(timezone.utc).isoformat()
                    t["_archive_reason"] = reason
                    # Move to archive
                    dest = archive_dir / f"{traj_id}.json"
                    dest.write_text(json.dumps(t, indent=2))
                    f.unlink()
                    logger.info(f"[DREAM] Archived trajectory {traj_id}: {reason}")
                    return
            except (json.JSONDecodeError, OSError):
                continue


def _prune_trajectory(traj_id: str) -> None:
    """Phase 4: Permanently remove a stale trajectory with no replays."""
    _archive_trajectory(traj_id, reason="Stale: no replays, exceeded prune threshold")


def _update_manifest_index() -> None:
    """Phase 4: Rebuild the ROP manifest index (lean, like MEMORY.md under 200 lines)."""
    rop_dir = _DATA_DIR / "rop_manifests"
    if not rop_dir.exists():
        return

    index = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "manifests": [],
    }

    for f in sorted(rop_dir.glob("*.json")):
        try:
            rop = json.loads(f.read_text())
            index["manifests"].append({
                "id": rop["id"],
                "name": rop.get("name", ""),
                "triggers": rop.get("triggers", [])[:3],
                "auto_promoted": rop.get("_auto_promoted", False),
            })
        except (json.JSONDecodeError, OSError, KeyError):
            continue

    index_path = rop_dir / "_index.json"
    index_path.write_text(json.dumps(index, indent=2))


def _log_dream(result: DreamResult) -> None:
    """Log the dream result for audit trail."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **asdict(result),
    }
    with open(_DREAM_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ─── Heartbeat advisory (maps to KAIROS heartbeat loop) ─────────────────

def heartbeat_check() -> dict[str, Any]:
    """Periodic check: should we dream? Is suggest_next ready? Any drift alerts?

    Called by the pipeline or a scheduled task. Returns a status dict
    that the caller can act on (or ignore for advisory-only mode).
    """
    from .rop_savings_tracker import get_rop_savings_tracker

    dream_status = should_dream()
    tracker = get_rop_savings_tracker()
    portfolio = tracker.portfolio_stats(days=7)

    # Check for high-divergence patterns that need attention
    alerts = []
    for p in tracker.pattern_stats():
        if p.get("divergence_rate", 0) > 0.4:
            alerts.append({
                "type": "high_divergence",
                "rop_id": p["rop_id"],
                "divergence_rate": p["divergence_rate"],
                "recommendation": "Consider re-learning or archiving this pattern",
            })

    return {
        "dream_ready": dream_status["should_run"],
        "dream_reason": dream_status["reason"],
        "portfolio_7d": {
            "runs": portfolio["total_runs"],
            "tokens_saved": portfolio["total_tokens_saved"],
            "cost_saved_usd": portfolio.get("total_cost_saved_usd", 0),
        },
        "alerts": alerts,
        "suggest_next_ready": True,  # Always ready if trajectories exist
    }
