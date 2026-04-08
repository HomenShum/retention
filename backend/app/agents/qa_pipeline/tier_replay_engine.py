"""
Tier-Aware Replay Engine — replays ROPs with checkpoint validation and model escalation.

Critical safety: REPLAY tier models receive ONLY pre-recorded steps.
They execute, observe, compare against checkpoints, and escalate on failure.
They NEVER generate novel action plans.

Yields SSE events matching replay_trajectory() / execute_test_suite() patterns
for pipeline compatibility.
"""

import logging
import time
import uuid
from typing import Any, AsyncGenerator, Dict, Optional

from ..model_fallback import estimate_cost, get_escalation_chain, get_tier_for_model
from .rop_models import (
    EscalationReason,
    ModelTier,
    RetainedOperationPattern,
    ROPCheckpoint,
)
from .rop_manager import ROPManager
from .trajectory_replay import (
    _capture_screen_state,
    _compute_screen_fingerprint,
    _execute_step_on_device,
    _now_iso,
    create_exploration_fallback,
)

logger = logging.getLogger(__name__)


class TierReplayEngine:
    """Replays ROPs with tier-aware checkpoint validation and automatic escalation."""

    def __init__(self, rop_manager: Optional[ROPManager] = None):
        self._manager = rop_manager or ROPManager()

    async def replay_rop(
        self,
        rop: RetainedOperationPattern,
        mobile_client,
        device_id: str,
        app_url: str = "",
        run_id: str = "",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Replay a ROP step-by-step with checkpoint validation.

        Yields SSE-compatible event dicts for streaming progress.
        """
        from ..device_testing.trajectory_logger import get_trajectory_logger

        tl = get_trajectory_logger()
        trajectory = tl.load_trajectory(rop.workflow_id, rop.origin_trajectory_id)

        if not trajectory:
            yield {
                "type": "rop_replay_error",
                "rop_id": rop.rop_id,
                "error": f"Source trajectory not found: {rop.workflow_id}/{rop.origin_trajectory_id}",
                "timestamp": _now_iso(),
            }
            return

        if not run_id:
            run_id = f"rop-replay-{uuid.uuid4().hex[:8]}"

        replay_model = rop.replay_model or rop.origin_model
        replay_tier = get_tier_for_model(replay_model)

        yield {
            "type": "rop_replay_start",
            "rop_id": rop.rop_id,
            "run_id": run_id,
            "replay_model": replay_model,
            "replay_tier": replay_tier,
            "total_steps": len(trajectory.steps),
            "total_checkpoints": len(rop.checkpoints),
            "timestamp": _now_iso(),
        }

        start_time = time.time()
        steps_executed = 0
        checkpoints_passed = 0
        checkpoints_failed = 0
        total_tokens = 0
        consecutive_drifts = 0
        escalated = False
        escalation_reason = None

        # Build checkpoint lookup: step_index → ROPCheckpoint
        checkpoint_map: Dict[int, ROPCheckpoint] = {
            cp.step_index: cp for cp in rop.checkpoints
        }

        for step in trajectory.steps:
            step_start = time.time()

            # Execute step on device
            exec_result = await _execute_step_on_device(
                step, mobile_client, device_id, app_url
            )
            steps_executed += 1

            # Checkpoint validation at configured intervals
            should_check = (
                step.step_index in checkpoint_map
                or (rop.replay_policy.checkpoint_interval > 0
                    and step.step_index % rop.replay_policy.checkpoint_interval == 0)
            )

            checkpoint_passed = None
            confidence = 1.0
            drift_reason = None

            if should_check:
                cp = checkpoint_map.get(step.step_index)
                passed, conf, reason = await self._validate_checkpoint(
                    cp, mobile_client, device_id
                )
                checkpoint_passed = passed
                confidence = conf

                if passed:
                    checkpoints_passed += 1
                    consecutive_drifts = 0
                else:
                    checkpoints_failed += 1
                    consecutive_drifts += 1
                    drift_reason = reason

            step_duration = int((time.time() - step_start) * 1000)

            yield {
                "type": "rop_step_complete",
                "rop_id": rop.rop_id,
                "run_id": run_id,
                "step_index": step.step_index,
                "action": step.action[:80],
                "exec_success": exec_result.get("success", False),
                "checkpoint_passed": checkpoint_passed,
                "confidence": round(confidence, 3),
                "duration_ms": step_duration,
                "timestamp": _now_iso(),
            }

            # Check escalation triggers
            if drift_reason and replay_tier == "replay":
                # REPLAY tier: any checkpoint failure → mandatory escalation
                if (
                    consecutive_drifts >= rop.replay_policy.max_consecutive_drifts
                    or drift_reason in [r.value for r in rop.replay_policy.escalation_triggers]
                ):
                    escalated = True
                    escalation_reason = drift_reason
                    escalation_target = self._manager.escalate_rop(
                        rop.rop_id,
                        EscalationReason(drift_reason) if drift_reason in [r.value for r in EscalationReason] else EscalationReason.CONTRACT_DRIFT,
                    )

                    yield {
                        "type": "rop_escalation",
                        "rop_id": rop.rop_id,
                        "run_id": run_id,
                        "reason": drift_reason,
                        "step_index": step.step_index,
                        "escalating_to": escalation_target,
                        "consecutive_drifts": consecutive_drifts,
                        "timestamp": _now_iso(),
                    }

                    # Delegate to exploration fallback
                    async for event in self._handle_escalation(
                        rop, step.step_index, mobile_client, device_id, app_url
                    ):
                        yield event
                    break

        # Compute final metrics
        elapsed_s = time.time() - start_time
        replay_cost = estimate_cost(total_tokens, replay_model)
        success = not escalated and checkpoints_failed == 0

        yield {
            "type": "rop_replay_complete",
            "rop_id": rop.rop_id,
            "run_id": run_id,
            "success": success,
            "steps_executed": steps_executed,
            "total_steps": len(trajectory.steps),
            "checkpoints_passed": checkpoints_passed,
            "checkpoints_failed": checkpoints_failed,
            "escalated": escalated,
            "escalation_reason": escalation_reason,
            "replay_model": replay_model,
            "replay_tier": replay_tier,
            "elapsed_s": round(elapsed_s, 2),
            "replay_tokens": total_tokens,
            "replay_cost_usd": round(replay_cost, 6),
            "discovery_cost_usd": round(rop.cost_metrics.discovery_cost_usd, 6),
            "savings_pct": round(
                1.0 - (replay_cost / rop.cost_metrics.discovery_cost_usd)
                if rop.cost_metrics.discovery_cost_usd > 0 else 0, 4
            ),
            "timestamp": _now_iso(),
        }

        # Update ROP metrics
        self._manager.record_replay_result(
            rop_id=rop.rop_id,
            success=success,
            replay_tokens=total_tokens,
            replay_cost_usd=replay_cost,
            replay_time_s=elapsed_s,
        )

        # ── ROP Savings Tracker — record tier replay run (RET-14) ─────────
        try:
            from ...services.rop_savings_tracker import get_rop_savings_tracker, ROPRunRecord
            _tracker = get_rop_savings_tracker()
            _tracker.record_run(ROPRunRecord(
                run_id=run_id,
                rop_id=rop.rop_id,
                rop_family=rop.workflow_family if hasattr(rop, "workflow_family") else "",
                run_type="replay" if not escalated else "assisted",
                timestamp=_now_iso(),
                total_tokens=total_tokens,
                reasoning_tokens=0,
                reasoning_tokens_avoided=max(0, int(rop.cost_metrics.discovery_tokens) - total_tokens) if hasattr(rop.cost_metrics, "discovery_tokens") else 0,
                total_time_s=elapsed_s,
                time_saved_s=max(0, rop.cost_metrics.discovery_time_s - elapsed_s) if hasattr(rop.cost_metrics, "discovery_time_s") else 0,
                checkpoints_passed=checkpoints_passed,
                checkpoints_failed=checkpoints_failed,
                success=success,
            ))
        except Exception as _savings_err:
            logger.debug(f"ROP savings recording skipped: {_savings_err}")

        # Auto-promote to OPERATING on first successful replay
        if success and rop.status.value == "promoted":
            self._manager.mark_operating(rop.rop_id)

    async def _validate_checkpoint(
        self,
        checkpoint: Optional[ROPCheckpoint],
        mobile_client,
        device_id: str,
    ) -> tuple:
        """Validate a single checkpoint against current device state.

        Returns: (passed: bool, confidence: float, reason: Optional[str])
        """
        if not checkpoint:
            # No specific checkpoint — just verify screen is responsive
            try:
                state = await _capture_screen_state(mobile_client, device_id)
                if state.get("error"):
                    return (False, 0.0, EscalationReason.MISSING_TOOL.value)
                return (True, 0.8, None)
            except Exception:
                return (False, 0.0, EscalationReason.MISSING_TOOL.value)

        try:
            state = await _capture_screen_state(mobile_client, device_id)
            if state.get("error"):
                return (False, 0.0, EscalationReason.MISSING_TOOL.value)

            # Compare screen fingerprint
            current_fp = _compute_screen_fingerprint(state)
            fp_match = (
                checkpoint.screen_fingerprint == ""
                or current_fp == checkpoint.screen_fingerprint
            )

            # Check required elements
            ui_elements = state.get("ui_elements", {})
            elements_text = str(ui_elements).lower()
            elements_found = 0
            for req in checkpoint.required_elements:
                if req.lower() in elements_text:
                    elements_found += 1

            total_required = len(checkpoint.required_elements) or 1
            element_ratio = elements_found / total_required

            # Compute confidence
            confidence = (0.5 * float(fp_match)) + (0.5 * element_ratio)

            if not fp_match and element_ratio < 0.5:
                return (False, confidence, EscalationReason.UNEXPECTED_BRANCH.value)
            if confidence < checkpoint.min_confidence:
                return (False, confidence, EscalationReason.CONFIDENCE_BELOW_THRESHOLD.value)

            return (True, confidence, None)

        except Exception as e:
            logger.warning(f"Checkpoint validation error: {e}")
            return (False, 0.0, EscalationReason.CHECKPOINT_FAILURE.value)

    async def _handle_escalation(
        self,
        rop: RetainedOperationPattern,
        drift_step: int,
        mobile_client,
        device_id: str,
        app_url: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Handle escalation by falling back to exploration from the drift point."""
        fallback = create_exploration_fallback(
            mobile_client=mobile_client,
            device_id=device_id,
            app_url=app_url,
            task_name=f"rop_{rop.rop_id}_{rop.workflow_name}",
            drift_step=drift_step,
        )

        try:
            async for event in fallback():
                event["_rop_escalation"] = True
                event["_rop_id"] = rop.rop_id
                yield event
        except Exception as e:
            logger.error(f"ROP escalation fallback failed: {e}")
            yield {
                "type": "rop_escalation_error",
                "rop_id": rop.rop_id,
                "error": str(e),
                "timestamp": _now_iso(),
            }
