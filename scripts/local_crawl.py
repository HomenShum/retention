"""Local crawl engine for retention.sh MCP proxy.

Runs Playwright on the USER'S machine — no cloud dependency.
Falls back to HTTP-only analysis if Playwright is not installed.

Usage from proxy.py:
    from local_crawl import local_qa_check, local_crawl_url, has_playwright
"""

import json
import os
import re
import time
import urllib.request
import urllib.error
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Playwright detection
# ---------------------------------------------------------------------------

_playwright_available = None

def has_playwright() -> bool:
    """Check if playwright is installed and browsers are available."""
    global _playwright_available
    if _playwright_available is not None:
        return _playwright_available
    try:
        from playwright.sync_api import sync_playwright
        # Quick check that chromium is installed
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        _playwright_available = True
    except Exception:
        _playwright_available = False
    return _playwright_available


# ---------------------------------------------------------------------------
# HTTP-only QA check (no Playwright needed)
# ---------------------------------------------------------------------------

class _HTMLAnalyzer(HTMLParser):
    """Lightweight HTML analyzer for QA findings."""

    def __init__(self):
        super().__init__()
        self.findings: List[Dict] = []
        self.links: List[str] = []
        self.images_without_alt: int = 0
        self.inputs_without_label: int = 0
        self.buttons_without_label: int = 0
        self.has_viewport_meta = False
        self.has_description_meta = False
        self.has_lang = False
        self.title = ""
        self._in_title = False
        self._label_for_ids: set = set()
        self._input_ids: set = set()

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)

        if tag == "html" and attr_dict.get("lang"):
            self.has_lang = True

        if tag == "meta":
            name = attr_dict.get("name", "").lower()
            if name == "viewport":
                self.has_viewport_meta = True
            if name == "description":
                self.has_description_meta = True

        if tag == "title":
            self._in_title = True

        if tag == "a":
            href = attr_dict.get("href", "")
            if href and not href.startswith("#") and not href.startswith("javascript:"):
                self.links.append(href)

        if tag == "img" and not attr_dict.get("alt"):
            self.images_without_alt += 1

        if tag in ("input", "textarea", "select"):
            input_id = attr_dict.get("id", "")
            if input_id:
                self._input_ids.add(input_id)
            input_type = attr_dict.get("type", "text")
            if input_type not in ("hidden", "submit", "button", "reset"):
                if not attr_dict.get("aria-label") and not attr_dict.get("aria-labelledby"):
                    self.inputs_without_label += 1

        if tag == "label":
            for_id = attr_dict.get("for", "")
            if for_id:
                self._label_for_ids.add(for_id)

        if tag == "button":
            if not attr_dict.get("aria-label") and not attr_dict.get("title"):
                self.buttons_without_label += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data.strip()


def local_qa_check(url: str) -> Dict[str, Any]:
    """HTTP-only QA check — no browser needed.

    Fetches the URL, parses HTML, checks for common issues:
    - Missing meta tags (viewport, description, lang)
    - Images without alt text
    - Inputs without labels
    - Broken links (HTTP check on each)
    - Basic a11y issues
    """
    start = time.time()
    findings = []

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "retention.sh/1.0 QA Checker"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return {
            "status": "error",
            "url": url,
            "error": f"HTTP {e.code}",
            "duration_ms": int((time.time() - start) * 1000),
        }
    except Exception as e:
        return {
            "status": "error",
            "url": url,
            "error": str(e),
            "duration_ms": int((time.time() - start) * 1000),
        }

    # Parse HTML
    analyzer = _HTMLAnalyzer()
    try:
        analyzer.feed(html)
    except Exception:
        pass

    # Generate findings
    if not analyzer.has_lang:
        findings.append({
            "severity": "warning",
            "category": "a11y",
            "title": "Missing lang attribute",
            "detail": "<html> element has no lang attribute. Screen readers need this.",
        })

    if not analyzer.has_viewport_meta:
        findings.append({
            "severity": "warning",
            "category": "responsive",
            "title": "Missing viewport meta tag",
            "detail": "No <meta name='viewport'> found. Page may not be mobile-friendly.",
        })

    if not analyzer.has_description_meta:
        findings.append({
            "severity": "info",
            "category": "seo",
            "title": "Missing meta description",
            "detail": "No <meta name='description'> found. This hurts SEO.",
        })

    if analyzer.images_without_alt > 0:
        findings.append({
            "severity": "warning",
            "category": "a11y",
            "title": f"{analyzer.images_without_alt} image(s) missing alt text",
            "detail": "Images should have descriptive alt attributes for screen readers.",
        })

    if analyzer.inputs_without_label > 0:
        findings.append({
            "severity": "warning",
            "category": "a11y",
            "title": f"{analyzer.inputs_without_label} input(s) missing labels",
            "detail": "Form inputs should have associated <label> elements or aria-label.",
        })

    if analyzer.buttons_without_label > 0:
        findings.append({
            "severity": "warning",
            "category": "a11y",
            "title": f"{analyzer.buttons_without_label} button(s) missing labels",
            "detail": "Buttons (especially icon buttons) should have aria-label or title.",
        })

    # Check a sample of links for broken ones
    broken_links = []
    checked = 0
    for link in analyzer.links[:20]:  # Check up to 20 links
        if not link.startswith("http"):
            # Resolve relative URL
            from urllib.parse import urljoin
            link = urljoin(url, link)
        try:
            link_req = urllib.request.Request(link, method="HEAD", headers={
                "User-Agent": "retention.sh/1.0 Link Checker"
            })
            with urllib.request.urlopen(link_req, timeout=5) as resp:
                if resp.status >= 400:
                    broken_links.append({"url": link, "status": resp.status})
            checked += 1
        except urllib.error.HTTPError as e:
            if e.code >= 400:
                broken_links.append({"url": link, "status": e.code})
            checked += 1
        except Exception:
            checked += 1

    if broken_links:
        findings.append({
            "severity": "error",
            "category": "links",
            "title": f"{len(broken_links)} broken link(s) found",
            "detail": ", ".join(f"{b['url']} ({b['status']})" for b in broken_links[:5]),
        })

    # Check page size
    page_size_kb = len(html) / 1024
    if page_size_kb > 500:
        findings.append({
            "severity": "warning",
            "category": "performance",
            "title": f"Large page size ({page_size_kb:.0f}KB)",
            "detail": "Page HTML is over 500KB. Consider lazy loading or code splitting.",
        })

    duration_ms = int((time.time() - start) * 1000)

    return {
        "status": "ok",
        "url": url,
        "title": analyzer.title or "(no title)",
        "mode": "http_only",
        "findings": findings,
        "findings_count": len(findings),
        "links_found": len(analyzer.links),
        "links_checked": checked,
        "broken_links": len(broken_links),
        "duration_ms": duration_ms,
        "verdict": "pass" if not any(f["severity"] == "error" for f in findings) else "fail",
        "note": "HTTP-only scan. Install Playwright for full browser rendering: pip install playwright && playwright install chromium",
    }


# ---------------------------------------------------------------------------
# Playwright-powered crawl (optional)
# ---------------------------------------------------------------------------

def local_crawl_url(url: str, max_pages: int = 10, mode: str = "auto") -> Dict[str, Any]:
    """Full Playwright crawl — runs on user's machine.

    Modes:
      - "auto": accessibility tree + lightweight screenshot (default, cheapest)
      - "accessibility": accessibility tree only, NO screenshots (cheapest, no vision model)
      - "screenshot": full screenshots + element counts (needs vision model)
      - "full": both accessibility tree + screenshots (most data, most expensive)

    Returns screens with findings, console errors.
    Falls back to HTTP-only if Playwright is not available.
    """
    if not has_playwright():
        result = local_qa_check(url)
        result["fallback"] = True
        result["note"] = (
            "Playwright not installed — using HTTP-only analysis. "
            "For full browser crawl with screenshots: "
            "pip install playwright && playwright install chromium"
        )
        return result

    from playwright.sync_api import sync_playwright
    import base64

    start = time.time()
    screens = []
    console_errors = []
    findings = []
    visited = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="retention.sh/1.0 Crawler (Playwright)",
        )
        page = context.new_page()

        # Capture console errors
        page.on("console", lambda msg: console_errors.append(msg.text[:200]) if msg.type == "error" else None)
        page.on("pageerror", lambda err: console_errors.append(str(err)[:200]))

        def capture_page(page_url: str, depth: int, name: str = ""):
            if page_url.rstrip("/") in visited or len(visited) >= max_pages:
                return
            visited.add(page_url.rstrip("/"))

            try:
                if page.url.rstrip("/") != page_url.rstrip("/"):
                    page.goto(page_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(3000)

                title = page.title()

                # Accessibility tree (cheap, structured, no vision model needed)
                a11y_tree = None
                if mode in ("auto", "accessibility", "full"):
                    try:
                        a11y_tree = page.accessibility.snapshot()
                    except Exception:
                        pass  # Some pages may not support this

                # Screenshot (only if mode requires it)
                b64 = None
                if mode in ("auto", "screenshot", "full"):
                    try:
                        screenshot = page.screenshot(type="jpeg", quality=40)
                        b64 = base64.b64encode(screenshot).decode("ascii")
                    except Exception:
                        pass

                # Extract structured element data (always — it's cheap)
                page_data = page.evaluate("""() => {
                    const interactive = document.querySelectorAll('a, button, input, select, textarea, [onclick], [role="button"]');
                    const links = Array.from(document.querySelectorAll('a[href]'))
                        .map(a => a.href)
                        .filter(h => h && !h.startsWith('javascript:'));
                    const imgs_no_alt = document.querySelectorAll('img:not([alt])').length;
                    const inputs_no_label = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([aria-label]):not([aria-labelledby])'))
                        .filter(el => !document.querySelector('label[for="' + el.id + '"]')).length;
                    const buttons_no_label = Array.from(document.querySelectorAll('button:not([aria-label]):not([title])'))
                        .filter(el => !el.textContent.trim()).length;
                    return {
                        interactive_count: interactive.length,
                        links: links,
                        imgs_no_alt: imgs_no_alt,
                        inputs_no_label: inputs_no_label,
                        buttons_no_label: buttons_no_label,
                        has_h1: !!document.querySelector('h1'),
                        has_main: !!document.querySelector('main'),
                        has_nav: !!document.querySelector('nav'),
                    };
                }""")

                # A11y findings from DOM analysis
                if page_data.get("imgs_no_alt", 0) > 0:
                    findings.append({"severity": "warning", "category": "a11y",
                        "title": f"{page_data['imgs_no_alt']} image(s) missing alt text on {title}",
                        "detail": "Images should have alt attributes for screen readers."})
                if page_data.get("inputs_no_label", 0) > 0:
                    findings.append({"severity": "warning", "category": "a11y",
                        "title": f"{page_data['inputs_no_label']} input(s) without labels on {title}",
                        "detail": "Form inputs need <label> or aria-label."})
                if page_data.get("buttons_no_label", 0) > 0:
                    findings.append({"severity": "warning", "category": "a11y",
                        "title": f"{page_data['buttons_no_label']} button(s) without labels on {title}",
                        "detail": "Icon buttons need aria-label or title."})

                screen_data = {
                    "screen_id": f"screen_{len(screens)}",
                    "screen_name": name or title or page_url.split("/")[-1] or "home",
                    "url": page_url,
                    "navigation_depth": depth,
                    "interactive_elements": page_data.get("interactive_count", 0),
                    "outgoing_links": len(page_data.get("links", [])),
                }
                if b64:
                    screen_data["screenshot_b64"] = b64
                if a11y_tree:
                    screen_data["accessibility_tree"] = a11y_tree

                screens.append(screen_data)
                links_on_page = page_data.get("links", [])

                # Follow links on same domain
                try:
                    base_domain = re.match(r"https?://[^/]+", url).group()
                    for link in links_on_page:
                        if link.startswith(base_domain) and len(visited) < max_pages:
                            capture_page(link, depth + 1)
                except Exception:
                    pass

            except Exception as e:
                findings.append({
                    "severity": "error",
                    "category": "crawl",
                    "title": f"Failed to crawl {page_url}",
                    "detail": str(e)[:200],
                })

        # Start crawl
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(5000)
        except Exception as e:
            browser.close()
            return {
                "status": "error",
                "url": url,
                "error": f"Could not load page: {str(e)[:200]}",
                "duration_ms": int((time.time() - start) * 1000),
            }

        capture_page(url, 0)
        browser.close()

    # Add console error findings
    if console_errors:
        findings.append({
            "severity": "error",
            "category": "javascript",
            "title": f"{len(console_errors)} console error(s)",
            "detail": "; ".join(console_errors[:5]),
        })

    duration_ms = int((time.time() - start) * 1000)

    return {
        "status": "ok",
        "url": url,
        "mode": f"playwright_local_{mode}",
        "screens": screens,
        "total_screens": len(screens),
        "findings": findings,
        "findings_count": len(findings),
        "console_errors": console_errors,
        "duration_ms": duration_ms,
        "verdict": "pass" if not any(f["severity"] == "error" for f in findings) else "needs_attention",
    }


# ---------------------------------------------------------------------------
# Cloud push (best-effort)
# ---------------------------------------------------------------------------

CONVEX_URL = os.environ.get("TA_CONVEX_URL", "https://exuberant-ferret-263.convex.site")

def push_results_to_cloud(results: Dict, token: str) -> bool:
    """Push crawl results to Convex dashboard. Best-effort, never blocks."""
    try:
        # Strip screenshots to reduce payload size
        clean_results = dict(results)
        if "screens" in clean_results:
            clean_results["screens_count"] = len(clean_results["screens"])
            del clean_results["screens"]  # Screenshots too large for Convex

        data = json.dumps({
            "token": token,
            "url": results.get("url", ""),
            "results": clean_results,
            "email": "",  # Token identifies the user
        }).encode()

        req = urllib.request.Request(
            CONVEX_URL + "/api/crawls/save",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False  # Best-effort
