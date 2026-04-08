"""API routes for the three-level perception system + cockpit shell.

Provides REST endpoints for:
  - Cockpit session lifecycle (create, get, delete)
  - Level switching (on-page / browser / OS)
  - Per-level state updates (surfaces, tabs, windows)
  - Agent presence management
  - Approval gates
  - Tool receipts
  - Full cockpit snapshots for the frontend shell
"""

from fastapi import APIRouter, HTTPException, Body
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import logging

from ..perception import (
    PerceptionLevel,
    CockpitState,
    SurfaceState,
    PerceptionRegistry,
)
from ..perception.models import (
    AgentPhase,
    TabInfo,
    WindowInfo,
    WorkspaceEntry,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/perception", tags=["perception"])


# ============================================================================
# Request models
# ============================================================================

class CreateSessionResponse(BaseModel):
    session_id: str
    active_level: str
    started_at: str


class SwitchLevelRequest(BaseModel):
    level: PerceptionLevel


class UpdateSurfacesRequest(BaseModel):
    surfaces: List[SurfaceState]
    active_surface_id: Optional[str] = None


class SelectEntityRequest(BaseModel):
    entity: Dict[str, Any]
    entity_type: str


class UpdateTabsRequest(BaseModel):
    tabs: List[TabInfo]
    active_tab_id: Optional[str] = None


class UpdatePageStateRequest(BaseModel):
    url: str
    title: str
    screenshot_b64: Optional[str] = None
    dom_snapshot: Optional[str] = None


class UpdateWindowsRequest(BaseModel):
    windows: List[WindowInfo]
    foreground_window_id: Optional[str] = None


class SetOSPermissionsRequest(BaseModel):
    screen_capture: Optional[bool] = None
    input_control: Optional[bool] = None
    cross_app: Optional[bool] = None


class RegisterAgentRequest(BaseModel):
    agent_id: str
    name: str
    perception_level: PerceptionLevel


class UpdateAgentPhaseRequest(BaseModel):
    phase: AgentPhase
    confidence: Optional[float] = None


class CreateApprovalRequest(BaseModel):
    agent_id: str
    perception_level: PerceptionLevel
    action_description: str
    risk_level: str = "medium"


class ResolveApprovalRequest(BaseModel):
    approved: bool


class AddReceiptRequest(BaseModel):
    agent_id: str
    tool_name: str
    perception_level: PerceptionLevel
    input_summary: str = ""
    output_summary: str = ""
    status: str = "success"
    duration_ms: int = 0


class SetWorkspacesRequest(BaseModel):
    workspaces: List[WorkspaceEntry]
    active_workspace_id: Optional[str] = None


# ============================================================================
# Session lifecycle
# ============================================================================

@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session():
    """Create a new cockpit session."""
    state = PerceptionRegistry.create_session()
    return CreateSessionResponse(
        session_id=state.session_id,
        active_level=state.active_level.value,
        started_at=state.started_at,
    )


@router.get("/sessions")
async def list_sessions():
    """List all active cockpit sessions."""
    session_ids = PerceptionRegistry.list_sessions()
    sessions = []
    for sid in session_ids:
        state = PerceptionRegistry.get_session(sid)
        if state:
            sessions.append({
                "session_id": sid,
                "active_level": state.active_level.value,
                "agent_count": len(state.agents),
                "started_at": state.started_at,
                "last_updated": state.last_updated,
            })
    return {"sessions": sessions, "count": len(sessions)}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get full cockpit snapshot for a session."""
    snapshot = PerceptionRegistry.get_snapshot(session_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return snapshot


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a cockpit session."""
    if not PerceptionRegistry.delete_session(session_id):
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return {"deleted": True, "session_id": session_id}


# ============================================================================
# Level switching
# ============================================================================

@router.post("/sessions/{session_id}/level")
async def switch_level(session_id: str, request: SwitchLevelRequest):
    """Switch the active perception level."""
    state = PerceptionRegistry.set_active_level(session_id, request.level)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return {"session_id": session_id, "active_level": state.active_level.value}


# ============================================================================
# Level 1: On-Page
# ============================================================================

@router.post("/sessions/{session_id}/on-page/surfaces")
async def update_surfaces(session_id: str, request: UpdateSurfacesRequest):
    """Update the surface registry for on-page context."""
    ctx = PerceptionRegistry.update_surfaces(
        session_id, request.surfaces, request.active_surface_id
    )
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return ctx.model_dump(exclude_none=True)


@router.post("/sessions/{session_id}/on-page/entity")
async def select_entity(session_id: str, request: SelectEntityRequest):
    """Set the selected entity in on-page context."""
    ctx = PerceptionRegistry.set_selected_entity(
        session_id, request.entity, request.entity_type
    )
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return ctx.model_dump(exclude_none=True)


@router.get("/sessions/{session_id}/on-page")
async def get_on_page_context(session_id: str):
    """Get current on-page context."""
    state = PerceptionRegistry.get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return state.on_page.model_dump(exclude_none=True)


# ============================================================================
# Level 2: Browser
# ============================================================================

@router.post("/sessions/{session_id}/browser/tabs")
async def update_tabs(session_id: str, request: UpdateTabsRequest):
    """Update browser tab state."""
    ctx = PerceptionRegistry.update_tabs(
        session_id, request.tabs, request.active_tab_id
    )
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return ctx.model_dump(exclude_none=True)


@router.post("/sessions/{session_id}/browser/page")
async def update_page_state(session_id: str, request: UpdatePageStateRequest):
    """Update current page state (URL, title, screenshot, DOM)."""
    ctx = PerceptionRegistry.update_page_state(
        session_id,
        url=request.url,
        title=request.title,
        screenshot_b64=request.screenshot_b64,
        dom_snapshot=request.dom_snapshot,
    )
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return {"updated": True, "page_url": ctx.page_url, "page_title": ctx.page_title}


@router.get("/sessions/{session_id}/browser")
async def get_browser_context(session_id: str):
    """Get current browser context."""
    state = PerceptionRegistry.get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return state.browser.model_dump(exclude_none=True)


# ============================================================================
# Level 3: OS
# ============================================================================

@router.post("/sessions/{session_id}/os/windows")
async def update_windows(session_id: str, request: UpdateWindowsRequest):
    """Update desktop window state."""
    ctx = PerceptionRegistry.update_windows(
        session_id, request.windows, request.foreground_window_id
    )
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return ctx.model_dump(exclude_none=True)


@router.post("/sessions/{session_id}/os/permissions")
async def set_os_permissions(session_id: str, request: SetOSPermissionsRequest):
    """Set OS-level permissions (screen capture, input control, cross-app)."""
    ctx = PerceptionRegistry.set_os_permissions(
        session_id,
        screen_capture=request.screen_capture,
        input_control=request.input_control,
        cross_app=request.cross_app,
    )
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return {
        "screen_capture_permitted": ctx.screen_capture_permitted,
        "input_control_permitted": ctx.input_control_permitted,
        "cross_app_permitted": ctx.cross_app_permitted,
    }


@router.get("/sessions/{session_id}/os")
async def get_os_context(session_id: str):
    """Get current OS context."""
    state = PerceptionRegistry.get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return state.os_level.model_dump(exclude_none=True)


# ============================================================================
# Agent presence
# ============================================================================

@router.post("/sessions/{session_id}/agents")
async def register_agent(session_id: str, request: RegisterAgentRequest):
    """Register an agent in the cockpit."""
    presence = PerceptionRegistry.register_agent(
        session_id, request.agent_id, request.name, request.perception_level
    )
    if not presence:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return presence.model_dump(exclude_none=True)


@router.patch("/sessions/{session_id}/agents/{agent_id}/phase")
async def update_agent_phase(session_id: str, agent_id: str, request: UpdateAgentPhaseRequest):
    """Update an agent's phase and confidence."""
    presence = PerceptionRegistry.update_agent_phase(
        session_id, agent_id, request.phase, request.confidence
    )
    if not presence:
        raise HTTPException(status_code=404, detail="Session or agent not found")
    return presence.model_dump(exclude_none=True)


@router.get("/sessions/{session_id}/agents")
async def list_agents(session_id: str):
    """List all agents in a cockpit session."""
    state = PerceptionRegistry.get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return {"agents": [a.model_dump(exclude_none=True) for a in state.agents]}


# ============================================================================
# Approval gates
# ============================================================================

@router.post("/sessions/{session_id}/approvals")
async def create_approval(session_id: str, request: CreateApprovalRequest):
    """Create an approval gate for a risky action."""
    gate = PerceptionRegistry.create_approval(
        session_id,
        agent_id=request.agent_id,
        level=request.perception_level,
        action_description=request.action_description,
        risk_level=request.risk_level,
    )
    if not gate:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return gate.model_dump(exclude_none=True)


@router.post("/sessions/{session_id}/approvals/{gate_id}/resolve")
async def resolve_approval(session_id: str, gate_id: str, request: ResolveApprovalRequest):
    """Approve or deny a pending approval gate."""
    gate = PerceptionRegistry.resolve_approval(session_id, gate_id, request.approved)
    if not gate:
        raise HTTPException(status_code=404, detail="Session or gate not found")
    return gate.model_dump(exclude_none=True)


@router.get("/sessions/{session_id}/approvals")
async def list_approvals(session_id: str):
    """List all approval gates (pending and resolved)."""
    state = PerceptionRegistry.get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return {
        "approvals": [g.model_dump(exclude_none=True) for g in state.pending_approvals],
        "pending_count": sum(1 for g in state.pending_approvals if g.status == "pending"),
    }


# ============================================================================
# Tool receipts
# ============================================================================

@router.post("/sessions/{session_id}/receipts")
async def add_receipt(session_id: str, request: AddReceiptRequest):
    """Record a tool invocation receipt."""
    receipt = PerceptionRegistry.add_receipt(
        session_id,
        agent_id=request.agent_id,
        tool_name=request.tool_name,
        level=request.perception_level,
        input_summary=request.input_summary,
        output_summary=request.output_summary,
        status=request.status,
        duration_ms=request.duration_ms,
    )
    if not receipt:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return receipt.model_dump(exclude_none=True)


@router.get("/sessions/{session_id}/receipts")
async def list_receipts(session_id: str, limit: int = 50):
    """List recent tool receipts."""
    state = PerceptionRegistry.get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return {
        "receipts": [r.model_dump(exclude_none=True) for r in state.receipts[:limit]],
        "total": len(state.receipts),
    }


# ============================================================================
# Workspaces
# ============================================================================

@router.post("/sessions/{session_id}/workspaces")
async def set_workspaces(session_id: str, request: SetWorkspacesRequest):
    """Set the workspace tree for the left rail."""
    state = PerceptionRegistry.set_workspaces(
        session_id, request.workspaces, request.active_workspace_id
    )
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return {
        "workspace_count": len(state.workspaces),
        "active_workspace_id": state.active_workspace_id,
    }


# ============================================================================
# Unified context for agent prompts
# ============================================================================

@router.get("/sessions/{session_id}/context-for-agent")
async def get_context_for_agent(session_id: str):
    """Get a flattened context payload suitable for injecting into agent prompts.

    Returns the active level's context deck plus agent presence and pending
    approvals — everything the agent needs to be aware of in one call.
    """
    state = PerceptionRegistry.get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    active_ctx = state.get_active_context()

    return {
        "active_level": state.active_level.value,
        "context_deck": active_ctx.context.model_dump(exclude_none=True),
        "agents": [a.model_dump(exclude_none=True) for a in state.agents],
        "pending_approvals": [
            g.model_dump(exclude_none=True)
            for g in state.pending_approvals
            if g.status == "pending"
        ],
        "recent_receipts": [
            r.model_dump(exclude_none=True) for r in state.receipts[:10]
        ],
        "active_workspace_id": state.active_workspace_id,
    }
