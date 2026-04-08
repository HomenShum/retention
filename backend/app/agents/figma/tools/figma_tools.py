from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx

from ....figma.client import FigmaClient
from ....figma.models import SnapshotRequest
from ....figma.service import FigmaService
from ...coordinator.context_compactor import get_full_output


_service_singleton: Optional[FigmaService] = None


def _get_service() -> FigmaService:
    global _service_singleton
    if _service_singleton is not None:
        return _service_singleton

    token = os.environ.get("FIGMA_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("FIGMA_ACCESS_TOKEN is not set")

    client = FigmaClient(access_token=token)
    _service_singleton = FigmaService(client=client)
    return _service_singleton


async def get_figma_snapshot(
    figma_url: Optional[str] = None,
    file_key: Optional[str] = None,
    level: str = "metadata",
    dimensions: Optional[list[str]] = None,
    node_ids: Optional[list[str]] = None,
) -> Dict[str, Any]:
    """Fetch a Figma snapshot via progressive disclosure.

    Returns compact results with ref_id per dimension.
    """
    service = _get_service()
    req = SnapshotRequest(
        figma_url=figma_url,
        file_key=file_key,
        level=level,
        dimensions=dimensions,
        node_ids=node_ids,
    )
    return (await service.get_snapshot(req)).model_dump()


def retrieve_figma_ref(ref_id: str) -> Any:
    """Retrieve stored (possibly large) tool output by ref_id."""
    return get_full_output(ref_id)


def create_figma_tools() -> Dict[str, Any]:
    return {
        "get_figma_snapshot": get_figma_snapshot,
        "retrieve_figma_ref": retrieve_figma_ref,
    }
