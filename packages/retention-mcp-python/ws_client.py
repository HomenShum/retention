"""
retention.sh — Outbound WebSocket client.

Connects OUT from the user's machine to the retention.sh server. No ports are
opened, nothing is exposed. The connection carries the user's identity via
the API key handshake.

Features:
  - Auto-reconnect with exponential backoff (2s → 60s cap)
  - Heartbeat / keep-alive every 30 seconds
  - JSON message dispatch to registered handlers
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

# Default server endpoint — overridden by TA_STUDIO_URL env var
DEFAULT_SERVER_URL = "wss://retention-backend.onrender.com/ws/agent-relay"

# Reconnect parameters
INITIAL_BACKOFF = 2.0
MAX_BACKOFF = 60.0
HEARTBEAT_INTERVAL = 30.0

MessageHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class TAWebSocketClient:
    """Outbound WebSocket client that connects to the retention.sh server."""

    def __init__(
        self,
        server_url: str = DEFAULT_SERVER_URL,
        auth_headers: Optional[dict[str, str]] = None,
    ):
        self.server_url = server_url
        self.auth_headers = auth_headers or {}
        self._ws: Any = None  # websockets.WebSocketClientProtocol
        self._handlers: dict[str, list[MessageHandler]] = {}
        self._running = False
        self._backoff = INITIAL_BACKOFF

    # -- Handler registration ------------------------------------------------

    def on(self, message_type: str, handler: MessageHandler) -> None:
        """Register a handler for a specific message type."""
        self._handlers.setdefault(message_type, []).append(handler)

    # -- Connection lifecycle ------------------------------------------------

    async def connect(self) -> None:
        """Connect to the server and start the message loop with auto-reconnect."""
        try:
            import websockets  # type: ignore[import-untyped]
        except ImportError:
            raise ImportError(
                "websockets package required: pip install websockets"
            )

        self._running = True
        while self._running:
            try:
                logger.info("Connecting to %s ...", self.server_url)
                async with websockets.connect(
                    self.server_url,
                    additional_headers=self.auth_headers,
                    ping_interval=HEARTBEAT_INTERVAL,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._backoff = INITIAL_BACKOFF
                    logger.info("Connected to retention.sh server")
                    await self._dispatch_event("connected", {})
                    await self._message_loop(ws)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(
                    "Connection lost (%s). Reconnecting in %.0fs ...",
                    e,
                    self._backoff,
                )
                await self._dispatch_event("disconnected", {"error": str(e)})
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, MAX_BACKOFF)

        self._ws = None
        logger.info("WebSocket client stopped")

    async def disconnect(self) -> None:
        """Gracefully close the connection."""
        self._running = False
        if self._ws:
            await self._ws.close()

    async def send(self, message: dict[str, Any]) -> None:
        """Send a JSON message to the server."""
        if not self._ws:
            raise ConnectionError("Not connected to retention.sh server")
        await self._ws.send(json.dumps(message))

    # -- Internal ------------------------------------------------------------

    async def _message_loop(self, ws: Any) -> None:
        """Read messages and dispatch to handlers."""
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Non-JSON message received, ignoring")
                continue
            msg_type = msg.get("type", "unknown")
            await self._dispatch_event(msg_type, msg)

    async def _dispatch_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Dispatch an event to all registered handlers."""
        for handler in self._handlers.get(event_type, []):
            try:
                await handler(data)
            except Exception:
                logger.exception("Handler error for event '%s'", event_type)
        # Also dispatch to wildcard handlers
        for handler in self._handlers.get("*", []):
            try:
                await handler(data)
            except Exception:
                logger.exception("Wildcard handler error for event '%s'", event_type)
