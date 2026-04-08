# Local-First Architecture: Kill the Render Dependency

## The Problem

Current architecture routes crawls through Render's free tier:
- 512MB RAM, CPU-limited
- 30-60s cold starts
- Can't handle real sites (Convex, Firebase, heavy SPAs)
- Single point of failure for the entire demo

## The Fix: Local Execution, Cloud Persistence

```
BEFORE:
  Claude Code → MCP proxy → HTTP → Render (Playwright) → results
                                      ↑ slow, unreliable

AFTER:
  Claude Code → MCP proxy → LOCAL Playwright → results
                    ↓
              Push to Convex → cloud dashboard shows their data
```

The proxy.py already runs on the user's machine. It already has a split:
- Client tools (ta.expose_local_app, ta.list_relays) run locally
- Server tools forward to Render

We extend this: crawl/QA tools run locally using the user's own Playwright.

## What Runs Locally (in proxy.py)

### Tier 1: Zero-dependency (stdlib only)
- `ta.qa_check(url)` — HTTP fetch + HTML parse for basic findings
- `ta.system_check` — verify env, check versions, report status

### Tier 2: Playwright (optional dependency)
- `ta.crawl.url(url, max_pages)` — full Playwright crawl with screenshots
- `ta.sitemap(action, url)` — interactive site map
- `ta.diff_crawl(url)` — before/after comparison

### Tier 3: Mobile (optional dependency)
- `ta.mobile.screenshot` — capture from local emulator/simulator
- `ta.mobile.crawl` — explore app via ADB/Appium locally

## What Stays on the Cloud

- **Convex**: trajectory storage, team data, user profiles, crawl results
- **Dashboard**: visualization, comparisons, team views, benchmarks
- **Render (optional)**: only for users without local Playwright (web demo)

## The Convex Push Pattern

After a local crawl, proxy.py pushes results to Convex:

```python
def _push_results_to_cloud(results, token):
    """Push crawl results to Convex for dashboard visibility."""
    try:
        data = json.dumps({
            "token": token,
            "url": results["url"],
            "screens": len(results.get("screens", [])),
            "findings": results.get("findings", []),
            "metrics": {
                "duration_ms": results.get("duration_ms"),
                "pages_crawled": results.get("total_screens"),
            }
        }).encode()
        req = urllib.request.Request(
            CONVEX_URL + "/api/crawls/save",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # Cloud push is best-effort, never blocks local results
```

## Install Flow Changes

Current:
```bash
curl -sL retention.sh/install.sh | bash
# Downloads proxy.py, generates token, writes config
```

New (additive):
```bash
curl -sL retention.sh/install.sh | bash
# Same as before, plus:
# - Checks if playwright is installed
# - If not: "pip install playwright && playwright install chromium" (optional)
# - If yes: enables local crawl tools automatically
```

Playwright install is OPTIONAL. Without it:
- ta.qa_check still works (HTTP-only, no browser needed)
- ta.crawl.url falls back to Render backend
- ta.sitemap falls back to Render backend

With it:
- Everything runs locally, zero cloud dependency
- Full browser rendering, JavaScript execution
- No timeouts, no cold starts
- Results pushed to cloud dashboard automatically

## Jordan Cutler's Workflow (Mobile)

His pattern: Claude Code drives browser/simulator → finds bugs → writes tests.

With local-first TA:
1. Claude Code calls `ta.crawl.url(url='http://localhost:3000')`
2. Proxy runs LOCAL Playwright → captures screens, finds bugs
3. Results pushed to Convex dashboard
4. Claude Code calls `ta.suggest_tests` → generates E2E tests from findings
5. User fixes bugs, calls `ta.diff_crawl` → local replay, 60-70% cheaper
6. Trajectory saved → next time even cheaper

For mobile:
1. User runs their app in simulator/emulator
2. Claude Code calls `ta.mobile.screenshot` → captures via ADB/xcrun
3. Claude Code calls `ta.mobile.crawl` → explores app locally
4. Same flow: findings → fixes → rerun → trajectory saved

## Why This Is The Right Architecture

1. **No vendor lock-in**: works without Render, without any cloud server
2. **Privacy**: user's code never leaves their machine for crawling
3. **Performance**: user's 16GB+ RAM vs Render's 512MB
4. **Reliability**: no cold starts, no timeouts, no network dependency
5. **Cost**: $0 infrastructure for crawling
6. **Mobile-ready**: same proxy can drive local emulators
7. **Cloud is additive**: dashboard/team features are the paid upgrade

## What Render Becomes

Render drops from "critical path" to "optional enhancement":
- Powers the web demo for visitors who don't have local Playwright
- Serves the /mcp/setup endpoints (could move to Vercel)
- Handles team operations that need a server

Eventually, Render can be eliminated entirely:
- Web demo crawls could use a Cloudflare Worker + Browser Rendering API
- Setup endpoints move to Vercel serverless
- All critical paths go through Convex (already 100% reliable)

## Implementation Priority

1. Add `ta.qa_check` as a local tool in proxy.py (HTTP-only, no Playwright needed)
2. Add local Playwright crawl tools (optional, detected at runtime)
3. Add Convex push for results
4. Update install.sh to offer Playwright install
5. Update demo page to show "Install locally for full crawl" when Render is slow
