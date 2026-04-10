from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
import asyncio
import logging
import os
import subprocess
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
from .observability.tracing import init_langsmith
from .agents.device_testing.infrastructure import MobileMCPStreamingManager
from .agents.device_testing import (
    UnifiedBugReproductionService,
    MobileMCPClient,
)
from .agents.device_testing.infrastructure.tools import device_tools, appium_tools
from .agents.coordinator.coordinator_service import AIAgentService
from .agents.search import VectorSearchService

logging.basicConfig(
	level=logging.INFO,
	format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
	datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler for startup and shutdown events."""
    # --- Startup ---

    # Auto-install Playwright Chromium if missing (Render free tier doesn't run build.sh)
    try:
        import shutil as _shutil
        _pw_bin = _shutil.which("playwright")
        if _pw_bin:
            _pw_cache = Path.home() / ".cache" / "ms-playwright"
            _render_cache = Path("/opt/render/.cache/ms-playwright")
            _has_chromium = (
                any(_pw_cache.glob("chromium*")) if _pw_cache.exists() else False
            ) or (
                any(_render_cache.glob("chromium*")) if _render_cache.exists() else False
            )
            if not _has_chromium:
                logger.info("Chromium binary not found — installing...")
                # Try multiple approaches in order of preference
                _install_cmds = [
                    ["playwright", "install", "chromium"],           # binary only (no sudo)
                    ["playwright", "install", "--with-deps", "chromium"],  # with system deps (needs sudo)
                ]
                for _cmd in _install_cmds:
                    logger.info(f"Attempting: {' '.join(_cmd)}")
                    _result = subprocess.run(_cmd, capture_output=True, text=True, timeout=180)
                    if _result.stdout:
                        logger.info(f"stdout: {_result.stdout[:500]}")
                    if _result.stderr:
                        logger.info(f"stderr: {_result.stderr[:500]}")
                    if _result.returncode == 0:
                        logger.info("Playwright Chromium installed successfully")
                        break
                    logger.warning(f"Failed (rc={_result.returncode})")
                else:
                    logger.error("All Playwright install attempts failed — browser tools will be unavailable")
            else:
                logger.info("Playwright Chromium binary found")
    except Exception as _pw_err:
        logger.warning(f"Playwright auto-install check failed (non-blocking): {_pw_err}")

    if mobile_mcp_client:
        try:
            import asyncio as _aio
            await _aio.wait_for(mobile_mcp_client.start(), timeout=15)
            logger.info("Mobile MCP client started successfully")
        except _aio.TimeoutError:
            logger.warning("Mobile MCP client startup timed out after 15s — will retry on first request")
        except Exception as e:
            logger.warning(f"Mobile MCP client startup failed (non-blocking): {e}")

    # Start the Streamable HTTP MCP session manager
    _mcp_cm = None
    try:
        from .api.mcp_streamable import mcp as _mcp_instance
        if _mcp_instance._session_manager is not None:
            _mcp_cm = _mcp_instance._session_manager.run()
            await _mcp_cm.__aenter__()
            logger.info("Streamable HTTP MCP session manager started")
    except Exception as e:
        logger.warning(f"Failed to start MCP session manager: {e}")
        _mcp_cm = None

    yield

    # --- Shutdown ---
    # Cancel tracked pipeline tasks
    try:
        from .api.mcp_pipeline import _create_pipeline_task  # noqa: F401
        # Pipeline tasks self-track via _create_pipeline_task done callbacks
    except Exception:
        pass

    if _mcp_cm is not None:
        try:
            await _mcp_cm.__aexit__(None, None, None)
            logger.info("Streamable HTTP MCP session manager stopped")
        except Exception as e:
            logger.warning(f"Failed to stop MCP session manager: {e}")

    if mobile_mcp_client:
        try:
            await mobile_mcp_client.stop()
            logger.info("Mobile MCP client stopped successfully")
        except Exception as e:
            logger.error(f"Failed to stop Mobile MCP client: {e}")
    try:
        if "_figma_client" in globals():
            await globals()["_figma_client"].aclose()
    except Exception as e:
        logger.warning(f"Failed to close Figma client: {e}")


app = FastAPI(title="retention.sh - Backend API", lifespan=lifespan)

# Default allowed origins for local development
default_origins = [
	"http://localhost:5173", "http://127.0.0.1:5173",
	"http://localhost:5174", "http://127.0.0.1:5174",
	"http://localhost:5175", "http://127.0.0.1:5175",
	"http://localhost:5176", "http://127.0.0.1:5176",
	"http://localhost:3000", "http://127.0.0.1:3000",
	"http://localhost:8000", "http://127.0.0.1:8000",
	"http://localhost:8011", "http://127.0.0.1:8011",
	"null",  # file:// protocol sends Origin: null — needed for strategy brief opened from Finder
]

# Production origins from environment variable
env_origins = os.getenv("ALLOWED_ORIGINS", "")
if env_origins:
	additional_origins = [o.strip() for o in env_origins.split(",") if o.strip()]
	origins = default_origins + additional_origins
else:
	# Add Vercel production domain by default
	origins = default_origins + [
		"https://test-studio-xi.vercel.app",
		"https://retention.sh",
		"https://www.retention.sh",
	]

# Starlette CORS supports regex matching for origins.
# Note: Starlette uses regex fullmatch on the incoming Origin.
vercel_origin_regex = os.getenv("ALLOWED_ORIGIN_REGEX", r"https://(.*\\.vercel\\.app|.*\\.run\\.app)")
if vercel_origin_regex.strip() == "":
	vercel_origin_regex = None

logger.info(f"CORS allowed origins: {origins}")
logger.info(f"CORS allowed origin regex: {vercel_origin_regex}")

app.add_middleware(
	CORSMiddleware,
	allow_origins=origins,
	allow_origin_regex=vercel_origin_regex,
	allow_credentials=True,
	allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
	allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
	expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)


# ---------------------------------------------------------------------------
# API auth middleware — require Bearer token for non-localhost requests
# to mutation endpoints. Localhost/127.0.0.1 requests pass freely so the
# local frontend demo and dev workflow are unaffected.
#
# Architecture: User machines connect OUT to this server via outbound
# WebSocket (no tunnel, no exposed ports on the client side). Auth is
# validated on the WebSocket handshake and on HTTP mutation endpoints.
# ---------------------------------------------------------------------------

from starlette.middleware.base import BaseHTTPMiddleware

# Paths that are always public (read-only viewers, health, static, MCP has its own auth)
_PUBLIC_PREFIXES = (
    "/api/health", "/api/relay/status", "/demo/curated", "/static/", "/clips/", "/slides/",
    "/strategy-brief/", "/mcp-stream/", "/mcp/", "/docs",
    "/openapi.json", "/redoc", "/favicon",
    "/api/live-demo/", "/ws/live-browser",  # Live browser demo — public for hosted frontend
    "/api/surfaces", "/api/workflows/compression-stats",  # Read-only public data
)
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "testserver"}
_LOCAL_CLIENT_HOSTS = {"testclient"}


def _is_local_request(request: Request) -> bool:
    """Treat loopback + FastAPI TestClient traffic as local."""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    client_host = ((request.client.host if request.client else "") or "").lower()
    return host in _LOCAL_HOSTS or client_host in _LOCAL_HOSTS or client_host in _LOCAL_CLIENT_HOSTS


class APIAuthMiddleware(BaseHTTPMiddleware):
    """Require Bearer token for non-local requests to mutation endpoints."""

    async def dispatch(self, request: Request, call_next):
        import hmac

        # Always allow local loopback + TestClient traffic
        if _is_local_request(request):
            return await call_next(request)

        # Always allow safe methods and public paths
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)
        path = request.url.path
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        # Non-local POST/PUT/DELETE/PATCH — require token
        expected = os.getenv("RETENTION_MCP_TOKEN", "").strip()
        if not expected:
            return await call_next(request)  # No token configured — open mode

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                {"detail": "Authorization required for remote access"},
                status_code=401,
            )
        token = auth_header[7:].strip()
        if not hmac.compare_digest(token, expected):
            return JSONResponse(
                {"detail": "Invalid token"},
                status_code=401,
            )
        return await call_next(request)


app.add_middleware(APIAuthMiddleware)

# ── Global exception handler ─────────────────────────────────
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
	logger.error(f"Unhandled exception on {request.method} {request.url.path}: {exc}", exc_info=True)
	return JSONResponse(
		status_code=500,
		content={"detail": "Internal server error"},
	)

# Auto-generate MCP auth token if not set
if not os.getenv("RETENTION_MCP_TOKEN"):
    import secrets
    _token_path = Path(__file__).parent.parent.parent / ".claude" / "mcp-token"
    if _token_path.exists():
        _auto_token = _token_path.read_text().strip()
    else:
        _auto_token = secrets.token_hex(16)
        _token_path.parent.mkdir(parents=True, exist_ok=True)
        _token_path.write_text(_auto_token)
    os.environ["RETENTION_MCP_TOKEN"] = _auto_token
    logger.info("MCP auth token configured (stored in .claude/mcp-token)")

ADB_PATHS = [
	"adb",
	os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
	"/usr/local/bin/adb",
	"/opt/android-sdk/platform-tools/adb",
]

def find_adb() -> Optional[str]:
	for path in ADB_PATHS:
		try:
			result = subprocess.run([path, "version"], capture_output=True, timeout=2)
			if result.returncode == 0:
				return path
		except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
			pass
	return None

ADB_PATH = find_adb()
mobile_mcp_client: Optional[MobileMCPClient] = None
mobile_mcp_streaming: Optional[MobileMCPStreamingManager] = None

# Initialize Mobile MCP Client
try:
	mobile_mcp_client = MobileMCPClient()
	logger.info("Mobile MCP Client created (will start on first request)")
except Exception as e:
	logger.warning(f"Failed to create Mobile MCP client: {e}")

# Initialize Mobile MCP Streaming Manager
try:
	if mobile_mcp_client:
		mobile_mcp_streaming = MobileMCPStreamingManager(mobile_mcp_client)
		# Set global references in tools
		device_tools.set_mcp_client(mobile_mcp_client)
		appium_tools.set_mcp_client(mobile_mcp_client)
		appium_tools.set_streaming_manager(mobile_mcp_streaming)
		logger.info("Mobile MCP Streaming Manager initialized")
except Exception as e:
	logger.warning(f"Failed to initialize Mobile MCP Streaming Manager: {e}")

ai_agent_service: Optional[AIAgentService] = None
bug_reproduction_service = UnifiedBugReproductionService()  # Uses default: backend/bug_screenshots
logger.info("Unified Bug Reproduction Service initialized")

# Observability (LangSmith)
init_langsmith()

vector_search_service = VectorSearchService(api_key=os.getenv("OPENAI_API_KEY"))
logger.info("Vector Search Service initialized")
capabilities_config = {}
try:
	possible_paths = [
		Path("backend/capabilities.json"),
		Path("capabilities.json"),
		Path(__file__).parent.parent / "capabilities.json"
	]
	loaded = False
	for capabilities_path in possible_paths:
		if capabilities_path.exists():
			with open(capabilities_path, "r") as f:
				capabilities_config = json.load(f)
			logger.info(f"✅ Loaded capabilities configuration from {capabilities_path}")
			logger.info(f"✅ Found {len(capabilities_config)} top-level keys: {list(capabilities_config.keys())}")
			if "instagram_test_scenarios" in capabilities_config:
				logger.info(f"✅ Found {len(capabilities_config['instagram_test_scenarios'])} Instagram scenarios")
			loaded = True
			break

	if not loaded:
		logger.warning(f"❌ Could not find capabilities.json in any of: {possible_paths}")
except Exception as e:
	logger.warning(f"Failed to load capabilities: {e}")

if mobile_mcp_streaming:
	try:
		ai_agent_service = AIAgentService(
			mobile_mcp_streaming,
			capabilities_config,
			vector_search_service=vector_search_service,
			bug_reproduction_service=bug_reproduction_service
		)
		logger.info("AI Agent Service initialized with Mobile MCP, vector search, and unified bug reproduction")
	except Exception as e:
		logger.warning(f"Failed to initialize AI Agent Service: {e}")

def get_android_devices() -> List[str]:
	if not ADB_PATH:
		logger.warning("ADB not found")
		return []

	try:
		result = subprocess.run(
			[ADB_PATH, "devices"],
			capture_output=True,
			text=True,
			timeout=5
		)
		devices = []
		for line in result.stdout.split('\n')[1:]:
			if '\t' in line:
				device_id = line.split('\t')[0].strip()
				if device_id and device_id != "":
					devices.append(device_id)
		return devices
	except Exception as e:
		logger.error(f"Failed to get Android devices: {e}")
		return []

def take_android_screenshot(device_id: str, output_path: str) -> bool:
	if not ADB_PATH:
		logger.warning("ADB not found, creating mock screenshot")
		Path(output_path).parent.mkdir(parents=True, exist_ok=True)
		Path(output_path).touch()
		return True

	try:
		subprocess.run(
			[ADB_PATH, "-s", device_id, "shell", "screencap", "-p", "/sdcard/screenshot.png"],
			timeout=10,
			check=True
		)
		subprocess.run(
			[ADB_PATH, "-s", device_id, "pull", "/sdcard/screenshot.png", output_path],
			timeout=10,
			check=True
		)
		logger.info(f"Screenshot saved: {output_path}")
		return True
	except Exception as e:
		logger.error(f"Failed to take Android screenshot: {e}")
		Path(output_path).parent.mkdir(parents=True, exist_ok=True)
		Path(output_path).touch()
		return True

sessions_store: Dict[str, Dict[str, Any]] = {}
results_store: Dict[str, Dict[str, Any]] = {}
from .api import health as health_router
from .api import vector_search as vector_search_router
from .api import ai_agent as ai_agent_router
from .api import device_simulation as device_simulation_router
from .api import figma as figma_router
from .api import investor_brief as investor_brief_router
from .investor_brief import InvestorBriefService

investor_brief_service = InvestorBriefService()

health_router.set_stores(sessions_store, results_store)
vector_search_router.set_vector_search_service(vector_search_service)
device_simulation_router.set_bug_reproduction_service(bug_reproduction_service, capabilities_config)
investor_brief_router.set_investor_brief_service(investor_brief_service)

if mobile_mcp_client:
    device_simulation_router.set_mobile_mcp_client(mobile_mcp_client)
if mobile_mcp_streaming:
    device_simulation_router.set_mobile_mcp_streaming(mobile_mcp_streaming)

# Set ANDROID_HOME for emulator management
android_home = os.getenv("ANDROID_HOME") or os.path.expanduser("~/Library/Android/sdk")
if os.path.exists(android_home):
    device_simulation_router.set_android_home(android_home)
    logger.info(f"Android SDK configured at: {android_home}")
else:
    logger.warning(f"Android SDK not found at {android_home}. Emulator launch will not work.")

if ai_agent_service:
    ai_agent_router.set_ai_agent_service(ai_agent_service)

# Configure Figma service (optional)
figma_access_token = os.environ.get("FIGMA_ACCESS_TOKEN")
if figma_access_token:
    try:
        from .figma.client import FigmaClient
        from .figma.service import FigmaService

        _figma_client = FigmaClient(access_token=figma_access_token)
        figma_router.set_figma_service(FigmaService(client=_figma_client))
        logger.info("✅ Figma service configured")
    except Exception as e:
        logger.warning(f"⚠️  Failed to configure Figma service: {e}")
else:
    figma_router.set_figma_service(None)

from app.api import chat_routes as chat_router
app.include_router(chat_router.router)
app.include_router(health_router.router)
app.include_router(vector_search_router.router)
app.include_router(ai_agent_router.router)
app.include_router(device_simulation_router.router)
app.include_router(figma_router.router)
app.include_router(investor_brief_router.router)

# Import and include agent sessions router
# Serve screenshots directory as static files
screenshots_dir = Path(__file__).parent.parent / "screenshots"
screenshots_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/screenshots", StaticFiles(directory=str(screenshots_dir)), name="screenshots")
logger.info(f"📷 Serving screenshots from {screenshots_dir} at /static/screenshots")

from .api import agent_sessions as agent_sessions_router
from .api import benchmarks as benchmarks_router
from .api import benchmark_comparison as benchmark_comparison_router
from .api import benchmark_comprehensive as benchmark_comprehensive_router
from .api import harness_benchmark_routes as harness_benchmark_router
from .api import test_generation as test_generation_router
from .api import setup as setup_router
from .api import chef as chef_router
from .api import action_spans as action_spans_router
from .api import validation_hooks as validation_hooks_router
from .api import mcp_server as mcp_server_router
from .api import deep_agent as deep_agent_router
from .api import agent_runner_routes as agent_runner_router
from .api import self_test_runner as self_test_runner_router
from .api import demo as demo_router
from .api import workflow_registry as workflow_registry_router
from .api import device_leasing as device_leasing_router
from .api import perception_routes as perception_router
from .api import code_linkage_routes
from .api import dogfood_endpoints as dogfood_router
from .api import playground_endpoints as playground_router

# Trigger agent registrations (imports register configs with AgentRegistry)
from .agents.registry import agents as _agent_registrations  # noqa: F401
from .benchmarks import prd_router

mcp_server_router.set_investor_brief_service(investor_brief_service)
if ai_agent_service:
    self_test_runner_router.set_self_test_service(ai_agent_service)

# QA Pipeline for demo
if mobile_mcp_client:
    try:
        from .agents.qa_pipeline import QAPipelineService
        _qa_pipeline_service = QAPipelineService(mobile_mcp_client)
        demo_router.set_qa_pipeline_service(_qa_pipeline_service)
        # Also wire pipeline service into MCP tools for remote agent access
        from .api.mcp_pipeline import set_pipeline_service
        set_pipeline_service(_qa_pipeline_service)
        logger.info("QA Pipeline Service initialized for demo + MCP")
    except Exception as e:
        logger.warning(f"Failed to initialize QA Pipeline Service: {e}")

# Chef integration (optional — only active when OPENAI_API_KEY is set)
try:
    from .integrations.chef.runner import ChefRunner
    from .integrations.chef.config import ChefConfig

    _chef_config = ChefConfig(
        chef_dir=str(Path(__file__).parent.parent.parent / "integrations" / "chef"),
    )
    runner = ChefRunner(_chef_config)
    chef_router.set_chef_runner(runner)
    demo_router.set_chef_runner(runner)
    if ai_agent_service:
        ai_agent_service.set_chef_runner(runner)
    logger.info("🍳 Chef integration enabled (model=%s)", _chef_config.model)
except Exception as _chef_err:
    logger.warning("Chef integration disabled: %s", _chef_err)

app.include_router(agent_sessions_router.router)
app.include_router(benchmarks_router.router, prefix="/api")
app.include_router(benchmark_comparison_router.router, prefix="/api")
from app.api import workflow_judge as workflow_judge_router
app.include_router(workflow_judge_router.router)
app.include_router(benchmark_comprehensive_router.router, prefix="/api")
app.include_router(harness_benchmark_router.router)
app.include_router(prd_router.router)
app.include_router(test_generation_router.router)
app.include_router(setup_router.router)
app.include_router(chef_router.router)
app.include_router(action_spans_router.router, prefix="/api")
app.include_router(validation_hooks_router.router, prefix="/api")
app.include_router(mcp_server_router.router)
app.include_router(deep_agent_router.router)
app.include_router(agent_runner_router.router)
app.include_router(self_test_runner_router.router)
app.include_router(demo_router.router)
if mobile_mcp_client:
    workflow_registry_router.set_mobile_mcp_client(mobile_mcp_client)
app.include_router(workflow_registry_router.router)
app.include_router(device_leasing_router.router)
app.include_router(perception_router.router)
app.include_router(code_linkage_routes.router)
app.include_router(dogfood_router.router)
app.include_router(playground_router.router)

# Shareable reports — short-URL access to benchmark/pipeline results
from .api import reports as reports_router
app.include_router(reports_router.router)

# Agent relay — outbound WebSocket endpoint for thin relay connections
from .api import agent_relay as agent_relay_router
app.include_router(agent_relay_router.router)

# Feedback packages — structured QA reports for Claude Code consumption
from .api import feedback_package as feedback_package_router
app.include_router(feedback_package_router.router)

# Context Graph — unified execution judgment infrastructure
from .api import context_graph_routes as context_graph_router
app.include_router(context_graph_router.router)

# Signup & token generation
from .api import signup as signup_router
app.include_router(signup_router.router)

# External tool benchmark comparison (retention.sh vs vanilla tools)
from .api import benchmark_external as benchmark_external_router
app.include_router(benchmark_external_router.router)

# GIF Replay — animated replay GIFs for pipeline runs
from .api import replay_routes as replay_router
app.include_router(replay_router.router)

# Kanban Board — team task tracking with SSE sync
from .api.board_routes import router as board_router
app.include_router(board_router)

# Agent Analytics — Claude Code tool call analysis
from .api import analytics_routes
app.include_router(analytics_routes.router)

# ROP Distillation — Frontier Discovery → Cheap Replay
from .api import rop_routes as rop_router
app.include_router(rop_router.router)

# DRX Delta Refresh Benchmark — live API-backed research replay evaluation
try:
    from .api import drx_benchmark_routes as drx_bench_router
    app.include_router(drx_bench_router.router)
except ImportError:
    pass  # Optional — drx_benchmark_routes may not exist yet

# QR Codes — shareable QR images for team invites, dashboards, benchmarks
from .api import qr_routes as qr_router
app.include_router(qr_router.router)

# Quick Actions — one-click convenience endpoints for common operations
from .api import quick_actions as quick_actions_router
app.include_router(quick_actions_router.router)

# Live Stats — verified aggregated stats from actual data files (no fabrication)
from .api import live_stats as live_stats_router
app.include_router(live_stats_router.router)

# Trace Compare — tool-call-level baseline vs replay comparison
from .api import trace_compare as trace_compare_router
app.include_router(trace_compare_router.router)

# Workflow Judge — always-on completion judge with Claude Code hook integration
from .api import judge_routes as judge_router
app.include_router(judge_router.router)

# Trajectory API — serves real trajectory data for compliance trace UI
from .api import trajectory_routes as trajectory_router
app.include_router(trajectory_router.router)

# Flywheel Cycle API — dev cycle management with distillation + streaming
from .api import flywheel_routes as flywheel_router
app.include_router(flywheel_router.router)

# OTEL Receiver — accept OpenTelemetry traces from any instrumented framework
from .api import otel_receiver
app.include_router(otel_receiver.router)

# ── Streamable HTTP MCP (spec-compliant, no proxy needed) ─────
# Claude Code connects with just: {"type":"http","url":"https://host/mcp"}
try:
    from .api.mcp_streamable import create_mcp_app
    _mcp_app = create_mcp_app()
    app.mount("/mcp-stream", _mcp_app)
    logger.info("Streamable HTTP MCP mounted at /mcp-stream")
except Exception as e:
    logger.warning(f"Failed to mount Streamable HTTP MCP: {e}")

# ── Pipeline Results Viewer (self-contained HTML) ─────────────
# Serves at /demo/curated so remote users can view results without a frontend server

@app.get("/demo/curated")
async def curated_results_viewer(request: Request, run: str = ""):
    """Self-contained HTML viewer for pipeline results.

    Accessible via the server URL so remote MCP users can click view_url
    and see results without needing the React frontend.
    """
    from .api.mcp_pipeline import _running_pipelines, _persisted_results

    # If a run is active, try SSE streaming mode
    is_live = run and run in _running_pipelines and _running_pipelines[run]["status"] == "running"
    stream_url = f"/api/demo/pipeline-stream/{run}" if is_live else ""

    # Load result data
    result_json = "{}"
    if run:
        entry = _running_pipelines.get(run)
        if entry and entry.get("result"):
            import json as _json
            result_json = _json.dumps(entry["result"], default=str)
        else:
            persisted = _persisted_results.get(run)
            if persisted:
                import json as _json
                r = persisted.get("result", persisted)
                result_json = _json.dumps(r, default=str)
            else:
                try:
                    from .api.demo import _pipeline_results
                    stored = _pipeline_results.get(run)
                    if stored:
                        import json as _json
                        result_json = _json.dumps(stored.get("result", stored), default=str)
                except Exception:
                    pass

    # List all available runs for the sidebar
    all_runs = []
    for rid, entry in _running_pipelines.items():
        if entry["status"] in ("complete", "error") and entry.get("result"):
            all_runs.append({"run_id": rid, "app_name": entry.get("app_name", ""), "status": entry["status"]})
    for rid, stored in _persisted_results.items():
        if rid not in {r["run_id"] for r in all_runs}:
            all_runs.append({"run_id": rid, "app_name": stored.get("app_name", ""), "status": "complete"})

    import json as _json
    all_runs_json = _json.dumps(all_runs, default=str)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>retention.sh — Pipeline Results</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0a0a0a; color: #e0e0e0; }}
  .header {{ background: #111; border-bottom: 1px solid #222; padding: 16px 24px; display: flex; align-items: center; gap: 16px; }}
  .header h1 {{ font-size: 18px; font-weight: 600; color: #fff; }}
  .header .badge {{ background: #1a1a2e; color: #7c8aff; padding: 4px 10px; border-radius: 12px; font-size: 12px; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
  .meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .meta-card {{ background: #141414; border: 1px solid #222; border-radius: 8px; padding: 16px; }}
  .meta-card .label {{ color: #888; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .meta-card .value {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}
  .section {{ margin-bottom: 32px; }}
  .section h2 {{ font-size: 16px; font-weight: 600; margin-bottom: 12px; color: #ccc; }}
  .test-card {{ background: #141414; border: 1px solid #222; border-radius: 8px; padding: 16px; margin-bottom: 8px; }}
  .test-card .test-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }}
  .test-card .test-id {{ font-family: monospace; font-size: 12px; color: #888; }}
  .test-card .test-name {{ font-weight: 500; }}
  .test-card .priority {{ padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
  .priority-P0 {{ background: #3d1111; color: #ff6b6b; }}
  .priority-P1 {{ background: #3d2c11; color: #ffa94d; }}
  .priority-P2 {{ background: #11303d; color: #74c0fc; }}
  .priority-P3 {{ background: #1a3d11; color: #69db7c; }}
  .steps {{ margin-top: 8px; }}
  .step {{ display: flex; gap: 8px; padding: 6px 0; border-top: 1px solid #1a1a1a; font-size: 13px; }}
  .step-num {{ color: #555; min-width: 24px; }}
  .step-action {{ flex: 1; }}
  .step-expected {{ color: #888; flex: 1; }}
  .workflow {{ background: #141414; border: 1px solid #222; border-radius: 8px; padding: 16px; margin-bottom: 8px; }}
  .workflow h3 {{ font-size: 14px; margin-bottom: 8px; }}
  .workflow .desc {{ color: #999; font-size: 13px; }}
  .live-banner {{ background: #1a2d11; border: 1px solid #2d5a1a; border-radius: 8px; padding: 16px; margin-bottom: 24px; display: flex; align-items: center; gap: 12px; }}
  .live-dot {{ width: 10px; height: 10px; background: #4caf50; border-radius: 50%; animation: pulse 1.5s infinite; }}
  @keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.3; }} }}
  .empty {{ text-align: center; padding: 60px; color: #666; }}
  .runs-list {{ margin-bottom: 24px; }}
  .runs-list a {{ color: #7c8aff; text-decoration: none; margin-right: 16px; font-size: 13px; }}
  .runs-list a:hover {{ text-decoration: underline; }}
  .runs-list a.active {{ color: #fff; font-weight: 600; }}
</style>
</head>
<body>
<div class="header">
  <h1>retention.sh</h1>
  <span class="badge">Pipeline Results</span>
  {"<span class='badge' style='background:#1a2d11;color:#4caf50'>LIVE</span>" if is_live else ""}
</div>
<div class="container">
  <div class="runs-list" id="runs-list"></div>
  {"<div class='live-banner'><div class='live-dot'></div><span>Pipeline is running live — results will appear as tests complete</span></div>" if is_live else ""}
  <div id="content"></div>
</div>
<script>
const runId = "{run}" || "";
const allRuns = {all_runs_json};
const streamUrl = "{stream_url}";
let resultData;
try {{ resultData = {result_json}; }} catch(e) {{ resultData = null; }}

// Render run list
const runsList = document.getElementById('runs-list');
if (allRuns.length > 0) {{
  allRuns.forEach(r => {{
    const a = document.createElement('a');
    a.href = '/demo/curated?run=' + r.run_id;
    a.textContent = (r.app_name || r.run_id).substring(0, 30);
    if (r.run_id === runId) a.className = 'active';
    runsList.appendChild(a);
  }});
}}

// Safe DOM helpers — no innerHTML, prevents XSS from LLM output
function el(tag, attrs, ...children) {{
  const e = document.createElement(tag);
  if (attrs) Object.entries(attrs).forEach(([k, v]) => {{
    if (k === 'className') e.className = v;
    else if (k === 'style') e.style.cssText = v;
    else e.setAttribute(k, v);
  }});
  children.forEach(ch => {{
    if (typeof ch === 'string') e.appendChild(document.createTextNode(ch));
    else if (ch) e.appendChild(ch);
  }});
  return e;
}}

function renderResults(data) {{
  const c = document.getElementById('content');
  c.textContent = '';
  if (!data || (typeof data === 'object' && Object.keys(data).length === 0)) {{
    c.appendChild(el('div', {{className: 'empty'}}, runId ? 'No results yet. Pipeline may still be running.' : 'No results yet. Select a run or start one via MCP.'));
    return;
  }}

  const testCases = data.test_cases || [];
  const workflows = data.workflows || [];
  const appName = data.app_name || runId;

  const meta = el('div', {{className: 'meta'}},
    el('div', {{className: 'meta-card'}}, el('div', {{className: 'label'}}, 'App'), el('div', {{className: 'value', style: 'font-size:16px'}}, appName)),
    el('div', {{className: 'meta-card'}}, el('div', {{className: 'label'}}, 'Test Cases'), el('div', {{className: 'value'}}, String(testCases.length))),
    el('div', {{className: 'meta-card'}}, el('div', {{className: 'label'}}, 'Workflows'), el('div', {{className: 'value'}}, String(workflows.length))),
    el('div', {{className: 'meta-card'}}, el('div', {{className: 'label'}}, 'Run ID'), el('div', {{className: 'value', style: 'font-size:12px;font-family:monospace'}}, runId))
  );
  c.appendChild(meta);

  if (workflows.length > 0) {{
    const sec = el('div', {{className: 'section'}}, el('h2', null, 'Workflows'));
    workflows.forEach((w, i) => {{
      const name = w.name || w.workflow_name || ('Workflow ' + (i+1));
      const desc = w.description || w.workflow_description || '';
      sec.appendChild(el('div', {{className: 'workflow'}}, el('h3', null, name), el('div', {{className: 'desc'}}, desc)));
    }});
    c.appendChild(sec);
  }}

  if (testCases.length > 0) {{
    const sec = el('div', {{className: 'section'}}, el('h2', null, 'Test Cases'));
    testCases.forEach(tc => {{
      const header = el('div', {{className: 'test-header'}},
        el('span', {{className: 'test-id'}}, tc.test_id || ''),
        el('span', {{className: 'priority priority-' + (tc.priority || 'P2')}}, tc.priority || 'P2'),
        el('span', {{className: 'test-name'}}, tc.name || '')
      );
      const card = el('div', {{className: 'test-card'}}, header);
      if (tc.steps && tc.steps.length > 0) {{
        const stepsDiv = el('div', {{className: 'steps'}});
        tc.steps.forEach(s => {{
          stepsDiv.appendChild(el('div', {{className: 'step'}},
            el('span', {{className: 'step-num'}}, String(s.step_number || '')),
            el('span', {{className: 'step-action'}}, s.action || ''),
            el('span', {{className: 'step-expected'}}, s.expected_result || '')
          ));
        }});
        card.appendChild(stepsDiv);
      }}
      sec.appendChild(card);
    }});
    c.appendChild(sec);
  }}
}}

renderResults(resultData);

// If live, connect to SSE stream
if (streamUrl) {{
  const es = new EventSource(streamUrl);
  es.addEventListener('stream_end', () => {{
    // Reload to get final results
    setTimeout(() => window.location.reload(), 1000);
  }});
  es.onerror = () => {{
    setTimeout(() => window.location.reload(), 3000);
  }};
}}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)

# Remote computer control via Slack
from .api import remote_control_routes as remote_control_router
app.include_router(remote_control_router.router)

from .api import telegram_routes as telegram_router
app.include_router(telegram_router.router)

from .api import auth_routes as auth_router
app.include_router(auth_router.router)

from .api import admin_routes as admin_router
app.include_router(admin_router.router)


# ── WebSocket: Live Playwright browser stream ─────────────────

@app.websocket("/ws/live-browser")
async def live_browser_ws(websocket: WebSocket):
    """Stream live Playwright screenshots to the browser via WebSocket.

    Connect, then POST /api/live-demo/start to begin a flow.
    Receives base64 JPEG frames + JSON event messages.
    """
    from .agents.self_testing.playwright_engine import subscribe_screenshots, unsubscribe_screenshots
    import asyncio as _asyncio

    await websocket.accept()
    logger.info("[WS] Live browser stream connected")

    q = subscribe_screenshots()
    try:
        while True:
            try:
                frame = await _asyncio.wait_for(q.get(), timeout=30.0)
                await websocket.send_json({"type": "frame", "data": frame})
            except _asyncio.TimeoutError:
                # Keep-alive ping
                await websocket.send_json({"type": "ping"})
            except Exception:
                break
    except Exception:
        pass
    finally:
        unsubscribe_screenshots(q)
        logger.info("[WS] Live browser stream disconnected")


# Active live demo state
_live_demo_task: Optional[asyncio.Task] = None
_live_demo_events: list = []


@app.post("/api/live-demo/start")
async def start_live_demo(request: Request):
    """Start a live Playwright demo flow on a URL. Screenshots stream via /ws/live-browser."""
    global _live_demo_task, _live_demo_events

    body = await request.json()
    url = body.get("url", "").strip()
    if not url:
        return {"error": "url_required"}
    if not url.startswith("http"):
        url = f"https://{url}"

    # Cancel any existing demo
    if _live_demo_task and not _live_demo_task.done():
        _live_demo_task.cancel()

    _live_demo_events = []

    async def _run_demo():
        from .agents.self_testing.playwright_engine import pw_discover
        try:
            _live_demo_events.append({"type": "started", "url": url})
            result = await pw_discover(url)
            _live_demo_events.append({"type": "completed", "result": result})
        except Exception as e:
            _live_demo_events.append({"type": "error", "error": str(e)})

    _live_demo_task = asyncio.create_task(_run_demo())

    return {"status": "started", "url": url, "ws": "/ws/live-browser"}


@app.post("/api/live-demo/crawl-sitemap")
async def crawl_sitemap(request: Request):
    """Crawl a URL and return a visual site map with screenshots.

    Returns screens with base64 JPEG thumbnails, element counts,
    and navigation transitions — ready for the SiteMemoryMap component.
    """
    import base64 as _b64

    body = await request.json()
    url = body.get("url", "").strip()
    if not url:
        return {"error": "url_required"}
    if not url.startswith("http"):
        url = f"https://{url}"

    try:
        from playwright.async_api import async_playwright
        from urllib.parse import urlparse

        parsed = urlparse(url)
        base_origin = f"{parsed.scheme}://{parsed.netloc}"
        screens = []
        transitions = []
        visited = set()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--single-process"])
            context = await browser.new_context(viewport={"width": 1280, "height": 720})
            page = await context.new_page()
            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text[:150]) if msg.type == "error" else None)
            page.on("pageerror", lambda err: console_errors.append(str(err)[:150]))

            # Use domcontentloaded — sites with persistent WebSocket connections
            # (Convex, Firebase, Supabase) never reach networkidle
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(5000)  # Extra wait for SPA hydration

            # Helper to capture a page
            async def capture_page(page_url: str, depth: int, name: str = ""):
                if page_url.rstrip("/") in visited:
                    return
                visited.add(page_url.rstrip("/"))

                try:
                    if page.url.rstrip("/") != page_url.rstrip("/"):
                        await page.goto(page_url, wait_until="domcontentloaded", timeout=10000)
                        await page.wait_for_timeout(3000)

                    title = await page.title()
                    screenshot = await page.screenshot(type="jpeg", quality=50)
                    b64 = _b64.b64encode(screenshot).decode("ascii")

                    # Extract elements
                    from .agents.self_testing.playwright_engine import EXTRACT_JS
                    elements = await page.evaluate(EXTRACT_JS)

                    path = urlparse(page.url).path or "/"
                    screen_name = name or title or path

                    interactive = [e for e in elements if e.get("type") in ("button", "link", "input")]
                    screen_id = path.replace("/", "_").strip("_") or "home"

                    screens.append({
                        "screen_id": screen_id,
                        "screen_name": screen_name[:50],
                        "navigation_depth": depth,
                        "screenshot_b64": b64,
                        "url": page.url,
                        "components": [
                            {
                                "element_id": f"{screen_id}_{i}",
                                "element_type": e.get("type", "unknown"),
                                "label": (e.get("text") or e.get("href", ""))[:80],
                                "is_interactive": e.get("type") in ("button", "link", "input"),
                            }
                            for i, e in enumerate(elements[:20])
                        ],
                    })

                    # Find same-origin links for next depth
                    if depth < 1:
                        links = [e for e in elements if e.get("type") == "link" and e.get("href", "").startswith(base_origin)]
                        for link in links[:8]:
                            href = link["href"].rstrip("/")
                            if href not in visited:
                                target_path = urlparse(href).path or "/"
                                target_id = target_path.replace("/", "_").strip("_") or "home"
                                transitions.append({
                                    "from_screen": screen_id,
                                    "to_screen": target_id,
                                    "action": f'Click "{link.get("text", "link")[:30]}"',
                                })
                                await capture_page(href, depth + 1, link.get("text", ""))

                except Exception as e:
                    logger.debug(f"Failed to capture {page_url}: {e}")

            await capture_page(url, 0)
            await browser.close()

        # Generate QA findings from crawl data
        findings = []
        total_components = sum(len(s["components"]) for s in screens)

        if console_errors:
            findings.append({
                "severity": "error",
                "category": "javascript",
                "title": f"{len(console_errors)} JavaScript error(s) detected",
                "detail": console_errors[0][:200],
                "recommendation": "Fix JS errors — the app may not render correctly in automated browsers, bots, or older devices.",
            })

        if total_components == 0 and screens:
            findings.append({
                "severity": "warning",
                "category": "rendering",
                "title": "No interactive elements found",
                "detail": "The page rendered but no links, buttons, or inputs were detected. This may indicate a client-side rendering failure.",
                "recommendation": "Check if the app works in headless Chrome. SSR (server-side rendering) improves crawlability and SEO.",
            })

        empty_screens = [s for s in screens if len(s["components"]) == 0]
        if empty_screens and total_components > 0:
            findings.append({
                "severity": "warning",
                "category": "navigation",
                "title": f"{len(empty_screens)} page(s) have no interactive elements",
                "detail": ", ".join(s["screen_name"][:30] for s in empty_screens[:5]),
                "recommendation": "These pages may be loading states, error pages, or dynamically rendered content that didn't finish loading.",
            })

        if len(screens) == 1 and len(transitions) == 0:
            findings.append({
                "severity": "info",
                "category": "structure",
                "title": "Single-page app detected",
                "detail": "Only 1 page was discovered. The site may use client-side routing that headless crawlers can't follow automatically.",
                "recommendation": "Install retention.sh locally for deeper SPA crawling with full JavaScript execution and interaction.",
            })

        no_a11y_labels = sum(1 for s in screens for c in s["components"] if c["is_interactive"] and not c["label"].strip())
        if no_a11y_labels > 0:
            findings.append({
                "severity": "warning",
                "category": "accessibility",
                "title": f"{no_a11y_labels} interactive element(s) missing labels",
                "detail": "Buttons or links without text, aria-label, or title attributes.",
                "recommendation": "Add aria-label or visible text to all interactive elements for accessibility compliance.",
            })

        return {
            "status": "ok",
            "url": url,
            "screens": screens,
            "transitions": transitions,
            "total_screens": len(screens),
            "total_components": total_components,
            "total_transitions": len(transitions),
            "console_errors": console_errors[:10],
            "findings": findings,
            "findings_count": len(findings),
        }

    except Exception as e:
        logger.error(f"Crawl sitemap failed: {e}")
        return {"error": str(e)}


@app.get("/api/live-demo/status")
async def live_demo_status():
    """Get current live demo status."""
    global _live_demo_task, _live_demo_events
    running = _live_demo_task is not None and not _live_demo_task.done()
    return {
        "running": running,
        "events": _live_demo_events[-20:],  # last 20 events
    }


@app.post("/api/live-demo/stop")
async def stop_live_demo():
    """Stop the current live demo."""
    global _live_demo_task
    if _live_demo_task and not _live_demo_task.done():
        _live_demo_task.cancel()
        return {"status": "stopped"}
    return {"status": "not_running"}


# ── WebSocket: Real-time benchmark streaming ─────────────────

@app.websocket("/ws/benchmark/{suite_id}")
async def benchmark_ws(websocket: WebSocket, suite_id: str):
    """Push benchmark execution progress to connected clients at 500ms intervals."""
    from .api.benchmark_comparison import _active_runs
    import asyncio as _asyncio

    await websocket.accept()
    logger.info(f"[WS] Benchmark stream connected for suite {suite_id}")
    last_completed = 0

    try:
        while True:
            run = _active_runs.get(suite_id)
            if not run:
                await websocket.send_json({"type": "error", "message": "Suite not found"})
                break

            completed = run.get("completed_tasks", 0)
            total = run.get("total_work", 0)
            status = run.get("status", "unknown")

            # Send progress update
            if completed != last_completed or status in ("completed", "failed"):
                await websocket.send_json({
                    "type": "progress",
                    "completed": completed,
                    "total": total,
                    "status": status,
                })
                last_completed = completed

            if status == "completed":
                await websocket.send_json({"type": "completed", "suite_id": suite_id})
                break
            elif status == "failed":
                await websocket.send_json({
                    "type": "error",
                    "message": run.get("error", "Unknown error"),
                })
                break

            await _asyncio.sleep(0.5)
    except Exception as exc:
        logger.debug(f"[WS] Benchmark stream closed: {exc}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# Serve benchmark run artifacts as static files
benchmark_runs_dir = Path(__file__).parent.parent / "data" / "benchmark_runs"
benchmark_runs_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/benchmark_runs", StaticFiles(directory=str(benchmark_runs_dir)), name="benchmark_runs")
logger.info(f"Serving benchmark artifacts from {benchmark_runs_dir} at /static/benchmark_runs")

# Serve strategy brief and other tmp files
tmp_dir = Path(__file__).parent.parent.parent / "tmp"
if tmp_dir.exists():
    app.mount("/strategy-brief", StaticFiles(directory=str(tmp_dir), html=True), name="strategy-brief")
    logger.info(f"Serving strategy brief from {tmp_dir} at /strategy-brief")


# ---------------------------------------------------------------------------
# Serve ActionSpan video clips
# ---------------------------------------------------------------------------
clips_dir = Path(__file__).parent.parent / "clips"
clips_dir.mkdir(parents=True, exist_ok=True)
app.mount("/clips", StaticFiles(directory=str(clips_dir)), name="clips")
logger.info(f"Serving ActionSpan clips from {clips_dir} at /clips")

# Serve generated slide decks as HTML pages (opens in browser, no download needed)
# Slides are saved to PROJECT_ROOT/slides/ by slide_generator.py
slides_dir = Path(__file__).parent.parent.parent / "slides"
slides_dir.mkdir(parents=True, exist_ok=True)
app.mount("/slides", StaticFiles(directory=str(slides_dir), html=True), name="slides")
logger.info(f"Serving slide decks from {slides_dir} at /slides")


@app.get("/api/clips/{session_id}/{span_id}/clip.mp4")
async def get_clip(session_id: str, span_id: str):
    """Serve an ActionSpan video clip with proper video headers."""
    clip_path = clips_dir / "action_spans" / session_id / span_id / "clip.mp4"
    if not clip_path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Clip not found: {session_id}/{span_id}")
    return FileResponse(
        path=str(clip_path),
        media_type="video/mp4",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600",
        },
    )


@app.get("/api/clips/{session_id}/{span_id}/{filename}")
async def get_clip_artifact(session_id: str, span_id: str, filename: str):
    """Serve any file from an ActionSpan directory (before.png, after.png, frames, etc.)."""
    # Sanitise filename to prevent path traversal
    safe_name = Path(filename).name
    artifact_path = clips_dir / "action_spans" / session_id / span_id / safe_name
    if not artifact_path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Artifact not found: {filename}")

    # Infer media type from extension
    suffix = artifact_path.suffix.lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".json": "application/json",
    }
    media_type = media_types.get(suffix, "application/octet-stream")

    return FileResponse(
        path=str(artifact_path),
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.websocket("/ws/ai-agent/simulation/{simulation_id}")
async def simulation_websocket(websocket: WebSocket, simulation_id: str):
    await ai_agent_router.simulation_websocket_handler(websocket, simulation_id)

@app.get("/api/countdown/{seconds}")
async def countdown(seconds: int):
	target = datetime.now(timezone.utc) + timedelta(seconds=seconds)
	return {"seconds": seconds, "target": target.isoformat()}


async def _build_app_graph(app_key: str) -> dict:
    """Shared logic: load crawl JSON and return graph payload with SOM + clean screenshot URLs."""
    from .agents.qa_pipeline.exploration_memory import normalize_crawl_payload

    crawl_dir = Path(__file__).resolve().parents[1] / "data" / "exploration_memory" / "crawl"
    crawl_file = crawl_dir / f"{app_key}.json"
    if not crawl_file.exists():
        return {"error": "not_found", "app_key": app_key}

    with open(crawl_file) as f:
        data = json.load(f)

    crawl = normalize_crawl_payload(data.get("crawl_data", {}), app_key=app_key)
    screens_raw = crawl.get("screens", [])
    transitions_raw = crawl.get("transitions", [])

    # Convert screenshot absolute paths to served URLs (SOM annotated + clean versions)
    screens = []
    for crawl_index, s in enumerate(screens_raw):
        spath = s.get("screenshot_path", "")
        fname = Path(spath).name if spath else ""
        # Derive clean (no SOM overlay) filename by removing the _annotated suffix
        clean_fname = fname.replace("_annotated", "") if fname else ""
        components = s.get("components", [])
        screens.append({
            "screen_id": s.get("screen_id", ""),
            "screen_name": s.get("screen_name", ""),
            "screenshot_url": f"/static/screenshots/{fname}" if fname else "",
            "clean_screenshot_url": f"/static/screenshots/{clean_fname}" if clean_fname else "",
            "screenshot_description": s.get("screenshot_description", ""),
            "navigation_depth": s.get("navigation_depth", 0),
            "parent_screen_id": s.get("parent_screen_id"),
            "crawl_index": crawl_index,
            "trigger_action": s.get("trigger_action", ""),
            "component_count": len(components),
            "interactive_count": sum(1 for c in components if c.get("is_interactive")),
            "interactive_elements": [
                {"type": c.get("element_type", ""), "text": c.get("text", ""), "action": c.get("action", "")}
                for c in components if c.get("is_interactive")
            ][:8],
        })

    transitions = [
        {
            "from_screen": t.get("from_screen", ""),
            "to_screen": t.get("to_screen", ""),
            "action": t.get("action", ""),
            "edge_type": t.get("edge_type", "action"),
        }
        for t in transitions_raw
        if t.get("from_screen") and t.get("to_screen")
    ]

    hierarchy_edges = []
    for s in screens:
        if s["parent_screen_id"]:
            hierarchy_edges.append({
                "from_screen": s["parent_screen_id"],
                "to_screen": s["screen_id"],
                "action": s["trigger_action"],
                "edge_type": "hierarchy",
            })

    hierarchy_pairs = {(e["from_screen"], e["to_screen"]) for e in hierarchy_edges}
    transitions = [t for t in transitions if (t["from_screen"], t["to_screen"]) not in hierarchy_pairs]

    depths = [s["navigation_depth"] for s in screens]
    max_depth = max(depths) if depths else 0

    return {
        "app_key": app_key,
        "app_name": data.get("app_name", ""),
        "app_url": data.get("app_url", ""),
        "crawl_fingerprint": data.get("crawl_fingerprint", ""),
        "total_screens": len(screens),
        "total_components": sum(s["component_count"] for s in screens),
        "max_depth": max_depth,
        "screen_graph": data.get("screen_graph", {}),
        "screens": screens,
        "transitions": transitions,
        "hierarchy_edges": hierarchy_edges,
    }


@app.get("/api/demo/memory/app/{app_key}/graph")
async def demo_memory_app_graph(app_key: str):
    """Demo route — crawl graph with SOM + clean screenshot URLs for the Live Memory Demo page."""
    return await _build_app_graph(app_key)


@app.get("/api/memory/app/{app_key}/graph")
async def memory_app_graph(app_key: str):
    """Legacy route — kept for backward compatibility with existing dashboard."""
    return await _build_app_graph(app_key)


@app.get("/api/memory/dashboard")
async def memory_dashboard():
    """Exploration memory dashboard — compounding value metrics."""
    from .agents.qa_pipeline.exploration_memory import get_memory_stats

    memory = get_memory_stats()

    # Scan pipeline results for run history
    results_dir = Path(__file__).resolve().parents[1] / "data" / "pipeline_results"
    runs = []
    if results_dir.exists():
        for p in sorted(results_dir.glob("*.json"), key=lambda x: x.stat().st_mtime):
            try:
                with open(p) as f:
                    d = json.load(f)
                token_metrics = d.get("token_metrics", d.get("result", {}).get("token_usage", {}))
                runs.append({
                    "run_id": d.get("run_id", p.stem),
                    "app_name": d.get("app_name", ""),
                    "flow_type": d.get("flow_type", ""),
                    "started_at": d.get("started_at", ""),
                    "duration_s": d.get("duration_s"),
                    "tokens": token_metrics.get("total_tokens", 0),
                    "cost_usd": token_metrics.get("estimated_cost_usd", 0.0),
                    "event_count": d.get("event_count", 0),
                    "status": d.get("status", "complete"),
                    "stages_skipped": d.get("skipped_stages", []),
                    "is_rerun": "rerun" in str(d.get("run_id", "")),
                })
            except Exception:
                pass

    # Compute savings summary
    total_cost = sum(r["cost_usd"] for r in runs if r["cost_usd"])
    rerun_count = sum(1 for r in runs if r["is_rerun"])
    memory_hit_count = sum(1 for r in runs if r.get("stages_skipped"))

    return {
        "memory": memory,
        "runs": runs,
        "savings": {
            "total_tokens_saved": memory.get("estimated_tokens_saved", 0),
            "total_cost_all_runs": round(total_cost, 6),
            "total_runs": len(runs),
            "rerun_count": rerun_count,
            "memory_hit_count": memory_hit_count,
            "hit_rate": memory.get("hit_rate", 0),
        },
    }


# ── Trajectory & Savings API ────────────────────────────────────────

@app.get("/api/trajectories")
async def list_trajectories():
    """List all saved trajectories with summary metadata."""
    from .agents.device_testing.trajectory_logger import get_trajectory_logger
    tl = get_trajectory_logger()
    return {"trajectories": tl.list_all_trajectories()}


@app.get("/api/trajectories/{trajectory_id}")
async def get_trajectory(trajectory_id: str, task_name: str = ""):
    """Get a single trajectory by ID. Searches all tasks if task_name not provided."""
    from .agents.device_testing.trajectory_logger import get_trajectory_logger
    from dataclasses import asdict
    tl = get_trajectory_logger()
    if task_name:
        traj = tl.load_trajectory(task_name, trajectory_id)
        if traj:
            return asdict(traj)
        return {"error": f"Trajectory {trajectory_id} not found in {task_name}"}
    # Search all task dirs
    base = tl._base_dir
    if base.exists():
        for task_dir in base.iterdir():
            if not task_dir.is_dir() or task_dir.name.startswith("_"):
                continue
            traj = tl.load_trajectory(task_dir.name, trajectory_id)
            if traj:
                return asdict(traj)
    return {"error": f"Trajectory {trajectory_id} not found"}


@app.get("/api/savings/comparison")
async def savings_comparison(run_id: str = ""):
    """Get full-crawl vs replay comparison for a specific run or latest."""
    from .agents.qa_pipeline.trajectory_replay import get_replay_result, get_replay_results, FULL_RUN_BASELINE
    if run_id:
        result = get_replay_result(run_id)
        if not result:
            return {"error": f"Replay result {run_id} not found"}
    else:
        results = get_replay_results()
        result = results[0] if results else None
    if not result:
        return {"error": "No replay results found"}
    comp = result.get("comparison_with_full", {})
    return {
        "run_id": result.get("replay_run_id", run_id),
        "full_run": {
            "tokens": FULL_RUN_BASELINE["tokens"],
            "time_seconds": FULL_RUN_BASELINE["time_seconds"],
            "api_calls": FULL_RUN_BASELINE["api_calls"],
            "cost_usd": FULL_RUN_BASELINE["cost_usd"],
        },
        "replay": {
            "tokens": result.get("token_usage", {}).get("estimated_replay_tokens", 0),
            "time_seconds": result.get("time_seconds", 0),
            "api_calls": result.get("steps_executed", 0),
            "cost_usd": round(result.get("token_usage", {}).get("estimated_replay_tokens", 0) * 0.40 / 1_000_000, 6),
        },
        "savings": {
            "token_savings_pct": comp.get("token_savings_pct", 0),
            "time_savings_pct": comp.get("time_savings_pct", 0),
        },
        "drift_score": result.get("drift_score", 0),
        "success": result.get("success", False),
    }


@app.get("/api/savings/aggregate")
async def savings_aggregate():
    """Aggregate savings metrics across all replay results."""
    from .agents.qa_pipeline.trajectory_replay import get_savings_aggregate
    return get_savings_aggregate()


@app.get("/api/savings/team")
async def savings_team():
    """Team-level savings breakdown (aggregated from local data, per-member attribution)."""
    from .agents.qa_pipeline.trajectory_replay import get_savings_aggregate, get_replay_results
    from .agents.device_testing.trajectory_logger import get_trajectory_logger
    aggregate = get_savings_aggregate()
    tl = get_trajectory_logger()
    trajectories = tl.list_all_trajectories()
    replays = get_replay_results()

    # Build per-member stats from metadata
    member_stats = {}  # email -> {runs, tokens_saved, time_saved_s, trajectories_shared, last_active}

    # Count trajectories created per member
    for t in trajectories:
        creator = t.get("created_by", "local")
        if creator not in member_stats:
            member_stats[creator] = {"runs": 0, "tokens_saved": 0, "time_saved_s": 0.0,
                                     "trajectories_shared": 0, "last_active": "", "hit_rate": 0.0}
        member_stats[creator]["trajectories_shared"] += 1

    # Count runs and savings per member from replay results
    for r in replays:
        meta = r.get("metadata", {})
        peer = meta.get("replayed_by") or meta.get("created_by") or "local"
        if peer not in member_stats:
            member_stats[peer] = {"runs": 0, "tokens_saved": 0, "time_saved_s": 0.0,
                                  "trajectories_shared": 0, "last_active": "", "hit_rate": 0.0}
        member_stats[peer]["runs"] += 1
        comp = r.get("comparison_with_full", {})
        tokens_saved = max(0, comp.get("tokens_full", 0) - comp.get("tokens_replay", 0))
        time_saved = max(0, comp.get("time_full_s", 0) - comp.get("time_replay_s", 0))
        member_stats[peer]["tokens_saved"] += tokens_saved
        member_stats[peer]["time_saved_s"] += round(time_saved, 1)
        ts = r.get("timestamp", "")
        if ts > member_stats[peer]["last_active"]:
            member_stats[peer]["last_active"] = ts

    # Compute hit rates
    for email, stats in member_stats.items():
        if stats["runs"] > 0:
            member_replays = [r for r in replays
                              if (r.get("metadata", {}).get("replayed_by") or
                                  r.get("metadata", {}).get("created_by") or "local") == email]
            successful = sum(1 for r in member_replays
                             if r.get("comparison_with_full", {}).get("token_savings_pct", 0) > 20)
            stats["hit_rate"] = round(successful / len(member_replays), 2) if member_replays else 0

    members = [{"email": email, **stats} for email, stats in sorted(member_stats.items())]

    return {
        "aggregate": aggregate,
        "total_trajectories": len(trajectories),
        "shared_trajectories": len([t for t in trajectories if t.get("replay_count", 0) > 1]),
        "members": members,
    }


@app.get("/api/savings/cumulative")
async def savings_cumulative():
    """Cumulative savings over time for charting — chronological array."""
    from .agents.qa_pipeline.trajectory_replay import get_replay_results

    results = get_replay_results()
    # Sort chronologically by timestamp
    results.sort(key=lambda x: x.get("timestamp", ""))

    cumulative = []
    running_tokens = 0
    running_time = 0.0
    for i, r in enumerate(results):
        comp = r.get("comparison_with_full", {})
        tokens_saved = max(0, comp.get("tokens_full", 0) - comp.get("tokens_replay", 0))
        time_saved = max(0, comp.get("time_full_s", 0) - comp.get("time_replay_s", 0))
        running_tokens += tokens_saved
        running_time += time_saved
        meta = r.get("metadata", {})
        peer = meta.get("replayed_by") or meta.get("created_by") or "unknown"
        is_replay = meta.get("is_replay", False)
        cumulative.append({
            "run_number": i + 1,
            "run_id": r.get("replay_run_id", ""),
            "workflow": r.get("workflow", ""),
            "cumulative_tokens_saved": running_tokens,
            "cumulative_time_saved_s": round(running_time, 1),
            "per_run_tokens_saved": tokens_saved,
            "per_run_time_saved_s": round(time_saved, 1),
            "per_run_tokens_used": comp.get("tokens_replay", 0),
            "peer": peer,
            "is_replay": is_replay,
            "timestamp": r.get("timestamp", ""),
        })
    return {"cumulative": cumulative, "total_runs": len(cumulative)}


# ─── ROP (Retained Operation Pattern) endpoints ─────────────────────────

@app.get("/api/rop/manifests")
async def rop_list_manifests():
    """List all ROP manifests — Layer 0 cards for agent routing."""
    from .agents.qa_pipeline.rop_manifest import get_rop_registry
    registry = get_rop_registry()
    return {"manifests": registry.list_cards(), "total": len(registry.list_all())}


@app.get("/api/rop/manifests/{rop_id}")
async def rop_get_manifest(rop_id: str, layer: int = 0):
    """Get a specific ROP manifest at a progressive disclosure layer.

    Layers: 0=card, 1=skeleton, 2=subpaths (needs cluster_id), 3=action_policy, 4=audit
    """
    from .agents.qa_pipeline.rop_manifest import get_rop_registry
    registry = get_rop_registry()
    manifest = registry.get(rop_id)
    if not manifest:
        return {"error": f"ROP manifest '{rop_id}' not found"}, 404

    if layer == 0:
        return manifest.card()
    elif layer == 1:
        return manifest.skeleton()
    elif layer == 3:
        return manifest.action_policy()
    elif layer == 4:
        return manifest.audit_checklist()
    else:
        return manifest.card()


@app.get("/api/rop/manifests/{rop_id}/cluster/{cluster_id}")
async def rop_get_cluster(rop_id: str, cluster_id: str):
    """Get Layer 2 subpath details for a specific cluster."""
    from .agents.qa_pipeline.rop_manifest import get_rop_registry
    registry = get_rop_registry()
    manifest = registry.get(rop_id)
    if not manifest:
        return {"error": f"ROP manifest '{rop_id}' not found"}, 404
    return manifest.subpaths(cluster_id)


@app.post("/api/rop/suggest-next")
async def rop_suggest_next(request: Request):
    """Core RET-12 endpoint: suggest the next action based on prefix matching.

    Body: { "actions": [...], "context": {}, "rop_family": "DRX"|"CSP"|"" }
    Returns: suggestion with action, confidence, rationale — or null
    """
    from .agents.qa_pipeline.suggest_next import ActionPrefix, suggest_next
    body = await request.json()
    prefix = ActionPrefix(
        actions=body.get("actions", []),
        context=body.get("context", {}),
        screen_fingerprint=body.get("screen_fingerprint", ""),
        current_directory=body.get("current_directory", ""),
        current_url=body.get("current_url", ""),
        rop_family=body.get("rop_family", ""),
    )
    min_conf = body.get("min_confidence", 0.65)
    suggestion = suggest_next(prefix, min_confidence=min_conf)
    if suggestion is None:
        return {"suggestion": None, "reason": "No confident match found"}
    from dataclasses import asdict
    return {"suggestion": asdict(suggestion)}


@app.post("/api/rop/check-divergence")
async def rop_check_divergence(request: Request):
    """RET-13 endpoint: check if agent has diverged from suggested path.

    Body: { "actions": [...], "last_suggestion": { action, confidence, ... } }
    Returns: { diverged, severity, reason, recommendation }
    """
    from .agents.qa_pipeline.suggest_next import ActionPrefix, Suggestion, check_divergence
    body = await request.json()
    prefix = ActionPrefix(actions=body.get("actions", []))
    last = body.get("last_suggestion")
    last_suggestion = None
    if last:
        last_suggestion = Suggestion(
            action=last.get("action", ""),
            confidence=last.get("confidence", 0),
            pattern_id=last.get("pattern_id", ""),
            branch=last.get("branch", ""),
            expected_checkpoint=last.get("expected_checkpoint", ""),
            reason=last.get("reason", ""),
        )
    return check_divergence(prefix, last_suggestion)


@app.get("/api/rop/suggestion-stats")
async def rop_suggestion_stats():
    """RET-14 stats: suggestion follow rate, tokens saved, per-pattern breakdown."""
    from .agents.qa_pipeline.suggest_next import get_suggestion_stats
    return get_suggestion_stats()


@app.post("/api/rop/match-trigger")
async def rop_match_trigger(request: Request):
    """Match a user request against ROP triggers — returns best matching manifest."""
    from .agents.qa_pipeline.rop_manifest import get_rop_registry
    body = await request.json()
    user_request = body.get("request", "")
    registry = get_rop_registry()
    manifest = registry.match_trigger(user_request)
    if manifest:
        return {"matched": True, "manifest": manifest.card()}
    return {"matched": False, "manifest": None}


@app.get("/api/rop/savings/patterns")
async def rop_savings_patterns():
    """Per-ROP pattern stats: invocations, success rate, avg savings."""
    from .services.rop_savings_tracker import get_rop_savings_tracker
    tracker = get_rop_savings_tracker()
    return {"patterns": tracker.pattern_stats()}


@app.get("/api/rop/savings/portfolio")
async def rop_savings_portfolio(days: int = 30):
    """Portfolio-level savings over time for the ROP dashboard."""
    from .services.rop_savings_tracker import get_rop_savings_tracker
    tracker = get_rop_savings_tracker()
    return tracker.portfolio_stats(days=days)


@app.get("/api/rop/savings/compare/{cold_run_id}/{assisted_run_id}")
async def rop_savings_compare(cold_run_id: str, assisted_run_id: str):
    """Compare a cold run vs retention-assisted run side by side."""
    from .services.rop_savings_tracker import get_rop_savings_tracker
    tracker = get_rop_savings_tracker()
    return tracker.compare_runs(cold_run_id, assisted_run_id)


@app.get("/api/rop/dream/status")
async def rop_dream_status():
    """KAIROS-style: check if dream consolidation should fire."""
    from .services.rop_dream_engine import should_dream
    return should_dream()


@app.post("/api/rop/dream/run")
async def rop_dream_run():
    """KAIROS-style: manually trigger dream consolidation.

    Four phases: Orient → Gather Signal → Consolidate → Prune.
    Promotes healthy trajectories to ROPs, archives unhealthy ones.
    """
    from .services.rop_dream_engine import run_dream
    from dataclasses import asdict
    result = run_dream()
    return asdict(result)


@app.get("/api/rop/dream/history")
async def rop_dream_history():
    """Dream consolidation history — audit trail of all dream cycles."""
    from pathlib import Path
    log_path = Path(__file__).resolve().parents[1] / "data" / "rop_dreams" / "dream_log.jsonl"
    if not log_path.exists():
        return {"dreams": [], "total": 0}
    dreams = []
    for line in log_path.read_text().strip().split("\n"):
        if line:
            try:
                dreams.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {"dreams": dreams[-20:], "total": len(dreams)}


@app.get("/api/rop/heartbeat")
async def rop_heartbeat():
    """KAIROS-style heartbeat: periodic status check for the ROP system.

    Returns dream readiness, portfolio stats, drift alerts, suggest_next readiness.
    """
    from .services.rop_dream_engine import heartbeat_check
    return heartbeat_check()


# ─── Benchmark Card endpoints ────────────────────────────────────────────

@app.get("/api/benchmarks/cards")
async def benchmark_list_cards():
    """List all benchmark cards (summary view)."""
    from .services.benchmark_card import list_cards
    return {"cards": list_cards()}


@app.post("/api/benchmarks/cards/generate")
async def benchmark_generate_all():
    """Generate/refresh benchmark cards from all eval data."""
    from .services.benchmark_card import generate_all_cards
    from dataclasses import asdict
    cards = generate_all_cards()
    return {"cards": [asdict(c) for c in cards], "total": len(cards)}


@app.get("/api/benchmarks/cards/{workflow_family}")
async def benchmark_get_card(workflow_family: str):
    """Get a specific benchmark card."""
    from .services.benchmark_card import load_card, generate_card
    from dataclasses import asdict
    card = load_card(workflow_family)
    if not card:
        card = generate_card(workflow_family)
    return asdict(card)


@app.get("/api/benchmarks/compare/{eval_id}")
async def benchmark_compare_pane(eval_id: str):
    """Three-pane compare view: frontier / replay / judge verdict."""
    from .services.benchmark_card import get_compare_pane
    from dataclasses import asdict
    pane = get_compare_pane(eval_id)
    if not pane:
        return {"error": f"Eval '{eval_id}' not found"}
    return asdict(pane)


# ─── Agent-Agnostic Ingestion (any SDK can POST tool calls) ──────────────

@app.post("/api/ingest/session")
async def ingest_session(request: Request):
    """Agent-agnostic session ingestion — any agent SDK can POST tool call logs.

    Accepts tool calls from Claude Code, OpenAI Agents SDK, LangGraph, CrewAI,
    or any custom agent. Runs the workflow judge and returns verdict + nudges.

    Body: {
      "session_id": "optional",
      "agent_sdk": "claude_code" | "openai_agents" | "langraph" | "crewai" | "custom",
      "prompt": "the user's original prompt",
      "tool_calls": [
        {"tool": "read_file", "input": {"path": "..."}, "output": "...", "timestamp": "...", "screenshot_url": ""},
        ...
      ]
    }
    """
    from .services.workflow_judge.unified import judge_with_nudges
    body = await request.json()

    prompt = body.get("prompt", "")
    raw_calls = body.get("tool_calls", [])
    agent_sdk = body.get("agent_sdk", "custom")
    session_id = body.get("session_id", "")

    # Normalize tool calls from any SDK format
    normalized = []
    for tc in raw_calls:
        name = tc.get("tool") or tc.get("name") or tc.get("function", {}).get("name", "")
        inp = tc.get("input") or tc.get("arguments") or tc.get("params") or ""
        out = tc.get("output") or tc.get("result") or ""

        normalized.append({
            "tool": name,
            "name": name,
            "result": str(out)[:500],
            "input": inp if isinstance(inp, dict) else {"raw": str(inp)[:500]},
            "timestamp": tc.get("timestamp", ""),
            "screenshot_url": tc.get("screenshot_url", ""),
        })

    # Run workflow judge
    result = judge_with_nudges(prompt=prompt, tool_calls=normalized)

    # Add session metadata
    result["session_id"] = session_id
    result["agent_sdk"] = agent_sdk
    result["tool_calls_received"] = len(normalized)
    result["tool_calls_normalized"] = normalized[:5]  # preview of first 5

    # Persist for the trajectory viewer
    if session_id:
        session_dir = Path(__file__).resolve().parents[1] / "data" / "ingested_sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        session_path = session_dir / f"{session_id}.json"
        session_path.write_text(json.dumps({
            "session_id": session_id,
            "agent_sdk": agent_sdk,
            "prompt": prompt,
            "tool_calls": normalized,
            "verdict": result.get("verdict", {}),
            "nudges": result.get("nudges", []),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, indent=2, default=str))

    return result


@app.get("/api/ingest/sessions")
async def list_ingested_sessions():
    """List all ingested agent sessions for the trajectory viewer."""
    session_dir = Path(__file__).resolve().parents[1] / "data" / "ingested_sessions"
    if not session_dir.exists():
        return {"sessions": [], "total": 0}
    sessions = []
    for f in sorted(session_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            sessions.append({
                "session_id": data.get("session_id", f.stem),
                "agent_sdk": data.get("agent_sdk", ""),
                "prompt": data.get("prompt", "")[:100],
                "tool_call_count": len(data.get("tool_calls", [])),
                "verdict": data.get("verdict", {}).get("verdict", ""),
                "timestamp": data.get("timestamp", ""),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return {"sessions": sessions[:50], "total": len(sessions)}


@app.get("/api/ingest/session/{session_id}")
async def get_ingested_session(session_id: str):
    """Get a full ingested session with tool calls, verdict, and nudges."""
    session_dir = Path(__file__).resolve().parents[1] / "data" / "ingested_sessions"
    path = session_dir / f"{session_id}.json"
    if not path.exists():
        return {"error": "Session not found"}
    return json.loads(path.read_text())


# ─── Workflow Judge + Nudge Engine ────────────────────────────────────────

@app.get("/api/workflow-judge/workflows")
async def wj_list_workflows():
    """List all known workflow patterns (built-in + saved)."""
    from .services.workflow_judge.models import WorkflowKnowledge
    wfs = WorkflowKnowledge.list_all()
    return {"workflows": wfs, "total": len(wfs)}


@app.post("/api/workflow-judge/migrate")
async def wj_migrate():
    """Migrate policies from data/workflow_policies/ into unified data/workflow_knowledge/ store."""
    from .services.workflow_judge import migrate_policies_to_unified_store
    migrated = migrate_policies_to_unified_store()
    return {"migrated": migrated}


@app.post("/api/workflow-judge/seed")
async def wj_seed():
    """Seed built-in workflow templates to disk (run once)."""
    from .services.workflow_judge.models import seed_builtin_workflows
    wfs = seed_builtin_workflows()
    return {"seeded": len(wfs), "workflow_ids": [w.workflow_id for w in wfs]}


@app.post("/api/workflow-judge/detect")
async def wj_detect(request: Request):
    """Detect which workflow a natural language prompt maps to.
    Body: { "prompt": "flywheel this" }
    """
    from .services.workflow_judge.detector import detect_workflow
    from dataclasses import asdict
    body = await request.json()
    result = detect_workflow(body.get("prompt", ""), body.get("context", ""))
    if result:
        return {"matched": True, **asdict(result)}
    return {"matched": False, "confidence": 0}


@app.post("/api/workflow-judge/judge")
async def wj_judge(request: Request):
    """Judge workflow completion against evidence.
    Body: { "prompt": "...", "tool_calls": [...], "workflow_id": "" }

    Flow: detect workflow from prompt → load workflow → score steps → verdict + nudges.
    """
    from .services.workflow_judge.detector import detect_workflow
    from .services.workflow_judge.judge import judge_completion
    from .services.workflow_judge.nudge import NudgeEngine
    from .services.workflow_judge.models import WorkflowKnowledge
    from dataclasses import asdict

    body = await request.json()
    prompt = body.get("prompt", "")
    tool_calls = body.get("tool_calls", [])
    workflow_id = body.get("workflow_id", "")

    # Load workflow: explicit ID or detect from prompt
    workflow = None
    if workflow_id:
        workflow = WorkflowKnowledge.load(workflow_id)
    if not workflow:
        detection = detect_workflow(prompt)
        if detection:
            workflow = WorkflowKnowledge.load(detection.workflow_id)

    if not workflow:
        return {
            "error": "No workflow detected",
            "hint": "Seed workflows first with POST /api/workflow-judge/seed, or provide a workflow_id",
        }

    # Judge
    verdict = judge_completion(workflow, tool_calls)

    # Generate nudges
    engine = NudgeEngine()
    nudges = engine.generate_nudges(verdict, workflow)
    engine.log_nudges(nudges, verdict)

    return {
        "verdict": asdict(verdict),
        "nudges": [asdict(n) for n in nudges],
        "nudge_summary": engine.format_nudges_for_user(nudges),
    }


@app.post("/api/workflow-judge/learn")
async def wj_learn(request: Request):
    """Learn from a user correction ("you forgot X").
    Body: { "workflow_id": "dev.flywheel.v3", "correction": "you didn't do the latest search" }
    """
    from .services.workflow_judge.learner import detect_correction, record_correction
    body = await request.json()
    correction = detect_correction(body.get("correction", ""))
    if correction:
        record_correction(correction, workflow_id=body.get("workflow_id", ""))
        return {"learned": True, "matched_step": correction.inferred_step, "correction_id": correction.correction_id}
    return {"learned": False, "matched_step": None}


@app.get("/api/workflow-judge/corrections")
async def wj_corrections(days: int = 30):
    """Analyze correction patterns — find systematic gaps."""
    from .services.workflow_judge.learner import analyze_corrections
    return analyze_corrections(days=days)


# ─── Strict LLM Replay Judge endpoints ───────────────────────────────────

@app.post("/api/benchmarks/strict-judge/single/{replay_result_id}")
async def strict_judge_single(replay_result_id: str, eval_id: str = "", model: str = "gpt-5.4-mini"):
    """Run strict LLM judge on a single replay. Makes a REAL API call."""
    from .benchmarks.strict_replay_judge import judge_replay
    from dataclasses import asdict
    verdict = await judge_replay(replay_result_id, eval_id=eval_id, model=model)
    return asdict(verdict)


@app.post("/api/benchmarks/strict-judge/batch")
async def strict_judge_batch(request: Request):
    """Run strict LLM judge on N replays for a workflow. Makes REAL API calls.

    Body: { "workflow": "...", "n": 10, "model": "gpt-5.4-mini" }
    """
    from .benchmarks.strict_replay_judge import judge_batch
    from dataclasses import asdict
    body = await request.json()
    result = await judge_batch(
        workflow=body.get("workflow", ""),
        n=body.get("n", 10),
        model=body.get("model", "gpt-5.4-mini"),
    )
    return asdict(result)


@app.get("/api/benchmarks/strict-judge/results")
async def strict_judge_results():
    """List all strict judge results."""
    from pathlib import Path as P
    results_dir = P(__file__).resolve().parents[1] / "data" / "strict_judge_results"
    if not results_dir.exists():
        return {"results": [], "total": 0}
    results = []
    for f in sorted(results_dir.glob("batch-*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            results.append({
                "batch_id": data.get("batch_id"),
                "workflow": data.get("workflow"),
                "total_judged": data.get("total_judged"),
                "acceptable_rate": data.get("acceptable_rate"),
                "avg_confidence": data.get("avg_confidence"),
                "agreement_rate": data.get("agreement_rate"),
                "judge_model": data.get("judge_model"),
                "timestamp": data.get("timestamp"),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return {"results": results[:20], "total": len(results)}


# ─── Temporal Memory Benchmark endpoints ─────────────────────────────────

@app.get("/api/benchmarks/temporal/cases")
async def temporal_list_cases():
    """List all temporal benchmark cases."""
    from .services.temporal_benchmark import get_builtin_cases
    from dataclasses import asdict
    cases = get_builtin_cases()
    return {"cases": [asdict(c) for c in cases], "total": len(cases)}


@app.post("/api/benchmarks/temporal/run/{case_id}")
async def temporal_run_case(case_id: str, condition: str = ""):
    """Run a temporal benchmark case. If no condition, runs all three and compares."""
    from .services.temporal_benchmark import run_temporal_benchmark, compare_conditions
    from dataclasses import asdict
    if condition:
        result = run_temporal_benchmark(case_id, condition)
        return asdict(result)
    else:
        comparison = compare_conditions(case_id)
        return asdict(comparison)


@app.post("/api/benchmarks/temporal/run-all")
async def temporal_run_all():
    """Run all temporal benchmark cases — produces full comparison suite."""
    from .services.temporal_benchmark import run_all_cases
    from dataclasses import asdict
    results = run_all_cases()
    return {
        "results": [asdict(r) for r in results],
        "total": len(results),
        "summary": {
            "avg_full_savings_pct": round(
                sum(r.full_vs_fresh_token_savings_pct for r in results) / max(len(results), 1), 1
            ),
            "avg_progressive_savings_pct": round(
                sum(r.progressive_vs_fresh_token_savings_pct for r in results) / max(len(results), 1), 1
            ),
            "retention_proven": sum(1 for r in results if r.verdict == "retention_proven"),
            "marginal": sum(1 for r in results if r.verdict == "marginal"),
        },
    }


@app.get("/api/benchmarks/temporal/compare/{case_id}")
async def temporal_compare(case_id: str):
    """Get comparison across all three conditions for a case."""
    from .services.temporal_benchmark import compare_conditions
    from dataclasses import asdict
    return asdict(compare_conditions(case_id))


# ─── TA Replay Kit endpoints (Product 1) ────────────────────────────────

@app.get("/api/replay-kit/stats")
async def replay_kit_stats():
    """Replay Kit overview: captures, replays, savings."""
    from .services.replay_kit import get_replay_kit
    return get_replay_kit().stats()


@app.get("/api/replay-kit/plan/{trajectory_id}")
async def replay_kit_plan(trajectory_id: str):
    """Build a replay plan from a captured trajectory."""
    from .services.replay_kit import get_replay_kit
    return get_replay_kit().build_replay_plan(trajectory_id)


@app.get("/api/replay-kit/compare/{trajectory_id}")
async def replay_kit_compare(trajectory_id: str, replay_id: str = ""):
    """Three-pane compare: frontier vs replay vs judge."""
    from .services.replay_kit import get_replay_kit
    from dataclasses import asdict
    return asdict(get_replay_kit().compare(trajectory_id, replay_id))


@app.get("/api/rop/dashboard")
async def rop_dashboard():
    """Combined ROP dashboard: manifests + suggestion stats + savings portfolio."""
    from .agents.qa_pipeline.rop_manifest import get_rop_registry
    from .agents.qa_pipeline.suggest_next import get_suggestion_stats
    from .services.rop_savings_tracker import get_rop_savings_tracker

    from .services.rop_dream_engine import should_dream, _load_state

    registry = get_rop_registry()
    tracker = get_rop_savings_tracker()
    dream_state = should_dream()

    return {
        "manifests": registry.list_cards(),
        "suggestion_stats": get_suggestion_stats(),
        "pattern_stats": tracker.pattern_stats(),
        "portfolio": tracker.portfolio_stats(days=30),
        "dream": {
            "ready": dream_state["should_run"],
            "reason": dream_state["reason"],
            "total_consolidations": dream_state["state"].get("total_consolidations", 0),
            "last_duration_s": dream_state["state"].get("last_dream_duration_s", 0),
        },
    }


@app.post("/api/usage/sync-ccusage")
async def sync_ccusage(days: int = 7):
    """Sync Claude Code usage (via ccusage) into unified telemetry."""
    from .services.ccusage_tracker import sync_ccusage_to_telemetry
    return sync_ccusage_to_telemetry(days=days)


@app.get("/api/usage/summary")
async def usage_summary(days: int = 1):
    """Summarize all token usage (Claude Code + OpenAI) for the last N days."""
    from .services.usage_telemetry import summarize_usage
    return summarize_usage(days=days)


@app.get("/api/team/stream")
async def team_event_stream():
    """SSE stream for real-time team dashboard updates.

    Pushes events when pipeline runs complete, trajectory replays finish,
    or savings data changes. Frontend connects via EventSource.
    """
    from starlette.responses import StreamingResponse
    from .api.mcp_pipeline import _team_event_queues

    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _team_event_queues.append(queue)

    async def event_generator():
        try:
            # Send initial heartbeat
            yield f"data: {json.dumps({'type': 'connected', 'timestamp': datetime.now(timezone.utc).isoformat()})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if queue in _team_event_queues:
                _team_event_queues.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/pipeline/results/{run_id}/audit-report")
async def audit_report(run_id: str):
    """Generate a printable HTML compliance audit report for a pipeline run.

    Returns an HTML page styled for print-to-PDF. No external libraries needed —
    the browser's native print dialog produces the PDF.
    """
    import json as _json
    from pathlib import Path as _Path
    from fastapi.responses import HTMLResponse as _HTMLResponse

    _results_dir = _Path(__file__).parent.parent / "data" / "pipeline_results"
    _result_file = _results_dir / f"{run_id}.json"
    if not _result_file.exists():
        return _HTMLResponse("<h1>Run not found</h1>", status_code=404)

    data = _json.loads(_result_file.read_text())
    result = data.get("result", {})
    summary = result.get("summary", {})
    test_cases = result.get("test_cases", [])
    execution = result.get("execution", [])
    exec_map = {e.get("test_id"): e for e in execution}

    started = data.get("started_at", "")[:19].replace("T", " ")
    completed = data.get("completed_at", "")[:19].replace("T", " ")
    duration = f"{data.get('duration_s', 0):.1f}s"
    tokens = data.get("token_metrics", {})
    stage_timings = data.get("stage_timings", {})

    def stage_row(name: str) -> str:
        s = stage_timings.get(name, {})
        if not s:
            return f"<tr><td>{name}</td><td>skipped</td><td>—</td><td>—</td></tr>"
        start = s.get("start", "")[:19].replace("T", " ")
        end = s.get("end", "")[:19].replace("T", " ")
        return f"<tr><td>{name}</td><td>✓</td><td>{start}</td><td>{end}</td></tr>"

    tc_rows = ""
    for tc in test_cases:
        tid = tc.get("test_id", "")
        ex = exec_map.get(tid, {})
        status = ex.get("status", "not_run")
        status_icon = "✓" if status == "passed" else ("✗" if status == "failed" else "—")
        status_color = "#22c55e" if status == "passed" else ("#ef4444" if status == "failed" else "#6b7280")
        steps_html = "".join(
            f"<li>{s.get('action','')}: {s.get('target', s.get('expected',''))}</li>"
            for s in tc.get("steps", [])
        )
        tc_rows += f"""
        <tr>
          <td style="font-family:monospace;font-size:11px">{tid}</td>
          <td>{tc.get('name','')}</td>
          <td>{tc.get('priority','')}</td>
          <td style="color:{status_color};font-weight:bold">{status_icon} {status}</td>
          <td><ul style="margin:0;padding-left:16px;font-size:11px">{steps_html}</ul></td>
        </tr>"""
        if ex.get("failure_reason"):
            tc_rows += f"""<tr><td colspan="5" style="color:#ef4444;font-size:11px;padding:4px 8px">
              ↳ Failure: {ex['failure_reason']}</td></tr>"""

    pass_rate = summary.get("pass_rate", 0)
    verdict_color = "#22c55e" if pass_rate >= 0.8 else ("#f59e0b" if pass_rate >= 0.5 else "#ef4444")

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Compliance Audit Report — {run_id}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 13px; color: #111; background: #fff; padding: 32px; max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 20px; font-weight: 700; margin-bottom: 4px; }}
  h2 {{ font-size: 14px; font-weight: 600; margin: 24px 0 8px; text-transform: uppercase; letter-spacing: 0.05em; color: #444; border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; }}
  .meta {{ font-size: 11px; color: #6b7280; margin-bottom: 24px; }}
  .verdict {{ display: inline-block; font-size: 18px; font-weight: 700; color: {verdict_color}; border: 2px solid {verdict_color}; border-radius: 8px; padding: 8px 20px; margin-bottom: 24px; }}
  .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px; }}
  .metric {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; text-align: center; }}
  .metric-value {{ font-size: 22px; font-weight: 700; }}
  .metric-label {{ font-size: 10px; color: #6b7280; text-transform: uppercase; margin-top: 2px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th {{ background: #f9fafb; text-align: left; padding: 6px 8px; font-size: 11px; color: #6b7280; text-transform: uppercase; border: 1px solid #e5e7eb; }}
  td {{ padding: 6px 8px; border: 1px solid #e5e7eb; vertical-align: top; }}
  tr:nth-child(even) td {{ background: #f9fafb; }}
  .hash {{ font-family: monospace; font-size: 10px; color: #6b7280; word-break: break-all; }}
  .footer {{ margin-top: 32px; font-size: 10px; color: #9ca3af; border-top: 1px solid #e5e7eb; padding-top: 12px; }}
  @media print {{
    body {{ padding: 16px; }}
    .no-print {{ display: none; }}
    h2 {{ break-after: avoid; }}
    table {{ break-inside: auto; }}
    tr {{ break-inside: avoid; }}
  }}
</style>
</head><body>
<button class="no-print" onclick="window.print()" style="float:right;padding:8px 16px;background:#111;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:12px">
  Print / Save as PDF
</button>
<h1>Compliance Audit Report</h1>
<div class="meta">Run ID: <strong>{run_id}</strong> &nbsp;·&nbsp; App: <strong>{data.get('app_name','—')}</strong> &nbsp;·&nbsp; Generated: {completed}</div>

<div class="verdict">{summary.get('passed',0)}/{summary.get('total',0)} passed &nbsp; {pass_rate*100:.1f}%</div>

<div class="grid">
  <div class="metric"><div class="metric-value">{summary.get('total',0)}</div><div class="metric-label">Test Cases</div></div>
  <div class="metric"><div class="metric-value" style="color:#22c55e">{summary.get('passed',0)}</div><div class="metric-label">Passed</div></div>
  <div class="metric"><div class="metric-value" style="color:#ef4444">{summary.get('failed',0)}</div><div class="metric-label">Failed</div></div>
  <div class="metric"><div class="metric-value">{data.get('event_count',0)}</div><div class="metric-label">Events</div></div>
</div>

<h2>Execution Provenance</h2>
<table>
  <tr><th>Field</th><th>Value</th></tr>
  <tr><td>Run ID</td><td class="hash">{run_id}</td></tr>
  <tr><td>App Name</td><td>{data.get('app_name','—')}</td></tr>
  <tr><td>Flow Type</td><td>{data.get('flow_type','—')}</td></tr>
  <tr><td>Started At</td><td>{started}</td></tr>
  <tr><td>Completed At</td><td>{completed}</td></tr>
  <tr><td>Duration</td><td>{duration}</td></tr>
  <tr><td>Tool Calls</td><td>{data.get('tool_call_count',0)}</td></tr>
  <tr><td>Input Tokens</td><td>{tokens.get('input_tokens',0):,}</td></tr>
  <tr><td>Output Tokens</td><td>{tokens.get('output_tokens',0):,}</td></tr>
  <tr><td>Est. Cost</td><td>${tokens.get('estimated_cost_usd',0):.4f}</td></tr>
</table>

<h2>Pipeline Stages</h2>
<table>
  <tr><th>Stage</th><th>Status</th><th>Started</th><th>Ended</th></tr>
  {stage_row('CRAWL')}
  {stage_row('WORKFLOW')}
  {stage_row('TESTCASE')}
  {stage_row('EXECUTION')}
</table>

<h2>Test Case Results ({len(test_cases)} cases)</h2>
<table>
  <tr><th>ID</th><th>Name</th><th>Priority</th><th>Result</th><th>Steps</th></tr>
  {tc_rows if tc_rows else '<tr><td colspan="5" style="color:#6b7280;text-align:center">No test cases in this run</td></tr>'}
</table>

<div class="footer">
  retention.sh — Workflow Intelligence &amp; Verification Layer &nbsp;·&nbsp;
  Report generated from run data at {completed} &nbsp;·&nbsp;
  This document constitutes an append-only audit record of agent-executed test cases.
</div>
</body></html>"""
    return _HTMLResponse(html)


@app.post("/api/memory/export")
async def memory_export():
    """Export all memory data (crawls, workflows, test suites, trajectories) as a bundle."""
    import json as _json
    from pathlib import Path as _Path

    memory_dir = _Path(__file__).parent.parent / "data" / "exploration_memory"
    traj_dir = _Path(__file__).parent.parent / "data" / "trajectories"
    replay_dir = _Path(__file__).parent.parent / "data" / "replay_results"

    bundle = {"exported_at": datetime.now(timezone.utc).isoformat(), "crawls": {}, "workflows": {}, "test_suites": {}, "trajectories": {}, "replay_results": {}}

    for subdir, key in [("crawl", "crawls"), ("workflows", "workflows"), ("test_suites", "test_suites")]:
        d = memory_dir / subdir
        if d.exists():
            for f in d.glob("*.json"):
                try:
                    bundle[key][f.stem] = _json.loads(f.read_text())
                except Exception:
                    pass

    if traj_dir.exists():
        for task_dir in traj_dir.iterdir():
            if task_dir.is_dir() and not task_dir.name.startswith("_"):
                for f in task_dir.glob("*.json"):
                    try:
                        bundle["trajectories"][f"{task_dir.name}/{f.stem}"] = _json.loads(f.read_text())
                    except Exception:
                        pass

    if replay_dir.exists():
        for f in replay_dir.glob("*.json"):
            try:
                bundle["replay_results"][f.stem] = _json.loads(f.read_text())
            except Exception:
                pass

    # Save bundle to disk and return path
    export_dir = _Path(__file__).parent.parent / "data" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = export_dir / f"memory_bundle_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    bundle_path.write_text(_json.dumps(bundle, indent=2, default=str))

    return {
        "status": "ok",
        "bundle_path": str(bundle_path),
        "counts": {k: len(v) for k, v in bundle.items() if isinstance(v, dict) and k != "exported_at"},
    }


@app.post("/api/memory/import")
async def memory_import(request: Request):
    """Import a memory bundle (JSON body)."""
    import json as _json
    from pathlib import Path as _Path

    body = await request.json()
    memory_dir = _Path(__file__).parent.parent / "data" / "exploration_memory"
    traj_dir = _Path(__file__).parent.parent / "data" / "trajectories"
    replay_dir = _Path(__file__).parent.parent / "data" / "replay_results"
    imported = {"crawls": 0, "workflows": 0, "test_suites": 0, "trajectories": 0, "replay_results": 0}

    for key, subdir in [("crawls", "crawl"), ("workflows", "workflows"), ("test_suites", "test_suites")]:
        d = memory_dir / subdir
        d.mkdir(parents=True, exist_ok=True)
        for name, data in body.get(key, {}).items():
            (d / f"{name}.json").write_text(_json.dumps(data, indent=2, default=str))
            imported[key] += 1

    for path_key, data in body.get("trajectories", {}).items():
        parts = path_key.split("/", 1)
        if len(parts) == 2:
            task_d = traj_dir / parts[0]
            task_d.mkdir(parents=True, exist_ok=True)
            (task_d / f"{parts[1]}.json").write_text(_json.dumps(data, indent=2, default=str))
            imported["trajectories"] += 1

    replay_dir.mkdir(parents=True, exist_ok=True)
    for name, data in body.get("replay_results", {}).items():
        (replay_dir / f"{name}.json").write_text(_json.dumps(data, indent=2, default=str))
        imported["replay_results"] += 1

    return {"status": "ok", "imported": imported}


@app.get("/api/pipeline/results")
async def pipeline_results(run_id: str = None):
    """REST endpoint for pipeline results — used by demo pages."""
    import json as _json
    from pathlib import Path as _Path
    results_dir = _Path(__file__).parent.parent / "data" / "pipeline_results"
    if run_id:
        f = results_dir / f"{run_id}.json"
        if f.exists():
            return _json.loads(f.read_text())
        return {"error": f"Run {run_id} not found"}
    # List all runs with summary
    runs = []
    for f in sorted(results_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            d = _json.loads(f.read_text())
            r = d.get("result", d)
            runs.append({
                "run_id": f.stem,
                "app_name": r.get("app_name", d.get("app_name", "")),
                "status": d.get("status", r.get("status", "complete")),
                "duration_s": d.get("duration_s", r.get("duration_s", 0)),
                "started_at": d.get("started_at", ""),
                "result": r,
            })
        except Exception:
            pass
    return {"runs": runs}


@app.get("/api/run-history")
async def run_history():
    """Time-series aggregation across all QA runs — trends by app, day, week."""
    from .agents.qa_pipeline.run_history import build_index
    return build_index(force=True)


@app.get("/api/run-history/health")
async def run_history_health():
    """Overall health summary — is the product getting better or worse?"""
    from .agents.qa_pipeline.run_history import get_health_summary
    return get_health_summary()


@app.get("/api/run-history/app/{app_name}")
async def run_history_app(app_name: str, days: int = 30):
    """Pass rate trend for a specific app over N days."""
    from .agents.qa_pipeline.run_history import get_app_trend
    return get_app_trend(app_name, days=days)


# ── Workflow Compression ─────────────────────────────────────────────────────

@app.post("/api/workflows/{task_name}/compress")
async def compress_workflow_endpoint(task_name: str):
    """Compress a workflow into CRUD shortcuts from repeated trajectories."""
    from .agents.qa_pipeline.workflow_compression import compress_workflow
    result = compress_workflow(task_name)
    if not result:
        return {"status": "insufficient_data", "message": f"Need 2+ successful trajectories for {task_name}"}
    return {"status": "ok", **_asdict_safe(result)}


@app.get("/api/workflows/compression-stats")
async def compression_stats():
    """Get compression stats across all workflows."""
    from .agents.qa_pipeline.workflow_compression import get_compression_stats
    return get_compression_stats()


@app.get("/api/workflows/{task_name}/shortcut")
async def get_shortcut(task_name: str):
    """Get a compressed shortcut for a workflow."""
    from .agents.qa_pipeline.workflow_compression import load_shortcut
    shortcut = load_shortcut(task_name)
    if not shortcut:
        return {"status": "not_found", "message": f"No shortcut for {task_name}. Run /compress first."}
    return {"status": "ok", **_asdict_safe(shortcut)}


# ── Multi-Surface Execution ──────────────────────────────────────────────────

@app.get("/api/surfaces")
async def list_surfaces():
    """List all available workflow surfaces (KYB, EHR, Legacy Portal, etc.)."""
    from .agents.qa_pipeline.multi_surface import list_surfaces as _list
    return {"surfaces": _list()}


@app.get("/api/surfaces/{surface_id}")
async def get_surface(surface_id: str):
    """Get surface config and savings comparison."""
    from .agents.qa_pipeline.multi_surface import get_surface_config, get_surface_savings_comparison
    config = get_surface_config(surface_id)
    if not config:
        return {"error": "surface_not_found"}
    savings = get_surface_savings_comparison(surface_id)
    return {"config": _asdict_safe(config), "savings": savings}


@app.post("/api/surfaces/{surface_id}/trajectory")
async def create_surface_trajectory_endpoint(surface_id: str):
    """Create a template trajectory for a surface (for replay without live execution)."""
    from .agents.qa_pipeline.multi_surface import get_surface_config, create_surface_trajectory
    config = get_surface_config(surface_id)
    if not config:
        return {"error": "surface_not_found"}
    traj = create_surface_trajectory(config)
    # Save it
    from .agents.device_testing.trajectory_logger import get_trajectory_logger
    tl = get_trajectory_logger()
    tl.save_trajectory(traj, config.name)
    return {"status": "ok", "trajectory_id": traj.trajectory_id, "task_name": config.name, "steps": len(traj.steps)}


# ── Longitudinal Benchmark ───────────────────────────────────────────────────

@app.get("/api/benchmarks/longitudinal/{task_name}")
async def get_longitudinal_rollup(task_name: str):
    """Get cumulative longitudinal rollup for a task."""
    rollup_path = _data_dir / "longitudinal" / f"rollup_{task_name}.json"
    if not rollup_path.exists():
        return {"status": "no_data", "message": f"No longitudinal data for {task_name}. Run scripts/longitudinal_harness.py first."}
    return _json.loads(rollup_path.read_text())


@app.get("/api/benchmarks/longitudinal")
async def list_longitudinal():
    """List all tasks with longitudinal benchmark data."""
    long_dir = _data_dir / "longitudinal"
    if not long_dir.exists():
        return {"tasks": []}
    tasks = []
    for f in long_dir.glob("rollup_*.json"):
        try:
            data = _json.loads(f.read_text())
            tasks.append({
                "task_name": data.get("task_name"),
                "total_runs": data.get("total_runs", 0),
                "durability_score": data.get("durability_score", 0),
                "success_rate": data.get("success_rate", 0),
                "drift_trend": data.get("drift_trend", "unknown"),
            })
        except Exception:
            continue
    return {"tasks": tasks}


def _asdict_safe(obj):
    """Convert dataclass to dict safely."""
    from dataclasses import asdict
    try:
        return asdict(obj)
    except Exception:
        return obj.__dict__ if hasattr(obj, "__dict__") else {}

