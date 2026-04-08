# Ready-to-Send Alpha Outreach Messages

Copy-paste these messages. They're personalized for each platform.
Do NOT mention: open source, MIT, GitHub repo, trajectory caching, exploration memory.

---

## DISCORD — Post in MCP Community (#showcase or #general)

```
Hey all — been building an MCP tool called **retention.sh** that gives Claude Code / Cursor persistent QA memory across sessions.

The problem: every new session, your agent re-crawls your app from scratch. Same 31K tokens. Same 4 minutes. Even after a 3-line CSS fix.

retention.sh remembers. After the first crawl, reruns use 60-70% fewer tokens and finish in 11 seconds. It catches JS errors, a11y issues, broken links, and rendering problems automatically.

We just verified the full install works in 60 seconds:
`curl -sL retention.sh/install.sh | bash`

Then in Claude Code: `ta.qa_check(url='http://localhost:3000')`

Running an **early access** — free, early users get direct access to the team. Want early users who'll tell us what's broken.

DM me or try the install command directly.
```

---

## DISCORD — Post in Anthropic/Claude Community (#projects or #showcase)

```
Made an MCP tool for Claude Code that gives it QA memory between sessions.

Problem: Claude Code re-explores your entire app every time you ask it to check something. Even if you only changed one line.

retention.sh fixes this — first crawl saves everything, reruns are 60-70% cheaper.

What it finds: JS console errors, broken links, a11y violations, rendering issues, missing labels.

60-second install: `curl -sL retention.sh/install.sh | bash`
Then: `ta.qa_check(url='http://localhost:3000')`

Just shipped — free, no signup. Looking for Claude Code daily users. DM me if interested.
```

---

## DISCORD — Post in Cursor Community

```
Built an MCP tool that gives Cursor persistent QA memory.

Every time you ask your agent to check your app, it starts from scratch. retention.sh remembers what it already found, so reruns cost 60-70% fewer tokens.

Works with Cursor's MCP support. 60-second install:
`RETENTION_PLATFORM=cursor curl -sL retention.sh/install.sh | bash`

Finds real bugs: JS errors, broken rendering, a11y gaps. First crawl usually catches 5-15 real issues.

Just shipped — free, no signup. Early users get direct access to the team. DM me to try it.
```

---

## X/TWITTER — DMs to MCP builders

### For @firecrawl_dev (or team members)
```
Hey — saw Firecrawl's MCP server work. Building something complementary: retention.sh gives Claude Code/Cursor persistent QA memory so reruns cost 60-70% fewer tokens. Instead of re-crawling from scratch every session, it replays saved paths.

60-second install, free, no signup. Would love your take on it since you're deep in the crawling space. Interested in trying it?
```

### For people posting about Claude Code workflows
```
Hey [name] — saw your [post/thread] about [topic]. Been building retention.sh — MCP tool that gives Claude Code persistent QA memory. First crawl finds real bugs (JS errors, a11y, broken links), and reruns are 60-70% cheaper because the agent remembers what it explored.

60-second install, free, no signup. Looking for daily Claude Code users who'll give honest feedback. Want in?
```

### For MCP community accounts / builders
```
Hey — building an MCP tool that solves a real gap: agents have no memory between sessions. retention.sh gives Claude Code / Cursor persistent QA memory. First crawl catches bugs, reruns are 60-70% cheaper.

Just shipped — 60-second install, free. Would love your feedback — DM me or try: curl -sL retention.sh/install.sh | bash
```

---

## REDDIT — Comment on relevant threads

### For r/ClaudeAI or r/vibecoding threads about testing/QA
```
This is the exact gap we built retention.sh to fill. It's an MCP tool that gives Claude Code persistent QA memory across sessions.

Point it at your app and it catches real stuff: JS console errors, broken links, a11y violations, rendering issues. First crawl usually finds 5-15 legit bugs most test suites miss.

The key thing: each subsequent run is 60-70% cheaper because the agent remembers what it already explored. No more starting from scratch every session.

60-second install: `curl -sL retention.sh/install.sh | bash`

Just shipped — free, no signup. DM me if you want to try it, or just run the install command.
```

---

## PRIORITY ORDER (send these first)

1. MCP Community Discord #showcase post
2. Anthropic/Claude Discord #projects post
3. X DMs to 3-5 targeted MCP builders
4. Cursor Discord post
5. Reddit comment on next relevant thread in r/ClaudeAI or r/vibecoding
