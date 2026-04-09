# r/ClaudeAI Post

**Title:** I built an MCP tool that gives Claude Code memory for QA — reruns cost 60-70% fewer tokens

**Body:**

I kept running into the same problem: every time I asked Claude Code to QA my web app, it re-crawled from scratch. Same 31K tokens. Same 254 seconds. Even when I only changed a few lines.

So I built **retention.sh** — an MCP tool that saves the crawl as a trajectory and replays it on reruns.

**Results:**
- First run: 31,000 tokens, 254s
- Every rerun: 1,400 tokens, 11s
- Savings: 95.5%

**Install:**
```
curl -sL retention.sh/install.sh | bash
```

Then: `retention.qa_check(url='http://localhost:3000')`

**What you get:**
- `retention.qa_check` — instant QA scan with verdict
- `retention.sitemap` — interactive site map with screenshots (drill into any page)
- `retention.ux_audit` — 21-rule UX audit
- `retention.diff_crawl` — before/after comparison
- Team memory sharing with invite codes

**Demo:** https://retention.sh/demo — enter any URL, see it crawled with findings.

Free during alpha. No signup required.

Works with Claude Code, Cursor, OpenClaw.

⚠️ STATUS: DRAFT — DO NOT POST. Pending private alpha phase completion.

---

# r/ChatGPTCoding Post

**Title:** MCP tool that makes AI QA reruns 60-70% cheaper — saves trajectory, replays instead of re-crawling

(Same body, adjusted for audience)
