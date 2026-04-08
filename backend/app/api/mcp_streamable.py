"""Streamable HTTP MCP server — spec-compliant MCP over HTTP.

Wraps the existing retention.sh tool dispatch (mcp_server.py call_tool) behind
a proper MCP JSON-RPC 2.0 interface using FastMCP + Streamable HTTP transport.

This lets Claude Code (and any MCP client) connect with just a URL:
  .mcp.json: {"mcpServers": {"retention": {"type": "http", "url": "https://host/mcp-stream/mcp"}}}

No proxy script, no local process, no stdio — just HTTP.
"""

import inspect
import json
import logging
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Create the FastMCP instance (stateless — no session affinity needed)
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="retention",
    instructions=(
        "retention.sh — AI-powered QA automation for mobile and web apps. "
        "Run full verification pipelines (crawl → workflow → testcase → execution) "
        "on any web or Android app using real emulators. "
        "Start with ta_system_check to verify connectivity, "
        "then ta_run_web_flow or ta_pipeline_run to test an app."
    ),
    stateless_http=True,
)


# ---------------------------------------------------------------------------
# Bridge to existing tool dispatch
# ---------------------------------------------------------------------------

async def _bridge_call(tool_name: str, **kwargs) -> str:
    """Call the existing tool dispatch and return the result as JSON string."""
    from .mcp_server import call_tool, MCPToolCallRequest

    args = {k: v for k, v in kwargs.items() if v is not None}
    req = MCPToolCallRequest(tool=tool_name, arguments=args)
    try:
        resp = await call_tool(req)
        if resp.status == "error":
            return json.dumps({"error": resp.error}, default=str)
        return json.dumps(resp.result, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


# ---------------------------------------------------------------------------
# Dynamic function builder with proper signatures
# ---------------------------------------------------------------------------

# Map MCP schema types to Python type annotations
_TYPE_MAP = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _build_typed_handler(tool_name: str, params: list):
    """Build an async function with a proper typed signature matching the tool's params.

    FastMCP introspects the function signature to build the inputSchema.
    We dynamically create a function with the exact parameters each tool expects.
    """
    safe_name = tool_name.replace(".", "_")

    # Build the parameter list for the function signature
    sig_params = []
    for p in params:
        py_type = _TYPE_MAP.get(p.type, str)
        if p.required:
            sig_params.append(
                inspect.Parameter(
                    p.name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=py_type,
                )
            )
        else:
            sig_params.append(
                inspect.Parameter(
                    p.name,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=None,
                    annotation=Optional[py_type],
                )
            )

    # Create the handler with the captured tool_name
    captured = tool_name

    async def handler(**kwargs) -> str:
        return await _bridge_call(captured, **kwargs)

    # Set the proper signature so FastMCP sees typed params
    handler.__signature__ = inspect.Signature(parameters=sig_params)
    handler.__name__ = safe_name
    handler.__qualname__ = safe_name
    handler.__doc__ = f"Bridge to {tool_name}"

    return handler


# ---------------------------------------------------------------------------
# Register tools
# ---------------------------------------------------------------------------

def _register_tools():
    """Register all retention.sh tools with FastMCP with proper typed signatures."""
    from .mcp_server import _TOOLS

    registered = 0
    for tool_def in _TOOLS:
        safe_name = tool_def.name.replace(".", "_")
        handler = _build_typed_handler(tool_def.name, tool_def.parameters)
        try:
            mcp.add_tool(fn=handler, name=safe_name, description=tool_def.description)
            registered += 1
        except Exception as e:
            logger.warning(f"Failed to register tool {tool_def.name}: {e}")

    logger.info(f"Registered {registered}/{len(_TOOLS)} tools with Streamable HTTP MCP")


# ---------------------------------------------------------------------------
# Auth middleware for Streamable HTTP MCP
# ---------------------------------------------------------------------------

class MCPAuthMiddleware:
    """ASGI middleware that validates Bearer token on MCP requests.

    Skips auth if RETENTION_MCP_TOKEN is not set (local-only mode).
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        expected = os.getenv("RETENTION_MCP_TOKEN", "").strip()
        if not expected:
            # Auth disabled — local use only
            await self.app(scope, receive, send)
            return

        # Extract Authorization header
        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        if not auth.startswith("Bearer "):
            response = JSONResponse(
                {"error": "Missing Authorization: Bearer <token> header"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        import hmac
        token = auth[7:].strip()
        if not hmac.compare_digest(token, expected):
            response = JSONResponse(
                {"error": "Invalid MCP token"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Create the ASGI app (no lifespan — main app manages session manager)
# ---------------------------------------------------------------------------

def create_mcp_app() -> Starlette:
    """Create the Streamable HTTP MCP Starlette app.

    The session manager's lifespan is handled by main.py's lifespan,
    not by this sub-app. Auth is enforced via MCPAuthMiddleware.
    """
    _register_tools()

    # Trigger lazy creation of session manager
    _ = mcp.streamable_http_app()
    session_manager = mcp._session_manager

    # Get the raw ASGI handler
    asgi_handler = StreamableHTTPASGIApp(session_manager)

    # Create a bare Starlette app with auth middleware
    return Starlette(
        routes=[Route("/mcp", endpoint=asgi_handler)],
        middleware=[Middleware(MCPAuthMiddleware)],
    )
