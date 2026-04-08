# Show HN: retention.sh — AI agents forget. retention.sh remembers (60-70% fewer tokens on QA reruns)

Hi HN,

I built retention.sh — an MCP tool that gives AI coding agents (Claude Code, Cursor, OpenClaw) memory for QA workflows.

**The problem:** Every time your AI agent runs QA on your app, it re-crawls from scratch — same screens, same navigation, same 31,000 tokens, same $0.013. Over and over.

**The fix:** retention.sh remembers. After the first crawl, every rerun uses a saved trajectory — 1,400 tokens instead of 31,000. 11 seconds instead of 254. $0.00 instead of $0.013.

**One command to install:**
```
curl -sL retention.sh/install.sh | bash
```

Then in Claude Code: `ta.qa_check(url='http://localhost:3000')`

**What you get immediately:**
- Instant QA findings (JS errors, a11y gaps, rendering issues)
- Interactive site map with screenshots of every page
- 21-rule UX audit (navigation, visual consistency, security)
- Team memory sharing via invite codes

**How it works:**
1. First crawl: Playwright explores your app, captures screenshots, saves a trajectory
2. You fix a bug
3. Second crawl: Replays the saved trajectory — skips exploration entirely
4. 60-70% fewer tokens, 96% faster, reduced rerun cost

**Tech stack:** FastAPI backend, Convex persistence, Playwright crawling, React dashboard. MCP protocol for agent integration. Free to use during alpha.

**Live demo:** https://retention.sh/demo — enter any URL, see it crawled with findings.

**Benchmark:** 5 apps, N=5 each, 95.6% average token savings, 100% bug detection consistency.

We dogfooded this on our own website and found 21 UX issues (layout jumps, dead routes, missing a11y labels, logo contrast, navigation inconsistencies) — then built the diagnostic rules into the tool so it catches the same issues on YOUR site automatically.

Install: `curl -sL retention.sh/install.sh | bash`

⚠️ STATUS: DRAFT — DO NOT POST. Pending private alpha phase completion and team decision on public launch timing.
