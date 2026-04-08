"""
retention.sh MCP — Entry point for the thin local relay.

Usage:
    python -m retention-mcp
    # or
    npx retention-mcp@latest

Connects outbound to the retention.sh server via WebSocket. Receives emulator
commands, executes them locally via ADB, and streams results back.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

# Use try/except to support both execution modes:
# - `python main.py` (script mode, PYTHONPATH set by cli.js) → absolute imports
# - `python -m retention_mcp` (module mode) → relative imports
try:
    from auth import build_auth_headers
    from ws_client import TAWebSocketClient, DEFAULT_SERVER_URL
    from emulator_relay import dispatch_command
    from stream import FrameStreamer
except ImportError:
    from .auth import build_auth_headers
    from .ws_client import TAWebSocketClient, DEFAULT_SERVER_URL
    from .emulator_relay import dispatch_command
    from .stream import FrameStreamer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("retention-mcp")


async def main() -> None:
    """Start the thin relay — connect out to retention.sh server."""
    server_url = os.getenv("TA_STUDIO_URL", "").rstrip("/")
    if server_url:
        # Convert HTTP URL to WebSocket URL
        ws_url = server_url.replace("https://", "wss://").replace("http://", "ws://")
        if not ws_url.endswith("/ws/agent-relay"):
            ws_url += "/ws/agent-relay"
    else:
        ws_url = DEFAULT_SERVER_URL

    logger.info("retention.sh thin relay starting")
    logger.info("Server: %s", ws_url)

    # Build auth headers
    try:
        auth_headers = build_auth_headers()
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    client = TAWebSocketClient(server_url=ws_url, auth_headers=auth_headers)
    streamer = FrameStreamer(send_fn=client.send)

    # -- Handle commands from server -----------------------------------------

    async def on_command(msg: dict) -> None:
        """Handle command messages from the TA agent."""
        response = await dispatch_command(msg)
        await client.send(response)

    async def on_stream_start(msg: dict) -> None:
        """Start frame streaming when server requests it."""
        session_id = msg.get("session_id", "default")
        device = msg.get("device")
        fps = msg.get("fps", 2)
        streamer._device = device
        streamer._fps = min(fps, 10)
        await streamer.start(session_id)

    async def on_stream_stop(_msg: dict) -> None:
        """Stop frame streaming."""
        await streamer.stop()

    async def on_connected(_msg: dict) -> None:
        """Send identity info on connection."""
        try:
            from emulator_relay import handle_list_devices
        except ImportError:
            from .emulator_relay import handle_list_devices
        devices = await handle_list_devices({})
        await client.send({
            "type": "relay_ready",
            "version": "1.0.0",
            "devices": devices.get("devices", []),
        })

    client.on("command", on_command)
    client.on("stream_start", on_stream_start)
    client.on("stream_stop", on_stream_stop)
    client.on("connected", on_connected)

    # Run until interrupted
    try:
        await client.connect()
    except KeyboardInterrupt:
        await client.disconnect()


def run() -> None:
    """Synchronous entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
