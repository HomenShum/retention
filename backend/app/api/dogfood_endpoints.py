"""Dogfood API endpoints — exposes self-QA data for the DogfoodProof component.

GET  /api/dogfood/latest   — latest run summary
GET  /api/dogfood/trends   — time-series over 30 days
GET  /api/dogfood/savings  — memory savings stats
GET  /api/dogfood/proof    — curated proof bundle for landing page
POST /api/dogfood/run      — trigger a dogfood run (auth required)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.agents.qa_pipeline.dogfood_tracker import (
    get_savings_proof,
    get_trends,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dogfood", tags=["dogfood"])


@router.get("/latest")
async def dogfood_latest():
    """Latest dogfood run summary."""
    proof = get_savings_proof()
    latest = proof.get("latest_run")
    if not latest:
        raise HTTPException(status_code=404, detail="No dogfood runs yet")
    return latest


@router.get("/trends")
async def dogfood_trends(days: int = 30):
    """Time-series trend data over N days."""
    return get_trends(days=days)


@router.get("/savings")
async def dogfood_savings():
    """Memory savings statistics."""
    proof = get_savings_proof()
    return {
        "total_tokens_saved": proof["total_tokens_saved"],
        "cache_hit_rate": proof["cache_hit_rate"],
        "total_runs": proof["total_runs"],
    }


@router.get("/proof")
async def dogfood_proof():
    """Curated proof bundle for the DogfoodProof landing page component."""
    return get_savings_proof()
