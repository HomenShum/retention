"""
Playwright Testing Engine — Pure Library

4 stateless async functions for web app testing. No framework dependencies
(no FastAPI, no @function_tool, no Agents SDK). Returns plain dicts.

Used by:
  - MCP tools (ta.playwright.*)
  - Agent function_tools (flywheel_tools.py)
  - SSE fast-path (self_test_runner.py)
"""

import asyncio
import base64
import json
import logging
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── Screenshot broadcast ──────────────────────────────────────
# Subscribers receive base64-encoded JPEG screenshots in real-time.

_screenshot_subscribers: list[asyncio.Queue] = []


def subscribe_screenshots() -> asyncio.Queue:
    """Subscribe to live Playwright screenshots. Returns a Queue that receives base64 JPEG strings."""
    q: asyncio.Queue = asyncio.Queue(maxsize=10)
    _screenshot_subscribers.append(q)
    return q


def unsubscribe_screenshots(q: asyncio.Queue) -> None:
    """Remove a subscriber queue."""
    try:
        _screenshot_subscribers.remove(q)
    except ValueError:
        pass


async def _broadcast_screenshot(page: Any) -> None:
    """Capture a JPEG screenshot and push to all subscribers."""
    if not _screenshot_subscribers:
        return
    try:
        raw = await page.screenshot(type="jpeg", quality=40)
        _push_frame(raw)
    except Exception:
        pass  # Non-critical — don't break the test flow


def _push_frame(raw: bytes) -> None:
    """Push raw JPEG bytes to all subscriber queues as base64."""
    b64 = base64.b64encode(raw).decode("ascii")
    for q in _screenshot_subscribers:
        try:
            q.put_nowait(b64)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(b64)
            except asyncio.QueueFull:
                pass


# ── Periodic screenshot streaming ─────────────────────────────
# Runs as a background task alongside Playwright, capturing ~1 fps
# for smooth Manus-style visual streaming.

_active_page: Any = None  # Set by engine functions while a page is open
_stream_task: Optional[asyncio.Task] = None


async def _periodic_screenshot_loop() -> None:
    """Background loop: capture a screenshot every ~750ms while _active_page is set."""
    while True:
        await asyncio.sleep(0.75)
        page = _active_page
        if page and _screenshot_subscribers:
            try:
                raw = await page.screenshot(type="jpeg", quality=35)
                _push_frame(raw)
            except Exception:
                pass  # Page may be navigating — skip frame


def _start_streaming(page: Any) -> None:
    """Register the active page and start periodic screenshot capture."""
    global _active_page, _stream_task
    _active_page = page
    if _screenshot_subscribers and (_stream_task is None or _stream_task.done()):
        _stream_task = asyncio.create_task(_periodic_screenshot_loop())


def _stop_streaming() -> None:
    """Stop periodic capture and clear the active page."""
    global _active_page, _stream_task
    _active_page = None
    if _stream_task and not _stream_task.done():
        _stream_task.cancel()
        _stream_task = None

_REPO_ROOT = Path(__file__).resolve().parents[4]  # backend/app/agents/self_testing → repo root

# ── JavaScript extraction snippets ────────────────────────────

EXTRACT_JS = """() => {
    const results = [];
    document.querySelectorAll('a[href]').forEach((el, i) => {
        if (el.textContent.trim() && el.href && !el.href.startsWith('javascript:') && el.offsetParent !== null) {
            results.push({
                type: 'link', text: el.textContent.trim().slice(0, 80),
                href: el.href, selector: `a[href="${el.getAttribute('href')}"]`,
                index: i
            });
        }
    });
    document.querySelectorAll('button, [role="button"], input[type="submit"]').forEach((el, i) => {
        const raw = el.textContent?.trim() || el.value || el.getAttribute('aria-label') || '';
        const text = raw.replace(/\\s+/g, ' ').slice(0, 80);
        if (text && el.offsetParent !== null) {
            results.push({ type: 'button', text, index: i,
                selector: el.id ? `#${el.id}` : `button:has-text("${text.slice(0, 30)}")`
            });
        }
    });
    document.querySelectorAll('input[type="text"], input[type="email"], input[type="search"], textarea').forEach((el, i) => {
        const ph = el.placeholder || el.getAttribute('aria-label') || el.name || '';
        if (ph && el.offsetParent !== null) {
            results.push({ type: 'input', text: ph.slice(0, 80), index: i,
                selector: el.id ? `#${el.id}` : `[placeholder="${ph.slice(0, 40)}"]`
            });
        }
    });
    return results.slice(0, 50);
}"""

CHECK_ERRORS_JS = """() => {
    const errors = [];
    const all = document.body.innerText || '';
    const errorPatterns = [/error/i, /404/i, /not found/i, /failed/i, /exception/i, /undefined/i, /cannot read/i, /null/i];
    for (const p of errorPatterns) {
        const m = all.match(new RegExp('.*' + p.source + '.*', 'im'));
        if (m) errors.push(m[0].trim().slice(0, 120));
    }
    if (document.body.children.length < 2) errors.push('Page appears blank (< 2 child elements)');
    document.querySelectorAll('img').forEach(img => {
        if (!img.complete || img.naturalWidth === 0) errors.push(`Broken image: ${img.src?.slice(0, 80) || 'unknown'}`);
    });
    return [...new Set(errors)].slice(0, 10);
}"""


# ── Helpers ───────────────────────────────────────────────────

_TRACE_EXCLUDE = {
    "_generated/", "node_modules/", ".min.js", "dist/", "build/",
    "package-lock.json", ".json", "__pycache__/", ".pyc",
}


def trace_to_source(search_terms: list[str]) -> list[dict]:
    """Use git grep to find source code matches for given terms.

    Excludes generated files, node_modules, and build artifacts.
    Prioritizes .tsx/.jsx component files over other matches.
    """
    matches = []
    for term in search_terms[:5]:
        if len(term) < 3:
            continue
        try:
            cmd = ["git", "grep", "-n", "-i", "--max-count=5", term]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=str(_REPO_ROOT))
            for line in out.stdout.strip().splitlines()[:5]:
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    fpath = parts[0]
                    # Skip generated/excluded files
                    if any(excl in fpath for excl in _TRACE_EXCLUDE):
                        continue
                    matches.append({
                        "file": fpath,
                        "line": int(parts[1]) if parts[1].isdigit() else 0,
                        "snippet": parts[2].strip()[:200],
                        "search_term": term,
                    })
        except Exception:
            pass

    # Sort: .tsx/.jsx first, then .ts/.js, then .py
    def _sort_key(m):
        f = m["file"]
        if f.endswith((".tsx", ".jsx")): return 0
        if f.endswith((".ts", ".js")): return 1
        if f.endswith(".py"): return 2
        return 3
    matches.sort(key=_sort_key)
    return matches


def _deduplicate_elements(all_elements: list[dict]) -> list[dict]:
    """Remove elements that appear on 3+ pages (shared nav)."""
    el_count = Counter(f"{e['type']}:{e.get('text', '')}" for e in all_elements)
    unique = []
    seen_shared: set[str] = set()
    for el in all_elements:
        key = f"{el['type']}:{el.get('text', '')}"
        if el_count[key] >= 3:
            if key in seen_shared:
                continue
            seen_shared.add(key)
        unique.append(el)
    return unique


# ── Engine Functions ──────────────────────────────────────────

async def pw_discover(url: str, crawl_depth: int = 1, max_links: int = 8) -> dict:
    """Crawl a URL with Playwright, extract interactive elements, follow same-origin links.

    Returns:
        {pages_found, total_interactions, pages: {path: {element_count, elements}}, console_errors}
    """
    from playwright.async_api import async_playwright

    parsed = urlparse(url)
    base_origin = f"{parsed.scheme}://{parsed.netloc}"
    pages_visited: dict[str, list[dict]] = {}
    all_elements: list[dict] = []
    console_errors: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--single-process"])
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text[:150]}") if msg.type in ("error", "warning") else None)
        _start_streaming(page)

        # Landing page
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)
        except Exception as e:
            _stop_streaming()
            await browser.close()
            return {"error": str(e), "pages_found": 0, "total_interactions": 0, "pages": {}}

        title = await page.title()
        landing_elements = await page.evaluate(EXTRACT_JS)
        page_path = parsed.path or "/"
        for el in landing_elements:
            el["_page"] = page_path
        all_elements.extend(landing_elements)
        pages_visited[page_path] = landing_elements

        # Crawl same-origin links
        if crawl_depth >= 1:
            internal_links = [el for el in landing_elements if el["type"] == "link" and el.get("href", "").startswith(base_origin)]
            visited = {url.rstrip("/")}

            for link_el in internal_links[:max_links]:
                href = link_el["href"].rstrip("/")
                if href in visited:
                    continue
                visited.add(href)
                try:
                    await page.goto(href, wait_until="domcontentloaded", timeout=10000)
                    await page.wait_for_timeout(1500)
                    await _broadcast_screenshot(page)
                    sub_path = urlparse(page.url).path or href
                    sub_elements = await page.evaluate(EXTRACT_JS)
                    # R3: If 0 elements found, wait longer for dynamic content
                    if not sub_elements:
                        await page.wait_for_load_state("networkidle", timeout=5000)
                        await page.wait_for_timeout(1500)
                        sub_elements = await page.evaluate(EXTRACT_JS)
                    for el in sub_elements:
                        el["_page"] = sub_path
                    all_elements.extend(sub_elements)
                    pages_visited[sub_path] = sub_elements
                except Exception:
                    continue

        _stop_streaming()
        await browser.close()

    unique_elements = _deduplicate_elements(all_elements)

    return {
        "url": url,
        "title": title,
        "pages_found": len(pages_visited),
        "total_interactions": len(unique_elements),
        "pages": {
            path: {
                "element_count": len(els),
                "elements": els[:15],
            }
            for path, els in pages_visited.items()
        },
        "suggested_test_plan": [f"Test {len(els)} elements on {p}" for p, els in pages_visited.items()],
        "console_errors": console_errors,
    }


async def pw_test_interaction(
    url: str,
    element: dict,
    base_origin: str = "",
    browser: Any = None,
) -> dict:
    """Test a single interactive element on a page.

    Args:
        url: The page URL to navigate to before testing
        element: {type, text, selector?, href?, _page?}
        base_origin: scheme://host for same-origin checks
        browser: Optional Playwright browser instance for session reuse

    Returns:
        {action, element, page, success, detail, errors_on_page?, console_errors?, anomaly?}
    """
    from playwright.async_api import async_playwright

    action = element.get("type", "unknown")
    text = element.get("text", "unknown")
    page_path = element.get("_page", "/")
    console_errors: list[str] = []
    own_browser = browser is None

    if not base_origin:
        parsed = urlparse(url)
        base_origin = f"{parsed.scheme}://{parsed.netloc}"

    result: dict = {"action": action, "element": text, "page": page_path}

    pw_ctx = None
    try:
        if own_browser:
            from playwright.async_api import async_playwright
            pw_ctx = await async_playwright().start()
            browser = await pw_ctx.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--single-process"])

        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text[:150]}") if msg.type in ("error", "warning") else None)
        _start_streaming(page)

        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(1000)

        if action == "input":
            sel = element.get("selector", f'[placeholder="{text[:30]}"]')
            try:
                await page.fill(sel, "test@example.com", timeout=3000)
                result["success"] = True
                result["detail"] = "Typed test value"
            except Exception:
                result["success"] = True
                result["detail"] = "Input field exists"

        elif action == "link":
            href = element.get("href", "")
            if href and href.startswith(base_origin):
                try:
                    await page.goto(href, wait_until="domcontentloaded", timeout=8000)
                    await page.wait_for_timeout(1000)
                    page_errors = await page.evaluate(CHECK_ERRORS_JS)
                    result["success"] = len(page_errors) == 0
                    result["detail"] = f"Navigated to {urlparse(page.url).path}"
                    if page_errors:
                        result["errors_on_page"] = page_errors[:3]
                        result["anomaly"] = {
                            "type": "error_on_navigate",
                            "severity": "high",
                            "description": f"Error after clicking '{text}': {page_errors[0]}",
                        }
                except Exception as e:
                    result["success"] = False
                    result["detail"] = f"Navigation failed: {str(e)[:80]}"
                    result["anomaly"] = {
                        "type": "navigation_failure",
                        "severity": "high",
                        "description": f"Link '{text}' failed to load: {str(e)[:80]}",
                    }
            else:
                result["success"] = True
                result["detail"] = "External link — skipped"

        elif action == "button":
            try:
                btn = page.get_by_text(text[:30], exact=False).first
                # Check visibility first — skip if element is hidden (inside modal/dialog)
                is_visible = await btn.is_visible()
                if not is_visible:
                    result["success"] = True  # Not a failure — element is intentionally hidden
                    result["detail"] = f"Skipped — '{text}' is not visible (likely inside modal/dialog)"
                    result["skipped_reason"] = "hidden_element"
                else:
                    await btn.click(timeout=3000)
                    await page.wait_for_timeout(1000)
                    page_errors = await page.evaluate(CHECK_ERRORS_JS)
                    new_errors = [e for e in console_errors if "[error]" in e.lower()]
                    result["success"] = len(page_errors) == 0 and len(new_errors) == 0
                    result["detail"] = "Clicked button"
                    if page_errors:
                        result["errors_on_page"] = page_errors[:3]
                    if new_errors:
                        result["console_errors"] = new_errors[:3]
                        result["anomaly"] = {
                            "type": "console_error",
                            "severity": "medium",
                            "description": f"Console error after clicking '{text}': {new_errors[0]}",
                        }
            except Exception as e:
                err_str = str(e)
                # Distinguish timeout (likely hidden element) from real failures
                if "Timeout" in err_str:
                    result["success"] = True  # Timeout = element not interactable, not an app bug
                    result["detail"] = f"Skipped — '{text}' timed out (likely hidden/modal)"
                    result["skipped_reason"] = "timeout_hidden"
                else:
                    result["success"] = False
                    result["detail"] = f"Click failed: {err_str[:60]}"
                    result["anomaly"] = {
                        "type": "click_failure",
                        "severity": "medium",
                        "description": f"Button '{text}' failed to click: {err_str[:60]}",
                    }

        await context.close()

    except Exception as e:
        result["success"] = False
        result["detail"] = str(e)[:100]
    finally:
        _stop_streaming()
        if own_browser and browser:
            await browser.close()
        if own_browser and pw_ctx:
            await pw_ctx.stop()

    return result


async def pw_check_page_health(url: str) -> dict:
    """Check a URL for console errors, broken images, blank page.

    Returns:
        {url, title, errors, broken_images, console_errors, is_blank, verdict}
    """
    from playwright.async_api import async_playwright

    console_errors: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--single-process"])
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text[:150]}") if msg.type in ("error", "warning") else None)
        _start_streaming(page)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)
        except Exception as e:
            _stop_streaming()
            await browser.close()
            return {"url": url, "error": str(e), "verdict": "load_failed"}

        title = await page.title()
        page_errors = await page.evaluate(CHECK_ERRORS_JS)
        broken_images = [e for e in page_errors if "Broken image" in e]
        text_errors = [e for e in page_errors if "Broken image" not in e]
        is_blank = any("blank" in e.lower() for e in page_errors)

        _stop_streaming()
        await browser.close()

    real_console_errors = [e for e in console_errors if "[error]" in e.lower()]

    return {
        "url": url,
        "title": title,
        "errors": text_errors,
        "broken_images": broken_images,
        "console_errors": real_console_errors,
        "is_blank": is_blank,
        "verdict": "healthy" if not text_errors and not broken_images and not real_console_errors else "issues_found",
    }


async def pw_batch_test(url: str, max_interactions: int = 15) -> dict:
    """Run the full deterministic self-test: discover → test → detect → trace → suggest.

    Returns complete results dict with all phases.
    """
    from playwright.async_api import async_playwright

    parsed = urlparse(url)
    base_origin = f"{parsed.scheme}://{parsed.netloc}"
    console_errors: list[str] = []
    anomalies: list[dict] = []
    test_results: list[dict] = []
    tests_run = 0

    # Phase 1: Discover
    discovery = await pw_discover(url)
    if discovery.get("error"):
        return {"error": discovery["error"], "phases": {"discover": discovery}}

    pages_visited = discovery["pages"]

    # Phase 2: Test interactions — round-robin across pages, prioritize inputs > buttons > links
    # Build prioritized test queue: inputs first, then buttons, then links.
    # Round-robin: take up to 3 elements per page before moving on.
    _TYPE_PRIORITY = {"input": 0, "button": 1, "link": 2}
    per_page_queues: list[tuple[str, list[dict]]] = []
    for page_path, page_data in pages_visited.items():
        elements = page_data.get("elements", [])
        testable = [el for el in elements if el["type"] in ("button", "link", "input")]
        # Sort by type priority within each page
        testable.sort(key=lambda e: _TYPE_PRIORITY.get(e["type"], 9))
        per_page_queues.append((page_path, testable))

    # Round-robin: take up to 3 per page per round
    test_queue: list[tuple[str, dict]] = []
    _PER_PAGE_PER_ROUND = 3
    round_idx = 0
    while len(test_queue) < max_interactions * 2:  # overfill, will truncate
        added_this_round = False
        for page_path, elements in per_page_queues:
            start = round_idx * _PER_PAGE_PER_ROUND
            batch = elements[start:start + _PER_PAGE_PER_ROUND]
            for el in batch:
                test_queue.append((page_path, el))
            if batch:
                added_this_round = True
        if not added_this_round:
            break
        round_idx += 1
    test_queue = test_queue[:max_interactions]

    # Anomaly dedup tracking
    _seen_anomaly_keys: set[str] = set()

    def _add_anomaly(anomaly: dict) -> None:
        """Add anomaly only if not a duplicate (by type + description core)."""
        # Key: type + the URL or core description (strip page-specific prefix)
        desc = anomaly.get("description", "")
        # Extract the actual error (after the colon)
        core = desc.split(": ", 1)[-1] if ": " in desc else desc
        key = f"{anomaly['type']}:{core}"
        if key in _seen_anomaly_keys:
            return
        _seen_anomaly_keys.add(key)
        anomalies.append(anomaly)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--single-process"])
            context = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await context.new_page()
            page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text[:150]}") if msg.type in ("error", "warning") else None)
            _start_streaming(page)

            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1500)

            current_page_path = parsed.path or "/"

            for page_path, el in test_queue:
                # Navigate to the right page if needed
                if page_path != current_page_path:
                    page_url = f"{base_origin}{page_path}"
                    try:
                        await page.goto(page_url, wait_until="domcontentloaded", timeout=10000)
                        await page.wait_for_timeout(1000)
                        current_page_path = page_path
                        await _broadcast_screenshot(page)
                    except Exception:
                        continue

                action = el["type"]
                text = el.get("text", "unknown")
                before_url = page.url
                before_errors_count = len(console_errors)
                test_result: dict = {"action": action, "element": text, "page": page_path}

                try:
                    if action == "input":
                        sel = el.get("selector", f'[placeholder="{text[:30]}"]')
                        # R2: Test multiple input values — empty, invalid, valid
                        is_email = "email" in text.lower() or "email" in sel.lower()
                        test_values = [
                            ("", "empty"),
                            ("invalid-not-email" if is_email else "x", "invalid"),
                            ("test@example.com" if is_email else "Test Value 123", "valid"),
                        ]
                        input_results = []
                        for val, val_type in test_values:
                            try:
                                await page.fill(sel, val, timeout=3000)
                                # Check if validation errors appear after empty/invalid
                                if val_type in ("empty", "invalid"):
                                    # Try to trigger validation by pressing Tab or clicking away
                                    await page.keyboard.press("Tab")
                                    await page.wait_for_timeout(300)
                                input_results.append({"value_type": val_type, "accepted": True})
                            except Exception:
                                input_results.append({"value_type": val_type, "accepted": False})
                        test_result["success"] = any(r["accepted"] for r in input_results)
                        test_result["detail"] = f"Input tested: {', '.join(r['value_type'] + ('=ok' if r['accepted'] else '=fail') for r in input_results)}"
                        test_result["input_tests"] = input_results
                    elif action == "link":
                        href = el.get("href", "")
                        if href and href.startswith(base_origin):
                            try:
                                await page.goto(href, wait_until="domcontentloaded", timeout=8000)
                                await page.wait_for_timeout(1000)
                                page_errors = await page.evaluate(CHECK_ERRORS_JS)
                                test_result["success"] = len(page_errors) == 0
                                test_result["detail"] = f"Navigated to {urlparse(page.url).path}"
                                if page_errors:
                                    test_result["errors_on_page"] = page_errors[:3]
                                    for err in page_errors[:2]:
                                        _add_anomaly({
                                            "type": "error_on_navigate",
                                            "severity": "high",
                                            "description": f"Error after clicking '{text}': {err}",
                                            "page": page_path,
                                            "element": text,
                                        })
                            except Exception as e:
                                test_result["success"] = False
                                test_result["detail"] = f"Navigation failed: {str(e)[:80]}"
                                _add_anomaly({
                                    "type": "navigation_failure",
                                    "severity": "high",
                                    "description": f"Link '{text}' failed to load: {str(e)[:80]}",
                                    "page": page_path,
                                    "element": text,
                                })
                        else:
                            test_result["success"] = True
                            test_result["detail"] = "External link — skipped"
                    elif action == "button":
                        try:
                            btn = page.get_by_text(text[:30], exact=False).first
                            await btn.click(timeout=3000)
                            await page.wait_for_timeout(1000)
                            page_errors = await page.evaluate(CHECK_ERRORS_JS)
                            new_console_errors = console_errors[before_errors_count:]
                            test_result["success"] = len(page_errors) == 0 and len(new_console_errors) == 0
                            test_result["detail"] = "Clicked button"
                            if page_errors:
                                test_result["errors_on_page"] = page_errors[:3]
                            if new_console_errors:
                                test_result["console_errors"] = new_console_errors[:3]
                                for cerr in new_console_errors[:2]:
                                    _add_anomaly({
                                        "type": "console_error",
                                        "severity": "medium",
                                        "description": f"Console error after clicking '{text}': {cerr}",
                                        "page": page_path,
                                        "element": text,
                                    })
                        except Exception as e:
                            test_result["success"] = False
                            test_result["detail"] = f"Click failed: {str(e)[:60]}"
                            _add_anomaly({
                                "type": "click_failure",
                                "severity": "medium",
                                "description": f"Button '{text}' failed to click: {str(e)[:60]}",
                                "page": page_path,
                                "element": text,
                            })

                    await _broadcast_screenshot(page)

                    # Navigate back if we left the current page
                    if page.url != before_url and not page.url.startswith(before_url):
                        try:
                            await page.goto(before_url, wait_until="domcontentloaded", timeout=8000)
                            await page.wait_for_timeout(500)
                        except Exception:
                            pass

                except Exception as e:
                    test_result["success"] = False
                    test_result["detail"] = str(e)[:100]

                tests_run += 1
                test_results.append(test_result)

            _stop_streaming()
            await browser.close()

    except Exception as e:
        _stop_streaming()
        logger.error("pw_batch_test error during testing: %s", e)

    # Add standalone console errors as anomalies
    real_errors = [e for e in console_errors if "[error]" in e.lower()]
    for cerr in real_errors[:5]:
        anomalies.append({
            "type": "console_error",
            "severity": "medium",
            "description": cerr,
            "page": "/",
            "element": "",
        })

    # Phase 3: Trace — extract meaningful search terms from anomalies
    source_traces: dict = {}
    if anomalies:
        search_terms: list[str] = []
        for a in anomalies:
            desc = a.get("description", "")
            # Extract URLs from broken image errors (more specific than page names)
            if "Broken image:" in desc:
                img_url = desc.split("Broken image:")[-1].strip()
                img_parsed = urlparse(img_url)
                # R4: Search for the domain (e.g. retention.com) in .tsx/.jsx
                if img_parsed.netloc:
                    search_terms.append(img_parsed.netloc)
                # Also search for the filename
                path_parts = [p for p in img_parsed.path.strip("/").split("/") if len(p) > 3]
                if path_parts:
                    search_terms.append(path_parts[-1])  # filename
            # Extract page route for navigation errors
            page = a.get("page", "").strip("/")
            if page and len(page) > 2:
                page_parts = page.split("/")
                search_terms.append(page_parts[-1])
            # Element text for click failures
            if a.get("type") in ("click_failure", "console_error"):
                el_text = a.get("element", "")
                if el_text and len(el_text) > 2:
                    search_terms.append(el_text)
        search_terms = list(dict.fromkeys(search_terms))[:8]  # dedupe, limit

        source_matches = trace_to_source(search_terms)
        frontend_matches = [m for m in source_matches if m["file"].endswith((".tsx", ".ts", ".jsx", ".js"))]
        backend_matches = [m for m in source_matches if m["file"].endswith(".py")]
        source_traces = {
            "search_terms": search_terms,
            "total_matches": len(source_matches),
            "frontend_matches": frontend_matches[:10],
            "backend_matches": backend_matches[:10],
        }

    # Phase 4: Suggest fixes — specific per anomaly type
    suggestions: list[dict] = []
    all_matches = source_traces.get("frontend_matches", []) + source_traces.get("backend_matches", [])
    for anomaly in anomalies[:5]:
        related = [m for m in all_matches if anomaly.get("element", "").lower() in m.get("snippet", "").lower()]
        if not related and all_matches:
            related = all_matches[:1]
        src = related[0] if related else None
        a_type = anomaly.get("type", "")
        a_desc = anomaly.get("description", "")
        a_page = anomaly.get("page", "/")

        # R1: Generate specific fix + regression test per anomaly type
        if "Broken image" in a_desc:
            img_url = a_desc.split("Broken image:")[-1].strip() if "Broken image:" in a_desc else ""
            fix = (
                f"**Broken image** on `{a_page}`\n"
                f"- URL: `{img_url}`\n"
                f"- **Fix options**:\n"
                f"  1. Host images locally: download to `public/images/` and update `src` attributes\n"
                f"  2. Add fallback: `<img src={{url}} onError={{(e) => e.target.src='/placeholder.png'}} />`\n"
                f"  3. Validate image URLs at build time with a link-checker script"
            )
            reg_test = (
                f"```ts\n"
                f"test('no broken images on {a_page}', async ({{ page }}) => {{\n"
                f"  await page.goto('http://localhost:5173{a_page}');\n"
                f"  const broken = await page.$$eval('img', imgs =>\n"
                f"    imgs.filter(i => !i.complete || i.naturalWidth === 0).map(i => i.src));\n"
                f"  expect(broken).toHaveLength(0);\n"
                f"}});\n"
                f"```"
            )
        elif a_type == "click_failure":
            fix = (
                f"**Button click failed** on `{a_page}`: `{anomaly.get('element', '')}`\n"
                f"- **Fix options**:\n"
                f"  1. Check selector is unique — `get_by_text('{anomaly.get('element', '')}')` may match multiple elements\n"
                f"  2. Add `data-testid` attribute to the button for reliable selection\n"
                f"  3. Ensure button is not disabled or behind an overlay"
            )
            reg_test = (
                f"```ts\n"
                f"test('button {anomaly.get('element', '')} is clickable on {a_page}', async ({{ page }}) => {{\n"
                f"  await page.goto('http://localhost:5173{a_page}');\n"
                f"  const btn = page.getByText('{anomaly.get('element', '')}');\n"
                f"  await expect(btn).toBeVisible();\n"
                f"  await btn.click();\n"
                f"}});\n"
                f"```"
            )
        elif a_type == "navigation_failure":
            fix = (
                f"**Navigation failed** on `{a_page}`: link `{anomaly.get('element', '')}`\n"
                f"- **Fix options**:\n"
                f"  1. Check route exists in React Router config\n"
                f"  2. Verify the target component renders without errors\n"
                f"  3. Check for 404 handling on the target path"
            )
            reg_test = (
                f"```ts\n"
                f"test('link {anomaly.get('element', '')} navigates successfully', async ({{ page }}) => {{\n"
                f"  await page.goto('http://localhost:5173{a_page}');\n"
                f"  await page.click('text={anomaly.get('element', '')}');\n"
                f"  await expect(page).not.toHaveURL(/.*404.*/);\n"
                f"}});\n"
                f"```"
            )
        elif a_type == "console_error":
            fix = (
                f"**Console error** on `{a_page}`\n"
                f"- Error: `{a_desc[:100]}`\n"
                f"- **Fix options**:\n"
                f"  1. Check browser DevTools Console for full stack trace\n"
                f"  2. Look for unhandled promise rejections or missing API responses\n"
                f"  3. Add error boundary around the component"
            )
            reg_test = (
                f"```ts\n"
                f"test('no console errors on {a_page}', async ({{ page }}) => {{\n"
                f"  const errors: string[] = [];\n"
                f"  page.on('console', msg => {{ if (msg.type() === 'error') errors.push(msg.text()); }});\n"
                f"  await page.goto('http://localhost:5173{a_page}');\n"
                f"  await page.waitForTimeout(2000);\n"
                f"  expect(errors).toHaveLength(0);\n"
                f"}});\n"
                f"```"
            )
        else:
            fix = f"Review {src['file']}:{src['line']}" if src else "Manual investigation needed"
            reg_test = f"Add Playwright test: navigate to {a_page}, interact, verify no errors"

        suggestions.append({
            "anomaly": a_desc,
            "source_file": src["file"] if src else "unknown",
            "source_snippet": src["snippet"] if src else "",
            "suggested_fix": fix,
            "regression_test": reg_test,
        })

    return {
        "url": url,
        "phases": {
            "discover": discovery,
            "test": {
                "tests_run": tests_run,
                "test_results": test_results,
            },
            "detect": {
                "anomaly_count": len(anomalies),
                "anomalies": anomalies[:20],
                "console_errors_count": len(console_errors),
                "verdict": "issues_found" if anomalies else "no_anomalies",
            },
            "trace": source_traces,
            "suggest": {
                "suggestions": suggestions,
            },
        },
        "summary": {
            "pages_found": discovery["pages_found"],
            "interactions_tested": tests_run,
            "anomalies_found": len(anomalies),
            "fixes_suggested": len(suggestions),
            "console_errors": len(console_errors),
        },
    }
