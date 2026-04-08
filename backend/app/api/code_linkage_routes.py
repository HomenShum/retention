"""Code Linkage API — exposes code-aware impact queries.

Routes:
    GET  /api/code-linkage/screen/{screen_id}    — anchors + screenshot for a screen
    GET  /api/code-linkage/workflow/{workflow_id} — anchors and screens for a workflow
    POST /api/code-linkage/impact                 — changed files → features + workflows + screens
    POST /api/code-linkage/index                  — trigger a full repo re-index
    GET  /api/code-linkage/stats                  — linkage graph stats + index status
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/code-linkage", tags=["code-linkage"])

_BACKEND_BASE_URL = os.getenv(
    "BACKEND_PUBLIC_URL", "https://retention-backend.onrender.com"
)

_CRAWL_DIR = Path(__file__).resolve().parents[2] / "data" / "exploration_memory" / "crawl"


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ImpactRequest(BaseModel):
    files_changed: List[str]
    commit_hash: str = ""


class ImpactResponse(BaseModel):
    commit: str = ""
    files_changed: List[str] = []
    affected_features: List[Dict[str, Any]] = []
    screens_to_retest: List[str] = []
    workflow_ids: List[str] = []
    suggested_reruns: List[str] = []
    confidence: str = "low"


class ScreenLinkageResponse(BaseModel):
    screen_id: str
    screen_name: str = ""
    screenshot_url: Optional[str] = None
    anchors: List[Dict[str, Any]] = []
    linked_features: List[str] = []
    linked_workflows: List[str] = []


class WorkflowLinkageResponse(BaseModel):
    workflow_id: str
    feature_ids: List[str] = []
    screen_ids: List[str] = []
    code_anchors: List[Dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _screenshot_url(screenshot_path: str) -> Optional[str]:
    if not screenshot_path or screenshot_path in ("unknown", "/tmp/screenshot.png"):
        return None
    filename = Path(screenshot_path).name
    if not filename or "." not in filename:
        return None
    return f"{_BACKEND_BASE_URL}/static/screenshots/{filename}"


def _lookup_screen_in_crawls(screen_id: str) -> Dict[str, Any]:
    """Find a screen's name and screenshot_path across all crawl files."""
    if not _CRAWL_DIR.exists():
        return {}
    import json
    for crawl_file in sorted(_CRAWL_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(crawl_file.read_text())
            for screen in data.get("crawl_data", {}).get("screens", []):
                if screen.get("screen_id") == screen_id:
                    return screen
        except Exception:
            continue
    return {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/screen/{screen_id}", response_model=ScreenLinkageResponse)
async def get_screen_linkage(screen_id: str) -> ScreenLinkageResponse:
    """Return code anchors and screenshot URL for a screen."""
    from app.agents.qa_pipeline.linkage_graph import _load_graph

    graph = _load_graph()
    screen_data = graph.get("screens", {}).get(screen_id, {})

    # Find screenshot from crawl files
    crawl_screen = _lookup_screen_in_crawls(screen_id)
    screenshot_url = _screenshot_url(crawl_screen.get("screenshot_path", ""))

    # Anchors: resolve entity_id → full anchor data
    code_symbols = graph.get("code_symbols", {})
    anchor_ids = screen_data.get("code_symbols", [])
    anchors = []
    for eid in anchor_ids:
        sym = code_symbols.get(eid, {})
        if sym:
            anchors.append({
                "entity_id": eid,
                "kind": sym.get("kind", ""),
                "file_path": sym.get("file_path", ""),
                "symbol_name": sym.get("symbol_name", ""),
                "route_path": sym.get("route_path", ""),
                "confidence": "high",  # stored entries are high-confidence
            })

    # Linked features
    linked_features_set = set(screen_data.get("features", []))
    for eid in anchor_ids:
        sym = code_symbols.get(eid, {})
        for fid in sym.get("features", []):
            linked_features_set.add(fid)
    linked_features = list(linked_features_set)

    # Linked workflows
    linked_workflows = [
        wf_id
        for wf_id, wf_data in graph.get("workflows", {}).items()
        if screen_id in wf_data.get("screen_ids", [])
    ]

    return ScreenLinkageResponse(
        screen_id=screen_id,
        screen_name=crawl_screen.get("screen_name", screen_id),
        screenshot_url=screenshot_url,
        anchors=anchors,
        linked_features=linked_features,
        linked_workflows=linked_workflows,
    )


@router.get("/workflow/{workflow_id}", response_model=WorkflowLinkageResponse)
async def get_workflow_linkage(workflow_id: str) -> WorkflowLinkageResponse:
    """Return feature/screen/anchor data for a registered workflow."""
    from app.agents.qa_pipeline.linkage_graph import _load_graph

    graph = _load_graph()
    wf = graph.get("workflows", {}).get(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found in linkage graph")

    return WorkflowLinkageResponse(
        workflow_id=workflow_id,
        feature_ids=wf.get("feature_ids", []),
        screen_ids=wf.get("screen_ids", []),
        code_anchors=wf.get("code_anchors", []),
    )


@router.post("/impact", response_model=ImpactResponse)
async def compute_impact(req: ImpactRequest) -> ImpactResponse:
    """Return which features, screens, and workflows are affected by file changes."""
    from app.agents.qa_pipeline.linkage_graph import get_workflow_rerun_suggestions

    result = get_workflow_rerun_suggestions(
        files_changed=req.files_changed,
        commit_hash=req.commit_hash,
    )
    return ImpactResponse(
        commit=result.get("commit", ""),
        files_changed=result.get("files_changed", []),
        affected_features=result.get("affected_features", []),
        screens_to_retest=result.get("screens_to_retest", []),
        workflow_ids=result.get("workflow_ids", []),
        suggested_reruns=result.get("suggested_reruns", []),
        confidence=result.get("confidence", "low"),
    )


@router.post("/index")
async def trigger_reindex() -> Dict[str, Any]:
    """Re-scan the repo and rebuild the code index. Invalidates the in-process cache."""
    from app.services.code_indexer import run_full_index, _CACHED_INDEX
    import app.services.code_indexer as _ci

    idx = run_full_index()
    _ci._CACHED_INDEX = idx  # refresh in-process cache

    return {
        "status": "ok",
        "entity_count": idx.get("entity_count", 0),
        "indexed_at": idx.get("indexed_at"),
    }


@router.get("/stats")
async def get_stats() -> Dict[str, Any]:
    """Return linkage graph stats and code index status."""
    from app.agents.qa_pipeline.linkage_graph import get_graph_stats
    from app.services.code_indexer import get_index

    graph_stats = get_graph_stats()
    idx = get_index()

    return {
        "linkage_graph": graph_stats,
        "code_index": {
            "entity_count": idx.get("entity_count", 0),
            "indexed_at": idx.get("indexed_at"),
        },
    }
