"""
Workflow Judge API — REST endpoints for the always-on completion judge.

Serves the compliance dashboard and integrates with Claude Code MCP tools.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/judge", tags=["workflow-judge"])


# ── Request models ──────────────────────────────────────────────

class JudgeCheckRequest(BaseModel):
    workflow_id: str = Field("", description="Workflow ID (empty = auto-detect)")
    prompt: str = Field("", description="User prompt for auto-detection")
    project_path: str = Field("", description="Project path for session lookup")


class CorrectionRequest(BaseModel):
    text: str = Field(..., description="The correction text (e.g. 'you forgot the search')")
    workflow_id: str = Field("", description="Which workflow this relates to")


class JudgeLLMRequest(BaseModel):
    task_description: str = Field(..., description="What the task was")
    frontier_output: str = Field("", description="What the full run produced")
    replay_output: str = Field("", description="What the replay produced")
    workflow_family: str = Field("CSP", description="Workflow family for judge calibration")


# ── Endpoints ───────────────────────────────────────────────────

@router.post("/check")
async def judge_check(request: JudgeCheckRequest):
    """Judge the current Claude Code session against a workflow.

    Reads real tool call data from ~/.claude session files.
    """
    from app.services.workflow_judge.mcp_tools import tool_judge_check

    return tool_judge_check(
        workflow_id=request.workflow_id,
        prompt=request.prompt,
        project_path=request.project_path,
    )


@router.get("/status")
async def judge_status(project_path: str = ""):
    """Quick session status — tool calls, capabilities, files touched."""
    from app.services.workflow_judge.mcp_tools import tool_judge_status

    return tool_judge_status(project_path)


@router.post("/detect")
async def judge_detect(prompt: str = ""):
    """Detect which workflow a prompt maps to."""
    from app.services.workflow_judge.mcp_tools import tool_judge_detect

    return tool_judge_detect(prompt)


@router.get("/workflows")
async def list_workflows():
    """List all available retained workflows."""
    from app.services.workflow_judge.mcp_tools import tool_judge_workflows

    return tool_judge_workflows()


@router.post("/correction")
async def record_correction(request: CorrectionRequest):
    """Record a user correction ('you forgot X')."""
    from app.services.workflow_judge.mcp_tools import tool_judge_correction

    return tool_judge_correction(request.text, request.workflow_id)


@router.get("/corrections/analyze")
async def analyze_corrections():
    """Analyze correction patterns — find systematic gaps."""
    from app.services.workflow_judge.mcp_tools import tool_judge_analyze_corrections

    return tool_judge_analyze_corrections()


@router.get("/sessions")
async def list_sessions(project_path: str = "", limit: int = 10):
    """List recent Claude Code sessions."""
    from app.services.workflow_judge.session_reader import list_sessions as _list

    return {"sessions": _list(project_path, limit)}


@router.post("/llm-judge")
async def run_llm_judge(request: JudgeLLMRequest):
    """Run the REAL structured LLM judge (makes API call).

    This is the truth-governed judge — costs real tokens.
    Use for final verification, not iteration.
    """
    from app.benchmarks.rerun_eval import judge_replay

    result = await judge_replay(
        task_description=request.task_description,
        frontier_output=request.frontier_output,
        replay_output=request.replay_output,
        workflow_family=request.workflow_family,
    )
    return result


# ── Diff Analysis (watchdog layer) ──────────────────────────────

@router.get("/diff")
async def get_quality_diff(window: str = "week", project_path: str = ""):
    """Analyze workflow quality diff across a time window.

    Cheap watchdog: signals when frontier model intervention is needed.
    Large models generate. This layer signals.
    """
    from app.services.workflow_judge.diff_analyzer import analyze_diff

    return analyze_diff(window, project_path)


@router.get("/timeline")
async def get_quality_timeline(days: int = 30):
    """Get workflow quality timeline for dashboard charting."""
    from app.services.workflow_judge.diff_analyzer import get_quality_timeline as _get

    return {"timeline": _get(days)}
