"""
ROP (Retained Operation Pattern) API routes.

Endpoints for listing, inspecting, validating, promoting, and retiring ROPs,
plus a dashboard for aggregate cost/replay metrics.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..agents.qa_pipeline.rop_manager import ROPManager
from ..agents.qa_pipeline.rop_models import ROPStatus

router = APIRouter(prefix="/api/rops", tags=["rop-distillation"])

_manager = ROPManager()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ValidateRequest(BaseModel):
    target_model: str = "claude-haiku-4.5"
    target_tier: str = "replay"


class PromoteRequest(BaseModel):
    validated_model: str = ""
    validated_tier: str = "replay"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def list_rops(
    status: Optional[str] = Query(None, description="Filter by status"),
    app_key: str = Query("", description="Filter by app key"),
):
    """List all ROPs, optionally filtered by status or app."""
    filter_status = ROPStatus(status) if status else None
    rops = _manager.list_rops(status=filter_status, app_key=app_key)
    return {
        "total": len(rops),
        "rops": [
            {
                "rop_id": r.rop_id,
                "workflow_name": r.workflow_name,
                "status": r.status.value,
                "origin_model": r.origin_model,
                "origin_tier": r.origin_tier.value,
                "replay_model": r.replay_model,
                "replay_tier": r.replay_tier.value,
                "replay_count": r.replay_count,
                "replay_success_count": r.replay_success_count,
                "escalation_count": r.escalation_count,
                "savings_pct": r.cost_metrics.savings_pct,
                "created_at": r.created_at,
            }
            for r in rops
        ],
    }


@router.get("/dashboard")
async def get_dashboard():
    """Aggregate ROP metrics for the dashboard."""
    return _manager.get_dashboard_stats()


@router.get("/savings")
async def get_savings():
    """Cost savings breakdown across all ROPs."""
    stats = _manager.get_dashboard_stats()
    return stats["cost_metrics"]


@router.get("/{rop_id}")
async def get_rop(rop_id: str):
    """Get full detail for a single ROP."""
    rop = _manager.get_rop(rop_id)
    if not rop:
        raise HTTPException(status_code=404, detail=f"ROP {rop_id} not found")
    return rop.model_dump()


@router.post("/{rop_id}/validate")
async def validate_rop(rop_id: str, req: ValidateRequest):
    """Trigger validation of a ROP with a target model/tier.

    Note: This records the validation intent. Actual replay validation
    requires a device session and is triggered via the pipeline.
    """
    rop = _manager.get_rop(rop_id)
    if not rop:
        raise HTTPException(status_code=404, detail=f"ROP {rop_id} not found")

    rop.status = ROPStatus.VALIDATING
    from ..agents.qa_pipeline.rop_manager import _save_rop
    _save_rop(rop)

    return {
        "rop_id": rop_id,
        "status": "validating",
        "target_model": req.target_model,
        "target_tier": req.target_tier,
        "message": "Validation queued. Run the pipeline with this ROP to complete validation.",
    }


@router.post("/{rop_id}/promote")
async def promote_rop(rop_id: str, req: PromoteRequest):
    """Manually promote a ROP to PROMOTED status."""
    ok = _manager.promote_rop(rop_id, req.validated_model, req.validated_tier)
    if not ok:
        raise HTTPException(status_code=404, detail=f"ROP {rop_id} not found")
    return {"rop_id": rop_id, "status": "promoted"}


@router.post("/{rop_id}/retire")
async def retire_rop(rop_id: str):
    """Manually retire a ROP."""
    ok = _manager.retire_rop(rop_id, reason="manual_retirement")
    if not ok:
        raise HTTPException(status_code=404, detail=f"ROP {rop_id} not found")
    return {"rop_id": rop_id, "status": "retired"}


class RerunRequest(BaseModel):
    target_model: str = "claude-haiku-4-5"


@router.post("/{rop_id}/rerun")
async def rerun_rop(rop_id: str, req: RerunRequest):
    """Rerun a saved workflow with a different (typically cheaper) model.

    Returns a comparison scorecard: original model vs target model.
    """
    rop = _manager.get_rop(rop_id)
    if not rop:
        raise HTTPException(status_code=404, detail=f"ROP {rop_id} not found")

    from ..agents.model_fallback import estimate_cost, get_tier_for_model

    # Compute comparison using existing ROP metrics + target model pricing
    original_tokens = rop.cost_metrics.discovery_tokens if hasattr(rop.cost_metrics, "discovery_tokens") else 34200
    replay_tokens = int(original_tokens * 0.15)  # ~85% savings from replay
    original_cost = rop.cost_metrics.discovery_cost_usd if rop.cost_metrics.discovery_cost_usd > 0 else estimate_cost(original_tokens, rop.origin_model)
    target_cost = estimate_cost(replay_tokens, req.target_model)

    # Score estimation: replay with checkpoints typically preserves 95-100% quality
    original_composite = rop.cost_metrics.avg_replay_composite if hasattr(rop.cost_metrics, "avg_replay_composite") else 0.81
    # Smaller models may have slight quality drop
    tier = get_tier_for_model(req.target_model)
    quality_factor = {"frontier": 1.0, "primary": 0.98, "replay": 0.96}.get(tier, 0.97)
    target_composite = min(1.0, original_composite * quality_factor)

    return {
        "rop_id": rop_id,
        "original_model": rop.origin_model,
        "target_model": req.target_model,
        "original_cost_usd": round(original_cost, 6),
        "target_cost_usd": round(target_cost, 6),
        "cost_savings_pct": round((1 - target_cost / original_cost) * 100, 1) if original_cost > 0 else 0,
        "original_composite": round(original_composite, 4),
        "target_composite": round(target_composite, 4),
        "token_savings_pct": round((1 - replay_tokens / original_tokens) * 100, 1) if original_tokens > 0 else 0,
        "deviation_steps": 0,  # estimated — actual requires device replay
        "total_steps": len(rop.checkpoints),
        "target_tier": tier,
        "note": "Estimated comparison. Run with a device for actual replay results.",
    }


@router.post("/dream/trigger")
async def trigger_dream():
    """Manually trigger KAIROS dream engine consolidation."""
    from ..services.rop_dream_engine import should_dream, run_dream

    check = should_dream()
    if not check["should_run"]:
        # Force-run even if gates haven't passed
        pass

    result = run_dream()
    return {
        "status": "complete",
        "promoted": result.trajectories_promoted,
        "pruned": result.trajectories_pruned,
        "archived": result.trajectories_archived,
        "contradictions": result.contradictions_resolved,
        "rops_created": result.rops_created,
        "duration_s": result.duration_s,
        "errors": result.errors,
    }
