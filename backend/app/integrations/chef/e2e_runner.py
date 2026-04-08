"""
Chef E2E Test Runner

Runs Playwright-based smoke tests against deployed Chef-generated apps.
Collects screenshots, console errors, and structural checks.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class E2ETestResult:
    """Result from running E2E smoke tests against a deployed app."""

    url: str
    passed: bool
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    console_errors: List[str] = field(default_factory=list)
    screenshots: Dict[str, str] = field(default_factory=dict)  # name -> base64
    checks: List[Dict] = field(default_factory=list)  # [{name, passed, detail}]
    duration_ms: int = 0
    error: Optional[str] = None


class ChefE2ERunner:
    """Run Playwright smoke tests against deployed Chef apps.

    Smoke test suite:
    1. Page loads (HTTP 200, no crash)
    2. No critical console errors
    3. Key structural elements present (root div, headings, interactive elements)
    4. Responsive check (mobile viewport)
    5. Screenshot capture for visual evidence
    """

    def __init__(self, timeout_seconds: int = 60) -> None:
        self.timeout_seconds = timeout_seconds

    async def run_smoke_tests(self, url: str) -> E2ETestResult:
        """Run smoke tests against a deployed app URL.

        Args:
            url: The deployed app URL to test.

        Returns:
            E2ETestResult with pass/fail, checks, screenshots, console errors.
        """
        import time

        start = time.monotonic()
        result = E2ETestResult(url=url, passed=False)

        try:
            checks = await self._execute_playwright_tests(url, result)
            result.checks = checks
            result.total_checks = len(checks)
            result.passed_checks = sum(1 for c in checks if c["passed"])
            result.failed_checks = result.total_checks - result.passed_checks
            result.passed = result.failed_checks == 0
        except Exception as exc:
            logger.exception("E2E test execution failed for %s: %s", url, exc)
            result.error = str(exc)
            result.passed = False

        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    async def _execute_playwright_tests(
        self, url: str, result: E2ETestResult
    ) -> List[Dict]:
        """Execute Playwright tests via subprocess.

        Uses npx playwright to avoid requiring playwright as a Python dependency.
        Falls back to HTTP-based checks if Playwright is unavailable.
        """
        checks: List[Dict] = []

        # Try Playwright via subprocess first
        try:
            checks = await self._run_playwright_subprocess(url, result)
        except FileNotFoundError:
            logger.warning("Playwright not available, falling back to HTTP checks")
            checks = await self._run_http_checks(url, result)

        return checks

    async def _run_playwright_subprocess(
        self, url: str, result: E2ETestResult
    ) -> List[Dict]:
        """Run Playwright checks via Node.js subprocess."""
        # Build inline Playwright script
        script = self._build_playwright_script(url)

        proc = await asyncio.create_subprocess_exec(
            "node", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=self.timeout_seconds,
        )

        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")

        return self._parse_playwright_output(stdout, stderr, result)

    def _build_playwright_script(self, url: str) -> str:
        """Build Node.js Playwright smoke test script."""
        return f"""
const {{ chromium }} = require('playwright');
(async () => {{
  const results = {{ checks: [], consoleErrors: [], screenshots: {{}} }};
  let browser;
  try {{
    browser = await chromium.launch({{ headless: true }});
    const page = await browser.newPage();

    // Collect console errors
    page.on('console', msg => {{
      if (msg.type() === 'error') results.consoleErrors.push(msg.text());
    }});

    // Check 1: Page loads
    const response = await page.goto('{url}', {{ waitUntil: 'networkidle', timeout: 30000 }});
    const status = response ? response.status() : 0;
    results.checks.push({{
      name: 'page_loads', passed: status >= 200 && status < 400,
      detail: `HTTP ${{status}}`
    }});

    // Check 2: No crash (body has content)
    const bodyText = await page.textContent('body').catch(() => '');
    results.checks.push({{
      name: 'has_content', passed: bodyText.length > 10,
      detail: `Body length: ${{bodyText.length}}`
    }});

    // Check 3: Has interactive elements
    const buttons = await page.$$('button, [role="button"], a[href]');
    results.checks.push({{
      name: 'has_interactive_elements', passed: buttons.length > 0,
      detail: `Found ${{buttons.length}} interactive elements`
    }});

    // Check 4: No critical console errors (filter noise)
    const criticalErrors = results.consoleErrors.filter(e =>
      !e.includes('favicon') && !e.includes('DevTools') && !e.includes('React DevTools')
    );
    results.checks.push({{
      name: 'no_critical_console_errors', passed: criticalErrors.length === 0,
      detail: criticalErrors.length > 0 ? criticalErrors.slice(0, 3).join('; ') : 'Clean'
    }});

    // Check 5: Responsive — mobile viewport
    await page.setViewportSize({{ width: 375, height: 812 }});
    await page.waitForTimeout(500);
    const mobileBody = await page.textContent('body').catch(() => '');
    results.checks.push({{
      name: 'responsive_mobile', passed: mobileBody.length > 10,
      detail: `Mobile body length: ${{mobileBody.length}}`
    }});

    // Screenshot
    const screenshot = await page.screenshot({{ fullPage: true }});
    results.screenshots.desktop = screenshot.toString('base64');

  }} catch (err) {{
    results.checks.push({{ name: 'execution', passed: false, detail: err.message }});
  }} finally {{
    if (browser) await browser.close();
  }}
  console.log(JSON.stringify(results));
}})();
"""

    def _parse_playwright_output(
        self, stdout: str, stderr: str, result: E2ETestResult
    ) -> List[Dict]:
        """Parse JSON output from Playwright subprocess."""
        import json

        checks: List[Dict] = []
        for line in stdout.strip().splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    checks = data.get("checks", [])
                    result.console_errors = data.get("consoleErrors", [])
                    result.screenshots = data.get("screenshots", {})
                    return checks
                except (json.JSONDecodeError, KeyError):
                    continue

        # If no JSON parsed, treat as failure
        if not checks:
            checks.append({
                "name": "playwright_parse",
                "passed": False,
                "detail": f"Could not parse output. stderr: {stderr[:200]}",
            })
        return checks

    async def _run_http_checks(
        self, url: str, result: E2ETestResult
    ) -> List[Dict]:
        """Fallback: basic HTTP checks when Playwright is unavailable."""
        import urllib.request
        import urllib.error

        checks: List[Dict] = []

        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", "ChefE2ERunner/1.0")
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = resp.status
                body = resp.read().decode(errors="replace")

            checks.append({
                "name": "page_loads",
                "passed": 200 <= status < 400,
                "detail": f"HTTP {status}",
            })
            checks.append({
                "name": "has_content",
                "passed": len(body) > 100,
                "detail": f"Body length: {len(body)}",
            })
            checks.append({
                "name": "has_html_structure",
                "passed": "<html" in body.lower() and "<body" in body.lower(),
                "detail": "HTML structure present" if "<html" in body.lower() else "Missing HTML",
            })
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            checks.append({
                "name": "page_loads",
                "passed": False,
                "detail": str(exc),
            })

        return checks

