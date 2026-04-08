"""ActionSpan API router.

Exposes the ActionSpan verification engine over HTTP so:
  - Agent code can programmatically open/score spans.
  - Frontend dashboards can poll evidence manifests.
  - External tools (OpenClaw, Cursor MCP) can retrieve verification receipts.

Endpoints:
  POST /action-spans/start              → begin capturing a span
  POST /action-spans/{span_id}/score    → stop + score the span
  GET  /action-spans/{span_id}          → retrieve a single span
  GET  /action-spans                    → list spans for a session
  GET  /action-spans/manifest/{session_id} → session-level evidence roll-up
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from ..agents.device_testing.action_span_models import (
    StartSpanRequest,
    StartSpanResponse,
    ScoreSpanRequest,
    ScoreSpanResponse,
    ListSpansResponse,
    ActionSpan,
    ActionSpanManifest,
)
from ..agents.device_testing.action_span_service import action_span_service

router = APIRouter(prefix="/action-spans", tags=["action-spans"])


@router.post("/start", response_model=StartSpanResponse, summary="Begin ActionSpan capture")
async def start_span(req: StartSpanRequest) -> StartSpanResponse:
    """Start recording a 2-3s verification clip for the current agent action.

    Call this **before** executing the action; call `/score` immediately after.
    """
    try:
        return action_span_service.start_span(req)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{span_id}/score", response_model=ScoreSpanResponse, summary="Score a completed span")
async def score_span(span_id: str, req: ScoreSpanRequest) -> ScoreSpanResponse:
    """Stop the recording, extract frames, compute scores, and mark the span as verified/failed.

    Returns the full `ActionSpan` with composite_score, pass/fail verdict, and rationale.
    """
    req.span_id = span_id
    try:
        span = action_span_service.score_span(req)
        return ScoreSpanResponse(span=span, manifest_updated=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/manifest/{session_id}", response_model=ActionSpanManifest, summary="Evidence manifest for a session")
async def get_manifest(session_id: str) -> ActionSpanManifest:
    """Return the session-level evidence roll-up: pass rate, average scores, all spans."""
    return action_span_service.get_manifest(session_id)


@router.get("/{span_id}", response_model=ActionSpan, summary="Retrieve a single ActionSpan")
async def get_span(span_id: str) -> ActionSpan:
    """Fetch a specific ActionSpan by ID."""
    span = action_span_service.get_span(span_id)
    if not span:
        raise HTTPException(status_code=404, detail=f"Span not found: {span_id}")
    return span


@router.get("", response_model=ListSpansResponse, summary="List spans for a session")
async def list_spans(session_id: str = Query(..., description="Session ID to filter spans")) -> ListSpansResponse:
    """List all ActionSpans for a given test session, plus the manifest."""
    spans = action_span_service.list_spans(session_id)
    manifest = action_span_service.get_manifest(session_id)
    return ListSpansResponse(session_id=session_id, spans=spans, manifest=manifest)

