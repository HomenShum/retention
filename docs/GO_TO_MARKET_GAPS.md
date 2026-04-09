# Go-to-Market Gaps — What We're Not Looking At

## What's DONE (strong)
- Product works end-to-end on retention.sh
- 12 pages live, all 200 OK
- 17 MCP tools deployed
- Team system with invite codes
- Hackathon landing page
- Founder demo video
- Smithery config ready
- 4-layer architecture documented

## What's MISSING for market push tomorrow

---

### 1. BENCHMARK — No Published Proof

**Problem:** We claim 95.5% token savings but the only proof is:
- Seeded demo data (we generated it ourselves)
- BenchmarkReportPage with hardcoded FALLBACK_DATA
- No third-party verification
- No public, reproducible benchmark that someone else can run

**What SWE-bench does:** Standardized tasks, public leaderboard, anyone can verify.
Source: https://www.swebench.com/

**What we need:**
1. **Public benchmark suite** — 5-10 open-source web apps with known bugs
   - Task: crawl app → find bugs → fix → replay → measure savings
   - Must be reproducible by anyone with `npx create-retention-app`
2. **Published results page** at `retention.sh/benchmarks`
   - Per-app results: tokens full vs replay, time, drift score
   - Model comparison: gpt-5.4-mini vs Claude 4.6 vs Gemini 3
   - N=5 consistency data (not just N=1)
3. **"Run it yourself" button** — visitors can trigger the benchmark on the demo
4. **Comparison with competitors:**
   - Spur ($4.5M, YC S24): "Tests in plain English, browser agents"
   - Canary (YC W26): "Reads source code, understands intent"
   - QA Wolf: "$91K+ salaries for manual QA" → our automation value

Source: https://www.qawolf.com/blog/the-12-best-ai-testing-tools-in-2026

**Action:** Build 5 benchmark apps with planted bugs, run N=5, publish results.

---

### 2. DISTRIBUTION — Not Listed Anywhere

**Problem:** Product exists at retention.sh but nobody can discover it. Zero distribution.

**Current channels:** None active.

**What needs to happen tomorrow:**

| Channel | Action | Difficulty |
|---|---|---|
| **Smithery.ai** | Run `smithery mcp publish` (config ready) | 5 min |
| **MCP Market** (mcpmarket.com) | Submit listing | 10 min |
| **OpenTools** | Submit listing | 10 min |
| **npm** | `npm publish` create-retention-app | 15 min |
| **GitHub** | Add topics: mcp, claude-code, qa, testing, playwright | 5 min |
| **Product Hunt** | Draft launch post | 30 min |
| **Hacker News** | "Show HN: retention.sh — AI agent memory for QA (60-70% fewer tokens)" | 10 min |
| **MCP Hackathon** | Register as enabler tool (hackerearth.com) | 10 min |
| **r/ClaudeAI** | Post about the MCP tool | 10 min |
| **X/Twitter** | Thread showing nodebenchai.com crawl → findings → fix loop | 20 min |
| **Dev.to** | "How I QA My App with 60-70% Fewer Tokens Using Claude Code + retention.sh" | 45 min |

Source: https://medium.com/mcp-server/the-rise-of-mcp-protocol-adoption-in-2026-and-emerging-monetization-models-cb03438e985c

---

### 3. PRICING — No Revenue Path

**Problem:** Pricing page exists at retention.sh/pricing but:
- No Stripe integration
- No feature gating
- No usage metering
- Everything is free with no path to paid

**What's needed:**
- **Free tier**: 10 crawls/month, 1 team member, local-only
- **Team tier** ($29/mo): unlimited crawls, 5 team members, Convex persistence, team dashboard
- **Enterprise**: custom pricing, private deployment, RBAC
- **Usage tracking**: count crawls per token in Convex (already have `usageCount` on mcpTokens)

**Not needed tomorrow** but needed before any revenue: Stripe checkout page + webhook for team tier activation.

---

### 4. SOCIAL PROOF — Zero Testimonials

**Problem:** No users besides ourselves. No testimonials. No "X companies use retention.sh."

**What's needed before hackathon:**
- @homen, @khush, @deirdre each write a 1-sentence testimonial
- nodebenchai.com case study: "Found and fixed a TDZ circular import that crashed headless Chrome"
- Screenshot of the nodebenchai.com findings as social proof

**What's needed within a week:**
- 3 external users from the MCP hackathon
- Their team dashboards as social proof
- "retention.sh found 3 issues in my app in 60 seconds" quote

---

### 5. COMPETITIVE POSITIONING — Not Clear Enough

**Problem:** Landing page says "AI Agent Memory for Claude Code" but competitors say:
- Spur: "AI QA Engineer — test with natural language"
- Canary: "First AI QA that understands your code"
- QA Wolf: "Get to 80% coverage in 4 months"

Our differentiator (trajectory replay = 60-70% savings) is buried.

**What the headline should actually be:**
> "Your QA agent re-crawls from scratch every run. retention.sh remembers — 60-70% fewer tokens, cheaper reruns."

Or even simpler:
> "AI agents forget. retention.sh remembers."

**Competitive comparison table needed:**
| Feature | retention.sh | Spur | Canary | QA Wolf |
|---|---|---|---|---|
| One-command install | ✅ curl | ❌ Dashboard signup | ❌ Dashboard | ❌ Sales call |
| Works with Claude Code | ✅ MCP | ❌ | ❌ | ❌ |
| Trajectory replay | ✅ 60-70% savings | ❌ Re-runs from scratch | ❌ | ❌ |
| Team memory sharing | ✅ Invite codes | ❌ | ❌ | ❌ Org accounts |
| Free tier | ✅ Unlimited local | ⚠️ Limited | ❌ | ❌ |
| Open source | ✅ MIT | ❌ | ❌ | ❌ |
| Price | $0 (free) → $29/mo | $$$$ | $$$$ | $10K+/mo |

Source: https://www.ycombinator.com/companies/spur, https://www.ycombinator.com/companies/canary

---

### 6. CONTENT — No Blog, No SEO

**Problem:** No content marketing. Zero SEO. Nobody searching "AI QA tool" or "MCP testing" will find us.

**What's needed:**
- Blog at retention.sh/blog (or dev.to cross-post)
- 3 articles:
  1. "How retention.sh Saves 60-70% of AI Agent Tokens on QA Reruns"
  2. "Building an MCP Tool for Claude Code — What We Learned"
  3. "The 21 UX Issues We Found and Fixed Using Our Own QA Tool"
- SEO meta tags on all pages (title, description, og:image)

---

### 7. ONBOARDING COMPLETION — Drop-off Risk

**Problem:** Users install → restart Claude Code → ... then what? No guided first experience inside Claude Code itself.

**What's needed:**
- After install, the rules file should trigger the agent to say:
  "retention.sh installed! Run `retention.qa_check(url='http://localhost:PORT')` to QA your app."
- The agent should auto-detect the dev server port (package.json scripts)
- First crawl results should include a "Share this with your team" CTA

---

### 8. RELIABILITY — Render Cold Start Is a Demo Killer

**Problem:** Render free tier sleeps after 15 min inactivity. First crawl takes ~30s. At a hackathon demo, 30s of "Server warming up..." is death.

**Options:**
1. Keep-warm cron (done, but only while this Claude session is active)
2. Upgrade Render to paid ($7/mo) — always-on
3. Move crawl to Convex HTTP action (no cold start) — but Convex can't run Playwright
4. Move to Fly.io ($5/mo with always-on) — faster cold starts (~5s)
5. **Best for tomorrow**: set up an external cron service (cron-job.org, free) to ping /api/health every 5 min

**Action:** Set up cron-job.org to keep Render warm 24/7 for free.

---

## Priority Order for Tomorrow

| Priority | Action | Time | Impact |
|---|---|---|---|
| **P0** | External cron to keep Render warm | 10 min | Demo doesn't fail |
| **P0** | Publish to Smithery + MCP Market + OpenTools | 30 min | Discoverability |
| **P0** | GitHub topics + README update | 15 min | SEO + discovery |
| **P1** | `npm publish` create-retention-app | 15 min | One-command starter |
| **P1** | Competitive comparison on pricing page | 30 min | Differentiation |
| **P1** | 3 internal testimonials | 15 min | Social proof |
| **P1** | Register for MCP Hackathon as tool | 10 min | Distribution |
| **P2** | Build 5 benchmark apps for public results | 2 hours | Credibility |
| **P2** | Dev.to article | 45 min | Content/SEO |
| **P2** | Product Hunt draft | 30 min | Launch prep |
| **P3** | Stripe integration | 4 hours | Revenue path |
| **P3** | Blog section | 2 hours | Content marketing |
