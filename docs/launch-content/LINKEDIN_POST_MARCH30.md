# LinkedIn Post — March 30, 2026

Copy-paste below. ~350 words, optimized for LinkedIn algorithm (hook, story, technical substance, CTA).

---

AI agents forget everything between sessions. That's the single biggest waste in AI-assisted development right now.

Every time you ask Claude Code or Cursor to QA your app, it re-crawls from scratch. Same 31K tokens. Same 4 minutes. Even after a 3-line CSS fix.

My team and I have been building retention.sh to fix this.

After the first crawl, every rerun replays from a saved trajectory — 60-70% fewer tokens, 11 seconds instead of 4 minutes. The agent remembers what it already explored.

Here's what we shipped this week:

Local-first architecture — all crawling runs on YOUR machine via Playwright. No cloud dependency, no cold starts, no timeouts. Your 16GB laptop beats a 512MB cloud server every time.

Accessibility tree mode — structured element data instead of screenshots. No vision model needed. 25% faster, dramatically cheaper. Inspired by how Microsoft's Playwright MCP uses accessibility snapshots over pixels.

Mobile QA — Android emulator via ADB, iOS Simulator via xcrun. Captures screenshots, UI hierarchies, and finds missing accessibility labels automatically. Works alongside ios-simulator-mcp and claude-in-mobile.

This was motivated by real pain:

Jordan Cutler described the closed-loop TDD cycle — tell Claude to "use red/green TDD, closed loop testing, >90% coverage" and let it run the simulator itself. The gap: every session starts from zero. Trajectory memory fixes that.

Christopher Meiklejohn built a full mobile QA pipeline — 25-screen sweep in 90 seconds via CDP on Android and IDB on iOS. Beautiful work, but every run re-explores. The memory layer sits on top and makes reruns nearly free.

Colin Lee reminded everyone to test release builds on real hardware — emulator QA catches ~80% of functional regressions before you ever need device.

We just shipped this — 60-second install, works with Claude Code and Cursor:
curl -sL retention.sh/install.sh | bash

Then: retention.qa_check(url='http://localhost:3000')

No signup. No API key. No cloud dependency. It just works.

We're early and actively building based on what real users hit. If you try it and something breaks, I want to hear about it — DM me or drop a comment.

retention.sh — AI agents forget. retention.sh remembers.

---

**Tag suggestions** (add in LinkedIn UI):
Jordan Cutler, Christopher Meiklejohn, Colin Lee, Zhen Han

**Hashtags**:
#ClaudeCode #MCP #AITesting #DeveloperTools #MobileQA #Playwright
