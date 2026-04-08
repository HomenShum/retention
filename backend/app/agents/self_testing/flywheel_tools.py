"""
Self-Testing Flywheel Tools — @function_tool wrappers around playwright_engine.

7 tools available to the Self-Test Specialist agent and the Coordinator.
All use Playwright directly — no emulator or AI API key needed.
"""

import json
import logging
from pathlib import Path
from typing import Any

from agents import function_tool

from .playwright_engine import (
    pw_discover,
    pw_test_interaction,
    pw_check_page_health,
    pw_batch_test,
    trace_to_source as _trace_to_source,
)

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[4]


# ── Tool 1: discover_app_screens ──────────────────────────────

@function_tool
async def discover_app_screens(url: str, crawl_depth: int = 1) -> str:
    """Discover testable screens and interactive elements in a web app by crawling it with Playwright.

    Args:
        url: The URL to start crawling from (e.g. http://localhost:5173)
        crawl_depth: How many link levels deep to follow (default 1)

    Returns:
        JSON with pages visited, element counts, and a suggested test plan
    """
    try:
        result = await pw_discover(url, crawl_depth=crawl_depth)
        return json.dumps(result, default=str)
    except Exception as e:
        logger.error("discover_app_screens failed: %s", e)
        return json.dumps({"error": str(e)})


# ── Tool 2: test_interaction ──────────────────────────────────

@function_tool
async def test_interaction(
    url: str,
    element_type: str,
    element_text: str,
    page_path: str = "/",
    element_selector: str = "",
    element_href: str = "",
) -> str:
    """Test a single interactive element on a web page using Playwright.

    Args:
        url: The page URL to navigate to before testing
        element_type: Type of element: 'link', 'button', or 'input'
        element_text: Visible text of the element to interact with
        page_path: The page path where the element lives (default '/')
        element_selector: CSS selector for the element (optional)
        element_href: Link href for link elements (optional)

    Returns:
        JSON with action performed, success/failure, errors found, and any anomaly detected
    """
    element = {
        "type": element_type,
        "text": element_text,
        "_page": page_path,
    }
    if element_selector:
        element["selector"] = element_selector
    if element_href:
        element["href"] = element_href

    try:
        result = await pw_test_interaction(url, element)
        return json.dumps(result, default=str)
    except Exception as e:
        logger.error("test_interaction failed: %s", e)
        return json.dumps({"error": str(e), "success": False})


# ── Tool 3: check_page_health ─────────────────────────────────

@function_tool
async def check_page_health(url: str) -> str:
    """Check a web page for console errors, broken images, blank screens, and other health issues.

    Args:
        url: The URL to health-check

    Returns:
        JSON with errors, broken images, console errors, and a health verdict
    """
    try:
        result = await pw_check_page_health(url)
        return json.dumps(result, default=str)
    except Exception as e:
        logger.error("check_page_health failed: %s", e)
        return json.dumps({"error": str(e), "verdict": "check_failed"})


# ── Tool 4: detect_anomalies ─────────────────────────────────

@function_tool
async def detect_anomalies(
    action_performed: str,
    success: bool = True,
    errors_on_page: str = "[]",
    console_errors: str = "[]",
    expected_behavior: str = "",
) -> str:
    """Analyze test results to detect UI anomalies or unexpected behavior.

    Args:
        action_performed: Description of the action that was executed
        success: Whether the action succeeded
        errors_on_page: JSON array of error strings found on the page
        console_errors: JSON array of console error strings
        expected_behavior: What should have happened (optional)

    Returns:
        JSON with anomaly analysis and severity
    """
    try:
        page_errors = json.loads(errors_on_page) if isinstance(errors_on_page, str) else errors_on_page
        c_errors = json.loads(console_errors) if isinstance(console_errors, str) else console_errors
    except json.JSONDecodeError:
        page_errors, c_errors = [], []

    anomalies = []

    if not success:
        anomalies.append({
            "type": "action_failed",
            "severity": "high",
            "description": f"Action '{action_performed}' failed",
        })

    error_keywords = ["error", "failed", "exception", "crash", "404", "500", "not found", "undefined"]
    for err in page_errors:
        if any(kw in str(err).lower() for kw in error_keywords):
            anomalies.append({
                "type": "error_displayed",
                "severity": "high",
                "description": f"Error on page: '{err}'",
            })

    for cerr in c_errors:
        anomalies.append({
            "type": "console_error",
            "severity": "medium",
            "description": f"Console: {cerr}",
        })

    return json.dumps({
        "action": action_performed,
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
        "verdict": "issues_found" if anomalies else "no_anomalies",
    })


# ── Tool 5: trace_to_source ──────────────────────────────────

@function_tool
async def trace_to_source(
    anomaly_description: str,
    page_url: str = "",
    element_text: str = "",
) -> str:
    """Trace a detected anomaly back to source code using git grep.

    Args:
        anomaly_description: Description of the anomaly to trace
        page_url: The page URL where the anomaly occurred
        element_text: Text of the UI element involved (if any)

    Returns:
        JSON with matching source files, line numbers, and relevant snippets
    """
    from urllib.parse import urlparse

    search_terms = []
    if page_url:
        parsed = urlparse(page_url)
        path_parts = [p for p in parsed.path.strip("/").split("/") if p and len(p) > 2]
        search_terms.extend(path_parts)
    if element_text:
        search_terms.append(element_text)
    keywords = [w for w in anomaly_description.split() if len(w) > 3 and w.isalpha()]
    search_terms.extend(keywords[:3])
    search_terms = list(dict.fromkeys(search_terms))[:5]

    matches = _trace_to_source(search_terms)
    frontend_matches = [m for m in matches if m["file"].endswith((".tsx", ".ts", ".jsx", ".js"))]
    backend_matches = [m for m in matches if m["file"].endswith(".py")]

    return json.dumps({
        "search_terms": search_terms,
        "total_matches": len(matches),
        "frontend_matches": frontend_matches[:10],
        "backend_matches": backend_matches[:10],
    })


# ── Tool 6: suggest_fix_and_test ──────────────────────────────

@function_tool
async def suggest_fix_and_test(
    anomaly_description: str,
    source_file: str = "",
    source_snippet: str = "",
    page_url: str = "",
) -> str:
    """Given an anomaly and its traced source, suggest a code fix and regression test.

    Args:
        anomaly_description: What went wrong
        source_file: The source file containing the likely bug
        source_snippet: Relevant code snippet from the source
        page_url: The page URL where the issue was found

    Returns:
        JSON with suggested_fix and regression_test
    """
    full_context = ""
    if source_file:
        fpath = _REPO_ROOT / source_file
        if fpath.exists() and fpath.is_file():
            try:
                content = fpath.read_text(errors="replace")
                lines = content.splitlines()
                full_context = f"Full file has {len(lines)} lines.\n"
                if source_snippet:
                    for i, line in enumerate(lines):
                        if source_snippet[:40] in line:
                            start = max(0, i - 5)
                            end = min(len(lines), i + 15)
                            full_context += f"Context (lines {start+1}-{end}):\n"
                            full_context += "\n".join(f"{j+1}: {lines[j]}" for j in range(start, end))
                            break
            except Exception:
                pass

    return json.dumps({
        "anomaly": anomaly_description,
        "source_file": source_file,
        "file_context": full_context[:2000],
        "suggested_fix": (
            f"**Issue**: {anomaly_description}\n"
            f"**File**: `{source_file}`\n\n"
            f"Review the code around the snippet and apply a fix."
        ),
        "regression_test": (
            f"Add Playwright test: navigate to {page_url}, "
            f"perform the action that triggered the bug, "
            f"assert no errors appear."
        ),
    }, default=str)


# ── Tool 7: batch_test (fast deterministic path) ─────────────

@function_tool
async def batch_test(url: str, max_interactions: int = 15) -> str:
    """Run the full deterministic self-test on a URL: discover → test → detect → trace → suggest.

    This is the fast path — no AI reasoning, fully deterministic.
    Use this when you want quick, comprehensive results.

    Args:
        url: The URL to test (e.g. http://localhost:5173)
        max_interactions: Maximum number of interactions to test (default 15)

    Returns:
        JSON with complete test results including pages, tests, anomalies, traces, and fix suggestions
    """
    try:
        result = await pw_batch_test(url, max_interactions=max_interactions)
        return json.dumps(result, default=str)
    except Exception as e:
        logger.error("batch_test failed: %s", e)
        return json.dumps({"error": str(e)})


# ── Factory ───────────────────────────────────────────────────

def create_flywheel_tools() -> dict:
    """Create all flywheel tools. No external dependencies needed.

    Returns:
        dict of tool_name → function_tool
    """
    return {
        "discover_app_screens": discover_app_screens,
        "test_interaction": test_interaction,
        "check_page_health": check_page_health,
        "detect_anomalies": detect_anomalies,
        "trace_to_source": trace_to_source,
        "suggest_fix_and_test": suggest_fix_and_test,
        "batch_test": batch_test,
    }
