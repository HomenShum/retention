# Hackathon & Market Landscape — March/April 2026

## Active Hackathons

### 1. Global MCP Hackathon (HackerEarth)
- **Dates**: April 4-17, 2026
- **URL**: https://www.hackerearth.com/challenges/hackathon/mcp-hackathon/
- **Themes**: Build AI Agent, Secure MCP Server, Agent-to-Agent Communication
- **Team size**: 1-5
- **Prizes**: Cash prizes across all 5 categories
- **retention.sh fit**: PERFECT — Theme 1 (Build AI Agent) directly maps to our MCP tools. Participants building agents need QA. retention.sh is the QA layer that sits on top of their agent.

### 2. AI Trading Agents Hackathon (Lablab.ai)
- **Dates**: March 30 - April 12, 2026
- **Prize pool**: $55,000
- **Focus**: Autonomous trading agents
- **retention.sh fit**: MODERATE — Trading agents need verification that trades execute correctly. ta.qa_check could verify trading UI. Less direct than pure MCP hackathons.

### 3. Lablab.ai General AI Hackathon (Hybrid SF)
- **Dates**: April 20-26, 2026
- **Location**: Online + San Francisco on-site
- **Focus**: AI agents, general
- **retention.sh fit**: STRONG — General agent builders need QA for any web app their agent produces or interacts with.

### 4. Microsoft AI Dev Days Hackathon
- **Prizes**: $10,000 per category + Build 2026 tickets
- **Focus**: Azure, GitHub, Microsoft Foundry
- **retention.sh fit**: MODERATE — Azure/GitHub focus. retention.sh's MCP tools work with any agent, including those on Azure.

### 5. NVIDIA Agent Toolkit Hackathon
- **Focus**: NVIDIA AgentIQ toolkit for agentic systems
- **retention.sh fit**: MODERATE — GPU-focused agents. retention.sh adds the QA/verification layer.

### 6. MCP Dev Summit NYC
- **Dates**: April 2-3, 2026
- **Scale**: 95+ sessions, speakers from Anthropic, Datadog, Hugging Face, Microsoft
- **Focus**: MCP protocol, conformance testing, security, enterprise deployment
- **retention.sh fit**: PERFECT — This is THE event for MCP. retention.sh is an MCP tool. Conformance testing and security are exactly what ta.ux_audit does.

## YC W2026 Competitors & Adjacents

### Direct Competitors
1. **Spur** (YC S24) — AI QA Engineer, $4.5M raised, vision-first browser testing
   - Their approach: Natural language tests → browser agents simulate users
   - Our difference: retention.sh gives agents MEMORY. Spur re-crawls every time. We replay trajectories at 60-70% fewer tokens.

2. **Canary** (YC W26) — First AI QA engineer that reads source code
   - Their approach: Understands developer intent from codebase
   - Our difference: We're the verification layer that sits ABOVE the coding agent, not a separate QA tool.

3. **Arga** (YC W26) — Validation infrastructure with service mocks
   - Their approach: Mock external services for agent testing
   - Our difference: We test against REAL services with real browsers. No mocks.

### Adjacent YC Companies
- **Manicule** — AI documentation agency (QA for docs)
- Multiple agent-building platforms (we complement all of them)

## Job Market Signal
- Senior QA Automation: $91K-$169K
- AI QA Manager roles emerging at startups
- Key skills: Playwright, Python, CI/CD
- Trend: "AI-powered QA" replacing manual test engineers

## Analysis: How Hackathon Users Benefit from retention.sh

### The Problem Every Hackathon Team Has
They build an agent/app in 48 hours. They need to:
1. Verify it works before demo day
2. Show it to judges with confidence
3. Iterate fast (fix → verify → fix → verify)
4. Not waste tokens re-testing the same flows

### How retention.sh Solves This (in order of hackathon urgency)

**Hour 1-12 (Building)**
- `curl -sL retention.sh/install.sh | bash` — 60 seconds to install
- Agent now has ta.qa_check — instant QA after every code change
- .claude/rules/retention.md makes Claude Code auto-QA

**Hour 12-24 (First Integration Test)**
- `ta.sitemap(url='http://localhost:3000')` — see the whole app mapped with screenshots
- `ta.ux_audit(url='...')` — catch nav issues, dead ends, a11y gaps before judges see them
- Findings tell you exactly what to fix and where

**Hour 24-36 (Fix Loop)**
- Fix code → `ta.diff_crawl(url='...')` → see what improved
- Each re-crawl costs 60-70% fewer tokens (trajectory replay)
- `ta.savings.compare` — show judges the efficiency

**Hour 36-48 (Demo Prep)**
- `ta.team.invite` — share with teammates instantly
- Team dashboard shows aggregate savings
- Site map with screenshots = ready-made demo visual

### What's Clear vs. What Needs Work

**CLEAR (works today):**
- One-liner install — no signup, no web forms
- ta.qa_check — instant value, zero config
- Team invite — one Slack message
- Site map demo — visual proof of crawl capability
- Dashboard with real data

**NEEDS WORK for hackathon fit:**
- Render cold start (~30s) — hackathon demos can't wait. Need to keep backend warm during hackathon hours.
- "What is retention.sh?" — landing page says "AI Agent Memory for Claude Code" but hackathon users think in terms of "QA tool" or "testing tool." May need hackathon-specific landing variant.
- Live browser demo — works when backend is warm, but cold start kills the demo. Should pre-warm before hackathon starts.
- No mobile demo — all browser-only. Hackathon teams building mobile apps can't use the live crawl (but MCP tools still work with local emulator).

## Recommendations for Hackathon Marketing

1. **Pre-warm Render** before hackathon start times (cron job hitting /api/health every 5 min during hackathon hours)
2. **Hackathon landing page** at retention.sh/hackathon — "QA your hackathon project in 60 seconds"
3. **Submit to MCP Hackathon** as a tool that participants can use (not compete — enable)
4. **Tweet thread** showing the nodebenchai.com crawl → findings → fix → re-crawl loop
5. **Discord/Slack presence** in hackathon channels with the one-liner install
