"""Board API — Kanban board CRUD with SSE broadcast for real-time team sync.

Storage: backend/data/boards/{board_id}.json
Broadcast: reuses _broadcast_team_event from mcp_pipeline.py
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .mcp_pipeline import _broadcast_team_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/board", tags=["board"])

_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "boards"
_board_locks: Dict[str, asyncio.Lock] = {}


def _get_lock(board_id: str) -> asyncio.Lock:
    if board_id not in _board_locks:
        _board_locks[board_id] = asyncio.Lock()
    return _board_locks[board_id]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _board_path(board_id: str) -> Path:
    return _DATA_DIR / f"{board_id}.json"


def _read_board(board_id: str) -> Optional[dict]:
    p = _board_path(board_id)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def _write_board(board: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = _board_path(board["board_id"])
    p.write_text(json.dumps(board, indent=2, default=str))


def _default_columns() -> List[dict]:
    return [
        {"id": "backlog", "title": "Backlog", "position": 1000, "color": "#6b7280"},
        {"id": "in-progress", "title": "In Progress", "position": 2000, "color": "#f59e0b"},
        {"id": "review", "title": "Review", "position": 3000, "color": "#8b5cf6"},
        {"id": "done", "title": "Done", "position": 4000, "color": "#22c55e"},
    ]


def _make_card(title: str, priority: str = "medium", tags: Optional[List[str]] = None,
               description: str = "", position: float = 0, column_id: str = "backlog",
               assignee: str = "") -> dict:
    return {
        "id": str(uuid.uuid4())[:8],
        "title": title,
        "description": description,
        "column_id": column_id,
        "position": position,
        "assignee": assignee,
        "priority": priority,
        "tags": tags or [],
        "due_date": None,
        "created_by": "system",
        "created_at": _now(),
        "updated_at": _now(),
    }


def _seed_cards() -> List[dict]:
    """Seed board with operating memo strategic context."""
    cards = []
    pos = 1000

    # Product Core
    for title in [
        "Ship cloud dashboard — stable, deployed",
        "Make replay savings legible in product",
        "Finalize starter MCP workflow",
        "Package one flagship demo",
        "Trajectory audit / shortcut recommendation",
        "Local-cloud profile sync",
    ]:
        cards.append(_make_card(title, "high", ["product-core", "phase-1"], position=pos))
        pos += 1000

    # External Proof
    for title in [
        "BrowserStack benchmark — recognizable baseline",
        "Publish one shareable case study",
        "Benchmark pages people can actually share",
    ]:
        cards.append(_make_card(title, "high", ["external-proof", "phase-2"], position=pos))
        pos += 1000

    # Pilot Conversion
    for title in [
        "Onboard first design partners (2-3)",
        "Run benchmark + ROI reports per partner",
        "Close first paid pilot",
        "Refine outreach script for compliance / legacy portal / healthcare",
    ]:
        cards.append(_make_card(title, "critical", ["pilot-conversion", "phase-2"], position=pos))
        pos += 1000

    # Ecosystem
    for title in [
        "GitHub starter repo + docs + examples",
        "OpenClaw / Claude Code integrations",
        "Shareable Slack run summaries",
    ]:
        cards.append(_make_card(title, "medium", ["ecosystem", "phase-3"], position=pos))
        pos += 1000

    # Hackathon
    for title in [
        "Review Khush's Bay Area hackathon spreadsheet (Apr/May 2026)",
        "Pick first hackathon events to attend",
        "LinkedIn distribution — target Jordan Cutler's audience",
    ]:
        cards.append(_make_card(title, "high", ["hackathon", "gtm"], position=pos))
        pos += 1000

    # GTM
    for title in [
        "Individual dashboard tabs — live for external users",
        "Explore-only mode validation (fix skip_stages bug)",
        "PII redaction pipeline",
        "Audit PDF generator",
        "Drift detection dashboard",
    ]:
        cards.append(_make_card(title, "medium", ["product-core", "phase-1"], position=pos))
        pos += 1000

    return cards


def _create_default_board(board_id: str) -> dict:
    board = {
        "board_id": board_id,
        "columns": _default_columns(),
        "cards": _seed_cards(),
        "version": 1,
        "updated_at": _now(),
    }
    _write_board(board)
    return board


# ── Request Models ──

class CreateCardRequest(BaseModel):
    title: str
    description: str = ""
    column_id: str = "backlog"
    assignee: str = ""
    priority: str = "medium"
    tags: List[str] = Field(default_factory=list)
    due_date: Optional[str] = None


class UpdateCardRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    assignee: Optional[str] = None
    priority: Optional[str] = None
    tags: Optional[List[str]] = None
    due_date: Optional[str] = None


class MoveCardRequest(BaseModel):
    column_id: str
    position: float


# ── Endpoints ──

@router.get("/{board_id}")
async def get_board(board_id: str):
    async with _get_lock(board_id):
        board = _read_board(board_id)
        if not board:
            board = _create_default_board(board_id)
        return board


@router.post("/{board_id}/cards")
async def create_card(board_id: str, req: CreateCardRequest):
    async with _get_lock(board_id):
        board = _read_board(board_id)
        if not board:
            board = _create_default_board(board_id)

        # Find max position in target column
        col_cards = [c for c in board["cards"] if c["column_id"] == req.column_id]
        max_pos = max((c["position"] for c in col_cards), default=0)

        card = _make_card(
            title=req.title,
            description=req.description,
            column_id=req.column_id,
            assignee=req.assignee,
            priority=req.priority,
            tags=req.tags,
            position=max_pos + 1000,
        )
        if req.due_date:
            card["due_date"] = req.due_date

        board["cards"].append(card)
        board["version"] += 1
        board["updated_at"] = _now()
        _write_board(board)

    _broadcast_team_event({
        "type": "board_update",
        "action": "card_created",
        "board_id": board_id,
        "card_id": card["id"],
        "version": board["version"],
        "timestamp": _now(),
    })
    return card


@router.patch("/{board_id}/cards/{card_id}")
async def update_card(board_id: str, card_id: str, req: UpdateCardRequest):
    async with _get_lock(board_id):
        board = _read_board(board_id)
        if not board:
            raise HTTPException(404, "Board not found")

        card = next((c for c in board["cards"] if c["id"] == card_id), None)
        if not card:
            raise HTTPException(404, "Card not found")

        updates = req.model_dump(exclude_none=True)
        for k, v in updates.items():
            card[k] = v
        card["updated_at"] = _now()

        board["version"] += 1
        board["updated_at"] = _now()
        _write_board(board)

    _broadcast_team_event({
        "type": "board_update",
        "action": "card_updated",
        "board_id": board_id,
        "card_id": card_id,
        "version": board["version"],
        "timestamp": _now(),
    })
    return card


@router.patch("/{board_id}/cards/{card_id}/move")
async def move_card(board_id: str, card_id: str, req: MoveCardRequest):
    async with _get_lock(board_id):
        board = _read_board(board_id)
        if not board:
            raise HTTPException(404, "Board not found")

        card = next((c for c in board["cards"] if c["id"] == card_id), None)
        if not card:
            raise HTTPException(404, "Card not found")

        card["column_id"] = req.column_id
        card["position"] = req.position
        card["updated_at"] = _now()

        board["version"] += 1
        board["updated_at"] = _now()
        _write_board(board)

    _broadcast_team_event({
        "type": "board_update",
        "action": "card_moved",
        "board_id": board_id,
        "card_id": card_id,
        "version": board["version"],
        "timestamp": _now(),
    })
    return card


@router.delete("/{board_id}/cards/{card_id}")
async def delete_card(board_id: str, card_id: str):
    async with _get_lock(board_id):
        board = _read_board(board_id)
        if not board:
            raise HTTPException(404, "Board not found")

        before = len(board["cards"])
        board["cards"] = [c for c in board["cards"] if c["id"] != card_id]
        if len(board["cards"]) == before:
            raise HTTPException(404, "Card not found")

        board["version"] += 1
        board["updated_at"] = _now()
        _write_board(board)

    _broadcast_team_event({
        "type": "board_update",
        "action": "card_deleted",
        "board_id": board_id,
        "card_id": card_id,
        "version": board["version"],
        "timestamp": _now(),
    })
    return {"deleted": card_id}
