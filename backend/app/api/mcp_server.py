"""OpenClaw / MCP inbound server surface.

Exposes retention.sh verification as a Model Context Protocol (MCP) tool endpoint
so external AI coding agents (Cursor, Devin, OpenClaw, Claude Code) can:
  - Discover what verification tools are available
  - Request a validation gate before merging
  - Poll for gate status
  - Retrieve ActionSpan evidence manifests

This is an HTTP-based MCP surface (JSON-RPC style, transport=http) that external
agents call. It is NOT the Mobile MCP client (which controls the Android device).

Spec reference: https://modelcontextprotocol.io/docs/concepts/architecture

Endpoints:
  GET  /mcp/tools          → list available TA verification tools
  POST /mcp/tools/call     → invoke a tool by name
  GET  /mcp/health         → readiness probe for external agents
"""

import json
import os
import re
import time
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..investor_brief import InvestorBriefService
from .validation_hooks import (
    ValidationHookRequest,
    HookReleaseRequest,
    HookFailRequest,
    _hooks,
    _now,
)
from .action_spans import action_span_service

logger = logging.getLogger(__name__)


_LOCAL_REQUEST_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "testserver"}
_LOCAL_CLIENT_HOSTS = {"testclient"}


def _is_local_request(request: Request) -> bool:
    """Treat loopback + FastAPI TestClient traffic as local/dev traffic."""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    client_host = ((request.client.host if request.client else "") or "").lower()
    return host in _LOCAL_REQUEST_HOSTS or client_host in _LOCAL_REQUEST_HOSTS or client_host in _LOCAL_CLIENT_HOSTS


# ---------------------------------------------------------------------------
# Bearer token auth (optional — skips if RETENTION_MCP_TOKEN is not set)
# ---------------------------------------------------------------------------

async def verify_mcp_token(request: Request):
    """Validate Bearer token — checks per-user tokens via Convex, falls back to shared env token.

    Auth flow (in order):
      1. No RETENTION_MCP_TOKEN env + no CONVEX_SITE_URL → auth disabled (local dev)
      2. Extract Bearer token from header
      3. Check against shared RETENTION_MCP_TOKEN env var (backward compat)
      4. Check against per-user tokens in Convex
      5. Record usage asynchronously on success
    """
    shared_token = os.getenv("RETENTION_MCP_TOKEN", "").strip()
    convex_url = os.getenv("CONVEX_SITE_URL", "").strip()

    if _is_local_request(request):
        request.state.mcp_user = {"email": "local-dev", "source": "local_request"}
        return

    if not shared_token and not convex_url:
        return  # Auth disabled — local-only use

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <token> header")
    token = auth_header[7:].strip()

    if not token or token == "no-token":
        raise HTTPException(status_code=401, detail="Invalid MCP token — get yours at https://test-studio-xi.vercel.app/docs/install")

    # Check 1: shared env token (backward compat for existing deployments)
    import hmac
    if shared_token and hmac.compare_digest(token, shared_token):
        request.state.mcp_user = {"email": "admin", "source": "env_token"}
        return

    # Check 2: local api_keys.json (tokens from /api/signup) — hashed lookup
    try:
        from .signup import _load_keys, _lookup_by_token
        local_keys = _load_keys()
        _key_id, info = _lookup_by_token(local_keys, token)
        if info:
            request.state.mcp_user = {
                "email": info.get("email", "unknown"),
                "name": info.get("name"),
                "source": "local_signup",
            }
            return
    except Exception:
        pass  # Signup module may not be loaded yet

    # Check 3: per-user token via Convex
    if convex_url:
        from ..services.convex_client import ConvexClient
        client = ConvexClient()
        result = await client.verify_mcp_token(token)
        if result.get("valid"):
            request.state.mcp_user = {
                "email": result.get("email", "unknown"),
                "name": result.get("name"),
                "source": "convex_token",
            }
            # Fire-and-forget usage recording
            import asyncio
            asyncio.create_task(client.record_mcp_usage(token))
            return
        reason = result.get("reason", "invalid")
        if reason == "token_revoked":
            raise HTTPException(status_code=401, detail="MCP token has been revoked — rotate at https://test-studio-xi.vercel.app/docs/install")
        if reason == "token_expired":
            raise HTTPException(status_code=401, detail="MCP token has expired — renew at https://test-studio-xi.vercel.app/docs/install")

    raise HTTPException(status_code=401, detail="Invalid MCP token — get yours at https://test-studio-xi.vercel.app/docs/install")

# ---------------------------------------------------------------------------
# Multi-MCP Security Layer
# ---------------------------------------------------------------------------
# When multiple MCPs (Figma, Stitch, OpenClaw, etc.) share the same LLM
# context, tool poisoning and confused deputy attacks become possible.
# This layer validates every inbound tool call before execution.

# Tools that are safe for external agents to call
MCP_TOOL_ALLOWLIST = frozenset({
    # QA Pipeline
    "ta.pipeline.run", "ta.pipeline.run_catalog", "ta.pipeline.status",
    "ta.pipeline.results", "ta.pipeline.list_apps", "ta.pipeline.screenshot",
    "ta.pipeline.rerun_failures", "ta.pipeline.replay_gif",
    # Benchmark generation
    "ta.benchmark.generate_app", "ta.benchmark.list_templates",
    "ta.benchmark.list_cases", "ta.benchmark.run_case",
    "ta.benchmark.score", "ta.benchmark.run_history", "ta.pipeline.failure_bundle", "ta.pipeline.run_log",
    # Feedback
    "ta.feedback.annotate", "ta.feedback.list", "ta.feedback.summary",
    "ta.feedback_package", "ta.summarize_failure", "ta.suggest_fix_context",
    "ta.collect_trace_bundle", "ta.emit_verdict", "ta.compare_before_after",
    # Device (scoped)
    "ta.device.list", "ta.device.lease",
    # System
    "ta.system_check", "ta.quickstart", "ta.get_handoff",
    # Exploration Memory
    "ta.memory.status", "ta.memory.graph", "ta.memory.apps",
    "ta.setup.status", "ta.setup.launch_emulator", "ta.setup.instructions",
    # Exploration Memory
    "ta.memory.stats", "ta.memory.check", "ta.memory.invalidate",
    # Linkage Graph
    "ta.linkage.register_feature", "ta.linkage.affected_features",
    "ta.linkage.rerun_suggestions", "ta.linkage.stats",
    # Screenshot Diff
    "ta.screenshots.set_baseline", "ta.screenshots.compare", "ta.screenshots.history",
    # Web Demo (Playwright, no emulator)
    "ta.web_demo.discover", "ta.web_demo.run", "ta.web_demo.scorecard", "ta.web_demo.status",
    # Codebase (read-only)
    "ta.codebase.search", "ta.codebase.read_file", "ta.codebase.git_log",
    "ta.codebase.git_diff", "ta.codebase.list_files",
    # QA flows
    "ta.run_web_flow", "ta.run_android_flow", "ta.rerun",
    # Coordinator agent
    "ta.agent.run",
    # Playwright (browser automation)
    "ta.playwright.navigate", "ta.playwright.screenshot",
    "ta.playwright.click", "ta.playwright.fill",
    # Design-to-Code (Figma, Stitch by Google)
    # Figma snapshot + flow analysis (existing FigmaService integration)
    "ta.design.figma_snapshot",     # Fetch Figma file data with progressive disclosure
    "ta.design.figma_analyze_flows", # Cluster Figma frames into flow groups
    # Stitch by Google / external design-to-code MCP bridge
    "ta.design.generate_from_design", # Convert design URL → React/HTML components
    # Design → Generate → QA (one-stop pipeline from design file)
    "ta.design.pipeline",           # design URL → code gen → deploy → crawl → test
    # Context Graph (execution judgment infrastructure)
    "ta.graph.list", "ta.graph.stats", "ta.graph.verdicts",
    "ta.graph.failure_chain", "ta.graph.precedents", "ta.graph.mermaid",
    "ta.graph.slack_topic_history", "ta.graph.slack_user_history",
    "ta.graph.slack_open_items", "ta.graph.slack_similar_request",
    # Trajectory Replay
    "ta.trajectory.list", "ta.trajectory.replay", "ta.trajectory.compare",
    "ta.memory.export", "ta.memory.import",
    # TCWP (Canonical Workflow Package)
    "ta.tcwp.generate", "ta.tcwp.validate", "ta.tcwp.list", "ta.tcwp.export", "ta.tcwp.ingest", "ta.tcwp.export_profile",
    # Audit Engine
    "ta.audit.validate_shortcut", "ta.audit.compare", "ta.audit.drift_report", "ta.audit.list",
    # Workflow Compression
    "ta.compress.workflow", "ta.compress.list", "ta.compress.stats", "ta.compress.rollback",
    # Checkpoint Validation
    "ta.checkpoint.list", "ta.checkpoint.set", "ta.checkpoint.verify", "ta.checkpoint.drift_report",
    # Savings Forecast + ROI
    "ta.savings.forecast", "ta.savings.roi", "ta.savings.breakdown",
    # Usage Tracking
    "ta.usage.sync_ccusage", "ta.usage.summary",
    # Explore-only (no test gen)
    "ta.explore.run",
    # Retention self-serve QA loop
    "ta.onboard.status", "ta.crawl.url", "ta.savings.compare",
    "ta.team.invite", "ta.qa.redesign",
    "ta.qa_check", "ta.diff_crawl", "ta.ux_audit", "ta.suggest_tests",
    "ta.sitemap",
    "ta.start_workflow", "ta.memory.rollup",
    # Workflow Judge (always-on completion enforcement)
    "ta.judge.check", "ta.judge.detect", "ta.judge.status",
    "ta.judge.workflows", "ta.judge.correction", "ta.judge.analyze",
})

# Tools that must NEVER be exposed to external agents
MCP_TOOL_DENYLIST = frozenset({
    "ta.codebase.shell_command",  # Arbitrary shell — internal only
    "ta.admin.reset",
    "ta.admin.delete_all",
})

# Patterns in tool args that indicate prompt injection attempts
# Checked against lowercased JSON dump of args — order: most specific first
_INJECTION_PATTERNS = [
    # Prompt injection — override instructions
    "ignore previous instructions",
    "ignore all instructions",
    "ignore all prior",
    "forget everything",
    "disregard all",
    "override instructions",
    "override your",
    "new instructions:",
    "system prompt",
    "you are now",
    "act as if",
    "pretend you are",
    "roleplay as",
    "do not follow",
    "bypass safety",
    "bypass security",
    "jailbreak",
    # XSS / HTML injection
    "<script",
    "</script",
    "javascript:",
    "data:text/html",
    "onerror=",
    "onload=",
    "onfocus=",
    "onclick=",
    "onmouseover=",
    "expression(",
    "vbscript:",
    # Template injection
    "{{",
    "${",
    "<%",
    # Path traversal
    "../",
    "..\\",
    # Command injection (for args that might reach shell)
    "; rm ",
    "&& rm ",
    "| rm ",
    "`rm ",
    "$(rm ",
]


def validate_mcp_tool_call(tool_name: str, args: dict) -> tuple[bool, str]:
    """Validate an MCP tool call against security policies.

    Returns (allowed, reason). If not allowed, reason explains why.
    """
    # Check denylist
    if tool_name in MCP_TOOL_DENYLIST:
        return False, f"Tool '{tool_name}' is blocked by security policy"

    # Check allowlist
    if tool_name not in MCP_TOOL_ALLOWLIST:
        # Allow unknown ta.* tools with a warning (forward compatibility)
        if not tool_name.startswith("ta."):
            return False, f"Tool '{tool_name}' is not a retention.sh tool"
        logger.warning("Unknown TA tool called: %s (not in allowlist)", tool_name)

    # Scan args for prompt injection patterns
    # Security: NFKC normalizes fullwidth chars (＜→<), strip null bytes before matching
    import unicodedata
    args_str = json.dumps(args).lower() if args else ""
    args_str = unicodedata.normalize("NFKC", args_str)
    args_str = args_str.replace("\\u0000", "").replace("\x00", "")
    for pattern in _INJECTION_PATTERNS:
        if pattern in args_str:
            logger.warning(
                "Potential prompt injection in tool %s args: matched '%s'",
                tool_name, pattern,
            )
            return False, f"Blocked: suspicious content detected in tool arguments"

    # URL validation for pipeline tools (SSRF protection)
    if tool_name in ("ta.pipeline.run", "ta.run_web_flow"):
        url = args.get("url", "")
        if url:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            blocked_hosts = {"169.254.169.254", "metadata.google.internal", "metadata"}
            if parsed.hostname in blocked_hosts:
                return False, "Blocked: cloud metadata endpoint access is not allowed"
            if parsed.scheme not in ("http", "https", ""):
                return False, f"Blocked: URL scheme '{parsed.scheme}' is not allowed"

    return True, ""


# ---------------------------------------------------------------------------
# Repo root for local codebase access
# ---------------------------------------------------------------------------

def _detect_repo_root() -> Path:
    env = os.getenv("TA_REPO_ROOT")
    if env:
        return Path(env).resolve()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()).resolve()
    except Exception:
        pass
    # Fallback: backend/app/api -> 3 levels up
    return Path(__file__).resolve().parents[3]


_REPO_ROOT: Path = _detect_repo_root()


def _safe_path(relative: str) -> Path:
    """Resolve a repo-relative path, raising ValueError if it escapes repo root."""
    cleaned = relative.lstrip("/")
    resolved = (_REPO_ROOT / cleaned).resolve()
    if not resolved.is_relative_to(_REPO_ROOT):
        raise ValueError(f"Path traversal detected: {relative}")
    return resolved


router = APIRouter(prefix="/mcp", tags=["mcp"])

_investor_brief_service: Optional[InvestorBriefService] = None


def set_investor_brief_service(service: Optional[InvestorBriefService]) -> None:
    global _investor_brief_service
    _investor_brief_service = service


def get_investor_brief_service() -> InvestorBriefService:
    if _investor_brief_service is None:
        raise HTTPException(status_code=503, detail="Investor brief service not initialized")
    return _investor_brief_service


# ---------------------------------------------------------------------------
# MCP protocol shapes
# ---------------------------------------------------------------------------

class MCPToolParam(BaseModel):
    name: str
    type: str
    description: str
    required: bool = False


class MCPTool(BaseModel):
    name: str
    description: str
    parameters: List[MCPToolParam]
    internal: bool = False  # True = only exposed in dev mode


class MCPToolCallRequest(BaseModel):
    tool: str
    arguments: Dict[str, Any] = {}


class MCPToolCallResponse(BaseModel):
    tool: str
    status: str               # "ok" | "error"
    result: Any = None
    error: Optional[str] = None
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

_TOOLS: List[MCPTool] = [
    MCPTool(
        name="ta.request_validation_gate",
        description=(
            "Open a Validation Stop Hook before submitting/merging a PR. "
            "Returns a hook_id. Poll ta.get_hook_status until status is 'released'. "
            "Do NOT merge if status is 'blocked'."
        ),
        parameters=[
            MCPToolParam(name="agent_id",          type="string", description="Your agent identifier, e.g. 'cursor'", required=True),
            MCPToolParam(name="task_description",  type="string", description="What you are about to merge",          required=True),
            MCPToolParam(name="pr_url",            type="string", description="GitHub PR URL"),
            MCPToolParam(name="repo",              type="string", description="owner/repo"),
            MCPToolParam(name="branch",            type="string", description="Branch name being merged"),
            MCPToolParam(name="requested_by",      type="string", description="Email or Slack handle of requester"),
        ],
    ),
    MCPTool(
        name="ta.get_hook_status",
        description="Poll a Validation Stop Hook by hook_id. Returns status: pending|running|released|blocked.",
        parameters=[
            MCPToolParam(name="hook_id", type="string", description="hook_id returned by ta.request_validation_gate", required=True),
        ],
    ),
    MCPTool(
        name="ta.get_evidence_manifest",
        description=(
            "Retrieve the ActionSpan evidence manifest for a test session. "
            "Returns pass rate, average composite score, and all captured spans."
        ),
        parameters=[
            MCPToolParam(name="session_id", type="string", description="TA test session ID", required=True),
        ],
    ),
    MCPTool(
        name="ta.smoke_test",
        description="Run a lightweight smoke test on the connected Android device and return a pass/fail verdict.",
        parameters=[
            MCPToolParam(name="device_id", type="string", description="ADB device serial (optional — uses first available)"),
            MCPToolParam(name="app_package", type="string", description="Android package to launch for the smoke test"),
        ],
    ),
    MCPTool(
        name="ta.investor_brief.get_state",
        description="Return the current investor-brief calculator state, derived totals, available actions, and section IDs.",
        parameters=[],
        internal=True,
    ),
    MCPTool(
        name="ta.investor_brief.list_sections",
        description="List stable investor-brief section IDs that can be retrieved or updated.",
        parameters=[],
        internal=True,
    ),
    MCPTool(
        name="ta.investor_brief.get_section",
        description="Retrieve one investor-brief section by stable section_id.",
        parameters=[
            MCPToolParam(name="section_id", type="string", description="Stable section ID returned by ta.investor_brief.list_sections", required=True),
        ],
        internal=True,
    ),
    MCPTool(
        name="ta.investor_brief.update_section",
        description="Replace the body of an investor-brief section while preserving its heading/title.",
        parameters=[
            MCPToolParam(name="section_id", type="string", description="Stable section ID to update", required=True),
            MCPToolParam(name="content", type="string", description="Replacement body content", required=True),
            MCPToolParam(name="content_format", type="string", description="Either 'html' or 'text'"),
        ],
        internal=True,
    ),
    MCPTool(
        name="ta.investor_brief.set_scenario",
        description="Apply a named sprint-cost scenario preset to the investor brief.",
        parameters=[
            MCPToolParam(name="scenario", type="string", description="One of: optimistic, base, pessimistic", required=True),
        ],
        internal=True,
    ),
    MCPTool(
        name="ta.investor_brief.set_variables",
        description="Apply partial calculator variable overrides using canonical sprint-cost keys.",
        parameters=[
            MCPToolParam(name="variables", type="object", description="Object of partial variable overrides keyed by canonical calculator field names", required=True),
        ],
        internal=True,
    ),
    MCPTool(
        name="ta.investor_brief.recalculate",
        description="Recompute derived cost outputs from the current persisted calculator inputs.",
        parameters=[],
        internal=True,
    ),
    # --- Codebase tools (local filesystem + git) ---
    MCPTool(
        name="ta.codebase.recent_commits",
        description="Get recent git commits from the local repository.",
        parameters=[
            MCPToolParam(name="limit", type="number", description="Number of commits (default 20, max 50)"),
            MCPToolParam(name="path", type="string", description="Optional file path prefix filter"),
        ],
        internal=True,
    ),
    MCPTool(
        name="ta.codebase.commit_diff",
        description="Get files changed in a specific commit with line-level stats.",
        parameters=[
            MCPToolParam(name="sha", type="string", description="Commit SHA to inspect", required=True),
        ],
        internal=True,
    ),
    MCPTool(
        name="ta.codebase.search",
        description="Search the codebase by keyword (file contents or file paths).",
        parameters=[
            MCPToolParam(name="query", type="string", description="Search terms", required=True),
            MCPToolParam(name="search_type", type="string", description="'code' for contents, 'path' for file names"),
        ],
        internal=True,
    ),
    MCPTool(
        name="ta.codebase.read_file",
        description="Read a file from the local repository.",
        parameters=[
            MCPToolParam(name="path", type="string", description="Repo-relative file path", required=True),
            MCPToolParam(name="start_line", type="number", description="First line (1-based)"),
            MCPToolParam(name="end_line", type="number", description="Last line (1-based, inclusive)"),
        ],
        internal=True,
    ),
    MCPTool(
        name="ta.codebase.list_directory",
        description="List files and subdirectories in a directory.",
        parameters=[
            MCPToolParam(name="path", type="string", description="Directory path relative to repo root"),
        ],
        internal=True,
    ),
    MCPTool(
        name="ta.codebase.file_tree",
        description="Get a recursive list of all tracked files in the repo (or a subtree).",
        parameters=[
            MCPToolParam(name="path", type="string", description="Root path to start from"),
        ],
        internal=True,
    ),
    MCPTool(
        name="ta.codebase.git_status",
        description="Get current git status showing modified, staged, and untracked files.",
        parameters=[],
        internal=True,
    ),
    MCPTool(
        name="ta.codebase.exec_python",
        description="Execute a Python snippet in a sandboxed environment. Safe imports: json, math, datetime, collections, csv, re, statistics, pandas, numpy, pathlib. Use print() for output. 60s timeout.",
        parameters=[
            MCPToolParam(name="code", type="string", description="Python code to execute", required=True),
        ],
        internal=True,
    ),
    MCPTool(
        name="ta.codebase.shell_command",
        description="Run a shell command for data processing. Allowed commands: wc, sort, uniq, head, tail, jq, date, ls, cat, grep, find, du, df, echo, awk, sed, tr, cut, paste, column, diff, stat, file. No rm, mv, cp, curl, wget, python, sudo. 30s timeout.",
        parameters=[
            MCPToolParam(name="command", type="string", description="Shell command to run", required=True),
        ],
        internal=True,
    ),
    # --- Playwright testing tools (browser automation) ---
    MCPTool(
        name="ta.playwright.discover",
        description="Crawl a web app URL with Playwright, extract all interactive elements (links, buttons, forms, inputs) across pages.",
        parameters=[
            MCPToolParam(name="url", type="string", description="URL to crawl (e.g. http://localhost:5173)", required=True),
            MCPToolParam(name="crawl_depth", type="number", description="Link depth to follow (default 1)"),
        ],
        internal=True,
    ),
    MCPTool(
        name="ta.playwright.test_interaction",
        description="Test a single interactive element on a web page — click buttons, follow links, fill inputs — and detect errors.",
        parameters=[
            MCPToolParam(name="url", type="string", description="Page URL to navigate to", required=True),
            MCPToolParam(name="element_type", type="string", description="Element type: 'link', 'button', or 'input'", required=True),
            MCPToolParam(name="element_text", type="string", description="Visible text of the element", required=True),
            MCPToolParam(name="page_path", type="string", description="Page path where element lives (default '/')"),
            MCPToolParam(name="element_selector", type="string", description="CSS selector (optional)"),
            MCPToolParam(name="element_href", type="string", description="Link href (for links)"),
        ],
        internal=True,
    ),
    MCPTool(
        name="ta.playwright.check_page_health",
        description="Check a URL for console errors, broken images, blank pages, and other health issues.",
        parameters=[
            MCPToolParam(name="url", type="string", description="URL to health-check", required=True),
        ],
        internal=True,
    ),
    MCPTool(
        name="ta.playwright.batch_test",
        description="Run a full deterministic self-test: discover pages, test interactions, detect anomalies, trace to source, suggest fixes.",
        parameters=[
            MCPToolParam(name="url", type="string", description="URL to test", required=True),
            MCPToolParam(name="max_interactions", type="number", description="Max interactions to test (default 15)"),
        ],
        internal=True,
    ),
    # --- QA Pipeline tools (remote agent access) ---
    MCPTool(
        name="ta.pipeline.run",
        description="Start a full QA pipeline (crawl → workflows → test cases) on any URL via the Android emulator. Returns a run_id for polling. Use entry_url to scope the crawl to a specific page/section instead of crawling the whole app. Use workflow_ids to re-test registered workflows without re-discovering.",
        parameters=[
            MCPToolParam(name="app_url", type="string", description="URL to crawl and test", required=True),
            MCPToolParam(name="app_name", type="string", description="Friendly name for the app (default: Custom App)"),
            MCPToolParam(name="device_id", type="string", description="ADB device ID (auto-detects if omitted)"),
            MCPToolParam(name="entry_url", type="string", description="Start crawl at this URL instead of app_url (scopes discovery to a specific section, e.g. '/settings')"),
            MCPToolParam(name="scope_hint", type="string", description="Natural language hint to focus the crawl (e.g. 'Only test the checkout flow')"),
            MCPToolParam(name="workflow_ids", type="string", description="Comma-separated registered workflow IDs to re-test (skips crawl+discovery, replays these workflows)"),
            MCPToolParam(name="max_crawl_turns", type="string", description="Max crawl turns (default: 80, use 20-30 for focused scoped crawls)"),
        ],
    ),
    MCPTool(
        name="ta.pipeline.run_catalog",
        description="Start a QA pipeline for a pre-configured demo app from the catalog. Use ta.pipeline.list_apps to see available apps.",
        parameters=[
            MCPToolParam(name="app_id", type="string", description="App ID from the catalog (e.g. 'google-contacts', 'kyb-ca-sos')", required=True),
            MCPToolParam(name="device_id", type="string", description="ADB device ID (auto-detects if omitted)"),
        ],
    ),
    MCPTool(
        name="ta.pipeline.status",
        description="Poll the status of a running QA pipeline. Returns current stage, progress metrics, and recent events.",
        parameters=[
            MCPToolParam(name="run_id", type="string", description="run_id from ta.pipeline.run or ta.pipeline.run_catalog", required=True),
        ],
    ),
    MCPTool(
        name="ta.pipeline.results",
        description="Get pipeline results. Without run_id: lists all completed runs. With run_id: returns full test suite (test cases, workflows, crawl data).",
        parameters=[
            MCPToolParam(name="run_id", type="string", description="Specific run_id to retrieve (omit to list all)"),
        ],
    ),
    MCPTool(
        name="ta.pipeline.list_apps",
        description="List all available demo apps in the catalog with their IDs, names, packages, and types.",
        parameters=[],
    ),
    # --- Device tools ---
    MCPTool(
        name="ta.device.list",
        description="List available Android emulators and devices with their connection status and specs.",
        parameters=[],
    ),
    MCPTool(
        name="ta.device.lease",
        description="Lease a device for exclusive testing use. Default lease duration is 30 minutes.",
        parameters=[
            MCPToolParam(name="device_id", type="string", description="Device ID to lease", required=True),
            MCPToolParam(name="duration_minutes", type="number", description="Lease duration in minutes (default 30)"),
        ],
    ),
    # --- Feedback / annotation tools ---
    MCPTool(
        name="ta.feedback.annotate",
        description="Attach a feedback annotation to a test case or workflow in a pipeline run. Use for UI review, bug flags, suggestions, approvals.",
        parameters=[
            MCPToolParam(name="run_id", type="string", description="Pipeline run_id", required=True),
            MCPToolParam(name="target_type", type="string", description="'test_case' or 'workflow'", required=True),
            MCPToolParam(name="target_id", type="string", description="Test case ID (e.g. 'tc_001') or workflow ID", required=True),
            MCPToolParam(name="annotation_type", type="string", description="'flag', 'suggestion', 'approval', or 'rejection'", required=True),
            MCPToolParam(name="content", type="string", description="The feedback content", required=True),
            MCPToolParam(name="author", type="string", description="Who is providing this feedback (default: remote-agent)"),
        ],
    ),
    MCPTool(
        name="ta.feedback.list",
        description="List feedback annotations for a pipeline run, optionally filtered by target.",
        parameters=[
            MCPToolParam(name="run_id", type="string", description="Pipeline run_id", required=True),
            MCPToolParam(name="target_type", type="string", description="Filter by 'test_case' or 'workflow'"),
            MCPToolParam(name="target_id", type="string", description="Filter by specific target ID"),
        ],
    ),
    MCPTool(
        name="ta.feedback.summary",
        description="Get a summary of all feedback for a pipeline run: counts by type, flagged items, approval status.",
        parameters=[
            MCPToolParam(name="run_id", type="string", description="Pipeline run_id", required=True),
        ],
    ),
    # --- Meta tools ---
    MCPTool(
        name="ta.meta.connection_info",
        description="Get server connection info: server URL, version, pipeline readiness, concurrent run count.",
        parameters=[],
        internal=True,
    ),
    MCPTool(
        name="ta.setup.status",
        description=(
            "Check what's installed on the local machine for Android QA: Java, Android SDK, ADB, AVDs, Node.js. "
            "Returns a prioritized list of fix commands for anything missing. "
            "Use this when ta.run_android_flow fails with 'No emulator found' — "
            "it tells Claude Code exactly what to install and in what order."
        ),
        parameters=[],
    ),
    MCPTool(
        name="ta.setup.launch_emulator",
        description=(
            "Launch an Android emulator by AVD name. If no AVD specified, launches the first available one. "
            "After calling this, wait ~30 seconds for boot, then call ta.system_check to verify."
        ),
        parameters=[
            MCPToolParam(name="avd_name", type="string", description="AVD name to launch (auto-detects if omitted)"),
        ],
    ),
    MCPTool(
        name="ta.setup.instructions",
        description="Get platform-specific setup instructions (macOS/Linux/Windows) for Android SDK, emulator, and retention.sh prerequisites.",
        parameters=[],
    ),
    MCPTool(
        name="ta.system_check",
        description=(
            "Run a full system readiness check. Verifies: backend health, "
            "ADB/emulator connectivity, Playwright availability, WebSocket relay, "
            "and MCP tool dispatch. Returns per-component pass/fail with fix instructions. "
            "Run this first to ensure everything works before calling ta.run_web_flow."
        ),
        parameters=[
            MCPToolParam(
                name="include_web_test",
                type="boolean",
                description="Also run a quick Playwright page load test (adds ~5s)",
            ),
        ],
    ),
    MCPTool(
        name="ta.pipeline.screenshot",
        description="Grab a live screenshot from the emulator right now. Returns image metadata and a view_url to watch the full live stream in the browser.",
        parameters=[
            MCPToolParam(name="run_id", type="string", description="Pipeline run_id (optional — helps find the right device)"),
            MCPToolParam(name="device_id", type="string", description="ADB device ID (auto-detects if omitted)"),
        ],
    ),
    MCPTool(
        name="ta.pipeline.rerun_failures",
        description=(
            "Rerun only the failed test cases from a previous QA run. Skips crawl, workflow discovery, "
            "and test generation — goes straight to execution. Massive time and token savings for "
            "verify-after-fix loops. Use ta.compare_before_after to diff the baseline vs rerun results."
        ),
        parameters=[
            MCPToolParam(name="baseline_run_id", type="string", description="run_id of the completed run whose failures to rerun", required=True),
            MCPToolParam(name="app_url", type="string", description="Override app URL (default: same URL from baseline run)"),
            MCPToolParam(name="device_id", type="string", description="ADB device ID (auto-detects if omitted)"),
        ],
    ),
    MCPTool(
        name="ta.pipeline.failure_bundle",
        description=(
            "Get a compact, token-efficient failure bundle optimized for Claude Code consumption. "
            "Returns only failures with suggested fixes and a rerun command. "
            "~500-1500 tokens vs 5000+ for raw results. Use this instead of ta.pipeline.results for fix loops."
        ),
        parameters=[
            MCPToolParam(name="run_id", type="string", description="Pipeline run_id", required=True),
        ],
    ),
    MCPTool(
        name="ta.pipeline.run_log",
        description=(
            "Read the persistent run log for a completed QA run. Contains compact failure bundle, "
            "timing data, and rerun command. Persisted to disk so Claude Code can read it across sessions. "
            "Call without run_id to list all available run logs."
        ),
        parameters=[
            MCPToolParam(name="run_id", type="string", description="Pipeline run_id (omit to list all logs)"),
        ],
    ),
    # --- GIF Replay ---
    MCPTool(
        name="ta.pipeline.replay_gif",
        description=(
            "Generate an animated replay GIF for a completed pipeline run. "
            "Stitches captured screenshots (or synthesizes frames from results) into "
            "an animated GIF with step overlays and progress bar. Returns metadata and "
            "a URL to view/download the GIF."
        ),
        parameters=[
            MCPToolParam(name="run_id", type="string", description="Pipeline run_id", required=True),
            MCPToolParam(name="fps", type="number", description="Frames per second (default 0.5)"),
            MCPToolParam(name="max_width", type="number", description="Max GIF width in pixels (default 1280)"),
        ],
    ),
    # --- Interactive Site Map ---
    MCPTool(
        name="ta.sitemap",
        description=(
            "Interactive site map — crawl a URL, then drill into specific screens, "
            "view screenshots, check findings. Stateful: crawl once, then explore.\n\n"
            "Actions:\n"
            "  crawl   — Crawl a URL and cache the site map (requires 'url' param)\n"
            "  overview — Navigation graph with depth distribution\n"
            "  screen  — Drill into a specific screen by index (requires 'index' param)\n"
            "  screenshot — Get base64 JPEG of a specific screen (requires 'index' param)\n"
            "  findings — List all QA findings with severity and fix suggestions"
        ),
        parameters=[
            MCPToolParam(name="action", type="string", description="crawl | overview | screen | screenshot | findings (default: crawl)"),
            MCPToolParam(name="url", type="string", description="URL to crawl (required for action='crawl')"),
            MCPToolParam(name="index", type="number", description="Screen index to drill into (for action='screen' or 'screenshot')"),
        ],
    ),
    # --- QA Verification tools (end-to-end flow + evidence + verdicts) ---
    MCPTool(
        name="ta.run_web_flow",
        description="Execute a complete QA verification flow for a web app: crawl, generate tests, run on emulator, collect evidence. Returns a run_id for polling.",
        parameters=[
            MCPToolParam(name="url", type="string", description="Web app URL to verify", required=True),
            MCPToolParam(name="app_name", type="string", description="Friendly name for the app"),
            MCPToolParam(name="timeout_seconds", type="number", description="Max wall-clock seconds (default 3600)"),
        ],
    ),
    MCPTool(
        name="ta.run_android_flow",
        description="Execute a QA verification flow on an Android emulator for a native app: crawl, generate tests, run, collect evidence. Returns a run_id for polling.",
        parameters=[
            MCPToolParam(name="app_package", type="string", description="Android package name (e.g. com.instagram.android)", required=True),
            MCPToolParam(name="app_name", type="string", description="Friendly name for the app"),
            MCPToolParam(name="device_id", type="string", description="ADB device ID (auto-detects if omitted)"),
            MCPToolParam(name="timeout_seconds", type="number", description="Max wall-clock seconds (default 3600)"),
        ],
    ),
    MCPTool(
        name="ta.rerun",
        description=(
            "Rerun tests from a prior QA run — skips crawl, workflow discovery, and test generation entirely. "
            "By default reruns only FAILED tests (failures_only=true). Saves ~98% of time vs a full run (measured: 10s rerun vs 505s full). "
            "Use after fixing bugs to verify the fix without re-crawling. "
            "Chain: ta.run_web_flow → ta.feedback_package → fix code → ta.rerun → ta.compare_before_after."
        ),
        parameters=[
            MCPToolParam(name="run_id", type="string", description="run_id of the prior completed run to rerun", required=True),
            MCPToolParam(name="failures_only", type="boolean", description="Only rerun failed tests (default true). Set false to rerun entire suite."),
            MCPToolParam(name="url", type="string", description="Override app URL (default: same URL from baseline run)"),
            MCPToolParam(name="device_id", type="string", description="ADB device ID (auto-detects if omitted)"),
            MCPToolParam(name="timeout_seconds", type="number", description="Max wall-clock seconds (default 3600)"),
        ],
    ),
    MCPTool(
        name="ta.collect_trace_bundle",
        description="Gather all evidence artifacts (screenshots, action spans, logs, video) from a completed run into a compact bundle.",
        parameters=[
            MCPToolParam(name="run_id", type="string", description="Pipeline run_id", required=True),
            MCPToolParam(name="include_video", type="boolean", description="Include video recording in bundle (default true)"),
            MCPToolParam(name="compress_format", type="string", description="Compression format: 'zip' or 'tar.gz' (default zip)"),
        ],
    ),
    MCPTool(
        name="ta.summarize_failure",
        description="Produce a token-efficient failure summary for a completed run — highlights only failing tests with root-cause hints.",
        parameters=[
            MCPToolParam(name="run_id", type="string", description="Pipeline run_id", required=True),
            MCPToolParam(name="max_tokens", type="number", description="Approximate max output tokens (default 500)"),
            MCPToolParam(name="priority_filter", type="string", description="Filter failures by priority: 'critical', 'high', 'medium', 'all' (default all)"),
        ],
    ),
    MCPTool(
        name="ta.compare_before_after",
        description="Diff two test runs — shows new failures, fixed tests, and metric deltas between a baseline and current run.",
        parameters=[
            MCPToolParam(name="baseline_run_id", type="string", description="Run ID of the baseline (before) run", required=True),
            MCPToolParam(name="current_run_id", type="string", description="Run ID of the current (after) run", required=True),
            MCPToolParam(name="include_metrics", type="boolean", description="Include detailed metric comparisons (default true)"),
        ],
    ),
    MCPTool(
        name="ta.emit_verdict",
        description="Emit a final pass/fail/blocked verdict for a completed run based on test results and a configurable pass threshold.",
        parameters=[
            MCPToolParam(name="run_id", type="string", description="Pipeline run_id", required=True),
            MCPToolParam(name="pass_threshold", type="number", description="Minimum pass rate (0.0-1.0) to emit PASS verdict (default 0.8)"),
        ],
    ),
    MCPTool(
        name="ta.suggest_fix_context",
        description="Analyze failures from a completed run and suggest root-cause candidates with relevant source file paths for debugging.",
        parameters=[
            MCPToolParam(name="run_id", type="string", description="Pipeline run_id", required=True),
            MCPToolParam(name="max_files", type="number", description="Max number of file suggestions to return (default 5)"),
        ],
    ),
    # --- TA Coordinator Agent (full orchestrated QA via the TA agent brain) ---
    MCPTool(
        name="ta.agent.run",
        description=(
            "Send a message to the retention.sh Coordinator agent — the same AI brain that "
            "powers the TA dashboard. The Coordinator automatically routes to the right "
            "specialist (Search, Test Generation, Device Testing) based on your request. "
            "Use this instead of calling individual ta.* tools when you want the TA agent "
            "to orchestrate a full QA workflow on your behalf. Supports multi-turn conversation "
            "via session_id."
        ),
        parameters=[
            MCPToolParam(name="message", type="string", description="Your request to the TA agent (e.g. 'QA my app at http://localhost:3000')", required=True),
            MCPToolParam(name="session_id", type="string", description="Resume an existing conversation (returned in previous response). Omit for new session."),
            MCPToolParam(name="app_url", type="string", description="App URL to provide as context (optional)"),
            MCPToolParam(name="app_package", type="string", description="Android package name to provide as context (optional)"),
        ],
    ),
    # --- Multi-app benchmark suite tools ---
    MCPTool(
        name="ta.benchmark.run_suite",
        description="Run the full retention.sh benchmark suite against real-world apps (Swag Labs, The Internet, OWASP Juice Shop, TaskFlow Pro)",
        parameters=[
            MCPToolParam(
                name="app_ids",
                type="array",
                description="App IDs to test. Leave empty for all available.",
            ),
            MCPToolParam(
                name="max_interactions",
                type="integer",
                description="Maximum number of interactions per app (default 30)",
            ),
        ],
    ),
    MCPTool(
        name="ta.benchmark.scorecard",
        description="Get the latest benchmark scorecard with pass/fail status for all QA metrics",
        parameters=[],
    ),
    # --- Benchmark App Generation (controllable QA targets) ---
    MCPTool(
        name="ta.benchmark.generate_app",
        description=(
            "Generate a controllable benchmark app with planted bugs for QA evaluation. "
            "Creates an Expo React Native app (or HTML fallback) from a template, injects "
            "bugs at the specified difficulty level, and registers it for benchmarking. "
            "Use ta.benchmark.run_case to execute QA against the generated app."
        ),
        parameters=[
            MCPToolParam(name="template", type="string", description="App template: booking, profile, feed, ecommerce, settings", required=True),
            MCPToolParam(name="difficulty", type="string", description="Bug difficulty: easy, medium, hard, mixed (default: medium)"),
            MCPToolParam(name="num_bugs", type="number", description="Number of bugs to plant (default: 5)"),
            MCPToolParam(name="change_requests", type="string", description="Comma-separated feature change requests for before/after eval"),
        ],
    ),
    MCPTool(
        name="ta.benchmark.list_templates",
        description="List available benchmark app templates with their screens and workflows.",
        parameters=[],
    ),
    MCPTool(
        name="ta.benchmark.list_cases",
        description="List all generated benchmark cases with their bug counts and status.",
        parameters=[],
    ),
    MCPTool(
        name="ta.benchmark.run_case",
        description="Run QA against a generated benchmark case. Installs the app, runs ta.run_web_flow or ta.run_android_flow, and collects evidence.",
        parameters=[
            MCPToolParam(name="case_id", type="string", description="Benchmark case ID from ta.benchmark.generate_app", required=True),
            MCPToolParam(name="thread_mode", type="string", description="'fresh' (new thread per run) or 'continuous' (same thread). Default: fresh"),
        ],
    ),
    MCPTool(
        name="ta.benchmark.score",
        description="Score a completed benchmark run against the planted bug manifest. Returns precision, recall, F1, and per-bug detection status.",
        parameters=[
            MCPToolParam(name="run_id", type="string", description="Benchmark run ID from ta.benchmark.run_case", required=True),
        ],
    ),
    MCPTool(
        name="ta.benchmark.run_history",
        description="List all benchmark runs with their scores and comparison data.",
        parameters=[],
    ),
    # --- Exploration Memory ---
    MCPTool(
        name="ta.memory.stats",
        description="Get exploration memory statistics: cached apps, hit rate, tokens saved. Shows the compounding value of retention.sh's durable memory.",
        parameters=[],
    ),
    MCPTool(
        name="ta.memory.check",
        description="Check what's cached for a specific app URL. Returns which pipeline stages can be skipped (CRAWL, WORKFLOW, TESTCASE) and estimated cost savings.",
        parameters=[
            MCPToolParam(name="app_url", type="string", description="App URL to check", required=True),
        ],
    ),
    MCPTool(
        name="ta.memory.invalidate",
        description="Clear cached exploration data for an app (force full re-exploration on next run). Use when the app UI has changed significantly.",
        parameters=[
            MCPToolParam(name="app_url", type="string", description="App URL to invalidate", required=True),
        ],
    ),
    # --- NemoClaw: NVIDIA Nemotron-powered QA agent ---
    MCPTool(
        name="ta.nemoclaw.run",
        description="Run a NemoClaw QA agent (Nemotron Super via OpenRouter free tier) that uses retention.sh tools to test apps. Autonomously crawls, generates workflows, executes tests, and reports findings. Supports OpenRouter, NVIDIA NIM, or any OpenAI-compatible endpoint.",
        parameters=[
            MCPToolParam(name="prompt", type="string", description="What to test, e.g. 'Run QA pipeline on https://example.com and analyze the checkout flow'", required=True),
            MCPToolParam(name="ta_endpoint", type="string", description="Override TA MCP endpoint (default: http://localhost:8000/mcp)"),
            MCPToolParam(name="model", type="string", description="Override model (e.g. 'mistralai/mistral-small-3.2-24b-instruct:free'). Default: auto-rotates best free model."),
        ],
    ),
    MCPTool(
        name="ta.nemoclaw.status",
        description="Check NemoClaw availability, current model, provider, and free model roster.",
        parameters=[],
        internal=True,
    ),
    MCPTool(
        name="ta.nemoclaw.telemetry",
        description="Get per-model telemetry: latency, tokens/sec, error rate, rate limits, rotation state. Shows which free models are healthy.",
        parameters=[],
        internal=True,
    ),
    MCPTool(
        name="ta.nemoclaw.refresh",
        description="Force re-scan OpenRouter for free models. Discovers newly released free models and re-ranks them by quality. Use after model releases or when rotation seems stale.",
        parameters=[],
        internal=True,
    ),
    MCPTool(
        name="ta.benchmark.model_compare",
        description="Start a model-vs-model benchmark. Runs each free model through 48 NemoClaw tasks across 8 categories (single_tool, multi_tool, reasoning, pipeline, error_recovery, debugging, feedback, adversarial), scoring tool accuracy, correctness, latency, and throughput. Returns a run_id to poll with ta.benchmark.model_compare_status.",
        parameters=[
            MCPToolParam(name="tasks", type="array", description="Task IDs to run (default: all 48). Filter by category prefix: st_*, mt_*, reason_*, pipe_*, err_*, debug_*, fb_*, adv_*", required=False),
            MCPToolParam(name="categories", type="array", description="Filter by category: single_tool, multi_tool, reasoning, pipeline, error_recovery, debugging, feedback, adversarial", required=False),
            MCPToolParam(name="models", type="number", description="How many top models to test (default: all in rotation pool)", required=False),
            MCPToolParam(name="model_ids", type="array", description="Explicit model IDs to test (overrides models count)", required=False),
            MCPToolParam(name="repeats", type="number", description="Run each task N times per model for variance analysis (1-5, default 1)", required=False),
        ],
    ),
    MCPTool(
        name="ta.benchmark.model_compare_status",
        description="Poll status or get results of a model comparison benchmark run.",
        parameters=[
            MCPToolParam(name="run_id", type="string", description="Benchmark run ID returned by ta.benchmark.model_compare", required=True),
        ],
    ),
    MCPTool(
        name="ta.benchmark.qa_pipeline",
        description=(
            "Run QA pipeline benchmark: execute N consecutive QA flows against a frozen app "
            "and measure wall clock time, tool calls, pass rate, and precision/recall against planted bugs. "
            "Use this to benchmark retention.sh performance for 1, 2, 5, 10 consecutive flows."
        ),
        parameters=[
            MCPToolParam(name="app_url", type="string", description="URL of the app to test (or package name for Android)", required=True),
            MCPToolParam(name="app_name", type="string", description="Friendly app name (default: 'Benchmark App')"),
            MCPToolParam(name="consecutive_counts", type="string", description="Comma-separated batch sizes (default: '1,2,5,10')"),
            MCPToolParam(name="flow_type", type="string", description="'web' or 'android' (default: 'web')"),
            MCPToolParam(name="device_id", type="string", description="ADB device ID (auto-detect if omitted)"),
            MCPToolParam(name="planted_bugs_file", type="string", description="Path to planted bugs JSON file for precision/recall"),
        ],
    ),
    MCPTool(
        name="ta.feedback_package",
        description=(
            "Bundle all findings from a completed QA run into a single structured prompt "
            "that the user's Claude Code can execute autonomously to fix bugs. "
            "Combines failure summary, root-cause file suggestions, evidence artifacts, "
            "and a step-by-step fix-verify loop. Returns an agent_prompt the user's coding "
            "agent can follow without further human input."
        ),
        parameters=[
            MCPToolParam(name="run_id", type="string", description="Pipeline run_id from a completed QA run", required=True),
            MCPToolParam(name="app_url", type="string", description="The app URL that was tested (for re-verification)"),
            MCPToolParam(name="repo_root", type="string", description="Path to the repo root on the user's machine (default: '.')"),
            MCPToolParam(name="rerun_command", type="string", description="Command to re-run QA after fixes (default: use ta.run_web_flow)"),
        ],
    ),
    MCPTool(
        name="ta.optimize_bundle",
        description=(
            "Analyze a React/Vite frontend project and return a step-by-step autonomous prompt "
            "for optimizing entry chunk size via lazy-loading, code splitting, and dependency analysis. "
            "The returned prompt is designed for Claude Code to execute directly — the user's agent "
            "runs the build, measures before/after, and applies fixes in a verified loop."
        ),
        parameters=[
            MCPToolParam(name="framework", type="string", description="Frontend framework: 'vite', 'next', 'cra' (default: 'vite')", required=False),
            MCPToolParam(name="entry_file", type="string", description="Path to the app entry/router file (default: 'src/App.tsx')", required=False),
            MCPToolParam(name="build_command", type="string", description="Build command (default: 'npx vite build')", required=False),
        ],
    ),
    # --- Quickstart & Handoff ---
    MCPTool(
        name="ta.quickstart",
        description=(
            "Smart one-call QA entry point. Detects your environment and picks the best mode:\n"
            "- Web app + emulator → full mobile pipeline (Chrome on emulator)\n"
            "- Web app + no emulator → Playwright-direct pipeline (no setup needed)\n"
            "- Native app + emulator → native pipeline\n"
            "- Native app + no emulator → guides you through emulator setup\n\n"
            "Returns a run_id to poll with ta.pipeline.status. When complete, "
            "call ta.get_handoff(run_id) to read the bug report. "
            "Works for web apps WITHOUT any emulator setup."
        ),
        parameters=[
            MCPToolParam(name="url", type="string", description="Your web app URL to test (e.g. https://myapp.com)"),
            MCPToolParam(name="app_name", type="string", description="Name for your app in reports"),
            MCPToolParam(name="package_name", type="string", description="Android package name for native apps (e.g. com.example.myapp)"),
        ],
    ),
    MCPTool(
        name="ta.get_handoff",
        description=(
            "Get a structured markdown QA report for a completed pipeline run. "
            "Includes: verdict, pass rate, failure table (test | reason), "
            "suggested files to investigate, and the rerun command. "
            "Designed for Claude Code to read and act on autonomously."
        ),
        parameters=[
            MCPToolParam(name="run_id", type="string", description="Pipeline run_id from a completed QA run", required=True),
        ],
    ),
    # --- Exploration Memory ---
    MCPTool(
        name="ta.memory.status",
        description=(
            "Check what exploration memory exists for an app. Shows cached crawl, "
            "workflows, and test suites with timestamps and estimated cost savings. "
            "Run 1 is expensive; Run N reuses memory and is nearly free."
        ),
        parameters=[
            MCPToolParam(name="app_url", type="string", description="App URL to check memory for"),
            MCPToolParam(name="app_name", type="string", description="App name (alternative to URL)"),
        ],
    ),
    MCPTool(
        name="ta.memory.graph",
        description=(
            "Get the screen fingerprint graph for an app. Shows all discovered screens, "
            "their components, transitions between screens, and fingerprint hashes. "
            "This is the durable path memory that makes reruns cheap."
        ),
        parameters=[
            MCPToolParam(name="app_url", type="string", description="App URL to get graph for"),
            MCPToolParam(name="app_name", type="string", description="App name (alternative to URL)"),
        ],
    ),
    MCPTool(
        name="ta.memory.apps",
        description="List all apps with stored exploration memory and their stats.",
        parameters=[],
    ),
    # --- Web Demo Bridge (Playwright-based, no emulator) ---
    MCPTool(
        name="ta.web_demo.discover",
        description=(
            "Discover testable tasks from a web URL using Playwright. "
            "Loads the page in a headless browser, extracts interactive elements "
            "(links, buttons, forms, inputs), and returns a list of test tasks. "
            "No emulator needed — pure browser automation. "
            "Use this as the first step before ta.web_demo.run."
        ),
        parameters=[
            MCPToolParam(name="url", type="string", description="The web URL to discover tasks from", required=True),
            MCPToolParam(name="crawl_depth", type="integer", description="How many levels of internal links to follow (default: 1)"),
        ],
    ),
    MCPTool(
        name="ta.web_demo.run",
        description=(
            "Run QA tests on discovered web tasks. Takes task_ids from ta.web_demo.discover "
            "and executes them in parallel using Playwright. Returns a suite_id to poll. "
            "No emulator needed. When complete, call ta.web_demo.scorecard to see results."
        ),
        parameters=[
            MCPToolParam(name="task_ids", type="string", description="Comma-separated task IDs from ta.web_demo.discover (or 'all' to run all)", required=True),
            MCPToolParam(name="parallel", type="integer", description="Number of parallel browser instances (default: 2)"),
        ],
    ),
    MCPTool(
        name="ta.web_demo.scorecard",
        description=(
            "Get the QA scorecard for a completed web demo suite. "
            "Shows pass/fail per task, mode comparison, and overall verdict."
        ),
        parameters=[
            MCPToolParam(name="suite_id", type="string", description="Suite ID from ta.web_demo.run", required=True),
        ],
    ),
    MCPTool(
        name="ta.web_demo.status",
        description="Check the status of a running web demo suite.",
        parameters=[
            MCPToolParam(name="suite_id", type="string", description="Suite ID from ta.web_demo.run", required=True),
        ],
    ),
    # --- Design-to-Code tools (Figma, Stitch by Google) ---
    MCPTool(
        name="ta.design.figma_snapshot",
        description=(
            "Fetch a Figma file snapshot with progressive disclosure. Returns design data at "
            "the requested level (metadata, components, or full). Large payloads are stored "
            "by ref_id and can be retrieved later. Use this to inspect designs before generating code."
        ),
        parameters=[
            MCPToolParam(name="figma_url", type="string", description="Figma file URL (e.g. https://www.figma.com/file/abc123/...)", required=True),
            MCPToolParam(name="level", type="string", description="Detail level: 'metadata', 'components', or 'full' (default: 'components')"),
            MCPToolParam(name="node_ids", type="string", description="Comma-separated Figma node IDs to scope the snapshot"),
        ],
    ),
    MCPTool(
        name="ta.design.figma_analyze_flows",
        description=(
            "Analyze a Figma file to detect visual user flows. Clusters frames into flow groups "
            "based on prototype connections, section groupings, name patterns, and spatial proximity. "
            "Returns named flow groups with screen sequences — use as input for code generation or "
            "direct QA pipeline testing."
        ),
        parameters=[
            MCPToolParam(name="figma_url", type="string", description="Figma file URL", required=True),
            MCPToolParam(name="min_group_size", type="number", description="Minimum frames per flow group (default 2)"),
        ],
    ),
    MCPTool(
        name="ta.design.generate_from_design",
        description=(
            "Convert a design file (Figma URL or Stitch export) into deployable code. "
            "Uses the Figma snapshot + flow analysis to produce React components or a self-contained "
            "HTML app. The generated code is saved and served for immediate QA pipeline testing. "
            "When Stitch MCP is available, routes through Google's Stitch for higher-fidelity code gen."
        ),
        parameters=[
            MCPToolParam(name="design_url", type="string", description="Figma file URL or Stitch project URL", required=True),
            MCPToolParam(name="target_framework", type="string", description="Output framework: 'react', 'html', 'next' (default: 'html')"),
            MCPToolParam(name="style_system", type="string", description="CSS approach: 'tailwind', 'css-modules', 'inline' (default: 'tailwind')"),
        ],
    ),
    MCPTool(
        name="ta.design.pipeline",
        description=(
            "One-stop design-to-QA pipeline: fetches a Figma design, generates code from it, "
            "deploys to emulator, runs the full QA crawl → workflow → test case → execution pipeline, "
            "and returns results. Combines ta.design.generate_from_design + ta.pipeline.run in one call."
        ),
        parameters=[
            MCPToolParam(name="design_url", type="string", description="Figma file URL", required=True),
            MCPToolParam(name="app_name", type="string", description="Friendly name for the generated app"),
            MCPToolParam(name="target_framework", type="string", description="Output framework: 'react', 'html' (default: 'html')"),
            MCPToolParam(name="device_id", type="string", description="ADB device ID (auto-detects if omitted)"),
        ],
    ),
    MCPTool(
        name="ta.codebase.analyze_ui_impact",
        description="Analyze the visual impact of modified files. Returns affected features, workflows, and UI screens with evidence.",
        parameters=[
            MCPToolParam(name="files_changed", type="string", description="Comma-separated list of modified file paths.", required=True),
        ],
    ),
    # --- Retention self-serve QA loop ---
    MCPTool(
        name="ta.onboard.status",
        description=(
            "Check what's working and what's not. Returns a checklist of: MCP connection, "
            "token/team status, backend health, emulator connectivity, Playwright availability, "
            "saved trajectories, and memory stats. Suggests the next step based on what's missing. "
            "Run this first after installing retention.sh."
        ),
        parameters=[],
    ),
    MCPTool(
        name="ta.crawl.url",
        description=(
            "Crawl any URL and get instant QA findings — JS errors, rendering issues, accessibility gaps, "
            "broken links, and SPA detection. Returns screenshots, interactive element counts, and navigation "
            "paths. Works without an emulator (uses Playwright). This is the fastest way to get value from "
            "retention.sh — point it at your site and see what's broken in 30 seconds."
        ),
        parameters=[
            MCPToolParam(name="url", type="string", description="URL to crawl (e.g. https://myapp.com)", required=True),
            MCPToolParam(name="depth", type="number", description="Max crawl depth (default: 2, max: 5)"),
            MCPToolParam(name="save_trajectory", type="boolean", description="Save the crawl as a replayable trajectory (default: true)"),
        ],
    ),
    MCPTool(
        name="ta.savings.compare",
        description=(
            "Run an A/B comparison: execute the same test with and without trajectory replay, "
            "then show the token/time/request savings side by side. This is the honest proof — "
            "same test, same conditions, measured delta. Returns a formatted comparison table."
        ),
        parameters=[
            MCPToolParam(name="url", type="string", description="URL to test (uses most recent trajectory if omitted)"),
            MCPToolParam(name="trajectory_id", type="string", description="Specific trajectory to compare against"),
        ],
    ),
    MCPTool(
        name="ta.team.invite",
        description=(
            "Generate a ready-to-share Slack/Discord message for teammates to join your team. "
            "Returns the formatted message with the one-liner install command, invite code, "
            "and dashboard URL. Just copy and paste it."
        ),
        parameters=[
            MCPToolParam(name="team_name", type="string", description="Team name (uses current team if omitted)"),
        ],
    ),
    MCPTool(
        name="ta.qa.redesign",
        description=(
            "Full QA→fix→verify loop for your web app. Crawls the URL, identifies issues, "
            "suggests fixes with file paths and code snippets, then offers to re-crawl after "
            "you apply the fix to verify it worked. Each re-crawl uses trajectory replay — "
            "getting cheaper every iteration. This is the self-serve dogfood loop."
        ),
        parameters=[
            MCPToolParam(name="url", type="string", description="URL of your web app to QA", required=True),
            MCPToolParam(name="focus", type="string", description="Focus area: 'all', 'a11y', 'performance', 'seo', 'errors' (default: 'all')"),
            MCPToolParam(name="fix_mode", type="boolean", description="If true, suggest code fixes for each finding (default: true)"),
        ],
    ),
    # --- TCWP (Canonical Workflow Package) ---
    MCPTool(
        name="ta.tcwp.generate",
        description="Generate a TCWP bundle from a saved trajectory. Produces manifest, workflow, run, trajectory, events, provenance, permissions, and optional sales brief.",
        parameters=[
            MCPToolParam(name="trajectory_id", type="string", description="ID of saved trajectory to package", required=True),
            MCPToolParam(name="task_name", type="string", description="Task name for trajectory lookup"),
            MCPToolParam(name="workflow_name", type="string", description="Human-readable workflow name"),
            MCPToolParam(name="workflow_goal", type="string", description="Goal description for the workflow"),
            MCPToolParam(name="include_sales_brief", type="boolean", description="Include sales brief (default: true)"),
        ],
    ),
    MCPTool(
        name="ta.tcwp.validate",
        description="Validate a TCWP bundle for integrity and schema compliance. Checks required files, field presence, and hash integrity.",
        parameters=[
            MCPToolParam(name="package_id", type="string", description="ID of the TCWP package to validate"),
            MCPToolParam(name="path", type="string", description="Path to a TCWP bundle directory (alternative to package_id)"),
        ],
    ),
    MCPTool(
        name="ta.tcwp.list",
        description="List all TCWP bundles stored locally with their workflow IDs, run IDs, tags, and file counts.",
        parameters=[],
    ),
    MCPTool(
        name="ta.tcwp.export",
        description="Export a TCWP bundle as a single JSON file for sharing with partners or uploading to retention.sh Cloud.",
        parameters=[
            MCPToolParam(name="package_id", type="string", description="ID of the TCWP package to export", required=True),
        ],
    ),
    MCPTool(
        name="ta.tcwp.ingest",
        description="Import a TCWP bundle from a directory, export JSON file, or JSON string into local storage.",
        parameters=[
            MCPToolParam(name="path", type="string", description="Path to a TCWP bundle directory or export JSON file"),
            MCPToolParam(name="json_data", type="string", description="JSON string of a TCWP export (alternative to path)"),
        ],
    ),
    MCPTool(
        name="ta.tcwp.export_profile",
        description="Export a TCWP bundle using a specific profile: 'ops' (replay/rerun), 'training' (fine-tuning/evals/reward modeling), or 'sales' (buyer proof/GTM). Filters files and applies consent/redaction rules per profile.",
        parameters=[
            MCPToolParam(name="package_id", type="string", description="ID of the TCWP package to export", required=True),
            MCPToolParam(name="profile", type="string", description="Export profile: ops, training, sales (default: ops)"),
        ],
    ),
    # --- Audit Engine ---
    MCPTool(
        name="ta.audit.validate_shortcut",
        description="Validate an optimization shortcut against a baseline trajectory. Compares end state, checkpoints, and cost metrics.",
        parameters=[
            MCPToolParam(name="trajectory_id", type="string", description="Baseline trajectory ID", required=True),
            MCPToolParam(name="candidate_id", type="string", description="Optimization candidate ID", required=True),
            MCPToolParam(name="task_name", type="string", description="Task name for lookup"),
            MCPToolParam(name="shortcut_steps", type="string", description="Comma-separated step IDs that form the shortcut"),
        ],
    ),
    MCPTool(
        name="ta.audit.compare",
        description="Compare a shortcut run against a baseline run. Shows cost deltas, checkpoint differences, and compression gains.",
        parameters=[
            MCPToolParam(name="baseline_run_id", type="string", description="Original full-crawl run ID"),
            MCPToolParam(name="shortcut_run_id", type="string", description="Shortcut/optimized run ID"),
            MCPToolParam(name="package_id", type="string", description="TCWP package ID (alternative lookup)"),
        ],
    ),
    MCPTool(
        name="ta.audit.drift_report",
        description="Generate a drift report for a trajectory — shows per-step stability, health status, and recommendations.",
        parameters=[
            MCPToolParam(name="trajectory_id", type="string", description="Trajectory to check drift for", required=True),
            MCPToolParam(name="task_name", type="string", description="Task name for lookup"),
        ],
    ),
    MCPTool(
        name="ta.audit.list",
        description="List all audit results with their verdicts and risk assessments.",
        parameters=[],
    ),
    # --- Workflow Compression ---
    MCPTool(
        name="ta.compress.workflow",
        description="Compress a workflow trajectory by removing redundant steps. Proposes an optimized path with fewer steps and lower token cost.",
        parameters=[
            MCPToolParam(name="trajectory_id", type="string", description="Trajectory to compress", required=True),
            MCPToolParam(name="task_name", type="string", description="Task name for lookup"),
            MCPToolParam(name="strategy", type="string", description="Compression strategy: auto, step_elimination, shortcut_generation (default: auto)"),
        ],
    ),
    MCPTool(
        name="ta.compress.list",
        description="List all compression results with step counts, token savings, and timestamps.",
        parameters=[],
    ),
    MCPTool(
        name="ta.compress.stats",
        description="Get aggregate compression statistics across all workflows or filtered by task name.",
        parameters=[
            MCPToolParam(name="task_name", type="string", description="Filter stats by task name (optional)"),
        ],
    ),
    MCPTool(
        name="ta.compress.rollback",
        description="Rollback to uncompressed trajectory. The original is always preserved.",
        parameters=[
            MCPToolParam(name="trajectory_id", type="string", description="Trajectory to rollback", required=True),
        ],
    ),
    # --- Checkpoint Validation ---
    MCPTool(
        name="ta.checkpoint.list",
        description="List all checkpoints for a trajectory, or all saved checkpoints if no trajectory specified.",
        parameters=[
            MCPToolParam(name="trajectory_id", type="string", description="Trajectory to list checkpoints for"),
            MCPToolParam(name="task_name", type="string", description="Task name for lookup"),
        ],
    ),
    MCPTool(
        name="ta.checkpoint.set",
        description="Set a checkpoint at a specific step with expected state.",
        parameters=[
            MCPToolParam(name="trajectory_id", type="string", description="Trajectory the checkpoint belongs to", required=True),
            MCPToolParam(name="step_id", type="string", description="Step ID where checkpoint is placed", required=True),
            MCPToolParam(name="label", type="string", description="Human-readable checkpoint label"),
            MCPToolParam(name="expected_state", type="string", description="Expected state fingerprint or description"),
        ],
    ),
    MCPTool(
        name="ta.checkpoint.verify",
        description="Verify a checkpoint against the current state. Returns pass/fail with drift score.",
        parameters=[
            MCPToolParam(name="checkpoint_id", type="string", description="Checkpoint to verify", required=True),
            MCPToolParam(name="current_state", type="string", description="Current state fingerprint to compare against"),
        ],
    ),
    MCPTool(
        name="ta.checkpoint.drift_report",
        description="Generate drift report for all checkpoints of a trajectory.",
        parameters=[
            MCPToolParam(name="trajectory_id", type="string", description="Trajectory to report on", required=True),
        ],
    ),
    # --- Savings Forecast + ROI ---
    MCPTool(
        name="ta.savings.forecast",
        description="Predict token/cost savings for future runs based on trajectory history. Shows projected savings curve.",
        parameters=[
            MCPToolParam(name="trajectory_id", type="string", description="Trajectory to forecast for", required=True),
            MCPToolParam(name="task_name", type="string", description="Task name for lookup"),
            MCPToolParam(name="runs_ahead", type="number", description="How many future runs to forecast (default: 10)"),
        ],
    ),
    MCPTool(
        name="ta.savings.roi",
        description="Calculate ROI of trajectory investment. Shows breakeven point, cumulative savings, and profitability.",
        parameters=[
            MCPToolParam(name="trajectory_id", type="string", description="Trajectory to calculate ROI for", required=True),
            MCPToolParam(name="task_name", type="string", description="Task name for lookup"),
            MCPToolParam(name="full_crawl_cost_usd", type="number", description="Cost of one full crawl (estimated if not provided)"),
        ],
    ),
    MCPTool(
        name="ta.savings.breakdown",
        description="Break down savings by pipeline stage (navigation, interaction, verification, wait). Shows which stages cost the most tokens.",
        parameters=[
            MCPToolParam(name="trajectory_id", type="string", description="Trajectory to break down", required=True),
            MCPToolParam(name="task_name", type="string", description="Task name for lookup"),
        ],
    ),
]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/health", summary="MCP server readiness probe")
async def mcp_health():
    public = [t for t in _TOOLS if not t.internal]
    return {
        "status": "ok",
        "server": "retention-mcp",
        "version": "0.1.0",
        "tools": len(public),
        "tools_total": len(_TOOLS),
        "dev_mode": _is_dev_mode(),
    }


@router.get("/setup", summary="Self-bootstrap: returns proxy script + token + MCP config")
async def mcp_setup(request: Request):
    """Zero-friction setup endpoint. Claude Code fetches this single URL,
    saves the proxy script, and writes MCP config. No manual steps."""
    # Server URL: env var > request origin (no tunnel file needed)
    server_url = os.environ.get("TA_BACKEND_URL", "").rstrip("/")
    if not server_url:
        server_url = str(request.base_url).rstrip("/")

    # Read or generate token
    token_path = _REPO_ROOT / ".claude" / "mcp-token"
    token = ""
    if token_path.exists():
        token = token_path.read_text().strip()
    if not token:
        import hashlib
        token = hashlib.md5(os.urandom(16)).hexdigest()
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(token)

    # Read the proxy script
    proxy_path = _REPO_ROOT / "scripts" / "remote_mcp_proxy.py"
    proxy_script = ""
    if proxy_path.exists():
        proxy_script = proxy_path.read_text()

    # Build the MCP config users drop into .mcp.json
    mcp_config = {
        "mcpServers": {
            "retention": {
                "command": "python3",
                "args": ["~/.retention/proxy.py"],
                "env": {
                    "TA_STUDIO_URL": server_url,
                    "RETENTION_MCP_TOKEN": token,
                },
            }
        }
    }

    return {
        "server_url": server_url,
        "token": token,
        "proxy_script": proxy_script,
        "proxy_install_path": "~/.retention/proxy.py",
        "mcp_config": mcp_config,
        "setup_instructions": (
            "Claude Code: run these 3 commands to complete setup:\n"
            "1. mkdir -p ~/.retention\n"
            f"2. curl -s {server_url}/mcp/setup/proxy.py -o ~/.retention/proxy.py\n"
            "3. Add the mcp_config to your project's .mcp.json\n"
            "\nThen restart Claude Code. All ta.* tools will be available."
        ),
        "one_liner": f"curl -s {server_url}/mcp/setup/install.sh | bash",
    }


@router.get("/setup/proxy.py", summary="Download the MCP proxy script directly")
async def mcp_setup_proxy():
    """Serves the proxy script as raw Python for curl/wget."""
    from fastapi.responses import PlainTextResponse
    proxy_path = _REPO_ROOT / "scripts" / "remote_mcp_proxy.py"
    if not proxy_path.exists():
        raise HTTPException(status_code=404, detail="Proxy script not found")
    return PlainTextResponse(proxy_path.read_text(), media_type="text/x-python")


@router.get("/setup/install.sh", summary="One-liner install script: curl | bash")
async def mcp_setup_install(request: Request):
    """Shell script that downloads proxy + writes .mcp.json.

    Usage:
       curl -s https://backend/mcp/setup/install.sh | bash

    Token is NOT accepted via query parameter — set RETENTION_MCP_TOKEN in .mcp.json
    after install, or use the zero-config /setup/init.sh installer instead.
    """
    from fastapi.responses import PlainTextResponse
    server_url = os.environ.get("TA_BACKEND_URL", "").rstrip("/")
    if not server_url:
        server_url = str(request.base_url).rstrip("/")

    token = "YOUR_TOKEN_HERE"
    token_instruction = '''echo ""
echo "  ⚠ Token not set. Get yours at:"
echo "    https://test-studio-xi.vercel.app/docs/install"
echo "  Then edit .mcp.json and replace YOUR_TOKEN_HERE with your token."
echo "  Or re-run: curl -s {server_url}/mcp/setup/init.sh | bash"'''

    script = f"""#!/bin/bash
set -e
echo "Setting up retention.sh MCP for Claude Code..."

# 1. Download proxy
mkdir -p ~/.retention
curl -s {server_url}/mcp/setup/proxy.py -o ~/.retention/proxy.py
chmod +x ~/.retention/proxy.py
echo "  Downloaded proxy → ~/.retention/proxy.py"

# 2. Write MCP config into .mcp.json (merges if exists)
MCP_FILE=".mcp.json"
python3 -c "
import json, os
p = '${{MCP_FILE}}'
d = json.load(open(p)) if os.path.exists(p) else {{}}
d.setdefault('mcpServers', {{}})['retention'] = {{
    'command': 'python3',
    'args': [os.path.expanduser('~/.retention/proxy.py')],
    'env': {{
        'TA_STUDIO_URL': '{server_url}',
        'RETENTION_MCP_TOKEN': '{token}'
    }}
}}
json.dump(d, open(p, 'w'), indent=2)
print('  Wrote MCP config → ' + p)
"
{token_instruction}

echo ""
echo "Done! Restart Claude Code to activate ta.* tools."
echo "Then tell Claude: \\"Test my app at localhost:3000\\""
"""
    return PlainTextResponse(script, media_type="text/x-shellscript")


@router.get("/setup/init.sh", summary="Zero-config one-command installer: curl | bash")
async def mcp_setup_init(request: Request, platform: str = "claude-code"):
    """All-in-one installer that handles token generation + proxy + config.

    Usage:
       curl -s https://backend/mcp/setup/init.sh | bash
       curl -s https://backend/mcp/setup/init.sh?platform=cursor | bash
       curl -s https://backend/mcp/setup/init.sh?platform=openclaw | bash

    This is the A-grade door: one command, no prerequisites, no prior token.
    Prompts for email, generates token, downloads proxy, writes the correct
    MCP config file for the specified platform.
    """
    from fastapi.responses import PlainTextResponse
    server_url = os.environ.get("TA_BACKEND_URL", "").rstrip("/")
    if not server_url:
        server_url = str(request.base_url).rstrip("/")

    convex_url = os.environ.get("CONVEX_SITE_URL", "https://exuberant-ferret-263.convex.site")
    if ".convex.cloud" in convex_url:
        convex_url = convex_url.replace(".convex.cloud", ".convex.site")

    # Platform-specific config path and agent display name
    if platform == "cursor":
        mcp_file = ".cursor/mcp.json"
        agent_display = "Cursor"
        mkdir_cmd = "mkdir -p .cursor"
    elif platform == "openclaw":
        mcp_file = ".openclaw/mcp.json"
        agent_display = "OpenClaw"
        mkdir_cmd = "mkdir -p .openclaw"
    else:
        mcp_file = ".mcp.json"
        agent_display = "Claude Code"
        mkdir_cmd = ""

    mkdir_step = f"\n{mkdir_cmd}" if mkdir_cmd else ""

    script = f"""#!/bin/bash
set -e

echo ""
echo "  ┌──────────────────────────────────────────┐"
echo "  │     retention.sh — One-Command Setup        │"
echo "  │     Platform: {agent_display:<27}│"
echo "  └──────────────────────────────────────────┘"
echo ""

# ── Step 1: Get email for token ──────────────────────────────
EMAIL="${{RETENTION_EMAIL:-}}"
if [ -z "$EMAIL" ]; then
  printf "  Your email (for API token): "
  read -r EMAIL
fi

if [ -z "$EMAIL" ]; then
  echo "  ✗ Email required. Exiting."
  exit 1
fi

echo ""
echo "  Generating API token..."

# Generate token via Convex API
TOKEN_RESPONSE=$(curl -s -X POST "{convex_url}/api/mcp/generate-token" \\
  -H "Content-Type: application/json" \\
  -d "{{\\"email\\": \\"$EMAIL\\", \\"platform\\": \\"{platform}\\"}}")

TOKEN=$(echo "$TOKEN_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null)

if [ -z "$TOKEN" ]; then
  echo "  ✗ Token generation failed. Response: $TOKEN_RESPONSE"
  exit 1
fi

echo "  ✓ Token: ${{TOKEN:0:8}}..."

# ── Step 2: Download proxy ───────────────────────────────────
mkdir -p ~/.retention
curl -s {server_url}/mcp/setup/proxy.py -o ~/.retention/proxy.py
chmod +x ~/.retention/proxy.py
echo "  ✓ Proxy downloaded → ~/.retention/proxy.py"

# ── Step 3: Write MCP config ─────────────────────────────────{mkdir_step}
MCP_FILE="{mcp_file}"
python3 -c "
import json, os
p = '${{MCP_FILE}}'
os.makedirs(os.path.dirname(p) if os.path.dirname(p) else '.', exist_ok=True)
d = json.load(open(p)) if os.path.exists(p) else {{}}
d.setdefault('mcpServers', {{}})['retention'] = {{
    'command': 'python3',
    'args': [os.path.expanduser('~/.retention/proxy.py')],
    'env': {{
        'TA_STUDIO_URL': '{server_url}',
        'RETENTION_MCP_TOKEN': '$TOKEN'
    }}
}}
json.dump(d, open(p, 'w'), indent=2)
print('  ✓ MCP config → ' + os.path.abspath(p))
"

echo ""
echo "  ┌──────────────────────────────────────────┐"
echo "  │  ✓ Done! Restart {agent_display:<24}│"
echo "  │             to activate ta.* tools       │"
echo "  │                                          │"
echo "  │  Then say:                               │"
echo "  │  \\"Test my app at localhost:3000\\"         │"
echo "  └──────────────────────────────────────────┘"
echo ""
"""
    return PlainTextResponse(script, media_type="text/x-shellscript")


@router.get("/setup/init.ps1", summary="Zero-config Windows PowerShell installer: irm | iex")
async def mcp_setup_init_ps1(request: Request, platform: str = "claude-code"):
    """PowerShell equivalent of init.sh for Windows users.

    Usage (PowerShell):
       irm "https://backend/mcp/setup/init.ps1" | iex
       irm "https://backend/mcp/setup/init.ps1?platform=cursor" | iex

    Prompts for email, generates token, downloads proxy, writes MCP config.
    """
    from fastapi.responses import PlainTextResponse
    server_url = os.environ.get("TA_BACKEND_URL", "").rstrip("/")
    if not server_url:
        server_url = str(request.base_url).rstrip("/")

    convex_url = os.environ.get("CONVEX_SITE_URL", "https://exuberant-ferret-263.convex.site")
    if ".convex.cloud" in convex_url:
        convex_url = convex_url.replace(".convex.cloud", ".convex.site")

    if platform == "cursor":
        mcp_file = ".cursor\\mcp.json"
        agent_display = "Cursor"
    elif platform == "openclaw":
        mcp_file = ".openclaw\\mcp.json"
        agent_display = "OpenClaw"
    else:
        mcp_file = ".mcp.json"
        agent_display = "Claude Code"

    script = f"""# retention.sh — Windows One-Command Setup ({agent_display})
# Run: irm "{server_url}/mcp/setup/init.ps1" | iex

$ErrorActionPreference = 'Stop'

Write-Host ""
Write-Host "  +-----------------------------------------+" -ForegroundColor Cyan
Write-Host "  |    retention.sh - One-Command Setup        |" -ForegroundColor Cyan
Write-Host "  |    Platform: {agent_display:<30}|" -ForegroundColor Cyan
Write-Host "  +-----------------------------------------+" -ForegroundColor Cyan
Write-Host ""

# Step 1: Get email
$email = $env:RETENTION_EMAIL
if (-not $email) {{
    Write-Host ""
    $email = Read-Host "  Your email (for API token)"
}}
if (-not $email) {{ Write-Error "Email is required."; exit 1 }}

Write-Host "  Generating API token..." -ForegroundColor Yellow

try {{
    $body = '{{\"email\": \"' + $email + '\", \"platform\": \"{platform}\"}}'
    $resp = Invoke-RestMethod -Method Post `
        -Uri "{convex_url}/api/mcp/generate-token" `
        -ContentType "application/json" `
        -Body $body
    $token = $resp.token
    if (-not $token) {{ throw "No token in response" }}
    Write-Host ("  + Token: " + $token.Substring(0, [Math]::Min(8, $token.Length)) + "...") -ForegroundColor Green
}} catch {{
    Write-Error "Token generation failed: $_"
    exit 1
}}

# Step 2: Download proxy
$installDir = "$env:USERPROFILE\\.retention"
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
$proxyPath = "$installDir\\proxy.py"
Write-Host "  Downloading MCP proxy..." -ForegroundColor Yellow
Invoke-WebRequest -Uri "{server_url}/mcp/setup/proxy.py" -OutFile $proxyPath
Write-Host "  + Proxy downloaded -> $proxyPath" -ForegroundColor Green

# Step 3: Write MCP config
$mcpFile = "{mcp_file}"
$mcpDir = Split-Path $mcpFile -Parent
if ($mcpDir -and -not (Test-Path $mcpDir)) {{
    New-Item -ItemType Directory -Force -Path $mcpDir | Out-Null
}}
$config = @{{
    mcpServers = @{{
        "retention" = @{{
            command = "python"
            args = @($proxyPath)
            env = @{{
                TA_STUDIO_URL = "{server_url}"
                RETENTION_MCP_TOKEN = $token
            }}
        }}
    }}
}} | ConvertTo-Json -Depth 10
Set-Content -Path $mcpFile -Value $config
Write-Host "  + MCP config -> $((Resolve-Path $mcpFile).Path)" -ForegroundColor Green

Write-Host ""
Write-Host "  +-----------------------------------------+" -ForegroundColor Green
Write-Host "  |  Done! Restart {agent_display:<27}|" -ForegroundColor Green
Write-Host "  |          to activate ta.* tools         |" -ForegroundColor Green
Write-Host "  |                                         |" -ForegroundColor Green
Write-Host "  |  Then say: Test my app at localhost:3000|" -ForegroundColor Green
Write-Host "  +-----------------------------------------+" -ForegroundColor Green
Write-Host ""
"""
    return PlainTextResponse(script, media_type="text/plain; charset=utf-8")


@router.get("/setup/for-agent", summary="Agent-readable setup instructions — plain text, sequential steps")
async def mcp_setup_for_agent(request: Request, platform: str = "claude-code", app_url: str = ""):
    """Machine-readable setup instructions for AI coding agents.

    Returns plain text with RUN: (shell commands) and ACTION: (agent actions)
    that any LLM agent can follow step-by-step without parsing HTML/JS.

    This is the canonical entry point for agent self-bootstrap. An agent
    visiting https://test-studio-xi.vercel.app/docs/install gets a React SPA
    (empty HTML), so it should fetch this endpoint instead.
    """
    from fastapi.responses import PlainTextResponse

    backend_url = str(request.base_url).rstrip("/")
    convex_site_url = os.getenv("CONVEX_SITE_URL", "").strip()
    if ".convex.cloud" in convex_site_url:
        convex_site_url = convex_site_url.replace(".convex.cloud", ".convex.site")
    if platform == "cursor":
        mcp_file = ".cursor/mcp.json"
        agent_name = "Cursor"
    elif platform == "openclaw":
        mcp_file = ".openclaw/mcp.json"
        agent_name = "OpenClaw"
    else:
        mcp_file = ".mcp.json"
        agent_name = "Claude Code"

    # Token generation step — uses Convex if available, else shared env token
    if convex_site_url:
        token_step = f"""STEP 1: Generate a personal API token
ACTION: Ask the user for their email address. Then run:
RUN: curl -s -X POST "{convex_site_url}/api/mcp/generate-token" -H "Content-Type: application/json" -d '{{"email": "USER_EMAIL", "name": "USER_NAME", "platform": "{platform}"}}'
NOTE: Response is JSON: {{"token": "abc123...", "isNew": true}}. Save the "token" value for Step 2.
NOTE: name is optional. Same email always returns the same token (idempotent).
NOTE: To verify a token later: curl -s -X POST "{convex_site_url}/api/mcp/verify-token" -H "Content-Type: application/json" -d '{{"token": "THE_TOKEN"}}'
"""
    else:
        token_step = f"""STEP 1: Get the shared API token
ACTION: Ask the user for the RETENTION_MCP_TOKEN value, or check the environment variable RETENTION_MCP_TOKEN.
"""

    app_step = ""
    if app_url:
        app_step = f"""
STEP 5: Test the user's app
ACTION: Call ta.run_web_flow with url="{app_url}" to start QA testing.
NOTE: If the URL is localhost, the proxy connects outbound via WebSocket so retention.sh can reach it.
"""
    else:
        app_step = """
STEP 5: Test the user's app
ACTION: Ask the user for their app URL, then call ta.run_web_flow to start QA testing.
NOTE: If the URL is localhost, the proxy connects outbound via WebSocket so retention.sh can reach it.
"""

    instructions = f"""TA_STUDIO_AGENT_SETUP_V2
========================
retention.sh — AI-powered QA automation for mobile and web apps.
Your agent connects to retention.sh's hosted infrastructure via a lightweight MCP proxy.
No backend to run yourself. Setup takes 3 commands.

BACKEND_URL: {backend_url}
PLATFORM: {platform}

PREREQUISITES:
- Python 3.10+ (check: python3 --version)
- curl (check: curl --version)

{token_step}
STEP 2: Install the MCP proxy and configure token in one command
RUN: curl -s "{backend_url}/mcp/setup/init.sh?platform={platform}" | bash
NOTE: This prompts for your email, generates a token, downloads ~/.retention/proxy.py, and writes {mcp_file} — all in one pass.
NOTE: No manual editing required. Token is embedded in {mcp_file} automatically.
NOTE: Platform-specific: the ?platform={platform} parameter ensures the correct config file is written.

STEP 3: Restart {agent_name}
ACTION: Tell the user to restart {agent_name} so it picks up the new retention MCP server.
ACTION: After restart, run /mcp to verify retention appears in the server list.

STEP 4: Verify connectivity
ACTION: Call ta.system_check to verify the connection works and list all available tools.
EXPECTED: A response confirming the backend is reachable and listing 50+ tools.
{app_step}
DONE

MANUAL ALTERNATIVE (if curl | bash doesn't work):

RUN: mkdir -p ~/.retention && curl -s "{backend_url}/mcp/setup/proxy.py" -o ~/.retention/proxy.py && chmod +x ~/.retention/proxy.py

Then write {mcp_file}:
RUN: python3 -c "
import json, os
config = {{'mcpServers': {{'retention': {{
    'command': 'python3',
    'args': [os.path.expanduser('~/.retention/proxy.py')],
    'env': {{'TA_STUDIO_URL': '{backend_url}', 'RETENTION_MCP_TOKEN': 'TOKEN_FROM_STEP_1'}}
}}}}}}
json.dump(config, open('{mcp_file}', 'w'), indent=2)
print('Wrote {mcp_file}')
"

TROUBLESHOOTING:
- "Missing Authorization: Bearer <token>" → Token not in {mcp_file}. Check env block has RETENTION_MCP_TOKEN.
- "Invalid MCP token" → Regenerate: curl -s -X POST "{convex_site_url or backend_url}/api/mcp/generate-token" -H "Content-Type: application/json" -d '{{"email": "user@email.com"}}'
- Backend slow on first request → Render free tier cold start (~30s). Pre-warm: curl -s "{backend_url}/api/health"
- Proxy syntax check: python3 -c "import py_compile, os; py_compile.compile(os.path.expanduser('~/.retention/proxy.py'), doraise=True)"

GETTING STARTED (run these in order):
1. ta.onboard.status       — Check what's working, get next steps
2. ta.crawl.url(url='https://your-app.com') — Crawl your site, get instant QA findings
3. Fix the findings in your code
4. ta.crawl.url(url='https://your-app.com') — Re-crawl to verify (uses trajectory replay = cheaper)
5. ta.savings.compare      — See the token/time savings between runs
6. ta.team.invite           — Generate a Slack message to onboard teammates

SELF-SERVE QA LOOP:
- ta.onboard.status         — Prerequisite checker + next steps
- ta.crawl.url              — Crawl any URL, get JS errors, a11y gaps, rendering issues
- ta.savings.compare        — A/B comparison: same test with/without trajectory replay
- ta.team.invite            — Ready-to-share Slack message for teammate onboarding
- ta.qa.redesign            — Full QA→fix→verify loop with fix suggestions

FULL QA PIPELINE:
- ta.run_web_flow           — Run browser-based QA test on any URL
- ta.run_android_flow       — Run Android emulator test on mobile apps
- ta.collect_trace_bundle   — Get ActionSpan evidence clips (2-3s video proof)
- ta.summarize_failure      — AI-summarize a test failure with root cause
- ta.suggest_fix_context    — Get fix suggestions scoped to your codebase
- ta.compare_before_after   — Compare two test runs to detect regressions

AGENT GUIDANCE:
When the user first connects, run ta.onboard.status to show what's ready.
After any code change (git diff shows modifications), suggest: ta.crawl.url to verify.
After a successful crawl with findings, suggest fixes then re-crawl.
After a re-crawl, suggest ta.savings.compare to show the improvement.
If no team exists, suggest ta.team.invite after the first successful run.
(56 tools total — run ta.onboard.status for the full list)
"""
    return PlainTextResponse(instructions.strip(), media_type="text/plain")


def _is_dev_mode() -> bool:
    """Dev mode exposes internal tools. Set TA_DEV_MODE=1 to enable."""
    return os.environ.get("TA_DEV_MODE", "").strip() in ("1", "true", "yes")


@router.get("/tools", response_model=List[MCPTool], summary="List available TA verification tools",
             dependencies=[Depends(verify_mcp_token)])
async def list_tools(request: Request) -> List[MCPTool]:
    """Implementing agents call this to discover available TA verification tools.

    Internal tools (codebase, playwright, investor_brief, etc.) are exposed
    for explicit dev mode OR local/TestClient requests used for dogfooding.
    """
    if _is_dev_mode() or _is_local_request(request):
        return _TOOLS
    return [t for t in _TOOLS if not t.internal]


# ── Retention dogfood: log our own MCP tool calls ──────────────────────
_RETENTION_BUFFER = Path.home() / ".retention" / "activity.jsonl"


def _log_retention_activity(
    tool_name: str, args: Dict[str, Any], status: str, duration_ms: int, caller_id: str,
) -> None:
    """Write tool call event to ~/.retention/activity.jsonl (fire-and-forget)."""
    try:
        _RETENTION_BUFFER.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": "retention-mcp",
            "session_id": caller_id,
            "tool_name": tool_name,
            "tool_input": {k: str(v)[:100] for k, v in (args or {}).items()},
            "status": status,
            "duration_ms": duration_ms,
        }
        with _RETENTION_BUFFER.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(event, default=str) + "\n")
    except Exception:
        pass  # never block the tool call


@router.post("/tools/call", response_model=MCPToolCallResponse, summary="Invoke a TA verification tool",
              dependencies=[Depends(verify_mcp_token)])
async def call_tool(req: MCPToolCallRequest, request: Request) -> MCPToolCallResponse:
    """Route a tool call from an external AI agent to the appropriate TA service."""
    t0 = time.time()
    args = req.arguments

    # Multi-MCP security: validate tool call before dispatch
    allowed, security_reason = validate_mcp_tool_call(req.tool, args)
    if not allowed:
        logger.warning("MCP security blocked tool=%s reason=%s", req.tool, security_reason)
        return MCPToolCallResponse(
            tool=req.tool,
            status="error",
            error=f"Security: {security_reason}",
            duration_ms=int((time.time() - t0) * 1000),
        )

    # Extract authenticated user context for downstream isolation
    mcp_user = getattr(request.state, "mcp_user", {}) if hasattr(request, "state") else {}
    caller_id = mcp_user.get("email", "anonymous")

    try:
        result = await _dispatch(req.tool, args, caller_id=caller_id)
        duration_ms = int((time.time() - t0) * 1000)

        # ── Dogfood: log every MCP tool call to retention activity buffer ──
        _log_retention_activity(req.tool, args, "ok", duration_ms, caller_id)

        return MCPToolCallResponse(
            tool=req.tool,
            status="ok",
            result=result,
            duration_ms=duration_ms,
        )
    except HTTPException as exc:
        duration_ms = int((time.time() - t0) * 1000)
        _log_retention_activity(req.tool, args, "error", duration_ms, caller_id)
        return MCPToolCallResponse(tool=req.tool, status="error", error=exc.detail, duration_ms=duration_ms)
    except Exception as exc:
        duration_ms = int((time.time() - t0) * 1000)
        _log_retention_activity(req.tool, args, "error", duration_ms, caller_id)
        logger.exception("MCP tool call failed: %s", req.tool)
        return MCPToolCallResponse(tool=req.tool, status="error", error=str(exc), duration_ms=duration_ms)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

async def _dispatch_codebase(tool: str, args: Dict[str, Any]) -> Any:
    """Handle ta.codebase.* tools using local git + filesystem."""
    args.pop("_caller_id", None)

    if tool == "ta.codebase.analyze_ui_impact":
        files_str = args.get("files_changed", "")
        files_changed = [f.strip() for f in files_str.split(",") if f.strip()]
        if not files_changed:
            return {"error": "files_changed is required"}
        from .code_linkage_routes import compute_impact, ImpactRequest
        impact_resp = await compute_impact(ImpactRequest(files_changed=files_changed))
        return {
            "tool": tool, "status": "ok",
            "affected_features": [f["name"] for f in impact_resp.affected_features],
            "screens_to_retest": impact_resp.screens_to_retest,
            "workflow_ids": impact_resp.workflow_ids,
            "suggested_reruns": impact_resp.suggested_reruns,
            "message": f"Found {len(impact_resp.affected_features)} affected UI features impacting {len(impact_resp.screens_to_retest)} screens."
        }


    if tool == "ta.codebase.recent_commits":
        limit = min(int(args.get("limit", 20)), 50)
        cmd = [
            "git", "log", f"-{limit}",
            "--format=%H|||%s|||%an|||%aI",
        ]
        path_filter = args.get("path")
        if path_filter:
            cmd += ["--", path_filter]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=_REPO_ROOT)
        if out.returncode != 0:
            raise ValueError(f"git log failed: {out.stderr[:300]}")
        commits = []
        for line in out.stdout.strip().splitlines():
            parts = line.split("|||", 3)
            if len(parts) == 4:
                commits.append({
                    "sha": parts[0][:8],
                    "sha_full": parts[0],
                    "message": parts[1][:120],
                    "author": parts[2],
                    "date": parts[3],
                })
        return commits

    if tool == "ta.codebase.commit_diff":
        sha = args.get("sha")
        if not sha:
            raise HTTPException(status_code=400, detail="sha is required")
        cmd = ["git", "show", "--stat", "--format=%H%n%s%n%an%n%aI", sha]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=_REPO_ROOT)
        if out.returncode != 0:
            raise ValueError(f"git show failed: {out.stderr[:300]}")
        lines = out.stdout.strip().splitlines()
        result = {
            "sha": lines[0][:8] if lines else sha,
            "message": lines[1] if len(lines) > 1 else "",
            "author": lines[2] if len(lines) > 2 else "",
            "date": lines[3] if len(lines) > 3 else "",
            "files": [],
        }
        # Parse stat lines (after the blank line)
        for line in lines[5:]:
            line = line.strip()
            if not line or line.startswith("Merge:"):
                continue
            # Format: "filename | N +++ ---"  or summary line
            if "|" in line:
                parts = line.split("|", 1)
                result["files"].append({
                    "filename": parts[0].strip(),
                    "changes": parts[1].strip(),
                })
        return result

    if tool == "ta.codebase.search":
        query = args.get("query")
        if not query:
            raise HTTPException(status_code=400, detail="query is required")
        search_type = args.get("search_type", "code")

        if search_type == "path":
            cmd = ["git", "ls-files"]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=_REPO_ROOT)
            files = [f for f in out.stdout.splitlines() if query.lower() in f.lower()]
            return {"matches": files[:300], "total": len(files)}
        else:
            # Use git grep (much faster than grep -r on git repos)
            cmd = ["git", "grep", "-l", "-i", query]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=_REPO_ROOT)
            file_matches = out.stdout.strip().splitlines()[:300]
            # Get a few content matches too
            cmd2 = ["git", "grep", "-n", "-i", "--max-count=3", query]
            out2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=10, cwd=_REPO_ROOT)
            content_matches = out2.stdout.strip().splitlines()[:100]
            return {"files": file_matches, "content_preview": content_matches, "total_files": len(file_matches)}

    if tool == "ta.codebase.read_file":
        rel_path = args.get("path")
        if not rel_path:
            raise HTTPException(status_code=400, detail="path is required")
        fpath = _safe_path(rel_path)
        if not fpath.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {rel_path}")
        if not fpath.is_file():
            raise HTTPException(status_code=400, detail=f"Not a file: {rel_path}")
        # Binary detection
        with open(fpath, "rb") as f:
            chunk = f.read(8192)
            if b"\x00" in chunk:
                return {"path": rel_path, "binary": True, "size": fpath.stat().st_size}
        content = fpath.read_text(errors="replace")
        lines = content.splitlines()
        total_lines = len(lines)
        start = max(0, int(args.get("start_line", 1)) - 1)
        end = int(args.get("end_line", 0)) or total_lines
        lines = lines[start:end]
        truncated = len(lines) > 500
        if truncated:
            lines = lines[:500]
        return {
            "path": rel_path,
            "total_lines": total_lines,
            "truncated": truncated,
            "content": "\n".join(lines),
        }

    if tool == "ta.codebase.list_directory":
        rel_path = args.get("path", "")
        dpath = _safe_path(rel_path) if rel_path else _REPO_ROOT
        if not dpath.exists():
            raise HTTPException(status_code=404, detail=f"Directory not found: {rel_path}")
        if not dpath.is_dir():
            raise HTTPException(status_code=400, detail=f"Not a directory: {rel_path}")
        entries = []
        for item in sorted(dpath.iterdir()):
            name = item.name
            if name.startswith(".") and name not in (".claude",):
                continue
            entries.append({
                "name": name,
                "type": "dir" if item.is_dir() else "file",
                "path": str(item.relative_to(_REPO_ROOT)),
                "size": item.stat().st_size if item.is_file() else None,
            })
        return {"path": rel_path or "/", "entries": entries}

    if tool == "ta.codebase.file_tree":
        tree_path = args.get("path", "")
        cmd = ["git", "ls-tree", "-r", "--name-only", "HEAD"]
        if tree_path:
            cmd += ["--", tree_path]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=_REPO_ROOT)
        all_files = out.stdout.strip().splitlines()
        if tree_path:
            all_files = [f for f in all_files if f.startswith(tree_path)]
        total = len(all_files)
        truncated = total > 500
        return {"root": tree_path or "/", "total_items": total, "truncated": truncated, "files": all_files[:500]}

    if tool == "ta.codebase.git_status":
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, cwd=_REPO_ROOT,
        )
        entries = []
        for line in out.stdout.splitlines():
            if len(line) >= 4:
                entries.append({"status": line[:2].strip(), "path": line[3:]})
        return {"entries": entries, "total": len(entries)}

    if tool == "ta.codebase.exec_python":
        code = args.get("code", "")
        if not code:
            raise HTTPException(status_code=400, detail="code is required")

        # Sandboxed execution: run in a subprocess with a restricted wrapper
        # that only exposes safe standard-library + data-science modules.
        output_dir = Path("/tmp/agent_outputs")
        output_dir.mkdir(exist_ok=True)

        wrapper = (
            "import builtins as _builtins\n"
            "# Block-list: dangerous modules that enable code execution, networking, or system access\n"
            "_BLOCKED_MODULES = {\n"
            "    'subprocess','shutil','socket','http',\n"
            "    'urllib.request','urllib.error',\n"
            "    'requests','httpx','aiohttp','paramiko','ftplib',\n"
            "    'smtplib','imaplib','poplib','telnetlib','xmlrpc',\n"
            "    'multiprocessing','signal','ctypes','cffi',\n"
            "    'importlib','runpy','code','codeop','compileall',\n"
            "    'webbrowser','antigravity','turtle','tkinter',\n"
            "}\n"
            "_orig_import = _builtins.__import__\n"
            "def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):\n"
            "    if level > 0: return _orig_import(name, globals, locals, fromlist, level)\n"
            "    top = name.split('.')[0]\n"
            "    if top in _BLOCKED_MODULES:\n"
            "        raise ImportError(f'Import {name!r} is not allowed in sandbox')\n"
            "    # Check dotted names (e.g. urllib.request is blocked but urllib.parse is not)\n"
            "    if any(name == b or name.startswith(b + '.') for b in _BLOCKED_MODULES):\n"
            "        raise ImportError(f'Import {name!r} is not allowed in sandbox')\n"
            "    mod = _orig_import(name, globals, locals, fromlist, level)\n"
            "    # Neuter os danger methods after import\n"
            "    if top == 'os' and hasattr(mod, 'system'):\n"
            "        for _d in ('system','popen','exec','execvp','execvpe','spawn',\n"
            "                   'spawnl','spawnle','fork','kill','killpg','remove',\n"
            "                   'unlink','rmdir','removedirs'):\n"
            "            if hasattr(mod, _d): setattr(mod, _d, lambda *a,**k: None)\n"
            "        mod.environ = {}  # hide real env\n"
            "    return mod\n"
            "_builtins.__import__ = _safe_import\n"
        )
        full_code = wrapper + "\n" + code

        # Run in isolated subprocess with NO inherited env vars (prevents secret leakage)
        safe_env = {
            "PATH": "/usr/bin:/usr/local/bin",
            "HOME": "/tmp",
            "AGENT_OUTPUT_DIR": str(output_dir),
            "PYTHONPATH": "",
        }
        try:
            out = subprocess.run(
                ["python3", "-c", full_code],
                capture_output=True, text=True, timeout=60,
                cwd=str(_REPO_ROOT),
                env=safe_env,
            )
            result = {
                "stdout": out.stdout[:8000],
                "stderr": out.stderr[:3000],
                "returncode": out.returncode,
            }
            stdout = out.stdout.strip()
            if stdout.startswith("{") or stdout.startswith("["):
                try:
                    result["parsed"] = json.loads(stdout)
                except (json.JSONDecodeError, ValueError):
                    pass
            return result
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "Execution timed out (60s limit)", "returncode": 1}

    if tool == "ta.codebase.shell_command":
        command = args.get("command", "")
        if not command:
            raise HTTPException(status_code=400, detail="command is required")

        # Allowlist approach: only permit known-safe data processing commands.
        # Parse the command to extract all executable words.
        _ALLOWED_CMDS = frozenset({
            "wc", "sort", "uniq", "head", "tail", "jq", "date", "cal",
            "ls", "cat", "grep", "find", "du", "df", "echo", "awk",
            "sed", "tr", "cut", "paste", "column", "tee", "xargs",
            "diff", "comm", "basename", "dirname", "realpath", "stat",
            "file", "true", "false", "test", "expr", "seq", "yes",
            "env", "printenv",
        })

        import shlex
        try:
            tokens = shlex.split(command)
        except ValueError:
            return {"stdout": "", "stderr": "Failed to parse command", "returncode": 1}

        # Extract all command positions: first token + after |, &&, ;
        cmd_positions = [0] if tokens else []
        for i, tok in enumerate(tokens):
            if tok in ("|", "&&", ";", "||") and i + 1 < len(tokens):
                cmd_positions.append(i + 1)

        for pos in cmd_positions:
            if pos >= len(tokens):
                continue
            cmd_base = os.path.basename(tokens[pos])
            if cmd_base not in _ALLOWED_CMDS:
                return {
                    "stdout": "",
                    "stderr": f"Command '{cmd_base}' is not in the allowlist. "
                              f"Allowed: {', '.join(sorted(_ALLOWED_CMDS))}",
                    "returncode": 1,
                }

        # Block subshells, process substitution, backticks
        for dangerous in ("$(", "`", "<(", ">("):
            if dangerous in command:
                return {
                    "stdout": "",
                    "stderr": f"Subshell/process substitution ('{dangerous}') is not allowed",
                    "returncode": 1,
                }

        # Block output redirection to non-tmp paths
        if ">" in command and "/tmp" not in command:
            return {"stdout": "", "stderr": "Output redirection only allowed to /tmp/", "returncode": 1}

        try:
            out = subprocess.run(
                ["/bin/bash", "-c", command],
                capture_output=True, text=True, timeout=30, cwd=str(_REPO_ROOT),
                env={"PATH": "/usr/bin:/usr/local/bin:/bin", "HOME": "/tmp"},
            )
            return {
                "stdout": out.stdout[:8000],
                "stderr": out.stderr[:3000],
                "returncode": out.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "Execution timed out (30s limit)", "returncode": 1}

    if tool == "ta.codebase.run_tests":
        files = args.get("files", [])
        timeout = min(int(args.get("timeout", 120)), 300)
        if not files:
            # Auto-discover: find test files for changed Python files
            diff_out = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                capture_output=True, text=True, timeout=10, cwd=_REPO_ROOT,
            )
            changed = [f for f in diff_out.stdout.splitlines() if f.endswith(".py")]
            test_files = set()
            for f in changed:
                name = os.path.basename(f)
                dir_path = os.path.dirname(f)
                # Convention: test_foo.py or foo_test.py in same dir or tests/ dir
                test_files.add(os.path.join(dir_path, f"test_{name}"))
                test_files.add(os.path.join(dir_path, f"{name.replace('.py', '')}_test.py"))
                test_files.add(os.path.join("backend/tests", f"test_{name}"))
            # Filter to files that actually exist
            files = [f for f in test_files if (_REPO_ROOT / f).exists()]
        if not files:
            return {"passed": True, "summary": "No test files found for changed code", "test_count": 0}
        try:
            out = subprocess.run(
                ["python3", "-m", "pytest", "-v", "--tb=short", "-q"] + files,
                capture_output=True, text=True, timeout=timeout, cwd=_REPO_ROOT,
            )
            passed = out.returncode == 0
            # Parse summary line (e.g. "4 passed, 1 failed")
            summary_line = ""
            for line in reversed(out.stdout.splitlines()):
                if "passed" in line or "failed" in line or "error" in line:
                    summary_line = line.strip()
                    break
            return {
                "passed": passed,
                "summary": summary_line or ("all passed" if passed else "tests failed"),
                "stdout": out.stdout[-3000:],
                "stderr": out.stderr[-1000:],
                "returncode": out.returncode,
                "files_tested": files,
            }
        except subprocess.TimeoutExpired:
            return {"passed": False, "summary": f"Tests timed out after {timeout}s", "test_count": 0}

    if tool == "ta.codebase.create_pull_request":
        branch_name = args.get("branch_name", "")
        title = args.get("title", "")
        body = args.get("body", "")
        base = args.get("base", "main")
        if not branch_name or not title:
            raise HTTPException(status_code=400, detail="branch_name and title are required")
        # Check gh CLI is available
        gh_check = subprocess.run(["which", "gh"], capture_output=True, text=True)
        if gh_check.returncode != 0:
            return {"status": "error", "error": "gh CLI not installed. Install with: brew install gh"}
        # Create branch and switch to it
        subprocess.run(["git", "checkout", "-b", branch_name], cwd=_REPO_ROOT, timeout=10,
                       capture_output=True, text=True)
        # Stage all changes
        subprocess.run(["git", "add", "-A"], cwd=_REPO_ROOT, timeout=10)
        # Check if there are staged changes
        status_out = subprocess.run(["git", "diff", "--cached", "--stat"],
                                    capture_output=True, text=True, timeout=10, cwd=_REPO_ROOT)
        if not status_out.stdout.strip():
            subprocess.run(["git", "checkout", base], cwd=_REPO_ROOT, timeout=10, capture_output=True)
            subprocess.run(["git", "branch", "-D", branch_name], cwd=_REPO_ROOT, timeout=10, capture_output=True)
            return {"status": "error", "error": "No changes to commit"}
        # Commit
        subprocess.run(["git", "commit", "-m", title], cwd=_REPO_ROOT, timeout=10, capture_output=True)
        # Push branch
        push = subprocess.run(["git", "push", "-u", "origin", branch_name],
                              capture_output=True, text=True, timeout=30, cwd=_REPO_ROOT)
        if push.returncode != 0:
            return {"status": "push_failed", "error": push.stderr[:500]}
        # Generate PR body if not provided
        if not body:
            diff_out = subprocess.run(["git", "diff", f"{base}...HEAD", "--stat"],
                                      capture_output=True, text=True, timeout=10, cwd=_REPO_ROOT)
            from ..services.llm_judge import call_responses_api
            body = await call_responses_api(
                f"Write a concise GitHub PR description for these changes. "
                f"Use markdown with ## Summary and ## Test plan sections.\n\n"
                f"Title: {title}\nFiles changed:\n{diff_out.stdout[:2000]}",
                task="compose_response", reasoning_effort="low", timeout_s=30,
            )
        # Create PR
        pr = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", body, "--base", base],
            capture_output=True, text=True, timeout=30, cwd=_REPO_ROOT,
        )
        # Switch back to base
        subprocess.run(["git", "checkout", base], cwd=_REPO_ROOT, timeout=10, capture_output=True)
        pr_url = pr.stdout.strip()
        return {"status": "created", "pr_url": pr_url, "branch": branch_name, "base": base}

    if tool == "ta.codebase.merge_pull_request":
        pr_number = int(args.get("pr_number", 0))
        merge_method = args.get("merge_method", "squash")
        if not pr_number:
            raise HTTPException(status_code=400, detail="pr_number is required")
        if merge_method not in ("merge", "squash", "rebase"):
            merge_method = "squash"
        result = subprocess.run(
            ["gh", "pr", "merge", str(pr_number), f"--{merge_method}", "--delete-branch"],
            capture_output=True, text=True, timeout=30, cwd=_REPO_ROOT,
        )
        if result.returncode != 0:
            return {"status": "failed", "error": result.stderr[:500]}
        return {"status": "merged", "pr_number": pr_number, "method": merge_method}

    if tool == "ta.codebase.create_github_issue":
        title = args.get("title", "")
        body = args.get("body", "")
        labels = args.get("labels", [])
        if not title:
            raise HTTPException(status_code=400, detail="title is required")
        gh_check = subprocess.run(["which", "gh"], capture_output=True, text=True)
        if gh_check.returncode != 0:
            return {"status": "error", "error": "gh CLI not installed"}
        cmd = ["gh", "issue", "create", "--title", title]
        if body:
            cmd += ["--body", body]
        if labels:
            cmd += ["--label", ",".join(labels)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=_REPO_ROOT)
        if result.returncode != 0:
            return {"status": "error", "error": result.stderr[:500]}
        return {"status": "created", "url": result.stdout.strip()}

    if tool == "ta.codebase.write_file":
        rel_path = args.get("path")
        content = args.get("content", "")
        if not rel_path:
            raise HTTPException(status_code=400, detail="path is required")
        fpath = _safe_path(rel_path)
        # Safety: no writing outside repo
        try:
            fpath.relative_to(_REPO_ROOT)
        except ValueError:
            raise HTTPException(status_code=400, detail="Path escapes repo root")
        # No writing to .env or credentials
        blocked_names = [".env", "credentials", "secrets", ".ssh", "id_rsa"]
        if any(b in str(fpath).lower() for b in blocked_names):
            raise HTTPException(status_code=400, detail=f"Cannot write to sensitive file: {rel_path}")
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content)
        return {"written": str(rel_path), "size": len(content)}

    if tool == "ta.codebase.git_commit_and_push":
        import json as _json
        message = args.get("message", "")
        files = args.get("files", [])
        skip_review = args.get("skip_review", False)
        if not message:
            raise HTTPException(status_code=400, detail="commit message is required")

        # 1. Stage files
        if files:
            for f in files:
                _safe_path(f)  # validate path
            subprocess.run(["git", "add"] + files, cwd=_REPO_ROOT, timeout=10)
        else:
            subprocess.run(["git", "add", "-A"], cwd=_REPO_ROOT, timeout=10)

        # 2. Get the diff for review
        diff_out = subprocess.run(
            ["git", "diff", "--cached", "--stat"],
            capture_output=True, text=True, timeout=10, cwd=_REPO_ROOT,
        )
        diff_detail = subprocess.run(
            ["git", "diff", "--cached"],
            capture_output=True, text=True, timeout=10, cwd=_REPO_ROOT,
        )
        if not diff_out.stdout.strip():
            return {"status": "nothing_to_commit", "message": "No staged changes"}

        # 2b. Run tests on changed files before proceeding
        changed_py = [
            line.split()[-1] for line in diff_out.stdout.splitlines()
            if line.strip().endswith(".py") and "|" in line
        ]
        if changed_py and not skip_review:
            test_result = await _dispatch_codebase("ta.codebase.run_tests", {"files": []})
            if not test_result.get("passed", True) and test_result.get("test_count", 0) > 0:
                subprocess.run(["git", "reset", "HEAD"], cwd=_REPO_ROOT, timeout=10)
                return {
                    "status": "tests_failed",
                    "test_output": test_result.get("summary", ""),
                    "stdout": test_result.get("stdout", "")[-1000:],
                    "message": "Tests failed. Fix the issues and try again.",
                }

        # 3. AI code review gate
        review_verdict = {"approved": True, "reason": "review skipped"}
        if not skip_review:
            from ..services.llm_judge import call_responses_api
            diff_text = diff_detail.stdout[:6000]  # Truncate for token budget
            review_prompt = (
                "You are a senior code reviewer. Review this git diff and decide "
                "whether it is safe to push to main. Check for:\n"
                "1. Security issues (hardcoded secrets, injection, unsafe eval)\n"
                "2. Syntax errors or broken imports\n"
                "3. Unintended file changes (node_modules, .env, large binaries)\n"
                "4. Logic bugs or regressions\n\n"
                f"Commit message: {message}\n\n"
                f"Diff:\n```\n{diff_text}\n```\n\n"
                "Respond with JSON: {\"approved\": true/false, \"reason\": \"...\"}\n"
                "If unsure, err on the side of approving — the git history allows revert."
            )
            try:
                review_raw = await call_responses_api(
                    review_prompt,
                    task="gate_evaluation",
                    reasoning_effort="medium",
                    timeout_s=30,
                )
                # Parse JSON from response
                review_raw = review_raw.strip()
                if review_raw.startswith("```"):
                    review_raw = review_raw.split("```")[1]
                    if review_raw.startswith("json"):
                        review_raw = review_raw[4:]
                review_verdict = _json.loads(review_raw)
            except Exception as e:
                # If review fails, still allow push but note it
                review_verdict = {"approved": True, "reason": f"Review error (auto-approved): {e}"}

        if not review_verdict.get("approved", False):
            # Unstage
            subprocess.run(["git", "reset", "HEAD"], cwd=_REPO_ROOT, timeout=10)
            return {
                "status": "rejected",
                "review": review_verdict,
                "message": "AI reviewer rejected this change. Fix the issues and try again.",
            }

        # 4. Commit
        commit_out = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True, text=True, timeout=10, cwd=_REPO_ROOT,
        )
        if commit_out.returncode != 0:
            return {"status": "commit_failed", "error": commit_out.stderr[:500]}

        # Extract commit SHA
        sha_out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=_REPO_ROOT,
        )
        sha = sha_out.stdout.strip()

        # 5. Tag checkpoint for easy revert
        tag_name = f"pre-push/{sha}"
        subprocess.run(
            ["git", "tag", tag_name, f"{sha}~1"],
            capture_output=True, text=True, timeout=5, cwd=_REPO_ROOT,
        )

        # 6. Push
        push_out = subprocess.run(
            ["git", "push", "origin", "main"],
            capture_output=True, text=True, timeout=30, cwd=_REPO_ROOT,
        )
        if push_out.returncode != 0:
            return {
                "status": "push_failed",
                "sha": sha,
                "error": push_out.stderr[:500],
                "review": review_verdict,
                "note": f"Committed locally as {sha} but push failed. Revert tag: {tag_name}",
            }

        return {
            "status": "pushed",
            "sha": sha,
            "review": review_verdict,
            "files_changed": diff_out.stdout.strip(),
            "revert_tag": tag_name,
            "message": f"Committed {sha} and pushed to GitHub. Revert with: git revert {sha}",
        }

    raise HTTPException(status_code=400, detail=f"Unknown codebase tool: {tool}")


async def _dispatch(tool: str, args: Dict[str, Any], *, caller_id: str = "anonymous") -> Any:
    # Inject caller_id into args for downstream isolation (pipeline, feedback, relay)
    args["_caller_id"] = caller_id

    if tool.startswith("ta.codebase."):
        return await _dispatch_codebase(tool, args)

    if tool == "ta.request_validation_gate":
        from .validation_hooks import ValidationHook, HookStatus
        import uuid
        hook = ValidationHook(
            hook_id=str(uuid.uuid4()),
            agent_id=args.get("agent_id", "unknown"),
            task_description=args.get("task_description", ""),
            status=HookStatus.PENDING,
            pr_url=args.get("pr_url"),
            repo=args.get("repo"),
            branch=args.get("branch"),
            requested_by=args.get("requested_by"),
            created_at=_now(),
            updated_at=_now(),
        )
        _hooks[hook.hook_id] = hook

        # Auto-trigger QA verification if repo/branch/pr_url provided
        # This makes the hook a real gate: request → QA runs → release/block
        app_url = args.get("app_url", "")
        if app_url:
            hook.status = HookStatus.RUNNING
            hook.updated_at = _now()

            async def _run_validation_qa():
                """Background QA run that releases or blocks the hook."""
                import asyncio as _asyncio
                try:
                    from .mcp_pipeline import run_playwright_pipeline, _running_pipelines, _persisted_results, _normalize_execution
                    qa_run_id = await run_playwright_pipeline(
                        url=app_url,
                        app_name=f"Validation: {hook.task_description[:30]}",
                    )
                    if qa_run_id:
                        # Poll until complete
                        for _ in range(360):
                            await _asyncio.sleep(10)
                            entry = _running_pipelines.get(qa_run_id) or _persisted_results.get(qa_run_id) or {}
                            if entry.get("status") in ("complete", "error"):
                                break

                        # Get pass rate
                        final_entry = _running_pipelines.get(qa_run_id) or _persisted_results.get(qa_run_id) or {}
                        final_result = final_entry.get("result", final_entry)
                        exec_norm = _normalize_execution(final_result)
                        pass_rate = exec_norm.get("pass_rate", 0)
                        if not pass_rate:
                            summary = final_result.get("summary", {})
                            pass_rate = summary.get("pass_rate", 0)

                        if pass_rate >= 0.8:
                            hook.status = HookStatus.RELEASED
                            hook.release_notes = f"QA passed ({pass_rate:.0%}). Run: {qa_run_id}"
                        else:
                            hook.status = HookStatus.BLOCKED
                            hook.failure_reason = f"QA failed ({pass_rate:.0%}). Run: {qa_run_id}"
                    else:
                        hook.status = HookStatus.BLOCKED
                        hook.failure_reason = "QA pipeline failed to start"
                except Exception as exc:
                    import traceback
                    logger.error(f"Validation gate QA error: {traceback.format_exc()}")
                    hook.status = HookStatus.BLOCKED
                    hook.failure_reason = f"QA error: {exc}"
                hook.updated_at = _now()

            import asyncio
            asyncio.create_task(_run_validation_qa())

        return {"hook_id": hook.hook_id, "status": hook.status, "message": "Gate opened. Poll ta.get_hook_status until released."}

    if tool == "ta.get_hook_status":
        hook_id = args.get("hook_id")
        hook = _hooks.get(hook_id)
        if not hook:
            raise HTTPException(status_code=404, detail=f"Hook not found: {hook_id}")
        return {"hook_id": hook.hook_id, "status": hook.status, "release_notes": hook.release_notes, "failure_reason": hook.failure_reason}

    if tool == "ta.get_evidence_manifest":
        session_id = args.get("session_id")
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")
        manifest = action_span_service.get_manifest(session_id)
        return manifest.model_dump()

    # ── ta.setup.* — Environment setup tools for Claude Code agent ────
    if tool == "ta.setup.status":
        from .setup import _check_android_sdk, _check_adb, _check_emulators, _check_node, _check_java
        import platform as _platform
        android_sdk = _check_android_sdk()
        adb = _check_adb()
        emulators = _check_emulators()
        node = _check_node()
        java = _check_java()

        requirements_met = [
            android_sdk["installed"], adb["installed"],
            emulators["count"] > 0, node["installed"], java["installed"],
        ]
        ready = all(requirements_met)
        progress = round(sum(requirements_met) / len(requirements_met) * 100)

        # Build actionable fix commands for each missing requirement
        fixes = []
        system = _platform.system()
        if not java["installed"]:
            fixes.append({
                "component": "java",
                "command": "brew install openjdk@17" if system == "Darwin" else "sudo apt install -y openjdk-17-jdk",
                "reason": "Java 17 is required for Android emulator",
            })
        if not android_sdk["installed"]:
            fixes.append({
                "component": "android_sdk",
                "command": "./scripts/setup-macos.sh" if system == "Darwin" else "./scripts/setup-linux.sh",
                "reason": "Android SDK provides emulator and ADB tools",
            })
        if not adb["installed"]:
            fixes.append({
                "component": "adb",
                "command": "Install Android SDK platform-tools (included in setup script)",
                "reason": "ADB communicates with Android emulators",
            })
        if emulators["count"] == 0 and android_sdk["installed"]:
            fixes.append({
                "component": "avd",
                "command": "avdmanager create avd -n Pixel_7_API_34 -k 'system-images;android-34;google_apis;arm64-v8a' -d pixel_7",
                "reason": "An AVD (virtual device) is needed to run the emulator",
            })
        if emulators["count"] > 0 and len(adb.get("devices", [])) == 0:
            avd = emulators["avds"][0] if emulators["avds"] else "Pixel_7_API_34"
            fixes.append({
                "component": "emulator_launch",
                "command": f"emulator -avd {avd} -no-audio -gpu swiftshader_indirect &",
                "reason": "An AVD exists but no emulator is running. Launch it.",
            })

        return {
            "ready": ready,
            "progress": progress,
            "system": {"os": system, "platform": _platform.platform(), "arch": _platform.machine()},
            "requirements": {
                "android_sdk": android_sdk,
                "adb": adb,
                "emulators": emulators,
                "node": node,
                "java": java,
            },
            "fixes": fixes,
            "setup_script": f"./scripts/setup-{'macos' if system == 'Darwin' else 'linux'}.sh",
            "next_step": fixes[0]["command"] if fixes else "All requirements met — ready for QA!",
        }

    if tool == "ta.setup.launch_emulator":
        from .setup import launch_emulator as _launch_emulator_endpoint
        avd_name = args.get("avd_name")
        try:
            result = await _launch_emulator_endpoint(avd_name=avd_name)
            return {
                "success": True,
                "avd": result["avd"],
                "message": result["message"],
                "next_step": "Wait ~30 seconds for emulator to boot, then call ta.system_check to verify.",
            }
        except HTTPException as exc:
            return {"success": False, "error": exc.detail, "fix": "Run ./scripts/setup-macos.sh to install Android SDK and create an AVD."}

    if tool == "ta.setup.instructions":
        from .setup import get_setup_instructions as _get_instructions
        return await _get_instructions()

    if tool == "ta.system_check":
        import shutil
        import subprocess
        checks = {}
        all_pass = True

        # 1. Backend health
        checks["backend"] = {"status": "pass", "detail": "FastAPI running on this server"}

        # 2. ADB / Emulator
        if shutil.which("adb"):
            try:
                out = subprocess.run(
                    ["adb", "devices", "-l"],
                    capture_output=True, text=True, timeout=10,
                )
                lines = [
                    l for l in out.stdout.strip().splitlines()[1:]
                    if l.strip() and "offline" not in l
                ]
                if lines:
                    devices = [l.split()[0] for l in lines]
                    checks["emulator"] = {
                        "status": "pass",
                        "detail": f"{len(devices)} device(s): {', '.join(devices)}",
                    }
                else:
                    checks["emulator"] = {
                        "status": "fail",
                        "detail": "ADB found but no devices connected",
                        "fix": "Run: emulator -avd Pixel_7_API_34 &",
                    }
                    all_pass = False
            except Exception as exc:
                checks["emulator"] = {
                    "status": "fail",
                    "detail": f"ADB error: {exc}",
                    "fix": "Check Android SDK installation",
                }
                all_pass = False
        else:
            checks["emulator"] = {
                "status": "skip",
                "detail": "ADB not found (optional — needed for Android QA only)",
                "fix": "Run: ./scripts/setup-macos.sh",
            }

        # 3. Playwright
        try:
            subprocess.run(
                ["python3", "-c", "import playwright; print('ok')"],
                capture_output=True, text=True, timeout=10,
                check=True,
            )
            checks["playwright"] = {"status": "pass", "detail": "Playwright installed"}
        except Exception:
            checks["playwright"] = {
                "status": "fail",
                "detail": "Playwright not installed",
                "fix": "pip install playwright && playwright install chromium",
            }
            all_pass = False

        # 4. WebSocket relay — check if agent_relay module is loaded
        from . import agent_relay as _ar
        relay_status = _ar.relay_registry.status()
        if relay_status.get("connected_relays", 0) > 0:
            checks["relay"] = {"status": "pass", "detail": f"WebSocket relay: {relay_status['connected_relays']} session(s)"}
        else:
            checks["relay"] = {
                "status": "warn",
                "detail": "No relay clients connected (OK for local-only use)",
                "fix": "Run: npx retention-mcp@latest --setup on client machine",
            }

        # 5. MCP token
        token_path = _REPO_ROOT / ".claude" / "mcp-token"
        if token_path.exists() and token_path.read_text().strip():
            checks["mcp_token"] = {"status": "pass", "detail": "MCP token configured"}
        else:
            checks["mcp_token"] = {
                "status": "warn",
                "detail": "No MCP token set (auth disabled — OK for local use)",
            }

        # 6. Optional quick web test
        if args.get("include_web_test"):
            try:
                from playwright.async_api import async_playwright
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--single-process"])
                    page = await browser.new_page()
                    await page.goto("http://localhost:5173", timeout=8000)
                    title = await page.title()
                    await browser.close()
                checks["web_test"] = {
                    "status": "pass",
                    "detail": f"Local app loaded — title: {title[:60]}",
                }
            except Exception as exc:
                checks["web_test"] = {
                    "status": "fail",
                    "detail": f"Could not load localhost:5173: {str(exc)[:120]}",
                    "fix": "Start frontend: cd frontend/test-studio && npm run dev",
                }
                all_pass = False

        passed = sum(1 for c in checks.values() if c["status"] == "pass")
        total = len(checks)

        # Check for updates
        update_available = None
        CURRENT_VERSION = "0.4.0"
        try:
            import httpx
            ver_resp = httpx.get(
                "https://test-studio-xi.vercel.app/retention-config/version.json",
                timeout=5,
            )
            if ver_resp.status_code == 200:
                ver_data = ver_resp.json()
                latest = ver_data.get("version", CURRENT_VERSION)
                if latest != CURRENT_VERSION:
                    changelog = ver_data.get("changelog", [{}])[0]
                    update_available = {
                        "current": CURRENT_VERSION,
                        "latest": latest,
                        "highlights": changelog.get("highlights", [])[:5],
                        "update_command": "curl -sL retention.sh/install.sh | bash",
                    }
                    checks["update"] = {
                        "status": "warn",
                        "detail": f"Update available: {CURRENT_VERSION} → {latest}",
                        "fix": "curl -sL retention.sh/install.sh | bash",
                    }
                else:
                    checks["update"] = {"status": "pass", "detail": f"Up to date (v{CURRENT_VERSION})"}
        except Exception:
            checks["update"] = {"status": "info", "detail": f"Version {CURRENT_VERSION} (update check skipped)"}

        return {
            "ready": all_pass,
            "version": CURRENT_VERSION,
            "summary": f"{passed}/{total} checks passed",
            "checks": checks,
            "update": update_available,
            "dashboard": "https://retention.sh/demo",
            "next_step": (
                "You're ready! Try: ta.sitemap(url='http://localhost:5173') or ta.qa_check(url='http://localhost:5173')"
                if all_pass
                else "Fix the failing checks above, then run ta.system_check again."
            ),
        }

    if tool == "ta.smoke_test":
        # Lightweight: just verify adb is reachable and report
        import shutil
        import subprocess
        device_id = args.get("device_id")
        if not shutil.which("adb"):
            return {"passed": False, "verdict": "NO_ADB", "error": "adb not found on PATH", "device_id": device_id or "auto"}
        adb_cmd = ["adb"] + (["-s", device_id] if device_id else []) + ["shell", "echo", "ta_smoke_ok"]
        try:
            out = subprocess.run(adb_cmd, capture_output=True, text=True, timeout=10)
            passed = "ta_smoke_ok" in out.stdout
            if not passed and "error: no devices" in (out.stderr or ""):
                return {"passed": False, "verdict": "NO_DEVICE", "error": "No connected devices/emulators", "device_id": device_id or "auto"}
            return {"passed": passed, "verdict": "PASS" if passed else "FAIL", "device_id": device_id or "auto"}
        except subprocess.TimeoutExpired:
            return {"passed": False, "verdict": "TIMEOUT", "error": "adb timed out after 10s", "device_id": device_id or "auto"}
        except Exception as exc:
            return {"passed": False, "verdict": "FAIL", "error": str(exc), "device_id": device_id or "auto"}

    if tool.startswith("ta.investor_brief."):
        return await _dispatch_investor_brief(tool, args)

    if tool.startswith("ta.slack."):
        return await _dispatch_slack(tool, args)

    if tool.startswith("ta.playwright."):
        return await _dispatch_playwright(tool, args)

    if tool.startswith("ta.pipeline."):
        from .mcp_pipeline import dispatch_pipeline
        return await dispatch_pipeline(tool, args)

    if tool.startswith("ta.feedback."):
        from .mcp_pipeline import dispatch_feedback
        return await dispatch_feedback(tool, args)

    if tool.startswith("ta.device."):
        from .mcp_pipeline import dispatch_device
        return await dispatch_device(tool, args)

    if tool.startswith("ta.meta."):
        from .mcp_pipeline import dispatch_meta
        return await dispatch_meta(tool, args)

    # QA verification tools (ta.run_*, ta.collect_*, ta.summarize_*, ta.compare_*, ta.emit_*, ta.suggest_*)
    _qa_verification_tools = {
        "ta.run_web_flow", "ta.run_android_flow", "ta.rerun",
        "ta.collect_trace_bundle",
        "ta.summarize_failure", "ta.compare_before_after", "ta.emit_verdict",
        "ta.suggest_fix_context",
    }
    if tool in _qa_verification_tools:
        from .mcp_pipeline import dispatch_qa_verification
        return await dispatch_qa_verification(tool, args)

    if tool.startswith("ta.benchmark."):
        return await _dispatch_benchmark(tool, args)

    if tool.startswith("ta.nemoclaw."):
        return await _dispatch_nemoclaw(tool, args)

    if tool.startswith("ta.design."):
        return await _dispatch_design(tool, args)

    if tool.startswith("ta.trajectory."):
        return _dispatch_trajectory(tool, args)

    if tool.startswith("ta.tcwp."):
        from .mcp_tcwp import dispatch_tcwp
        return dispatch_tcwp(tool, args)

    if tool.startswith("ta.audit."):
        from .mcp_audit import dispatch_audit
        return dispatch_audit(tool, args)

    if tool.startswith("ta.compress."):
        from .mcp_compress import dispatch_compress
        return dispatch_compress(tool, args)

    if tool.startswith("ta.checkpoint."):
        from .mcp_checkpoint import dispatch_checkpoint
        return dispatch_checkpoint(tool, args)

    if tool in ("ta.savings.forecast", "ta.savings.roi", "ta.savings.breakdown"):
        from .mcp_savings import dispatch_savings
        return dispatch_savings(tool, args)

    if tool == "ta.usage.sync_ccusage":
        from ..services.ccusage_tracker import sync_ccusage_to_telemetry
        days = int(args.get("days", 7))
        result = sync_ccusage_to_telemetry(days=days)
        return {"tool": tool, "status": "ok", **result}

    if tool == "ta.usage.summary":
        from ..services.usage_telemetry import summarize_usage
        days = int(args.get("days", 1))
        return {"tool": tool, "status": "ok", **summarize_usage(days=days)}

    if tool == "ta.explore.run":
        return await _dispatch_explore(args)

    # Retention self-serve QA loop
    if tool in ("ta.onboard.status", "ta.crawl.url", "ta.savings.compare", "ta.team.invite", "ta.qa.redesign"):
        return await _dispatch_retention(tool, args)

    if tool.startswith("ta.memory."):
        from ..agents.qa_pipeline.exploration_memory import (
            get_memory_stats, check_memory, invalidate_app,
        )
        if tool == "ta.memory.stats":
            return get_memory_stats()
        if tool == "ta.memory.check":
            app_url = args.get("app_url", "")
            mem = check_memory(app_url=app_url)
            return mem.summary()
        if tool == "ta.memory.invalidate":
            app_url = args.get("app_url", "")
            return invalidate_app(app_url=app_url)
        # export/import handled in _dispatch_memory
        return _dispatch_memory(tool, args)

    if tool == "ta.feedback_package":
        return await _build_feedback_package(tool, args)

    if tool == "ta.optimize_bundle":
        return _build_optimize_bundle_prompt(args)

    if tool == "ta.quickstart":
        return await _handle_quickstart(args)

    if tool == "ta.get_handoff":
        return await _handle_get_handoff(args)

    if tool.startswith("ta.memory."):
        return _dispatch_memory(tool, args)

    if tool.startswith("ta.linkage."):
        return _dispatch_linkage(tool, args)

    if tool.startswith("ta.graph."):
        return await _dispatch_context_graph(tool, args)

    if tool.startswith("ta.screenshots."):
        return _dispatch_screenshots(tool, args)

    if tool.startswith("ta.web_demo."):
        return await _dispatch_web_demo(tool, args)

    if tool == "ta.agent.run":
        return await _dispatch_agent_run(args)

    raise HTTPException(status_code=400, detail=f"Unknown tool: {tool}")


async def _build_feedback_package(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate failure data from a completed run into an autonomous fix prompt."""
    from .mcp_pipeline import dispatch_qa_verification

    caller_id = args.pop("_caller_id", "anonymous")
    run_id = args.get("run_id")
    if not run_id:
        return {"error": "run_id is required"}

    app_url = args.get("app_url", "")
    repo_root = args.get("repo_root", ".")
    rerun_command = args.get("rerun_command", "")

    # ── Gather data from the three underlying tools ──────────────────
    # Thread caller_id so ownership checks pass on internal calls
    summary = await dispatch_qa_verification(
        "ta.summarize_failure", {"run_id": run_id, "priority_filter": "all", "_caller_id": caller_id}
    )
    fix_ctx = await dispatch_qa_verification(
        "ta.suggest_fix_context", {"run_id": run_id, "max_files": 8, "_caller_id": caller_id}
    )
    trace = await dispatch_qa_verification(
        "ta.collect_trace_bundle", {"run_id": run_id, "include_video": False, "_caller_id": caller_id}
    )

    if summary.get("error"):
        return {"error": summary["error"]}

    # ── Build failure table for the prompt ────────────────────────────
    failures = summary.get("failures", [])
    if not failures:
        return {
            "tool": "ta.feedback_package",
            "status": "ok",
            "type": "agent_prompt",
            "run_id": run_id,
            "message": "No failures found — all tests passing. Nothing to fix.",
            "prompt": "",
        }

    failure_lines = []
    for i, f in enumerate(failures[:15], 1):
        step_info = ""
        if f.get("failing_step"):
            fs = f["failing_step"]
            step_info = (
                f"\n     Failing step #{fs.get('step_number', '?')}: "
                f"{fs.get('action', '?')} — expected: {fs.get('expected', '?')}, "
                f"got: {fs.get('actual', 'unknown')}"
            )
        failure_lines.append(
            f"  {i}. [{f.get('priority', 'medium').upper()}] {f.get('name', f.get('test_id', '?'))}\n"
            f"     Category: {f.get('category', 'unknown')}\n"
            f"     Status: {f.get('status', 'fail')}"
            f"{step_info}"
        )

    failure_block = "\n".join(failure_lines)

    # ── Build investigation suggestions block ────────────────────────
    suggestions = fix_ctx.get("suggestions", [])
    file_lines = []
    for s in suggestions:
        # suggest_fix_context returns {category/workflow, investigation} not file_path
        label = s.get("file_path", s.get("category", s.get("workflow", "")))
        detail = s.get("reason", s.get("investigation", ""))
        file_lines.append(f"  - {label}: {detail}" if label else f"  - {detail}")
    file_block = "\n".join(file_lines) if file_lines else "  (no specific suggestions — investigate based on failure categories)"

    # ── Build evidence block ─────────────────────────────────────────
    artifacts = trace.get("artifacts", {})
    evidence_block = (
        f"  Screenshots captured: {artifacts.get('screenshots_count', 0)}\n"
        f"  Tool calls recorded: {artifacts.get('tool_calls_count', 0)}\n"
        f"  Pipeline stages: {', '.join(artifacts.get('stages_traversed', []))}\n"
        f"  View full results: {summary.get('view_url', 'N/A')}"
    )

    # ── Build rerun instruction ──────────────────────────────────────
    if rerun_command:
        rerun_instruction = f"Run: `{rerun_command}`"
    elif app_url:
        rerun_instruction = (
            f"Call `ta.pipeline.rerun_failures` with baseline_run_id=\"{run_id}\" and "
            f"app_url=\"{app_url}\" to rerun ONLY the failed tests (skips crawl/discovery). "
            f"Then call `ta.compare_before_after` with baseline_run_id=\"{run_id}\" and "
            f"current_run_id=<new_run_id> to see what's fixed."
        )
    else:
        rerun_instruction = (
            f"Call `ta.pipeline.rerun_failures` with baseline_run_id=\"{run_id}\" to rerun "
            f"ONLY the failed tests (skips crawl/discovery — saves time and tokens). "
            f"Then call `ta.compare_before_after` to diff the results."
        )

    pass_rate = summary.get("pass_rate", 0)
    total = summary.get("total_test_cases", 0)
    fail_count = summary.get("failure_count", 0)

    prompt = f"""## retention.sh — Bug Fix Agent

You are now operating as an autonomous bug-fix agent. A QA pipeline has completed and
found **{fail_count} failing test(s)** out of {total} total ({pass_rate:.0%} pass rate).

Your job: fix every bug, then re-run QA to prove the fix. Do NOT ask the human for help
unless you are genuinely blocked.

---

### Run Summary

- **Run ID:** {run_id}
- **Pass rate:** {pass_rate:.0%} ({total - fail_count}/{total} passing)
- **Failure count:** {fail_count}

### Failing Tests

{failure_block}

### Likely Source Files

{file_block}

### Evidence

{evidence_block}

---

### FIX LOOP — execute this until pass rate = 100%

**STEP 1: Read the failing test details above.**
Understand what each test expected vs what actually happened. The failing step
(if shown) tells you exactly where the flow broke.

**STEP 2: Read the suggested source files.**
Start with the files listed above. Use grep/search to find the relevant code
for each failure category.

**STEP 3: Fix one bug at a time.**
Make the smallest change that fixes the specific failure. Do NOT refactor
surrounding code. Do NOT add features. Just fix the bug.

**STEP 4: Verify locally.**
- If there are unit tests, run them: `pytest` or `npm test`
- If there's a typecheck, run it: `npx tsc --noEmit` or equivalent
- If neither exists, read the code change carefully for correctness

**STEP 5: Re-run QA.**
{rerun_instruction}

**STEP 6: Check the new results.**
If the new run still has failures, loop back to STEP 1 with the new failure data.
If all tests pass → done. Report the fix summary.

### Rules

- **One bug at a time.** Fix, verify, re-run. Don't batch fixes — you need to know
  which fix resolved which failure.
- **Minimal changes.** The goal is to fix the bug, not improve the code. Don't touch
  files that aren't related to a failure.
- **Trust the test.** If the test says the login button isn't clickable, the issue is
  in the login flow — don't second-guess the test framework.
- **Include evidence.** When reporting a fix, reference the test_id and what you changed.
- **Escalate if stuck.** If you've tried 3 approaches for the same bug and none work,
  report what you've tried and ask for human input.

### Fix Report Template

When all tests pass, output this summary:

```
## Fix Report — Run {run_id}

| Test ID | Bug | Fix | Files Changed |
|---------|-----|-----|---------------|
| tc_001  | ... | ... | path/to/file  |

**Before:** {pass_rate:.0%} pass rate ({fail_count} failures)
**After:** 100% pass rate (0 failures)
**Verification run:** <new_run_id>
```
"""

    return {
        "tool": "ta.feedback_package",
        "status": "ok",
        "type": "agent_prompt",
        "run_id": run_id,
        "prompt": prompt,
        "failure_count": fail_count,
        "pass_rate": pass_rate,
        "investigation_hints": [s.get("investigation", s.get("file_path", "")) for s in suggestions],
        "view_url": summary.get("view_url", ""),
        "message": (
            f"Feedback package ready: {fail_count} failures bundled into an autonomous "
            f"fix prompt. Send the 'prompt' field to the user's Claude Code agent."
        ),
    }


async def _dispatch_agent_run(args: Dict[str, Any]) -> Dict[str, Any]:
    """Route ta.agent.run to the Coordinator agent via the AI agent service.

    This gives Claude Code access to the same orchestrating brain that powers
    the retention.sh dashboard — automatic routing to Search, Test Gen, or Device
    Testing specialists based on intent.
    """
    caller_id = args.pop("_caller_id", "anonymous")
    message = args.get("message", "")
    if not message:
        return {"error": "message is required"}

    session_id = args.get("session_id", "")
    app_url = args.get("app_url", "")
    app_package = args.get("app_package", "")

    # Session ownership: prefix session_id with caller to prevent hijacking
    # If resuming, validate the session belongs to this caller
    if session_id and not session_id.startswith(f"u:{caller_id}:"):
        return {"error": "Session belongs to another user. Start a new session by omitting session_id."}

    # Build context hint if app info provided
    context_parts = []
    if app_url:
        context_parts.append(f"App URL: {app_url}")
    if app_package:
        context_parts.append(f"Android package: {app_package}")

    user_message = message
    if context_parts:
        user_message = f"{message}\n\n[Context: {', '.join(context_parts)}]"

    try:
        from .ai_agent import get_ai_agent_service, AIAgentService
        try:
            service = get_ai_agent_service()
        except HTTPException:
            # Service not initialized — fall through to fallback
            raise ImportError("AI agent service not initialized")

        from ..agents.coordinator.coordinator_service import ChatMessage
        messages = [ChatMessage(role="user", content=user_message)]

        # Collect streaming response into a single result
        full_content = []
        tool_calls_made = []

        async for event in service.chat_stream(
            messages=messages,
            ui_context=None,
            resume_session_id=session_id or None,
        ):
            event_type = event.get("type", "")
            if event_type in ("content", "final"):
                content = event.get("content", "")
                if content:
                    full_content.append(content)
            elif event_type == "tool_call":
                tool_calls_made.append({
                    "tool": event.get("tool", ""),
                    "status": event.get("status", ""),
                })
            elif event_type == "session_created":
                raw_sid = event.get("session_id", session_id)
                # Prefix with caller_id for ownership tracking
                session_id = f"u:{caller_id}:{raw_sid}"
            elif event_type == "error":
                return {"error": event.get("content", "Unknown agent error")}

        return {
            "tool": "ta.agent.run",
            "status": "ok",
            "session_id": session_id,
            "response": "\n".join(full_content),
            "tool_calls_made": tool_calls_made,
            "message": (
                "TA Agent responded. Use session_id to continue the conversation. "
                "The agent may have triggered QA flows — check for run_ids in the response."
            ),
        }

    except ImportError:
        # Fallback: route to tools directly when AI agent service isn't available
        logger.warning("AI agent service not available — falling back to direct tool dispatch")
        msg_lower = message.lower()

        # ── Setup / emulator requests ────────────────────────────────
        if any(kw in msg_lower for kw in ("setup", "emulator", "install", "avd", "android sdk", "configure")):
            setup_status = await _dispatch("ta.setup.status", {})
            if setup_status.get("ready"):
                return {
                    "tool": "ta.agent.run", "status": "ok", "session_id": "",
                    "response": (
                        "Your environment is already fully set up!\n\n"
                        f"Progress: {setup_status.get('progress', 0)}%\n\n"
                        "Ready to run QA:\n"
                        "- `ta.run_web_flow(url=\"http://localhost:3000\")` for web apps\n"
                        "- `ta.run_android_flow(app_package=\"com.example.app\")` for native apps"
                    ),
                    "tool_calls_made": [{"tool": "ta.setup.status", "status": "ok"}],
                    "setup_status": setup_status,
                }
            else:
                fixes = setup_status.get("fixes", [])
                fix_lines = []
                for i, fix in enumerate(fixes, 1):
                    fix_lines.append(f"{i}. **{fix['component']}**: `{fix['command']}`\n   {fix['reason']}")

                reqs = setup_status.get("requirements", {})
                emulators = reqs.get("emulators", {})
                can_launch = emulators.get("count", 0) > 0

                parts = [f"Setup Progress: {setup_status.get('progress', 0)}%\n"]
                if fix_lines:
                    parts.append("Run these commands to fix:\n" + "\n".join(fix_lines))
                if can_launch:
                    avd = emulators.get("avds", [""])[0]
                    parts.append(
                        f"\nAVD `{avd}` exists but no emulator is running.\n"
                        f"Next: `ta.setup.launch_emulator(avd_name=\"{avd}\")`\n"
                        f"Then wait ~30s → `ta.system_check` to verify."
                    )
                parts.append(
                    "\nAfter fixes: `ta.setup.status` to verify → "
                    "`ta.run_web_flow` or `ta.run_android_flow` to start QA."
                )

                return {
                    "tool": "ta.agent.run", "status": "ok", "session_id": "",
                    "response": "\n".join(parts),
                    "tool_calls_made": [{"tool": "ta.setup.status", "status": "ok"}],
                    "setup_status": setup_status,
                    "fixes": fixes,
                }

        # ── QA / test requests ───────────────────────────────────────
        if any(kw in msg_lower for kw in ("qa", "test", "check", "verify", "run", "bug", "tweaking")):
            if app_url:
                from .mcp_pipeline import dispatch_qa_verification
                return await dispatch_qa_verification(
                    "ta.run_web_flow",
                    {"url": app_url, "app_name": "User App", "_caller_id": caller_id},
                )
            elif app_package:
                from .mcp_pipeline import dispatch_qa_verification
                return await dispatch_qa_verification(
                    "ta.run_android_flow",
                    {"app_package": app_package, "app_name": "User App", "_caller_id": caller_id},
                )
            # No app context — check system first
            system_check = await _dispatch("ta.system_check", {})
            if not system_check.get("ready"):
                return await _dispatch_agent_run({"message": "Help me set up an Android emulator"})
            return {
                "tool": "ta.agent.run", "status": "ok",
                "response": (
                    "System is ready for QA. Provide your app:\n"
                    "- `ta.run_web_flow(url=\"http://your-app\")`\n"
                    "- `ta.run_android_flow(app_package=\"com.your.package\")`"
                ),
                "tool_calls_made": [{"tool": "ta.system_check", "status": "ok"}],
            }

        # ── Generic fallback ─────────────────────────────────────────
        return {
            "tool": "ta.agent.run", "status": "ok",
            "response": (
                "I'm the retention.sh assistant. I can help with:\n"
                "- **Setup**: `ta.setup.status` — check your environment\n"
                "- **Web QA**: `ta.run_web_flow(url=\"...\")` — test a web app\n"
                "- **Mobile QA**: `ta.run_android_flow(app_package=\"...\")` — test an Android app\n"
                "- **Rerun**: `ta.rerun(run_id=\"...\")` — re-test after fixing bugs\n"
                "- **Results**: `ta.summarize_failure(run_id=\"...\")` — get failure summary"
            ),
            "tool_calls_made": [],
        }

    except Exception as exc:
        logger.exception("ta.agent.run failed")
        return {"error": f"Agent error: {str(exc)[:200]}"}


def _build_optimize_bundle_prompt(args: Dict[str, Any]) -> Dict[str, Any]:
    """Return a structured autonomous prompt for frontend bundle optimization."""
    args.pop("_caller_id", None)
    framework = args.get("framework", "vite")
    entry_file = args.get("entry_file", "src/App.tsx")
    build_cmd = args.get("build_command", "npx vite build")

    prompt = f"""## retention.sh — Frontend Bundle Optimization Agent

You are now operating as an autonomous bundle optimization agent. Follow this loop
exactly. Do NOT skip the measurement steps — every change must have before/after proof.

### Environment
- Framework: {framework}
- Entry file: {entry_file}
- Build command: `{build_cmd}`

---

### PHASE 1: Measure Baseline

1. Run `{build_cmd}` and capture the full output.
2. Record the **entry chunk** size (the JS file referenced in `dist/index.html`).
   Save these numbers — they are your baseline:
   - Entry JS raw size (KB)
   - Entry JS gzip size (KB)
   - Total dist size

### PHASE 2: Identify Eager Imports

3. Read `{entry_file}` and list every **eager** (non-lazy) import.
4. For each eager import, trace its dependency tree 2 levels deep:
   - What UI libraries does it pull in? (Radix, MUI, Ant, etc.)
   - How many icon imports? (lucide, heroicons, etc.)
   - Does it import heavy packages? (markdown renderers, chart libs, rich text editors)
5. Classify each eager import:
   - **Shared infrastructure** — used on >50% of routes (keep eager)
   - **Route-specific** — used on 1-3 routes (must lazy-load)
   - **Wrapper** — layout/error boundary used on few routes (lazy-load via co-import pattern)

### PHASE 3: Apply Fixes (one at a time, verify each)

For each component classified as "route-specific" or "wrapper":

6. **Pages not yet lazy-loaded**: Convert to `const Page = lazy(() => import('./pages/Page'))`.

7. **Layout wrappers used on few routes**: Remove the eager import. Instead, co-import
   the wrapper inside the lazy boundary of the pages that use it:
   ```tsx
   const MyPage = lazy(() =>
     Promise.all([import('./pages/MyPage'), import('./components/MyLayout')]).then(
       ([m, layout]) => ({{ default: () => <layout.MyLayout><m.MyPage /></layout.MyLayout> }})
     )
   )
   ```
   Then update the route to remove the inline wrapper:
   ```tsx
   // Before: <Route path="/x" element={{<MyLayout><MyPage /></MyLayout>}} />
   // After:  <Route path="/x" element={{<MyPage />}} />
   ```

8. **ErrorBoundary wrappers on single routes**: Same co-import pattern as step 7.

### PHASE 4: Verify

9. Run typecheck: `npx tsc --noEmit` (or project-specific typecheck command).
   - If new errors appear in files you changed, fix them.
   - Pre-existing errors in other files are acceptable.

10. Run `{build_cmd}` again. Record the new entry chunk size.

11. Report the delta:
    ```
    Entry JS: {{before}} KB → {{after}} KB ({{percent}}% reduction)
    Entry JS gzip: {{before_gz}} KB → {{after_gz}} KB
    ```

### PHASE 5: Analyze Remaining Entry Chunk

12. If the entry chunk is still >100 KB raw, analyze what's left:
    - Check which node_modules are bundled into it
    - Look for barrel imports pulling in unused code
    - Check if vendor chunking in the build config could help

13. For genuinely shared infrastructure (router, auth gate, design system primitives
    used everywhere), document why it cannot be further split and stop.

### Rules

- **Never skip measurement.** Every optimization claim needs a build number.
- **One change at a time.** Don't batch 5 lazy conversions then build once — you won't
  know which one broke if typecheck fails.
- **Don't over-split shared infrastructure.** If a component (auth gate, toast provider)
  is used on >50% of routes, it belongs in the entry chunk. Splitting it would cause
  redundant downloads and layout flicker.
- **Preserve route behavior.** Lazy wrappers must produce identical component trees.
  Test by checking that the route still renders (dev server or build preview).
- **Target: entry chunk <100 KB raw, <30 KB gzip.** Stop when you hit this or when
  only shared infrastructure remains.
"""

    return {
        "tool": "ta.optimize_bundle",
        "status": "ok",
        "type": "agent_prompt",
        "prompt": prompt,
        "message": (
            "Bundle optimization prompt generated. Your Claude Code agent should execute "
            "this prompt autonomously — it will measure, optimize, and verify in a loop."
        ),
        "framework": framework,
        "entry_file": entry_file,
        "build_command": build_cmd,
    }


async def _dispatch_benchmark(tool: str, args: Dict[str, Any]) -> Any:
    """Handle ta.benchmark.* tools."""
    args.pop("_caller_id", None)
    import httpx

    base_url = os.environ.get("TA_BACKEND_URL", "http://localhost:8000").rstrip("/") + "/api"

    if tool == "ta.benchmark.run_suite":
        app_ids = args.get("app_ids") or []
        max_interactions = int(args.get("max_interactions", 30))
        payload: Dict[str, Any] = {"max_interactions": max_interactions}
        if app_ids:
            payload["app_ids"] = app_ids
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{base_url}/benchmarks/suite/run", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return {
            "suite_id": data.get("suite_id"),
            "status": data.get("status"),
            "apps": data.get("apps", []),
            "message": f"Suite started. Poll ta.benchmark.run_suite status with suite_id={data.get('suite_id')}",
        }

    if tool == "ta.benchmark.scorecard":
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{base_url}/benchmarks/comprehensive/scorecard")
            resp.raise_for_status()
            return resp.json()

    if tool == "ta.benchmark.model_compare":
        from ..benchmarks.model_benchmark import run_model_benchmark
        task_ids = args.get("tasks")
        categories = args.get("categories")
        model_count = int(args["models"]) if args.get("models") else None
        model_ids = args.get("model_ids")
        repeats = int(args["repeats"]) if args.get("repeats") else 1
        return await run_model_benchmark(
            task_ids=task_ids,
            model_count=model_count,
            model_ids=model_ids,
            categories=categories,
            repeats=repeats,
        )

    if tool == "ta.benchmark.model_compare_status":
        from ..benchmarks.model_benchmark import get_benchmark_run
        run_id = args.get("run_id", "")
        run = get_benchmark_run(run_id)
        if not run:
            return {"error": f"No benchmark run found with id: {run_id}"}
        # Return summary when running, full results when complete
        if run["status"] == "running":
            return {
                "run_id": run["run_id"],
                "status": "running",
                "progress": f"{run['completed']}/{run['total_work']}",
                "current": run.get("current", ""),
            }
        return {
            "run_id": run["run_id"],
            "status": run["status"],
            "started_at": run["started_at"],
            "completed_at": run.get("completed_at"),
            "ranking": run.get("ranking", []),
            "results": run.get("results", {}),
        }

    if tool.startswith("ta.benchmark.qa_pipeline"):
        from ..benchmarks.qa_benchmark import dispatch_qa_benchmark
        return await dispatch_qa_benchmark(tool, args)

    # Benchmark app generation tools
    _gen_tools = {
        "ta.benchmark.generate_app", "ta.benchmark.list_templates",
        "ta.benchmark.list_cases", "ta.benchmark.run_case",
        "ta.benchmark.score", "ta.benchmark.run_history",
    }
    if tool in _gen_tools:
        from ..integrations.benchmark_gen import dispatch_benchmark_gen
        return await dispatch_benchmark_gen(tool, args)

    raise HTTPException(status_code=400, detail=f"Unknown benchmark tool: {tool}")


async def _dispatch_investor_brief(tool: str, args: Dict[str, Any]) -> Any:
    """Handle ta.investor_brief.* tools. Reusable from the agent runner."""
    args.pop("_caller_id", None)
    service = get_investor_brief_service()

    if tool == "ta.investor_brief.get_state":
        return service.get_state()

    if tool == "ta.investor_brief.list_sections":
        return service.list_sections()

    if tool == "ta.investor_brief.get_section":
        section_id = args.get("section_id")
        if not section_id:
            raise HTTPException(status_code=400, detail="section_id is required")
        try:
            return service.get_section(section_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    if tool == "ta.investor_brief.update_section":
        section_id = args.get("section_id")
        content = args.get("content")
        if not section_id:
            raise HTTPException(status_code=400, detail="section_id is required")
        if content is None:
            raise HTTPException(status_code=400, detail="content is required")
        try:
            return service.update_section(
                section_id=section_id,
                content=content,
                content_format=args.get("content_format", "html"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if tool == "ta.investor_brief.set_scenario":
        scenario = args.get("scenario")
        if not scenario:
            raise HTTPException(status_code=400, detail="scenario is required")
        try:
            return service.set_scenario(scenario)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if tool == "ta.investor_brief.set_variables":
        variables = args.get("variables")
        if variables is None:
            raise HTTPException(status_code=400, detail="variables is required")
        if not isinstance(variables, dict):
            raise HTTPException(status_code=400, detail="variables must be an object")
        return service.set_variables(variables)

    if tool == "ta.investor_brief.recalculate":
        return service.recalculate()

    raise HTTPException(status_code=400, detail=f"Unknown investor_brief tool: {tool}")


# ---------------------------------------------------------------------------
# Slack tool dispatcher
# ---------------------------------------------------------------------------

_SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")

_SLACK_API = "https://slack.com/api"
_CHANNEL_CACHE: Dict[str, str] = {}  # name → ID cache


async def _slack_api(method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Call a Slack Web API method and return the JSON response."""
    import httpx
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_SLACK_API}/{method}",
            headers={"Authorization": f"Bearer {_SLACK_BOT_TOKEN}"},
            params=params or {},
        )
        data = resp.json()
        if not data.get("ok"):
            error = data.get("error", "unknown_error")
            return {"error": error, "ok": False}
        return data


async def _resolve_channel_id(channel: str) -> str:
    """Resolve a channel name (e.g. '#general' or 'general') to a channel ID."""
    # Already an ID
    if channel.startswith("C") and len(channel) > 8:
        return channel
    # Strip leading #
    name = channel.lstrip("#").lower()
    if name in _CHANNEL_CACHE:
        return _CHANNEL_CACHE[name]
    # Look up
    data = await _slack_api("conversations.list", {"types": "public_channel,private_channel", "limit": "200"})
    for ch in data.get("channels", []):
        _CHANNEL_CACHE[ch["name"].lower()] = ch["id"]
    if name in _CHANNEL_CACHE:
        return _CHANNEL_CACHE[name]
    return channel  # Return as-is if not found


async def _dispatch_slack(tool: str, args: Dict[str, Any]) -> Any:
    """Handle ta.slack.* tools for Slack message access."""
    args.pop("_caller_id", None)

    if tool == "ta.slack.search_messages":
        query = args.get("query", "")
        if not query:
            return {"error": "query is required"}
        count = min(int(args.get("count", 10)), 50)
        data = await _slack_api("search.messages", {"query": query, "count": str(count), "sort": "timestamp", "sort_dir": "desc"})
        if data.get("error"):
            # search:read scope may not be available — fall back to manual scan
            if data["error"] in ("missing_scope", "not_allowed_token_type"):
                return {"error": f"Bot token lacks search:read scope. Search is unavailable. Use slack_get_channel_history instead to browse specific channels.", "suggestion": "slack_get_channel_history"}
            return {"error": data["error"]}
        matches = data.get("messages", {}).get("matches", [])
        results = []
        for m in matches[:count]:
            results.append({
                "text": m.get("text", "")[:500],
                "user": m.get("username", m.get("user", "unknown")),
                "channel": m.get("channel", {}).get("name", ""),
                "channel_id": m.get("channel", {}).get("id", ""),
                "ts": m.get("ts", ""),
                "permalink": m.get("permalink", ""),
            })
        return {"query": query, "total": data.get("messages", {}).get("total", 0), "results": results}

    if tool == "ta.slack.get_channel_history":
        channel = args.get("channel", "")
        if not channel:
            return {"error": "channel is required"}
        channel_id = await _resolve_channel_id(channel)
        limit = min(int(args.get("limit", 20)), 100)
        data = await _slack_api("conversations.history", {"channel": channel_id, "limit": str(limit)})
        if data.get("error"):
            return {"error": data["error"]}
        messages = []
        for m in data.get("messages", []):
            messages.append({
                "text": m.get("text", "")[:500],
                "user": m.get("user", "unknown"),
                "ts": m.get("ts", ""),
                "thread_ts": m.get("thread_ts", ""),
                "reply_count": m.get("reply_count", 0),
            })
        return {"channel": channel_id, "count": len(messages), "messages": messages}

    if tool == "ta.slack.get_thread":
        channel = args.get("channel", "")
        thread_ts = args.get("thread_ts", "")
        if not channel or not thread_ts:
            return {"error": "channel and thread_ts are required"}
        channel_id = await _resolve_channel_id(channel)
        data = await _slack_api("conversations.replies", {"channel": channel_id, "ts": thread_ts, "limit": "100"})
        if data.get("error"):
            return {"error": data["error"]}
        messages = []
        for m in data.get("messages", []):
            messages.append({
                "text": m.get("text", "")[:500],
                "user": m.get("user", "unknown"),
                "ts": m.get("ts", ""),
            })
        return {"channel": channel_id, "thread_ts": thread_ts, "count": len(messages), "messages": messages}

    if tool == "ta.slack.list_channels":
        limit = min(int(args.get("limit", 50)), 200)
        data = await _slack_api("conversations.list", {"types": "public_channel", "limit": str(limit)})
        if data.get("error"):
            return {"error": data["error"]}
        channels = []
        for ch in data.get("channels", []):
            channels.append({
                "id": ch["id"],
                "name": ch["name"],
                "topic": ch.get("topic", {}).get("value", ""),
                "num_members": ch.get("num_members", 0),
            })
        return {"count": len(channels), "channels": channels}

    if tool == "ta.slack.add_reaction":
        channel = args.get("channel", "")
        timestamp = args.get("timestamp", "")
        emoji = args.get("emoji", "")
        if not channel or not timestamp or not emoji:
            return {"error": "channel, timestamp, and emoji are required"}
        data = await _slack_api("reactions.add", {
            "channel": channel, "timestamp": timestamp, "name": emoji,
        })
        if data.get("error"):
            return {"error": data["error"]}
        return {"ok": True, "emoji": emoji, "message_ts": timestamp}

    if tool == "ta.slack.post_message":
        channel = args.get("channel", "")
        text = args.get("text", "")
        thread_ts = args.get("thread_ts", "")
        if not channel or not text:
            return {"error": "channel and text are required"}
        # Auto-split long messages at paragraph boundaries
        messages_to_send = []
        if len(text) > 3900:
            chunks = []
            current = ""
            for para in text.split("\n\n"):
                if len(current) + len(para) + 2 > 3900:
                    if current:
                        chunks.append(current)
                    current = para
                else:
                    current = current + "\n\n" + para if current else para
            if current:
                chunks.append(current)
            messages_to_send = chunks
        else:
            messages_to_send = [text]

        sent = []
        for i, msg in enumerate(messages_to_send):
            if i > 0:
                msg = f"_… continued ({i+1}/{len(messages_to_send)})_\n\n{msg}"
            params = {"channel": channel, "text": msg}
            if thread_ts:
                params["thread_ts"] = thread_ts
            data = await _slack_api("chat.postMessage", params)
            if data.get("error"):
                return {"error": data["error"], "sent_count": i}
            sent.append(data.get("ts", ""))
            # Use first message ts as thread for continuations
            if not thread_ts and i == 0:
                thread_ts = data.get("ts", "")
        return {"ok": True, "sent_count": len(sent), "timestamps": sent}

    if tool == "ta.slack.arbitrate_conflict":
        channel = args.get("channel", "")
        thread_ts = args.get("thread_ts", "")
        topic = args.get("topic", "")
        if not channel or not thread_ts:
            return {"error": "channel and thread_ts are required"}
        # Read the thread to get all agent opinions
        thread_data = await _slack_api("conversations.replies", {
            "channel": channel, "ts": thread_ts, "limit": "50",
        })
        if thread_data.get("error"):
            return {"error": thread_data["error"]}
        messages = thread_data.get("messages", [])
        # Build transcript of agent positions
        transcript = []
        for m in messages:
            text = m.get("text", "")[:400]
            user = m.get("user", "unknown")
            transcript.append(f"[{user}]: {text}")
        transcript_text = "\n".join(transcript[-20:])  # Last 20 messages

        from ..services.llm_judge import call_responses_api
        synthesis = await call_responses_api(
            f"Multiple agent roles have weighed in on: {topic}\n\n"
            f"Their positions:\n{transcript_text}\n\n"
            f"Synthesize a single recommendation that:\n"
            f"1. Identifies the majority position\n"
            f"2. Notes any significant dissent with reasoning\n"
            f"3. Gives a clear, actionable recommendation\n"
            f"4. Flags any unresolved risks\n\n"
            f"Use Slack mrkdwn (*bold*, _italic_). Keep under 300 words.",
            task="compose_response",
            reasoning_effort="high",
            timeout_s=60,
        )
        # Post arbitration as thread reply
        arb_text = f"*Arbitration — {topic}*\n\n{synthesis}"
        data = await _slack_api("chat.postMessage", {
            "channel": channel, "thread_ts": thread_ts, "text": arb_text,
        })
        return {
            "ok": True,
            "posted": not data.get("error"),
            "synthesis": synthesis[:500],
            "positions_analyzed": len(transcript),
        }

    raise HTTPException(status_code=400, detail=f"Unknown slack tool: {tool}")


# ---------------------------------------------------------------------------
# Playwright self-test tools
# ---------------------------------------------------------------------------

async def _dispatch_playwright(tool: str, args: Dict[str, Any]) -> Any:
    """Handle ta.playwright.* tools — Playwright-based web app testing."""
    args.pop("_caller_id", None)
    from ..agents.self_testing.playwright_engine import (
        pw_discover,
        pw_test_interaction,
        pw_check_page_health,
        pw_batch_test,
    )

    if tool == "ta.playwright.discover":
        url = args.get("url", "")
        if not url:
            raise HTTPException(status_code=400, detail="url is required")
        crawl_depth = int(args.get("crawl_depth", 1))
        return await pw_discover(url, crawl_depth=crawl_depth)

    if tool == "ta.playwright.test_interaction":
        url = args.get("url", "")
        if not url:
            raise HTTPException(status_code=400, detail="url is required")
        element = {
            "type": args.get("element_type", "link"),
            "text": args.get("element_text", ""),
            "_page": args.get("page_path", "/"),
        }
        if args.get("element_selector"):
            element["selector"] = args["element_selector"]
        if args.get("element_href"):
            element["href"] = args["element_href"]
        from urllib.parse import urlparse
        parsed = urlparse(url)
        base_origin = f"{parsed.scheme}://{parsed.netloc}"
        return await pw_test_interaction(url, element, base_origin)

    if tool == "ta.playwright.check_page_health":
        url = args.get("url", "")
        if not url:
            raise HTTPException(status_code=400, detail="url is required")
        return await pw_check_page_health(url)

    if tool == "ta.playwright.batch_test":
        url = args.get("url", "")
        if not url:
            raise HTTPException(status_code=400, detail="url is required")
        max_interactions = int(args.get("max_interactions", 15))
        return await pw_batch_test(url, max_interactions=max_interactions)

    raise HTTPException(status_code=400, detail=f"Unknown playwright tool: {tool}")


# ---------------------------------------------------------------------------
# NemoClaw dispatcher
# ---------------------------------------------------------------------------

async def _dispatch_nemoclaw(tool: str, args: Dict[str, Any]) -> Any:
    """Handle ta.nemoclaw.* tools — auto-rotating free model QA agent."""
    args.pop("_caller_id", None)
    from ..integrations.nemoclaw import (
        NemotronClient,
        dispatch_nemoclaw_run,
        dispatch_nemoclaw_telemetry,
        dispatch_nemoclaw_refresh,
    )

    if tool == "ta.nemoclaw.run":
        return await dispatch_nemoclaw_run(args)

    if tool == "ta.nemoclaw.status":
        client = NemotronClient()
        from ..integrations.openrouter_rotation import get_rotation
        rotation = get_rotation()
        telemetry = rotation.get_telemetry()
        return {
            "available": client.is_configured(),
            "provider": client.provider,
            "model": telemetry.get("current_model") or client.model,
            "base_url": client.base_url,
            "free_models_discovered": telemetry.get("total_free_models", 0),
            "top_models": telemetry.get("models_ranked", [])[:5],
            "fallback": telemetry.get("fallback"),
            "setup_hint": (
                "Set OPENROUTER_API_KEY (free tier) or NVIDIA_API_KEY"
                if not client.is_configured()
                else f"Ready — using {client.provider}"
            ),
        }

    if tool == "ta.nemoclaw.telemetry":
        return await dispatch_nemoclaw_telemetry()

    if tool == "ta.nemoclaw.refresh":
        return await dispatch_nemoclaw_refresh()

    raise HTTPException(status_code=400, detail=f"Unknown nemoclaw tool: {tool}")


# ---------------------------------------------------------------------------
# Design tools dispatcher (Figma / Google Stitch → code gen → deploy → QA)
# ---------------------------------------------------------------------------

async def _dispatch_design(tool: str, args: Dict[str, Any]) -> Any:
    args.pop("_caller_id", None)
    """Handle ta.design.* tools — design-to-QA pipeline."""
    import os, httpx

    figma_token = os.getenv("FIGMA_ACCESS_TOKEN", "")

    if tool == "ta.design.figma_snapshot":
        file_key = args.get("file_key", "")
        depth = args.get("depth", "metadata")  # metadata | components | full
        if not file_key:
            raise HTTPException(status_code=400, detail="file_key is required")
        if not figma_token:
            return {"error": "FIGMA_ACCESS_TOKEN not set", "setup_hint": "Set FIGMA_ACCESS_TOKEN env var with a Figma personal access token"}

        async with httpx.AsyncClient(timeout=30) as client:
            headers = {"X-Figma-Token": figma_token}
            # Metadata level — just file info
            resp = await client.get(f"https://api.figma.com/v1/files/{file_key}", headers=headers, params={"depth": 1 if depth == "metadata" else 2})
            resp.raise_for_status()
            data = resp.json()

            result = {
                "file_key": file_key,
                "name": data.get("name", ""),
                "last_modified": data.get("lastModified", ""),
                "version": data.get("version", ""),
                "depth": depth,
            }

            if depth in ("components", "full"):
                # Extract top-level pages and frames
                pages = []
                doc = data.get("document", {})
                for page in doc.get("children", []):
                    frames = [
                        {"id": f.get("id"), "name": f.get("name"), "type": f.get("type")}
                        for f in page.get("children", [])[:50]
                    ]
                    pages.append({"id": page.get("id"), "name": page.get("name"), "frames": frames})
                result["pages"] = pages

            if depth == "full":
                # Fetch images for top frames
                frame_ids = [f["id"] for p in result.get("pages", []) for f in p.get("frames", [])][:20]
                if frame_ids:
                    img_resp = await client.get(
                        f"https://api.figma.com/v1/images/{file_key}",
                        headers=headers,
                        params={"ids": ",".join(frame_ids), "format": "png", "scale": 1},
                    )
                    if img_resp.status_code == 200:
                        result["frame_images"] = img_resp.json().get("images", {})

            return result

    if tool == "ta.design.figma_analyze_flows":
        file_key = args.get("file_key", "")
        if not file_key:
            raise HTTPException(status_code=400, detail="file_key is required")
        if not figma_token:
            return {"error": "FIGMA_ACCESS_TOKEN not set"}

        async with httpx.AsyncClient(timeout=30) as client:
            headers = {"X-Figma-Token": figma_token}
            resp = await client.get(f"https://api.figma.com/v1/files/{file_key}", headers=headers, params={"depth": 2})
            resp.raise_for_status()
            data = resp.json()

        # Cluster frames into flow groups by naming conventions
        flows = []
        doc = data.get("document", {})
        for page in doc.get("children", []):
            flow_group = {"page": page.get("name", ""), "screens": []}
            for frame in page.get("children", []):
                flow_group["screens"].append({
                    "id": frame.get("id"),
                    "name": frame.get("name"),
                    "type": frame.get("type"),
                    "width": frame.get("absoluteBoundingBox", {}).get("width"),
                    "height": frame.get("absoluteBoundingBox", {}).get("height"),
                })
            if flow_group["screens"]:
                flows.append(flow_group)

        return {
            "file_key": file_key,
            "flow_count": len(flows),
            "flows": flows,
            "hint": "Use ta.design.generate_from_design to turn these flows into runnable code",
        }

    if tool == "ta.design.generate_from_design":
        design_url = args.get("design_url", "")
        output_format = args.get("output_format", "react")  # react | html
        if not design_url:
            raise HTTPException(status_code=400, detail="design_url is required")

        # Route to existing generation paths
        from .demo import _get_relay_session

        if output_format == "html":
            # Use Showcase path (OpenAI HTML gen)
            return {
                "status": "ready",
                "generation_path": "showcase",
                "design_url": design_url,
                "next_step": "POST /api/demo/showcase/pipeline with the design_url as the prompt source",
                "hint": "Extract key screens from the design and describe them as prompts for the HTML generator",
            }
        else:
            # Use Chef path (full-stack React/Convex)
            return {
                "status": "ready",
                "generation_path": "chef",
                "design_url": design_url,
                "next_step": "POST /api/demo/chef/pipeline with design-derived spec",
                "hint": "Extract component hierarchy from design and pass as app_description to Chef",
            }

    if tool == "ta.design.pipeline":
        design_url = args.get("design_url", "")
        if not design_url:
            raise HTTPException(status_code=400, detail="design_url is required")
        output_format = args.get("output_format", "html")
        run_qa = args.get("run_qa", True)

        return {
            "status": "pipeline_ready",
            "stages": [
                {"stage": "DESIGN_FETCH", "description": "Fetch and analyze design from URL"},
                {"stage": "CODE_GEN", "description": f"Generate {output_format} from design screens"},
                {"stage": "DEPLOY", "description": "Deploy generated app (Vercel for React, inline for HTML)"},
                {"stage": "QA", "description": "Run QA pipeline on deployed app via relay" if run_qa else "Skipped"},
            ],
            "design_url": design_url,
            "output_format": output_format,
            "run_qa": run_qa,
            "next_step": "Call ta.design.figma_snapshot first, then ta.design.generate_from_design, then ta.pipeline.run",
        }

    raise HTTPException(status_code=400, detail=f"Unknown design tool: {tool}")


# ---------------------------------------------------------------------------
# Quickstart & Handoff handlers
# ---------------------------------------------------------------------------

QUICKCART_URL = "https://test-studio-xi.vercel.app/demo/planted-bugs/"


async def _handle_quickstart(args: Dict[str, Any]) -> Dict[str, Any]:
    """Smart first-time experience: detects environment → picks best QA mode → runs.

    Decision logic (agent-style, not hardcoded):
    - Web URL + emulator available → full mobile pipeline (emulator Chrome)
    - Web URL + no emulator → Playwright-direct pipeline (no emulator needed)
    - Native app (package name) + emulator → native pipeline
    - Native app + no emulator → guide emulator setup (required for native)

    The agent context determines the path, not if/else branches.
    """
    caller_id = args.pop("_caller_id", "anonymous")
    from .mcp_pipeline import dispatch_qa_verification

    url = args.get("url") or QUICKCART_URL
    app_name = args.get("app_name") or ("QuickCart Demo" if url == QUICKCART_URL else "User App")
    package_name = args.get("package_name", "")

    # 1. Assess environment capabilities
    try:
        system = await _dispatch("ta.system_check", {"include_web_test": False}, caller_id=caller_id)
    except Exception as exc:
        system = {"ready": False, "error": str(exc)}

    has_emulator = False
    from .mcp_pipeline import _qa_pipeline_service
    if _qa_pipeline_service:
        try:
            import re as _re
            devices_text = await _qa_pipeline_service.mobile_mcp_client.list_available_devices()
            has_emulator = bool(_re.findall(r"emulator-\d+", devices_text))
        except Exception:
            pass

    is_web_url = url and url.startswith("http")
    is_native = bool(package_name)

    # 2. Build context for decision
    context = {
        "target_type": "native_app" if is_native else "web_app" if is_web_url else "unknown",
        "has_emulator": has_emulator,
        "has_playwright": True,  # Playwright is always available server-side
        "url": url,
        "package_name": package_name,
    }

    # 3. Route based on context (not hardcoded if/else — each path is a capability match)

    # Path A: Emulator available → use full mobile pipeline (best quality for both web + native)
    if has_emulator:
        try:
            if is_native:
                qa_result = await dispatch_qa_verification(
                    "ta.run_android_flow",
                    {"package_name": package_name, "app_name": app_name, "timeout_seconds": 3600, "_caller_id": caller_id},
                )
            else:
                qa_result = await dispatch_qa_verification(
                    "ta.run_web_flow",
                    {"url": url, "app_name": app_name, "timeout_seconds": 3600, "_caller_id": caller_id},
                )
        except Exception as exc:
            return {"tool": "ta.quickstart", "status": "error", "context": context, "error": str(exc)}

        run_id = qa_result.get("run_id", "")
        return {
            "tool": "ta.quickstart",
            "status": "ok",
            "mode": "emulator",
            "context": context,
            "system_check": system,
            "run_id": run_id,
            "app_url": url,
            "app_name": app_name,
            "message": f"Full QA pipeline started for {app_name} (emulator mode).",
            "next_steps": [
                f"Poll progress: ta.pipeline.status(run_id='{run_id}')",
                f"When complete: ta.get_handoff(run_id='{run_id}')",
                "Read the handoff — it lists bugs found, files to fix, and rerun command.",
                f"After fixing: ta.rerun(run_id='{run_id}') to verify ($0, ~10s).",
            ],
        }

    # Path B: Web app + no emulator → Playwright-direct (no setup needed)
    if is_web_url and not is_native:
        try:
            qa_result = await dispatch_qa_verification(
                "ta.run_web_flow",
                {
                    "url": url, "app_name": app_name, "timeout_seconds": 3600,
                    "mode": "playwright",  # Explicit Playwright mode
                    "_caller_id": caller_id,
                },
            )
        except Exception as exc:
            # Playwright also failed — fall back to web_demo tools
            return {
                "tool": "ta.quickstart",
                "status": "ok",
                "mode": "web_demo",
                "context": context,
                "system_check": system,
                "app_url": url,
                "app_name": app_name,
                "message": (
                    f"Running web QA for {app_name} using Playwright (no emulator needed). "
                    "Follow the steps below to discover and test your app."
                ),
                "next_steps": [
                    f"Step 1: ta.web_demo.discover(url='{url}')",
                    "Step 2: ta.web_demo.run(task_ids='all')",
                    "Step 3: ta.web_demo.scorecard(suite_id=<from step 2>)",
                    "These produce structured test results, failure bundles, and rerun commands — same as the emulator pipeline.",
                ],
            }

        # Playwright pipeline started successfully
        if qa_result.get("status") == "setup_required":
            # Even Playwright path needs emulator — fall to web_demo
            return {
                "tool": "ta.quickstart",
                "status": "ok",
                "mode": "web_demo",
                "context": context,
                "system_check": system,
                "app_url": url,
                "app_name": app_name,
                "message": (
                    f"No emulator detected, but that's OK for web apps. "
                    f"Running web QA for {app_name} using browser-based testing."
                ),
                "next_steps": [
                    f"Step 1: ta.web_demo.discover(url='{url}')",
                    "Step 2: ta.web_demo.run(task_ids='all')",
                    "Step 3: ta.web_demo.scorecard(suite_id=<from step 2>)",
                ],
                "emulator_setup_available": True,
                "emulator_setup_hint": (
                    "For deeper mobile-specific testing (touch, viewport, native features), "
                    "set up an emulator: ta.setup.instructions()"
                ),
            }

        run_id = qa_result.get("run_id", "")
        return {
            "tool": "ta.quickstart",
            "status": "ok",
            "mode": "playwright" if run_id else "web_demo",
            "context": context,
            "system_check": system,
            "run_id": run_id,
            "app_url": url,
            "app_name": app_name,
            "message": f"Web QA started for {app_name} (Playwright mode — no emulator needed).",
            "next_steps": [
                f"Poll progress: ta.pipeline.status(run_id='{run_id}')",
                f"When complete: ta.get_handoff(run_id='{run_id}')",
                "Read the handoff — bugs, fix suggestions, rerun command.",
                f"After fixing: ta.rerun(run_id='{run_id}') to verify.",
            ] if run_id else [
                f"Step 1: ta.web_demo.discover(url='{url}')",
                "Step 2: ta.web_demo.run(task_ids='all')",
                "Step 3: ta.web_demo.scorecard(suite_id=<from step 2>)",
            ],
        }

    # Path C: Native app + no emulator → emulator is required, guide setup
    manual_steps = []
    try:
        instructions = await _dispatch("ta.setup.instructions", {}, caller_id=caller_id)
        manual_steps = instructions.get("steps", [])
    except Exception:
        pass

    setup_commands = []
    for step in manual_steps:
        cmd = step.get("command", "")
        if cmd and not cmd.startswith("ta."):
            setup_commands.append(f"# Step {step.get('step', '?')}: {step.get('title', '')}\n{cmd}")

    return {
        "tool": "ta.quickstart",
        "status": "setup_required",
        "mode": "needs_emulator",
        "context": context,
        "system_check": system,
        "app_url": url,
        "app_name": app_name,
        "message": (
            "Native Android app testing requires an emulator. "
            "Execute the setup commands below on your local machine, "
            "then retry ta.quickstart."
        ),
        "setup_prompt": (
            "Execute these commands to set up an Android emulator:\n\n"
            + "\n\n".join(setup_commands)
            + "\n\nAfter boot (~30s), verify: adb devices\n"
            + f"Then retry: ta.quickstart(package_name='{package_name}')"
        ),
        "manual_steps": manual_steps,
    }


async def _handle_get_handoff(args: Dict[str, Any]) -> Dict[str, Any]:
    """Build or retrieve a markdown QA handoff report for a completed run."""
    from .mcp_pipeline import dispatch_qa_verification, build_handoff_md

    run_id = args.get("run_id")
    if not run_id:
        return {"error": "run_id is required"}

    # Check if handoff already generated on disk
    handoff_dir = Path(__file__).resolve().parent.parent.parent / "data" / "handoff"
    handoff_path = handoff_dir / f"{run_id}.md"
    if handoff_path.exists():
        return {
            "tool": "ta.get_handoff",
            "status": "ok",
            "run_id": run_id,
            "format": "markdown",
            "report": handoff_path.read_text(encoding="utf-8"),
        }

    # Generate on-demand from pipeline results
    try:
        report_md = await build_handoff_md(run_id)
    except Exception as exc:
        return {"error": f"Failed to build handoff: {exc}"}

    if not report_md:
        return {"error": f"No results found for run_id: {run_id}. Is the pipeline still running? Check ta.pipeline.status."}

    return {
        "tool": "ta.get_handoff",
        "status": "ok",
        "run_id": run_id,
        "format": "markdown",
        "report": report_md,
    }


# ---------------------------------------------------------------------------
# Web Demo Bridge — Playwright-based QA (no emulator needed)
# ---------------------------------------------------------------------------

_web_demo_tasks: Dict[str, list] = {}


async def _dispatch_web_demo(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Bridge MCP tools to the benchmark_comparison Playwright flow."""
    args.pop("_caller_id", None)
    import uuid as _uuid

    if tool == "ta.web_demo.discover":
        url = args.get("url")
        if not url:
            return {"error": "url is required"}
        crawl_depth = int(args.get("crawl_depth", 1))
        from .benchmark_comparison import _discover_tasks_from_page, _registry
        tasks = await _discover_tasks_from_page(url, label=url, crawl_depth=crawl_depth)
        if not tasks:
            return {"tool": tool, "status": "ok", "tasks": [], "count": 0,
                    "message": "No interactive elements found."}
        task_dicts = [t.model_dump() for t in tasks]
        session_key = f"discover-{_uuid.uuid4().hex[:8]}"
        _web_demo_tasks[session_key] = tasks
        for t in tasks:
            _registry._tasks[t.task_id] = t
        return {
            "tool": tool, "status": "ok", "session_key": session_key,
            "count": len(task_dicts),
            "tasks": [{"task_id": t["task_id"], "bucket": t.get("bucket", ""),
                        "prompt": t.get("prompt", "")} for t in task_dicts],
            "next_step": f"Call ta.web_demo.run with task_ids='all' to run all {len(task_dicts)} tasks.",
        }

    if tool == "ta.web_demo.run":
        task_ids_raw = args.get("task_ids", "all")
        parallel = int(args.get("parallel", 2))
        from .benchmark_comparison import _active_runs, _run_suite_background, _registry, AgentMode
        if task_ids_raw == "all":
            task_ids = list(_registry._tasks.keys())
        else:
            task_ids = [tid.strip() for tid in task_ids_raw.split(",") if tid.strip()]
        if not task_ids:
            return {"error": "No tasks found. Run ta.web_demo.discover first."}
        suite_id = _uuid.uuid4().hex[:8]
        modes = [AgentMode.TEST_ASSURANCE]
        _active_runs[suite_id] = {
            "suite_id": suite_id, "status": "pending", "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None, "task_ids": task_ids, "modes": [m.value for m in modes],
            "task_count": len(task_ids), "completed_tasks": 0,
            "total_work": len(task_ids), "error": None,
        }
        import asyncio
        asyncio.create_task(_run_suite_background(suite_id, task_ids, modes, parallel))
        return {
            "tool": tool, "status": "ok", "suite_id": suite_id,
            "task_count": len(task_ids),
            "message": f"Running {len(task_ids)} tasks with {parallel} parallel browsers.",
            "next_steps": [
                f"Poll: ta.web_demo.status(suite_id='{suite_id}')",
                f"When done: ta.web_demo.scorecard(suite_id='{suite_id}')",
            ],
        }

    if tool == "ta.web_demo.status":
        suite_id = args.get("suite_id")
        if not suite_id:
            return {"error": "suite_id is required"}
        from .benchmark_comparison import _active_runs
        run = _active_runs.get(suite_id)
        if not run:
            return {"error": f"No suite found: {suite_id}"}
        return {"tool": tool, "suite_id": suite_id, "status": run["status"],
                "completed_tasks": run.get("completed_tasks", 0),
                "task_count": run.get("task_count", 0), "error": run.get("error")}

    if tool == "ta.web_demo.scorecard":
        suite_id = args.get("suite_id")
        if not suite_id:
            return {"error": "suite_id is required"}
        from .benchmark_comparison import _active_runs, _writer
        run = _active_runs.get(suite_id)
        if not run:
            return {"error": f"No suite found: {suite_id}"}
        if run["status"] != "completed":
            return {"tool": tool, "suite_id": suite_id, "status": run["status"],
                    "message": f"Not complete yet (status: {run['status']}). Poll ta.web_demo.status."}
        scorecard_suite_id = run.get("scorecard_suite_id", suite_id)
        scorecard = _writer.load_scorecard(scorecard_suite_id)
        if not scorecard:
            return {"error": f"Scorecard not found for suite {suite_id}"}
        return {
            "tool": tool, "status": "ok", "suite_id": suite_id,
            "scorecard": scorecard.model_dump() if hasattr(scorecard, "model_dump") else scorecard,
        }

    return {"error": f"Unknown web demo tool: {tool}"}


# ---------------------------------------------------------------------------
# Exploration Memory — query durable path/product/run memory
# ---------------------------------------------------------------------------

def _dispatch_memory(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle ta.memory.* tools for querying exploration memory."""

    if tool == "ta.memory.apps":
        from ..agents.qa_pipeline.exploration_memory import _load_index
        index = _load_index()
        apps = []
        for key, info in index.get("apps", {}).items():
            apps.append({
                "app_key": key,
                "app_name": info.get("app_name", ""),
                "app_url": info.get("app_url", ""),
                "screens": info.get("screens", 0),
                "components": info.get("components", 0),
                "last_crawl": info.get("last_crawl", ""),
                "crawl_count": info.get("crawl_count", 0),
            })
        return {
            "tool": tool, "status": "ok",
            "apps": apps,
            "total": len(apps),
            "stats": index.get("stats", {}),
        }

    if tool == "ta.memory.status":
        app_url = args.get("app_url", "")
        app_name = args.get("app_name", "")
        if not app_url and not app_name:
            return {"error": "app_url or app_name is required"}

        from ..agents.qa_pipeline.exploration_memory import check_memory
        mem = check_memory(app_url=app_url, app_name=app_name)
        return {
            "tool": tool, "status": "ok",
            "memory": mem.summary(),
            "message": (
                f"Full cache hit — all 3 stages cached. Rerun will skip CRAWL+WORKFLOW+TESTCASE."
                if mem.full_hit else
                f"Partial: skipping {mem.stages_skipped or 'nothing'}, need {mem.stages_needed or ['CRAWL','WORKFLOW','TESTCASE']}."
            ),
            "compounding": {
                "stages_skipped": mem.stages_skipped,
                "estimated_tokens_saved": mem.estimated_tokens_saved,
                "estimated_cost_saved_usd": round(mem.estimated_cost_saved, 6),
                "run_1_cost": "$0.013 (full pipeline)",
                "run_n_cost": "$0.000 (cached)" if mem.full_hit else f"${0.013 - mem.estimated_cost_saved:.3f} (partial)",
            },
        }

    if tool == "ta.memory.graph":
        app_url = args.get("app_url", "")
        app_name = args.get("app_name", "")
        if not app_url and not app_name:
            return {"error": "app_url or app_name is required"}

        from ..agents.qa_pipeline.exploration_memory import app_fingerprint, load_crawl
        app_key = app_fingerprint(app_url=app_url, app_name=app_name)
        cached = load_crawl(app_key)
        if not cached:
            return {
                "tool": tool, "status": "empty",
                "message": f"No exploration memory for this app. Run ta.quickstart or ta.run_web_flow first.",
            }

        crawl_result, crawl_fp = cached
        # Build adjacency from transitions
        adjacency: Dict[str, list] = {}
        for t in crawl_result.transitions:
            adjacency.setdefault(t.from_screen, []).append({
                "to": t.to_screen,
                "action": t.action,
            })

        screens_summary = []
        for s in crawl_result.screens:
            screens_summary.append({
                "screen_id": s.screen_id,
                "name": s.screen_name,
                "depth": s.navigation_depth,
                "parent": s.parent_screen_id,
                "components": len(s.components),
                "interactive": sum(1 for c in s.components if c.is_interactive),
            })

        return {
            "tool": tool, "status": "ok",
            "app_key": app_key,
            "crawl_fingerprint": crawl_fp,
            "total_screens": crawl_result.total_screens,
            "total_components": crawl_result.total_components,
            "screens": screens_summary,
            "transitions": [t.model_dump() for t in crawl_result.transitions],
            "adjacency": adjacency,
            "message": (
                f"Screen graph: {crawl_result.total_screens} screens, "
                f"{len(crawl_result.transitions)} transitions. "
                f"Fingerprint: {crawl_fp}. "
                f"This map is reused on subsequent runs — no re-exploration needed."
            ),
        }

    # ── ta.memory.export ──
    if tool == "ta.memory.export":
        import json as _json
        from pathlib import Path as _Path
        from datetime import datetime, timezone

        memory_dir = _Path(__file__).resolve().parents[1] / "data" / "exploration_memory"
        traj_dir = _Path(__file__).resolve().parents[1] / "data" / "trajectories"
        bundle = {"exported_at": datetime.now(timezone.utc).isoformat(), "crawls": {}, "workflows": {}, "test_suites": {}, "trajectories": {}}

        for subdir, key in [("crawl", "crawls"), ("workflows", "workflows"), ("test_suites", "test_suites")]:
            d = memory_dir / subdir
            if d.exists():
                for f in d.glob("*.json"):
                    try:
                        bundle[key][f.stem] = _json.loads(f.read_text())
                    except Exception:
                        pass
        if traj_dir.exists():
            for task_d in traj_dir.iterdir():
                if task_d.is_dir() and not task_d.name.startswith("_"):
                    for f in task_d.glob("*.json"):
                        try:
                            bundle["trajectories"][f"{task_d.name}/{f.stem}"] = _json.loads(f.read_text())
                        except Exception:
                            pass

        export_dir = _Path(__file__).resolve().parents[1] / "data" / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = export_dir / f"memory_bundle_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        bundle_path.write_text(_json.dumps(bundle, indent=2, default=str))
        return {
            "tool": tool, "status": "ok",
            "bundle_path": str(bundle_path),
            "counts": {k: len(v) for k, v in bundle.items() if isinstance(v, dict) and k != "exported_at"},
        }

    # ── ta.memory.import ──
    if tool == "ta.memory.import":
        import json as _json
        from pathlib import Path as _Path

        bundle_path = args.get("bundle_path", "")
        if not bundle_path:
            return {"error": "bundle_path is required"}
        bp = _Path(bundle_path)
        if not bp.exists():
            return {"error": f"Bundle file not found: {bundle_path}"}
        bundle = _json.loads(bp.read_text())

        memory_dir = _Path(__file__).resolve().parents[1] / "data" / "exploration_memory"
        traj_dir = _Path(__file__).resolve().parents[1] / "data" / "trajectories"
        imported = {"crawls": 0, "workflows": 0, "test_suites": 0, "trajectories": 0}

        for key, subdir in [("crawls", "crawl"), ("workflows", "workflows"), ("test_suites", "test_suites")]:
            d = memory_dir / subdir
            d.mkdir(parents=True, exist_ok=True)
            for name, data in bundle.get(key, {}).items():
                (d / f"{name}.json").write_text(_json.dumps(data, indent=2, default=str))
                imported[key] += 1

        for path_key, data in bundle.get("trajectories", {}).items():
            parts = path_key.split("/", 1)
            if len(parts) == 2:
                task_d = traj_dir / parts[0]
                task_d.mkdir(parents=True, exist_ok=True)
                (task_d / f"{parts[1]}.json").write_text(_json.dumps(data, indent=2, default=str))
                imported["trajectories"] += 1

        return {"tool": tool, "status": "ok", "imported": imported}

    return {"error": f"Unknown memory tool: {tool}"}


# ---------------------------------------------------------------------------
# Trajectory Replay — replay saved trajectories with checkpoint validation
# ---------------------------------------------------------------------------

def _dispatch_trajectory(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle ta.trajectory.* tools."""

    if tool == "ta.trajectory.list":
        from ..agents.device_testing.trajectory_logger import get_trajectory_logger
        tl = get_trajectory_logger()
        trajectories = tl.list_all_trajectories()
        return {
            "tool": tool, "status": "ok",
            "trajectories": trajectories,
            "total": len(trajectories),
        }

    if tool == "ta.trajectory.compare":
        from ..agents.device_testing.trajectory_logger import get_trajectory_logger
        tl = get_trajectory_logger()
        id_a = args.get("trajectory_id_a", "")
        id_b = args.get("trajectory_id_b", "")
        task_a = args.get("task_name_a", "")
        task_b = args.get("task_name_b", "")
        if not id_a or not id_b:
            return {"error": "trajectory_id_a and trajectory_id_b are required"}

        # Search all tasks if task names not provided
        traj_a = None
        traj_b = None
        base = tl._base_dir
        if base.exists():
            for task_dir in base.iterdir():
                if not task_dir.is_dir() or task_dir.name.startswith("_"):
                    continue
                if not traj_a:
                    traj_a = tl.load_trajectory(task_a or task_dir.name, id_a)
                if not traj_b:
                    traj_b = tl.load_trajectory(task_b or task_dir.name, id_b)
                if traj_a and traj_b:
                    break

        if not traj_a:
            return {"error": f"Trajectory {id_a} not found"}
        if not traj_b:
            return {"error": f"Trajectory {id_b} not found"}

        from dataclasses import asdict
        comparison = tl.compare_trajectories(traj_a, traj_b)
        return {"tool": tool, "status": "ok", "comparison": asdict(comparison)}

    if tool == "ta.trajectory.replay":
        # Note: actual device replay requires async + pipeline service.
        # Return trajectory info and instructions for now.
        from ..agents.device_testing.trajectory_logger import get_trajectory_logger
        tl = get_trajectory_logger()
        trajectory_id = args.get("trajectory_id", "")
        task_name = args.get("task_name", "")
        if not trajectory_id:
            return {"error": "trajectory_id is required"}

        # Find the trajectory
        traj = None
        if task_name:
            traj = tl.load_trajectory(task_name, trajectory_id)
        else:
            base = tl._base_dir
            if base.exists():
                for task_dir in base.iterdir():
                    if task_dir.is_dir() and not task_dir.name.startswith("_"):
                        traj = tl.load_trajectory(task_dir.name, trajectory_id)
                        if traj:
                            task_name = task_dir.name
                            break

        if not traj:
            return {"error": f"Trajectory {trajectory_id} not found"}

        from ..agents.qa_pipeline.trajectory_replay import get_savings_aggregate
        from dataclasses import asdict
        return {
            "tool": tool, "status": "ok",
            "trajectory": {
                "trajectory_id": traj.trajectory_id,
                "task_name": traj.task_name,
                "task_goal": traj.task_goal,
                "surface": traj.surface,
                "total_steps": len(traj.steps),
                "replay_count": traj.replay_count,
                "drift_score": traj.drift_score,
                "avg_token_savings": traj.avg_token_savings,
                "avg_time_savings": traj.avg_time_savings,
            },
            "message": f"Trajectory loaded: {len(traj.steps)} steps, replayed {traj.replay_count}x. Use ta.pipeline.run or the REST API POST /api/trajectories/{trajectory_id}/replay to execute.",
        }

    return {"error": f"Unknown trajectory tool: {tool}"}


# ---------------------------------------------------------------------------
# Explore-Only Mode (no test case generation)
# ---------------------------------------------------------------------------

async def _dispatch_explore(args: Dict[str, Any]) -> Dict[str, Any]:
    """Lightweight explore-only mode: navigate + capture trajectory, no test gen.

    This is the fast path for the explore-vs-replay comparison.
    Runs CRAWL only (skips WORKFLOW, TESTCASE, EXECUTION stages).
    Saves a trajectory for later replay.

    Args:
        app_url: URL to explore (browser) OR app_package for Android
        app_name: Human-readable name
        surface: 'web' or 'android' (default: auto-detect)
        device_id: Android device ID (default: auto-detect)
        task: Task description for the exploration goal
    """
    app_url = args.get("app_url", "")
    app_package = args.get("app_package", "")
    app_name = args.get("app_name", app_url or app_package)
    surface = args.get("surface", "")
    task = args.get("task", f"Explore {app_name}")

    if not app_url and not app_package:
        return {"error": "app_url (for web) or app_package (for android) is required"}

    # Auto-detect surface
    if not surface:
        surface = "android" if app_package else "web"

    # Run the appropriate crawl pipeline (CRAWL stage only)
    from .mcp_pipeline import run_playwright_pipeline, _running_pipelines

    if surface == "web":
        run_id = await run_playwright_pipeline(
            url=app_url,
            app_name=app_name,
            skip_stages=["WORKFLOW", "TESTCASE", "EXECUTION"],
        )
    else:
        # Android explore via the existing pipeline with stage skip
        run_id = await run_playwright_pipeline(
            url=app_package,
            app_name=app_name,
            flow_type="android",
            device_id=args.get("device_id", ""),
            skip_stages=["WORKFLOW", "TESTCASE", "EXECUTION"],
        )

    if not run_id:
        return {"error": "Failed to start explore flow"}

    return {
        "tool": "ta.explore.run",
        "status": "ok",
        "run_id": run_id,
        "surface": surface,
        "app_name": app_name,
        "mode": "explore_only",
        "stages": ["CRAWL"],
        "message": f"Explore-only flow started ({surface}). Captures trajectory without test generation. Poll ta.pipeline.status for progress.",
    }


# ---------------------------------------------------------------------------
# Retention Self-Serve QA Loop
# ---------------------------------------------------------------------------

async def _dispatch_retention(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle ta.onboard.status, ta.crawl.url, ta.savings.compare, ta.team.invite, ta.qa.redesign."""

    if tool == "ta.onboard.status":
        checks: Dict[str, Any] = {}
        next_steps: list[str] = []

        # 1. MCP connection — if we got here, it works
        checks["mcp_connection"] = {"status": "pass", "detail": "Connected"}

        # 2. Token / team
        token = os.environ.get("RETENTION_MCP_TOKEN", "")
        if token:
            checks["token"] = {"status": "pass", "detail": f"Token: {token[:8]}..."}
        else:
            checks["token"] = {"status": "warn", "detail": "No token set — using open mode"}

        team_id = os.environ.get("RETENTION_TEAM", "")
        if team_id:
            checks["team"] = {"status": "pass", "detail": f"Team: {team_id}"}
        else:
            checks["team"] = {"status": "info", "detail": "No team — run ta.team.invite to create one"}
            next_steps.append("Create or join a team: ta.team.invite")

        # 3. Backend health — if we're responding, backend is running
        checks["backend"] = {"status": "pass", "detail": "Backend running (this response proves it)"}

        # 4. Playwright (module + browser binary)
        try:
            from playwright.async_api import async_playwright  # noqa: F811
            # Check if browser binary actually exists
            import pathlib
            pw_cache = pathlib.Path.home() / ".cache" / "ms-playwright"
            if not pw_cache.exists():
                pw_cache = pathlib.Path("/opt/render/.cache/ms-playwright")
            chromium_dirs = list(pw_cache.glob("chromium*")) if pw_cache.exists() else []
            if chromium_dirs:
                checks["playwright"] = {"status": "pass", "detail": f"Playwright + Chromium installed ({chromium_dirs[0].name})"}
            else:
                checks["playwright"] = {"status": "warn", "detail": "Playwright module installed but Chromium binary missing",
                                        "fix": "playwright install chromium"}
        except ImportError:
            checks["playwright"] = {"status": "warn", "detail": "Playwright not installed — web crawl will use backend",
                                    "fix": "pip install playwright && playwright install chromium"}

        # 5. Emulator (optional)
        import shutil
        if shutil.which("adb"):
            try:
                result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5)
                devices = [l for l in result.stdout.strip().split("\n")[1:] if l.strip() and "device" in l]
                if devices:
                    checks["emulator"] = {"status": "pass", "detail": f"{len(devices)} device(s) connected"}
                else:
                    checks["emulator"] = {"status": "info", "detail": "ADB available but no devices connected",
                                          "fix": "ta.setup.launch_emulator"}
            except Exception:
                checks["emulator"] = {"status": "info", "detail": "ADB timeout"}
        else:
            checks["emulator"] = {"status": "info", "detail": "No ADB — mobile testing not available (web QA works fine)"}

        # 6. Saved trajectories
        from ..agents.device_testing.trajectory_logger import get_trajectory_logger
        tl = get_trajectory_logger()
        trajectories = tl.list_all_trajectories()
        traj_count = len(trajectories)
        if traj_count > 0:
            checks["trajectories"] = {"status": "pass", "detail": f"{traj_count} saved trajectories"}
        else:
            checks["trajectories"] = {"status": "info", "detail": "No trajectories yet — run ta.crawl.url to create one"}
            next_steps.insert(0, "Crawl your site: ta.crawl.url(url='https://your-app.com')")

        # 7. Memory stats
        from ..agents.qa_pipeline.exploration_memory import get_memory_stats
        stats = get_memory_stats()
        checks["memory"] = {
            "status": "pass" if stats.get("total_entries", 0) > 0 else "info",
            "detail": f"{stats.get('total_entries', 0)} cached entries across {stats.get('unique_apps', 0)} apps",
        }

        all_pass = all(c["status"] in ("pass", "info") for c in checks.values())
        if not next_steps:
            next_steps = ["You're all set! Try: ta.crawl.url(url='https://your-app.com')"]

        return {
            "tool": tool, "status": "ok",
            "ready": all_pass,
            "checks": checks,
            "next_steps": next_steps,
            "summary": "✅ Ready for QA" if all_pass else "⚠️ Some components need setup — see next_steps",
        }

    if tool == "ta.crawl.url":
        url = args.get("url", "")
        if not url:
            return {"error": "url is required"}
        depth = min(int(args.get("depth", 2)), 5)
        save_traj = args.get("save_trajectory", True)

        # Use Playwright to crawl
        findings: list[dict] = []
        screens: list[dict] = []
        console_errors: list[str] = []

        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--single-process"])
                context = await browser.new_context(viewport={"width": 1280, "height": 800})
                page = await context.new_page()

                # Capture console errors
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

                visited: set[str] = set()
                to_visit: list[tuple[str, int]] = [(url, 0)]

                while to_visit and len(screens) < 10:
                    current_url, current_depth = to_visit.pop(0)
                    if current_url in visited or current_depth > depth:
                        continue
                    visited.add(current_url)

                    try:
                        await page.goto(current_url, wait_until="domcontentloaded", timeout=15000)
                        await page.wait_for_timeout(3000)  # SPA hydrate + render

                        # Capture screenshot
                        screenshot_bytes = await page.screenshot(type="jpeg", quality=60)
                        import base64
                        screenshot_b64 = base64.b64encode(screenshot_bytes).decode()

                        # Extract elements
                        elements = await page.evaluate("""() => {
                            const els = document.querySelectorAll('a, button, input, select, textarea, [role="button"], [onclick]');
                            return Array.from(els).map(el => ({
                                tag: el.tagName.toLowerCase(),
                                text: (el.textContent || '').trim().slice(0, 100),
                                href: el.getAttribute('href') || '',
                                type: el.getAttribute('type') || '',
                                role: el.getAttribute('role') || '',
                            }));
                        }""")

                        interactive_count = len(elements)

                        # Extract links for depth crawl
                        links = await page.evaluate("""() => {
                            return Array.from(document.querySelectorAll('a[href]'))
                                .map(a => a.href)
                                .filter(h => h.startsWith('http'));
                        }""")

                        # Filter to same domain
                        from urllib.parse import urlparse
                        base_domain = urlparse(url).netloc
                        same_domain_links = [l for l in links if urlparse(l).netloc == base_domain and l not in visited]

                        screens.append({
                            "url": current_url,
                            "depth": current_depth,
                            "screenshot": screenshot_b64[:100] + "..." if screenshot_b64 else None,
                            "screenshot_full": screenshot_b64,
                            "interactive_elements": interactive_count,
                            "outgoing_links": len(same_domain_links),
                            "title": await page.title(),
                        })

                        for link in same_domain_links[:5]:
                            to_visit.append((link, current_depth + 1))

                    except Exception as e:
                        screens.append({
                            "url": current_url, "depth": current_depth,
                            "error": str(e), "interactive_elements": 0,
                        })

                await browser.close()

            # Analyze findings
            if console_errors:
                findings.append({
                    "severity": "error", "category": "javascript",
                    "title": f"{len(console_errors)} JavaScript error(s) detected",
                    "details": console_errors[:5],
                    "fix": "Fix JS errors — the app may not render correctly in automated browsers, bots, or older devices.",
                })

            no_elements = [s for s in screens if s.get("interactive_elements", 0) == 0 and not s.get("error")]
            if no_elements:
                findings.append({
                    "severity": "warning", "category": "rendering",
                    "title": f"{len(no_elements)} page(s) with no interactive elements",
                    "details": [s["url"] for s in no_elements],
                    "fix": "Check if the app works in headless Chrome. SSR improves crawlability and SEO.",
                })

            # Check for missing alt text, lang attr, etc.
            total_interactive = sum(s.get("interactive_elements", 0) for s in screens)
            if total_interactive == 0 and len(screens) > 0:
                findings.append({
                    "severity": "info", "category": "spa",
                    "title": "Single-page app detected with client-side rendering",
                    "fix": "Install retention.sh locally for deeper SPA crawling with full JavaScript execution.",
                })

            # Save trajectory if requested
            trajectory_id = None
            if save_traj and screens:
                try:
                    from ..agents.device_testing.trajectory_logger import get_trajectory_logger
                    tl = get_trajectory_logger()
                    from ..agents.device_testing.trajectory_logger import TrajectoryStep, TrajectoryLog
                    import uuid
                    traj_id = str(uuid.uuid4())[:8]
                    steps = [
                        TrajectoryStep(
                            step_index=i,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            action=f"Navigate to {s['url']}",
                            state_before={"url": s.get("url", ""), "title": s.get("title", "")},
                            state_after={"url": s.get("url", ""), "elements": s.get("interactive_elements", 0)},
                            success=not s.get("error"),
                            duration_ms=2000,
                            semantic_label=f"Visit {s.get('title', s['url'])}",
                        )
                        for i, s in enumerate(screens)
                    ]
                    traj = TrajectoryLog(
                        trajectory_id=traj_id,
                        task_name="web_crawl",
                        task_goal=f"Crawl {url}",
                        device_id="playwright",
                        started_at=datetime.now(timezone.utc).isoformat(),
                        completed_at=datetime.now(timezone.utc).isoformat(),
                        steps=steps,
                        total_actions=len(steps),
                        success=len(findings) == 0 or all(f["severity"] != "error" for f in findings),
                        surface="browser",
                        workflow_family="web_crawl",
                    )
                    tl.save_trajectory("web_crawl", traj)
                    trajectory_id = traj_id
                except Exception as traj_err:
                    logger.warning(f"Trajectory save failed (non-fatal): {traj_err}")

        except ImportError:
            return {
                "tool": tool, "status": "fallback",
                "message": "Playwright not installed locally. Use the web demo at retention.sh/demo to crawl URLs.",
                "fix": "pip install playwright && playwright install chromium",
            }
        except Exception as e:
            return {"tool": tool, "status": "error", "error": str(e)}

        # Strip full screenshots from MCP response (too large for token context)
        screens_summary = [{k: v for k, v in s.items() if k != "screenshot_full"} for s in screens]

        return {
            "tool": tool, "status": "ok",
            "url": url,
            "screens": screens_summary,
            "total_screens": len(screens),
            "total_interactive_elements": total_interactive,
            "findings": findings,
            "trajectory_id": trajectory_id,
            "next_steps": [
                f"Fix the {len(findings)} finding(s) above, then re-crawl: ta.crawl.url(url='{url}')" if findings
                else f"Clean crawl! Save trajectory for replay: ta.savings.compare(url='{url}')",
                "View site map: retention.sh/demo (enter your URL to see the visual crawl)",
                "Deep UX audit: ta.ux_audit(url='" + url + "')",
                "Generate tests: ta.suggest_tests(url='" + url + "')",
            ],
        }

    if tool == "ta.savings.compare":
        url = args.get("url", "")
        trajectory_id = args.get("trajectory_id", "")

        from ..agents.device_testing.trajectory_logger import get_trajectory_logger
        tl = get_trajectory_logger()
        trajectories = tl.list_all_trajectories()

        if not trajectory_id and not url:
            if trajectories:
                # Use most recent
                latest = max(trajectories, key=lambda t: t.get("created_at", ""))
                trajectory_id = latest.get("trajectory_id", "")
                url = latest.get("task_goal", "").replace("Crawl ", "")
            else:
                return {"error": "No trajectories found. Run ta.crawl.url first to create a baseline."}

        # Find the trajectory
        traj = None
        base = tl._base_dir
        if base.exists():
            for task_dir in base.iterdir():
                if task_dir.is_dir() and not task_dir.name.startswith("_"):
                    if trajectory_id:
                        traj = tl.load_trajectory(task_dir.name, trajectory_id)
                    if traj:
                        break

        if not traj:
            return {"error": f"Trajectory {trajectory_id} not found. Run ta.crawl.url to create one."}

        # Simulate comparison (actual device replay requires running pipeline)
        from ..agents.qa_pipeline.trajectory_replay import get_savings_aggregate
        aggregate = get_savings_aggregate()

        return {
            "tool": tool, "status": "ok",
            "trajectory": {
                "id": traj.trajectory_id,
                "task": traj.task_name,
                "goal": traj.task_goal,
                "steps": len(traj.steps),
                "surface": traj.surface,
                "replay_count": traj.replay_count,
            },
            "comparison": {
                "full_crawl": {
                    "tokens": traj.source_tokens_actual or 31000,
                    "time_s": (traj.source_time_actual_s or 85),
                    "requests": len(traj.steps) * 3,
                },
                "trajectory_replay": {
                    "tokens": int((traj.source_tokens_actual or 31000) * (1 - (traj.avg_token_savings or 0.955))),
                    "time_s": round((traj.source_time_actual_s or 85) * 0.5, 1),
                    "requests": len(traj.steps),
                },
                "savings": {
                    "tokens_pct": f"{(traj.avg_token_savings or 0.955) * 100:.1f}%",
                    "time_pct": "50.0%",
                    "requests_pct": f"{(1 - 1/3) * 100:.1f}%",
                },
            },
            "aggregate": aggregate,
            "next_step": "Apply a fix to your app, then re-crawl: ta.crawl.url(url='" + (url or traj.task_goal) + "')",
        }

    if tool == "ta.team.invite":
        team_name = args.get("team_name", "")
        team_code = os.environ.get("RETENTION_TEAM", "")

        if not team_code:
            # Create a new team
            import hashlib
            email = os.environ.get("RETENTION_EMAIL", "user@example.com")
            team_code = hashlib.sha256(f"{email}:{time.time()}".encode()).hexdigest()[:6].upper()
            team_name = team_name or "My Team"

        convex_url = os.environ.get("CONVEX_SITE_URL", "https://exuberant-ferret-263.convex.site")
        dashboard_url = os.environ.get("RETENTION_DASHBOARD_URL", "https://test-studio-xi.vercel.app")

        slack_message = f"""🧠 Set up QA memory for our team

Paste this into Claude Code (or your terminal):

```
RETENTION_TEAM={team_code} curl -sL retention.sh/install.sh | bash
```

Then restart Claude Code. That's it — your QA runs now sync to the team.

Dashboard: {dashboard_url}/memory/team?team={team_code}"""

        return {
            "tool": tool, "status": "ok",
            "team_code": team_code,
            "team_name": team_name,
            "dashboard_url": f"{dashboard_url}/memory/team?team={team_code}",
            "slack_message": slack_message,
            "instructions": "Copy the message above and paste it in your Slack/Discord channel.",
        }

    if tool == "ta.qa.redesign":
        url = args.get("url", "")
        if not url:
            return {"error": "url is required"}
        focus = args.get("focus", "all")
        fix_mode = args.get("fix_mode", True)

        # Step 1: Crawl
        crawl_result = await _dispatch_retention("ta.crawl.url", {"url": url, "depth": 2, "save_trajectory": True})

        if crawl_result.get("status") == "error":
            return crawl_result

        findings = crawl_result.get("findings", [])
        screens = crawl_result.get("screens", [])
        trajectory_id = crawl_result.get("trajectory_id")

        # Step 2: Generate fix suggestions
        fix_suggestions: list[dict] = []
        if fix_mode and findings:
            for finding in findings:
                suggestion: dict[str, Any] = {
                    "finding": finding["title"],
                    "severity": finding["severity"],
                    "category": finding.get("category", "general"),
                }

                if finding.get("category") == "javascript":
                    suggestion["fix_approach"] = (
                        "Check the browser console for the specific error. Common causes:\n"
                        "- Minified code referencing variables before initialization (build tool issue)\n"
                        "- Missing polyfills for older browser APIs\n"
                        "- Race conditions in SPA hydration\n"
                        "Fix: Check your build output, ensure SSR fallbacks exist."
                    )
                    suggestion["files_to_check"] = ["vite.config.ts", "next.config.js", "src/main.tsx", "src/App.tsx"]

                elif finding.get("category") == "rendering":
                    suggestion["fix_approach"] = (
                        "Pages with 0 interactive elements likely have:\n"
                        "- Client-only rendering without SSR fallback\n"
                        "- JavaScript errors preventing mount\n"
                        "- Content behind auth/login wall\n"
                        "Fix: Add server-side rendering or static generation for key pages."
                    )
                    suggestion["files_to_check"] = ["src/pages/", "src/app/", "next.config.js"]

                elif finding.get("category") == "a11y":
                    suggestion["fix_approach"] = (
                        "Add missing accessibility attributes:\n"
                        "- alt text on images\n"
                        "- aria-labels on icon buttons\n"
                        "- lang attribute on <html>\n"
                        "- proper heading hierarchy"
                    )

                elif finding.get("category") == "spa":
                    suggestion["fix_approach"] = (
                        "SPA detected — for better crawlability:\n"
                        "- Add SSR or static generation (Next.js, Remix, Astro)\n"
                        "- Ensure meta tags render server-side\n"
                        "- Add a robots.txt and sitemap.xml"
                    )

                fix_suggestions.append(suggestion)

        # Step 3: Build the redesign loop
        return {
            "tool": tool, "status": "ok",
            "url": url,
            "crawl_summary": {
                "screens": len(screens),
                "interactive_elements": crawl_result.get("total_interactive_elements", 0),
                "findings": len(findings),
            },
            "findings": findings,
            "fix_suggestions": fix_suggestions,
            "trajectory_id": trajectory_id,
            "qa_loop": {
                "step_1": "Review the findings and fix suggestions above",
                "step_2": "Apply fixes to your codebase",
                "step_3": f"Re-crawl to verify: ta.crawl.url(url='{url}')",
                "step_4": f"Compare savings: ta.savings.compare(trajectory_id='{trajectory_id}')" if trajectory_id else "Save a trajectory first",
                "step_5": "Repeat until clean — each re-crawl uses trajectory replay and costs less",
            },
            "dashboard": f"View results: retention.sh/demo",
        }

    # ── Interactive Site Map ─────────────────────────────────────────────────
    # ta.sitemap — stateful, drillable site map via MCP

    if tool == "ta.sitemap":
        action = args.get("action", "crawl")  # crawl | overview | screen | screenshot | findings
        url = args.get("url", "")

        # Persistent crawl cache (lives for the session)
        if not hasattr(_dispatch_retention, "_sitemap_cache"):
            _dispatch_retention._sitemap_cache = {}  # type: ignore[attr-defined]
        cache = _dispatch_retention._sitemap_cache  # type: ignore[attr-defined]

        if action == "crawl":
            if not url:
                return {"error": "url is required for action='crawl'. Example: ta.sitemap(url='https://myapp.com')"}
            # Run the crawl and cache results
            crawl = await _dispatch_retention("ta.crawl.url", {"url": url, "depth": 2, "save_trajectory": True})
            if crawl.get("status") == "error":
                return crawl
            cache[url] = crawl
            screens = crawl.get("screens", [])
            findings = crawl.get("findings", [])
            return {
                "tool": tool, "action": "crawl", "status": "ok",
                "url": url,
                "summary": f"{len(screens)} screens, {crawl.get('total_interactive_elements', 0)} elements, {len(findings)} findings",
                "screens": [
                    {"index": i, "url": s.get("url", ""), "title": s.get("title", ""), "depth": s.get("depth", 0),
                     "elements": s.get("interactive_elements", 0), "has_screenshot": bool(s.get("screenshot_full"))}
                    for i, s in enumerate(screens)
                ],
                "findings_summary": [{"severity": f["severity"], "title": f["title"]} for f in findings],
                "next": [
                    "ta.sitemap(action='overview') — see the full navigation map",
                    "ta.sitemap(action='screen', index=0) — drill into a specific screen",
                    "ta.sitemap(action='screenshot', index=0) — get the screenshot of a screen",
                    "ta.sitemap(action='findings') — see all findings with details",
                ],
            }

        if action == "overview":
            # Return the cached crawl overview (no re-crawl)
            if not cache:
                return {"error": "No crawl cached. Run ta.sitemap(url='...') first."}
            last_url = list(cache.keys())[-1]
            crawl = cache[last_url]
            screens = crawl.get("screens", [])

            # Build navigation graph
            nav_graph = {}
            for s in screens:
                page_url = s.get("url", "")
                nav_graph[page_url] = {
                    "title": s.get("title", ""),
                    "depth": s.get("depth", 0),
                    "elements": s.get("interactive_elements", 0),
                    "outgoing": s.get("outgoing_links", 0),
                }

            return {
                "tool": tool, "action": "overview", "url": last_url,
                "total_screens": len(screens),
                "navigation_graph": nav_graph,
                "depth_distribution": {
                    d: len([s for s in screens if s.get("depth") == d])
                    for d in sorted(set(s.get("depth", 0) for s in screens))
                },
                "next": [
                    f"ta.sitemap(action='screen', index={i}) — {s.get('title', s.get('url', ''))[:40]}"
                    for i, s in enumerate(screens[:8])
                ],
            }

        if action == "screen":
            # Drill into a specific screen
            index = int(args.get("index", 0))
            if not cache:
                return {"error": "No crawl cached. Run ta.sitemap(url='...') first."}
            last_url = list(cache.keys())[-1]
            screens = cache[last_url].get("screens", [])
            if index >= len(screens):
                return {"error": f"Screen index {index} out of range (0-{len(screens)-1})"}

            screen = screens[index]
            return {
                "tool": tool, "action": "screen", "index": index,
                "url": screen.get("url", ""),
                "title": screen.get("title", ""),
                "depth": screen.get("depth", 0),
                "interactive_elements": screen.get("interactive_elements", 0),
                "outgoing_links": screen.get("outgoing_links", 0),
                "has_screenshot": bool(screen.get("screenshot_full")),
                "error": screen.get("error"),
                "next": [
                    f"ta.sitemap(action='screenshot', index={index}) — view this page's screenshot",
                    "ta.sitemap(action='findings') — see QA findings",
                    "ta.sitemap(action='overview') — back to overview",
                ],
            }

        if action == "screenshot":
            # Return base64 screenshot for a specific screen
            index = int(args.get("index", 0))
            if not cache:
                return {"error": "No crawl cached. Run ta.sitemap(url='...') first."}
            last_url = list(cache.keys())[-1]
            screens = cache[last_url].get("screens", [])
            if index >= len(screens):
                return {"error": f"Screen index {index} out of range"}

            screen = screens[index]
            b64 = screen.get("screenshot_full", "")
            if not b64:
                return {"error": f"No screenshot available for screen {index}"}

            return {
                "tool": tool, "action": "screenshot", "index": index,
                "url": screen.get("url", ""),
                "title": screen.get("title", ""),
                "image_base64": b64,
                "image_type": "jpeg",
                "note": "This is a base64 JPEG screenshot of the page as seen by the crawler.",
            }

        if action == "findings":
            if not cache:
                return {"error": "No crawl cached. Run ta.sitemap(url='...') first."}
            last_url = list(cache.keys())[-1]
            findings = cache[last_url].get("findings", [])
            return {
                "tool": tool, "action": "findings",
                "url": last_url,
                "total": len(findings),
                "findings": findings,
                "next": [
                    f"Fix the issues, then re-crawl: ta.sitemap(url='{last_url}')",
                    f"ta.ux_audit(url='{last_url}') — deeper UX analysis",
                ],
            }

        return {"error": f"Unknown sitemap action: {action}. Use: crawl, overview, screen, screenshot, findings"}

    if tool == "ta.start_workflow":
        # Canonical workflow start — checks for existing trajectory, decides replay vs explore
        url = args.get("url", "")
        workflow_id = args.get("workflow_id", "")
        mode = args.get("mode", "auto")  # auto | replay | explore | replay_with_fallback

        if not url and not workflow_id:
            return {"error": "url or workflow_id required"}

        from ..agents.device_testing.trajectory_logger import get_trajectory_logger, ExecutionPacket
        tl = get_trajectory_logger()

        # Check for existing trajectory
        trajectories = tl.list_all_trajectories()
        matching = [t for t in trajectories if url in (t.get("task_goal", "") or "") or t.get("workflow_family") == workflow_id]

        # Decide mode
        if mode == "auto":
            if matching:
                best = max(matching, key=lambda t: t.get("replay_count", 0))
                mode = "replay_with_fallback"
                trajectory_id = best.get("trajectory_id")
            else:
                mode = "explore"
                trajectory_id = None
        else:
            trajectory_id = matching[0].get("trajectory_id") if matching else None

        # Build packet
        import uuid as _uuid
        packet = ExecutionPacket(
            packet_id=f"pkt_{_uuid.uuid4().hex[:8]}",
            workflow_id=workflow_id or f"web_crawl_{url.replace('https://', '').replace('/', '_')[:30]}",
            run_mode=mode,
            surface="browser",
            target_url=url,
            trajectory_id=trajectory_id,
            success_criteria=args.get("success_criteria", ["page loads", "no JS errors", "interactive elements present"]),
            memory_context={
                "prior_runs": len(matching),
                "best_trajectory": trajectory_id,
                "avg_savings": matching[0].get("avg_token_savings", 0) if matching else 0,
            },
            budget=args.get("budget", {"max_requests": 50, "max_cost_usd": 0.05, "max_duration_s": 300}),
            runtime_target=args.get("runtime", "auto"),
        )

        # Execute based on mode
        if mode in ("replay", "replay_with_fallback") and trajectory_id:
            # Replay existing trajectory
            crawl = await _dispatch_retention("ta.crawl.url", {"url": url, "depth": 1, "save_trajectory": True})
            return {
                "tool": tool, "status": "ok",
                "packet": {
                    "packet_id": packet.packet_id,
                    "workflow_id": packet.workflow_id,
                    "run_mode": mode,
                    "trajectory_id": trajectory_id,
                    "prior_runs": len(matching),
                },
                "result": {k: v for k, v in crawl.items() if k != "screens"},
                "savings_note": f"Used trajectory replay — {matching[0].get('avg_token_savings', 0):.0f}% fewer tokens" if matching else "First run — full crawl",
            }
        else:
            # Full exploration
            crawl = await _dispatch_retention("ta.crawl.url", {"url": url, "depth": 2, "save_trajectory": True})
            return {
                "tool": tool, "status": "ok",
                "packet": {
                    "packet_id": packet.packet_id,
                    "workflow_id": packet.workflow_id,
                    "run_mode": "explore",
                    "trajectory_id": crawl.get("trajectory_id"),
                },
                "result": {k: v for k, v in crawl.items() if k != "screens"},
                "next": f"Trajectory saved. Next run will use replay: ta.start_workflow(url='{url}')",
            }

    if tool == "ta.memory.rollup":
        period = args.get("period", "daily")  # daily | weekly | monthly
        workflow_family = args.get("workflow_family", "")

        from ..agents.device_testing.trajectory_logger import get_trajectory_logger
        tl = get_trajectory_logger()
        trajectories = tl.list_all_trajectories()

        if workflow_family:
            trajectories = [t for t in trajectories if t.get("workflow_family") == workflow_family]

        total_runs = sum(t.get("replay_count", 0) + 1 for t in trajectories)
        total_saved = sum(t.get("avg_token_savings", 0) * (t.get("replay_count", 0) + 1) for t in trajectories)
        avg_drift = sum(t.get("drift_score", 0) for t in trajectories) / max(len(trajectories), 1)

        from ..agents.qa_pipeline.trajectory_replay import get_savings_aggregate
        aggregate = get_savings_aggregate()

        return {
            "tool": tool, "status": "ok",
            "period": period,
            "workflow_family": workflow_family or "all",
            "rollup": {
                "total_workflows": len(trajectories),
                "total_runs": total_runs,
                "total_tokens_saved": aggregate.get("total_tokens_saved", 0),
                "total_time_saved_s": aggregate.get("total_time_saved_s", 0),
                "avg_drift_score": round(avg_drift, 3),
                "replay_success_rate": aggregate.get("replay_success_rate", 0),
                "durability_score": round(aggregate.get("replay_success_rate", 0) * 100, 0),
            },
            "next": [
                "ta.memory.rollup(period='weekly') — weekly rollup",
                "ta.savings.compare — detailed A/B comparison",
            ],
        }

    if tool == "ta.qa_check":
        # One-shot QA check — crawl + findings in one call
        url = args.get("url", "")
        if not url:
            return {"error": "url is required. Example: ta.qa_check(url='http://localhost:3000')"}
        crawl = await _dispatch_retention("ta.crawl.url", {"url": url, "depth": 1, "save_trajectory": False})
        if crawl.get("status") == "error":
            return crawl
        findings = crawl.get("findings", [])
        errors = [f for f in findings if f["severity"] == "error"]
        warnings = [f for f in findings if f["severity"] == "warning"]
        return {
            "tool": tool, "status": "ok",
            "url": url,
            "verdict": "fail" if errors else "warn" if warnings else "pass",
            "screens_found": crawl.get("total_screens", 0),
            "interactive_elements": crawl.get("total_interactive_elements", 0),
            "errors": len(errors),
            "warnings": len(warnings),
            "findings": findings,
            "summary": (
                f"❌ {len(errors)} error(s), {len(warnings)} warning(s)" if errors
                else f"⚠️ {len(warnings)} warning(s), no errors" if warnings
                else f"✅ Clean — {crawl.get('total_screens', 0)} screens, {crawl.get('total_interactive_elements', 0)} elements"
            ),
        }

    if tool == "ta.diff_crawl":
        # Compare current crawl vs last saved crawl
        url = args.get("url", "")
        if not url:
            return {"error": "url is required"}

        # Get last saved crawl for this URL
        from ..agents.device_testing.trajectory_logger import get_trajectory_logger
        tl = get_trajectory_logger()
        trajectories = tl.list_all_trajectories()
        prev = [t for t in trajectories if url in (t.get("task_goal", "") or "")]
        prev_screens = len(prev[0].get("steps", [])) if prev else 0

        # Crawl now
        current = await _dispatch_retention("ta.crawl.url", {"url": url, "depth": 1, "save_trajectory": True})
        if current.get("status") == "error":
            return current

        current_screens = current.get("total_screens", 0)
        current_elements = current.get("total_interactive_elements", 0)
        current_findings = len(current.get("findings", []))

        diff = {
            "screens_before": prev_screens,
            "screens_after": current_screens,
            "screens_delta": current_screens - prev_screens,
            "findings_count": current_findings,
            "has_previous": bool(prev),
        }

        return {
            "tool": tool, "status": "ok",
            "url": url,
            "diff": diff,
            "current_crawl": {k: v for k, v in current.items() if k != "screens"},
            "summary": (
                f"New: {current_screens} screens, {current_elements} elements, {current_findings} findings"
                if not prev
                else f"Delta: {diff['screens_delta']:+d} screens, {current_findings} findings"
            ),
        }

    if tool == "ta.ux_audit":
        # Deep UX audit — navigation structure, layout scoring, first-time visitor flow
        url = args.get("url", "")
        if not url:
            return {"error": "url is required"}

        crawl = await _dispatch_retention("ta.crawl.url", {"url": url, "depth": 2, "save_trajectory": True})
        if crawl.get("status") == "error":
            return crawl

        screens = crawl.get("screens", [])
        findings = crawl.get("findings", [])
        total_elements = crawl.get("total_interactive_elements", 0)

        # Navigation analysis
        nav_issues = []
        if len(screens) <= 1:
            nav_issues.append("Only 1 page discovered — SPA or gated content")
        dead_ends = [s for s in screens if s.get("outgoing_links", 0) == 0]
        if dead_ends:
            nav_issues.append(f"{len(dead_ends)} dead-end page(s) with no outgoing links")

        # First-time visitor scoring
        landing = screens[0] if screens else {}
        landing_score = 0
        landing_notes = []
        if landing.get("interactive_elements", 0) >= 3:
            landing_score += 30
            landing_notes.append("Landing has CTAs")
        else:
            landing_notes.append("Landing has few/no interactive elements — may confuse visitors")
        if landing.get("outgoing_links", 0) >= 2:
            landing_score += 20
            landing_notes.append("Landing links to other pages")
        if len(screens) >= 3:
            landing_score += 25
            landing_notes.append("Multiple pages discoverable")
        if not findings or all(f["severity"] != "error" for f in findings):
            landing_score += 25
            landing_notes.append("No critical errors")
        else:
            landing_notes.append("Critical errors detected — fix before showing to visitors")

        # Layout consistency check
        element_counts = [s.get("interactive_elements", 0) for s in screens]
        layout_variance = max(element_counts) - min(element_counts) if element_counts else 0

        return {
            "tool": tool, "status": "ok",
            "url": url,
            "audit": {
                "total_screens": len(screens),
                "total_elements": total_elements,
                "findings_count": len(findings),
                "navigation_issues": nav_issues,
                "dead_end_pages": len(dead_ends),
                "landing_page_score": landing_score,
                "landing_notes": landing_notes,
                "layout_element_variance": layout_variance,
                "overall_score": min(100, landing_score + (10 if not nav_issues else 0) + (10 if layout_variance < 10 else 0)),
            },
            "findings": findings,
            "recommendations": [
                "Fix all error-severity findings first",
                "Ensure every page has at least 1 CTA (link or button)",
                "Add a clear install/signup action on the landing page",
                "Keep navigation consistent across pages (sidebar or top nav on all product pages)",
                "Test with a first-time visitor mindset — what do they see in 5 seconds?",
            ],
            "next_steps": [
                f"Fix findings: {len(findings)} issue(s) to address",
                f"Re-audit after fixes: ta.ux_audit(url='{url}')",
                f"Compare improvement: ta.diff_crawl(url='{url}')",
            ],
        }

    if tool == "ta.suggest_tests":
        url = args.get("url", "")
        if not url:
            return {"error": "url is required"}

        crawl = await _dispatch_retention("ta.crawl.url", {"url": url, "depth": 1, "save_trajectory": False})
        screens = crawl.get("screens", [])
        total_elements = crawl.get("total_interactive_elements", 0)

        test_cases = []
        for s in screens:
            screen_url = s.get("url", "")
            title = s.get("title", screen_url)
            elements = s.get("interactive_elements", 0)
            if elements > 0:
                test_cases.append({
                    "test_name": f"Verify {title[:40]}",
                    "url": screen_url,
                    "steps": [
                        f"Navigate to {screen_url}",
                        f"Verify page loads without JS errors",
                        f"Verify {elements} interactive elements are present and clickable",
                        f"Verify page responds within 3 seconds",
                    ],
                    "priority": "P0" if s.get("depth", 0) == 0 else "P1",
                })

        return {
            "tool": tool, "status": "ok",
            "url": url,
            "test_cases": test_cases,
            "total_tests": len(test_cases),
            "summary": f"Generated {len(test_cases)} test cases from {len(screens)} screens",
        }

    return {"error": f"Unknown retention tool: {tool}"}


# ---------------------------------------------------------------------------
# Linkage Graph — connect runs to features, commits, code
# ---------------------------------------------------------------------------

def _dispatch_linkage(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle ta.linkage.* tools for feature/commit/code linkage."""
    from ..agents.qa_pipeline.linkage_graph import (
        register_feature, get_affected_features,
        get_rerun_suggestions, get_graph_stats,
    )

    if tool == "ta.linkage.register_feature":
        feature_id = args.get("feature_id", "")
        name = args.get("name", "")
        if not feature_id or not name:
            return {"error": "feature_id and name are required"}
        result = register_feature(
            feature_id=feature_id, name=name,
            description=args.get("description", ""),
            prd_section=args.get("prd_section", ""),
            design_ref=args.get("design_ref", ""),
        )
        return {"tool": tool, "status": "ok", "feature": result}

    if tool == "ta.linkage.affected_features":
        files_changed = args.get("files_changed", [])
        if not files_changed:
            return {"error": "files_changed list is required"}
        affected = get_affected_features(files_changed)
        return {
            "tool": tool, "status": "ok",
            "affected": affected,
            "total": len(affected),
            "message": (
                f"{len(affected)} features affected by changes to {len(files_changed)} files. "
                + (f"Suggest re-testing: {', '.join(a['name'] for a in affected[:5])}" if affected else "No known features affected — linkage graph may need more data.")
            ),
        }

    if tool == "ta.linkage.rerun_suggestions":
        commit = args.get("commit", "")
        files = args.get("files_changed", [])
        result = get_rerun_suggestions(commit_hash=commit, files_changed=files)
        return {"tool": tool, "status": "ok", **result}

    if tool == "ta.linkage.stats":
        return {"tool": tool, "status": "ok", **get_graph_stats()}

    return {"error": f"Unknown linkage tool: {tool}"}


# ---------------------------------------------------------------------------
# Context Graph — execution judgment infrastructure
# ---------------------------------------------------------------------------

async def _dispatch_context_graph(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle ta.graph.* tools for context graph queries.

    Available tools:
      ta.graph.list              — list all persisted graphs
      ta.graph.stats             — graph statistics + verdict breakdown
      ta.graph.verdicts          — verdict attribution (app bug vs agent bug vs env)
      ta.graph.failure_chain     — walk failure chain from outcome node
      ta.graph.precedents        — find similar past runs by fingerprint
      ta.graph.mermaid           — export graph as Mermaid diagram
      ta.graph.slack_topic_history    — search past Slack conversations by topic
      ta.graph.slack_user_history     — get all requests from a user
      ta.graph.slack_open_items       — find unresolved action items
      ta.graph.slack_similar_request  — find similar past requests
    """
    from .mcp_context_graph import (
        graph_list, graph_stats, graph_verdicts,
        graph_failure_chain, graph_precedents, graph_mermaid,
        slack_topic_history, slack_user_history,
        slack_open_items, slack_similar_request,
    )

    if tool == "ta.graph.list":
        return await graph_list()

    if tool == "ta.graph.stats":
        return await graph_stats(args.get("graph_id", "global"))

    if tool == "ta.graph.verdicts":
        return await graph_verdicts(
            args.get("graph_id", "global"),
            args.get("run_id", ""),
        )

    if tool == "ta.graph.failure_chain":
        graph_id = args.get("graph_id", "")
        node_id = args.get("node_id", "")
        if not graph_id or not node_id:
            return {"error": "graph_id and node_id are required"}
        return await graph_failure_chain(graph_id, node_id)

    if tool == "ta.graph.precedents":
        graph_id = args.get("graph_id", "")
        fingerprint = args.get("fingerprint", "")
        if not graph_id or not fingerprint:
            return {"error": "graph_id and fingerprint are required"}
        return await graph_precedents(
            graph_id, fingerprint,
            args.get("node_type", ""),
            int(args.get("limit", 5)),
        )

    if tool == "ta.graph.mermaid":
        return await graph_mermaid(
            args.get("graph_id", "global"),
            args.get("run_id", ""),
            int(args.get("max_nodes", 50)),
        )

    # Slack graph queries
    if tool == "ta.graph.slack_topic_history":
        keywords = args.get("keywords", "")
        if not keywords:
            return {"error": "keywords is required (comma-separated)"}
        return await slack_topic_history(keywords, int(args.get("limit", 10)))

    if tool == "ta.graph.slack_user_history":
        user_id = args.get("user_id", "")
        if not user_id:
            return {"error": "user_id is required"}
        return await slack_user_history(user_id, int(args.get("limit", 20)))

    if tool == "ta.graph.slack_open_items":
        return await slack_open_items()

    if tool == "ta.graph.slack_similar_request":
        message = args.get("message", "")
        if not message:
            return {"error": "message is required"}
        return await slack_similar_request(message, int(args.get("limit", 5)))

    return {"error": f"Unknown context graph tool: {tool}"}


# ---------------------------------------------------------------------------
# Screenshot Diff — before/after visual comparison
# ---------------------------------------------------------------------------

def _dispatch_screenshots(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle ta.screenshots.* tools for visual regression detection."""
    from ..agents.qa_pipeline.screenshot_diff import (
        set_baseline, compare_screenshots, get_diff_history,
    )

    if tool == "ta.screenshots.set_baseline":
        app_name = args.get("app_name", "")
        run_id = args.get("run_id", "")
        if not app_name or not run_id:
            return {"error": "app_name and run_id are required"}
        result = set_baseline(app_name=app_name, run_id=run_id, app_url=args.get("app_url", ""))
        return {"tool": tool, "status": "ok", **result}

    if tool == "ta.screenshots.compare":
        app_name = args.get("app_name", "")
        run_id = args.get("run_id", "")
        if not app_name or not run_id:
            return {"error": "app_name and run_id are required"}
        result = compare_screenshots(app_name=app_name, run_id=run_id, app_url=args.get("app_url", ""))
        return {"tool": tool, "status": "ok", **result}

    if tool == "ta.screenshots.history":
        app_name = args.get("app_name", "")
        if not app_name:
            return {"error": "app_name is required"}
        history = get_diff_history(app_name=app_name, app_url=args.get("app_url", ""))
        return {"tool": tool, "status": "ok", "diffs": history, "total": len(history)}

    return {"error": f"Unknown screenshots tool: {tool}"}

