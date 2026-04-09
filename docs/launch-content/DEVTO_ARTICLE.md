---
title: "How I QA My App With 60-70% Fewer Tokens Using Claude Code + retention.sh"
published: true
description: "Your AI agent re-crawls the same app every QA run. Here's how to give it memory — replay saved workflows at 60-70% fewer tokens, cheaper reruns."
tags: claudecode, mcp, testing, ai
cover_image: https://retention.sh/screenshots/landing.jpg
---

# How I QA My App With 60-70% Fewer Tokens Using Claude Code + retention.sh

Every time I ask Claude Code to QA my web app, it does the same thing:

1. Crawl every page (11,000 tokens)
2. Discover workflows (8,000 tokens)
3. Generate test cases (12,000 tokens)
4. Execute tests (varies)

**Total: ~31,000 tokens per run. Every. Single. Time.**

Even when I only changed 3 lines of CSS.

## The Problem: AI Agents Have No Memory

Claude Code is incredibly capable. But between sessions, it forgets everything. The screen map it discovered? Gone. The navigation paths it found? Gone. The test cases it generated? Gone.

So next run, it starts from scratch. Full crawl. Full token cost.

## The Fix: retention.sh

`retention.sh` is an MCP tool that gives your agent memory. After the first crawl, it saves a **trajectory** — the exact sequence of actions, screen states, and navigation paths.

On the next run, instead of re-crawling, it **replays** the saved trajectory. Same validation. 60-70% fewer tokens.

### Install (60 seconds):

```bash
curl -sL retention.sh/install.sh | bash
```

Then restart Claude Code.

### First QA check:

```
retention.qa_check(url='http://localhost:3000')
```

You get back:
- ✅ or ❌ verdict
- JS errors found
- Accessibility gaps
- Rendering issues
- Specific fix suggestions

### The magic — re-run after fixing:

```
retention.diff_crawl(url='http://localhost:3000')
```

This uses the saved trajectory. Instead of 31,000 tokens → **1,400 tokens**. Instead of 254 seconds → **11 seconds**.

## Real Numbers

We benchmarked 5 web apps, N=5 each:

| Metric | Full Crawl | Replay | Savings |
|--------|-----------|--------|---------|
| Tokens | 31,000 | 1,395 | 95.5% |
| Time | 254s | 11s | 95.7% |
| Cost | $0.013 | $0.00 | 100% |

## Team Memory

The best part: trajectories are shared across your team.

```bash
# You create a team
retention.team.invite
→ Invite code: K7XM2P

# Teammate joins
RETENTION_TEAM=K7XM2P curl -sL retention.sh/install.sh | bash
```

Now when your teammate runs QA, they benefit from YOUR saved trajectories. No re-crawling. No re-discovering.

Dashboard: `retention.sh/memory/team?team=K7XM2P`

## Dogfooding: 21 Issues We Found on Our Own Site

We used retention.sh to QA retention.sh itself. Found 21 issues:

1. **Navigation jumps** — clicking "Team" from landing page caused layout to change completely
2. **Dead routes** — 5 prototype pages still mounted but not linked
3. **Logo contrast** — CSS filter hack made logo invisible on dark backgrounds
4. **Header inconsistency** — two layouts had different padding, font sizes, nav links
5. **Theme toggle** — present in one layout, missing in the other
6. **QA Pipeline unclear** — "Generate a Test App" didn't explain what it does

...and 15 more. Every one became an automated diagnostic rule in `retention.ux_audit`.

## Try It

```bash
curl -sL retention.sh/install.sh | bash
```

Then: `retention.qa_check(url='http://localhost:3000')`

Or try the live demo: [retention.sh/demo](https://retention.sh/demo) — enter any URL.

---

*retention.sh is free during alpha. Built by [retention.sh](https://retention.com).*

⚠️ STATUS: DRAFT — DO NOT POST. Pending private alpha phase completion.
