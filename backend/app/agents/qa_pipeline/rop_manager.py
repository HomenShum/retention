"""
ROP Manager — lifecycle engine for Retained Operation Patterns.

Handles: create → validate → promote → operate → escalate → retire.

Storage follows the same JSON-on-disk pattern as exploration_memory.py:
  backend/data/rop_patterns/{rop_id}.json + rop_index.json
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .rop_models import (
    EscalationReason,
    ModelTier,
    ROPCheckpoint,
    ROPCostMetrics,
    ROPStatus,
    ROPValidationAttempt,
    ReplayPolicy,
    RetainedOperationPattern,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_ROP_DIR = Path(__file__).resolve().parents[3] / "data" / "rop_patterns"
_ROP_DIR.mkdir(parents=True, exist_ok=True)

_ROP_INDEX_PATH = _ROP_DIR / "rop_index.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_index() -> Dict[str, Any]:
    if _ROP_INDEX_PATH.exists():
        try:
            return json.loads(_ROP_INDEX_PATH.read_text())
        except Exception:
            pass
    return {"rops": {}, "stats": {}}


def _save_index(index: Dict[str, Any]) -> None:
    _ROP_INDEX_PATH.write_text(json.dumps(index, indent=2, default=str))


def _load_rop(rop_id: str) -> Optional[RetainedOperationPattern]:
    path = _ROP_DIR / f"{rop_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return RetainedOperationPattern(**data)
    except Exception as e:
        logger.error(f"Failed to load ROP {rop_id}: {e}")
        return None


def _save_rop(rop: RetainedOperationPattern) -> None:
    path = _ROP_DIR / f"{rop.rop_id}.json"
    path.write_text(json.dumps(rop.model_dump(), indent=2, default=str))


# ---------------------------------------------------------------------------
# ROPManager
# ---------------------------------------------------------------------------

class ROPManager:
    """Lifecycle manager for Retained Operation Patterns."""

    def create_rop(
        self,
        trajectory_id: str,
        task_name: str,
        app_key: str,
        app_url: str = "",
        workflow_id: str = "",
        workflow_name: str = "",
        origin_model: str = "",
        origin_tier: str = "frontier",
        crawl_fingerprint: str = "",
        screen_fingerprints: Optional[Dict[str, str]] = None,
        steps_summary: Optional[List[str]] = None,
        step_count: int = 0,
        discovery_tokens: int = 0,
        discovery_cost_usd: float = 0.0,
        discovery_time_s: float = 0.0,
        checkpoints: Optional[List[ROPCheckpoint]] = None,
    ) -> RetainedOperationPattern:
        """Create a DRAFT ROP after a successful frontier exploration.

        Args:
            trajectory_id: Links to the TrajectoryLog that was captured
            task_name: Task name for trajectory lookup
            app_key: From app_fingerprint()
            origin_model: Which model solved the workflow
            checkpoints: Pre-built checkpoints (or auto-generated from trajectory)
        """
        from ..model_fallback import get_tier_for_model

        rop_id = f"rop-{uuid.uuid4().hex[:12]}"
        tier = ModelTier(origin_tier) if origin_tier in [t.value for t in ModelTier] else ModelTier(get_tier_for_model(origin_model))

        cost_metrics = ROPCostMetrics(
            discovery_tokens=discovery_tokens,
            discovery_cost_usd=discovery_cost_usd,
            discovery_time_s=discovery_time_s,
        )

        rop = RetainedOperationPattern(
            rop_id=rop_id,
            app_key=app_key,
            app_url=app_url,
            workflow_id=workflow_id or task_name,
            workflow_name=workflow_name or task_name,
            origin_model=origin_model,
            origin_tier=tier,
            origin_trajectory_id=trajectory_id,
            replay_policy=ReplayPolicy(),
            checkpoints=checkpoints or [],
            crawl_fingerprint=crawl_fingerprint,
            screen_fingerprints=screen_fingerprints or {},
            step_count=step_count,
            steps_summary=steps_summary or [],
            status=ROPStatus.DRAFT,
            created_at=_now_iso(),
            cost_metrics=cost_metrics,
        )

        _save_rop(rop)

        # Update index
        index = _load_index()
        index["rops"][rop_id] = {
            "app_key": app_key,
            "workflow_id": rop.workflow_id,
            "workflow_name": rop.workflow_name,
            "origin_model": origin_model,
            "origin_tier": tier.value,
            "status": ROPStatus.DRAFT.value,
            "created_at": rop.created_at,
            "step_count": step_count,
        }
        _save_index(index)

        logger.info(f"Created ROP {rop_id}: app={app_key}, workflow={rop.workflow_name}, model={origin_model}")
        return rop

    def create_rop_from_trajectory(
        self,
        trajectory,  # TrajectoryLog dataclass
        app_key: str,
        app_url: str = "",
        origin_model: str = "",
        crawl_fingerprint: str = "",
        screen_fingerprints: Optional[Dict[str, str]] = None,
        discovery_tokens: int = 0,
        discovery_cost_usd: float = 0.0,
    ) -> RetainedOperationPattern:
        """Create a DRAFT ROP directly from a TrajectoryLog, auto-generating checkpoints."""
        checkpoints = []
        for step in trajectory.steps:
            if step.screen_fingerprint_after:
                checkpoints.append(ROPCheckpoint(
                    step_index=step.step_index,
                    screen_fingerprint=step.screen_fingerprint_after,
                    expected_action_type=step.action.split()[0].lower() if step.action else "",
                ))

        steps_summary = [
            step.semantic_label or step.action[:80]
            for step in trajectory.steps
        ]

        return self.create_rop(
            trajectory_id=trajectory.trajectory_id,
            task_name=trajectory.task_name,
            app_key=app_key,
            app_url=app_url,
            workflow_id=trajectory.task_name,
            workflow_name=trajectory.task_goal or trajectory.task_name,
            origin_model=origin_model,
            crawl_fingerprint=crawl_fingerprint,
            screen_fingerprints=screen_fingerprints,
            steps_summary=steps_summary,
            step_count=len(trajectory.steps),
            discovery_tokens=discovery_tokens,
            discovery_cost_usd=discovery_cost_usd,
            discovery_time_s=(
                trajectory.metadata.get("duration_s", 0)
                if trajectory.metadata else 0
            ),
            checkpoints=checkpoints,
        )

    def get_rop(self, rop_id: str) -> Optional[RetainedOperationPattern]:
        """Load a single ROP by ID."""
        return _load_rop(rop_id)

    def list_rops(self, status: Optional[ROPStatus] = None, app_key: str = "") -> List[RetainedOperationPattern]:
        """List all ROPs, optionally filtered by status or app."""
        index = _load_index()
        results = []
        for rop_id, info in index.get("rops", {}).items():
            if status and info.get("status") != status.value:
                continue
            if app_key and info.get("app_key") != app_key:
                continue
            rop = _load_rop(rop_id)
            if rop:
                results.append(rop)
        return results

    def find_rop_for_workflow(
        self, app_key: str, workflow_id: str
    ) -> Optional[RetainedOperationPattern]:
        """Find the best PROMOTED or OPERATING ROP for a workflow.

        Returns the one with the highest replay success ratio.
        """
        candidates = []
        index = _load_index()
        for rop_id, info in index.get("rops", {}).items():
            if info.get("app_key") != app_key:
                continue
            if info.get("workflow_id") != workflow_id:
                continue
            if info.get("status") not in (ROPStatus.PROMOTED.value, ROPStatus.OPERATING.value):
                continue
            rop = _load_rop(rop_id)
            if rop:
                candidates.append(rop)

        if not candidates:
            return None

        # Pick the one with the best success ratio
        def _score(rop: RetainedOperationPattern) -> float:
            if rop.replay_count == 0:
                return 0.5  # untested promoted ROP — moderate priority
            return rop.replay_success_count / rop.replay_count

        candidates.sort(key=_score, reverse=True)
        return candidates[0]

    def promote_rop(self, rop_id: str, validated_model: str = "", validated_tier: str = "replay") -> bool:
        """Promote a ROP to PROMOTED status after successful validation."""
        rop = _load_rop(rop_id)
        if not rop:
            return False

        rop.status = ROPStatus.PROMOTED
        rop.promoted_at = _now_iso()
        if validated_model:
            rop.replay_model = validated_model
        if validated_tier:
            rop.replay_tier = ModelTier(validated_tier)

        _save_rop(rop)
        self._update_index_status(rop_id, ROPStatus.PROMOTED.value)
        logger.info(f"Promoted ROP {rop_id} → replay_model={validated_model}")
        return True

    def mark_operating(self, rop_id: str) -> bool:
        """Mark a promoted ROP as actively in production use."""
        rop = _load_rop(rop_id)
        if not rop:
            return False
        rop.status = ROPStatus.OPERATING
        _save_rop(rop)
        self._update_index_status(rop_id, ROPStatus.OPERATING.value)
        return True

    def escalate_rop(self, rop_id: str, reason: EscalationReason, drift_details: str = "") -> str:
        """Escalate a ROP when replay detects drift.

        Returns the model to escalate to.
        """
        from ..model_fallback import get_escalation_chain

        rop = _load_rop(rop_id)
        if not rop:
            return ""

        rop.status = ROPStatus.ESCALATED
        rop.escalation_count += 1
        rop.last_escalation_reason = reason
        _save_rop(rop)
        self._update_index_status(rop_id, ROPStatus.ESCALATED.value)

        # Determine escalation target
        chain = get_escalation_chain(rop.replay_model or rop.origin_model)
        target = chain[0] if chain else rop.origin_model
        logger.warning(
            f"Escalated ROP {rop_id}: reason={reason.value}, "
            f"from={rop.replay_model} → {target}, details={drift_details}"
        )
        return target

    def retire_rop(self, rop_id: str, reason: str = "") -> bool:
        """Retire a ROP — app changes invalidated the pattern."""
        rop = _load_rop(rop_id)
        if not rop:
            return False
        rop.status = ROPStatus.RETIRED
        rop.retired_at = _now_iso()
        _save_rop(rop)
        self._update_index_status(rop_id, ROPStatus.RETIRED.value)
        logger.info(f"Retired ROP {rop_id}: {reason}")
        return True

    def record_validation_attempt(
        self,
        rop_id: str,
        model: str,
        tier: str,
        success: bool,
        drift_score: float = 0.0,
        checkpoints_passed: int = 0,
        checkpoints_total: int = 0,
    ) -> None:
        """Record a replay validation attempt on a ROP."""
        rop = _load_rop(rop_id)
        if not rop:
            return

        attempt = ROPValidationAttempt(
            model=model,
            tier=tier,
            success=success,
            drift_score=drift_score,
            checkpoints_passed=checkpoints_passed,
            checkpoints_total=checkpoints_total,
            timestamp=_now_iso(),
        )
        rop.validation_attempts.append(attempt)

        if success:
            rop.status = ROPStatus.PROMOTED
            rop.validated_at = _now_iso()
            rop.replay_model = model
            rop.replay_tier = ModelTier(tier)
        else:
            rop.status = ROPStatus.DRAFT  # back to draft for another attempt

        _save_rop(rop)
        self._update_index_status(rop_id, rop.status.value)

    def record_replay_result(
        self,
        rop_id: str,
        success: bool,
        replay_tokens: int = 0,
        replay_cost_usd: float = 0.0,
        replay_time_s: float = 0.0,
    ) -> None:
        """Record the result of a production replay."""
        rop = _load_rop(rop_id)
        if not rop:
            return

        rop.replay_count += 1
        if success:
            rop.replay_success_count += 1
        else:
            rop.replay_failure_count += 1

        rop.last_replayed_at = _now_iso()
        rop.cost_metrics.replay_tokens += replay_tokens
        rop.cost_metrics.replay_cost_usd += replay_cost_usd
        rop.cost_metrics.replay_time_s += replay_time_s

        # Update savings
        if rop.cost_metrics.discovery_cost_usd > 0:
            avg_replay_cost = rop.cost_metrics.replay_cost_usd / max(rop.replay_count, 1)
            rop.cost_metrics.savings_pct = 1.0 - (avg_replay_cost / rop.cost_metrics.discovery_cost_usd)

        rop.cost_metrics.cumulative_savings_usd = (
            (rop.cost_metrics.discovery_cost_usd * rop.replay_count)
            - rop.cost_metrics.replay_cost_usd
        )

        _save_rop(rop)

    def check_rop_validity(self, rop_id: str, current_screen_graph: Dict[str, str]) -> bool:
        """Check if a ROP is still valid by comparing stored vs current screen fingerprints.

        Returns True if the ROP is still valid, False if it should be retired.
        """
        rop = _load_rop(rop_id)
        if not rop or not rop.screen_fingerprints:
            return True  # no fingerprints to compare

        for screen_id, expected_fp in rop.screen_fingerprints.items():
            current_fp = current_screen_graph.get(screen_id)
            if current_fp is not None and current_fp != expected_fp:
                logger.info(
                    f"ROP {rop_id} screen {screen_id} changed: "
                    f"{expected_fp} → {current_fp}"
                )
                return False

        return True

    def check_all_rops_for_app(
        self, app_key: str, current_screen_graph: Dict[str, str]
    ) -> List[str]:
        """Check all ROPs for an app and retire any that are invalid.

        Returns list of retired ROP IDs.
        """
        retired = []
        index = _load_index()
        for rop_id, info in index.get("rops", {}).items():
            if info.get("app_key") != app_key:
                continue
            if info.get("status") in (ROPStatus.RETIRED.value,):
                continue
            if not self.check_rop_validity(rop_id, current_screen_graph):
                self.retire_rop(rop_id, reason="app_fingerprint_changed")
                retired.append(rop_id)

        if retired:
            logger.info(f"Retired {len(retired)} ROPs for app {app_key}: {retired}")
        return retired

    def get_dashboard_stats(self) -> Dict[str, Any]:
        """Aggregate metrics across all ROPs for the dashboard."""
        index = _load_index()
        by_status: Dict[str, int] = {}
        by_tier: Dict[str, int] = {}
        total_discovery_cost = 0.0
        total_replay_cost = 0.0
        total_replays = 0
        total_successes = 0
        total_escalations = 0

        for rop_id in index.get("rops", {}):
            rop = _load_rop(rop_id)
            if not rop:
                continue
            by_status[rop.status.value] = by_status.get(rop.status.value, 0) + 1
            by_tier[rop.origin_tier.value] = by_tier.get(rop.origin_tier.value, 0) + 1
            total_discovery_cost += rop.cost_metrics.discovery_cost_usd
            total_replay_cost += rop.cost_metrics.replay_cost_usd
            total_replays += rop.replay_count
            total_successes += rop.replay_success_count
            total_escalations += rop.escalation_count

        total_savings = (total_discovery_cost * max(total_replays, 1)) - total_replay_cost

        return {
            "total_rops": len(index.get("rops", {})),
            "by_status": by_status,
            "by_tier": by_tier,
            "cost_metrics": {
                "total_discovery_cost_usd": round(total_discovery_cost, 4),
                "total_replay_cost_usd": round(total_replay_cost, 4),
                "total_savings_usd": round(max(total_savings, 0), 4),
                "savings_pct": round(
                    1.0 - (total_replay_cost / (total_discovery_cost * max(total_replays, 1)))
                    if total_discovery_cost > 0 and total_replays > 0 else 0, 4
                ),
            },
            "replay_metrics": {
                "total_replays": total_replays,
                "replay_success_rate": round(
                    total_successes / max(total_replays, 1), 4
                ),
                "escalation_frequency": round(
                    total_escalations / max(total_replays, 1), 4
                ),
            },
        }

    # -- Internal helpers --

    def _update_index_status(self, rop_id: str, status: str) -> None:
        index = _load_index()
        if rop_id in index.get("rops", {}):
            index["rops"][rop_id]["status"] = status
            _save_index(index)
