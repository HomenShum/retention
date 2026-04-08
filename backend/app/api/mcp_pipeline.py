"""MCP Pipeline tools — async QA pipeline runner, feedback/annotations, device proxy.

Provides dispatchers called from mcp_server.py for:
  ta.pipeline.*   — run/status/results/list_apps/run_catalog
  ta.feedback.*   — annotate/list/summary
  ta.device.*     — list/lease
  ta.meta.*       — connection_info
"""

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State registries (in-memory, per-process)
# ---------------------------------------------------------------------------

_qa_pipeline_service = None          # Set by main.py on startup
_running_pipelines: Dict[str, dict] = {}   # run_id -> pipeline state
_annotations: Dict[str, List[dict]] = {}   # run_id -> list of annotations

MAX_CONCURRENT_PIPELINES = 2
MAX_COMPLETED_IN_MEMORY = 50  # Evict oldest completed runs beyond this limit

# ---------------------------------------------------------------------------
# Team dashboard SSE broadcast
# ---------------------------------------------------------------------------
_team_event_queues: List[asyncio.Queue] = []


def _broadcast_team_event(event: dict) -> None:
    """Push an event to all connected team dashboard SSE clients."""
    dead = []
    for q in _team_event_queues:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _team_event_queues.remove(q)


def _check_run_access(run_id: str, caller_id: str) -> Optional[str]:
    """Check if caller owns this run. Returns error string or None if allowed."""
    _DENIED = "Run not found or access denied"
    entry = _running_pipelines.get(run_id)
    if entry:
        owner = entry.get("owner_id", "anonymous")
        if owner == "anonymous" or caller_id == "anonymous":
            return None
        return _DENIED if owner != caller_id else None
    try:
        p = _persisted_results.get(run_id)
    except NameError:
        p = None
    if p:
        owner = p.get("owner_id", "anonymous")
        if owner != "anonymous" and caller_id != "anonymous" and owner != caller_id:
            return _DENIED
    return None


# Disk persistence for completed pipeline results
_RESULTS_DIR = Path(__file__).resolve().parents[2] / "data" / "pipeline_results"
_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Disk persistence for feedback annotations
_ANNOTATIONS_DIR = Path(__file__).resolve().parents[2] / "data" / "annotations"
_ANNOTATIONS_DIR.mkdir(parents=True, exist_ok=True)


def _persist_annotations(run_id: str) -> None:
    """Save annotations for a run to disk."""
    annotations = _annotations.get(run_id)
    if not annotations:
        return
    try:
        path = _ANNOTATIONS_DIR / f"{run_id}.json"
        path.write_text(json.dumps(annotations, indent=2))
    except Exception as e:
        logger.warning(f"Failed to persist annotations for {run_id}: {e}")


def _load_annotations(run_id: str) -> List[dict]:
    """Load annotations from disk if not in memory."""
    if run_id in _annotations:
        return _annotations[run_id]
    path = _ANNOTATIONS_DIR / f"{run_id}.json"
    if path.exists():
        try:
            annotations = json.loads(path.read_text())
            _annotations[run_id] = annotations
            return annotations
        except Exception as e:
            logger.warning(f"Failed to load annotations for {run_id}: {e}")
    return []


def _persist_result(run_id: str, data: dict) -> None:
    """Save a completed pipeline result to disk and update run history index."""
    try:
        path = _RESULTS_DIR / f"{run_id}.json"
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info(f"Persisted pipeline result: {path}")
    except Exception as e:
        logger.warning(f"Failed to persist result {run_id}: {e}")

    # Auto-index: rebuild run history so trends are always current
    try:
        from ..agents.qa_pipeline.run_history import build_index
        build_index(force=True)
    except Exception as e:
        logger.debug(f"Run history auto-index skipped: {e}")


def _load_persisted_results() -> Dict[str, dict]:
    """Load all persisted pipeline results from disk."""
    results = {}
    for path in _RESULTS_DIR.glob("*.json"):
        try:
            with open(path) as f:
                data = json.load(f)
            run_id = data.get("run_id", path.stem)
            results[run_id] = data
        except Exception as e:
            logger.warning(f"Failed to load {path}: {e}")
    logger.info(f"Loaded {len(results)} persisted pipeline results")
    return results


# Load persisted results on import
_persisted_results: Dict[str, dict] = _load_persisted_results()


def set_pipeline_service(service) -> None:
    global _qa_pipeline_service
    _qa_pipeline_service = service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _count_running() -> int:
    return sum(1 for p in _running_pipelines.values() if p["status"] == "running")


def _evict_completed() -> None:
    """Remove oldest completed runs from memory when over the limit. Results stay on disk."""
    completed = [
        (rid, entry) for rid, entry in _running_pipelines.items()
        if entry["status"] in ("complete", "error")
    ]
    if len(completed) <= MAX_COMPLETED_IN_MEMORY:
        return
    # Sort by started_at, evict oldest
    completed.sort(key=lambda x: x[1].get("started_at", ""))
    to_evict = len(completed) - MAX_COMPLETED_IN_MEMORY
    for rid, _ in completed[:to_evict]:
        _running_pipelines.pop(rid, None)
        _annotations.pop(rid, None)
    logger.info(f"Evicted {to_evict} completed pipeline runs from memory")


def _get_base_url() -> str:
    """Return the public base URL from env var or fallback to localhost."""
    return os.environ.get("TA_BACKEND_URL", "http://localhost:8000").rstrip("/")


def _view_url(run_id: str) -> str:
    """Build an absolute URL to the backend-served results viewer."""
    return f"{_get_base_url()}/demo/curated?run={run_id}"


def _persist_partial_results(run_id: str, entry: dict, app_name: str, timeout_seconds: int) -> None:
    """Save whatever results accumulated before a timeout so they're not lost."""
    events = entry.get("events", [])
    # Reconstruct partial result from events
    test_results = []
    workflows = []
    for ev in events:
        etype = ev.get("type", "")
        data = ev.get("data", ev)
        if etype == "test_execution_result":
            test_results.append(data)
        elif etype == "workflow_identified":
            workflows.append(data)

    partial = {
        "partial": True,
        "timeout_seconds": timeout_seconds,
        "test_execution_results": test_results,
        "workflows_identified": workflows,
        "total_events": len(events),
        "tests_executed": len(test_results),
        "workflows_found": len(workflows),
    }
    entry["result"] = partial
    _persisted_results[run_id] = partial

    # Write run log
    run_log = {
        "run_id": run_id,
        "status": "timeout",
        "app_name": app_name,
        "total_events": len(events),
        "tests_executed": len(test_results),
        "workflows_found": len(workflows),
        "timeout_seconds": timeout_seconds,
    }
    try:
        log_path = _RESULTS_DIR.parent / "run_logs" / f"{run_id}.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(run_log, indent=2, default=str))
    except Exception as e:
        logger.warning("Failed to persist partial run log: %s", e)
    logger.info("Persisted partial results for %s: %d tests, %d workflows", run_id, len(test_results), len(workflows))


def _validate_app_url(url: str) -> str:
    """Validate app_url to prevent SSRF. Returns the URL or raises ValueError."""
    from urllib.parse import urlparse
    import ipaddress

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http/https URLs are allowed, got: {parsed.scheme}")

    hostname = parsed.hostname or ""
    # Allow localhost and Android emulator host loopback (10.0.2.2)
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "10.0.2.2"):
        return url

    # Block private/reserved IPs
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
            raise ValueError(f"Private/reserved IP addresses are not allowed: {hostname}")
    except ValueError as e:
        if "not allowed" in str(e):
            raise
        # hostname is a domain name, not an IP — allow it
        pass

    # Block cloud metadata endpoints
    if hostname in ("169.254.169.254", "metadata.google.internal"):
        raise ValueError("Cloud metadata endpoints are blocked")

    return url


def _emulator_url(url: str) -> str:
    """Rewrite localhost/127.0.0.1 URLs to 10.0.2.2 so the Android emulator can reach the host."""
    if not url:
        return url
    return re.sub(
        r"(https?://)(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?",
        r"\g<1>10.0.2.2\3",
        url,
    )


def _load_demo_apps() -> Dict[str, dict]:
    """Load the demo apps catalog as {app_id: app_meta}."""
    for candidate in [
        Path(__file__).parent.parent.parent / "data" / "demo_apps.json",
        Path("backend/data/demo_apps.json"),
    ]:
        if candidate.exists():
            with open(candidate) as f:
                raw = json.load(f)
            if isinstance(raw, list):
                return {a["id"]: a for a in raw}
            if isinstance(raw, dict):
                # Could be {id: meta} already, or {"apps": [...]}
                if "apps" in raw and isinstance(raw["apps"], list):
                    return {a["id"]: a for a in raw["apps"]}
                return raw
    return {}


# ---------------------------------------------------------------------------
# Async pipeline background runner
# ---------------------------------------------------------------------------

def _create_pipeline_task(coro, run_id: str):
    """Create an asyncio task with error callback to prevent silent failures."""
    task = asyncio.create_task(coro, name=f"pipeline-{run_id}")

    def _on_task_done(t: asyncio.Task):
        if t.cancelled():
            logger.warning("Pipeline task %s was cancelled", run_id)
            entry = _running_pipelines.get(run_id)
            if entry and entry["status"] == "running":
                entry["status"] = "error"
                entry["error"] = "Task cancelled"
        elif t.exception():
            exc = t.exception()
            logger.error("Pipeline task %s failed silently: %s", run_id, exc, exc_info=exc)
            entry = _running_pipelines.get(run_id)
            if entry and entry["status"] == "running":
                entry["status"] = "error"
                entry["error"] = str(exc)

    task.add_done_callback(_on_task_done)
    return task


async def run_playwright_pipeline(url: str, app_name: str = "Web App",
                                   skip_stages: Optional[List[str]] = None,
                                   flow_type: str = "playwright",
                                   device_id: str = "") -> str:
    """Public API: start a Playwright pipeline and return the run_id.

    Used by the validation gate to trigger QA without going through MCP dispatch.
    skip_stages: list of stage names to skip, e.g. ["WORKFLOW", "TESTCASE", "EXECUTION"]
    """
    run_id = f"pw-{uuid.uuid4().hex[:8]}"
    _skip = skip_stages or []
    _running_pipelines[run_id] = {
        "run_id": run_id,
        "owner_id": "validation-gate",
        "status": "running",
        "current_stage": "CRAWL",
        "events": [],
        "result": None,
        "progress": {},
        "started_at": _now_iso(),
        "error": None,
        "app_url": url,
        "app_name": app_name,
        "flow_type": flow_type,
        "timeout_seconds": 3600,
    }
    _create_pipeline_task(_run_playwright_pipeline(run_id, url=url, app_name=app_name, skip_stages=_skip), run_id)
    return run_id


async def _run_playwright_pipeline(run_id: str, *, url: str, app_name: str = "Web App",
                                    timeout_seconds: int = 3600, model_override: str = "",
                                    skip_stages: Optional[List[str]] = None):
    """Run QA pipeline via Playwright (headless browser) — no emulator needed.

    Uses the same pipeline stages as the emulator path but with Playwright
    for page access instead of ADB + Chrome DevTools Protocol.
    """
    entry = _running_pipelines[run_id]

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        entry["status"] = "error"
        entry["error"] = "Playwright not installed. Run: pip install playwright && playwright install chromium"
        return

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--single-process"])
            page = await browser.new_page(viewport={"width": 375, "height": 812})

            # ── Stage: CRAWL (Playwright) ────────────────────────────────
            entry["current_stage"] = "CRAWL"
            entry["events"].append({"type": "stage_transition", "timestamp": _now_iso(), "data": {"to_stage": "CRAWL"}})

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)  # let SPA hydrate
            title = await page.title()

            # Extract page structure
            page_text = await page.inner_text("body")
            buttons = await page.query_selector_all("button")
            inputs = await page.query_selector_all("input, textarea, select")
            links = await page.query_selector_all("a[href]")
            forms = await page.query_selector_all("form")

            # Build component list
            components = []
            for i, btn in enumerate(buttons):
                text = (await btn.inner_text()).strip()[:50]
                components.append({"type": "button", "text": text, "id": f"btn-{i}", "interactive": True})
            for i, inp in enumerate(inputs):
                inp_type = await inp.get_attribute("type") or "text"
                placeholder = await inp.get_attribute("placeholder") or ""
                components.append({"type": f"input-{inp_type}", "text": placeholder[:50], "id": f"inp-{i}", "interactive": True})
            for i, link in enumerate(links[:20]):
                text = (await link.inner_text()).strip()[:50]
                href = await link.get_attribute("href") or ""
                components.append({"type": "link", "text": text, "href": href[:100], "id": f"link-{i}", "interactive": True})

            # Screenshot
            screenshot_path = f"/tmp/pw-{run_id}-crawl.png"
            await page.screenshot(path=screenshot_path)

            crawl_result = {
                "app_name": app_name,
                "url": url,
                "title": title,
                "screens": [{"screen_id": "screen_001", "screen_name": title or "Home", "components": components}],
                "total_screens": 1,
                "total_components": len(components),
                "text_length": len(page_text),
            }

            entry["events"].append({
                "type": "crawl_progress", "timestamp": _now_iso(),
                "data": {"screens_found": 1, "components_found": len(components), "current_screen": title},
            })

            # ── Stage: WORKFLOW (analyze structure) ──────────────────────
            _skip_set = set(skip_stages or [])
            if "WORKFLOW" in _skip_set:
                # Explore-only: finish after CRAWL, save trajectory stub
                entry["current_stage"] = "COMPLETE"
                entry["status"] = "complete"
                entry["completed_at"] = _now_iso()
                entry["result"] = {
                    "app_name": app_name, "crawl_result": crawl_result,
                    "stages_run": ["CRAWL"], "stages_skipped": list(_skip_set),
                    "test_cases": [], "execution": [], "summary": {"passed": 0, "failed": 0, "total": 0, "pass_rate": 0.0},
                }
                entry["events"].append({"type": "pipeline_complete", "timestamp": _now_iso(), "data": {"stages_run": ["CRAWL"]}})
                await browser.close()
                return

            entry["current_stage"] = "WORKFLOW"
            entry["events"].append({"type": "stage_transition", "timestamp": _now_iso(), "data": {"to_stage": "WORKFLOW"}})

            # Derive workflows from page structure
            workflows = []
            if any(c["type"].startswith("input") for c in components):
                workflows.append({"name": "Form Input", "description": "Fill and submit forms", "screens": ["screen_001"]})
            if any(c["type"] == "button" for c in components):
                workflows.append({"name": "Button Actions", "description": "Click buttons and verify outcomes", "screens": ["screen_001"]})
            if any(c["type"] == "link" for c in components):
                workflows.append({"name": "Navigation", "description": "Follow links and verify destinations", "screens": ["screen_001"]})

            # ── Stage: TESTCASE (generate from structure) ────────────────
            entry["current_stage"] = "TESTCASE"
            entry["events"].append({"type": "stage_transition", "timestamp": _now_iso(), "data": {"to_stage": "TESTCASE"}})

            test_cases = []
            # Generate test cases from components
            for i, comp in enumerate(components):
                if comp["type"] == "button":
                    test_cases.append({
                        "test_id": f"TC-{i+1:03d}",
                        "name": f"Click '{comp['text'] or 'button'}'",
                        "priority": "P1",
                        "category": "interaction",
                        "steps": [
                            {"action": "navigate", "target": url},
                            {"action": "click", "target": comp["text"] or f"button #{i}"},
                            {"action": "verify", "expected": "Page responds without error"},
                        ],
                    })
                elif comp["type"].startswith("input"):
                    test_cases.append({
                        "test_id": f"TC-{i+1:03d}",
                        "name": f"Fill '{comp['text'] or 'input'}'",
                        "priority": "P1",
                        "category": "form",
                        "steps": [
                            {"action": "navigate", "target": url},
                            {"action": "fill", "target": comp["text"] or f"input #{i}", "value": "test input"},
                            {"action": "verify", "expected": "Input accepts value"},
                        ],
                    })

            # ── Stage: EXECUTION (run tests via Playwright) ──────────────
            entry["current_stage"] = "EXECUTION"
            entry["events"].append({"type": "stage_transition", "timestamp": _now_iso(), "data": {"to_stage": "EXECUTION"}})

            results = []
            for tc in test_cases:
                try:
                    # Re-navigate for clean state
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(2000)  # let SPA hydrate

                    passed = True
                    error_msg = ""

                    for step in tc["steps"]:
                        if step["action"] == "click":
                            target = step["target"]
                            el = await page.query_selector(f"button:has-text('{target}')") if target else None
                            if el:
                                # Check visibility — skip hidden elements (inside modals/dialogs)
                                is_visible = await el.is_visible()
                                if is_visible:
                                    await el.click(timeout=3000)
                                    await page.wait_for_timeout(500)
                                else:
                                    # Element exists but not visible — not an app bug
                                    pass
                            else:
                                # Try by index
                                btns = await page.query_selector_all("button")
                                if btns:
                                    is_visible = await btns[0].is_visible()
                                    if is_visible:
                                        await btns[0].click(timeout=3000)
                                        await page.wait_for_timeout(500)

                        elif step["action"] == "fill":
                            target = step["target"]
                            inp = None
                            if target:
                                # CSS.escape-safe: use XPath for complex placeholders
                                # to avoid CSS selector parse errors on quotes/newlines/em-dashes
                                safe_target = target.replace("'", "\\'").replace("\n", " ")[:40]
                                try:
                                    inp = await page.query_selector(f"input[placeholder*='{safe_target}']")
                                except Exception:
                                    pass
                                if not inp:
                                    try:
                                        inp = await page.query_selector(f"textarea[placeholder*='{safe_target}']")
                                    except Exception:
                                        pass
                                if not inp:
                                    # Fallback: find all inputs/textareas, match by partial placeholder
                                    all_inputs = await page.query_selector_all("input, textarea")
                                    for candidate in all_inputs:
                                        ph = await candidate.get_attribute("placeholder") or ""
                                        if target[:20].lower() in ph.lower():
                                            inp = candidate
                                            break
                            if not inp:
                                inps = await page.query_selector_all("input, textarea")
                                inp = inps[0] if inps else None
                            if inp:
                                # Check visibility — skip hidden inputs (inside modals)
                                is_visible = await inp.is_visible()
                                if is_visible:
                                    await inp.fill(step.get("value", "test"), timeout=3000)
                                    await page.wait_for_timeout(300)
                                # If not visible, skip silently — not an app bug

                    # Check for JS errors
                    # (page.on("pageerror") would be better but we check console)

                except Exception as exec_err:
                    err_str = str(exec_err)
                    # Timeouts on hidden elements are not app bugs
                    if "Timeout" in err_str:
                        pass  # Skip — likely hidden modal element
                    else:
                        passed = False
                        error_msg = err_str[:200]

                results.append({
                    "test_id": tc["test_id"],
                    "name": tc["name"],
                    "status": "passed" if passed else "failed",
                    "priority": tc["priority"],
                    "error": error_msg if not passed else None,
                })

                entry["events"].append({
                    "type": "test_result", "timestamp": _now_iso(),
                    "data": {"test_id": tc["test_id"], "name": tc["name"], "status": "passed" if passed else "failed"},
                })

            await browser.close()

        # ── Compile results ──────────────────────────────────────────
        total = len(results)
        passed = sum(1 for r in results if r["status"] == "passed")
        failed = total - passed

        final_result = {
            "app_name": app_name,
            "app_url": url,
            "test_cases": test_cases,
            "workflows": workflows,
            "total_tests": total,
            "execution": results,
            "by_workflow": {},
            "by_priority": {},
            "by_category": {},
            "token_usage": {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0},
            "summary": {"total": total, "passed": passed, "failed": failed, "pass_rate": round(passed / max(total, 1), 4)},
        }

        entry["status"] = "complete"
        completed_at = _now_iso()
        entry["completed_at"] = completed_at
        entry["result"] = final_result

        try:
            t0 = datetime.fromisoformat(entry["started_at"])
            t1 = datetime.fromisoformat(completed_at)
            entry["duration_s"] = round((t1 - t0).total_seconds(), 2)
        except Exception:
            entry["duration_s"] = None

        entry["event_count"] = len(entry.get("events", []))

        # Persist
        _persist_result(run_id, {
            "run_id": run_id,
            "app_name": app_name,
            "app_url": url,
            "flow_type": "playwright",
            "started_at": entry.get("started_at"),
            "completed_at": completed_at,
            "duration_s": entry.get("duration_s"),
            "event_count": entry["event_count"],
            "result": final_result,
        })
        _persisted_results[run_id] = entry

        # Auto-generate handoff
        try:
            bundle = format_compact_bundle(run_id)
            if "error" not in bundle:
                _persist_run_log(run_id, bundle)
        except Exception:
            pass
        try:
            await build_handoff_md(run_id)
        except Exception:
            pass

        logger.info(f"Playwright pipeline {run_id} complete: {passed}/{total} passed, {entry.get('duration_s')}s")

    except Exception as exc:
        logger.exception("Playwright pipeline %s failed", run_id)
        entry["status"] = "error"
        entry["error"] = str(exc)[:300]
    finally:
        entry["events"] = entry.get("events", [])[-20:]
        _evict_completed()
        import gc
        gc.collect()


async def _run_pipeline_background(run_id: str, *, app_url: str = None,
                                    app_name: str = "Custom App",
                                    app_id: str = None,
                                    app_package: str = None,
                                    device_id: str = None,
                                    max_crawl_turns: int = 80,
                                    timeout_seconds: int = 3600,
                                    entry_url: str = None,
                                    scope_hint: str = None,
                                    workflow_ids: list = None,
                                    model_override: str = ""):
    """Run pipeline in background task, collecting SSE events into the registry."""
    entry = _running_pipelines[run_id]

    # ── ActionLedger: per-action telemetry ──
    from app.agents.qa_pipeline.action_ledger import ActionLedger
    _surface = "android_emulator" if app_package else "browser"
    ledger = ActionLedger(
        run_id=run_id,
        workflow_family="mobile_app" if app_package else "kyb_aml",
        setup_variant="B_ta_harness",
        model=model_override or "gpt-5.4-mini",
        surface=_surface,
        app_name=app_name,
    )

    try:
        # Auto-detect device if not specified
        if not device_id and _qa_pipeline_service:
            try:
                devices_text = await _qa_pipeline_service.mobile_mcp_client.list_available_devices()
                matches = re.findall(r"(emulator-\d+)", devices_text)
                if matches:
                    device_id = matches[0]
            except Exception:
                pass

        if not device_id:
            entry["status"] = "error"
            entry["error"] = "No emulator device found"
            return

        # Determine pipeline mode
        if app_package:
            # Direct native Android app — launch by package name, no catalog lookup
            gen = _qa_pipeline_service.run_pipeline(
                app_name, app_package, device_id,
                target_workflows=None, crawl_hints=None,
                max_crawl_turns=max_crawl_turns,
            )
        elif app_id:
            # Catalog app
            catalog = _load_demo_apps()
            app_meta = catalog.get(app_id)
            if not app_meta:
                entry["status"] = "error"
                entry["error"] = f"Unknown app_id: {app_id}"
                return
            app_name = app_meta.get("name", app_id)
            package_name = app_meta.get("package", "")
            app_type = app_meta.get("type", "native")
            target_workflows = app_meta.get("target_workflows")
            crawl_hints = app_meta.get("crawl_hints")
            max_crawl_turns = app_meta.get("max_crawl_turns", 30)

            if app_type == "web":
                app_url = app_meta.get("url", "")
                gen = _qa_pipeline_service.run_pipeline_for_url(
                    app_url, app_name, device_id, max_crawl_turns=max_crawl_turns,
                )
            else:
                gen = _qa_pipeline_service.run_pipeline(
                    app_name, package_name, device_id,
                    target_workflows, crawl_hints, max_crawl_turns
                )
        elif app_url:
            gen = _qa_pipeline_service.run_pipeline_for_url(
                app_url, app_name, device_id,
                max_crawl_turns=max_crawl_turns,
                entry_url=entry_url,
                scope_hint=scope_hint,
                workflow_ids=workflow_ids,
                model_override=model_override,
            )
        else:
            entry["status"] = "error"
            entry["error"] = "Either app_url or app_id is required"
            return

        # Consume SSE events with timeout enforcement
        async def _consume_events():
            async for event in gen:
                event_type = event.get("type", "unknown")
                entry["events"].append({
                    "type": event_type,
                    "timestamp": _now_iso(),
                    "data": event,
                })

                # Feed into ActionLedger for per-action telemetry
                try:
                    ledger.from_pipeline_event(event)
                except Exception:
                    pass  # Never let telemetry break the pipeline

                # Track stage transitions + checkpoint
                if event_type == "stage_transition":
                    prev_stage = entry["current_stage"]
                    entry["current_stage"] = event.get("to_stage", entry["current_stage"])
                    # Checkpoint: persist partial results at each stage boundary
                    # so crashes don't lose all progress
                    try:
                        _persist_result(run_id, {
                            "run_id": run_id,
                            "app_name": app_name,
                            "status": "in_progress",
                            "current_stage": entry["current_stage"],
                            "completed_stages": [prev_stage],
                            "event_count": len(entry["events"]),
                            "checkpoint_at": _now_iso(),
                            "started_at": entry.get("started_at"),
                        })
                    except Exception:
                        pass  # Best-effort checkpoint
                elif event_type == "crawl_progress":
                    entry["progress"] = {
                        "screens": event.get("screens_found", 0),
                        "components": event.get("components_found", 0),
                        "current_screen": event.get("current_screen"),
                    }
                elif event_type == "pipeline_complete":
                    result = event.get("result", {})
                    entry["result"] = result
                    # Also save to shared demo results store
                    try:
                        from .demo import _save_pipeline_result
                        _save_pipeline_result(run_id, app_name, result, source="mcp")
                    except Exception:
                        pass

        try:
            await asyncio.wait_for(_consume_events(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning("Pipeline %s timed out after %ds", run_id, timeout_seconds)
            entry["status"] = "error"
            entry["error"] = f"Pipeline timed out after {timeout_seconds}s"
            _persist_partial_results(run_id, entry, app_name, timeout_seconds)
            # Resource cleanup: free event list from memory
            entry["events"] = entry["events"][-20:]  # Keep only last 20 for debugging
            _evict_completed()
            return
        except Exception as pipeline_err:
            logger.error("Pipeline %s crashed: %s", run_id, pipeline_err, exc_info=True)
            entry["status"] = "error"
            entry["error"] = f"Pipeline crashed: {str(pipeline_err)[:200]}"
            _persist_partial_results(run_id, entry, app_name, timeout_seconds)
            entry["events"] = entry["events"][-20:]
            _evict_completed()
            return

        entry["status"] = "complete"
        completed_at = _now_iso()

        # ── Compute run metrics ──────────────────────────────────
        events = entry.get("events", [])
        stage_timings: Dict[str, Any] = {}
        prev_stage_time = entry.get("started_at", "")
        prev_stage_name = "CRAWL"
        tool_call_count = 0
        agent_reasoning_count = 0
        for ev in events:
            ev_type = ev.get("type", "")
            if ev_type in ("tool_call", "tool_call_output"):
                tool_call_count += 1
            if ev_type in ("agent_reasoning", "trajectory_plan", "trajectory_progress"):
                agent_reasoning_count += 1
            if ev_type == "stage_transition":
                stage_timings[prev_stage_name] = {
                    "started_at": prev_stage_time,
                    "ended_at": ev.get("timestamp", ""),
                }
                prev_stage_name = ev.get("data", {}).get("to_stage", "UNKNOWN")
                prev_stage_time = ev.get("timestamp", "")
        # Close the last stage
        stage_timings[prev_stage_name] = {
            "started_at": prev_stage_time,
            "ended_at": completed_at,
        }

        # Duration in seconds
        try:
            t0 = datetime.fromisoformat(entry["started_at"])
            t1 = datetime.fromisoformat(completed_at)
            duration_s = round((t1 - t0).total_seconds(), 2)
        except Exception:
            duration_s = None

        # Token cost: aggregate from usage telemetry for this run's time window
        token_metrics = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0}
        try:
            from ..services.usage_telemetry import _iter_events
            run_start = datetime.fromisoformat(entry["started_at"])
            run_end = datetime.fromisoformat(completed_at)
            for tel_ev in _iter_events(days=1):
                try:
                    ev_ts = datetime.fromisoformat(tel_ev["timestamp"])
                    if run_start <= ev_ts <= run_end:
                        token_metrics["input_tokens"] += tel_ev.get("input_tokens", 0)
                        token_metrics["output_tokens"] += tel_ev.get("output_tokens", 0)
                        token_metrics["total_tokens"] += tel_ev.get("total_tokens", 0)
                        token_metrics["estimated_cost_usd"] += tel_ev.get("estimated_cost_usd", 0.0)
                except Exception:
                    pass
            token_metrics["estimated_cost_usd"] = round(token_metrics["estimated_cost_usd"], 6)
        except Exception as te:
            logger.warning(f"Failed to aggregate token metrics for {run_id}: {te}")

        entry["completed_at"] = completed_at
        entry["duration_s"] = duration_s
        entry["tool_call_count"] = tool_call_count
        entry["agent_reasoning_count"] = agent_reasoning_count
        entry["stage_timings"] = stage_timings
        entry["token_metrics"] = token_metrics

        # ── Save ActionLedger ──
        try:
            ledger_path = ledger.save()
            rollup = ledger.get_rollup()
            entry["action_ledger_path"] = ledger_path
            entry["action_rollup"] = rollup
            logger.info(
                f"Action ledger: {rollup.get('actions_total', 0)} actions, "
                f"{rollup.get('memory_hits', 0)} memory hits, "
                f"avg latency {rollup.get('avg_action_latency_ms', 0)}ms"
            )
        except Exception as le:
            logger.warning(f"Failed to save action ledger for {run_id}: {le}")

        # Persist to disk — store original user URL, not emulator-rewritten URL
        original_app_url = entry.get("original_app_url", entry.get("app_url", ""))
        if entry.get("result"):
            _persist_result(run_id, {
                "run_id": run_id,
                "app_name": app_name,
                "app_url": original_app_url,
                "flow_type": entry.get("flow_type", ""),
                "started_at": entry.get("started_at", ""),
                "completed_at": completed_at,
                "duration_s": duration_s,
                "tool_call_count": tool_call_count,
                "agent_reasoning_count": agent_reasoning_count,
                "stage_timings": stage_timings,
                "token_metrics": token_metrics,
                "event_count": len(events),
                "result": entry["result"],
            })
            _persisted_results[run_id] = entry
            # Auto-generate run log for Claude Code consumption
            try:
                bundle = format_compact_bundle(run_id)
                if "error" not in bundle:
                    _persist_run_log(run_id, bundle)
            except Exception as log_err:
                logger.warning(f"Failed to auto-generate run log for {run_id}: {log_err}")
            # Auto-generate handoff markdown
            try:
                await build_handoff_md(run_id)
            except Exception as handoff_err:
                logger.warning(f"Failed to auto-generate handoff for {run_id}: {handoff_err}")

            # ── Bridge: write to replay_results/ for team dashboard ──
            # This makes every pipeline run visible in /api/savings/team
            try:
                from ..agents.qa_pipeline.trajectory_replay import _REPLAY_DIR, FULL_RUN_BASELINE
                import os as _os
                import subprocess as _subprocess
                _REPLAY_DIR.mkdir(parents=True, exist_ok=True)

                total_tokens = token_metrics.get("total_tokens", 0)
                if total_tokens == 0:
                    # Estimate from event count if telemetry didn't capture
                    total_tokens = len(events) * 200  # rough estimate

                # Detect user for team attribution
                user_email = ""
                try:
                    _git = _subprocess.run(["git", "config", "user.email"], capture_output=True, text=True, timeout=3)
                    if _git.returncode == 0:
                        user_email = _git.stdout.strip()
                except Exception:
                    pass
                user_email = user_email or _os.environ.get("TA_USER_EMAIL", "local")

                baseline_tokens = FULL_RUN_BASELINE["tokens"]
                baseline_time = FULL_RUN_BASELINE["time_seconds"]
                token_savings_pct = max(0, (baseline_tokens - total_tokens) / baseline_tokens * 100) if baseline_tokens > 0 else 0
                time_savings_pct = max(0, (baseline_time - duration_s) / baseline_time * 100) if baseline_time > 0 else 0

                replay_result = {
                    "trajectory_id": f"pipeline_{run_id}",
                    "replay_run_id": run_id,
                    "workflow": app_name or run_id,
                    "success": True,
                    "total_steps": len(events),
                    "steps_executed": len(events),
                    "steps_matched": 0,
                    "steps_drifted": 0,
                    "drift_point": None,
                    "drift_score": 0.0,
                    "fallback_to_exploration": True,  # This was exploration, not replay
                    "token_usage": {
                        "estimated_replay_tokens": total_tokens,
                        "full_run_baseline_tokens": baseline_tokens,
                    },
                    "time_seconds": duration_s,
                    "comparison_with_full": {
                        "token_savings_pct": round(token_savings_pct, 1),
                        "time_savings_pct": round(time_savings_pct, 1),
                        "tokens_full": baseline_tokens,
                        "tokens_replay": total_tokens,
                        "time_full_s": baseline_time,
                        "time_replay_s": round(duration_s, 1),
                        "baseline_source": "estimated",
                    },
                    "per_step_results": [],
                    "timestamp": completed_at,
                    "metadata": {
                        "created_by": user_email,
                        "replayed_by": None,
                        "is_replay": False,
                        "run_number": 1,
                        "flow_type": entry.get("flow_type", ""),
                        "app_name": app_name,
                    },
                }
                import json as _json
                replay_path = _REPLAY_DIR / f"{run_id}.json"
                replay_path.write_text(_json.dumps(replay_result, indent=2, default=str))
                logger.info(f"Bridge: wrote pipeline {run_id} to replay_results for team dashboard")

                # ── Broadcast to team dashboard SSE ──
                _broadcast_team_event({
                    "type": "run_complete",
                    "run_id": run_id,
                    "app_name": app_name,
                    "peer": user_email,
                    "is_replay": False,
                    "tokens": total_tokens,
                    "duration_s": round(duration_s, 1),
                    "timestamp": completed_at,
                })
            except Exception as bridge_err:
                logger.warning(f"Failed to bridge {run_id} to replay_results: {bridge_err}")

    except Exception as exc:
        logger.exception("Pipeline %s failed", run_id)
        entry["status"] = "error"
        entry["error"] = str(exc)
    finally:
        # Resource cleanup: trim event list to save memory
        # Full events are already persisted to disk via _persist_result
        if entry.get("status") in ("complete", "error"):
            entry["events"] = entry.get("events", [])[-20:]
        _evict_completed()
        import gc
        gc.collect()  # Explicit GC after pipeline to free crawl closure data


# ---------------------------------------------------------------------------
# Pipeline dispatchers
# ---------------------------------------------------------------------------

async def dispatch_pipeline(tool: str, args: Dict[str, Any]) -> Any:
    """Handle ta.pipeline.* tools."""
    caller_id = args.pop("_caller_id", "anonymous")

    # ── ta.pipeline.replay_gif ──────────────────────────────────────────
    if tool == "ta.pipeline.replay_gif":
        run_id = args.get("run_id")
        if not run_id:
            return {"error": "run_id is required"}
        fps = float(args.get("fps", 0.5))
        max_width = int(args.get("max_width", 1280))
        try:
            from ..services.gif_replay import generate_replay_gif, REPLAY_DIR
            path = generate_replay_gif(run_id, fps=fps, max_width=max_width)
            meta_path = REPLAY_DIR / f"{run_id}.json"
            meta = {}
            if meta_path.exists():
                import json as _json
                with open(meta_path) as f:
                    meta = _json.load(f)
            return {
                "run_id": run_id,
                "gif_url": f"/api/replays/{run_id}",
                "frames": meta.get("frames", 0),
                "size_kb": meta.get("size_kb", 0),
                "created": meta.get("created", ""),
                "message": f"Replay GIF generated with {meta.get('frames', 0)} frames ({meta.get('size_kb', 0)} KB). "
                           f"View at /api/replays/{run_id}",
            }
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception(f"Failed to generate replay GIF for {run_id}")
            return {"error": f"GIF generation failed: {e}"}

    # ── ta.pipeline.failure_bundle ─────────────────────────────────────
    if tool == "ta.pipeline.failure_bundle":
        run_id = args.get("run_id")
        if not run_id:
            return {"error": "run_id is required"}
        bundle = format_compact_bundle(run_id)
        if "error" not in bundle:
            # Enrich with contextual graph data (prior runs + smart attribution)
            try:
                import glob as _glob_mod
                from ..services.context_graph import ContextGraph
                _graph_files = sorted(_glob_mod.glob(str(Path(__file__).parent.parent.parent / "data" / "context_graphs" / "*.json")))
                if _graph_files:
                    _graph = ContextGraph.load(_graph_files[-1])
                else:
                    _graph = None
            except Exception:
                _graph = None
            if _graph is not None:
                _enrich_bundle_with_graph(bundle, _graph)
            _persist_run_log(run_id, bundle)
        return bundle

    # ── ta.pipeline.run_log ────────────────────────────────────────────
    if tool == "ta.pipeline.run_log":
        run_id = args.get("run_id")
        if not run_id:
            logs = []
            for p in sorted(_RUN_LOGS_DIR.glob("*.json"), reverse=True)[:20]:
                try:
                    with open(p) as f:
                        entry = json.load(f)
                    logs.append({
                        "run_id": entry.get("run_id", p.stem),
                        "app_name": entry.get("app_name", ""),
                        "timestamp": entry.get("timestamp", ""),
                        "duration_s": entry.get("duration_s", 0),
                        "summary": entry.get("compact_bundle", {}).get("summary", {}),
                    })
                except Exception:
                    pass
            return {"run_logs": logs, "total": len(logs)}
        path = _RUN_LOGS_DIR / f"{run_id}.json"
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception as e:
                return {"error": f"Failed to read run log: {e}"}
        bundle = format_compact_bundle(run_id)
        if "error" not in bundle:
            _persist_run_log(run_id, bundle)
            return bundle
        return {"error": f"No run log for run_id: {run_id}"}

    if tool == "ta.pipeline.list_apps":
        catalog = _load_demo_apps()
        return [
            {
                "app_id": app_id,
                "name": meta.get("name", app_id),
                "package": meta.get("package", ""),
                "type": meta.get("type", "native"),
                "description": meta.get("description", ""),
                "url": meta.get("url"),
            }
            for app_id, meta in catalog.items()
        ]

    if tool == "ta.pipeline.run":
        if not _qa_pipeline_service:
            return {"error": "Pipeline service not initialized (no emulator connected)"}
        if _count_running() >= MAX_CONCURRENT_PIPELINES:
            return {"error": f"Max {MAX_CONCURRENT_PIPELINES} concurrent pipelines. Try again later.", "queued": True}

        app_url = args.get("app_url")
        if not app_url:
            return {"error": "app_url is required"}

        try:
            app_url = _validate_app_url(app_url)
        except ValueError as e:
            return {"error": str(e)}

        # Rewrite localhost URLs so the Android emulator can reach the host machine
        app_url = _emulator_url(app_url)

        run_id = f"mcp-{uuid.uuid4().hex[:8]}"
        _running_pipelines[run_id] = {
            "run_id": run_id,
            "owner_id": caller_id,
            "status": "running",
            "current_stage": "CRAWL",
            "events": [],
            "result": None,
            "progress": {},
            "started_at": _now_iso(),
            "error": None,
            "app_url": app_url,
            "app_name": args.get("app_name", "Custom App"),
        }
        # Scoped crawl parameters
        entry_url = args.get("entry_url")
        if entry_url:
            try:
                entry_url = _validate_app_url(entry_url)
            except ValueError:
                entry_url = entry_url  # Relative path, will be resolved later
            entry_url = _emulator_url(entry_url) if entry_url.startswith("http") else entry_url

        scope_hint = args.get("scope_hint")
        workflow_ids_str = args.get("workflow_ids", "")
        workflow_ids = [w.strip() for w in workflow_ids_str.split(",") if w.strip()] if workflow_ids_str else None
        crawl_turns = int(args.get("max_crawl_turns", 80))

        _create_pipeline_task(_run_pipeline_background(
            run_id,
            app_url=app_url,
            app_name=args.get("app_name", "Custom App"),
            device_id=args.get("device_id"),
            entry_url=entry_url,
            scope_hint=scope_hint,
            workflow_ids=workflow_ids,
            max_crawl_turns=crawl_turns,
        ), run_id)
        return {
            "run_id": run_id,
            "status": "running",
            "view_url": _view_url(run_id),
            "stream_url": f"/api/demo/pipeline-stream/{run_id}",
            "message": "Pipeline started. Open view_url in a browser to watch live. Poll ta.pipeline.status for progress.",
        }

    if tool == "ta.pipeline.run_catalog":
        if not _qa_pipeline_service:
            return {"error": "Pipeline service not initialized (no emulator connected)"}
        if _count_running() >= MAX_CONCURRENT_PIPELINES:
            return {"error": f"Max {MAX_CONCURRENT_PIPELINES} concurrent pipelines. Try again later.", "queued": True}

        app_id = args.get("app_id")
        if not app_id:
            return {"error": "app_id is required"}

        run_id = f"mcp-{uuid.uuid4().hex[:8]}"
        catalog = _load_demo_apps()
        app_meta = catalog.get(app_id, {})
        _running_pipelines[run_id] = {
            "run_id": run_id,
            "owner_id": caller_id,
            "status": "running",
            "current_stage": "CRAWL",
            "events": [],
            "result": None,
            "progress": {},
            "started_at": _now_iso(),
            "error": None,
            "app_id": app_id,
            "app_name": app_meta.get("name", app_id),
        }
        _create_pipeline_task(_run_pipeline_background(
            run_id,
            app_id=app_id,
            device_id=args.get("device_id"),
        ), run_id)
        return {
            "run_id": run_id,
            "status": "running",
            "app_id": app_id,
            "view_url": _view_url(run_id),
            "stream_url": f"/api/demo/pipeline-stream/{run_id}",
            "message": "Pipeline started. Open view_url in a browser to watch live. Poll ta.pipeline.status for progress.",
        }

    if tool == "ta.pipeline.status":
        run_id = args.get("run_id")
        if not run_id:
            return {"error": "run_id is required"}
        access_err = _check_run_access(run_id, caller_id)
        if access_err:
            return {"error": access_err}
        entry = _running_pipelines.get(run_id)
        if not entry:
            return {"error": f"Unknown run_id: {run_id}"}
        # Return last 10 events to keep response small
        recent_events = entry["events"][-10:] if entry["events"] else []

        # Extract recent screenshot descriptions from register_screen events
        recent_screens = []
        for ev in reversed(entry["events"]):
            if len(recent_screens) >= 3:
                break
            data = ev.get("data", {})
            if ev.get("type") == "tool_call_output" and data.get("tool_name") == "register_screen":
                output = data.get("tool_output", "")
                if "Registered screen_" in output:
                    recent_screens.append({
                        "screen": output[:200],
                        "timestamp": ev.get("timestamp", ""),
                    })

        # Check for memory hit/miss events
        memory_info = None
        for ev in entry["events"]:
            data = ev.get("data", {})
            if ev.get("type") == "stage_activity" and data.get("stage") == "MEMORY":
                memory_info = {
                    "activity": data.get("activity", ""),
                    "message": data.get("message", ""),
                }
                break

        resp = {
            "run_id": run_id,
            "status": entry["status"],
            "current_stage": entry["current_stage"],
            "progress": entry.get("progress", {}),
            "started_at": entry["started_at"],
            "error": entry.get("error"),
            "event_count": len(entry["events"]),
            "recent_events": [{"type": e["type"], "timestamp": e["timestamp"]} for e in recent_events],
            "has_result": entry["result"] is not None,
            "recent_screens": recent_screens,
            "view_url": _view_url(run_id),
            "view_message": "Open this URL in a browser to watch the live emulator run",
        }
        if memory_info:
            resp["exploration_memory"] = memory_info
        return resp

    if tool == "ta.pipeline.results":
        run_id = args.get("run_id")
        if run_id:
            access_err = _check_run_access(run_id, caller_id)
            if access_err:
                return {"error": access_err}
            # Check running pipelines first
            entry = _running_pipelines.get(run_id)
            if entry and entry["result"]:
                result = entry["result"]
                if isinstance(result, dict):
                    result["view_url"] = _view_url(run_id)
                return result
            # Check in-memory persisted results
            persisted = _persisted_results.get(run_id)
            if persisted:
                # If it has run_id at top level, return full object (disk format)
                if persisted.get("run_id"):
                    return persisted
                result = persisted.get("result", persisted)
                if isinstance(result, dict):
                    result["view_url"] = _view_url(run_id)
                return result
            # Fall back to disk-persisted pipeline results (full file, not just inner result)
            try:
                disk_path = Path(__file__).resolve().parents[3] / "data" / "pipeline_results" / f"{run_id}.json"
                if disk_path.exists():
                    disk_data = json.loads(disk_path.read_text())
                    _persisted_results[run_id] = disk_data  # cache for next call
                    # Return the full object (run_id, duration_s, token_metrics, result, etc.)
                    return disk_data
            except Exception:
                pass
            # Fall back to demo results store
            try:
                from .demo import _pipeline_results
                stored = _pipeline_results.get(run_id)
                if stored:
                    result = stored.get("result", stored)
                    if isinstance(result, dict):
                        result["view_url"] = _view_url(run_id)
                    return result
            except Exception:
                pass
            return {"error": f"No results for run_id: {run_id}"}
        else:
            # List all results
            all_results = []
            # From running pipelines
            for rid, entry in _running_pipelines.items():
                if entry["status"] == "complete" and entry["result"]:
                    all_results.append({
                        "run_id": rid,
                        "app_name": entry.get("app_name", ""),
                        "status": "complete",
                        "total_tests": entry["result"].get("total_tests", 0),
                        "total_workflows": len(entry["result"].get("workflows", [])),
                        "started_at": entry["started_at"],
                        "view_url": _view_url(rid),
                    })
            # From disk-persisted results
            seen = {r["run_id"] for r in all_results}
            for rid, stored in _persisted_results.items():
                if rid not in seen:
                    r = stored.get("result", {})
                    all_results.append({
                        "run_id": rid,
                        "app_name": stored.get("app_name", ""),
                        "status": "complete",
                        "total_tests": r.get("total_tests", 0) if isinstance(r, dict) else 0,
                        "total_workflows": len(r.get("workflows", [])) if isinstance(r, dict) else 0,
                        "started_at": stored.get("started_at", ""),
                        "view_url": _view_url(rid),
                    })
                    seen.add(rid)
            # From demo store
            try:
                from .demo import _pipeline_results
                for rid, stored in _pipeline_results.items():
                    if rid not in seen:
                        all_results.append({
                            "run_id": rid,
                            "app_name": stored.get("app_name", ""),
                            "status": "complete",
                            "total_tests": stored.get("total_tests", 0),
                            "total_workflows": stored.get("total_workflows", 0),
                            "started_at": stored.get("timestamp", ""),
                            "view_url": _view_url(rid),
                        })
            except Exception:
                pass
            return {"results": all_results, "count": len(all_results)}

    if tool == "ta.pipeline.screenshot":
        device_id = args.get("device_id")
        run_id = args.get("run_id")

        if not device_id:
            # Try to find device from the specified run
            if run_id:
                entry = _running_pipelines.get(run_id)
                if entry:
                    device_id = entry.get("device_id")

            # Auto-detect if still not found
            if not device_id and _qa_pipeline_service:
                try:
                    devices_text = await _qa_pipeline_service.mobile_mcp_client.list_available_devices()
                    matches = re.findall(r"(emulator-\d+)", devices_text)
                    if matches:
                        device_id = matches[0]
                except Exception:
                    pass

        if not device_id:
            return {"error": "No device found. Provide device_id or start an emulator."}
        if not _qa_pipeline_service:
            return {"error": "Pipeline service not initialized"}

        try:
            screenshot = await _qa_pipeline_service.mobile_mcp_client.take_screenshot(device_id)
            if isinstance(screenshot, dict) and screenshot.get("error"):
                return {"error": f"Screenshot failed: {screenshot['error']}"}

            content = screenshot.get("content", []) if isinstance(screenshot, dict) else []
            for item in content if isinstance(content, list) else []:
                if isinstance(item, dict) and item.get("type") == "image":
                    return {
                        "run_id": run_id,
                        "device_id": device_id,
                        "type": "image",
                        "mimeType": item.get("mimeType", "image/png"),
                        "data": item.get("data", "")[:100] + "...(truncated for MCP)",
                        "data_length": len(item.get("data", "")),
                        "message": "Screenshot captured. For live video stream, open the view_url.",
                        "view_url": _view_url(run_id) if run_id else f"{_get_base_url()}/demo/curated",
                    }

            return {
                "run_id": run_id,
                "device_id": device_id,
                "message": "Screenshot captured but format unexpected. Check /curated for live view.",
            }
        except Exception as exc:
            return {"error": f"Screenshot failed: {exc}"}

    if tool == "ta.pipeline.rerun_failures":
        if not _qa_pipeline_service:
            return {"error": "Pipeline service not initialized"}
        if _count_running() >= MAX_CONCURRENT_PIPELINES:
            return {"error": f"Max {MAX_CONCURRENT_PIPELINES} concurrent pipelines. Try again later."}

        baseline_run_id = args.get("baseline_run_id")
        if not baseline_run_id:
            return {"error": "baseline_run_id is required — pass the run_id of a completed run"}

        baseline_result = _get_run_result(baseline_run_id)
        if not baseline_result:
            return {"error": f"No results for baseline_run_id: {baseline_run_id}"}

        # Extract failing test cases from the baseline run
        execution = _normalize_execution(baseline_result)
        if not execution.get("results"):
            return {"error": "Baseline run has no execution results — only runs with executed tests can be rerun"}

        exec_results = execution["results"]
        failed_tests = [tr for tr in exec_results if tr.get("status") not in ("pass", "passed")]
        if not failed_tests:
            return {
                "status": "all_passed",
                "message": f"All {len(exec_results)} tests passed in baseline run — nothing to rerun.",
                "baseline_run_id": baseline_run_id,
            }

        # Get app context from the baseline run's persisted entry
        baseline_entry = _running_pipelines.get(baseline_run_id) or _persisted_results.get(baseline_run_id) or {}
        app_url = args.get("app_url") or baseline_entry.get("app_url", "")
        app_name = baseline_entry.get("app_name", "Rerun")
        device_id = args.get("device_id")

        if not app_url:
            return {"error": "app_url not found in baseline run. Pass app_url explicitly."}

        # Build synthetic test suite from failed tests only
        from ..agents.qa_pipeline.schemas import TestCase, TestStep, TestSuiteResult, WorkflowSummary

        rerun_test_cases = []
        workflow_names = set()
        for tr in failed_tests:
            steps = []
            for sr in tr.get("step_results", []):
                steps.append(TestStep(
                    step_number=sr.get("step_number", 0),
                    action=sr.get("action", ""),
                    expected_result=sr.get("expected", ""),
                ))
            rerun_test_cases.append(TestCase(
                test_id=tr.get("test_id", ""),
                name=tr.get("name", ""),
                workflow_id=tr.get("workflow_id", ""),
                workflow_name=tr.get("workflow_name", ""),
                description=f"Rerun of failed test from {baseline_run_id}",
                steps=steps,
                expected_result=tr.get("expected_result", ""),
                priority=tr.get("priority", "medium"),
                category=tr.get("category", "regression"),
            ))
            if tr.get("workflow_name"):
                workflow_names.add(tr["workflow_name"])

        rerun_suite = TestSuiteResult(
            app_name=app_name,
            test_cases=rerun_test_cases,
            total_tests=len(rerun_test_cases),
        )

        # Start a new pipeline run that only does execution (skips crawl + discovery)
        run_id = f"rerun-{uuid.uuid4().hex[:8]}"
        _running_pipelines[run_id] = {
            "run_id": run_id,
            "owner_id": caller_id,
            "status": "running",
            "current_stage": "EXECUTION",
            "events": [],
            "result": None,
            "progress": {},
            "started_at": _now_iso(),
            "error": None,
            "app_url": app_url,
            "app_name": f"{app_name} (rerun)",
            "baseline_run_id": baseline_run_id,
            "flow_type": "rerun",
        }

        async def _rerun_failures_background():
            entry = _running_pipelines[run_id]
            try:
                # Auto-detect device
                _device_id = device_id
                if not _device_id and _qa_pipeline_service:
                    try:
                        devices_text = await _qa_pipeline_service.mobile_mcp_client.list_available_devices()
                        matches = re.findall(r"(emulator-\d+)", devices_text)
                        if matches:
                            _device_id = matches[0]
                    except Exception:
                        pass
                if not _device_id:
                    entry["status"] = "error"
                    entry["error"] = "No emulator device found"
                    return

                # Run execution only
                from ..agents.qa_pipeline.execution_agent import execute_test_suite

                entry["events"].append({
                    "type": "stage_transition",
                    "timestamp": _now_iso(),
                    "data": {"to_stage": "EXECUTION", "type": "stage_transition"},
                })

                async for event in execute_test_suite(
                    rerun_suite,
                    _qa_pipeline_service.mobile_mcp_client,
                    _device_id,
                    app_url=app_url,
                    flow_type="web",
                ):
                    entry["events"].append({
                        "type": event.get("type", "unknown"),
                        "timestamp": _now_iso(),
                        "data": event,
                    })
                    if event.get("type") == "execution_complete":
                        entry["result"] = {
                            "execution": event,
                            "test_cases": [tc.model_dump() for tc in rerun_test_cases],
                            "total_tests": len(rerun_test_cases),
                            "baseline_run_id": baseline_run_id,
                            "rerun": True,
                        }

                entry["status"] = "complete"
                if entry.get("result"):
                    _persist_result(run_id, {
                        "run_id": run_id,
                        "app_name": f"{app_name} (rerun)",
                        "app_url": app_url,
                        "flow_type": "rerun",
                        "baseline_run_id": baseline_run_id,
                        "started_at": entry["started_at"],
                        "completed_at": _now_iso(),
                        "result": entry["result"],
                    })

            except Exception as exc:
                logger.exception("Rerun %s failed", run_id)
                entry["status"] = "error"
                entry["error"] = str(exc)

        _create_pipeline_task(_rerun_failures_background(), run_id)

        return {
            "run_id": run_id,
            "status": "running",
            "baseline_run_id": baseline_run_id,
            "failed_tests": len(failed_tests),
            "total_baseline_tests": len(exec_results),
            "skipped": ["crawl", "workflow_discovery", "test_generation"],
            "view_url": _view_url(run_id),
            "stream_url": f"/api/demo/pipeline-stream/{run_id}",
            "message": f"Rerunning {len(failed_tests)} failed tests from {baseline_run_id}. Crawl/discovery skipped — execution only.",
        }

    raise ValueError(f"Unknown pipeline tool: {tool}")


# ---------------------------------------------------------------------------
# Feedback dispatchers
# ---------------------------------------------------------------------------

async def dispatch_feedback(tool: str, args: Dict[str, Any]) -> Any:
    """Handle ta.feedback.* tools."""
    args.pop("_caller_id", None)

    if tool == "ta.feedback.annotate":
        run_id = args.get("run_id")
        if not run_id:
            return {"error": "run_id is required"}
        target_type = args.get("target_type", "test_case")
        target_id = args.get("target_id")
        if not target_id:
            return {"error": "target_id is required"}
        annotation_type = args.get("annotation_type", "suggestion")
        content = args.get("content", "")
        if not content:
            return {"error": "content is required"}

        annotation = {
            "annotation_id": f"ann-{uuid.uuid4().hex[:8]}",
            "run_id": run_id,
            "target_type": target_type,
            "target_id": target_id,
            "annotation_type": annotation_type,
            "content": content,
            "author": args.get("author", "remote-agent"),
            "created_at": _now_iso(),
        }

        if run_id not in _annotations:
            _annotations[run_id] = _load_annotations(run_id)
        _annotations[run_id].append(annotation)
        _persist_annotations(run_id)

        return {"annotation_id": annotation["annotation_id"], "status": "saved"}

    if tool == "ta.feedback.list":
        run_id = args.get("run_id")
        if not run_id:
            return {"error": "run_id is required"}
        annotations = _load_annotations(run_id)

        # Optional filters
        target_type = args.get("target_type")
        target_id = args.get("target_id")
        if target_type:
            annotations = [a for a in annotations if a["target_type"] == target_type]
        if target_id:
            annotations = [a for a in annotations if a["target_id"] == target_id]

        return {"run_id": run_id, "annotations": annotations, "count": len(annotations)}

    if tool == "ta.feedback.summary":
        run_id = args.get("run_id")
        if not run_id:
            return {"error": "run_id is required"}
        annotations = _load_annotations(run_id)

        by_type = {}
        flagged = []
        for a in annotations:
            at = a["annotation_type"]
            by_type[at] = by_type.get(at, 0) + 1
            if at == "flag":
                flagged.append({"target_id": a["target_id"], "content": a["content"]})

        return {
            "run_id": run_id,
            "total_annotations": len(annotations),
            "by_type": by_type,
            "flagged_items": flagged,
        }

    raise ValueError(f"Unknown feedback tool: {tool}")


# ---------------------------------------------------------------------------
# Device dispatchers
# ---------------------------------------------------------------------------

async def dispatch_device(tool: str, args: Dict[str, Any]) -> Any:
    """Handle ta.device.* tools — thin proxy to device_leasing endpoints."""
    args.pop("_caller_id", None)

    if tool == "ta.device.list":
        try:
            from .device_leasing import list_available_devices
            return await list_available_devices()
        except Exception as exc:
            return {"error": f"Failed to list devices: {exc}"}

    if tool == "ta.device.lease":
        device_id = args.get("device_id")
        if not device_id:
            return {"error": "device_id is required"}
        try:
            from .device_leasing import lease_device, LeaseRequest
            req = LeaseRequest(device_id=device_id, duration_minutes=args.get("duration_minutes", 30))
            return await lease_device(req)
        except Exception as exc:
            return {"error": f"Failed to lease device: {exc}"}

    raise ValueError(f"Unknown device tool: {tool}")


# ---------------------------------------------------------------------------
# Meta dispatchers
# ---------------------------------------------------------------------------

async def dispatch_meta(tool: str, args: Dict[str, Any]) -> Any:
    """Handle ta.meta.* tools."""
    args.pop("_caller_id", None)

    if tool == "ta.meta.connection_info":
        return {
            "server_url": os.environ.get("TA_BACKEND_URL", "http://localhost:8000"),
            "server_version": "1.0.0",
            "pipeline_service_ready": _qa_pipeline_service is not None,
            "running_pipelines": _count_running(),
            "max_concurrent": MAX_CONCURRENT_PIPELINES,
        }

    raise ValueError(f"Unknown meta tool: {tool}")


# ---------------------------------------------------------------------------
# QA Verification dispatchers (end-to-end flows, evidence, verdicts)
# ---------------------------------------------------------------------------

def _get_run_result(run_id: str) -> dict:
    """Retrieve a completed pipeline result by run_id, checking all stores."""
    # 1. In-memory running pipelines
    entry = _running_pipelines.get(run_id)
    if entry and entry.get("result"):
        return entry["result"]
    # 2. Disk-persisted results
    persisted = _persisted_results.get(run_id)
    if persisted:
        return persisted.get("result", persisted)
    # 3. Fall back to demo results store
    try:
        from .demo import _pipeline_results
        stored = _pipeline_results.get(run_id)
        if stored:
            return stored.get("result", stored)
    except Exception:
        pass
    return {}


def _normalize_execution(result: dict) -> dict:
    """Normalize execution data to a consistent dict format.

    Playwright stores execution as a list of test results.
    Emulator stores it as a dict with {results: [...], passed: N, ...}.
    This function always returns a dict with 'results', 'passed', 'failed',
    'total', and 'pass_rate' keys.
    """
    raw = result.get("execution")
    if raw is None:
        return {"results": [], "passed": 0, "failed": 0, "total": 0, "pass_rate": 0.0}

    if isinstance(raw, list):
        results = raw
        passed = sum(1 for r in results if r.get("status") == "passed")
        total = len(results)
        failed = total - passed
        return {
            "results": results,
            "passed": passed,
            "failed": failed,
            "total": total,
            "pass_rate": passed / total if total else 0.0,
        }

    if isinstance(raw, dict):
        return raw

    return {"results": [], "passed": 0, "failed": 0, "total": 0, "pass_rate": 0.0}


def _extract_test_cases(result: dict) -> list:
    """Extract test_cases list from a pipeline result dict."""
    if "test_cases" in result:
        return result["test_cases"]
    # Nested under workflows
    cases = []
    for wf in result.get("workflows", []):
        cases.extend(wf.get("test_cases", []))
    return cases


# ---------------------------------------------------------------------------
# Contextual Graph enrichment for failure bundles
# ---------------------------------------------------------------------------

def _enrich_bundle_with_graph(bundle: dict, graph: "Any") -> None:
    """Append prior-run history and smart attribution from the contextual graph.

    Mutates *bundle* in-place.  All graph lookups are wrapped in try/except so
    the failure bundle degrades gracefully when the graph is sparse or empty.
    """
    from ..services.context_graph import (
        ContextGraph, NodeKind, EdgeType,
        TaskNode, VerdictNode, OutcomeNode, PrecedentNode,
    )
    assert isinstance(graph, ContextGraph)

    # Determine the app_url for this run from the bundle metadata
    run_id = bundle.get("run_id", "")
    app_url = ""
    entry = _running_pipelines.get(run_id, {})
    persisted = _persisted_results.get(run_id, {})
    app_url = entry.get("app_url", "") or persisted.get("app_url", "")

    precedent_count = 0

    # ------------------------------------------------------------------
    # 1. Prior Runs section — find task nodes for the same app_url
    # ------------------------------------------------------------------
    prior_runs: List[dict] = []
    try:
        task_nodes = graph.nodes_by_kind(NodeKind.TASK)
        seen_run_ids: set = set()
        for tn in task_nodes:
            if not isinstance(tn, TaskNode):
                continue
            node_url = tn.metadata.get("app_url", "")
            # Match on same app URL or same app name in intent
            if app_url and node_url and node_url == app_url and tn.run_id != run_id:
                if tn.run_id in seen_run_ids:
                    continue
                seen_run_ids.add(tn.run_id)

                # Find linked verdict nodes to extract pass rates
                lineage = graph.get_task_lineage(tn.id)
                verdicts = [n for n in lineage if isinstance(n, VerdictNode)]
                outcomes = [n for n in lineage if isinstance(n, OutcomeNode)]

                total_outcomes = len(outcomes)
                success_outcomes = sum(1 for o in outcomes if o.status == "success")
                prior_pass_rate = round(success_outcomes / total_outcomes, 4) if total_outcomes > 0 else None

                verdict_summary = ""
                if verdicts:
                    types = [v.verdict_type for v in verdicts]
                    from collections import Counter
                    counts = Counter(types)
                    verdict_summary = ", ".join(f"{k}: {v}" for k, v in counts.most_common())

                prior_entry: dict = {
                    "run_id": tn.run_id,
                    "date": tn.created_at,
                    "intent": tn.intent[:120],
                }
                if prior_pass_rate is not None:
                    prior_entry["pass_rate"] = prior_pass_rate
                if verdict_summary:
                    prior_entry["verdicts"] = verdict_summary

                prior_runs.append(prior_entry)
                if len(prior_runs) >= 5:
                    break
    except Exception:
        pass

    if prior_runs:
        bundle["prior_runs"] = prior_runs

    # ------------------------------------------------------------------
    # 2. Smart attribution — for each failure, check graph precedents
    # ------------------------------------------------------------------
    try:
        failures = bundle.get("failures", [])
        verdict_nodes = graph.nodes_by_kind(NodeKind.VERDICT)
        all_edges = graph._edges  # direct access for FAILURE_FIXED_BY scan

        for failure in failures:
            try:
                test_name = (failure.get("name", "") or "").lower()
                test_id = failure.get("test_id", "")
                if not test_name and not test_id:
                    continue

                # Find verdict nodes with similar failure pattern
                matching_verdict = None
                for vn in verdict_nodes:
                    if not isinstance(vn, VerdictNode):
                        continue
                    # Match by test_id in metadata or by reasoning overlap
                    v_test_id = vn.metadata.get("test_id", "")
                    if test_id and v_test_id == test_id and vn.run_id != run_id:
                        matching_verdict = vn
                        break
                    # Fall back to keyword matching on reasoning
                    if test_name and vn.reasoning:
                        v_tokens = set(vn.reasoning.lower().split())
                        t_tokens = set(test_name.split())
                        if t_tokens and v_tokens:
                            overlap = len(t_tokens & v_tokens) / max(len(t_tokens | v_tokens), 1)
                            if overlap >= 0.4 and vn.run_id != run_id:
                                matching_verdict = vn
                                break

                if matching_verdict:
                    precedent_count += 1
                    attr: dict = {
                        "seen_before": True,
                        "prior_run_id": matching_verdict.run_id,
                        "verdict_type": matching_verdict.verdict_type,
                    }
                    if matching_verdict.reasoning:
                        attr["likely_cause"] = matching_verdict.reasoning[:200]

                    # Check for FAILURE_FIXED_BY edges from the matching verdict's outcome
                    try:
                        outcome_edges = graph.get_edges(matching_verdict.id, direction="in", edge_type=EdgeType.OUTCOME_JUDGED_AS)
                        for oe in outcome_edges:
                            fix_edges = graph.get_edges(oe.from_id, direction="out", edge_type=EdgeType.FAILURE_FIXED_BY)
                            for fe in fix_edges:
                                fix_target = graph.get_node(fe.to_id)
                                if isinstance(fix_target, OutcomeNode) and fix_target.status == "success":
                                    fix_desc = fe.metadata.get("fix_description", "")
                                    if fix_desc:
                                        attr["fix_hint"] = fix_desc
                                    else:
                                        attr["fix_hint"] = f"Fixed in run {fix_target.run_id}"
                                    break
                            if "fix_hint" in attr:
                                break
                    except Exception:
                        pass

                    failure["graph_attribution"] = attr
                else:
                    failure["graph_attribution"] = {"seen_before": False}
            except Exception:
                continue
    except Exception:
        pass

    # ------------------------------------------------------------------
    # 3. Graph confidence line
    # ------------------------------------------------------------------
    total_failures = len(bundle.get("failures", []))
    if total_failures > 0:
        coverage = round(precedent_count / total_failures, 4)
        if coverage >= 0.7:
            confidence = "high"
        elif coverage >= 0.3:
            confidence = "medium"
        else:
            confidence = "low"
        bundle["graph_insight"] = {
            "precedents_found": precedent_count,
            "total_failures": total_failures,
            "coverage": coverage,
            "confidence": confidence,
            "note": (
                f"{precedent_count}/{total_failures} failures have prior precedents in the graph. "
                f"Confidence: {confidence}."
            ),
        }


# ---------------------------------------------------------------------------
# Compact Failure Bundle — token-efficient format for Claude Code consumption
# ---------------------------------------------------------------------------

_RUN_LOGS_DIR = Path(__file__).resolve().parents[2] / "data" / "run_logs"
_RUN_LOGS_DIR.mkdir(parents=True, exist_ok=True)

_HANDOFF_DIR = Path(__file__).resolve().parents[2] / "data" / "handoff"
_HANDOFF_DIR.mkdir(parents=True, exist_ok=True)


def format_compact_bundle(run_id: str) -> dict:
    """Transform a completed pipeline result into a compact failure bundle.

    Designed for Claude Code consumption: ~500-1500 tokens vs 5000+ raw.
    Only includes failures with suggested fixes and a rerun command.
    """
    result = _get_run_result(run_id)
    if not result:
        return {"error": f"No results for run_id: {run_id}"}

    # Pull metadata from the persisted entry or running pipeline
    entry = _running_pipelines.get(run_id, {})
    persisted = _persisted_results.get(run_id, {})
    meta = persisted or entry

    app_name = meta.get("app_name", result.get("app_name", "Unknown"))
    flow_type = meta.get("flow_type", "unknown")
    started_at = meta.get("started_at", "")
    completed_at = meta.get("completed_at", "")

    # Compute duration
    duration_s = 0.0
    if started_at and completed_at:
        try:
            from datetime import datetime, timezone
            t0 = datetime.fromisoformat(started_at)
            t1 = datetime.fromisoformat(completed_at)
            duration_s = round((t1 - t0).total_seconds(), 1)
        except Exception:
            pass

    # Extract execution summary
    execution = _normalize_execution(result)
    total = execution.get("total", result.get("total_tests", 0))
    passed = execution.get("passed", 0)
    failed = execution.get("failed", 0)
    pass_rate = execution.get("pass_rate", (passed / total if total > 0 else 0.0))

    # Build failures list (only failed tests, capped at 15)
    failures = []
    exec_results = execution.get("results", [])
    test_cases = _extract_test_cases(result)
    tc_lookup = {tc.get("test_id", ""): tc for tc in test_cases}

    for tr in exec_results:
        if tr.get("status") in ("pass", "passed"):
            continue
        tc = tc_lookup.get(tr.get("test_id", ""), {})

        # Find first failing step
        failing_step = None
        for sr in tr.get("step_results", []):
            if sr.get("status") != "pass":
                failing_step = {
                    "step_number": sr.get("step_number"),
                    "action": sr.get("action", ""),
                    "expected": sr.get("expected", ""),
                    "actual": sr.get("actual_result", sr.get("actual", "")),
                }
                break

        failure_entry = {
            "test_id": tr.get("test_id", ""),
            "name": tr.get("name", tc.get("name", "")),
            "priority": tr.get("priority", tc.get("priority", "")),
            "category": tc.get("category", ""),
        }
        if failing_step:
            failure_entry["failing_step"] = failing_step
        if tr.get("error"):
            failure_entry["error"] = tr["error"]

        # Category-based fix hint
        category = tc.get("category", "").lower()
        hint = _category_fix_hint(category, tc.get("workflow_name", ""))
        if hint:
            failure_entry["suggested_fix"] = hint

        failures.append(failure_entry)
        if len(failures) >= 15:
            break

    bundle = {
        "run_id": run_id,
        "app": app_name,
        "flow_type": flow_type,
        "timestamp": completed_at or started_at,
        "duration_s": duration_s,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": round(pass_rate, 4),
        },
        "failures": failures,
        "evidence_url": f"/api/demo/pipeline-results/{run_id}",
        "view_url": _view_url(run_id),
    }
    # Include token cost metrics if available
    token_metrics = meta.get("token_metrics")
    if token_metrics and token_metrics.get("total_tokens", 0) > 0:
        bundle["token_metrics"] = token_metrics
    if failures:
        bundle["rerun_command"] = f'ta.pipeline.rerun_failures(baseline_run_id="{run_id}", failures_only=true)'
    return bundle


def _category_fix_hint(category: str, workflow_name: str = "") -> Optional[dict]:
    """Return generic fix hints based on failure category. NOT hardcoded to retention.sh paths."""
    hints = {
        "smoke": {"hint": "Core app launch or home screen issue — check main entry point and initial render"},
        "regression": {"hint": "Previously working flow broke — check recent changes to this feature"},
        "edge_case": {"hint": "Boundary condition failure — check input validation and error handling"},
        "negative": {"hint": "Error handling issue — check how the app handles invalid input or missing data"},
        "accessibility": {"hint": "Accessibility violation — check ARIA labels, contrast ratios, focus management"},
        "navigation": {"hint": "Navigation flow broken — check routing, back button, and screen transitions"},
    }
    result = hints.get(category)
    if result and workflow_name:
        result = dict(result)
        result["workflow"] = workflow_name
    return result


def _persist_run_log(run_id: str, compact_bundle: dict) -> None:
    """Save a run log to disk for Claude Code to read without re-querying API."""
    try:
        path = _RUN_LOGS_DIR / f"{run_id}.json"
        log_entry = {
            "run_id": run_id,
            "timestamp": compact_bundle.get("timestamp", _now_iso()),
            "app_name": compact_bundle.get("app", ""),
            "flow_type": compact_bundle.get("flow_type", ""),
            "duration_s": compact_bundle.get("duration_s", 0),
            "compact_bundle": compact_bundle,
            "rerun_command": compact_bundle.get("rerun_command", ""),
        }
        with open(path, "w") as f:
            json.dump(log_entry, f, indent=2, default=str)
        logger.info(f"Persisted run log: {path}")
    except Exception as e:
        logger.warning(f"Failed to persist run log {run_id}: {e}")


# ---------------------------------------------------------------------------
# Handoff markdown — structured report for Claude Code
# ---------------------------------------------------------------------------

async def build_handoff_md(run_id: str) -> Optional[str]:
    """Build a markdown QA handoff report from a completed pipeline run.

    Returns the markdown string and persists to disk. Returns None if
    no result found for run_id.
    """
    result_data = _get_run_result(run_id)
    if not result_data:
        return None

    entry = _running_pipelines.get(run_id) or _persisted_results.get(run_id) or {}
    app_name = entry.get("app_name", "Unknown App")
    app_url = entry.get("app_url", "")
    duration_s = entry.get("duration_s", 0)
    started_at = entry.get("started_at", "")

    # Extract test case data
    test_cases = _extract_test_cases(result_data)
    total_tests = len(test_cases)

    # Check for execution results (Stage 4)
    # Playwright pipeline stores execution as a list of test results directly;
    # emulator pipeline stores it as a dict with {results: [...], passed: N, ...}
    raw_exec = result_data.get("execution", {})
    if isinstance(raw_exec, list):
        # Playwright format: list of {test_id, name, status, error}
        exec_results = raw_exec
        passed = sum(1 for r in exec_results if r.get("status") == "passed")
        failed = sum(1 for r in exec_results if r.get("status") != "passed")
        total_tests = len(exec_results)
        pass_rate = passed / total_tests if total_tests else 0
        verdict = "PASS" if pass_rate >= 0.8 else "FAIL"
        has_execution = True
    elif isinstance(raw_exec, dict) and raw_exec.get("results"):
        # Emulator format: dict with results list + summary fields
        passed = raw_exec.get("passed", 0)
        failed = raw_exec.get("failed", 0)
        total_tests = raw_exec.get("total", total_tests)
        pass_rate = raw_exec.get("pass_rate", 0)
        verdict = "PASS" if pass_rate >= 0.8 else "FAIL"
        exec_results = raw_exec.get("results", [])
        has_execution = True
    else:
        # No execution — report test case generation only
        passed = 0
        failed = 0
        pass_rate = 0
        verdict = "PENDING (no execution stage)"
        exec_results = []
        has_execution = False

    # Build markdown
    lines = [
        f"# retention.sh QA Report — {run_id}",
        "",
        f"**App:** {app_name}  ",
        f"**URL:** {app_url}  " if app_url else "",
        f"**Verdict:** {verdict}  ",
        f"**Pass Rate:** {pass_rate:.0%} ({passed}/{total_tests})  ",
        f"**Duration:** {duration_s:.1f}s  ",
        f"**Started:** {started_at}  ",
        "",
    ]

    # Failures table
    failures = [r for r in exec_results if r.get("status") in ("fail", "failed", "error", "blocked")]
    if failures:
        lines.append("## Failures")
        lines.append("")
        lines.append("| # | Test | Status | Failing Step | Expected | Actual |")
        lines.append("|---|------|--------|-------------|----------|--------|")
        for i, f in enumerate(failures, 1):
            test_name = f.get("name", f.get("test_id", "?"))
            status = f.get("status", "fail")
            # Find first failing step — handle both emulator and Playwright formats
            step_info = ""
            expected = ""
            actual = ""
            if f.get("step_results"):
                # Emulator format: has step_results list
                for sr in f.get("step_results", []):
                    if sr.get("status") in ("fail", "error"):
                        step_info = f"Step {sr.get('step_number', '?')}: {sr.get('action', '?')}"
                        expected = sr.get("expected", "")
                        actual = sr.get("actual_result", "")
                        break
            elif f.get("error"):
                # Playwright format: has error string directly
                step_info = f.get("name", "")
                actual = f["error"][:100]
            lines.append(f"| {i} | {test_name} | {status} | {step_info} | {expected} | {actual} |")
        lines.append("")
    elif not has_execution:
        lines.append("## Test Cases Generated (not yet executed)")
        lines.append("")
        for i, tc in enumerate(test_cases[:15], 1):
            priority = tc.get("priority", "P2")
            name = tc.get("name", tc.get("test_id", "?"))
            lines.append(f"  {i}. [{priority}] {name}")
        lines.append("")
    else:
        lines.append("## All tests passed!")
        lines.append("")

    # Suggested files to investigate (heuristic)
    if failures:
        lines.append("## Files to Investigate")
        lines.append("")
        categories = set()
        for f in failures:
            for sr in f.get("step_results", []):
                action = sr.get("action", "").lower()
                if any(k in action for k in ("search", "filter")):
                    categories.add("search")
                if any(k in action for k in ("cart", "add", "remove")):
                    categories.add("cart")
                if any(k in action for k in ("login", "sign", "auth")):
                    categories.add("auth")
                if any(k in action for k in ("checkout", "pay", "card")):
                    categories.add("checkout")
                if any(k in action for k in ("navigate", "page", "tab")):
                    categories.add("navigation")
        if not categories:
            categories.add("general")
        for cat in sorted(categories):
            lines.append(f"- Look at components related to **{cat}** functionality")
        lines.append("")

    # Rerun instructions
    lines.append("## Next Steps")
    lines.append("")
    lines.append(f"1. Fix the bugs listed above in your codebase")
    lines.append(f"2. Re-verify: call `ta.rerun` with run_id `{run_id}`")
    lines.append(f"3. Compare: call `ta.compare_before_after` with baseline `{run_id}` and the new run_id")
    lines.append(f"4. Final verdict: call `ta.emit_verdict` on the new run_id")
    lines.append("")

    report = "\n".join(lines)

    # Persist to disk
    try:
        handoff_path = _HANDOFF_DIR / f"{run_id}.md"
        handoff_path.write_text(report, encoding="utf-8")
        logger.info(f"Persisted handoff report: {handoff_path}")
    except Exception as e:
        logger.warning(f"Failed to persist handoff {run_id}: {e}")

    return report


async def dispatch_qa_verification(tool: str, args: Dict[str, Any]) -> Any:
    """Handle ta.run_web_flow, ta.run_android_flow, ta.collect_trace_bundle,
    ta.summarize_failure, ta.compare_before_after, ta.emit_verdict,
    ta.suggest_fix_context tools."""
    caller_id = args.pop("_caller_id", "anonymous")

    # ── ta.run_web_flow ─────────────────────────────────────────────────
    if tool == "ta.run_web_flow":
        url = args.get("url")
        if not url:
            return {"error": "url is required"}

        try:
            url = _validate_app_url(url)
        except ValueError as e:
            return {"error": str(e)}

        app_name = args.get("app_name", "Web App")
        model_override = args.get("model", "")  # Optional model override for benchmarking
        requested_mode = args.get("mode", "")  # "playwright" to skip emulator

        # ── Check if emulator is available ─────────────────────────────
        has_emulator = False
        if _qa_pipeline_service and requested_mode != "playwright":
            try:
                devices_text = await _qa_pipeline_service.mobile_mcp_client.list_available_devices()
                has_emulator = bool(re.findall(r"(emulator-\d+)", devices_text))
            except Exception:
                pass

        # Emulator execution is slower — default 1200s (20 min); Playwright is fast — 600s
        _default_timeout = 1200 if has_emulator else 600
        timeout_seconds = int(args.get("timeout_seconds", _default_timeout))

        # ── Playwright-direct mode: no emulator needed for web apps ───
        if requested_mode == "playwright" or (not has_emulator and requested_mode != "emulator"):
            # Try Playwright-based web QA pipeline
            if _count_running() >= MAX_CONCURRENT_PIPELINES:
                return {"error": f"Max {MAX_CONCURRENT_PIPELINES} concurrent pipelines. Try again later.", "queued": True}

            run_id = f"pw-{uuid.uuid4().hex[:8]}"
            _running_pipelines[run_id] = {
                "run_id": run_id,
                "owner_id": caller_id,
                "status": "running",
                "current_stage": "CRAWL",
                "events": [],
                "result": None,
                "progress": {},
                "started_at": _now_iso(),
                "error": None,
                "app_url": url,
                "original_app_url": url,
                "app_name": app_name,
                "flow_type": "playwright",
                "timeout_seconds": timeout_seconds,
                "model": model_override or "gpt-5.4-mini",
            }

            # Use Playwright pipeline — crawls via headless browser, no emulator
            _create_pipeline_task(_run_playwright_pipeline(
                run_id, url=url, app_name=app_name,
                timeout_seconds=timeout_seconds, model_override=model_override,
            ), run_id)
            return {
                "run_id": run_id,
                "status": "running",
                "flow_type": "playwright",
                "engine": "playwright",
                "view_url": _view_url(run_id),
                "message": f"Web QA started for {app_name} (Playwright mode — no emulator needed). Poll ta.pipeline.status for progress.",
            }

        if has_emulator:
            # ── Full pipeline: emulator-based crawl → workflow → test → execute
            if _count_running() >= MAX_CONCURRENT_PIPELINES:
                return {"error": f"Max {MAX_CONCURRENT_PIPELINES} concurrent pipelines. Try again later.", "queued": True}

            emulator_url = _emulator_url(url)

            run_id = f"web-{uuid.uuid4().hex[:8]}"
            _running_pipelines[run_id] = {
                "run_id": run_id,
                "owner_id": caller_id,
                "status": "running",
                "current_stage": "CRAWL",
                "events": [],
                "result": None,
                "progress": {},
                "started_at": _now_iso(),
                "error": None,
                "app_url": emulator_url,
                "original_app_url": url,  # Preserve user's original URL for results
                "app_name": app_name,
                "flow_type": "web",
                "timeout_seconds": timeout_seconds,
                "model": model_override or "gpt-5.4-mini",
            }
            _create_pipeline_task(_run_pipeline_background(
                run_id,
                app_url=emulator_url,
                app_name=app_name,
                timeout_seconds=timeout_seconds,
                model_override=model_override,
            ), run_id)
            return {
                "run_id": run_id,
                "status": "running",
                "flow_type": "web",
                "engine": "emulator",
                "view_url": _view_url(run_id),
                "stream_url": f"/api/demo/pipeline-stream/{run_id}",
                "message": f"Web QA flow started for {app_name} (emulator mode). Poll ta.pipeline.status for progress.",
            }
        else:
            # ── No emulator: return setup guidance ──────────────────────
            # The TA agent orchestrator will guide the user's Claude Code
            # to set up the emulator via the outbound WSS relay.
            logger.info(f"No emulator — returning setup guidance for {url}")
            return {
                "status": "setup_required",
                "error": None,
                "requires": "android_emulator",
                "message": (
                    "No Android emulator detected. retention.sh needs an emulator to run "
                    "QA flows (even for web apps — we test in a real mobile Chrome browser). "
                    "Use ta.agent.run to get guided setup, or follow the steps below."
                ),
                "guided_setup": {
                    "tool": "ta.agent.run",
                    "message": "Help me set up an Android emulator so I can run QA on my app",
                    "description": (
                        "The TA Coordinator agent will walk your Claude Code through "
                        "emulator setup step-by-step via the outbound WSS relay."
                    ),
                },
                "manual_steps": [
                    {
                        "step": 1,
                        "title": "Install Android SDK command-line tools",
                        "command": "brew install --cask android-commandlinetools",
                        "alt": "Download from https://developer.android.com/studio#command-tools",
                    },
                    {
                        "step": 2,
                        "title": "Accept SDK licenses",
                        "command": "yes | sdkmanager --licenses",
                    },
                    {
                        "step": 3,
                        "title": "Install system image and emulator",
                        "command": (
                            'sdkmanager "platform-tools" "emulator" '
                            '"system-images;android-34;google_apis;arm64-v8a"'
                        ),
                    },
                    {
                        "step": 4,
                        "title": "Create AVD (Android Virtual Device)",
                        "command": (
                            'avdmanager create avd -n ta_device -k '
                            '"system-images;android-34;google_apis;arm64-v8a" --force'
                        ),
                    },
                    {
                        "step": 5,
                        "title": "Start the emulator",
                        "command": "emulator -avd ta_device -no-window -no-audio &",
                        "note": "Use -no-window for headless CI. Remove for local dev with GUI.",
                    },
                    {
                        "step": 6,
                        "title": "Verify device is visible",
                        "command": "adb devices",
                        "expected": "emulator-5554  device",
                    },
                    {
                        "step": 7,
                        "title": "Retry QA flow",
                        "command": f'ta.run_web_flow(url="{url}", app_name="{app_name}")',
                    },
                ],
                "app_url": url,
                "app_name": app_name,
            }

    # ── ta.run_android_flow ─────────────────────────────────────────────
    if tool == "ta.run_android_flow":
        if not _qa_pipeline_service:
            app_package = args.get("app_package", "")
            return {
                "status": "setup_required",
                "error": None,
                "requires": "android_emulator",
                "message": (
                    "No Android emulator detected. Use ta.agent.run for guided setup, "
                    "or follow the manual steps in the setup_required response from ta.run_web_flow."
                ),
                "guided_setup": {
                    "tool": "ta.agent.run",
                    "message": f"Help me set up an Android emulator so I can test {app_package}",
                },
            }
        if _count_running() >= MAX_CONCURRENT_PIPELINES:
            return {"error": f"Max {MAX_CONCURRENT_PIPELINES} concurrent pipelines. Try again later.", "queued": True}

        app_package = args.get("app_package")
        if not app_package:
            return {"error": "app_package is required"}

        app_name = args.get("app_name", app_package)
        device_id = args.get("device_id")
        timeout_seconds = int(args.get("timeout_seconds", 1200))  # Emulator runs need 20 min

        # Auto-detect device if not specified
        if not device_id and _qa_pipeline_service:
            try:
                import re as _re
                devices_text = await _qa_pipeline_service.mobile_mcp_client.list_available_devices()
                matches = _re.findall(r"(emulator-\d+)", devices_text)
                if matches:
                    device_id = matches[0]
            except Exception:
                pass

        if not device_id:
            return {
                "error": "No emulator device found",
                "setup_required": True,
                "next_steps": [
                    "1. Call ta.setup.status to check what's installed and get fix commands",
                    "2. Run the fix commands to install missing components",
                    "3. Call ta.setup.launch_emulator to start an AVD",
                    "4. Wait ~30s for boot, then call ta.system_check to verify",
                    "5. Retry ta.run_android_flow",
                ],
                "quick_fix": "If AVDs exist: call ta.setup.launch_emulator. If not: run ./scripts/setup-macos.sh",
            }

        run_id = f"android-{uuid.uuid4().hex[:8]}"
        _running_pipelines[run_id] = {
            "run_id": run_id,
            "owner_id": caller_id,
            "status": "running",
            "current_stage": "CRAWL",
            "events": [],
            "result": None,
            "progress": {},
            "started_at": _now_iso(),
            "error": None,
            "app_package": app_package,
            "app_name": app_name,
            "device_id": device_id,
            "flow_type": "android",
            "timeout_seconds": timeout_seconds,
        }

        _create_pipeline_task(_run_pipeline_background(
            run_id,
            app_package=app_package,
            app_name=app_name,
            device_id=device_id,
            timeout_seconds=timeout_seconds,
        ), run_id)
        return {
            "run_id": run_id,
            "status": "running",
            "flow_type": "android",
            "device_id": device_id,
            "view_url": _view_url(run_id),
            "stream_url": f"/api/demo/pipeline-stream/{run_id}",
            "message": f"Android QA flow started for {app_name}. Open view_url in a browser to watch live. Poll ta.pipeline.status for progress.",
        }

    # ── ta.rerun ────────────────────────────────────────────────────────
    if tool == "ta.rerun":
        baseline_run_id = args.get("run_id")
        if not baseline_run_id:
            return {"error": "run_id is required (the prior run to rerun)"}
        access_err = _check_run_access(baseline_run_id, caller_id)
        if access_err:
            return {"error": access_err}
        if not _qa_pipeline_service:
            return {"error": "Pipeline service not initialized (no emulator connected)"}
        if _count_running() >= MAX_CONCURRENT_PIPELINES:
            return {"error": f"Max {MAX_CONCURRENT_PIPELINES} concurrent pipelines. Try again later."}

        baseline_result = _get_run_result(baseline_run_id)
        if not baseline_result:
            return {"error": f"No results found for run_id: {baseline_run_id}"}

        test_cases = _extract_test_cases(baseline_result)
        if not test_cases:
            return {"error": f"No test cases found in run {baseline_run_id}"}

        # Filter: failures_only (default True) reruns only failed tests
        failures_only = args.get("failures_only", True)
        if failures_only:
            raw_exec = baseline_result.get("execution", {})
            exec_results_list = []
            if isinstance(raw_exec, list):
                # Playwright format: list of {test_id, name, status, error}
                exec_results_list = raw_exec
            elif isinstance(raw_exec, dict) and raw_exec.get("results"):
                # Emulator format: dict with results list
                exec_results_list = raw_exec["results"]
            if exec_results_list:
                failed_ids = {
                    r.get("test_id") for r in exec_results_list
                    if r.get("status") in ("fail", "failed", "error", "blocked")
                }
                if failed_ids:
                    test_cases = [tc for tc in test_cases if tc.get("test_id") in failed_ids]
            if not test_cases:
                test_cases = _extract_test_cases(baseline_result)

        baseline_entry = _running_pipelines.get(baseline_run_id) or _persisted_results.get(baseline_run_id) or {}
        app_url = args.get("url") or baseline_entry.get("app_url", "")
        app_name = baseline_entry.get("app_name", "Rerun")
        flow_type = baseline_entry.get("flow_type", "web")
        device_id = args.get("device_id")
        # Rerun is execution-only but emulator runs still need headroom (20 min default)
        timeout_seconds = int(args.get("timeout_seconds", 1200))

        if not device_id and _qa_pipeline_service:
            try:
                import re as _re
                devices_text = await _qa_pipeline_service.mobile_mcp_client.list_available_devices()
                matches = _re.findall(r"(emulator-\d+)", devices_text)
                if matches:
                    device_id = matches[0]
            except Exception:
                pass

        run_id = f"rerun-{uuid.uuid4().hex[:8]}"
        _running_pipelines[run_id] = {
            "run_id": run_id,
            "owner_id": caller_id,
            "status": "running",
            "current_stage": "EXECUTION",
            "events": [],
            "result": None,
            "progress": {},
            "started_at": _now_iso(),
            "error": None,
            "app_url": app_url,
            "app_name": app_name,
            "flow_type": flow_type,
            "timeout_seconds": timeout_seconds,
            "baseline_run_id": baseline_run_id,
        }

        async def _rerun_background():
            entry = _running_pipelines[run_id]
            try:
                from ..agents.qa_pipeline.schemas import (
                    TestCase as TCModel, TestStep as TSModel,
                    TestSuiteResult, WorkflowSummary,
                )

                tc_models = []
                for tc in test_cases:
                    steps = [
                        TSModel(
                            step_number=s.get("step_number", i + 1),
                            action=s.get("action", ""),
                            expected_result=s.get("expected_result", s.get("expected", "")),
                        )
                        for i, s in enumerate(tc.get("steps", []))
                    ]
                    tc_models.append(TCModel(
                        test_id=tc.get("test_id", f"tc_{len(tc_models) + 1:03d}"),
                        name=tc.get("name", ""),
                        workflow_id=tc.get("workflow_id", ""),
                        workflow_name=tc.get("workflow_name", ""),
                        description=tc.get("description", ""),
                        steps=steps,
                        expected_result=tc.get("expected_result", ""),
                        priority=tc.get("priority", "P1"),
                        category=tc.get("category", "regression"),
                        pressure_point=tc.get("pressure_point"),
                    ))

                wf_summaries = baseline_result.get("workflows", [])
                if wf_summaries and isinstance(wf_summaries[0], dict):
                    wf_summaries = [
                        WorkflowSummary(
                            workflow_id=w.get("workflow_id", ""),
                            name=w.get("name", ""),
                            test_count=w.get("test_count", 0),
                        )
                        for w in wf_summaries
                    ]

                test_suite = TestSuiteResult(
                    app_name=app_name,
                    test_cases=tc_models,
                    workflows=wf_summaries if wf_summaries else [],
                    total_tests=len(tc_models),
                    by_workflow={},
                    by_priority={},
                    by_category={},
                )

                entry["events"].append({
                    "type": "stage_transition",
                    "timestamp": _now_iso(),
                    "data": {"to_stage": "EXECUTION", "rerun": True, "baseline_run_id": baseline_run_id},
                })

                from ..agents.qa_pipeline.relay_execution import execute_via_relay
                try:
                    from .demo import _get_relay_session
                    relay_session = _get_relay_session()
                except Exception:
                    relay_session = None

                if relay_session:
                    async for ev in execute_via_relay(test_suite, relay_session, app_url=app_url, flow_type=flow_type):
                        entry["events"].append({"type": ev.get("type", "unknown"), "timestamp": _now_iso(), "data": ev})
                        if ev.get("type") == "execution_complete":
                            entry["result"] = {
                                "app_name": app_name,
                                "test_cases": [tc.model_dump() if hasattr(tc, "model_dump") else tc for tc in tc_models],
                                "workflows": baseline_result.get("workflows", []),
                                "total_tests": len(tc_models),
                                "rerun": True,
                                "baseline_run_id": baseline_run_id,
                                "execution": ev,
                            }
                elif device_id and _qa_pipeline_service:
                    from ..agents.qa_pipeline.execution_agent import execute_test_suite
                    async for ev in execute_test_suite(
                        test_suite, _qa_pipeline_service.mobile_mcp_client, device_id
                    ):
                        entry["events"].append({"type": ev.get("type", "unknown"), "timestamp": _now_iso(), "data": ev})
                        if ev.get("type") == "execution_complete":
                            entry["result"] = {
                                "app_name": app_name,
                                "test_cases": [tc.model_dump() if hasattr(tc, "model_dump") else tc for tc in tc_models],
                                "workflows": baseline_result.get("workflows", []),
                                "total_tests": len(tc_models),
                                "rerun": True,
                                "baseline_run_id": baseline_run_id,
                                "execution": ev,
                            }
                elif flow_type == "playwright" and app_url:
                    # Playwright rerun — execute directly in browser, no emulator needed
                    from playwright.async_api import async_playwright
                    pw_results = []
                    async with async_playwright() as p:
                        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--single-process"])
                        page = await browser.new_page()
                        for tc in test_cases:
                            tc_passed = True
                            error_msg = None
                            try:
                                await page.goto(app_url, wait_until="domcontentloaded", timeout=15000)
                                for step in tc.get("steps", []):
                                    if step.get("action") == "click":
                                        target = step.get("target", "")
                                        try:
                                            el = await page.query_selector(f"text={target}")
                                            if el:
                                                await el.click(timeout=5000)
                                                await page.wait_for_timeout(300)
                                        except Exception as click_err:
                                            tc_passed = False
                                            error_msg = str(click_err)[:150]
                                    elif step.get("action") == "fill":
                                        target = step.get("target", "")
                                        safe = target.replace("'", "\\'").replace("\n", " ")[:40]
                                        inp = None
                                        try:
                                            inp = await page.query_selector(f"input[placeholder*='{safe}']")
                                        except Exception:
                                            pass
                                        if not inp:
                                            all_inp = await page.query_selector_all("input, textarea")
                                            for c in all_inp:
                                                ph = await c.get_attribute("placeholder") or ""
                                                if target[:20].lower() in ph.lower():
                                                    inp = c
                                                    break
                                        if inp:
                                            await inp.fill(step.get("value", "test"))
                                        else:
                                            tc_passed = False
                                            error_msg = f"Input not found: {target[:50]}"
                            except Exception as tc_err:
                                tc_passed = False
                                error_msg = str(tc_err)[:150]
                            pw_results.append({
                                "test_id": tc.get("test_id", ""),
                                "name": tc.get("name", ""),
                                "status": "passed" if tc_passed else "failed",
                                "priority": tc.get("priority", "P1"),
                                "error": error_msg,
                            })
                        await browser.close()

                    pw_passed = sum(1 for r in pw_results if r["status"] == "passed")
                    entry["result"] = {
                        "app_name": app_name,
                        "test_cases": test_cases,
                        "workflows": baseline_result.get("workflows", []),
                        "total_tests": len(pw_results),
                        "rerun": True,
                        "baseline_run_id": baseline_run_id,
                        "execution": pw_results,
                        "summary": {
                            "total": len(pw_results),
                            "passed": pw_passed,
                            "failed": len(pw_results) - pw_passed,
                            "pass_rate": pw_passed / len(pw_results) if pw_results else 0,
                        },
                    }
                else:
                    entry["status"] = "error"
                    entry["error"] = "No relay session or local device available for execution"
                    return

                entry["status"] = "complete"
                if entry.get("result"):
                    _persist_result(run_id, {
                        "run_id": run_id,
                        "app_name": app_name,
                        "app_url": app_url,
                        "flow_type": flow_type,
                        "started_at": entry["started_at"],
                        "completed_at": _now_iso(),
                        "baseline_run_id": baseline_run_id,
                        "result": entry["result"],
                    })
                    _persisted_results[run_id] = entry

            except Exception as exc:
                logger.exception("Rerun %s failed", run_id)
                entry["status"] = "error"
                entry["error"] = str(exc)
            finally:
                _evict_completed()

        _create_pipeline_task(_rerun_background(), run_id)

        return {
            "run_id": run_id,
            "status": "running",
            "flow_type": flow_type,
            "baseline_run_id": baseline_run_id,
            "tests_to_run": len(test_cases),
            "failures_only": failures_only,
            "skipped_stages": ["CRAWL", "WORKFLOW", "TESTCASE"],
            "view_url": _view_url(run_id),
            "stream_url": f"/api/demo/pipeline-stream/{run_id}",
            "message": f"Rerun started: {len(test_cases)} tests from {baseline_run_id} (skipping crawl/workflow/testcase). Poll ta.pipeline.status for progress.",
        }

    # ── ta.collect_trace_bundle ─────────────────────────────────────────
    if tool == "ta.collect_trace_bundle":
        run_id = args.get("run_id")
        if not run_id:
            return {"error": "run_id is required"}
        access_err = _check_run_access(run_id, caller_id)
        if access_err:
            return {"error": access_err}

        include_video = args.get("include_video", True)
        compress_format = args.get("compress_format", "zip")

        entry = _running_pipelines.get(run_id)
        if not entry:
            # Fall back to disk-persisted results
            persisted = _persisted_results.get(run_id)
            if persisted:
                entry = {
                    "run_id": run_id,
                    "status": "complete",
                    "app_name": persisted.get("app_name", ""),
                    "flow_type": persisted.get("flow_type", ""),
                    "started_at": persisted.get("started_at", ""),
                    "events": persisted.get("events", []),
                    "result": persisted.get("result", persisted),
                }
            else:
                return {"error": f"Unknown run_id: {run_id}"}
        if entry.get("status") == "running":
            return {"error": "Pipeline still running. Wait for completion before collecting traces."}

        result = entry.get("result", {})
        events = entry.get("events", [])

        # Collect available artifact references from events
        screenshots = []
        action_spans = []
        tool_calls = []
        for ev in events:
            data = ev.get("data", {})
            ev_type = ev.get("type", "")
            if ev_type == "tool_call":
                tool_calls.append({
                    "tool": data.get("tool_name", ""),
                    "timestamp": ev.get("timestamp", ""),
                    "call_id": data.get("call_id", ""),
                })
            if ev_type == "tool_call_output" and data.get("tool_name") == "take_screenshot":
                screenshots.append(ev.get("timestamp", ""))

        bundle = {
            "run_id": run_id,
            "app_name": entry.get("app_name", ""),
            "flow_type": entry.get("flow_type", "unknown"),
            "started_at": entry.get("started_at", ""),
            "status": entry["status"],
            "compress_format": compress_format,
            "include_video": include_video,
            "artifacts": {
                "total_events": len(events),
                "tool_calls_count": len(tool_calls),
                "screenshots_count": len(screenshots),
                "has_result": result is not None and len(result) > 0,
                "stages_traversed": list({
                    ev.get("data", {}).get("to_stage")
                    for ev in events
                    if ev.get("type") == "stage_transition"
                    and ev.get("data", {}).get("to_stage")
                }),
            },
            "test_summary": {
                "total_tests": result.get("total_tests", 0),
                "total_workflows": len(result.get("workflows", [])),
            } if result else None,
        }

        # Include execution results if available
        if result:
            execution = _normalize_execution(result)
            if execution.get("results"):
                bundle["execution_summary"] = {
                    "executed": True,
                    "total": execution.get("total", 0),
                    "passed": execution.get("passed", 0),
                    "failed": execution.get("failed", 0),
                    "pass_rate": execution.get("pass_rate", 0.0),
                    "screenshots_captured": sum(
                        len(tr.get("screenshots", []))
                        for tr in execution.get("results", [])
                    ),
                }

        bundle["view_url"] = _view_url(run_id)
        return bundle

    # ── ta.summarize_failure ────────────────────────────────────────────
    if tool == "ta.summarize_failure":
        run_id = args.get("run_id")
        if not run_id:
            return {"error": "run_id is required"}
        access_err = _check_run_access(run_id, caller_id)
        if access_err:
            return {"error": access_err}

        max_tokens = int(args.get("max_tokens", 500))
        priority_filter = args.get("priority_filter", "all")

        result = _get_run_result(run_id)
        if not result:
            return {"error": f"No results for run_id: {run_id}"}

        # Prefer execution results for real failure data
        execution = _normalize_execution(result)
        if execution.get("results"):
            exec_results = execution["results"]
            failures = []
            total_tests = execution.get("total", 0)

            for tr in exec_results:
                status = tr.get("status", "unknown")
                priority = str(tr.get("priority", "medium")).lower()

                if priority_filter != "all" and priority != priority_filter:
                    continue

                if status not in ("pass", "passed"):
                    # Find the first failing step for root-cause
                    failing_step = None
                    for sr in tr.get("step_results", []):
                        if sr.get("status") != "pass":
                            failing_step = sr
                            break

                    summary_entry = {
                        "test_id": tr.get("test_id", ""),
                        "name": tr.get("name", ""),
                        "priority": tr.get("priority", "medium"),
                        "category": tr.get("category", ""),
                        "status": status,
                        "steps_passed": sum(1 for s in tr.get("step_results", []) if s.get("status") == "pass"),
                        "steps_total": tr.get("steps_executed", 0),
                        "duration_ms": tr.get("duration_ms", 0),
                        "screenshots": len(tr.get("screenshots", [])),
                    }
                    if failing_step:
                        summary_entry["failing_step"] = {
                            "step_number": failing_step.get("step_number"),
                            "action": failing_step.get("action", ""),
                            "expected": failing_step.get("expected", ""),
                            "actual": failing_step.get("actual_result", ""),
                            "screenshot": failing_step.get("screenshot_path"),
                        }
                    if tr.get("error"):
                        summary_entry["error"] = tr["error"]

                    failures.append(summary_entry)

            char_budget = max_tokens * 4
            truncated = False
            trimmed_failures = []
            running_chars = 0
            for f in failures:
                entry_chars = len(json.dumps(f))
                if running_chars + entry_chars > char_budget:
                    truncated = True
                    break
                trimmed_failures.append(f)
                running_chars += entry_chars

            return {
                "run_id": run_id,
                "total_test_cases": total_tests,
                "executed": True,
                "failure_count": len(failures),
                "shown": len(trimmed_failures),
                "truncated": truncated,
                "priority_filter": priority_filter,
                "pass_rate": execution.get("pass_rate", 0.0),
                "failures": trimmed_failures,
                "view_url": _view_url(run_id),
            }

        # Fallback: generated-only test cases (no execution)
        test_cases = _extract_test_cases(result)
        if not test_cases:
            return {"run_id": run_id, "failures": [], "message": "No test cases found in results."}

        failures = []
        for tc in test_cases:
            status = str(tc.get("status", tc.get("expected_result", ""))).lower()
            priority = str(tc.get("priority", "medium")).lower()

            if priority_filter != "all" and priority != priority_filter:
                continue

            is_failure = any(kw in status for kw in ("fail", "error", "block", "bug"))
            if not is_failure and "pass" not in status:
                is_failure = True

            if is_failure:
                summary_entry = {
                    "test_id": tc.get("test_id", ""),
                    "name": tc.get("name", ""),
                    "priority": tc.get("priority", "medium"),
                    "category": tc.get("category", ""),
                    "workflow_name": tc.get("workflow_name", ""),
                }
                if tc.get("preconditions"):
                    summary_entry["hint"] = tc["preconditions"][:100]
                if tc.get("steps"):
                    summary_entry["steps_count"] = len(tc["steps"])

                failures.append(summary_entry)

        # Truncate to stay within token budget (~4 chars per token rough estimate)
        char_budget = max_tokens * 4
        truncated = False
        trimmed_failures = []
        running_chars = 0
        for f in failures:
            entry_chars = len(json.dumps(f))
            if running_chars + entry_chars > char_budget:
                truncated = True
                break
            trimmed_failures.append(f)
            running_chars += entry_chars

        return {
            "run_id": run_id,
            "total_test_cases": len(test_cases),
            "failure_count": len(failures),
            "shown": len(trimmed_failures),
            "truncated": truncated,
            "priority_filter": priority_filter,
            "failures": trimmed_failures,
            "view_url": _view_url(run_id),
        }

    # ── ta.compare_before_after ─────────────────────────────────────────
    if tool == "ta.compare_before_after":
        baseline_run_id = args.get("baseline_run_id")
        current_run_id = args.get("current_run_id")
        if not baseline_run_id or not current_run_id:
            return {"error": "Both baseline_run_id and current_run_id are required"}
        for rid in (baseline_run_id, current_run_id):
            access_err = _check_run_access(rid, caller_id)
            if access_err:
                return {"error": access_err}

        include_metrics = args.get("include_metrics", True)

        baseline_result = _get_run_result(baseline_run_id)
        current_result = _get_run_result(current_run_id)

        if not baseline_result:
            return {"error": f"No results for baseline_run_id: {baseline_run_id}"}
        if not current_result:
            return {"error": f"No results for current_run_id: {current_run_id}"}

        baseline_cases = _extract_test_cases(baseline_result)
        current_cases = _extract_test_cases(current_result)

        # Build lookup by test name for comparison
        baseline_by_name = {tc.get("name", ""): tc for tc in baseline_cases}
        current_by_name = {tc.get("name", ""): tc for tc in current_cases}

        all_names = set(baseline_by_name.keys()) | set(current_by_name.keys())

        new_tests = []
        removed_tests = []
        regressions = []  # was passing, now failing
        fixes = []        # was failing, now passing
        unchanged = 0

        for name in sorted(all_names):
            b = baseline_by_name.get(name)
            c = current_by_name.get(name)

            if not b and c:
                new_tests.append(name)
                continue
            if b and not c:
                removed_tests.append(name)
                continue

            b_status = str(b.get("status", b.get("expected_result", ""))).lower()
            c_status = str(c.get("status", c.get("expected_result", ""))).lower()
            b_pass = "pass" in b_status
            c_pass = "pass" in c_status

            if b_pass and not c_pass:
                regressions.append(name)
            elif not b_pass and c_pass:
                fixes.append(name)
            else:
                unchanged += 1

        diff = {
            "baseline_run_id": baseline_run_id,
            "current_run_id": current_run_id,
            "baseline_test_count": len(baseline_cases),
            "current_test_count": len(current_cases),
            "new_tests": new_tests,
            "removed_tests": removed_tests,
            "regressions": regressions,
            "fixes": fixes,
            "unchanged": unchanged,
        }

        if include_metrics:
            baseline_wf = len(baseline_result.get("workflows", []))
            current_wf = len(current_result.get("workflows", []))
            diff["metrics"] = {
                "baseline_workflows": baseline_wf,
                "current_workflows": current_wf,
                "workflow_delta": current_wf - baseline_wf,
                "test_count_delta": len(current_cases) - len(baseline_cases),
                "regression_count": len(regressions),
                "fix_count": len(fixes),
            }

        # ── Contextual graph enrichment ───────────────────────────────────
        # Loads the graph (try/except, load latest graph file) and annotates
        # each status-changed test with verdict attribution and history.
        try:
            _cg_dir = Path(__file__).resolve().parents[2] / "data" / "context_graphs"
            _graph = None

            # Try loading graph for the current run, then baseline
            for _rid in (current_run_id, baseline_run_id):
                _gpath = _cg_dir / f"{_rid}.json"
                if _gpath.exists():
                    from ..agents.qa_pipeline.context_graph import ContextGraph
                    _graph = ContextGraph.load(_gpath)
                    break

            # Fallback: load latest graph file if per-run graphs absent
            if _graph is None and _cg_dir.exists():
                _candidates = sorted(
                    _cg_dir.glob("*.json"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if _candidates:
                    from ..agents.qa_pipeline.context_graph import ContextGraph
                    _graph = ContextGraph.load(_candidates[0])

            if _graph is not None:
                from ..agents.qa_pipeline.context_graph import NodeType as _NT

                _all_verdicts = _graph.get_nodes_by_type(_NT.VERDICT)
                _all_outcomes = _graph.get_nodes_by_type(_NT.OUTCOME)
                _all_constraints = _graph.get_nodes_by_type(_NT.CONSTRAINT)

                # --- helpers ---
                def _find_verdict_for_test(test_name: str, rid: str):
                    """Find verdict node matching a test in a given run."""
                    name_lower = test_name.lower()
                    for v in _all_verdicts:
                        if v.run_id == rid and name_lower in v.label.lower():
                            return v
                    # Broader: any verdict in this run referencing the test
                    for o in _all_outcomes:
                        if o.run_id == rid and name_lower in o.label.lower():
                            for v in _all_verdicts:
                                if v.run_id == rid:
                                    return v
                    return None

                def _find_constraint_for_test(test_name: str):
                    name_lower = test_name.lower()
                    for c in _all_constraints:
                        if name_lower in c.label.lower():
                            return c.label
                    return None

                # --- Per-test annotations ---
                test_annotations = {}

                # Fixes: was failing, now passes
                for name in fixes:
                    test_annotations[name] = {
                        "status_change": "fixed",
                        "note": "\u2713 Your fix worked",
                    }

                # Regressions: was passing, now fails
                for name in regressions:
                    test_annotations[name] = {
                        "status_change": "regression",
                        "note": "\u26a0 Regression \u2014 check what changed",
                    }

                # Still-failing tests (both runs failing)
                _still_failing = []
                for name in sorted(all_names):
                    b = baseline_by_name.get(name)
                    c = current_by_name.get(name)
                    if b and c:
                        b_pass = "pass" in str(
                            b.get("status", b.get("expected_result", ""))
                        ).lower()
                        c_pass = "pass" in str(
                            c.get("status", c.get("expected_result", ""))
                        ).lower()
                        if not b_pass and not c_pass:
                            _still_failing.append(name)

                for name in _still_failing:
                    verdict = _find_verdict_for_test(name, current_run_id)
                    if verdict:
                        attr = verdict.data.get("attribution", "unknown")
                        test_annotations[name] = {
                            "status_change": "still_failing",
                            "note": f"Known issue: {attr}",
                            "attribution": attr,
                        }
                    else:
                        test_annotations[name] = {
                            "status_change": "still_failing",
                            "note": "Still failing \u2014 no verdict attribution yet",
                        }

                # Skipped tests: check for constraint nodes
                for name in sorted(all_names):
                    c = current_by_name.get(name)
                    if c:
                        c_status = str(
                            c.get("status", c.get("expected_result", ""))
                        ).lower()
                        if "skip" in c_status or "blocked" in c_status:
                            reason = _find_constraint_for_test(name)
                            test_annotations[name] = {
                                "status_change": "skipped",
                                "note": f"Skipped: {reason}" if reason else "Skipped \u2014 no constraint reason found",
                            }

                if test_annotations:
                    diff["graph_annotations"] = test_annotations

                # --- Verdict history for changed tests ---
                test_history = {}
                _runs_in_graph = {
                    v.run_id for v in _all_verdicts if v.run_id
                }
                _changed_tests = set(fixes) | set(regressions) | set(_still_failing)

                for name in _changed_tests:
                    _fail_count = 0
                    _total_count = 0
                    name_lower = name.lower()
                    for o in _all_outcomes:
                        if o.run_id and name_lower in o.label.lower():
                            _total_count += 1
                            o_status = o.data.get("status", "").lower()
                            if any(kw in o_status for kw in ("fail", "error", "bug")):
                                _fail_count += 1

                    if _total_count > 1:
                        if 0 < _fail_count < _total_count:
                            assessment = f"Failed {_fail_count}/{_total_count} last runs \u2014 likely flaky (environment)"
                        elif _fail_count == _total_count:
                            assessment = f"Failed {_fail_count}/{_total_count} runs \u2014 persistent issue"
                        else:
                            assessment = f"Passed all {_total_count} prior runs"
                        test_history[name] = {
                            "failed_runs": _fail_count,
                            "total_runs": _total_count,
                            "assessment": assessment,
                        }
                    elif name in (set(regressions) | set(_still_failing)):
                        test_history[name] = {
                            "failed_runs": _fail_count,
                            "total_runs": max(_total_count, 1),
                            "assessment": "First time seeing this failure \u2014 likely new bug",
                        }

                if test_history:
                    diff["test_history"] = test_history

                # --- Graph confidence summary ---
                verdict_stats = _graph.get_verdict_stats()
                total_verdicts = verdict_stats.get("total_verdicts", 0)
                prior_runs = len(_runs_in_graph)

                if len(fixes) > len(regressions):
                    _direction = "healthier"
                elif len(regressions) > len(fixes):
                    _direction = "worse"
                else:
                    _direction = "unchanged"

                _confidence = min(95, 40 + (prior_runs * 8) + (total_verdicts * 2))

                diff["graph_summary"] = {
                    "confidence_pct": _confidence,
                    "direction": _direction,
                    "message": (
                        f"Graph confidence: {_confidence}% this build is {_direction} than last. "
                        f"Based on: {total_verdicts} precedents across {prior_runs} prior runs"
                    ),
                    "total_precedents": total_verdicts,
                    "prior_runs": prior_runs,
                }

        except Exception as _graph_err:
            logger.debug(f"Graph enrichment skipped for compare: {_graph_err}")

        return diff

    # ── ta.emit_verdict ─────────────────────────────────────────────────
    if tool == "ta.emit_verdict":
        run_id = args.get("run_id")
        if not run_id:
            return {"error": "run_id is required"}
        access_err = _check_run_access(run_id, caller_id)
        if access_err:
            return {"error": access_err}

        pass_threshold = float(args.get("pass_threshold", 0.8))

        entry = _running_pipelines.get(run_id)
        if entry and entry["status"] == "running":
            return {"error": "Pipeline still running. Wait for completion before emitting verdict."}

        result = _get_run_result(run_id)
        if not result:
            return {"error": f"No results for run_id: {run_id}"}

        # Prefer execution results (Stage 4) over generated test specs
        execution = _normalize_execution(result)
        if execution.get("results"):
            # Real execution data available
            total = execution["total"]
            pass_count = execution["passed"]
            fail_count = execution["failed"]
            pass_rate = execution["pass_rate"]

            # Include per-test breakdown
            test_breakdown = []
            for tr in execution["results"]:
                test_breakdown.append({
                    "test_id": tr.get("test_id", ""),
                    "name": tr.get("name", tr.get("test_id", "unknown")),
                    "status": tr.get("status", "unknown"),
                    "priority": tr.get("priority", ""),
                    "steps_passed": sum(1 for s in tr.get("step_results", []) if s.get("status") == "pass"),
                    "steps_total": tr.get("steps_executed", 0),
                    "screenshots": len(tr.get("screenshots", [])),
                    "duration_ms": tr.get("duration_ms", 0),
                })
        else:
            # Fallback: count from test case specs (old behavior)
            test_cases = _extract_test_cases(result)
            total = len(test_cases)
            test_breakdown = []

            if total == 0:
                return {
                    "run_id": run_id,
                    "verdict": "blocked",
                    "reason": "No test cases found in results",
                    "pass_rate": 0.0,
                    "pass_threshold": pass_threshold,
                    "total_tests": 0,
                }

            pass_count = 0
            fail_count = 0
            for tc in test_cases:
                status = str(tc.get("status", tc.get("expected_result", ""))).lower()
                if "pass" in status:
                    pass_count += 1
                elif any(kw in status for kw in ("fail", "error", "bug")):
                    fail_count += 1
            pass_rate = pass_count / total if total > 0 else 0.0

        if pass_rate >= pass_threshold:
            verdict = "pass"
            reason = f"{pass_count}/{total} tests passed ({pass_rate:.0%} >= {pass_threshold:.0%} threshold)"
        elif entry and entry.get("status") == "error":
            verdict = "blocked"
            reason = f"Pipeline error: {entry.get('error', 'unknown')}"
        else:
            verdict = "fail"
            reason = f"{pass_count}/{total} tests passed ({pass_rate:.0%} < {pass_threshold:.0%} threshold)"

        verdict_result = {
            "run_id": run_id,
            "verdict": verdict,
            "reason": reason,
            "pass_rate": round(pass_rate, 4),
            "pass_threshold": pass_threshold,
            "total_tests": total,
            "passed": pass_count,
            "failed": fail_count,
            "other": total - pass_count - fail_count,
            "executed": execution is not None,
            "view_url": _view_url(run_id),
            "view_message": "Open this URL to see full results with screenshots and execution details",
        }
        if test_breakdown:
            verdict_result["test_breakdown"] = test_breakdown
        # Include timing metadata if available
        if entry:
            if entry.get("duration_s") is not None:
                verdict_result["duration_s"] = entry["duration_s"]
            if entry.get("tool_call_count") is not None:
                verdict_result["tool_call_count"] = entry["tool_call_count"]
            if entry.get("stage_timings"):
                verdict_result["stage_timings"] = entry["stage_timings"]
        return verdict_result

    # ── ta.suggest_fix_context ──────────────────────────────────────────
    if tool == "ta.suggest_fix_context":
        run_id = args.get("run_id")
        if not run_id:
            return {"error": "run_id is required"}
        access_err = _check_run_access(run_id, caller_id)
        if access_err:
            return {"error": access_err}

        max_files = int(args.get("max_files", 5))

        result = _get_run_result(run_id)
        if not result:
            return {"error": f"No results for run_id: {run_id}"}

        test_cases = _extract_test_cases(result)
        entry = _running_pipelines.get(run_id, {})

        # Gather failure info
        failures = []
        categories = set()
        workflow_names = set()
        for tc in test_cases:
            status = str(tc.get("status", tc.get("expected_result", ""))).lower()
            if "pass" in status:
                continue
            failures.append({
                "test_id": tc.get("test_id", ""),
                "name": tc.get("name", ""),
                "category": tc.get("category", ""),
                "workflow_name": tc.get("workflow_name", ""),
            })
            if tc.get("category"):
                categories.add(tc["category"])
            if tc.get("workflow_name"):
                workflow_names.add(tc["workflow_name"])

        if not failures:
            return {
                "run_id": run_id,
                "message": "No failures found — all tests passing.",
                "suggestions": [],
            }

        # Build root-cause candidates based on failure categories
        suggestions = []

        # Generic fix hints based on failure categories — NOT hardcoded to retention.sh paths.
        # The user is testing THEIR app, so we provide investigation guidance, not file paths.
        category_investigation_hints = {
            "navigation": "Check routing config, screen transition handlers, and back-button behavior",
            "authentication": "Check login/signup flow, token storage, session management, and auth middleware",
            "form_validation": "Check input validators, required field checks, and error message rendering",
            "data_display": "Check data fetching, API response parsing, list rendering, and empty-state handling",
            "api_integration": "Check API endpoint handlers, request/response schemas, and error responses",
            "ui_rendering": "Check component render logic, conditional display, CSS/layout, and responsive behavior",
            "state_management": "Check state updates, context providers, store mutations, and side effects",
            "performance": "Check expensive renders, unnecessary re-fetches, large payloads, and missing pagination",
            "smoke": "Check app entry point, main screen load, and critical path initialization",
            "regression": "Check recent git changes to this feature area — something that worked before is now broken",
            "edge_case": "Check boundary conditions: empty inputs, very long strings, special characters, concurrent actions",
            "negative": "Check error handling: invalid input rejection, graceful failures, user-facing error messages",
            "accessibility": "Check ARIA labels, focus management, color contrast, and keyboard navigation",
        }

        for cat in categories:
            cat_lower = cat.lower().replace(" ", "_")
            for key, hint in category_investigation_hints.items():
                if key in cat_lower or cat_lower in key:
                    suggestions.append({"category": cat, "investigation": hint})
                    break

        # Workflow-based hints
        for wf_name in workflow_names:
            wf_lower = wf_name.lower()
            hint = None
            if "login" in wf_lower or "auth" in wf_lower:
                hint = "Check authentication flow: login form, credential validation, token handling"
            elif "search" in wf_lower:
                hint = "Check search input handling, query execution, and result rendering"
            elif "create" in wf_lower or "add" in wf_lower:
                hint = "Check form submission, data creation endpoint, and success/error feedback"
            elif "delete" in wf_lower or "remove" in wf_lower:
                hint = "Check deletion confirmation, API call, and list/UI update after removal"
            elif "settings" in wf_lower or "profile" in wf_lower:
                hint = "Check settings form, save handler, and state persistence"
            if hint:
                suggestions.append({"workflow": wf_name, "investigation": hint})

        if not suggestions:
            suggestions.append({
                "investigation": "Start by checking the main app entry point and the primary screen render path",
            })

        return {
            "run_id": run_id,
            "failure_count": len(failures),
            "categories": sorted(categories),
            "workflow_names": sorted(workflow_names),
            "suggestions": suggestions[:max_files],
            "top_failures": failures[:5],
        }

    raise ValueError(f"Unknown QA verification tool: {tool}")


# ---------------------------------------------------------------------------
# Benchmark harness — consecutive QA flow runs with timing/cost tracking
# ---------------------------------------------------------------------------

_BENCHMARK_DIR = Path(__file__).resolve().parents[2] / "data" / "benchmark_runs"
_BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)

_running_benchmarks: Dict[str, dict] = {}


async def run_qa_benchmark(
    *,
    flow_type: str = "web",
    target: str = "",           # url or app_package
    app_name: str = "Benchmark App",
    run_count: int = 1,
    timeout_per_run: int = 1200,  # Emulator benchmark runs need 20 min
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run N consecutive QA flows against a frozen app, collecting timing data.

    Returns a benchmark_id that can be polled via get_qa_benchmark().
    """
    benchmark_id = f"qa-bench-{uuid.uuid4().hex[:8]}"
    _running_benchmarks[benchmark_id] = {
        "benchmark_id": benchmark_id,
        "status": "running",
        "flow_type": flow_type,
        "target": target,
        "app_name": app_name,
        "run_count": run_count,
        "completed_runs": 0,
        "runs": [],
        "started_at": _now_iso(),
        "error": None,
    }

    async def _execute():
        entry = _running_benchmarks[benchmark_id]
        try:
            for i in range(run_count):
                # Start a QA flow
                if flow_type == "web":
                    result = await dispatch_qa_verification(
                        "ta.run_web_flow",
                        {"url": target, "app_name": app_name, "timeout_seconds": timeout_per_run},
                    )
                else:
                    result = await dispatch_qa_verification(
                        "ta.run_android_flow",
                        {
                            "app_package": target,
                            "app_name": app_name,
                            "device_id": device_id or "",
                            "timeout_seconds": timeout_per_run,
                        },
                    )

                if result.get("error"):
                    entry["runs"].append({
                        "run_index": i + 1,
                        "status": "error",
                        "error": result["error"],
                    })
                    entry["completed_runs"] = i + 1
                    continue

                run_id = result.get("run_id", "")

                # Poll until complete
                poll_start = datetime.now(timezone.utc)
                while True:
                    await asyncio.sleep(5)
                    elapsed = (datetime.now(timezone.utc) - poll_start).total_seconds()
                    if elapsed > timeout_per_run + 60:
                        break
                    pipeline_entry = _running_pipelines.get(run_id)
                    if not pipeline_entry or pipeline_entry["status"] != "running":
                        break

                pipeline_entry = _running_pipelines.get(run_id, {})

                # Get verdict
                verdict = await dispatch_qa_verification(
                    "ta.emit_verdict", {"run_id": run_id, "pass_threshold": 0.8}
                )

                run_record = {
                    "run_index": i + 1,
                    "run_id": run_id,
                    "status": pipeline_entry.get("status", "unknown"),
                    "duration_s": pipeline_entry.get("duration_s"),
                    "tool_call_count": pipeline_entry.get("tool_call_count", 0),
                    "stage_timings": pipeline_entry.get("stage_timings", {}),
                    "event_count": len(pipeline_entry.get("events", [])),
                    "verdict": verdict.get("verdict", "unknown"),
                    "pass_rate": verdict.get("pass_rate", 0.0),
                    "total_tests": verdict.get("total_tests", 0),
                    "passed": verdict.get("passed", 0),
                    "failed": verdict.get("failed", 0),
                }
                entry["runs"].append(run_record)
                entry["completed_runs"] = i + 1

            entry["status"] = "complete"
            entry["completed_at"] = _now_iso()

            # Compute aggregate stats
            durations = [r["duration_s"] for r in entry["runs"] if r.get("duration_s")]
            tool_calls = [r["tool_call_count"] for r in entry["runs"] if r.get("tool_call_count")]
            pass_rates = [r["pass_rate"] for r in entry["runs"] if r.get("pass_rate") is not None]

            entry["aggregate"] = {
                "total_runs": len(entry["runs"]),
                "successful_runs": sum(1 for r in entry["runs"] if r.get("status") == "complete"),
                "avg_duration_s": round(sum(durations) / len(durations), 2) if durations else None,
                "min_duration_s": round(min(durations), 2) if durations else None,
                "max_duration_s": round(max(durations), 2) if durations else None,
                "total_duration_s": round(sum(durations), 2) if durations else None,
                "avg_tool_calls": round(sum(tool_calls) / len(tool_calls), 1) if tool_calls else None,
                "total_tool_calls": sum(tool_calls) if tool_calls else 0,
                "avg_pass_rate": round(sum(pass_rates) / len(pass_rates), 4) if pass_rates else None,
                "consistency": all(r.get("verdict") == "pass" for r in entry["runs"]) if entry["runs"] else False,
            }

            # Persist
            try:
                bench_path = _BENCHMARK_DIR / f"{benchmark_id}.json"
                bench_path.write_text(json.dumps(entry, indent=2, default=str))
            except Exception as e:
                logger.warning(f"Failed to persist benchmark {benchmark_id}: {e}")

        except Exception as exc:
            entry["status"] = "error"
            entry["error"] = str(exc)
            logger.exception("Benchmark %s failed", benchmark_id)

    asyncio.create_task(_execute())
    return {
        "benchmark_id": benchmark_id,
        "status": "running",
        "run_count": run_count,
        "message": f"Benchmark started: {run_count} consecutive {flow_type} QA flows against {app_name}.",
    }


def get_qa_benchmark(benchmark_id: str) -> Dict[str, Any]:
    """Get status/results of a running or completed benchmark."""
    entry = _running_benchmarks.get(benchmark_id)
    if not entry:
        # Try disk
        bench_path = _BENCHMARK_DIR / f"{benchmark_id}.json"
        if bench_path.exists():
            try:
                return json.loads(bench_path.read_text())
            except Exception:
                pass
        return {"error": f"Unknown benchmark_id: {benchmark_id}"}
    return entry


def list_qa_benchmarks() -> List[Dict[str, Any]]:
    """List all benchmark runs."""
    results = []
    for bench_path in sorted(_BENCHMARK_DIR.glob("qa-bench-*.json"), reverse=True):
        try:
            data = json.loads(bench_path.read_text())
            results.append({
                "benchmark_id": data.get("benchmark_id", bench_path.stem),
                "status": data.get("status", "unknown"),
                "flow_type": data.get("flow_type", ""),
                "app_name": data.get("app_name", ""),
                "run_count": data.get("run_count", 0),
                "completed_runs": data.get("completed_runs", 0),
                "started_at": data.get("started_at", ""),
                "aggregate": data.get("aggregate"),
            })
        except Exception:
            pass
    # Add in-memory running ones
    for bid, entry in _running_benchmarks.items():
        if not any(r.get("benchmark_id") == bid for r in results):
            results.append({
                "benchmark_id": bid,
                "status": entry.get("status", "running"),
                "flow_type": entry.get("flow_type", ""),
                "app_name": entry.get("app_name", ""),
                "run_count": entry.get("run_count", 0),
                "completed_runs": entry.get("completed_runs", 0),
                "started_at": entry.get("started_at", ""),
                "aggregate": entry.get("aggregate"),
            })
    return results
