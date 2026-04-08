# Private Alpha Strategy — retention.sh

## Decision: Hold Public Launch

**Date:** 2026-03-29
**Rationale:** No data moat yet. Public launch reveals architecture before accumulating the trajectory data flywheel that makes the product defensible. Competitors (TestSprite, Sandy, Firecrawl + AI combos) could replicate the basic pitch in 2-4 weeks.

## What We Reveal vs. Protect

### Safe to Show (Outcomes)
- "Found 21 bugs in 60 seconds"
- "QA reruns cost 95% less"
- "Works with Claude Code, Cursor — one command install"
- "No signup, no dashboard, no web form"
- Time saved, money saved, bugs found

### Never Reveal (Architecture)
- Exploration memory / trajectory caching mechanism
- Screen fingerprinting with drift detection
- Automatic fallback-to-exploration on drift
- ActionSpan economics and verification clips
- Multi-layer memory (crawl cache, workflow cache, test suite cache)
- Golden bug benchmarking framework
- Workflow compression (LCS-based)

## Phases

### Phase 1: Private Alpha (Now → 50-100 users)
**Goal:** Accumulate real trajectory data across diverse apps

- Direct outreach via Discord DMs, X DMs, Reddit comments
- Target: Claude Code power users, MCP enthusiasts, indie devs
- Each user gets 3 invite codes
- Private Slack/Discord for feedback
- Track: trajectory count, unique apps crawled, rerun frequency

### Phase 2: Referral Beta (50 → 500 users)
**Goal:** Prove the compounding value + team features

- Invite codes create viral loop
- Run "find the most bugs" challenges
- Collect case studies with real numbers
- Track: team adoption, shared trajectory usage

### Phase 3: Controlled Public Launch (500+ users)
**Goal:** Launch with proof that can't be faked

- "Found 5,000+ bugs across 300 apps"
- "Average 94% token savings across 50,000 reruns"
- Real testimonials, real case studies
- NOW post Show HN, Dev.to, Reddit, Twitter
- Competitors can't shortcut the data flywheel

## Blockers to Fix Before Any Outreach
1. [ ] Render backend returning 404 — must be live for demo to work
2. [ ] Verify install.sh works end-to-end on a clean machine
3. [ ] Ensure crawl-any-URL demo produces results reliably
4. [ ] Set up basic analytics (who's installing, who's crawling what)

## Key Metric: Trajectory Data Accumulated
The moat is measured in trajectories. Track:
- Total trajectories saved
- Unique apps/URLs crawled
- Rerun-to-first-run ratio (proves replay value)
- Team trajectory sharing events
- Time from install to first finding

## Competitive Intelligence
- **TestSprite** — funded, just launched Product Hunt March 2026. No memory/replay.
- **Sandy** — "Think once, replay forever" (HN Feb 2026, ~150 stars). Generic, not QA-specific. WATCH CLOSELY.
- **Firecrawl** — 85K stars. Commodity crawling substrate. Anyone could layer QA on top.
- **Microsoft Playwright MCP** — official, 25+ tools. Table stakes.
- **GitHub Copilot** — Playwright MCP built in. Biggest long-term threat to crawl layer.

## Decision Criteria for Going Public
All must be true:
- [ ] 100+ real users with saved trajectories
- [ ] 1,000+ trajectories across 50+ unique apps
- [ ] 3+ case studies with real company names
- [ ] Team sharing feature used by 5+ teams
- [ ] Clear competitive positioning tested with alpha users
- [ ] Team/CEO alignment on OSS strategy (Pattern C: open MCP client, proprietary platform)
