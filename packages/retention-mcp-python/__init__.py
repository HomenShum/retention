"""
retention.sh MCP — Thin local relay package.

This package runs on the user's machine and connects OUT to the retention.sh
server via outbound WebSocket. No ports are opened. No tunnel required.

Components:
  - ws_client: Outbound WebSocket connection with auto-reconnect
  - auth: API key authentication for the handshake
  - emulator_relay: Receives and executes ADB commands from server
  - stream: Captures and streams emulator frames to server
"""

__version__ = "1.0.0"
