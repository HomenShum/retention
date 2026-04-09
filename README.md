# retention.sh

**See what your AI agent actually missed.**

Your agent says "done." retention.sh shows you the skipped tests, the forgotten steps, and the missing context -- then blocks it from happening again.

```bash
curl -sL retention.sh/install.sh | bash
```

## The menu

Three things we do. That's it.

### Workflow Judge (the signature dish)

See what the agent did, what it missed, and whether it should have kept going. Hard verdict: PASS, FAIL, or BLOCKED.

*"Stop re-explaining the same steps every time."*

### Replay Kit

Capture one expensive workflow. Replay it at 60-70% lower cost. Strict judge verifies the replay actually worked.

*"Replay the same workflow cheaper, with proof it still works."*

### Run Anatomy

Full trace of every tool call, with screenshots, evidence, and per-step cost. Shareable link for your team.

*"Here's what happened. Here's what got skipped."*

## Who this is for

**Engineers** -- Agent keeps skipping tests and search steps. Catch skipped steps, replay repeated workflows cheaper.

**Team leads** -- No visibility into what agents actually did. See what happened, what was missed, where savings came from.

**Founders** -- Repeating expensive AI work manually every time. Turn repeated work into reusable operating leverage.

## How it works

4 hooks. Always on. No opt-out.

| Hook | What it does |
|------|-------------|
| `on-session-start` | Resumes prior incomplete work. Remembers what was left undone. |
| `on-prompt` | Detects workflow type. Injects required steps before the agent starts. |
| `on-tool-use` | Every tool call is tracked as evidence. Nudges if steps are missing. |
| `on-stop` | The gate. Blocks completion if mandatory steps are incomplete. |

## One line. Any agent.

### MCP (Claude Code, Cursor, Windsurf)

```bash
curl -sL retention.sh/install.sh | bash
# Then: ta.qa_check(url='http://localhost:3000')
```

### Python SDK (any provider)

```bash
pip install retention
```

```python
from retention import track
track()  # Auto-detects installed providers
```

Works with:

```python
# OpenAI
track(providers=["openai"])

# Anthropic
track(providers=["anthropic"])

# OpenAI Agents SDK
track(providers=["openai_agents"])

# LangChain
track(providers=["langchain"])

# CrewAI
track(providers=["crewai"])

# Claude Agent SDK
track(providers=["claude_agent"])
```

Every tool call, LLM response, and agent action is captured as a canonical event with privacy scrubbing. Stored locally in `~/.retention/activity.jsonl`.

## Telemetry

Every agent action produces a structured event:

```json
{
  "event_type": "tool_call",
  "tool_name": "bash",
  "input_keys": ["command"],
  "scrubbed_input": {"command": "[140c]"},
  "timestamp": "2026-04-08T14:30:00",
  "runtime": "anthropic",
  "duration_ms": 1200
}
```

Sensitive data is auto-scrubbed: API keys, tokens, passwords, file paths. You get telemetry without leaking secrets.

## MCP tools

| Tool | What it does |
|------|-------------|
| `ta.qa_check(url)` | Instant QA scan -- JS errors, a11y, rendering |
| `ta.sitemap(url)` | Interactive site map with screenshots |
| `ta.diff_crawl(url)` | Before/after comparison |
| `ta.start_workflow(url)` | Smart start -- replays saved trajectory if available |
| `ta.team.invite` | Share workflow memory across your team |

## Measured, not promised

| Metric | Result |
|--------|--------|
| Cost savings on reruns | **63-73%** |
| Judge agreement rate | **89%** |
| Workflow families tested | 3 |
| Corrections needed with retention.sh | **0** |

Every number is from real API calls, verified by an independent LLM judge.

## Team setup

```bash
ta.team.invite              # Person A creates team -> code: K7XM2P
RETENTION_TEAM=K7XM2P curl -sL retention.sh/install.sh | bash  # Person B joins
```

## Project structure

```
backend/           FastAPI backend (Python)
  app/             API routes, agents, services
  tests/           pytest suite
packages/
  retention-cli/   CLI tool (npm)
  retention-mcp/   MCP server (TypeScript)
  retention-sdk/   Python SDK -- 7 provider wrappers
  tcwp/            Trajectory/Checkpoint/Workflow Package
frontend/          React + Vite + Tailwind dashboard
scripts/           Shell scripts
```

## License

Copyright (c) 2026 retention.sh
