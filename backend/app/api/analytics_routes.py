"""Agent Analytics API — tool call analysis + ingest endpoints."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from typing import Any

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"])

_RETENTION_BUFFER = Path.home() / ".retention" / "activity.jsonl"


@router.get("/tool-calls")
async def get_tool_calls(days: int = Query(7, ge=1, le=90)) -> dict[str, Any]:
    """Full tool call analysis — frequency, cost, duplicates, patterns, sessions."""
    from ..services.tool_call_analyzer import analyze_tool_calls
    return analyze_tool_calls(days)


@router.get("/summary")
async def get_summary(days: int = Query(7, ge=1, le=90)) -> dict[str, Any]:
    """Compact summary for hero metrics."""
    from ..services.tool_call_analyzer import analyze_tool_calls
    data = analyze_tool_calls(days)
    freq = data.get("tool_frequency", {})
    cost = data.get("tool_cost", {})
    top_tools = list(freq.items())[:10]
    top_cost = list(cost.items())[:10]
    return {
        "totals": data.get("totals", {}),
        "savings_estimate": data.get("savings_estimate", {}),
        "top_tools_by_frequency": top_tools,
        "top_tools_by_cost": top_cost,
        "is_demo": data.get("is_demo", False),
    }


@router.get("/patterns")
async def get_patterns(days: int = Query(7, ge=1, le=90)) -> dict[str, Any]:
    """Repeated tool call sequences."""
    from ..services.tool_call_analyzer import analyze_tool_calls
    data = analyze_tool_calls(days)
    return {
        "patterns": data.get("patterns", []),
        "duplicates": data.get("duplicates", []),
        "totals": data.get("totals", {}),
    }


@router.get("/external-apis")
async def get_external_apis(days: int = Query(7, ge=1, le=90)) -> dict[str, Any]:
    """External API cost breakdown."""
    from ..services.tool_call_analyzer import analyze_tool_calls
    data = analyze_tool_calls(days)
    apis = data.get("external_apis", [])
    by_cat: dict[str, dict] = {}
    for api in apis:
        cat = api.get("category", "other")
        if cat not in by_cat:
            by_cat[cat] = {"category": cat, "tools": 0, "total_calls": 0, "total_cost_usd": 0.0}
        by_cat[cat]["tools"] += 1
        by_cat[cat]["total_calls"] += api.get("count", 0)
        by_cat[cat]["total_cost_usd"] = round(by_cat[cat]["total_cost_usd"] + api.get("total_cost_usd", 0), 4)
    return {
        "by_tool": apis,
        "by_category": sorted(by_cat.values(), key=lambda x: -x["total_cost_usd"]),
        "totals": data.get("totals", {}),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Divergence analysis — trajectory health and replay stability
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/divergence")
async def get_divergence() -> dict[str, Any]:
    """Trajectory divergence analysis — health grades, unstable steps, confidence scores."""
    from ..services.divergence_analyzer import analyze_divergence
    return analyze_divergence()


# ─────────────────────────────────────────────────────────────────────────────
# Ingest endpoint — accept events from retention-sh SDK and external apps
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/ingest")
async def ingest_event(request: Request) -> JSONResponse:
    """Accept a tool call event from the retention-sh SDK or any external source.

    POST /api/analytics/ingest
    Content-Type: application/json

    Body: single event or array of events. Each event should have at minimum:
      { "tool_name": "...", "ts": "..." }

    Optional fields: source, session_id, project, tool_input, tool_output_preview,
    status, duration_ms, tokens_in, tokens_out, model, cost_usd, metadata.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    # Accept single event or array
    events = body if isinstance(body, list) else [body]
    written = 0

    _RETENTION_BUFFER.parent.mkdir(parents=True, exist_ok=True)

    try:
        with _RETENTION_BUFFER.open("a", encoding="utf-8") as f:
            for event in events:
                if not isinstance(event, dict):
                    continue
                if "tool_name" not in event:
                    continue

                # Ensure timestamp
                if "ts" not in event:
                    event["ts"] = datetime.now(timezone.utc).isoformat()

                # Ensure source
                if "source" not in event:
                    event["source"] = "external-api"

                f.write(json.dumps(event, default=str) + "\n")
                written += 1
    except OSError as e:
        logger.warning("Failed to write to retention buffer: %s", e)
        return JSONResponse({"error": "buffer write failed"}, status_code=500)

    # Invalidate analyzer cache so next read picks up new data
    from ..services.tool_call_analyzer import _cache
    _cache.clear()

    return JSONResponse({"accepted": written, "buffer": str(_RETENTION_BUFFER)})
