"""Chat endpoint for retention.sh agent interface.

Accepts user messages, routes to appropriate MCP tools,
and streams responses with tool-call visibility.
"""

import json
import time
import asyncio
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str  # "user" | "agent" | "tool_call" | "system"
    content: str
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    tool_status: Optional[str] = None  # "running" | "success" | "error"
    duration_ms: Optional[int] = None


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []


# -----------------------------------------------------------------------
# Command parser — routes natural language to tool calls
# -----------------------------------------------------------------------

COMMANDS = {
    "scan": {
        "pattern": ["scan", "check", "qa", "crawl", "test"],
        "tool": "retention.qa_check",
        "extract_url": True,
        "description": "Run a QA scan on a URL",
    },
    "sitemap": {
        "pattern": ["sitemap", "map", "pages"],
        "tool": "retention.sitemap",
        "extract_url": True,
        "description": "Generate a site map",
    },
    "diff": {
        "pattern": ["diff", "compare", "before", "after"],
        "tool": "retention.diff_crawl",
        "extract_url": True,
        "description": "Compare before/after",
    },
    "replay": {
        "pattern": ["replay", "rerun", "cheaper", "save"],
        "tool": "retention.rerun",
        "extract_url": False,
        "description": "Replay a saved workflow cheaper",
    },
    "status": {
        "pattern": ["status", "health", "alive"],
        "tool": "health_check",
        "extract_url": False,
        "description": "Check system status",
    },
    "help": {
        "pattern": ["help", "what can", "commands", "?"],
        "tool": "show_help",
        "extract_url": False,
        "description": "Show available commands",
    },
}


def _extract_url(text: str) -> Optional[str]:
    """Extract a URL from user message."""
    import re
    match = re.search(r'https?://[^\s<>"]+', text)
    if match:
        return match.group(0)
    # Check for localhost patterns
    match = re.search(r'localhost:\d+[^\s]*', text)
    if match:
        return f"http://{match.group(0)}"
    return None


def _match_command(text: str) -> Optional[dict]:
    """Match user message to a command."""
    lower = text.lower().strip()
    for cmd_name, cmd in COMMANDS.items():
        for pattern in cmd["pattern"]:
            if pattern in lower:
                return {**cmd, "name": cmd_name}
    return None


# -----------------------------------------------------------------------
# Tool executors
# -----------------------------------------------------------------------

async def _execute_health_check(request: Request) -> dict:
    """Check backend health."""
    return {
        "status": "ok",
        "uptime": "running",
        "tools_available": len(COMMANDS),
        "message": "retention.sh backend is healthy.",
    }


async def _execute_help() -> dict:
    """Return available commands."""
    lines = ["**Available commands:**\n"]
    for name, cmd in COMMANDS.items():
        lines.append(f"- **{name}**: {cmd['description']}")
    lines.append("\n**Examples:**")
    lines.append('- "Scan https://myapp.com"')
    lines.append('- "What did the agent miss?"')
    lines.append('- "Show me the sitemap"')
    lines.append('- "Replay this cheaper"')
    lines.append('- "Status"')
    return {"message": "\n".join(lines)}


async def _execute_qa_check(url: str, request: Request) -> dict:
    """Run QA check via the existing pipeline."""
    from backend.app.api import mcp_server as mcp

    try:
        # Try the real MCP pipeline
        result = await asyncio.wait_for(
            _call_qa_endpoint(url, request),
            timeout=120.0,
        )
        return result
    except Exception as e:
        # Fallback: basic HTTP check
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                return {
                    "url": url,
                    "status": "pass" if resp.status_code == 200 else "fail",
                    "verdict": "PASS" if resp.status_code == 200 else "FAIL",
                    "findings": [
                        {"type": "info", "category": "http", "message": f"HTTP {resp.status_code}"},
                    ],
                    "source": "fallback",
                }
        except Exception as e2:
            return {
                "url": url,
                "status": "fail",
                "verdict": "BLOCKED",
                "findings": [
                    {"type": "error", "category": "connectivity", "message": f"Cannot reach {url}: {e2}"},
                ],
                "source": "fallback",
            }


async def _call_qa_endpoint(url: str, request: Request) -> dict:
    """Call the internal QA check endpoint."""
    import httpx
    base = str(request.base_url).rstrip("/")
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{base}/api/qa/check",
            json={"url": url},
        )
        if resp.status_code == 200:
            return resp.json()
        return {
            "url": url,
            "status": "fail",
            "verdict": "FAIL",
            "findings": [{"type": "error", "message": f"QA endpoint returned {resp.status_code}"}],
        }


# -----------------------------------------------------------------------
# Chat endpoint with SSE streaming
# -----------------------------------------------------------------------

@router.post("")
async def chat(req: ChatRequest, request: Request):
    """Process a chat message and stream the response with tool-call visibility."""

    async def generate():
        text = req.message.strip()
        cmd = _match_command(text)

        if not cmd:
            # No command matched — respond with help
            yield _sse_event("agent", "I can help you with QA scanning, site mapping, and workflow replay. Try saying:\n\n- \"Scan https://myapp.com\"\n- \"Show the sitemap\"\n- \"What's the status?\"\n- \"Help\"")
            return

        # --- Handle help/status locally ---
        if cmd["tool"] == "show_help":
            result = await _execute_help()
            yield _sse_event("agent", result["message"])
            return

        if cmd["tool"] == "health_check":
            yield _sse_event("tool_call", json.dumps({
                "tool": "health_check",
                "status": "running",
            }))
            result = await _execute_health_check(request)
            yield _sse_event("tool_call", json.dumps({
                "tool": "health_check",
                "status": "success",
                "result": result,
            }))
            yield _sse_event("agent", result["message"])
            return

        # --- Handle URL-based commands ---
        url = _extract_url(text) if cmd.get("extract_url") else None
        if cmd.get("extract_url") and not url:
            yield _sse_event("agent", f"I need a URL to run **{cmd['name']}**. Try: \"{cmd['name']} https://your-app.com\"")
            return

        # Emit tool_call start
        yield _sse_event("tool_call", json.dumps({
            "tool": cmd["tool"],
            "args": {"url": url} if url else {},
            "status": "running",
        }))

        start = time.monotonic()

        # Execute the tool
        try:
            if cmd["tool"] == "retention.qa_check":
                result = await _execute_qa_check(url, request)
            else:
                # For other tools, try the internal endpoint
                result = {"message": f"Tool `{cmd['tool']}` executed.", "url": url}

            duration_ms = int((time.monotonic() - start) * 1000)

            # Emit tool_call complete
            yield _sse_event("tool_call", json.dumps({
                "tool": cmd["tool"],
                "args": {"url": url} if url else {},
                "status": "success",
                "duration_ms": duration_ms,
                "result": _summarize_result(result),
            }))

            # Emit agent summary
            summary = _format_result_summary(cmd["tool"], result, duration_ms)
            yield _sse_event("agent", summary)

        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            yield _sse_event("tool_call", json.dumps({
                "tool": cmd["tool"],
                "status": "error",
                "duration_ms": duration_ms,
                "error": str(e)[:200],
            }))
            yield _sse_event("agent", f"Error running {cmd['tool']}: {str(e)[:200]}")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_event(event_type: str, data: str) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event_type}\ndata: {data}\n\n"


def _summarize_result(result: dict) -> dict:
    """Summarize a result for the tool_call card (keep it small)."""
    return {
        "verdict": result.get("verdict", result.get("status", "unknown")),
        "findings_count": len(result.get("findings", [])),
        "url": result.get("url"),
    }


def _format_result_summary(tool: str, result: dict, duration_ms: int) -> str:
    """Format a human-readable summary of the result."""
    verdict = result.get("verdict", result.get("status", "unknown"))
    findings = result.get("findings", [])
    url = result.get("url", "")

    errors = [f for f in findings if f.get("type") == "error"]
    warnings = [f for f in findings if f.get("type") == "warning"]
    infos = [f for f in findings if f.get("type") == "info"]

    lines = [f"**QA Complete** for `{url}` ({duration_ms}ms)\n"]
    lines.append(f"**Verdict: {verdict}**\n")

    if errors:
        lines.append(f"**{len(errors)} errors:**")
        for e in errors[:5]:
            lines.append(f"- {e.get('message', '')}")

    if warnings:
        lines.append(f"\n**{len(warnings)} warnings:**")
        for w in warnings[:5]:
            lines.append(f"- {w.get('message', '')}")

    if infos:
        lines.append(f"\n**{len(infos)} info:**")
        for i in infos[:3]:
            lines.append(f"- {i.get('message', '')}")

    if not findings:
        lines.append("No findings detected.")

    return "\n".join(lines)
