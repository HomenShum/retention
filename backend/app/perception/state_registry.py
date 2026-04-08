"""In-memory perception state registry.

Manages cockpit sessions — one per connected client. Each session holds
the full CockpitState with all three perception levels.

Thread-safe via asyncio (single-threaded event loop). If we ever need
multi-process, swap for Redis-backed state.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .models import (
    AgentPhase,
    AgentPresence,
    ApprovalGate,
    ApprovalStatus,
    BrowserContext,
    CockpitState,
    ContextDeck,
    OnPageContext,
    OSContext,
    PerceptionLevel,
    SurfaceState,
    TabInfo,
    ToolReceipt,
    WindowInfo,
    WorkspaceEntry,
)

logger = logging.getLogger(__name__)


class PerceptionRegistry:
    """Singleton registry for cockpit sessions."""

    _sessions: Dict[str, CockpitState] = {}

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def create_session(cls) -> CockpitState:
        """Create a new cockpit session and return it."""
        session_id = f"cockpit-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        state = CockpitState(
            session_id=session_id,
            started_at=now,
            last_updated=now,
        )
        cls._sessions[session_id] = state
        logger.info(f"Created cockpit session {session_id}")
        return state

    @classmethod
    def get_session(cls, session_id: str) -> Optional[CockpitState]:
        return cls._sessions.get(session_id)

    @classmethod
    def get_or_create(cls, session_id: Optional[str] = None) -> CockpitState:
        if session_id and session_id in cls._sessions:
            return cls._sessions[session_id]
        return cls.create_session()

    @classmethod
    def list_sessions(cls) -> List[str]:
        return list(cls._sessions.keys())

    @classmethod
    def delete_session(cls, session_id: str) -> bool:
        if session_id in cls._sessions:
            del cls._sessions[session_id]
            logger.info(f"Deleted cockpit session {session_id}")
            return True
        return False

    # ------------------------------------------------------------------
    # Level switching
    # ------------------------------------------------------------------

    @classmethod
    def set_active_level(cls, session_id: str, level: PerceptionLevel) -> Optional[CockpitState]:
        state = cls.get_session(session_id)
        if not state:
            return None
        state.active_level = level
        state.last_updated = datetime.now(timezone.utc).isoformat()
        logger.info(f"Session {session_id} switched to {level.value}")
        return state

    # ------------------------------------------------------------------
    # On-page operations
    # ------------------------------------------------------------------

    @classmethod
    def update_surfaces(
        cls, session_id: str, surfaces: List[SurfaceState], active_id: Optional[str] = None
    ) -> Optional[OnPageContext]:
        state = cls.get_session(session_id)
        if not state:
            return None
        state.on_page.surfaces = surfaces
        if active_id:
            state.on_page.active_surface_id = active_id
        state.last_updated = datetime.now(timezone.utc).isoformat()
        return state.on_page

    @classmethod
    def set_selected_entity(
        cls, session_id: str, entity: Dict, entity_type: str
    ) -> Optional[OnPageContext]:
        state = cls.get_session(session_id)
        if not state:
            return None
        state.on_page.selected_entity = entity
        state.on_page.selected_entity_type = entity_type
        state.on_page.context.push_active({"type": entity_type, **entity})
        state.last_updated = datetime.now(timezone.utc).isoformat()
        return state.on_page

    # ------------------------------------------------------------------
    # Browser operations
    # ------------------------------------------------------------------

    @classmethod
    def update_tabs(
        cls, session_id: str, tabs: List[TabInfo], active_tab_id: Optional[str] = None
    ) -> Optional[BrowserContext]:
        state = cls.get_session(session_id)
        if not state:
            return None
        state.browser.tabs = tabs
        if active_tab_id:
            state.browser.active_tab_id = active_tab_id
        state.last_updated = datetime.now(timezone.utc).isoformat()
        return state.browser

    @classmethod
    def update_page_state(
        cls,
        session_id: str,
        url: str,
        title: str,
        screenshot_b64: Optional[str] = None,
        dom_snapshot: Optional[str] = None,
    ) -> Optional[BrowserContext]:
        state = cls.get_session(session_id)
        if not state:
            return None
        state.browser.page_url = url
        state.browser.page_title = title
        if screenshot_b64:
            state.browser.page_screenshot_b64 = screenshot_b64
        if dom_snapshot:
            state.browser.dom_snapshot = dom_snapshot
        state.browser.context.push_active({"url": url, "title": title})
        state.last_updated = datetime.now(timezone.utc).isoformat()
        return state.browser

    # ------------------------------------------------------------------
    # OS operations
    # ------------------------------------------------------------------

    @classmethod
    def update_windows(
        cls, session_id: str, windows: List[WindowInfo], foreground_id: Optional[str] = None
    ) -> Optional[OSContext]:
        state = cls.get_session(session_id)
        if not state:
            return None
        state.os_level.windows = windows
        if foreground_id:
            state.os_level.foreground_window_id = foreground_id
            for w in windows:
                if w.window_id == foreground_id:
                    state.os_level.foreground_app = w.app_name
                    break
        state.last_updated = datetime.now(timezone.utc).isoformat()
        return state.os_level

    @classmethod
    def set_os_permissions(
        cls,
        session_id: str,
        screen_capture: Optional[bool] = None,
        input_control: Optional[bool] = None,
        cross_app: Optional[bool] = None,
    ) -> Optional[OSContext]:
        state = cls.get_session(session_id)
        if not state:
            return None
        if screen_capture is not None:
            state.os_level.screen_capture_permitted = screen_capture
        if input_control is not None:
            state.os_level.input_control_permitted = input_control
        if cross_app is not None:
            state.os_level.cross_app_permitted = cross_app
        state.last_updated = datetime.now(timezone.utc).isoformat()
        return state.os_level

    # ------------------------------------------------------------------
    # Agent presence
    # ------------------------------------------------------------------

    @classmethod
    def register_agent(
        cls,
        session_id: str,
        agent_id: str,
        name: str,
        level: PerceptionLevel,
    ) -> Optional[AgentPresence]:
        state = cls.get_session(session_id)
        if not state:
            return None
        presence = AgentPresence(
            agent_id=agent_id,
            name=name,
            perception_level=level,
            last_active=datetime.now(timezone.utc).isoformat(),
        )
        # Replace if exists, append if new
        state.agents = [a for a in state.agents if a.agent_id != agent_id]
        state.agents.append(presence)
        state.last_updated = datetime.now(timezone.utc).isoformat()
        return presence

    @classmethod
    def update_agent_phase(
        cls, session_id: str, agent_id: str, phase: AgentPhase, confidence: Optional[float] = None
    ) -> Optional[AgentPresence]:
        state = cls.get_session(session_id)
        if not state:
            return None
        for agent in state.agents:
            if agent.agent_id == agent_id:
                agent.phase = phase
                if confidence is not None:
                    agent.confidence = confidence
                agent.last_active = datetime.now(timezone.utc).isoformat()
                state.last_updated = datetime.now(timezone.utc).isoformat()
                return agent
        return None

    # ------------------------------------------------------------------
    # Approvals
    # ------------------------------------------------------------------

    @classmethod
    def create_approval(
        cls,
        session_id: str,
        agent_id: str,
        level: PerceptionLevel,
        action_description: str,
        risk_level: str = "medium",
    ) -> Optional[ApprovalGate]:
        state = cls.get_session(session_id)
        if not state:
            return None
        gate = ApprovalGate(
            gate_id=f"gate-{uuid.uuid4().hex[:8]}",
            agent_id=agent_id,
            perception_level=level,
            action_description=action_description,
            risk_level=risk_level,
        )
        state.add_approval(gate)
        state.last_updated = datetime.now(timezone.utc).isoformat()
        return gate

    @classmethod
    def resolve_approval(
        cls, session_id: str, gate_id: str, approved: bool
    ) -> Optional[ApprovalGate]:
        state = cls.get_session(session_id)
        if not state:
            return None
        status = ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
        state.resolve_approval(gate_id, status, resolver="user")
        state.last_updated = datetime.now(timezone.utc).isoformat()
        for gate in state.pending_approvals:
            if gate.gate_id == gate_id:
                return gate
        return None

    # ------------------------------------------------------------------
    # Tool receipts
    # ------------------------------------------------------------------

    @classmethod
    def add_receipt(
        cls,
        session_id: str,
        agent_id: str,
        tool_name: str,
        level: PerceptionLevel,
        input_summary: str = "",
        output_summary: str = "",
        status: str = "success",
        duration_ms: int = 0,
    ) -> Optional[ToolReceipt]:
        state = cls.get_session(session_id)
        if not state:
            return None
        receipt = ToolReceipt(
            receipt_id=f"rcpt-{uuid.uuid4().hex[:8]}",
            agent_id=agent_id,
            tool_name=tool_name,
            perception_level=level,
            input_summary=input_summary,
            output_summary=output_summary,
            status=status,
            duration_ms=duration_ms,
        )
        state.add_receipt(receipt)
        state.last_updated = datetime.now(timezone.utc).isoformat()
        return receipt

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    @classmethod
    def set_workspaces(
        cls, session_id: str, workspaces: List[WorkspaceEntry], active_id: Optional[str] = None
    ) -> Optional[CockpitState]:
        state = cls.get_session(session_id)
        if not state:
            return None
        state.workspaces = workspaces
        if active_id:
            state.active_workspace_id = active_id
        state.last_updated = datetime.now(timezone.utc).isoformat()
        return state

    # ------------------------------------------------------------------
    # Snapshot for frontend
    # ------------------------------------------------------------------

    @classmethod
    def get_snapshot(cls, session_id: str) -> Optional[Dict]:
        """Return a JSON-safe snapshot of the full cockpit state."""
        state = cls.get_session(session_id)
        if not state:
            return None
        return state.model_dump(exclude_none=True)
