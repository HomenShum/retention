"""
Demo API Router

Serves the curated demo app catalog, the QA Pipeline SSE endpoint,
and the Showcase Pipeline (generate → crawl → test).
"""

import asyncio
import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/demo", tags=["demo"])


def _get_relay_session():
    """Get an available relay session for test execution on user's device."""
    try:
        from .agent_relay import relay_registry
        # Try authenticated user first, then anonymous
        for user_id in ("authenticated-user", "anonymous"):
            session = relay_registry.get_any_session(user_id)
            if session:
                logger.info(f"Found relay session for execution: user={user_id}")
                return session
    except Exception as e:
        logger.debug(f"No relay session available: {e}")
    return None

# Load demo apps catalog
_demo_apps: dict = {}
_qa_pipeline_service = None
_chef_runner = None

# ── Showcase app generation ──────────────────────────────────────────────────

SHOWCASE_APP_DIR = Path(tempfile.gettempdir()) / "showcase-apps"
SHOWCASE_APP_DIR.mkdir(parents=True, exist_ok=True)

_generated_apps: Dict[str, Path] = {}

# ── Pipeline result persistence (in-memory) ─────────────────────────────────

_pipeline_results: Dict[str, dict] = {}  # run_id -> {app_name, timestamp, result, source}


def _save_pipeline_result(run_id: str, app_name: str, result: dict, source: str = "catalog"):
    _pipeline_results[run_id] = {
        "run_id": run_id,
        "app_name": app_name,
        "timestamp": datetime.utcnow().isoformat(),
        "total_tests": result.get("total_tests", len(result.get("test_cases", []))),
        "total_workflows": len(result.get("workflows", [])),
        "result": result,
        "source": source,
    }

APP_GEN_SYSTEM_PROMPT = """You are a web app generator. Given a description, generate a SINGLE self-contained HTML file.

Requirements:
- Use hash-based client-side routing (#dashboard, #create, #list, #settings, etc.)
- Include a sidebar or top navigation bar with links to ALL pages
- Create 4-5 distinct views/pages with interactive elements:
  - Buttons, forms, text inputs, dropdowns, toggles, checkboxes
  - Each page should have different UI elements
- Use modern, clean CSS (inline <style> tag) with a professional color scheme
- All JavaScript inline in <script> tags — NO external dependencies
- Descriptive text on all elements (labels, placeholders, button text)
- The app should feel realistic and functional (UI only — no backend needed)
- Include a header with the app name and current page title
- Make navigation highlight the active page

Output ONLY the HTML. No explanations, no markdown fences."""


class ShowcasePipelineRequest(BaseModel):
    prompt: str
    model: str = "gpt-5.4-mini"


async def _generate_app(prompt: str, model: str) -> tuple[str, str, int]:
    """Generate a self-contained HTML app using OpenAI. Returns (run_id, html, char_count)."""
    import openai

    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY not set — app generation requires an OpenAI API key. "
                   "Set it in your environment or use the 'test a pre-installed app' option.",
        )

    run_id = uuid.uuid4().hex[:12]
    client = openai.AsyncOpenAI()

    # GPT-5.4 family uses max_completion_tokens; legacy models use max_tokens
    is_5_4 = "5.4" in model
    token_param = {"max_completion_tokens": 16000} if is_5_4 else {"max_tokens": 16000}

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": APP_GEN_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
        **token_param,
    )

    html = response.choices[0].message.content or ""

    # Strip markdown fences if present
    if html.startswith("```"):
        lines = html.split("\n")
        # Remove first line (```html) and last line (```)
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        elif lines[0].startswith("```"):
            lines = lines[1:]
        html = "\n".join(lines)

    # Save to temp dir
    app_dir = SHOWCASE_APP_DIR / run_id
    app_dir.mkdir(parents=True, exist_ok=True)
    index_path = app_dir / "index.html"
    index_path.write_text(html, encoding="utf-8")
    _generated_apps[run_id] = app_dir

    logger.info(f"Generated showcase app: run_id={run_id}, chars={len(html)}")
    return run_id, html, len(html)


def set_qa_pipeline_service(service):
    """Called from main.py to inject the QAPipelineService instance."""
    global _qa_pipeline_service
    _qa_pipeline_service = service
    logger.info("QAPipelineService injected into demo router")


def set_chef_runner(runner):
    """Called from main.py to inject the ChefRunner instance."""
    global _chef_runner
    _chef_runner = runner
    logger.info("ChefRunner injected into demo router")


def _load_catalog() -> dict:
    """Load demo_apps.json from backend/data/."""
    global _demo_apps
    if _demo_apps:
        return _demo_apps

    possible_paths = [
        Path("backend/data/demo_apps.json"),
        Path("data/demo_apps.json"),
        Path(__file__).parent.parent.parent / "data" / "demo_apps.json",
    ]

    for p in possible_paths:
        if p.exists():
            with open(p, "r") as f:
                _demo_apps = json.load(f)
            logger.info(f"Loaded demo catalog from {p} ({len(_demo_apps.get('apps', []))} apps)")
            return _demo_apps

    logger.warning("demo_apps.json not found")
    return {"apps": []}


@router.get("/apps")
async def get_demo_apps():
    """Return the curated demo app catalog."""
    catalog = _load_catalog()
    return catalog


@router.post("/qa-pipeline/{app_id}")
async def run_qa_pipeline(app_id: str, request: Request):
    """
    Run the 3-stage QA pipeline for a demo app.

    Streams SSE events: stage_transition, crawl_progress, tool_call,
    workflow_identified, test_case_generated, pipeline_complete.
    """
    if not _qa_pipeline_service:
        raise HTTPException(status_code=503, detail="QA Pipeline service not initialized")

    # Look up app in catalog
    catalog = _load_catalog()
    app_info = None
    for app in catalog.get("apps", []):
        if app["id"] == app_id:
            app_info = app
            break

    if not app_info:
        raise HTTPException(status_code=404, detail=f"App '{app_id}' not found in catalog")

    app_name = app_info["name"]
    package_name = app_info["package"]
    target_workflows = app_info.get("target_workflows", None)
    crawl_hints = app_info.get("crawl_hints", None)
    max_crawl_turns = app_info.get("max_crawl_turns", None)

    # Get device_id from query param or auto-detect
    device_id = request.query_params.get("device_id", "")
    if not device_id:
        # Auto-detect first available device
        try:
            devices_text = await _qa_pipeline_service.mobile_mcp_client.list_available_devices()
            # Parse device list — look for emulator-XXXX pattern
            import re
            device_matches = re.findall(r"(emulator-\d+)", devices_text)
            if device_matches:
                device_id = device_matches[0]
            else:
                raise HTTPException(status_code=400, detail="No emulator detected. Launch one first.")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to detect device: {str(e)}")

    app_type = app_info.get("type", "native")
    app_url = app_info.get("url", "")

    # For web workflows, accept custom URL from query param
    if app_type == "web":
        custom_url = request.query_params.get("url", "")
        if custom_url:
            app_url = custom_url
        if not app_url:
            raise HTTPException(status_code=400, detail="Web workflow requires a URL")

    logger.info(f"Starting QA pipeline: app={app_name}, type={app_type}, device={device_id}")

    async def event_stream():
        try:
            if app_type == "web":
                # Web workflow: open URL in Chrome on emulator
                async for event in _qa_pipeline_service.run_pipeline_for_url(
                    app_url, app_name, device_id,
                ):
                    event_type = event.get("type", "unknown")
                    data = json.dumps(event)
                    yield f"event: {event_type}\ndata: {data}\n\n"

                    if event_type == "pipeline_complete" and "result" in event:
                        _save_pipeline_result(app_id, app_name, event["result"], "web")

                    await asyncio.sleep(0.05)
                yield "event: done\ndata: {}\n\n"
                return

            async for event in _qa_pipeline_service.run_pipeline(
                app_name, package_name, device_id, target_workflows, crawl_hints, max_crawl_turns,
            ):
                event_type = event.get("type", "unknown")
                data = json.dumps(event)
                yield f"event: {event_type}\ndata: {data}\n\n"

                if event_type == "pipeline_complete" and "result" in event:
                    _save_pipeline_result(app_id, app_name, event["result"], "catalog")

                # Small delay to prevent overwhelming the client
                await asyncio.sleep(0.05)

            yield "event: done\ndata: {}\n\n"

        except Exception as e:
            logger.error(f"QA pipeline error: {e}")
            error_data = json.dumps({"type": "error", "content": str(e)})
            yield f"event: error\ndata: {error_data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Showcase Pipeline (Generate → Crawl → Test) ─────────────────────────────


@router.get("/showcase-app/{run_id}")
async def serve_generated_app(run_id: str):
    """Serve a generated showcase app's index.html."""
    app_dir = _generated_apps.get(run_id)
    if not app_dir or not (app_dir / "index.html").exists():
        raise HTTPException(404, "App not found")
    return FileResponse(app_dir / "index.html", media_type="text/html")


def _sse(event_type: str, data: dict) -> str:
    """Format a single SSE message."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


@router.post("/showcase-pipeline")
async def run_showcase_pipeline(body: ShowcasePipelineRequest, request: Request):
    """
    Full showcase pipeline: generate app → open in Chrome → crawl → workflow → testcase.

    Streams SSE events including generation_progress, generation_complete,
    then all standard pipeline events.
    """
    if not _qa_pipeline_service:
        raise HTTPException(status_code=503, detail="QA Pipeline service not initialized")

    async def event_stream():
        try:
            # ── Phase 1: GENERATE ────────────────────────────────────────
            yield _sse("stage_transition", {"to_stage": "GENERATE"})
            yield _sse("generation_progress", {
                "type": "generation_progress",
                "step": "generating",
                "message": f"Generating app with {body.model}...",
            })

            run_id, html, char_count = await _generate_app(body.prompt, body.model)

            app_name = body.prompt[:50].strip()

            yield _sse("generation_progress", {
                "type": "generation_progress",
                "step": "ready",
                "message": f"App generated — {char_count:,} characters",
            })

            # ── Auto-detect device ───────────────────────────────────────
            device_id = ""
            try:
                devices_text = await _qa_pipeline_service.mobile_mcp_client.list_available_devices()
                device_matches = re.findall(r"(emulator-\d+)", devices_text)
                if device_matches:
                    device_id = device_matches[0]
            except Exception:
                pass

            if not device_id:
                # No emulator — serve the generated app for browser preview
                backend_base = os.environ.get("TA_BACKEND_URL", "http://localhost:8000")
                preview_url = f"{backend_base}/api/demo/showcase-app/{run_id}"
                yield _sse("generation_complete", {
                    "type": "generation_complete",
                    "run_id": run_id,
                    "app_url": preview_url,
                    "preview_only": True,
                    "files_count": 1,
                    "message": "App generated. Connect an emulator to run the full QA pipeline.",
                })
                _save_pipeline_result(run_id, app_name, {"preview_only": True, "html_chars": char_count}, "generated")
                yield _sse("done", {})
                return

            # Emulator found — use emulator-accessible URL
            app_url = f"http://10.0.2.2:8000/api/demo/showcase-app/{run_id}"
            yield _sse("generation_complete", {
                "type": "generation_complete",
                "run_id": run_id,
                "app_url": app_url,
                "files_count": 1,
            })

            logger.info(f"Showcase pipeline: app={app_name}, run_id={run_id}, device={device_id}")

            # ── Phase 2-4: Crawl → Workflow → TestCase → Execution ────
            async for event in _qa_pipeline_service.run_pipeline_for_url(
                app_url, app_name, device_id,
            ):
                event_type = event.get("type", "unknown")
                yield _sse(event_type, event)

                if event_type == "pipeline_complete" and "result" in event:
                    _save_pipeline_result(run_id, app_name, event["result"], "generated")

                await asyncio.sleep(0.05)

            yield _sse("done", {})

        except Exception as e:
            logger.error(f"Showcase pipeline error: {e}")
            yield _sse("error", {"type": "error", "content": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Chef-generated app file serving ──────────────────────────────────────────

CHEF_SERVE_DIR = Path(tempfile.gettempdir()) / "chef-serve"
CHEF_SERVE_DIR.mkdir(parents=True, exist_ok=True)

_chef_served_apps: Dict[str, Path] = {}


@router.get("/chef-app/{run_id}/{path:path}")
async def serve_chef_app_file(run_id: str, path: str):
    """Serve a file from a Chef-generated app's servable output."""
    app_dir = _chef_served_apps.get(run_id)
    if not app_dir:
        raise HTTPException(404, "Chef app not found")
    file_path = (app_dir / path).resolve()
    # Prevent path traversal
    if not str(file_path).startswith(str(app_dir.resolve())):
        raise HTTPException(403, "Forbidden")
    if not file_path.exists() or not file_path.is_file():
        # Fall back to index.html for SPA routing
        index = app_dir / "index.html"
        if index.exists():
            return FileResponse(index, media_type="text/html")
        raise HTTPException(404, "File not found")
    # Guess content type
    suffix = file_path.suffix.lower()
    media_types = {
        ".html": "text/html", ".js": "application/javascript", ".mjs": "application/javascript",
        ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml",
        ".png": "image/png", ".jpg": "image/jpeg", ".ico": "image/x-icon",
    }
    return FileResponse(file_path, media_type=media_types.get(suffix, "application/octet-stream"))


@router.get("/chef-app/{run_id}")
async def serve_chef_app_index(run_id: str):
    """Serve a Chef-generated app's index.html."""
    return await serve_chef_app_file(run_id, "index.html")


def _prepare_chef_servable(run_id: str, chef_result) -> Optional[Path]:
    """Prepare a servable directory from Chef output.

    Checks for built output (dist/, build/), or an index.html in the output.
    Returns the directory to serve from, or None if nothing servable.
    """
    output_dir = Path(chef_result.output_dir) if chef_result.output_dir else None
    if not output_dir or not output_dir.exists():
        return None

    # Check for built output directories
    for candidate in ["dist", "build", ".output/public", "out"]:
        built = output_dir / candidate
        if built.exists() and (built / "index.html").exists():
            _chef_served_apps[run_id] = built
            return built

    # Check for root index.html
    if (output_dir / "index.html").exists():
        _chef_served_apps[run_id] = output_dir
        return output_dir

    # Check if any HTML file exists at all — serve the directory
    html_files = list(output_dir.glob("*.html"))
    if html_files:
        _chef_served_apps[run_id] = output_dir
        return output_dir

    # Generate a simple HTML wrapper from source files as last resort
    serve_dir = CHEF_SERVE_DIR / run_id
    serve_dir.mkdir(parents=True, exist_ok=True)
    if chef_result.files:
        # Write all files to serve dir and create an index
        for fname, content in chef_result.files.items():
            fpath = serve_dir / fname
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")
        # If there's now an index.html, use it
        if (serve_dir / "index.html").exists():
            _chef_served_apps[run_id] = serve_dir
            return serve_dir

    return None


# ── Chef Pipeline (Chef Generate → Serve → Crawl → Test) ────────────────────


class ChefPipelineRequest(BaseModel):
    prompt: str
    model: str = "gpt-5.4"


@router.post("/chef-pipeline")
async def run_chef_pipeline(body: ChefPipelineRequest, request: Request):
    """
    Full Chef pipeline: generate app with Chef → serve/deploy → crawl → test.

    Streams SSE events including chef_progress, generation_complete,
    then all standard pipeline events with live emulator streaming.
    """
    if not _chef_runner:
        raise HTTPException(status_code=503, detail="Chef integration not configured")
    if not _qa_pipeline_service:
        raise HTTPException(status_code=503, detail="QA Pipeline service not initialized")

    async def event_stream():
        try:
            # ── Phase 1: GENERATE with Chef ─────────────────────────────
            yield _sse("stage_transition", {"to_stage": "GENERATE"})
            yield _sse("generation_progress", {
                "type": "generation_progress",
                "step": "chef_starting",
                "message": f"Starting Chef app generation ({body.model})...",
                "engine": "chef",
            })

            run_id = uuid.uuid4().hex[:12]
            chef_result = await _chef_runner.run(
                prompt=body.prompt,
                run_id=run_id,
                model=body.model,
            )

            if not chef_result.success:
                yield _sse("generation_progress", {
                    "type": "generation_progress",
                    "step": "chef_failed",
                    "message": f"Chef generation failed (deploys={chef_result.num_deploys}). Falling back to HTML generator.",
                    "engine": "chef",
                })
                # Fall back to simple HTML generation
                run_id, html, char_count = await _generate_app(body.prompt, "gpt-5.4-mini")
                yield _sse("generation_progress", {
                    "type": "generation_progress",
                    "step": "fallback_ready",
                    "message": f"Fallback app generated — {char_count:,} characters",
                    "engine": "html_fallback",
                })
            else:
                yield _sse("generation_progress", {
                    "type": "generation_progress",
                    "step": "chef_complete",
                    "message": f"Chef app generated — {len(chef_result.files)} files, {chef_result.num_deploys} deploys",
                    "engine": "chef",
                })

            # ── Resolve app URL ──────────────────────────────────────────
            app_url = None
            app_url_browser = None
            backend_base = os.environ.get("TA_BACKEND_URL", "http://localhost:8000")

            # Priority 1: Chef deployment URL
            if chef_result.success and chef_result.deploy_url:
                app_url = chef_result.deploy_url
                yield _sse("generation_progress", {
                    "type": "generation_progress",
                    "step": "deployed",
                    "message": f"App deployed to {app_url}",
                    "engine": "chef",
                })

            # Priority 2: Serve Chef output files locally
            if not app_url and chef_result.success:
                serve_dir = _prepare_chef_servable(run_id, chef_result)
                if serve_dir:
                    app_url_browser = f"{backend_base}/api/demo/chef-app/{run_id}"
                    yield _sse("generation_progress", {
                        "type": "generation_progress",
                        "step": "serving",
                        "message": f"Serving Chef app locally at {app_url_browser}",
                        "engine": "chef",
                    })

            # Priority 3: Fallback — showcase-app HTML (already generated above on failure)
            if not app_url and not chef_result.success:
                app_url_browser = f"{backend_base}/api/demo/showcase-app/{run_id}"

            # ── Auto-detect device ───────────────────────────────────────
            device_id = ""
            try:
                devices_text = await _qa_pipeline_service.mobile_mcp_client.list_available_devices()
                device_matches = re.findall(r"(emulator-\d+)", devices_text)
                if device_matches:
                    device_id = device_matches[0]
            except Exception:
                pass

            if not device_id:
                # No emulator — show preview only
                preview_url = app_url or app_url_browser
                yield _sse("generation_complete", {
                    "type": "generation_complete",
                    "run_id": run_id,
                    "app_url": preview_url,
                    "preview_only": True,
                    "files_count": len(chef_result.files) if chef_result.success else 1,
                    "engine": "chef" if chef_result.success else "html_fallback",
                    "message": "App generated. Connect an emulator to run the full QA pipeline.",
                })
                app_name = body.prompt[:50].strip()
                _save_pipeline_result(run_id, app_name, {
                    "preview_only": True,
                    "engine": "chef" if chef_result.success else "html_fallback",
                    "files_count": len(chef_result.files),
                }, "chef")
                yield _sse("done", {})
                return

            # Determine emulator-accessible URL
            if app_url and ("convex.cloud" in app_url or "vercel.app" in app_url or "convex.site" in app_url):
                # External deployment — emulator can access directly
                emulator_url = app_url
            elif chef_result.success and run_id in _chef_served_apps:
                emulator_url = f"http://10.0.2.2:8000/api/demo/chef-app/{run_id}"
            else:
                emulator_url = f"http://10.0.2.2:8000/api/demo/showcase-app/{run_id}"

            app_name = body.prompt[:50].strip()
            yield _sse("generation_complete", {
                "type": "generation_complete",
                "run_id": run_id,
                "app_url": app_url or app_url_browser,
                "emulator_url": emulator_url,
                "files_count": len(chef_result.files) if chef_result.success else 1,
                "engine": "chef" if chef_result.success else "html_fallback",
            })

            logger.info(
                "Chef pipeline: app=%s, run_id=%s, device=%s, url=%s",
                app_name, run_id, device_id, emulator_url,
            )

            # ── Phase 2-4: Crawl → Workflow → TestCase → Execution ────
            async for event in _qa_pipeline_service.run_pipeline_for_url(
                emulator_url, app_name, device_id,
            ):
                event_type = event.get("type", "unknown")
                yield _sse(event_type, event)

                if event_type == "pipeline_complete" and "result" in event:
                    _save_pipeline_result(run_id, app_name, event["result"], "chef")

                await asyncio.sleep(0.05)

            yield _sse("done", {})

        except Exception as e:
            logger.error(f"Chef pipeline error: {e}")
            yield _sse("error", {"type": "error", "content": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Pipeline result retrieval ────────────────────────────────────────────────


@router.get("/pipeline-results")
async def list_pipeline_results():
    """List all saved pipeline results (summary only, no full test cases)."""
    summaries = []
    for r in _pipeline_results.values():
        summaries.append({
            "run_id": r["run_id"],
            "app_name": r["app_name"],
            "timestamp": r["timestamp"],
            "total_tests": r["total_tests"],
            "total_workflows": r["total_workflows"],
            "source": r["source"],
        })
    summaries.sort(key=lambda x: x["timestamp"], reverse=True)
    return {"results": summaries}


@router.get("/pipeline-results/{run_id}")
async def get_pipeline_result(run_id: str):
    """Get full pipeline result including all test cases."""
    if run_id not in _pipeline_results:
        raise HTTPException(404, "Result not found")
    return _pipeline_results[run_id]


# ── Active runs (bridge for MCP-initiated pipelines visible on /curated) ─────


@router.get("/active-runs")
async def list_active_runs():
    """List all currently running pipelines (MCP or browser-initiated).

    CuratedDemoPage polls this to discover MCP-initiated runs and auto-subscribe
    to their event streams so the user can watch live emulator activity.
    """
    from .mcp_pipeline import _running_pipelines

    runs = []
    for rid, entry in _running_pipelines.items():
        runs.append({
            "run_id": rid,
            "status": entry["status"],
            "current_stage": entry.get("current_stage", ""),
            "app_name": entry.get("app_name", ""),
            "app_url": entry.get("app_url", ""),
            "app_id": entry.get("app_id", ""),
            "flow_type": entry.get("flow_type", ""),
            "started_at": entry.get("started_at", ""),
            "progress": entry.get("progress", {}),
            "event_count": len(entry.get("events", [])),
            "source": "mcp",
        })
    runs.sort(key=lambda x: x["started_at"], reverse=True)
    return {"runs": runs}


@router.get("/pipeline-stream/{run_id}")
async def stream_pipeline_events(run_id: str):
    """SSE stream for any pipeline run (MCP or browser-initiated).

    Replays all past events then tails new ones in real-time.
    CuratedDemoPage connects here to watch MCP-initiated runs live.
    """
    from .mcp_pipeline import _running_pipelines

    entry = _running_pipelines.get(run_id)
    if not entry:
        raise HTTPException(404, f"No active run: {run_id}")

    async def event_stream():
        cursor = 0
        try:
            while True:
                events = entry.get("events", [])
                # Emit any new events since our cursor
                while cursor < len(events):
                    ev = events[cursor]
                    ev_type = ev.get("type", "unknown")
                    data = json.dumps(ev.get("data", ev))
                    yield f"event: {ev_type}\ndata: {data}\n\n"
                    cursor += 1

                # If run is done, send final status and close
                if entry["status"] in ("complete", "error"):
                    final = {
                        "type": "stream_end",
                        "status": entry["status"],
                        "error": entry.get("error"),
                        "has_result": entry.get("result") is not None,
                    }
                    yield f"event: stream_end\ndata: {json.dumps(final)}\n\n"
                    return

                # Poll interval — check for new events
                await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
