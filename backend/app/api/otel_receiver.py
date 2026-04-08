"""OTEL Receiver — accept OpenTelemetry traces from any instrumented framework.

POST /api/otel/v1/traces  — OTLP JSON format

Parses resourceSpans → scopeSpans → spans, extracts gen_ai tool call spans,
writes to ~/.retention/activity.jsonl in normalized format.

Covers: PydanticAI, AG2/AutoGen, Haystack, Semantic Kernel, LangChain (via OTEL bridge).
Developer sets one env var: OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:8000/api/otel
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/otel", tags=["otel"])

_RETENTION_BUFFER = Path.home() / ".retention" / "activity.jsonl"


def _extract_attr(attributes: list[dict], key: str) -> Any:
    """Extract a value from OTLP attribute list [{key, value}]."""
    for attr in attributes:
        if attr.get("key") == key:
            val = attr.get("value", {})
            # OTLP values are typed: stringValue, intValue, doubleValue, etc.
            return (
                val.get("stringValue")
                or val.get("intValue")
                or val.get("doubleValue")
                or val.get("boolValue")
                or val.get("arrayValue")
                or ""
            )
    return None


def _nano_to_ms(start_ns: str | int, end_ns: str | int) -> int:
    """Convert nanosecond timestamps to duration in milliseconds."""
    try:
        return int((int(end_ns) - int(start_ns)) / 1_000_000)
    except (ValueError, TypeError):
        return 0


def _nano_to_iso(ns: str | int) -> str:
    """Convert nanosecond timestamp to ISO 8601."""
    try:
        ts = int(ns) / 1_000_000_000
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return datetime.now(timezone.utc).isoformat()


@router.post("/v1/traces")
async def receive_traces(request: Request) -> JSONResponse:
    """Accept OTLP JSON traces and extract tool call events.

    OTLP JSON format:
    {
      "resourceSpans": [{
        "resource": { "attributes": [...] },
        "scopeSpans": [{
          "spans": [{
            "name": "...",
            "kind": 1,
            "startTimeUnixNano": "...",
            "endTimeUnixNano": "...",
            "attributes": [
              {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
              {"key": "gen_ai.tool.name", "value": {"stringValue": "search_companies"}},
              {"key": "gen_ai.usage.input_tokens", "value": {"intValue": 1200}},
              ...
            ]
          }]
        }]
      }]
    }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    resource_spans = body.get("resourceSpans", [])
    if not resource_spans:
        return JSONResponse({"accepted": 0, "reason": "no resourceSpans"})

    events_written = 0
    _RETENTION_BUFFER.parent.mkdir(parents=True, exist_ok=True)

    try:
        with _RETENTION_BUFFER.open("a", encoding="utf-8") as f:
            for rs in resource_spans:
                # Extract service name from resource attributes
                resource_attrs = rs.get("resource", {}).get("attributes", [])
                service_name = _extract_attr(resource_attrs, "service.name") or "otel-unknown"

                for ss in rs.get("scopeSpans", []):
                    for span in ss.get("spans", []):
                        attrs = span.get("attributes", [])

                        # Check if this is a tool execution span
                        op_name = _extract_attr(attrs, "gen_ai.operation.name")
                        tool_name = _extract_attr(attrs, "gen_ai.tool.name")

                        # Accept tool execution spans OR function spans
                        if not tool_name:
                            # Try alternative attribute names
                            tool_name = (
                                _extract_attr(attrs, "tool.name")
                                or _extract_attr(attrs, "function.name")
                            )
                        if not tool_name:
                            # Use span name as fallback for non-annotated spans
                            span_name = span.get("name", "")
                            if op_name == "execute_tool" or "tool" in span_name.lower():
                                tool_name = span_name
                            else:
                                continue  # Not a tool call span

                        # Extract timing
                        start_ns = span.get("startTimeUnixNano", "0")
                        end_ns = span.get("endTimeUnixNano", "0")
                        duration_ms = _nano_to_ms(start_ns, end_ns)

                        # Extract token counts
                        tokens_in = _extract_attr(attrs, "gen_ai.usage.input_tokens") or 0
                        tokens_out = _extract_attr(attrs, "gen_ai.usage.output_tokens") or 0

                        # Extract model
                        model = (
                            _extract_attr(attrs, "gen_ai.response.model")
                            or _extract_attr(attrs, "gen_ai.request.model")
                            or ""
                        )

                        # Extract tool parameters (scrubbed — keys only)
                        params_raw = _extract_attr(attrs, "gen_ai.tool.parameters")
                        tool_input: dict[str, str] = {}
                        if params_raw:
                            try:
                                parsed = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
                                if isinstance(parsed, dict):
                                    tool_input = {k: f"[{len(str(v))}c]" for k, v in parsed.items()}
                            except (json.JSONDecodeError, TypeError):
                                pass

                        event = {
                            "ts": _nano_to_iso(start_ns),
                            "source": f"otel:{service_name}",
                            "session_id": span.get("traceId", ""),
                            "tool_name": tool_name,
                            "tool_input": tool_input,
                            "status": "ok" if span.get("status", {}).get("code", 0) != 2 else "error",
                            "duration_ms": duration_ms,
                            "tokens_in": int(tokens_in) if tokens_in else 0,
                            "tokens_out": int(tokens_out) if tokens_out else 0,
                            "model": str(model),
                        }

                        f.write(json.dumps(event, default=str) + "\n")
                        events_written += 1

    except OSError as e:
        logger.warning("Failed to write OTEL traces to retention buffer: %s", e)
        return JSONResponse({"error": "buffer write failed"}, status_code=500)

    # Invalidate analyzer cache
    try:
        from ..services.tool_call_analyzer import _cache
        _cache.clear()
    except Exception:
        pass

    return JSONResponse({"accepted": events_written})
