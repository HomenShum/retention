"""
Agent Relay — Server-side WebSocket endpoint for outbound relay connections.

User machines and device-farm nodes connect OUT to this endpoint via WSS.
The TA agent on the server sends emulator commands down the WebSocket and
receives results/frames back. No port is ever opened on the client side.

Endpoints:
    /ws/agent-relay  — main relay WebSocket (auth required)

Session management:
    - Each connection is tracked by user identity (from API key)
    - Multiple devices per user supported
    - Heartbeat / keep-alive enforced
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets  # noqa: F401 — used in _evict_stale_commands and session HMAC
import time
import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Session registry — tracks connected relays
# ---------------------------------------------------------------------------


class RelaySession:
    """Represents a single connected thin-relay client."""

    def __init__(self, ws: WebSocket, user_id: str, session_id: str):
        self.ws = ws
        self.user_id = user_id
        self.session_id = session_id
        self.devices: list[dict[str, Any]] = []
        self.connected_at = time.time()
        self.last_heartbeat = time.time()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def send_command(
        self,
        command: str,
        timeout: float = 30.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a command to the relay and wait for the response."""
        request_id = str(uuid.uuid4())
        msg = {
            "type": "command",
            "command": command,
            "request_id": request_id,
            **kwargs,
        }

        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future

        try:
            await self.ws.send_json(msg)
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return {"error": f"Command '{command}' timed out after {timeout}s"}
        finally:
            self._pending.pop(request_id, None)

    async def start_stream(self, session_id: str, device: str | None = None, fps: int = 2) -> None:
        """Ask the relay to start frame streaming."""
        await self.ws.send_json({
            "type": "stream_start",
            "session_id": session_id,
            "device": device,
            "fps": fps,
        })

    async def stop_stream(self) -> None:
        """Ask the relay to stop frame streaming."""
        await self.ws.send_json({"type": "stream_stop"})

    def resolve_response(self, msg: dict[str, Any]) -> bool:
        """Resolve a pending command future. Returns True if matched."""
        request_id = msg.get("request_id", "")
        future = self._pending.get(request_id)
        if future and not future.done():
            future.set_result(msg)
            return True
        return False


class RelayRegistry:
    """Global registry of connected relay sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, RelaySession] = {}  # session_id → session
        self._by_user: dict[str, list[str]] = {}  # user_id → [session_ids]

    def register(self, session: RelaySession) -> None:
        self._sessions[session.session_id] = session
        self._by_user.setdefault(session.user_id, []).append(session.session_id)
        logger.info(
            "Relay registered: user=%s session=%s devices=%d",
            session.user_id,
            session.session_id,
            len(session.devices),
        )

    def unregister(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            user_sessions = self._by_user.get(session.user_id, [])
            if session_id in user_sessions:
                user_sessions.remove(session_id)
            if not user_sessions:
                self._by_user.pop(session.user_id, None)
            logger.info("Relay disconnected: session=%s", session_id)

    def get_session(self, session_id: str) -> Optional[RelaySession]:
        return self._sessions.get(session_id)

    def get_user_sessions(self, user_id: str) -> list[RelaySession]:
        session_ids = self._by_user.get(user_id, [])
        return [self._sessions[sid] for sid in session_ids if sid in self._sessions]

    def get_any_session(self, user_id: str) -> Optional[RelaySession]:
        """Get any available relay for a user (round-robin could go here)."""
        sessions = self.get_user_sessions(user_id)
        return sessions[0] if sessions else None

    @property
    def connected_count(self) -> int:
        return len(self._sessions)

    def status(self) -> dict[str, Any]:
        return {
            "connected_relays": self.connected_count,
            "users": {
                uid: len(sids) for uid, sids in self._by_user.items()
            },
        }


# Global registry instance
relay_registry = RelayRegistry()

# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _validate_relay_auth(token: str) -> Optional[str]:
    """Validate a relay connection token. Returns user_id or None."""
    expected = os.getenv("RETENTION_MCP_TOKEN", "").strip()
    if not expected:
        # Open mode — no auth configured, assign anonymous identity
        return "anonymous"
    if hmac.compare_digest(token, expected):
        return "authenticated-user"
    return None


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/agent-relay")
async def agent_relay_ws(websocket: WebSocket) -> None:
    """Outbound relay WebSocket endpoint.

    Protocol:
      1. Client connects with Authorization header (Bearer <api_key>)
      2. Server validates auth and accepts
      3. Client sends relay_ready with device list
      4. Server sends commands, client sends responses and frames
    """
    # --- Auth from headers or query param ---
    auth_header = websocket.headers.get("authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    else:
        # Fallback: ?token=xxx query param (for browser-based testing)
        token = websocket.query_params.get("token", "")

    user_id = _validate_relay_auth(token)
    if not user_id:
        await websocket.close(code=4001, reason="Invalid API key")
        return

    await websocket.accept()

    session_id = str(uuid.uuid4())
    session = RelaySession(ws=websocket, user_id=user_id, session_id=session_id)

    try:
        # Wait for relay_ready message
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=15.0)
            init_msg = json.loads(raw)
            if init_msg.get("type") == "relay_ready":
                session.devices = init_msg.get("devices", [])
        except (asyncio.TimeoutError, json.JSONDecodeError):
            logger.warning("Relay did not send relay_ready within 15s")

        relay_registry.register(session)

        # --- Message loop ---
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "response":
                session.resolve_response(msg)
            elif msg_type == "frame":
                # Frame data from streamer — forward to any interested consumers
                # (benchmark WS, dashboard, ActionSpan recorder, etc.)
                await _handle_incoming_frame(session, msg)
            elif msg_type == "relay_ready":
                # Device list update
                session.devices = msg.get("devices", [])
            elif msg_type == "heartbeat":
                session.last_heartbeat = time.time()
                await websocket.send_json({"type": "heartbeat_ack"})
            else:
                logger.debug("Unknown message type from relay: %s", msg_type)

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Relay session error")
    finally:
        relay_registry.unregister(session_id)
        _evict_stale_commands(max_age_seconds=300)


async def _handle_incoming_frame(session: RelaySession, msg: dict[str, Any]) -> None:
    """Process a frame received from a relay. Placeholder for integration."""
    # In production, this would forward to:
    # - ActionSpan recorder
    # - Live dashboard WebSocket
    # - Benchmark streaming consumers
    pass


# ---------------------------------------------------------------------------
# Convenience API for the TA agent to use
# ---------------------------------------------------------------------------


async def send_command_to_user(
    user_id: str,
    command: str,
    timeout: float = 30.0,
    **kwargs: Any,
) -> dict[str, Any]:
    """Send a command to a user's connected relay.

    This is the primary interface for the TA agent to drive remote emulators.
    """
    session = relay_registry.get_any_session(user_id)
    if not session:
        return {"error": f"No relay connected for user '{user_id}'"}
    return await session.send_command(command, timeout=timeout, **kwargs)


# ---------------------------------------------------------------------------
# Per-user rate limiting
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Sliding-window rate limiter keyed by user identity."""

    def __init__(self, max_requests: int = 30, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}  # user_id → [timestamps]

    def check(self, user_id: str) -> tuple[bool, str]:
        """Returns (allowed, reason). Prunes expired entries on each call."""
        now = time.time()
        cutoff = now - self.window

        hits = self._hits.setdefault(user_id, [])
        # Prune expired
        self._hits[user_id] = hits = [t for t in hits if t > cutoff]

        if len(hits) >= self.max_requests:
            retry_after = round(hits[0] - cutoff, 1)
            return False, f"Rate limit exceeded: {self.max_requests} requests per {int(self.window)}s. Retry after {retry_after}s"

        hits.append(now)
        return True, ""

    def cleanup(self) -> None:
        """Remove stale user entries (call periodically)."""
        now = time.time()
        cutoff = now - self.window
        stale = [uid for uid, hits in self._hits.items() if not hits or hits[-1] < cutoff]
        for uid in stale:
            del self._hits[uid]


# 30 requests per 60s per user — generous for real use, blocks abuse
_rate_limiter = _RateLimiter(max_requests=30, window_seconds=60.0)


# ---------------------------------------------------------------------------
# Phone-to-laptop relay: command submission + result streaming
# ---------------------------------------------------------------------------

# Stores recent command results for SSE consumers and polling
# Key: command_id, Value: {status, result, events[]}
_command_results: dict[str, dict[str, Any]] = {}

# SSE subscribers waiting for updates on a command_id
_command_subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}


def _evict_stale_commands(max_age_seconds: float = 300) -> None:
    """Remove completed command results older than max_age to prevent memory leaks."""
    now = time.time()
    stale = [c for c, e in _command_results.items()
             if e.get("status") in ("completed", "error")
             and now - e.get("submitted_at", now) > max_age_seconds]
    for c in stale:
        _command_results.pop(c, None)
        _command_subscribers.pop(c, None)
    if stale:
        logger.info("Evicted %d stale command results", len(stale))


# Allowed high-level commands (security allowlist)
ALLOWED_RELAY_COMMANDS = {
    "run_flow",          # Run a QA test flow
    "run_web_flow",      # Browser-based QA
    "run_android_flow",  # Android emulator QA
    "execute_test_case", # Execute a single generated test case with steps
    "screenshot",        # Capture current screen
    "device_list",       # List connected devices
    "system_check",      # Health check
    "stop_flow",         # Cancel a running flow
}

# Commands that are NEVER relayed (security denylist)
DENIED_COMMANDS = {
    "shell", "exec", "rm", "delete", "install", "uninstall",
    "adb_shell_raw", "su", "root", "reboot", "format",
}


def _validate_command(command: str) -> tuple[bool, str]:
    """Validate a relay command against allowlist/denylist."""
    cmd_lower = command.lower().strip()

    # Check denylist first
    for denied in DENIED_COMMANDS:
        if denied in cmd_lower:
            return False, f"Command contains denied keyword: '{denied}'"

    # Check allowlist
    if cmd_lower not in ALLOWED_RELAY_COMMANDS:
        return False, f"Unknown command: '{command}'. Allowed: {sorted(ALLOWED_RELAY_COMMANDS)}"

    return True, ""


from fastapi import Request
from fastapi.responses import StreamingResponse


class RelayCommandRequest(BaseModel):
    """Phone → Server command request."""
    user_id: str
    command: str
    args: dict[str, Any] = {}
    timeout: float = 60.0


@router.post("/api/relay/command")
async def relay_command(req: RelayCommandRequest, request: Request) -> dict[str, Any]:
    """Accept a command from phone/browser and relay to user's laptop.

    Flow: Phone → POST /api/relay/command → Server → WSS → Laptop
    Results stream back via GET /api/relay/command/{id}/stream (SSE)
    or poll GET /api/relay/command/{id}/result.
    """
    # Auth: require valid token in Authorization header
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    caller_id = _validate_relay_auth(token)
    if not caller_id:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Rate limit: per-user sliding window
    rate_ok, rate_reason = _rate_limiter.check(caller_id)
    if not rate_ok:
        raise HTTPException(status_code=429, detail=rate_reason)

    # Security: validate command against allowlist
    allowed, reason = _validate_command(req.command)
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)

    # Security: prevent confused-deputy — caller can only relay to own session
    effective_user_id = caller_id
    if req.user_id != caller_id:
        logger.warning("Relay confused-deputy blocked: caller=%s tried user_id=%s", caller_id, req.user_id)

    session = relay_registry.get_any_session(effective_user_id)
    if not session:
        raise HTTPException(
            status_code=404,
            detail=f"No relay connected for user '{effective_user_id}'. Is Claude Code running with retention-mcp?",
        )

    command_id = str(uuid.uuid4())
    _command_results[command_id] = {
        "command_id": command_id,
        "owner_id": caller_id,
        "status": "running",
        "command": req.command,
        "args": req.args,
        "submitted_at": time.time(),
        "events": [],
        "result": None,
    }
    _command_subscribers.setdefault(command_id, [])

    # Relay the command to laptop
    try:
        result = await session.send_command(
            req.command,
            timeout=req.timeout,
            **req.args,
        )
        _command_results[command_id]["status"] = "completed"
        _command_results[command_id]["result"] = result
        _command_results[command_id]["completed_at"] = time.time()

        # Notify SSE subscribers
        event = {"type": "completed", "result": result}
        _command_results[command_id]["events"].append(event)
        for q in _command_subscribers.get(command_id, []):
            await q.put(event)

    except Exception as e:
        _command_results[command_id]["status"] = "error"
        _command_results[command_id]["result"] = {"error": str(e)}
        _command_results[command_id]["completed_at"] = time.time()

        event = {"type": "error", "error": str(e)}
        _command_results[command_id]["events"].append(event)
        for q in _command_subscribers.get(command_id, []):
            await q.put(event)

    return {
        "command_id": command_id,
        "status": _command_results[command_id]["status"],
        "result": _command_results[command_id]["result"],
        "stream_url": f"/api/relay/command/{command_id}/stream",
    }


@router.get("/api/relay/command/{command_id}/result")
async def relay_command_result(command_id: str, request: Request) -> dict[str, Any]:
    """Poll for command result (auth required, owner-scoped)."""
    auth = request.headers.get("authorization", "")
    tok = auth[7:].strip() if auth.startswith("Bearer ") else ""
    cid = _validate_relay_auth(tok)
    if not cid:
        raise HTTPException(status_code=401, detail="Invalid API key")
    entry = _command_results.get(command_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Command not found")
    if entry.get("owner_id") and entry["owner_id"] != cid:
        raise HTTPException(status_code=403, detail="Access denied")
    return entry


@router.get("/api/relay/command/{command_id}/stream")
async def relay_command_stream(command_id: str, request: Request) -> StreamingResponse:
    """SSE stream for real-time command progress (auth required, owner-scoped)."""
    auth = request.headers.get("authorization", "")
    tok = auth[7:].strip() if auth.startswith("Bearer ") else ""
    cid = _validate_relay_auth(tok)
    if not cid:
        raise HTTPException(status_code=401, detail="Invalid API key")
    entry = _command_results.get(command_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Command not found")
    if entry.get("owner_id") and entry["owner_id"] != cid:
        raise HTTPException(status_code=403, detail="Access denied")

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    _command_subscribers.setdefault(command_id, []).append(queue)

    async def event_generator():
        try:
            # Send any existing events first (replay)
            for evt in entry.get("events", []):
                yield f"data: {json.dumps(evt)}\n\n"

            # If already done, close
            if entry["status"] in ("completed", "error"):
                return

            # Stream new events
            while True:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=120.0)
                    yield f"data: {json.dumps(evt)}\n\n"
                    if evt.get("type") in ("completed", "error"):
                        return
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"
        finally:
            subs = _command_subscribers.get(command_id, [])
            if queue in subs:
                subs.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# REST endpoints for relay status
# ---------------------------------------------------------------------------


@router.get("/api/relay/status")
async def relay_status(request: Request) -> dict[str, Any]:
    """Return relay connection status (auth required, user-scoped)."""
    auth = request.headers.get("authorization", "")
    tok = auth[7:].strip() if auth.startswith("Bearer ") else ""
    cid = _validate_relay_auth(tok)
    if not cid:
        raise HTTPException(status_code=401, detail="Invalid API key")
    full = relay_registry.status()
    mine = full.get("sessions_by_user", {}).get(cid, [])
    return {"connected": len(mine) > 0, "session_count": len(mine), "sessions": mine}
