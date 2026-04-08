# retention.sh

**The always-on workflow judge for AI coding agents.**

AI agents re-crawl your app from scratch every QA run -- 31K tokens, 254 seconds, every time. retention.sh gives agents memory: replay saved workflows at 60-70% fewer tokens, verified by structured LLM judge.

```bash
curl -sL retention.sh/install.sh | bash
```

Then in Claude Code:

```
ta.qa_check(url='http://localhost:3000')
```

## Key tools

| Tool | What it does |
|------|-------------|
| `ta.qa_check(url)` | Instant QA scan -- JS errors, a11y, rendering |
| `ta.sitemap(url)` | Interactive site map with screenshots |
| `ta.ux_audit(url)` | 21-rule UX audit with scoring |
| `ta.diff_crawl(url)` | Before/after comparison |
| `ta.start_workflow(url)` | Smart start -- auto-replays if trajectory exists |
| `ta.team.invite` | Generate invite for teammates |

## How it works

1. AI coding agent writes code
2. Agent calls retention.sh via MCP to verify
3. retention.sh runs the real app flow (browser or Android emulator)
4. Captures structured evidence (ActionSpan clips, screenshots)
5. Produces compact failure report -- agent fixes precisely
6. retention.sh reruns and compares before/after
7. On success, saves the workflow as a replayable trajectory

Next time the same flow needs QA, retention.sh replays the saved trajectory at 60-70% fewer tokens instead of re-crawling from scratch.

## Benchmark proof

Every number is from real API calls, verified by an independent LLM judge.

| Metric | Result |
|--------|--------|
| Cost savings (frontier to cheap replay) | **63-73%** |
| Quality judge: acceptable replays | **43% at nano, improved with mini** |
| Soft agreement (acceptable vs not) | **89%** |
| Pairwise winner agreement | **89%** |
| Workflow families tested | 3 (code changes, research, QA) |
| Total live API proof runs | 7 tasks x 3 calls each = 21 real API calls |

Verify it yourself:

```bash
python backend/scripts/verify_stats.py       # Data integrity (24/24 pass)
python backend/scripts/live_retention_proof.py   # Live API proof
python backend/scripts/run_calibration.py     # Structured judge
```

## Team setup

```bash
# Person A creates team
ta.team.invite  # -> code: K7XM2P

# Person B joins
RETENTION_TEAM=K7XM2P curl -sL retention.sh/install.sh | bash

# Dashboard
https://retention.sh/memory/team?team=K7XM2P
```

## Starter template

```bash
npx create-retention-app my-app
cd my-app && npm run dev
```

## Project structure

```
backend/           FastAPI backend (Python)
  app/             API routes, agents, services
  tests/           pytest suite
  scripts/         Utility scripts
packages/
  retention-cli/   CLI tool (npm)
  retention-mcp/   MCP server (TypeScript)
  retention-mcp-python/  MCP server (Python)
  retention-sdk/   Python SDK
  tcwp/            Trajectory/Checkpoint/Workflow Package
  create-retention-app/  npx scaffolding
scripts/           Shell scripts (setup, deploy, agents)
tests/e2e/         Playwright E2E tests
docs/              Architecture docs, case studies
```

## Deploy

```bash
# Fly.io
fly deploy

# Render
# Push to main -- auto-deploys via render.yaml

# Docker
docker build -t retention-backend .
docker run -p 8000:8000 --env-file .env retention-backend
```

## License

Copyright (c) 2026 retention.sh
