"""Perception module — three-level agent context system.

Levels:
  1. On-page  — app-scoped copilot (React surfaces, panels, entities)
  2. Browser  — tab-scoped operator (DOM, URLs, screenshots, web tasks)
  3. OS       — desktop-scoped autonomous assistant (windows, screen capture, input)

Each level has its own context deck, tool set, and permission model.
The CockpitState ties all three together into a persistent shell.
"""

from .models import (
    PerceptionLevel,
    SurfaceState,
    ContextDeck,
    OnPageContext,
    BrowserContext,
    OSContext,
    CockpitState,
    AgentPresence,
    ApprovalGate,
    ToolReceipt,
)
from .state_registry import PerceptionRegistry

__all__ = [
    "PerceptionLevel",
    "SurfaceState",
    "ContextDeck",
    "OnPageContext",
    "BrowserContext",
    "OSContext",
    "CockpitState",
    "AgentPresence",
    "ApprovalGate",
    "ToolReceipt",
    "PerceptionRegistry",
]
