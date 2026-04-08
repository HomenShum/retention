"""
Self-Test Runner API

Two modes:
  - fast (default): Direct Playwright — deterministic, no AI, instant results
  - agent: Adaptive AI agent — uses LLM to prioritize what to test

Both stream SSE events in the same format so the frontend needs no changes.
"""

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..agents.self_testing.playwright_engine import (
    pw_batch_test,
    subscribe_screenshots,
    unsubscribe_screenshots,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/self-test", tags=["self-test"])

# Optional: AI agent service for agent mode
_ai_agent_service = None


def set_self_test_service(service) -> None:
    global _ai_agent_service
    _ai_agent_service = service


class SelfTestRequest(BaseModel):
    url: str
    mode: str = "fast"  # "fast" or "agent"
    device_id: Optional[str] = None
    max_interactions: int = 15


def _sse(event: str, data: dict) -> dict:
    return {"event": event, "data": json.dumps(data)}


# ── Fast mode: direct Playwright ──────────────────────────────

async def _run_fast(url: str, max_interactions: int = 15) -> AsyncGenerator[dict, None]:
    """Run deterministic Playwright test, yield SSE events."""
    yield _sse("message", {"content": f"Starting self-test on {url}...\n"})

    try:
        result = await pw_batch_test(url, max_interactions=max_interactions)
    except Exception as e:
        yield _sse("error", {"error": str(e)})
        yield _sse("done", {"status": "error"})
        return

    if result.get("error"):
        yield _sse("error", {"error": result["error"]})
        yield _sse("done", {"status": "error"})
        return

    phases = result.get("phases", {})

    # Discover
    discovery = phases.get("discover", {})
    yield _sse("message", {"content": f"Navigating to {url}...\nPage loaded: \"{discovery.get('title', '')}\"\n"})
    yield _sse("tool_call", {
        "tool_name": "discover_app_screens",
        "arguments": json.dumps({"url": url, "crawl_depth": 1}),
        "output": json.dumps({
            "pages_found": discovery.get("pages_found", 0),
            "total_interactions": discovery.get("total_interactions", 0),
            "pages": {p: {"element_count": d.get("element_count", 0)} for p, d in discovery.get("pages", {}).items()},
            "suggested_test_plan": discovery.get("suggested_test_plan", []),
        }),
    })

    # Test results
    test_phase = phases.get("test", {})
    tests = test_phase.get("test_results", [])
    if tests:
        yield _sse("message", {"content": f"\nTesting top {len(tests)} interactions...\n"})
    for tr in tests:
        yield _sse("tool_call", {
            "tool_name": "execute_test_on_emulator",
            "arguments": json.dumps({"action": tr.get("action", ""), "element_text": tr.get("element", "")}),
            "output": json.dumps(tr),
        })

    # Detect
    detect = phases.get("detect", {})
    anomaly_count = detect.get("anomaly_count", 0)
    yield _sse("message", {"content": f"\nAnalyzing results: {len(tests)} tests, {anomaly_count} anomalies...\n"})
    yield _sse("tool_call", {
        "tool_name": "detect_anomalies",
        "arguments": json.dumps({"tests_run": len(tests)}),
        "output": json.dumps(detect),
    })

    # Trace
    trace = phases.get("trace", {})
    if trace and trace.get("total_matches"):
        yield _sse("message", {"content": "\nTracing issues to source code...\n"})
        yield _sse("tool_call", {
            "tool_name": "trace_to_source",
            "arguments": json.dumps({"search_terms": trace.get("search_terms", [])}),
            "output": json.dumps(trace),
        })

    # Suggest
    suggestions = phases.get("suggest", {}).get("suggestions", [])
    if suggestions:
        yield _sse("message", {"content": "\nGenerating fix suggestions...\n"})
        for s in suggestions:
            yield _sse("tool_call", {
                "tool_name": "suggest_fix_and_test",
                "arguments": json.dumps({"anomaly": s.get("anomaly", "")}),
                "output": json.dumps(s),
            })
    elif anomaly_count == 0:
        yield _sse("message", {"content": "\nNo anomalies detected — all tested interactions passed!\n"})

    # Summary
    summary = result.get("summary", {})
    yield _sse("message", {
        "content": (
            f"\n--- Self-Test Complete ---\n"
            f"Pages discovered: {summary.get('pages_found', 0)}\n"
            f"Interactions tested: {summary.get('interactions_tested', 0)}\n"
            f"Anomalies found: {summary.get('anomalies_found', 0)}\n"
            f"Console errors: {summary.get('console_errors', 0)}\n"
        )
    })
    yield _sse("done", {"status": "completed"})


# ── Agent mode: AI-driven adaptive testing ────────────────────

async def _run_agent(url: str, max_interactions: int = 15) -> AsyncGenerator[dict, None]:
    """Run adaptive AI agent, yield SSE events."""
    if not _ai_agent_service:
        yield _sse("error", {"error": "AI Agent Service not initialized. Use mode=fast instead."})
        yield _sse("done", {"status": "error"})
        return

    from ..agents.coordinator.coordinator_service import ChatMessage

    prompt = (
        f"Run the self-testing flywheel on {url}. "
        f"Use the adaptive testing loop: discover all screens, prioritize by risk, "
        f"test the top {max_interactions} interactions, detect anomalies, trace to source code, "
        f"and suggest fixes. Adapt your testing based on what you find — "
        f"go deeper on buggy pages, skip clean ones."
    )

    messages = [ChatMessage(role="user", content=prompt)]

    try:
        async for evt in _ai_agent_service.chat_stream(messages, None, None):
            if isinstance(evt, dict):
                evt_type = evt.get("type", "message")
                if evt_type == "content":
                    yield _sse("message", {"content": evt.get("content", "")})
                elif evt_type == "tool_call":
                    yield _sse("tool_call", {
                        "tool_name": evt.get("tool_name", ""),
                        "arguments": evt.get("arguments", ""),
                        "output": evt.get("output", ""),
                    })
                elif evt_type == "handoff":
                    yield _sse("handoff", {
                        "from_agent": evt.get("from_agent", ""),
                        "to_agent": evt.get("to_agent", ""),
                    })
                elif evt_type == "error":
                    yield _sse("error", {"error": evt.get("content", "Unknown error")})
                elif evt_type == "final":
                    yield _sse("done", {"status": "completed"})
                else:
                    yield _sse(evt_type, evt)
    except Exception as e:
        logger.error("Agent self-test error: %s", e)
        yield _sse("error", {"error": str(e)})

    yield _sse("done", {"status": "completed"})


# ── Endpoint ──────────────────────────────────────────────────

@router.post("/stream")
async def stream_self_test(request: SelfTestRequest):
    """Stream self-test results via SSE. mode=fast (default) or mode=agent."""
    if request.mode == "agent":
        return EventSourceResponse(_run_agent(request.url, request.max_interactions))
    return EventSourceResponse(_run_fast(request.url, request.max_interactions))


@router.websocket("/screen")
async def screen_stream(websocket: WebSocket):
    """WebSocket endpoint that streams live Playwright screenshots as base64 JPEG.

    Connect while a self-test is running to receive frames in real-time.
    Frames are ~20-50KB JPEG at quality=40, sent as text messages.
    """
    await websocket.accept()
    q = subscribe_screenshots()
    try:
        while True:
            try:
                frame = await asyncio.wait_for(q.get(), timeout=30.0)
                await websocket.send_text(frame)
            except asyncio.TimeoutError:
                # Send keepalive ping
                await websocket.send_text("ping")
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        unsubscribe_screenshots(q)
