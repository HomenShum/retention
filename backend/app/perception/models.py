"""Perception context models for three-level agent system.

Defines the data structures for on-page, browser, and OS-level perception,
plus the unified CockpitState that wraps all three into a persistent shell.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PerceptionLevel(str, Enum):
    ON_PAGE = "on_page"
    BROWSER = "browser"
    OS = "os"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    AUTO_APPROVED = "auto_approved"


class AgentPhase(str, Enum):
    IDLE = "idle"
    OBSERVING = "observing"
    PLANNING = "planning"
    ACTING = "acting"
    VERIFYING = "verifying"
    WAITING_APPROVAL = "waiting_approval"


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

class SurfaceState(BaseModel):
    """A named UI surface with its current state."""
    surface_id: str
    surface_type: str  # panel, drawer, tab, card, modal, rail
    label: str = ""
    visible: bool = True
    active: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ContextDeck(BaseModel):
    """Ordered context deck: active → on-deck → recent → memory → evidence → hover.

    The agent sees items in priority order. Items closer to 'active' get
    more weight in the prompt window.
    """
    active: Optional[Dict[str, Any]] = None
    on_deck: List[Dict[str, Any]] = Field(default_factory=list)
    recent: List[Dict[str, Any]] = Field(default_factory=list, max_length=10)
    memory: List[Dict[str, Any]] = Field(default_factory=list, max_length=50)
    evidence: List[Dict[str, Any]] = Field(default_factory=list, max_length=20)
    hover: Optional[Dict[str, Any]] = None

    def push_active(self, item: Dict[str, Any]) -> None:
        """Promote item to active, demoting current active to on-deck."""
        if self.active:
            self.on_deck.insert(0, self.active)
            # Trim on-deck to 5
            self.on_deck = self.on_deck[:5]
        self.active = item

    def record_evidence(self, item: Dict[str, Any]) -> None:
        """Add evidence item (ActionSpan clip, screenshot, trace)."""
        self.evidence.insert(0, item)
        self.evidence = self.evidence[:20]


# ---------------------------------------------------------------------------
# Level 1: On-Page Context (app-scoped copilot)
# ---------------------------------------------------------------------------

class OnPageContext(BaseModel):
    """What the on-page agent can perceive — the React surface only."""
    level: PerceptionLevel = PerceptionLevel.ON_PAGE

    # Surface registry — all visible panels, drawers, tabs, cards
    surfaces: List[SurfaceState] = Field(default_factory=list)
    active_surface_id: Optional[str] = None

    # Entity focus
    selected_entity: Optional[Dict[str, Any]] = None  # doc, run, ticket, bug
    selected_entity_type: Optional[str] = None

    # Agent-native metadata attributes
    data_surface_id: Optional[str] = None
    data_active_scope_id: Optional[str] = None

    # Context deck (scoped to app)
    context: ContextDeck = Field(default_factory=ContextDeck)

    # App-scoped actions available
    available_actions: List[str] = Field(default_factory=list)

    def get_active_surface(self) -> Optional[SurfaceState]:
        if not self.active_surface_id:
            return None
        for s in self.surfaces:
            if s.surface_id == self.active_surface_id:
                return s
        return None


# ---------------------------------------------------------------------------
# Level 2: Browser Context (tab-scoped operator)
# ---------------------------------------------------------------------------

class TabInfo(BaseModel):
    """Browser tab metadata."""
    tab_id: str
    url: str
    title: str
    active: bool = False
    favicon_url: Optional[str] = None


class BrowserContext(BaseModel):
    """What the browser-level agent can perceive — DOM, tabs, page state."""
    level: PerceptionLevel = PerceptionLevel.BROWSER

    # Tab state
    tabs: List[TabInfo] = Field(default_factory=list)
    active_tab_id: Optional[str] = None

    # Page state (active tab)
    page_url: Optional[str] = None
    page_title: Optional[str] = None
    page_screenshot_b64: Optional[str] = None  # base64 PNG
    dom_snapshot: Optional[str] = None  # accessibility tree or simplified DOM

    # Browser-state snapshots
    cookies_summary: Optional[Dict[str, int]] = None  # domain -> count
    local_storage_keys: List[str] = Field(default_factory=list)

    # Context deck (scoped to browser)
    context: ContextDeck = Field(default_factory=ContextDeck)

    # Permission model
    allowed_domains: List[str] = Field(default_factory=list)
    blocked_domains: List[str] = Field(default_factory=list)

    def get_active_tab(self) -> Optional[TabInfo]:
        for t in self.tabs:
            if t.tab_id == self.active_tab_id:
                return t
        return None


# ---------------------------------------------------------------------------
# Level 3: OS Context (desktop-scoped autonomous assistant)
# ---------------------------------------------------------------------------

class WindowInfo(BaseModel):
    """Desktop window metadata."""
    window_id: str
    app_name: str
    title: str
    bounds: Dict[str, int] = Field(default_factory=dict)  # x, y, w, h
    foreground: bool = False
    minimized: bool = False


class OSContext(BaseModel):
    """What the OS-level agent can perceive — desktop windows, screen, input."""
    level: PerceptionLevel = PerceptionLevel.OS

    # Window state
    windows: List[WindowInfo] = Field(default_factory=list)
    foreground_app: Optional[str] = None
    foreground_window_id: Optional[str] = None

    # Screen capture
    screen_capture_b64: Optional[str] = None  # base64 PNG
    screen_resolution: Optional[Dict[str, int]] = None  # w, h

    # Audio
    system_audio_active: bool = False
    microphone_active: bool = False

    # Context deck (scoped to OS)
    context: ContextDeck = Field(default_factory=ContextDeck)

    # Strict permission gates
    screen_capture_permitted: bool = False
    input_control_permitted: bool = False
    cross_app_permitted: bool = False

    def get_foreground_window(self) -> Optional[WindowInfo]:
        for w in self.windows:
            if w.window_id == self.foreground_window_id:
                return w
        return None


# ---------------------------------------------------------------------------
# Cockpit Shell — persistent product wrapper around all three levels
# ---------------------------------------------------------------------------

class AgentPresence(BaseModel):
    """Agent state shown in the right rail of the cockpit."""
    agent_id: str
    name: str
    perception_level: PerceptionLevel
    phase: AgentPhase = AgentPhase.IDLE
    confidence: float = 0.0  # 0.0 - 1.0
    current_plan: List[str] = Field(default_factory=list)
    transcript: List[Dict[str, Any]] = Field(default_factory=list, max_length=100)
    last_active: Optional[str] = None


class ApprovalGate(BaseModel):
    """Pending approval for a risky action."""
    gate_id: str
    agent_id: str
    perception_level: PerceptionLevel
    action_description: str
    risk_level: str = "medium"  # low, medium, high, critical
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: Optional[str] = None
    resolver: Optional[str] = None  # "user" or "auto"


class ToolReceipt(BaseModel):
    """Record of a tool invocation for the bottom rail."""
    receipt_id: str
    agent_id: str
    tool_name: str
    perception_level: PerceptionLevel
    input_summary: str = ""
    output_summary: str = ""
    status: str = "success"  # success, error, timeout
    duration_ms: int = 0
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class WorkspaceEntry(BaseModel):
    """Left rail item — a workspace, run, entity, or doc."""
    entry_id: str
    entry_type: str  # workspace, run, entity, doc, session
    label: str
    icon: Optional[str] = None
    active: bool = False
    children: List["WorkspaceEntry"] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# Allow recursive model
WorkspaceEntry.model_rebuild()


class CockpitState(BaseModel):
    """Unified cockpit state — the persistent shell wrapping all perception levels.

    Structure:
      left rail   — workspaces, runs, entities, docs
      center      — one active surface (from whichever perception level is active)
      right rail  — agent presence, plan, approvals, confidence, transcript
      bottom rail — receipts, tools, trace, interventions
    """
    # Active perception level
    active_level: PerceptionLevel = PerceptionLevel.ON_PAGE

    # Perception contexts (one per level, lazily populated)
    on_page: OnPageContext = Field(default_factory=OnPageContext)
    browser: BrowserContext = Field(default_factory=BrowserContext)
    os_level: OSContext = Field(default_factory=OSContext)

    # Left rail — workspace tree
    workspaces: List[WorkspaceEntry] = Field(default_factory=list)
    active_workspace_id: Optional[str] = None

    # Right rail — agent presence
    agents: List[AgentPresence] = Field(default_factory=list)
    pending_approvals: List[ApprovalGate] = Field(default_factory=list)

    # Bottom rail — tool receipts and trace
    receipts: List[ToolReceipt] = Field(default_factory=list, max_length=200)
    interventions: List[Dict[str, Any]] = Field(default_factory=list, max_length=50)

    # Session metadata
    session_id: Optional[str] = None
    started_at: Optional[str] = None
    last_updated: Optional[str] = None

    def get_active_context(self) -> OnPageContext | BrowserContext | OSContext:
        """Return the context for the currently active perception level."""
        if self.active_level == PerceptionLevel.ON_PAGE:
            return self.on_page
        elif self.active_level == PerceptionLevel.BROWSER:
            return self.browser
        return self.os_level

    def add_receipt(self, receipt: ToolReceipt) -> None:
        self.receipts.insert(0, receipt)
        self.receipts = self.receipts[:200]

    def add_approval(self, gate: ApprovalGate) -> None:
        self.pending_approvals.append(gate)

    def resolve_approval(self, gate_id: str, status: ApprovalStatus, resolver: str = "user") -> bool:
        for gate in self.pending_approvals:
            if gate.gate_id == gate_id:
                gate.status = status
                gate.resolved_at = datetime.now(timezone.utc).isoformat()
                gate.resolver = resolver
                return True
        return False
