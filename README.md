# retention.sh

**Your AI agent says "Done!" It isn't.**

You're correcting the same mistakes every session. Skipped tests. Missing steps. Forgotten context. retention.sh watches every tool call and blocks incomplete work -- before you have to.

```bash
curl -sL retention.sh/install.sh | bash
```

## The problem

AI coding agents (Claude, Cursor, Windsurf) skip steps, forget context, and declare victory early. Every correction costs tokens, time, and trust:

- "You didn't run the tests."
- "Where's the search step?"
- "I asked you to QA all 5 surfaces, not just the landing page."
- "The deploy is broken. The agent said it was done."

## How retention.sh fixes it

**4 hooks. Always on. No opt-out.**

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
