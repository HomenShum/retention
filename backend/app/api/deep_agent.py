"""Deep Agent subagent endpoint.

Legacy endpoint preserved for backward compatibility.
Internally delegates to the AgentRegistry runner for the "subagent" agent type.

New agent types should register via the registry and use /api/agents/{name} instead.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agents.registry import AgentRegistry, AgentRunner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/deep-agent", tags=["deep-agent"])


class SubagentRequest(BaseModel):
    prompt: str
    tools: Optional[List[str]] = None
    model: str = "gpt-5.4"
    max_turns: int = 3


class SubagentResponse(BaseModel):
    text: str
    tool_calls: List[str] = []
    turns: int = 0
    tokens: Dict[str, int] = {}
    duration_ms: int = 0
    error: Optional[str] = None


@router.post("/subagent", response_model=SubagentResponse)
async def run_subagent(req: SubagentRequest) -> SubagentResponse:
    """Run a mini agent loop with codebase tools. Delegates to the AgentRegistry."""
    config = AgentRegistry.get("subagent")
    runner = AgentRunner(config)
    result = await runner.run(
        req.prompt,
        model=req.model,
        max_turns=req.max_turns,
        telemetry_interface="deep-agent-api",
        telemetry_operation="subagent",
    )
    return SubagentResponse(
        text=result.get("text", ""),
        tool_calls=result.get("tool_calls", []),
        turns=result.get("turns", 0),
        tokens=result.get("tokens", {}),
        duration_ms=result.get("duration_ms", 0),
        error=result.get("error"),
    )
