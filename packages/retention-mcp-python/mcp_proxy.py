#!/usr/bin/env python3
"""MCP stdio→HTTP proxy for remote Claude Code access to retention.sh.

This script translates Claude Code's MCP protocol (JSON-RPC over stdio)
into HTTP calls to the retention.sh backend via outbound WebSocket relay.

Key feature: when you call ta.run_web_flow with a localhost URL, the proxy
automatically starts an outbound WebSocket relay so the remote emulator can
reach your local app. No inbound ports or firewall changes needed.

Setup:
  1. Copy this script to your remote machine
  2. Set env vars: RETENTION_URL, RETENTION_MCP_TOKEN
  3. Add to your Claude Code MCP config:
     {
       "mcpServers": {
         "retention": {
           "command": "python3",
           "args": ["/path/to/remote_mcp_proxy.py"],
           "env": {
             "RETENTION_URL": "https://<your-server>",
             "RETENTION_MCP_TOKEN": "<token>"
           }
         }
       }
     }

No dependencies beyond Python 3.8+ stdlib (websockets recommended for relay).
Install relay support: pip install websockets  — or —  npx retention-mcp@latest
"""

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error

BASE_URL = os.environ.get("RETENTION_URL", "").rstrip("/")
TOKEN = os.environ.get("RETENTION_MCP_TOKEN", "")

# ---------------------------------------------------------------------------
# Localhost relay manager (outbound WebSocket)
# ---------------------------------------------------------------------------

_active_relays: dict = {}  # port → {"process": Popen, "url": str}
_relay_lock = threading.Lock()


def _log(msg: str):
    """Log to stderr (stdout is reserved for MCP JSON-RPC)."""
    print(f"[ta-proxy] {msg}", file=sys.stderr, flush=True)


def _is_localhost_url(url: str) -> bool:
    """Check if URL points to localhost/127.0.0.1/0.0.0.0."""
    return bool(re.match(
        r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?",
        url or "",
        re.IGNORECASE,
    ))


def _extract_port(url: str) -> int:
    """Extract port from localhost URL, defaulting to 80/443."""
    m = re.match(r"https?://[^:]+:(\d+)", url)
    if m:
        return int(m.group(1))
    return 443 if url.startswith("https") else 80


def _ensure_websockets() -> bool:
    """Check if the websockets package is available for outbound relay."""
    try:
        import importlib
        importlib.import_module("websockets")
        return True
    except ImportError:
        return False


def _start_relay(port: int) -> str:
    """Start an outbound WebSocket relay for a local port. Returns the relay URL."""
    with _relay_lock:
        if port in _active_relays:
            return _active_relays[port]["url"]

    if not _ensure_websockets():
        raise RuntimeError(
            "websockets package not found. Install it:\n"
            "  pip install websockets\n"
            "  — or —\n"
            "  npx retention-mcp@latest"
        )

    relay_ws_url = f"{BASE_URL.replace('http', 'ws')}/ws/agent-relay"
    _log(f"Starting outbound relay for localhost:{port} → {relay_ws_url}...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "websockets", "relay",
         f"http://localhost:{port}", relay_ws_url],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Give the relay a moment to connect
    relay_url = f"{BASE_URL}/relay/{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(0.5)
        # Check if process is still running (relay connected)
        if proc.poll() is None:
            relay_url = f"{BASE_URL}/relay/{port}"
            break

    if proc.poll() is not None:
        raise RuntimeError(f"Failed to start relay for port {port}. Is your app running on localhost:{port}?")

    with _relay_lock:
        _active_relays[port] = {"process": proc, "url": relay_url}

    _log(f"Relay active: localhost:{port} → {relay_url}")
    return relay_url


def _stop_relay(port: int):
    """Stop a relay for a specific port."""
    with _relay_lock:
        info = _active_relays.pop(port, None)
    if info and info["process"].poll() is None:
        info["process"].terminate()
        try:
            info["process"].wait(timeout=5)
        except subprocess.TimeoutExpired:
            info["process"].kill()
        _log(f"Relay stopped for port {port}")


def _stop_all_relays():
    """Stop all active relays on exit."""
    with _relay_lock:
        ports = list(_active_relays.keys())
    for port in ports:
        _stop_relay(port)


def _rewrite_localhost_url(url: str) -> str:
    """If URL is localhost, start a relay and return the public URL."""
    if not _is_localhost_url(url):
        return url
    port = _extract_port(url)
    relay_url = _start_relay(port)
    # Preserve path after the host:port
    m = re.match(r"https?://[^/]+(/.*)$", url)
    path = m.group(1) if m else ""
    return relay_url + path


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _headers():
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _http_get(path: str):
    req = urllib.request.Request(f"{BASE_URL}{path}", headers=_headers())
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _http_post(path: str, body: dict):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Client-side tools (run on user's machine, not forwarded to server)
# ---------------------------------------------------------------------------

_CLIENT_TOOLS = [
    {
        "name": "ta.expose_local_app",
        "description": (
            "Expose your local development server to retention.sh's remote emulator. "
            "Starts an outbound WebSocket relay so the emulator can reach your localhost app. "
            "Returns the public URL to use with ta.run_web_flow."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {
                    "type": "number",
                    "description": "Local port your app is running on (e.g. 3000, 5173, 8080)",
                },
                "url": {
                    "type": "string",
                    "description": "Full local URL (e.g. http://localhost:3000). Alternative to port.",
                },
            },
        },
    },
    {
        "name": "ta.stop_relay",
        "description": "Stop an active outbound relay for a local port.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {
                    "type": "number",
                    "description": "Local port to stop relaying",
                },
            },
            "required": ["port"],
        },
    },
    {
        "name": "ta.list_relays",
        "description": "List all active outbound relays exposing local apps to retention.sh.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _handle_client_tool(tool_name: str, arguments: dict) -> dict:
    """Handle tools that run on the client machine."""
    if tool_name == "ta.expose_local_app":
        url = arguments.get("url", "")
        port = arguments.get("port")
        if url:
            port = _extract_port(url)
        elif port:
            url = f"http://localhost:{int(port)}"
        else:
            return {"error": "Provide either 'port' or 'url'"}

        port = int(port)
        try:
            relay_url = _start_relay(port)
            return {
                "status": "ok",
                "local": f"http://localhost:{port}",
                "public_url": relay_url,
                "message": (
                    f"Your app is now accessible at {relay_url}. "
                    f"Use this URL with ta.run_web_flow to test your app on the emulator."
                ),
            }
        except RuntimeError as e:
            return {"status": "error", "error": str(e)}

    if tool_name == "ta.stop_relay":
        port = int(arguments.get("port", 0))
        _stop_relay(port)
        return {"status": "ok", "message": f"Relay for port {port} stopped"}

    if tool_name == "ta.list_relays":
        with _relay_lock:
            relays = [
                {"port": p, "url": info["url"], "alive": info["process"].poll() is None}
                for p, info in _active_relays.items()
            ]
        return {"relays": relays, "count": len(relays)}

    return {"error": f"Unknown client tool: {tool_name}"}


_CLIENT_TOOL_NAMES = {t["name"] for t in _CLIENT_TOOLS}

# ---------------------------------------------------------------------------
# Tools that need URL rewriting (auto-relay localhost)
# ---------------------------------------------------------------------------

_URL_REWRITE_TOOLS = {"ta.run_web_flow", "ta.pipeline.run"}
_URL_PARAM_NAMES = {"url", "app_url"}


def _maybe_rewrite_urls(tool_name: str, arguments: dict) -> dict:
    """Auto-relay localhost URLs before forwarding to the server."""
    if tool_name not in _URL_REWRITE_TOOLS:
        return arguments
    rewritten = dict(arguments)
    for param in _URL_PARAM_NAMES:
        val = rewritten.get(param, "")
        if val and _is_localhost_url(val):
            _log(f"Auto-relaying {param}={val}")
            rewritten[param] = _rewrite_localhost_url(val)
            _log(f"  → {rewritten[param]}")
    return rewritten


# ---------------------------------------------------------------------------
# MCP JSON-RPC handler
# ---------------------------------------------------------------------------

def handle_request(msg: dict) -> dict:
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params", {})

    try:
        if method == "initialize":
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "retention-remote", "version": "2.0.0"},
                },
            }

        if method == "notifications/initialized":
            return None  # No response needed for notifications

        if method == "tools/list":
            # Get server tools
            tools_raw = _http_get("/mcp/tools")
            tools = []
            for t in tools_raw:
                props = {}
                required = []
                for p in t.get("parameters", []):
                    props[p["name"]] = {"type": p["type"], "description": p["description"]}
                    if p.get("required"):
                        required.append(p["name"])
                tools.append({
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": {
                        "type": "object",
                        "properties": props,
                        **({"required": required} if required else {}),
                    },
                })
            # Append client-side tools
            tools.extend(_CLIENT_TOOLS)
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}}

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            # Handle client-side tools locally
            if tool_name in _CLIENT_TOOL_NAMES:
                result = _handle_client_tool(tool_name, arguments)
                is_error = result.get("status") == "error" or "error" in result
                return {
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                        "isError": is_error and "relays" not in result,
                    },
                }

            # Auto-relay localhost URLs before forwarding
            arguments = _maybe_rewrite_urls(tool_name, arguments)

            resp = _http_post("/mcp/tools/call", {"tool": tool_name, "arguments": arguments})
            content_text = json.dumps(resp.get("result", resp), indent=2) if resp.get("status") == "ok" else resp.get("error", "Unknown error")
            is_error = resp.get("status") != "ok"
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": content_text}],
                    "isError": is_error,
                },
            }

        # Unknown method
        return {
            "jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"},
        }

    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else str(e)
        return {
            "jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32000, "message": f"HTTP {e.code}: {error_body[:200]}"},
        }
    except Exception as e:
        return {
            "jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32000, "message": str(e)},
        }


def main():
    if not BASE_URL:
        print("Error: RETENTION_URL environment variable not set", file=sys.stderr)
        print("", file=sys.stderr)
        print("Quick setup:", file=sys.stderr)
        print("  export RETENTION_URL='https://<your-server>'", file=sys.stderr)
        print("  export RETENTION_MCP_TOKEN='<token>'", file=sys.stderr)
        print("", file=sys.stderr)
        print("Or add to Claude Code MCP config (.mcp.json):", file=sys.stderr)
        print(json.dumps({
            "mcpServers": {
                "retention": {
                    "command": "python3",
                    "args": [os.path.abspath(__file__)],
                    "env": {
                        "RETENTION_URL": "https://<your-server>",
                        "RETENTION_MCP_TOKEN": "<token>",
                    },
                }
            }
        }, indent=2), file=sys.stderr)
        sys.exit(1)

    # Clean up relays on exit
    signal.signal(signal.SIGTERM, lambda *_: (_stop_all_relays(), sys.exit(0)))
    signal.signal(signal.SIGINT, lambda *_: (_stop_all_relays(), sys.exit(0)))
    import atexit
    atexit.register(_stop_all_relays)

    _log(f"Connected to retention.sh at {BASE_URL}")
    _log("Ready — localhost URLs will be auto-relayed via outbound WebSocket")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        response = handle_request(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
