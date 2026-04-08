"""Registry-driven agent endpoints.

Serves any registered agent at POST /api/agents/{agent_name}.
New agent types just need to register an AgentConfig — no new route code needed.
"""

import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from ..agents.registry import AgentRegistry, AgentRunner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/", summary="List registered agent types")
async def list_agents() -> Dict[str, Any]:
    """Return all registered agent names."""
    return {"agents": AgentRegistry.list_agents()}


@router.post("/{agent_name}", summary="Run a registered agent")
async def run_agent(agent_name: str, req: Dict[str, Any]) -> Dict[str, Any]:
    """Generic endpoint — runs any registered agent by name."""
    try:
        config = AgentRegistry.get(agent_name)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown agent: {agent_name!r}. Available: {AgentRegistry.list_agents()}",
        )

    question = req.get("question") or req.get("prompt") or ""
    if not question:
        raise HTTPException(status_code=400, detail="'question' or 'prompt' field is required")

    kwargs = {k: v for k, v in req.items() if k not in ("question", "prompt", "context")}
    context = req.get("context", [])  # Prior conversation messages for thread follow-ups

    runner = AgentRunner(config)
    result = await runner.run(
        question,
        context=context,
        telemetry_interface="agent-api",
        telemetry_operation=agent_name,
        **kwargs,
    )
    return result


@router.post("/{agent_name}/stream", summary="Run a registered agent with SSE streaming")
async def run_agent_stream(agent_name: str, req: Dict[str, Any]):
    """Streaming endpoint — yields SSE events for each tool call and the final result.

    Events:
      event: status     — progress messages (strategy selected, turn N, etc.)
      event: tool_start — a tool call is about to execute
      event: tool_done  — a tool call completed with a summary
      event: done       — final result (same shape as non-streaming response)
      event: error      — something went wrong
    """
    try:
        config = AgentRegistry.get(agent_name)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown agent: {agent_name!r}. Available: {AgentRegistry.list_agents()}",
        )

    question = req.get("question") or req.get("prompt") or ""
    if not question:
        raise HTTPException(status_code=400, detail="'question' or 'prompt' field is required")

    kwargs = {k: v for k, v in req.items() if k not in ("question", "prompt", "context")}
    context = req.get("context", [])  # Prior conversation messages for thread follow-ups

    runner = AgentRunner(config)

    async def event_generator():
        async for evt in runner.run_streaming(
            question,
            context=context,
            telemetry_interface="agent-api-stream",
            telemetry_operation=agent_name,
            **kwargs,
        ):
            event_type = evt.get("event", "status")
            data = evt.get("data", {})
            yield {
                "event": event_type,
                "data": json.dumps(data, default=str),
            }

    return EventSourceResponse(event_generator())
