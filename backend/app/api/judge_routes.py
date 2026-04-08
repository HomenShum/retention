"""
Workflow Judge API routes — hook endpoints for Claude Code integration.

These endpoints are called by Claude Code hooks:
  POST /api/judge/on-prompt  — UserPromptSubmit hook
  POST /api/judge/on-tool-use — PostToolUse hook
  POST /api/judge/on-stop     — Stop hook
  POST /api/judge/on-session-start — SessionStart hook
  GET  /api/judge/status      — Current session status
  GET  /api/judge/settings    — Generate hook settings for .claude/settings.json
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..services.workflow_judge.hooks import (
    generate_hook_settings,
    on_prompt_submit,
    on_session_start,
    on_stop,
    on_tool_use,
    get_or_create_session,
)

router = APIRouter(prefix="/api/judge", tags=["workflow-judge"])


class PromptRequest(BaseModel):
    """Claude Code UserPromptSubmit sends: {"prompt": "..."}"""
    model_config = ConfigDict(populate_by_name=True)
    prompt: str
    session_id: str = ""


class ToolUseRequest(BaseModel):
    """Claude Code PostToolUse sends: {"tool_name": "...", "input": {...}}"""
    model_config = ConfigDict(populate_by_name=True)
    tool_name: str
    tool_input: Dict[str, Any] = Field(default={}, alias="input")
    session_id: str = ""


class SessionRequest(BaseModel):
    session_id: str = ""


@router.post("/on-prompt")
async def hook_on_prompt(req: PromptRequest) -> Dict[str, Any]:
    """Called by UserPromptSubmit hook — detect workflow and inject context."""
    return on_prompt_submit(req.prompt, req.session_id)


@router.post("/on-tool-use")
async def hook_on_tool_use(req: ToolUseRequest) -> Dict[str, Any]:
    """Called by PostToolUse hook — update evidence and check progress."""
    return on_tool_use(req.tool_name, req.tool_input, req.session_id)


@router.post("/on-stop")
async def hook_on_stop(req: Optional[SessionRequest] = None) -> Dict[str, Any]:
    """Called by Stop hook — run completion judge, decide whether to block."""
    session_id = req.session_id if req else ""
    return on_stop(session_id)


@router.post("/on-session-start")
async def hook_on_session_start(req: Optional[SessionRequest] = None) -> Dict[str, Any]:
    """Called by SessionStart hook — hydrate retained workflow state."""
    session_id = req.session_id if req else ""
    return on_session_start(session_id)


@router.get("/status")
async def get_judge_status() -> Dict[str, Any]:
    """Get current workflow session status."""
    session = get_or_create_session()
    return {
        "session_id": session.session_id,
        "workflow_id": session.workflow_id,
        "tool_call_count": len(session.tool_calls),
        "prompt": session.prompt[:200] if session.prompt else "",
        "verdict": session.verdict.verdict if session.verdict else None,
        "started_at": session.started_at,
    }


@router.get("/settings")
async def get_hook_settings(api_base: str = "http://localhost:8000") -> Dict[str, Any]:
    """Generate Claude Code settings.json hook configuration."""
    return generate_hook_settings(api_base)
