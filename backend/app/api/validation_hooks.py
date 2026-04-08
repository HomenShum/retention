"""Validation Stop Hook API.

A completion gate that prevents AI coding agents (Cursor, Devin, OpenClaw) from
marking a task 'done' until retention.sh confirms visual/functional success.

Flow:
  1. External agent POSTs /hooks/request  → gets back a hook_id
  2. TA backend runs verification (or agent triggers it manually)
  3. External agent polls GET /hooks/{hook_id} waiting for status=released
  4. Only when released can the external agent mark the PR merged / task done

Endpoints:
  POST /hooks/request               → open a new validation gate
  POST /hooks/{hook_id}/release     → pass + release the gate (internal TA use)
  POST /hooks/{hook_id}/fail        → fail + block the gate
  GET  /hooks/{hook_id}             → poll current status
  GET  /hooks                       → list all hooks (optional filter by agent/status)
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hooks", tags=["validation-hooks"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class HookStatus(str, Enum):
    PENDING   = "pending"    # Waiting for TA verification
    RUNNING   = "running"    # TA is actively verifying
    RELEASED  = "released"   # PASS — external agent may proceed
    BLOCKED   = "blocked"    # FAIL — external agent must NOT merge/close


class ValidationHookRequest(BaseModel):
    agent_id: str                        # e.g. "cursor", "devin", "openclaw"
    task_description: str
    pr_url: Optional[str] = None
    repo: Optional[str] = None
    branch: Optional[str] = None
    requested_by: Optional[str] = None  # email / slack handle
    metadata: Dict = {}


class ValidationHook(BaseModel):
    hook_id: str
    agent_id: str
    task_description: str
    status: HookStatus = HookStatus.PENDING
    pr_url: Optional[str] = None
    repo: Optional[str] = None
    branch: Optional[str] = None
    requested_by: Optional[str] = None
    created_at: str
    updated_at: str
    released_at: Optional[str] = None
    release_notes: str = ""
    failure_reason: str = ""
    action_span_session_id: Optional[str] = None  # Link to ActionSpan evidence
    metadata: Dict = {}


class HookReleaseRequest(BaseModel):
    release_notes: str = ""
    action_span_session_id: Optional[str] = None


class HookFailRequest(BaseModel):
    failure_reason: str
    action_span_session_id: Optional[str] = None


# ---------------------------------------------------------------------------
# JSON-backed persistent store
# ---------------------------------------------------------------------------

_HOOKS_FILE = Path(__file__).parent.parent.parent / "data" / "hooks.json"


class _JsonBackedDict:
    """Dict-like object that persists ValidationHook values to a JSON file on every write."""

    def __init__(self, path: Path = _HOOKS_FILE) -> None:
        self._path = path
        self._data: Dict[str, ValidationHook] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            if self._path.exists():
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._data = {k: ValidationHook(**v) for k, v in raw.items()}
        except Exception as exc:
            logger.warning("Could not load hooks from %s: %s", self._path, exc)
            self._data = {}

    def _save(self) -> None:
        try:
            import tempfile
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {k: v.model_dump() for k, v in self._data.items()}
            # Atomic write: write to temp file then rename
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._path.parent), suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
                os.replace(tmp_path, str(self._path))
            except Exception:
                os.unlink(tmp_path)
                raise
        except Exception as exc:
            logger.warning("Could not persist hooks to %s: %s", self._path, exc)

    # ------------------------------------------------------------------
    # Dict interface
    # ------------------------------------------------------------------

    def __getitem__(self, key: str) -> ValidationHook:
        return self._data[key]

    def __setitem__(self, key: str, value: ValidationHook) -> None:
        self._data[key] = value
        self._save()

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def get(self, key: str, default: Optional[ValidationHook] = None) -> Optional[ValidationHook]:
        return self._data.get(key, default)

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def __len__(self) -> int:
        return len(self._data)


_hooks: _JsonBackedDict = _JsonBackedDict()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/request", response_model=ValidationHook, summary="Open a validation gate")
async def request_validation(req: ValidationHookRequest) -> ValidationHook:
    """External agent calls this before submitting a PR.  Returns hook_id to poll."""
    hook = ValidationHook(
        hook_id=str(uuid.uuid4()),
        agent_id=req.agent_id,
        task_description=req.task_description,
        status=HookStatus.PENDING,
        pr_url=req.pr_url,
        repo=req.repo,
        branch=req.branch,
        requested_by=req.requested_by,
        created_at=_now(),
        updated_at=_now(),
        metadata=req.metadata,
    )
    _hooks[hook.hook_id] = hook
    return hook


@router.post("/{hook_id}/release", response_model=ValidationHook, summary="Release (pass) a gate")
async def release_hook(hook_id: str, req: HookReleaseRequest) -> ValidationHook:
    """TA calls this when verification passes.  External agent may now proceed."""
    hook = _hooks.get(hook_id)
    if not hook:
        raise HTTPException(status_code=404, detail=f"Hook not found: {hook_id}")
    hook.status = HookStatus.RELEASED
    hook.released_at = _now()
    hook.updated_at = _now()
    hook.release_notes = req.release_notes
    hook.action_span_session_id = req.action_span_session_id
    _hooks[hook_id] = hook
    return hook


@router.post("/{hook_id}/fail", response_model=ValidationHook, summary="Fail (block) a gate")
async def fail_hook(hook_id: str, req: HookFailRequest) -> ValidationHook:
    """TA calls this when verification fails.  External agent is blocked."""
    hook = _hooks.get(hook_id)
    if not hook:
        raise HTTPException(status_code=404, detail=f"Hook not found: {hook_id}")
    hook.status = HookStatus.BLOCKED
    hook.failure_reason = req.failure_reason
    hook.action_span_session_id = req.action_span_session_id
    hook.updated_at = _now()
    _hooks[hook_id] = hook
    return hook


class AttachSessionRequest(BaseModel):
    session_id: str  # ActionSpan session to link


@router.post("/{hook_id}/attach-session", response_model=ValidationHook, summary="Link an ActionSpan session to a hook")
async def attach_session(hook_id: str, req: AttachSessionRequest) -> ValidationHook:
    """Link an ActionSpan session to a validation hook.

    When all spans in the session pass scoring, the hook will auto-release.
    """
    hook = _hooks.get(hook_id)
    if not hook:
        raise HTTPException(status_code=404, detail=f"Hook not found: {hook_id}")
    hook.action_span_session_id = req.session_id
    hook.status = HookStatus.RUNNING
    hook.updated_at = _now()
    _hooks[hook_id] = hook
    return hook


def try_auto_release_hooks_for_session(session_id: str, pass_rate: float, total_spans: int) -> None:
    """Called by ActionSpan service after scoring. Auto-releases linked hooks if all spans pass.

    A session with pass_rate >= 1.0 and at least 1 span is considered passing.
    """
    if total_spans < 1 or pass_rate < 1.0:
        return
    for hook_id, hook in _hooks.items():
        if hook.action_span_session_id == session_id and hook.status in (HookStatus.PENDING, HookStatus.RUNNING):
            hook.status = HookStatus.RELEASED
            hook.released_at = _now()
            hook.updated_at = _now()
            hook.release_notes = f"Auto-released: {total_spans} spans passed (pass_rate={pass_rate:.0%})"
            _hooks[hook_id] = hook
            logger.info("Auto-released hook %s for session %s", hook_id, session_id)


@router.get("/{hook_id}", response_model=ValidationHook, summary="Poll a gate")
async def get_hook(hook_id: str) -> ValidationHook:
    """External agent polls this to know when it can proceed."""
    hook = _hooks.get(hook_id)
    if not hook:
        raise HTTPException(status_code=404, detail=f"Hook not found: {hook_id}")
    return hook


@router.get("", response_model=List[ValidationHook], summary="List validation hooks")
async def list_hooks(
    agent_id: Optional[str] = Query(None),
    status: Optional[HookStatus] = Query(None),
) -> List[ValidationHook]:
    """Dashboard listing.  Optionally filter by agent_id or status."""
    hooks = list(_hooks.values())
    if agent_id:
        hooks = [h for h in hooks if h.agent_id == agent_id]
    if status:
        hooks = [h for h in hooks if h.status == status]
    return sorted(hooks, key=lambda h: h.created_at, reverse=True)

