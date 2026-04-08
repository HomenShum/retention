---
name: retention-setup
description: Full onboarding for retention.sh — checks prerequisites, installs MCP tools, verifies connection, runs first crawl, shows results. Use when the user says "set up retention", "install retention.sh", "get started with QA", or "connect MCP tools".
---

# Retention Setup

One-command onboarding for retention.sh QA memory tools.

## Flow

1. **Check prerequisites**
   Run `ta.onboard.status` to see what's working and what's missing.

2. **Install if needed**
   If MCP tools aren't connected, run the installer:
   ```bash
   curl -sL retention.sh/install.sh | bash
   ```
   Then restart Claude Code and run `/mcp` to verify retention appears.

3. **First crawl**
   Ask the user for their app URL (localhost or deployed), then:
   ```
   ta.crawl.url(url='https://their-app.com')
   ```

4. **Show findings**
   Present the QA findings clearly:
   - JS errors (red) — things that are broken
   - Rendering issues (yellow) — things that might not work for bots/crawlers
   - Accessibility gaps (blue) — things to improve
   - SPA detection (info) — structural observations

5. **Suggest fixes**
   For each finding, suggest specific file paths and code changes.

6. **Re-crawl after fix**
   After the user fixes something:
   ```
   ta.crawl.url(url='https://their-app.com')
   ```
   Show that the re-crawl used trajectory replay and was cheaper.

7. **Team setup (optional)**
   If the user mentions teammates:
   ```
   ta.team.invite
   ```
   This generates a ready-to-paste Slack message.

## Key Principle

Get the user from "just installed" to "seeing their own QA data" in under 5 minutes.
The first crawl IS the demo — instant value without reading docs.
