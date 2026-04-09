# Twitter/X Thread

## Tweet 1 (hook)
AI agents forget everything between sessions.

Your QA agent re-crawls from scratch every run — 31,000 tokens, 254 seconds, same app.

I built retention.sh to fix this.

One command. 60-70% fewer tokens. cheaper reruns.

🧵 ↓

## Tweet 2 (problem)
The problem:

Claude Code crawls your app → discovers screens → generates tests → runs them

Cost: ~31K tokens, ~$0.013

You fix 3 lines of CSS.

Claude Code: *crawls everything again from scratch*

Same 31K tokens. Same $0.013. For 3 lines of CSS.

## Tweet 3 (solution)
The fix: retention.sh gives your agent memory.

After the first crawl, it saves a trajectory.

Next run → replays the saved path → 1,400 tokens instead of 31,000.

11 seconds instead of 254.

$0.00 instead of $0.013.

## Tweet 4 (install)
Install in 60 seconds:

```
curl -sL retention.sh/install.sh | bash
```

Then in Claude Code:

```
retention.qa_check(url='http://localhost:3000')
```

Instant findings: JS errors, a11y gaps, rendering issues.

No signup. No dashboard. No web form.

## Tweet 5 (team)
Team memory:

One person discovers a workflow → saves trajectory.

Every teammate gets it for free.

```
RETENTION_TEAM=K7XM2P curl -sL retention.sh/install.sh | bash
```

Dashboard: retention.sh/memory/team?team=K7XM2P

## Tweet 6 (demo)
We dogfooded this on our own site.

Found 21 UX issues in one session:
- Layout jumps between pages
- Dead routes nobody could reach
- Logo invisible on dark backgrounds
- Nav inconsistencies across layouts

Every fix → re-crawl → verified automatically.

## Tweet 7 (CTA)
Try it:

→ retention.sh/demo (enter any URL)
→ retention.sh/hackathon (hackathon teams)
→ curl -sL retention.sh/install.sh | bash

Free during alpha — no signup required
Works with Claude Code, Cursor, OpenClaw
17 MCP tools included

AI agents forget. retention.sh remembers.
