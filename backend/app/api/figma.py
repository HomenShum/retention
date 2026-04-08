"""Figma API Router.

This exposes a thin HTTP API for fetching Figma "snapshots" via progressive disclosure.
Large payloads are stored in the in-memory context compactor storage and returned by ref_id.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from typing import Any, Dict
import json

from ..agents.coordinator.context_compactor import get_full_output, get_storage_info
from ..figma.models import SnapshotRequest, SnapshotResponse
from ..figma.service import FigmaService


router = APIRouter(prefix="/api/figma", tags=["figma"])

_figma_service: FigmaService | None = None


def set_figma_service(service: FigmaService | None) -> None:
    global _figma_service
    _figma_service = service


def get_figma_service() -> FigmaService:
    if _figma_service is None:
        raise HTTPException(
            status_code=503,
            detail="Figma service not configured. Set FIGMA_ACCESS_TOKEN on the backend.",
        )
    return _figma_service


@router.post("/snapshot", response_model=SnapshotResponse)
async def snapshot(req: SnapshotRequest) -> SnapshotResponse:
    service = get_figma_service()
    try:
        return await service.get_snapshot(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/refs/{ref_id}")
async def get_ref(ref_id: str) -> Dict[str, Any]:
    content = get_full_output(ref_id)
    if content is None:
        raise HTTPException(status_code=404, detail="ref_id not found")

    info = get_storage_info(ref_id) or {}
    # Attempt to decode JSON payloads back into objects.
    try:
        parsed = json.loads(content)
    except Exception:
        parsed = content

    return {"ref_id": ref_id, "info": info, "content": parsed}
