"""Playground API endpoints — crawl + analyze + chat for the interactive playground.

POST /api/playground/crawl             — Crawl a URL with Playwright, return findings
POST /api/playground/analyze           — Server-side JSONL analysis fallback
POST /api/playground/chat/stream       — SSE chat with discovery-led agent
GET  /api/playground/status            — Health check
GET  /api/playground/signals/summary   — Dev-facing signal aggregation (internal)

Chat strategy:
  - Agent is consultative, not salesy. Leads with questions to map the user's problem space.
  - Extracts psychographic signals (stack, use case, pain points, gaps) from every message.
  - Signals are persisted to backend/data/playground_signals/ for product research.
  - Hybrid LLM: OpenRouter free model for Q&A, Claude for action/analysis tasks.
  - Rate limited globally (100 msgs/min) and per session (20 msgs/min).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/playground", tags=["playground"])

_SIGNALS_DIR = Path(__file__).resolve().parents[2] / "data" / "playground_signals"


# ─── Rate limiting ────────────────────────────────────────────────────────

_GLOBAL_LIMIT = 100
_SESSION_LIMIT = 20
_global_window: list[float] = []
_session_windows: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(session_id: str) -> str | None:
    now = time.time()
    cutoff = now - 60
    _global_window[:] = [t for t in _global_window if t > cutoff]
    if len(_global_window) >= _GLOBAL_LIMIT:
        return "Rate limit reached. Please try again in a minute."
    window = _session_windows[session_id]
    window[:] = [t for t in window if t > cutoff]
    if len(window) >= _SESSION_LIMIT:
        return "You've sent too many messages. Please wait a moment."
    _global_window.append(now)
    window.append(now)
    return None


# ─── Signal extraction ────────────────────────────────────────────────────

_STACK_SIGNALS = {
    "openai agents": "agents-sdk",
    "agents sdk": "agents-sdk",
    "openclaw": "agents-sdk",
    "openai": "openai-sdk",
    "anthropic": "anthropic-sdk",
    "langchain": "langchain",
    "langgraph": "langgraph",
    "crewai": "crewai",
    "pydanticai": "pydantic-ai",
    "pydantic ai": "pydantic-ai",
    "autogen": "autogen",
    "cursor": "cursor",
    "claude code": "claude-code",
    "fine-tun": "ml-training",
    "finetune": "ml-training",
    "mcp server": "mcp-server",
    "mcp tool": "mcp-server",
    "build a server": "mcp-server",
    "tool server": "mcp-server",
    "cline": "cline",
    "openrouter": "openrouter",
    "groq": "groq",
    "gemini": "gemini",
    "mistral": "mistral",
    "ollama": "ollama",
    "bedrock": "aws-bedrock",
    "vertex": "gcp-vertex",
    "azure": "azure-openai",
}

_USE_CASE_SIGNALS = {
    "test": "testing",
    "qa": "qa",
    "monitor": "monitoring",
    "observ": "observability",
    "cost": "cost-reduction",
    "token": "token-optimization",
    "debug": "debugging",
    "trace": "tracing",
    "eval": "evaluation",
    "fine-tun": "fine-tuning",
    "train": "training",
    "rag": "rag",
    "retriev": "retrieval",
    "agent": "agents",
    "automat": "automation",
    "workflow": "workflow",
    "pipeline": "pipeline",
    "scrape": "web-scraping",
    "crawl": "web-crawling",
}

_PAIN_SIGNALS = {
    "expens": "cost-pain",
    "too much": "cost-pain",
    "slow": "latency-pain",
    "timeout": "latency-pain",
    "repeat": "repetition-pain",
    "same thing": "repetition-pain",
    "redund": "repetition-pain",
    "break": "reliability-pain",
    "fail": "reliability-pain",
    "flak": "flakiness-pain",
    "no visib": "visibility-gap",
    "can't see": "visibility-gap",
    "don't know": "visibility-gap",
    "how much": "cost-inquiry",
    "memory": "memory-gap",
    "forget": "memory-gap",
    "context": "context-loss",
    "start over": "context-loss",
}

_INTENT_SIGNALS = {
    "install": "install-intent",
    "try": "trial-intent",
    "sign up": "signup-intent",
    "buy": "purchase-intent",
    "pricing": "pricing-inquiry",
    "how do i": "how-to-inquiry",
    "integrate": "integration-intent",
    "connect": "integration-intent",
    "how does": "product-curiosity",
    "what is": "product-curiosity",
    "show me": "demo-request",
    "example": "demo-request",
}


def _extract_signals(message: str, history: list[dict]) -> dict[str, Any]:
    """Extract psychographic signals from the current message and conversation history."""
    # Combine message with recent history for richer context
    full_text = message.lower()
    for msg in history[-4:]:
        full_text += " " + msg.get("content", "").lower()

    stacks = [label for kw, label in _STACK_SIGNALS.items() if kw in full_text]
    use_cases = [label for kw, label in _USE_CASE_SIGNALS.items() if kw in full_text]
    pains = [label for kw, label in _PAIN_SIGNALS.items() if kw in full_text]
    intents = [label for kw, label in _INTENT_SIGNALS.items() if kw in message.lower()]

    # Detect explicit gaps / feature requests
    gaps: list[str] = []
    gap_phrases = [
        "wish", "would be nice", "if only", "can you add", "do you support",
        "what about", "does it work with", "can it", "will it", "feature request",
        "missing", "lacks", "doesn't have", "not supported",
    ]
    for phrase in gap_phrases:
        if phrase in full_text:
            gaps.append(message[:120])  # capture snippet as gap note
            break

    return {
        "stacks": list(dict.fromkeys(stacks)),       # deduplicated, order-preserved
        "use_cases": list(dict.fromkeys(use_cases)),
        "pains": list(dict.fromkeys(pains)),
        "intents": list(dict.fromkeys(intents)),
        "gaps": gaps,
        "message_len": len(message),
        "turn": len(history),
    }


def _persist_signal(session_id: str, signals: dict[str, Any], message: str) -> None:
    """Append a signal record to the session's JSONL file."""
    if not any([signals["stacks"], signals["use_cases"], signals["pains"],
                signals["intents"], signals["gaps"]]):
        return  # nothing interesting to save

    _SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = _SIGNALS_DIR / f"{date_str}.jsonl"

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session": session_id[:12],
        "msg_preview": message[:100],
        **signals,
    }
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


# ─── System prompt ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """# retention.sh Agent — Skills Manifest v2

You are the retention.sh playground agent. Your job: help developers understand their AI agent costs and integrate retention.sh.

## Identity
Senior engineer. Direct. Concrete. Never salesy. Peer on Slack, not a support rep.
Short responses. Code blocks for anything copy-pasteable. One question max per turn.

---

## SKILLS

### SKILL: scaffold_project
**Triggers**: "hackathon", "don't know what to build", "idk what to build", "what should I build", "give me an idea", framework mentioned + demo context
**Execute**: Call `scaffold_project(framework, hackathon)` immediately — no permission needed
**If framework unknown**: Ask exactly ONE question: "What's your stack?" — then call the tool
**After tool runs**: Say "I've built the files above — hit ▶ run demo to see the before/after."
**Never**: Ask for permission, describe what you're about to do, give walls of text

### SKILL: crawl_url
**Triggers**: "crawl", "quick check", "health check", "check my site"
**Execute**: Call `crawl_url(url=...)` for a fast health check (~3s). Good for quick JS error / a11y / broken link scan.
**After tool runs**: Summarize findings concisely (3 bullets max), suggest fix if errors found

### SKILL: web_qa
**Triggers**: "test my app", "QA my app", "run tests on", "find bugs", "full QA", URL + "test" context
**Execute**: Call `run_web_qa(url=...)` immediately if URL present; ask for URL if not
**After tool runs**: Say "QA pipeline started — check the QA tab for live progress." Don't poll manually.
**Note**: This is the FULL pipeline (crawl→workflow→testcase→execute), not the quick crawl. Takes 2-10 min.

### SKILL: mobile_qa
**Triggers**: Android package name pattern (com.X.Y), "test my Android app", "native app"
**Execute**: Call `run_mobile_qa(package_name=...)` immediately
**If no emulator**: Show setup steps from the tool result. Be specific and actionable.
**After tool runs**: Say "Mobile QA started — check the QA tab."

### SKILL: generate_integration
**Triggers**: User names a framework AND asks to install/integrate/use retention.sh, "how do I add this", "integrate", "set up"
**Execute**: Call `generate_integration_code(framework)` — paste the output directly
**Then explain**: After showing the code, explain what the key line does in one sentence:
- `track()` → "This one line intercepts every LLM/tool call. First run records. Every rerun replays."
- `curl install` → "Adds retention.sh as an MCP tool to your editor. No code changes."
- `@observe` → "Wraps a function so identical inputs return cached results instantly."
- MCP server → "Your agent connects to retention.sh as an MCP server. Tool calls get memory at the protocol layer — no tool code changes."
**If they're building an agent harness** (custom tool-calling system, not using an existing framework): recommend the **MCP server path** — `retention-sh serve --port 3847` — their agent connects as an MCP client and gets memory for free.

### SKILL: hackathon_guide
**Triggers**: "hackathon", "judges", "prize", "hours left", "demo time", "time pressure"
**Execute**:
1. Tell them the winning demo formula: cold run → install → warm run → show before/after
2. Ask their stack if unknown
3. Call scaffold_project once known
**End every response with**: One concrete next step (imperative sentence)

### SKILL: cost_analysis
**Triggers**: "jsonl", "logs", "how much", "expensive", "bill spike", token questions
**Execute**: "Paste your JSONL in the **Analyze** tab — you'll see cost by tool + repetition % in 10 seconds."
**If they give numbers**: Do the math. Monthly = runs × cost_per_run. Savings = monthly × 0.85.

### SKILL: explain_product
**Triggers**: "what is", "how does", "explain", "does it work with", "tell me about", general curiosity
**Response pattern** — follow this exact structure (Calculus Made Easy: start from what they know, one idea at a time):

1. **The problem they already know**: "Your agent starts from scratch every run. Same crawl, same discovery, same cost. Every time."
2. **The one insight**: "But the *structure* of what it does is the same — which pages to visit, which tools to call. Only the *data* changes."
3. **What retention.sh does**: "It captures that structure after the first run, and replays it on every run after — filling in fresh data as it goes."
4. **The concrete number**: "First run: ~1,800 tokens. Second run: ~30 tokens. Same answer. 98% cheaper."
5. **The safety guarantee**: "If the task changes enough that the old plan doesn't fit, it falls back to fresh execution. Never replays stale actions."
6. **One next step**: Point to something they can do RIGHT NOW — try the Preview demo, paste their logs, or tell you their stack.

**Rules**: No jargon without context. No "cached navigation paths" — say "remembers where it went." Short sentences. Concrete numbers. Anyone from a CEO to a junior dev should read it and think "oh yeah, that makes sense."

### SKILL: explain_divergence
**Triggers**: "what if the task changes", "what about failures", "stale cache", "wrong action", "nondeterminism", "confidence", "how reliable", "break"
**Response pattern** — follow this structure:

1. **Validate the concern**: "This is the most important question. If retention.sh replayed the wrong thing, it would be worse than useless."
2. **The mechanism**: "Every task gets a fingerprint. On rerun, retention.sh checks: how similar is this new task to the one I remembered? It computes a confidence score, 0 to 100%."
3. **Three tiers** (use a table or list):
   - **>85% match** → full replay, 98% savings
   - **50-85%** → partial replay — reuse what matches, re-explore what changed, 40-70% savings
   - **<50%** → fresh execution — agent thinks from scratch, no risk
4. **The guarantee**: "Low confidence = the agent runs fresh. Your workflow works correctly, even if that run costs full price. retention.sh is exciting, but it doesn't break your stuff."
5. **Point to the demo**: "Try the Preview tab — click ▶ three times to see all three cases: replay, partial cache, and divergence fallback."

---

## What retention.sh does
- **Memory**: Caches exploration paths + workflow templates — reruns skip re-exploration (up to 95% fewer tokens)
- **Divergence handling**: Fingerprint confidence scoring — high match = replay, low match = dynamic fallback. Never replays stale trajectories.
- **Partial replay**: When task changes slightly, reuses what it can, re-explores only what changed (typically 40-70% savings vs cold start)
- **Cost visibility**: Per-session breakdown by tool, with repetition flagged
- **Framework coverage**: Claude Code hook, OpenAI SDK, Anthropic SDK, LangChain, CrewAI, PydanticAI, Agents SDK, MCP proxy
- **Team memory**: One agent's exploration saves all teammates' reruns

## Integration — 5 paths (pick the one that fits)

**Path 1 — SDK auto-patch** (simplest, for Python agent devs):
```python
pip install retention-sh
from retention_sh import track
track()  # one line — patches OpenAI, Anthropic, LangChain, CrewAI, PydanticAI automatically
```
Explain: "Line 2 of your code — `track()` — intercepts every LLM call and tool call. First run records the trajectory. Every rerun replays it. You change zero other code."

**Path 2 — Claude Code hook** (for daily Claude Code / Cursor / OpenClaw users):
```bash
curl -sL retention.sh/install.sh | bash
# restart Claude Code — done
```
Explain: "This adds retention.sh as an MCP tool to your Claude Code config. Every session gets memory. No code changes."

**Path 3 — @observe decorator** (for granular control over what gets cached):
```python
from retention_sh import observe
@observe(name="fetch_data")
def fetch_data(url): ...
```
Explain: "Wrap any function with @observe. If the inputs match a previous call, the cached result is returned instantly. Great for data fetching, preprocessing, API calls."

**Path 4 — MCP server** (for devs building their own agent harness):
```python
# Your agent's MCP server config (mcp.json or tool manifest)
{
  "mcpServers": {
    "retention": {
      "command": "retention-sh",
      "args": ["serve", "--port", "3847"]
    }
  }
}
```
Explain: "If you're building an agent with tool-calling (OpenAI Agents SDK, custom harness, any MCP client), retention.sh runs as an MCP server your agent connects to. It intercepts tool calls at the MCP layer — your agent gets memory without changing a single tool implementation."

**Path 5 — REST API** (for any language, any framework):
```
POST https://api.retention.sh/v1/ingest
{"tool_name": "search", "input_keys": ["query"], "duration_ms": 340}
```

When explaining integration, ALWAYS:
1. Identify which path fits the user's stack
2. Show the exact code (copy-pasteable)
3. Explain what that one line/config does in plain English
4. Point to the scaffold demo: "See line 2 of agent.py — that's doing all of this"

## Pain → solution mapping
- "expensive / bill jumped" → cost breakdown in Analyze tab + memory replay math
- "re-explores every session / starts from scratch" → that's the memory gap — first run is investment, every rerun is near-free
- "I don't know what it's doing" → per-session tool call trace with costs
- "flaky tests / keeps failing" → deterministic replay from saved trajectories
- "too slow" → replay skips the slow exploration phase entirely
- "we run this N times a day" → do the math: $X/run × N × 30 = monthly waste, then ×0.85

## Tone and style
- Concrete numbers > vague claims. "You'd save $240/month" beats "significant savings"
- Short. Use code blocks for anything copy-pasteable. No walls of text.
- Answer the question asked. Don't volunteer unrelated features.
- If they ask something outside retention.sh: answer it briefly if you know it, then bring it back
- Never say "Great question!" or any sycophantic filler
"""


# ─── LLM streaming helpers ────────────────────────────────────────────────

from typing import AsyncGenerator
import httpx


_OR_MODELS = [
    "liquid/lfm-2.5-1.2b-instruct:free",      # small but reliable
    "google/gemma-3-12b-it:free",              # fallback
    "meta-llama/llama-3.3-70b-instruct:free",  # best quality when available
]


async def _stream_openrouter(messages: list[dict]) -> AsyncGenerator[Any, None]:
    """Token-by-token SSE stream from OpenRouter. Yields str tokens + dict metadata."""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return

    for model in _OR_MODELS:
        yielded = False
        char_count = 0
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST",
                    "https://openrouter.ai/api/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": True,
                        "temperature": 0.7,
                        "max_tokens": 1024,
                    },
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://retention.sh",
                        "X-Title": "retention.sh playground",
                    },
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data.strip() == "[DONE]":
                            break
                        try:
                            parsed = json.loads(data)
                            delta = parsed["choices"][0]["delta"].get("content", "")
                            if delta:
                                yielded = True
                                char_count += len(delta)
                                yield delta
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
            if yielded:
                # Estimate tokens (4 chars ≈ 1 token) — OpenRouter free models don't return usage
                input_tokens = sum(len(m.get("content", "")) for m in messages) // 4
                output_tokens = char_count // 4
                yield {
                    "type": "token_usage",
                    "model": model,
                    "provider": "openrouter",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": 0.0,  # free tier
                    "ms": int((time.time() - t0) * 1000),
                }
                return  # success — don't try next model
        except Exception as e:
            logger.debug("OpenRouter model %s failed: %s", model, e)
            continue


# ─── Playground tools (agent harness) ───────────────────────────────────

PLAYGROUND_TOOLS = [
    {
        "name": "scaffold_project",
        "description": "Generate a complete runnable demo project for the user with retention.sh pre-integrated. Call this when the user doesn't know what to build, is at a hackathon, or asks for a project idea. Generates actual files they can run immediately.",
        "input_schema": {
            "type": "object",
            "properties": {
                "framework": {
                    "type": "string",
                    "enum": ["claude-code", "langchain", "openai", "anthropic", "crewai", "pydantic-ai", "agents-sdk", "ml-training", "mcp-server", "rest"],
                    "description": "The framework/stack the user has mentioned",
                },
                "use_case": {
                    "type": "string",
                    "description": "What the user wants to build or demonstrate (e.g. 'QA testing agent', 'web research agent')",
                },
                "hackathon": {
                    "type": "boolean",
                    "description": "True if the user is at a hackathon — optimizes for demo impact over completeness",
                },
            },
            "required": ["framework"],
        },
    },
    {
        "name": "crawl_url",
        "description": "Crawl a URL with Playwright to find QA issues: JS errors, a11y gaps, broken links. Call this when the user wants to check their app or site.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The full URL to crawl"}
            },
            "required": ["url"],
        },
    },
    {
        "name": "generate_integration_code",
        "description": "Generate exact copy-paste integration code for the user's framework. Call this when the user says what stack they're using and wants to integrate retention.sh.",
        "input_schema": {
            "type": "object",
            "properties": {
                "framework": {
                    "type": "string",
                    "enum": ["claude-code", "langchain", "openai", "anthropic", "crewai", "pydantic-ai", "agents-sdk", "ml-training", "mcp-server", "rest"],
                    "description": "The framework or SDK the user is using",
                },
                "use_case": {"type": "string", "description": "Brief description of what the user is building"},
            },
            "required": ["framework"],
        },
    },
    {
        "name": "generate_demo_script",
        "description": "Generate a 2-minute hackathon demo script showing before/after with retention.sh. Call this when the user is at a hackathon or wants a demo narrative.",
        "input_schema": {
            "type": "object",
            "properties": {
                "framework": {"type": "string"},
                "use_case": {"type": "string"},
                "hours_left": {"type": "number", "description": "Hours remaining in the hackathon"},
            },
        },
    },
    {
        "name": "run_web_qa",
        "description": "Run a full QA pipeline on a web app URL: crawl all pages, discover workflows, generate test cases, execute them. Call this when the user says 'test my app', 'QA my site', 'find bugs', 'run tests on [URL]'. Returns a run_id the QA tab uses to show live progress.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The full URL to test (e.g. http://localhost:3000)"},
                "app_name": {"type": "string", "description": "A friendly name for the app"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "run_mobile_qa",
        "description": "Run a full QA pipeline on an Android app: launch on emulator, crawl screens, discover workflows, generate and execute test cases. Call this when the user mentions an Android package name (com.X.Y pattern) or says 'test my Android app'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "package_name": {"type": "string", "description": "Android package name (e.g. com.instagram.android)"},
                "app_name": {"type": "string", "description": "A friendly name for the app"},
            },
            "required": ["package_name"],
        },
    },
    {
        "name": "check_qa_status",
        "description": "Check the progress of a running QA pipeline. Call this when the user asks 'how is the QA going', 'check progress', 'is it done yet'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "The run_id returned by run_web_qa or run_mobile_qa"},
            },
            "required": ["run_id"],
        },
    },
]


# Cache for structured scaffold data — read by _stream_anthropic_with_tools after tool execution
_SCAFFOLD_CACHE: dict[str, Any] = {}

# Cache for QA run info — used to emit qa_started events
_QA_RUN_CACHE: dict[str, Any] = {}


def _generate_scaffold(framework: str, use_case: str, hackathon: bool = False) -> dict[str, Any]:
    """Generate a minimal runnable project for the given framework."""
    uc = use_case.strip() or "demo agent"
    tag = "# HACKATHON DEMO" if hackathon else "# retention.sh demo"

    templates: dict[str, dict[str, Any]] = {
        "langchain": {
            "project_name": "langchain-retention-demo",
            "description": f"LangChain agent with retention.sh memory — {uc}",
            "files": [
                {
                    "path": "agent.py",
                    "language": "python",
                    "content": f"""{tag}
from retention_sh import track
track()  # ← one line — all LangChain calls now cached

from langchain_openai import ChatOpenAI
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate
from langchain_community.tools import DuckDuckGoSearchRun
import time

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
tools = [DuckDuckGoSearchRun()]
prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful assistant. Be concise."),
    ("human", "{{input}}"),
    ("placeholder", "{{agent_scratchpad}}"),
])
agent = create_tool_calling_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools, verbose=False)

start = time.time()
result = executor.invoke({{"input": "What is retention.sh and why do agents need memory?"}})
elapsed = time.time() - start

print(f"Answer: {{result['output']}}")
print(f"Time: {{elapsed:.1f}}s")
print("Run again — retention.sh serves from memory (85-95% fewer tokens)")
""",
                },
                {
                    "path": "requirements.txt",
                    "language": "text",
                    "content": "langchain>=0.2\nlangchain-openai\nlangchain-community\nretention-sh\nduckduckgo-search\n",
                },
                {
                    "path": "demo.sh",
                    "language": "bash",
                    "content": """#!/bin/bash
echo "=== Run 1: Cold start (full exploration) ==="
python agent.py

echo ""
echo "=== Run 2: Replay from memory ==="
python agent.py

echo ""
echo "Open https://retention.sh/memory to see token savings breakdown"
""",
                },
            ],
            "demo_steps": [
                "pip install -r requirements.txt",
                "export OPENAI_API_KEY=sk-...",
                "bash demo.sh",
                "Point at run 2's token count vs run 1 for judges",
            ],
            "highlight": "retention.sh is wired at line 2 of agent.py — zero other changes needed",
        },
        "openai": {
            "project_name": "openai-retention-demo",
            "description": f"OpenAI agent with retention.sh memory — {uc}",
            "files": [
                {
                    "path": "agent.py",
                    "language": "python",
                    "content": f"""{tag}
from retention_sh import track
track()  # ← patches openai.chat.completions.create automatically

from openai import OpenAI
import time, json

client = OpenAI()
tools = [{{
    "type": "function",
    "function": {{
        "name": "search_web",
        "description": "Search the web for information",
        "parameters": {{"type": "object", "properties": {{"query": {{"type": "string"}}}}, "required": ["query"]}},
    }},
}}]

def run_agent(query: str) -> str:
    messages = [{{"role": "user", "content": query}}]
    start = time.time()

    while True:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=tools,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            messages.append(msg)
            for tc in msg.tool_calls:
                result = f"Search result for: {{json.loads(tc.function.arguments)['query']}}"
                messages.append({{"role": "tool", "tool_call_id": tc.id, "content": result}})
        else:
            elapsed = time.time() - start
            print(f"Answer: {{msg.content}}")
            print(f"Tokens: {{resp.usage.total_tokens}}, Time: {{elapsed:.1f}}s")
            return msg.content

run_agent("Explain how AI agent memory reduces costs")
print("Run again — second call uses cached exploration")
""",
                },
                {
                    "path": "requirements.txt",
                    "language": "text",
                    "content": "openai>=1.0\nretention-sh\n",
                },
                {
                    "path": "demo.sh",
                    "language": "bash",
                    "content": "#!/bin/bash\necho '=== Run 1 ==='\npython agent.py\necho ''\necho '=== Run 2 (from memory) ==='\npython agent.py\n",
                },
            ],
            "demo_steps": [
                "pip install -r requirements.txt",
                "export OPENAI_API_KEY=sk-...",
                "bash demo.sh",
                "Show token count drops from run 1 to run 2",
            ],
            "highlight": "track() at line 2 patches OpenAI client — no other changes needed",
        },
        "anthropic": {
            "project_name": "claude-retention-demo",
            "description": f"Claude agent with retention.sh memory — {uc}",
            "files": [
                {
                    "path": "agent.py",
                    "language": "python",
                    "content": f"""{tag}
from retention_sh import track
track()  # ← patches anthropic.messages.create automatically

import anthropic, time

client = anthropic.Anthropic()
tools = [{{
    "name": "search",
    "description": "Search for information",
    "input_schema": {{"type": "object", "properties": {{"query": {{"type": "string"}}}}, "required": ["query"]}},
}}]

start = time.time()
messages = [{{"role": "user", "content": "How does AI agent memory work? Use search if needed."}}]

while True:
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        tools=tools,
        messages=messages,
    )
    if resp.stop_reason == "tool_use":
        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                tool_results.append({{"type": "tool_result", "tool_use_id": block.id, "content": f"Result for: {{block.input.get('query', '')}}" }})
        messages.append({{"role": "assistant", "content": resp.content}})
        messages.append({{"role": "user", "content": tool_results}})
    else:
        text = next((b.text for b in resp.content if hasattr(b, "text")), "")
        print(f"Answer: {{text[:200]}}")
        print(f"Tokens: {{resp.usage.input_tokens + resp.usage.output_tokens}}, Time: {{time.time()-start:.1f}}s")
        break

print("Run again — retention.sh replays from memory")
""",
                },
                {"path": "requirements.txt", "language": "text", "content": "anthropic>=0.30\nretention-sh\n"},
                {"path": "demo.sh", "language": "bash", "content": "#!/bin/bash\necho '=== Run 1 ==='\npython agent.py\necho ''\necho '=== Run 2 (memory) ==='\npython agent.py\n"},
            ],
            "demo_steps": [
                "pip install -r requirements.txt",
                "export ANTHROPIC_API_KEY=sk-ant-...",
                "bash demo.sh",
                "Point at token difference between run 1 and run 2",
            ],
            "highlight": "track() at line 2 — that's the entire integration",
        },
        "crewai": {
            "project_name": "crewai-retention-demo",
            "description": f"CrewAI multi-agent setup with retention.sh memory — {uc}",
            "files": [
                {
                    "path": "crew.py",
                    "language": "python",
                    "content": f"""{tag}
from retention_sh import track
track()  # ← hooks CrewAI task execution

from crewai import Agent, Task, Crew
from crewai_tools import SerperDevTool
import time

search_tool = SerperDevTool()

researcher = Agent(
    role="Research Analyst",
    goal="Find information about AI agent memory systems",
    backstory="You are a concise researcher. One paragraph answers only.",
    tools=[search_tool],
    verbose=False,
)
writer = Agent(
    role="Technical Writer",
    goal="Summarize findings for a technical audience",
    backstory="You write clear, concise technical summaries.",
    verbose=False,
)

research_task = Task(
    description="Research how agent memory reduces token costs. Cite one specific example.",
    expected_output="2-sentence summary with a specific cost reduction number.",
    agent=researcher,
)
write_task = Task(
    description="Write a one-paragraph summary of the research findings for a hackathon demo.",
    expected_output="One paragraph, demo-ready.",
    agent=writer,
)

crew = Crew(agents=[researcher, writer], tasks=[research_task, write_task], verbose=False)

start = time.time()
result = crew.kickoff()
print(f"Result: {{result.raw}}")
print(f"Time: {{time.time()-start:.1f}}s | Run again to see memory savings")
""",
                },
                {"path": "requirements.txt", "language": "text", "content": "crewai>=0.67\ncrewai-tools\nretention-sh\n"},
                {"path": "demo.sh", "language": "bash", "content": "#!/bin/bash\necho '=== Run 1 ==='\npython crew.py\necho ''\necho '=== Run 2 (memory) ==='\npython crew.py\n"},
            ],
            "demo_steps": [
                "pip install -r requirements.txt",
                "export OPENAI_API_KEY=sk-...",
                "export SERPER_API_KEY=...  # free at serper.dev",
                "bash demo.sh",
            ],
            "highlight": "track() at line 2 — hooks all CrewAI task and tool calls",
        },
        "pydantic-ai": {
            "project_name": "pydanticai-retention-demo",
            "description": f"PydanticAI agent with retention.sh memory — {uc}",
            "files": [
                {
                    "path": "agent.py",
                    "language": "python",
                    "content": f"""{tag}
from retention_sh import track
track()  # ← hooks PydanticAI agent runs

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIModel
import time

model = OpenAIModel("gpt-4o-mini")
agent = Agent(model, system_prompt="You are a concise assistant. Answer in 1-2 sentences.")

@agent.tool
async def fetch_fact(ctx: RunContext[None], topic: str) -> str:
    "Fetch a quick fact about a topic"
    return f"Key fact about {{topic}}: It demonstrates how structured data reduces re-exploration."

start = time.time()
result = agent.run_sync("Explain how AI agent memory reduces costs. Use the fetch_fact tool.")
print(f"Answer: {{result.data}}")
print(f"Tokens: {{result.usage().total_tokens}}, Time: {{time.time()-start:.1f}}s")
print("Run again — retention.sh replays exploration from memory")
""",
                },
                {"path": "requirements.txt", "language": "text", "content": "pydantic-ai>=0.0.14\nretention-sh\n"},
                {"path": "demo.sh", "language": "bash", "content": "#!/bin/bash\necho '=== Run 1 ==='\npython agent.py\necho ''\necho '=== Run 2 (memory) ==='\npython agent.py\n"},
            ],
            "demo_steps": [
                "pip install -r requirements.txt",
                "export OPENAI_API_KEY=sk-...",
                "bash demo.sh",
            ],
            "highlight": "track() at line 2 — that's the entire retention.sh integration",
        },
        "claude-code": {
            "project_name": "claude-code-retention-demo",
            "description": f"Claude Code + retention.sh — {uc}",
            "files": [
                {
                    "path": "CLAUDE.md",
                    "language": "markdown",
                    "content": f"""{tag.replace('# ', '# ')}

# Project: {uc or 'Retention Demo'}

## What retention.sh does here
Every MCP tool call (ta.crawl_url, ta.qa_check, etc.) is cached after the first run.
Run 2 replays from memory — 85-95% fewer tokens.

## Install
```
curl -sL retention.sh/install.sh | bash
# Restart Claude Code — done
```

## Demo flow
1. Run: `ta.qa_check(url='http://localhost:3000')`
2. Note the token count
3. Run it again: `ta.qa_check(url='http://localhost:3000')`
4. Token count drops 85-95% — that's retention.sh memory

## Check savings
```
ta.savings.compare
```
""",
                },
                {
                    "path": "demo-task.md",
                    "language": "markdown",
                    "content": """# Demo Task

Paste this into Claude Code:

```
1. Run ta.qa_check(url='http://localhost:3000') and note the token count
2. Run the same command again
3. Show me ta.savings.compare
```

The second run should cost 85-95% less. That's the demo.
""",
                },
            ],
            "demo_steps": [
                "curl -sL retention.sh/install.sh | bash",
                "Restart Claude Code",
                "Open CLAUDE.md for context",
                "Paste demo-task.md into Claude Code and run it",
            ],
            "highlight": "No code changes — the hook is installed globally via curl",
        },
        "agents-sdk": {
            "project_name": "agents-sdk-retention-demo",
            "description": f"OpenAI Agents SDK with retention.sh memory — {uc}",
            "files": [
                {
                    "path": "agent.py",
                    "language": "python",
                    "content": f"""{tag}
from retention_sh import track
track()  # ← patches all OpenAI calls — agents, tools, handoffs

from agents import Agent, Runner, function_tool
import time

@function_tool
def search_web(query: str) -> str:
    \"\"\"Search the web for information.\"\"\"
    return f"Search result for: {{query}}"

@function_tool
def analyze_data(data: str) -> str:
    \"\"\"Analyze structured data and return insights.\"\"\"
    return f"Analysis of {{len(data)}} chars: 3 patterns found"

agent = Agent(
    name="research-agent",
    instructions="You are a research assistant. Use tools to gather information, then synthesize.",
    tools=[search_web, analyze_data],
    model="gpt-5.4-mini",
)

start = time.time()
result = Runner.run_sync(agent, "What is retention.sh and how does it help AI agents?")
elapsed = time.time() - start

print(f"Answer: {{result.final_output[:200]}}")
print(f"Time: {{elapsed:.1f}}s")
print("Run again — retention.sh replays tool calls from memory")
""",
                },
                {
                    "path": "requirements.txt",
                    "language": "text",
                    "content": "openai-agents>=0.6\nretention-sh\n",
                },
                {
                    "path": "demo.sh",
                    "language": "bash",
                    "content": "#!/bin/bash\\necho '=== Run 1: Cold start ==='\\npython agent.py\\necho ''\\necho '=== Run 2: Memory replay ==='\\npython agent.py\\n",
                },
            ],
            "demo_steps": [
                "pip install -r requirements.txt",
                "export OPENAI_API_KEY=sk-...",
                "bash demo.sh",
                "Compare token counts between runs",
            ],
            "highlight": "track() at line 2 patches the entire Agents SDK — tools, handoffs, all cached",
        },
        "ml-training": {
            "project_name": "ml-training-retention-demo",
            "description": f"ML fine-tuning pipeline with retention.sh memory — {uc}",
            "files": [
                {
                    "path": "pipeline.py",
                    "language": "python",
                    "content": f"""{tag}
from retention_sh import track, observe
track()  # ← caches data fetching + preprocessing across pipeline runs

import time, json, hashlib

@observe(name="fetch_training_data")
def fetch_training_data(source_url: str) -> list[dict]:
    \"\"\"Fetch and parse training examples from a data source.\"\"\"
    # In production: HTTP fetch + parse. retention.sh caches identical fetches.
    return [
        {{"input": "What is AI?", "output": "AI is..."}},
        {{"input": "Explain LLMs", "output": "LLMs are..."}},
        {{"input": "What are agents?", "output": "Agents use tools..."}},
    ] * 100  # 300 examples

@observe(name="preprocess")
def preprocess(examples: list[dict]) -> list[dict]:
    \"\"\"Tokenize + format for fine-tuning. Deterministic = cacheable.\"\"\"
    return [{{"prompt": e["input"], "completion": e["output"], "tokens": len(e["input"].split())}} for e in examples]

@observe(name="estimate_cost")
def estimate_cost(processed: list[dict], model: str = "meta-llama/Llama-3.2-3B") -> dict:
    \"\"\"Estimate fine-tuning cost for a small LM (<7B params).\"\"\"
    total_tokens = sum(p["tokens"] for p in processed)
    cost_per_1k = 0.008  # typical for 3B model fine-tuning
    return {{
        "model": model,
        "examples": len(processed),
        "total_tokens": total_tokens,
        "estimated_cost": round(total_tokens / 1000 * cost_per_1k, 4),
        "epochs": 3,
    }}

start = time.time()
data = fetch_training_data("https://dataset-source.example.com/v2")
processed = preprocess(data)
cost = estimate_cost(processed)
elapsed = time.time() - start

print(f"Pipeline complete: {{cost['examples']}} examples, {{cost['total_tokens']}} tokens")
print(f"Estimated fine-tuning cost: ${{cost['estimated_cost']}}")
print(f"Pipeline time: {{elapsed:.2f}}s")
print()
print("Run again — fetch + preprocess replayed from retention.sh memory")
""",
                },
                {
                    "path": "requirements.txt",
                    "language": "text",
                    "content": "retention-sh\\n# Add your fine-tuning framework:\\n# transformers\\n# trl\\n# peft\\n",
                },
                {
                    "path": "demo.sh",
                    "language": "bash",
                    "content": "#!/bin/bash\\necho '=== Run 1: Full pipeline ==='\\npython pipeline.py\\necho ''\\necho '=== Run 2: Cached pipeline ==='\\npython pipeline.py\\n",
                },
            ],
            "demo_steps": [
                "pip install -r requirements.txt",
                "bash demo.sh",
                "Note: fetch + preprocess cached on run 2",
                "Show judges the pipeline speedup",
            ],
            "highlight": "@observe() decorator caches any deterministic function — data fetching, preprocessing, cost estimation",
        },
        "mcp-server": {
            "project_name": "mcp-retention-server-demo",
            "description": f"MCP server with retention.sh memory for custom agent harnesses — {uc}",
            "files": [
                {
                    "path": "server.py",
                    "language": "python",
                    "content": f"""{tag}
# retention.sh as an MCP server — your agent connects as a client
# Tool calls get memory at the protocol layer — no tool code changes

from retention_sh import track, observe
track()  # patches MCP tool dispatch

from mcp.server import Server
from mcp.types import Tool, TextContent
import json, time

app = Server("retention-demo")

@app.tool()
async def search_web(query: str) -> list[TextContent]:
    \"\"\"Search the web — results cached by retention.sh on repeat queries.\"\"\"
    # Your real implementation here
    return [TextContent(type="text", text=f"Results for: {{query}}")]

@app.tool()
async def analyze_page(url: str) -> list[TextContent]:
    \"\"\"Analyze a web page — cached when same URL is revisited.\"\"\"
    return [TextContent(type="text", text=f"Analysis of {{url}}: 3 findings")]

@app.tool()
async def generate_report(data: str) -> list[TextContent]:
    \"\"\"Generate a report from data — deterministic input = cached output.\"\"\"
    return [TextContent(type="text", text=f"Report: {{len(data)}} chars analyzed")]

if __name__ == "__main__":
    import asyncio
    from mcp.server.stdio import stdio_server
    asyncio.run(stdio_server(app))
""",
                },
                {
                    "path": "mcp.json",
                    "language": "json",
                    "content": """{
  "mcpServers": {
    "retention-demo": {
      "command": "python",
      "args": ["server.py"],
      "env": {}
    }
  }
}
""",
                },
                {
                    "path": "requirements.txt",
                    "language": "text",
                    "content": "retention-sh\\nmcp>=1.0\\n",
                },
                {
                    "path": "demo.sh",
                    "language": "bash",
                    "content": "#!/bin/bash\\necho '=== MCP Server with retention.sh memory ==='\\necho ''\\necho 'Your agent connects to this server as an MCP client.'\\necho 'Tool calls like search_web, analyze_page get cached automatically.'\\necho ''\\necho 'Run 1: cold — server processes every tool call fresh'\\necho 'Run 2: cached — identical tool calls return from memory'\\necho ''\\necho 'To test: connect Claude Code or your agent to this MCP server'\\necho 'using the mcp.json config, then call the same tools twice.'\\n",
                },
            ],
            "demo_steps": [
                "pip install -r requirements.txt",
                "Add mcp.json to your agent config",
                "Connect your agent (Claude Code, custom harness, etc.)",
                "Call search_web twice with same query — second is from memory",
            ],
            "highlight": "track() at line 5 gives your MCP server memory — every tool call cached at the protocol layer",
        },
    }

    # Shared interactive preview — same for all frameworks
    fw_label = {"langchain": "LangChain", "openai": "OpenAI SDK", "anthropic": "Anthropic SDK",
                "crewai": "CrewAI", "pydantic-ai": "PydanticAI", "claude-code": "Claude Code",
                "agents-sdk": "OpenAI Agents SDK", "ml-training": "ML Training Pipeline",
                "mcp-server": "MCP Server", "rest": "REST API"}.get(framework, framework)
    findings = {
        "langchain": [
            "ConversationBufferMemory re-reads full history each turn (+340 tokens/turn avg)",
            "Tool calls repeat for the same URLs across sessions — no dedup by default",
            "Switching to VectorStoreRetriever + retention.sh: 85-98% token reduction on reruns",
        ],
        "openai": [
            "function_call results are re-fetched every session — no cross-run caching",
            "System prompt + tool schemas cost ~600 tokens per call regardless of novelty",
            "retention.sh skips re-exploration: run 2 pays only for new information",
        ],
        "crewai": [
            "Each Crew.kickoff() re-runs all research tasks from scratch by default",
            "Agent delegation chains average 2,800 tokens/run — 70% is repeated context",
            "Caching task outputs with retention.sh cuts repeat runs to ~180 tokens",
        ],
        "anthropic": [
            "Tool results are not persisted across sessions in the default SDK",
            "Context window re-fills with known state every cold start — wasted tokens",
            "retention.sh injects saved context: warm start costs 31 tokens vs 1,847",
        ],
        "pydantic-ai": [
            "RunContext is rebuilt from scratch on every agent.run() call",
            "Tool decorators execute identically on repeated identical inputs",
            "Memoizing tool results via retention.sh: 98% token reduction on reruns",
        ],
        "claude-code": [
            "Each Claude Code session re-reads CLAUDE.md + project files from scratch",
            "MCP tool calls (crawl, qa_check) re-explore pages already mapped last session",
            "retention.sh hook: session 2 skips re-exploration, uses saved trajectories",
        ],
        "agents-sdk": [
            "Agent.run() re-executes all tool calls from scratch each invocation",
            "Handoff chains repeat identical tool sequences on the same inputs",
            "retention.sh caches tool results: handoff replay costs ~30 tokens vs ~2,800",
        ],
        "ml-training": [
            "Data fetching re-downloads identical datasets on every pipeline run",
            "Preprocessing recomputes tokenization for unchanged examples",
            "retention.sh @observe: fetch + preprocess cached, pipeline runs 10x faster on repeat",
        ],
        "mcp-server": [
            "MCP tool calls re-execute identical operations on every agent session",
            "Search, analysis, and report tools repeat work when inputs haven't changed",
            "retention.sh at the MCP layer: tool results cached, agent gets memory without changing tools",
        ],
    }
    fw_findings = findings.get(framework, findings["langchain"])
    f1, f2, f3 = fw_findings[0], fw_findings[1], fw_findings[2]

    preview_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>retention.sh — {fw_label}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:#09090b;color:#e2e8f0;padding:14px;font-size:12px;overflow-y:auto}}
.header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}}
.title{{font-size:13px;font-weight:700}}.fw{{font-size:10px;color:rgba(255,255,255,0.35)}}
.task{{background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);border-radius:8px;padding:10px 12px;margin-bottom:8px}}
.task-label{{font-size:9px;font-weight:600;letter-spacing:.06em;color:rgba(255,255,255,.3);margin-bottom:3px}}
.task-text{{font-size:11px;color:rgba(255,255,255,.75)}}
.btn{{width:100%;padding:8px;background:#8b5cf6;color:#fff;border:none;border-radius:7px;font-size:11px;font-weight:600;cursor:pointer;transition:background .2s;margin-bottom:8px}}
.btn:hover{{background:#7c3aed}}.btn:disabled{{opacity:.4;cursor:not-allowed}}
.run-block{{border-radius:8px;padding:9px 11px;margin-bottom:6px;display:none}}
.run-block.r1{{background:rgba(248,113,113,.05);border:1px solid rgba(248,113,113,.15)}}
.run-block.r2{{background:rgba(34,197,94,.05);border:1px solid rgba(34,197,94,.18)}}
.run-block.r3{{background:rgba(251,191,36,.05);border:1px solid rgba(251,191,36,.18)}}
.run-head{{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}}
.run-label{{font-size:8px;font-weight:700;letter-spacing:.07em}}
.r1 .run-label{{color:rgba(248,113,113,.7)}}.r2 .run-label{{color:rgba(74,222,128,.7)}}.r3 .run-label{{color:rgba(251,191,36,.7)}}
.cost-pill{{font-size:8px;padding:2px 6px;border-radius:99px;font-weight:600}}
.r1 .cost-pill{{background:rgba(248,113,113,.1);color:#f87171}}.r2 .cost-pill{{background:rgba(34,197,94,.1);color:#4ade80}}.r3 .cost-pill{{background:rgba(251,191,36,.1);color:#fbbf24}}
.steps{{font-family:monospace;font-size:9px;color:rgba(255,255,255,.35);line-height:1.7;margin-bottom:6px;min-height:0}}
.step{{animation:fi .2s ease}}
@keyframes fi{{from{{opacity:0;transform:translateY(2px)}}to{{opacity:1}}}}
.findings{{background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.06);border-radius:6px;padding:7px 9px}}
.findings-label{{font-size:8px;font-weight:600;letter-spacing:.06em;color:rgba(255,255,255,.25);margin-bottom:5px}}
.finding{{display:flex;gap:5px;font-size:9px;color:rgba(255,255,255,.65);line-height:1.4;margin-bottom:3px}}
.finding .dot{{color:#8b5cf6;flex-shrink:0}}.r2 .finding .dot{{color:#4ade80}}.r3 .finding .dot{{color:#fbbf24}}
.finding .check{{color:#4ade80;flex-shrink:0}}
.from-mem{{font-size:8px;color:rgba(74,222,128,.6);margin-bottom:4px;font-family:monospace}}
.diverge-note{{font-size:8px;color:rgba(251,191,36,.6);margin-bottom:4px;font-family:monospace}}
.conf{{display:inline-flex;align-items:center;gap:4px;font-size:8px;font-family:monospace;padding:2px 6px;border-radius:4px;margin-bottom:4px}}
.conf.high{{background:rgba(34,197,94,.08);color:#4ade80;border:1px solid rgba(34,197,94,.15)}}
.conf.low{{background:rgba(251,191,36,.08);color:#fbbf24;border:1px solid rgba(251,191,36,.15)}}
.compare{{background:rgba(34,197,94,.07);border:1px solid rgba(34,197,94,.2);border-radius:7px;padding:8px 10px;display:none;margin-bottom:4px}}
.compare-row{{display:flex;align-items:center;gap:6px;font-size:9px;flex-wrap:wrap}}
.cval{{font-size:14px;font-weight:800;line-height:1}}
.cdesc{{color:rgba(255,255,255,.4);font-size:9px;margin-top:1px}}
.arrow{{color:rgba(34,197,94,.5);font-size:14px}}
.summary{{background:rgba(139,92,246,.06);border:1px solid rgba(139,92,246,.15);border-radius:7px;padding:8px 10px;display:none;margin-top:4px}}
.summary-label{{font-size:8px;font-weight:700;letter-spacing:.06em;color:rgba(139,92,246,.5);margin-bottom:4px}}
.summary-text{{font-size:9px;color:rgba(255,255,255,.55);line-height:1.5}}
</style>
</head>
<body>
<div class="header">
  <div><div class="title">retention.sh</div><div class="fw">{fw_label} agent</div></div>
</div>
<div class="task">
  <div class="task-label">TASK</div>
  <div class="task-text">Analyze {fw_label} memory patterns &mdash; find what costs the most tokens</div>
</div>
<button class="btn" id="btn">&#9654; Run agent</button>

<!-- RUN 1: Cold start -->
<div class="run-block r1" id="r1">
  <div class="run-head">
    <span class="run-label">RUN 1 &mdash; COLD START</span>
    <span class="cost-pill" id="c1pill">&mdash;</span>
  </div>
  <div class="steps" id="steps1"></div>
  <div class="findings" id="f1box" style="display:none">
    <div class="findings-label">FINDINGS</div>
    <div class="finding"><span class="dot">&#9670;</span><span>{f1}</span></div>
    <div class="finding"><span class="dot">&#9670;</span><span>{f2}</span></div>
    <div class="finding"><span class="dot">&#9670;</span><span>{f3}</span></div>
  </div>
</div>

<!-- RUN 2: Memory replay (same task) -->
<div class="run-block r2" id="r2">
  <div class="run-head">
    <span class="run-label">RUN 2 &mdash; MEMORY REPLAY</span>
    <span class="cost-pill" id="c2pill">&mdash;</span>
  </div>
  <div class="conf high" id="conf2" style="display:none">&#9679; fingerprint match: 97% &rarr; replay</div>
  <div class="from-mem" id="frommem" style="display:none">&#8627; [retention.sh] replaying from memory&hellip;</div>
  <div class="findings" id="f2box" style="display:none">
    <div class="findings-label">SAME FINDINGS &mdash; FROM MEMORY</div>
    <div class="finding"><span class="check">&#10003;</span><span>{f1}</span></div>
    <div class="finding"><span class="check">&#10003;</span><span>{f2}</span></div>
    <div class="finding"><span class="check">&#10003;</span><span>{f3}</span></div>
  </div>
</div>

<!-- RUN 3: Divergent task — fallback -->
<div class="run-block r3" id="r3">
  <div class="run-head">
    <span class="run-label">RUN 3 &mdash; DIVERGENCE DETECTED</span>
    <span class="cost-pill" id="c3pill">&mdash;</span>
  </div>
  <div class="conf low" id="conf3" style="display:none">&#9679; fingerprint match: 41% &rarr; dynamic fallback</div>
  <div class="diverge-note" id="divnote" style="display:none">&#8627; [retention.sh] task changed &mdash; falling back to dynamic execution (partial cache)</div>
  <div class="steps" id="steps3"></div>
  <div class="findings" id="f3box" style="display:none">
    <div class="findings-label">NEW FINDINGS &mdash; DYNAMIC + PARTIAL CACHE</div>
    <div class="finding"><span class="check">&#10003;</span><span>{f1}</span></div>
    <div class="finding"><span class="dot">&#9670;</span><span>New pattern: task parameters changed, triggered fresh exploration for modified steps</span></div>
    <div class="finding"><span class="check">&#10003;</span><span>{f3}</span></div>
  </div>
</div>

<!-- Compare -->
<div class="compare" id="cmp">
  <div class="compare-row">
    <div><div class="cval" style="color:#f87171">1,847</div><div class="cdesc">run 1 (cold)</div></div>
    <div class="arrow">&#8594;</div>
    <div><div class="cval" style="color:#4ade80">31</div><div class="cdesc">run 2 (replay)</div></div>
    <div class="arrow">&#8594;</div>
    <div><div class="cval" style="color:#fbbf24">624</div><div class="cdesc">run 3 (diverge)</div></div>
  </div>
</div>

<!-- Summary -->
<div class="summary" id="sum">
  <div class="summary-label">HOW IT WORKS</div>
  <div class="summary-text">
    <strong>Same task?</strong> 98% cached, 31 tokens.<br>
    <strong>Task changed?</strong> Detects divergence, falls back to dynamic. Still 66% cheaper than cold start &mdash; reuses what it can, re-explores only what changed.<br>
    <strong>Never silent failures.</strong> If confidence is low, retention.sh lets the agent think fresh instead of replaying stale actions.
  </div>
</div>

<script>
var btn=document.getElementById('btn'),rc=0;
function step(id,txt){{var d=document.createElement('div');d.className='step';d.textContent=txt;document.getElementById(id).appendChild(d);}}
function sl(ms){{return new Promise(function(r){{setTimeout(r,ms);}});}}
function show(id){{document.getElementById(id).style.display='';}}
btn.addEventListener('click',async function(){{
  rc++;btn.disabled=true;
  if(rc===1){{
    show('r1');
    step('steps1','\\u21b3 Loading {fw_label} session context...');await sl(320);
    step('steps1','\\u21b3 Calling analysis tools (3 of 3)...');await sl(380);
    step('steps1','\\u21b3 Writing findings...');await sl(340);
    document.getElementById('steps1').innerHTML='';
    document.getElementById('c1pill').textContent='1,847 tokens \\u00b7 $0.009 \\u00b7 1.2s';
    show('f1box');
    await sl(500);
    btn.textContent='\\u25b6 Run again \\u2192 memory replay';btn.disabled=false;
  }}else if(rc===2){{
    show('r2');show('conf2');await sl(150);
    show('frommem');await sl(150);
    document.getElementById('c2pill').textContent='31 tokens \\u00b7 $0.0002 \\u00b7 0.06s';
    show('f2box');await sl(300);
    btn.textContent='\\u25b6 Change task \\u2192 see divergence handling';btn.disabled=false;
  }}else if(rc===3){{
    show('r3');show('conf3');await sl(200);
    show('divnote');await sl(200);
    step('steps3','\\u21b3 Reusing cached context (2 of 3 steps)...');await sl(280);
    step('steps3','\\u21b3 Re-exploring modified step dynamically...');await sl(350);
    step('steps3','\\u21b3 Merging results...');await sl(250);
    document.getElementById('steps3').innerHTML='';
    document.getElementById('c3pill').textContent='624 tokens \\u00b7 $0.003 \\u00b7 0.4s';
    show('f3box');await sl(300);
    show('cmp');show('sum');
    btn.textContent='\\u25b6 Run again';btn.disabled=false;
  }}else{{
    btn.textContent='\\u25b6 Demo complete \\u2014 see code tabs for implementation';btn.disabled=true;
    await sl(200);btn.disabled=false;rc=0;
    btn.textContent='\\u25b6 Run agent';
  }}
}});
</script>
</body></html>"""

    scaffold = templates.get(framework, templates["langchain"])
    if hackathon:
        scaffold["description"] = f"[HACKATHON] {scaffold['description']}"
    # Append live preview HTML to every scaffold
    scaffold["files"] = list(scaffold["files"]) + [
        {"path": "index.html", "language": "html", "content": preview_html}
    ]
    return scaffold


async def _execute_tool(name: str, args: dict[str, Any]) -> str:
    """Execute a playground tool and return a text summary of results."""
    if name == "scaffold_project":
        fw = args.get("framework", "langchain")
        use_case = args.get("use_case", "")
        hackathon = bool(args.get("hackathon", False))
        scaffold = _generate_scaffold(fw, use_case, hackathon)
        _SCAFFOLD_CACHE["latest"] = scaffold
        files_summary = ", ".join(f["path"] for f in scaffold["files"])
        return (
            f"Generated project '{scaffold['project_name']}': {scaffold['description']}\n\n"
            f"Files: {files_summary}\n"
            f"Highlight: {scaffold['highlight']}\n\n"
            f"Demo steps:\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(scaffold["demo_steps"]))
        )

    if name == "crawl_url":
        url = args.get("url", "")
        if not url:
            return "No URL provided."
        try:
            req = CrawlRequest(url=url)
            result = await playground_crawl(req)
            errors = len(result.get("console_errors", []))
            a11y = len(result.get("a11y_issues", []))
            broken = len(result.get("broken_links", []))
            title = result.get("title", url)
            return (
                f"Crawled: {title} ({url})\n"
                f"- JS errors: {errors}\n"
                f"- A11y issues: {a11y}\n"
                f"- Broken links: {broken}\n"
                + (f"- Errors: {result['console_errors'][:3]}" if errors else "- No JS errors found")
            )
        except Exception as e:
            return f"Crawl failed: {e}"

    if name == "generate_integration_code":
        fw = args.get("framework", "claude-code")
        data = _INTEGRATION_CODE.get(fw, _INTEGRATION_CODE["claude-code"])
        use_case = args.get("use_case", "your agent")
        return (
            f"Integration code for {fw} ({use_case}):\n\n"
            f"**Install:**\n```\n{data['install']}\n```\n\n"
            f"**Integrate:**\n```python\n{data['integrate']}\n```\n\n"
            f"**Run:**\n```\n{data['demo_cmd']}\n```"
        )

    if name == "generate_demo_script":
        fw = args.get("framework", "claude-code")
        use_case = args.get("use_case", "AI agent")
        hours = int(args.get("hours_left", 8))
        data = _INTEGRATION_CODE.get(fw, _INTEGRATION_CODE["claude-code"])
        return (
            f"2-minute demo script for {fw} ({use_case}, {hours}h left):\n\n"
            f"**INTRO:** 'Your agent re-explores from scratch every run - burning tokens and time. Watch what changes.'\n\n"
            f"**STEP 1 (without memory):** Run `{data['demo_cmd']}` - expected ~$0.08-0.15, 15-30s\n\n"
            f"**STEP 2 (with memory):** Run same command again - expected ~$0.003-0.01, 2-4s (85-95% savings)\n\n"
            f"**CLOSE:** 'One install, permanent memory. Every rerun is near-free.'"
        )

    # ── QA pipeline tools ──────────────────────────────────────────────
    if name == "run_web_qa":
        url = args.get("url", "")
        if not url:
            return "No URL provided. Please give me the full URL (e.g. http://localhost:3000)."
        app_name = args.get("app_name", "Web App")
        try:
            from .mcp_pipeline import dispatch_qa_verification
            result = await dispatch_qa_verification("ta.run_web_flow", {
                "url": url, "app_name": app_name, "mode": "playwright",
            })
            if isinstance(result, dict):
                if result.get("error"):
                    return f"QA pipeline error: {result['error']}"
                if result.get("status") == "setup_required":
                    return f"Setup needed: {result.get('message', 'Playwright not available')}. {' '.join(result.get('next_steps', []))}"
                run_id = result.get("run_id", "")
                if run_id:
                    _QA_RUN_CACHE["latest_qa"] = {"run_id": run_id, "type": "web"}
                    return f"QA pipeline started (run_id: {run_id}). The QA tab will show live progress — crawling pages, discovering workflows, generating and running test cases."
            return f"QA pipeline returned unexpected result: {str(result)[:200]}"
        except ImportError:
            return "QA pipeline not available — mcp_pipeline module not found."
        except Exception as e:
            return f"Failed to start QA pipeline: {e}"

    if name == "run_mobile_qa":
        package_name = args.get("package_name", "")
        if not package_name:
            return "No package name provided. Give me the Android package (e.g. com.instagram.android)."
        app_name = args.get("app_name", package_name)
        try:
            from .mcp_pipeline import dispatch_qa_verification
            result = await dispatch_qa_verification("ta.run_android_flow", {
                "app_package": package_name, "app_name": app_name,
            })
            if isinstance(result, dict):
                if result.get("error"):
                    return f"Mobile QA error: {result['error']}"
                if result.get("status") == "setup_required":
                    return (
                        "**No Android emulator detected.** Two options:\n\n"
                        "**Option A — Local emulator (if you have Android Studio):**\n"
                        "```bash\n"
                        "# List available AVDs\n"
                        "$ANDROID_HOME/emulator/emulator -list-avds\n"
                        "# Launch one\n"
                        "$ANDROID_HOME/emulator/emulator -avd Pixel_6_API_36 -no-audio &\n"
                        "```\n\n"
                        "**Option B — Relay from your machine (recommended):**\n"
                        "If your emulator is running locally but the playground is hosted, "
                        "expose it via WebSocket relay:\n"
                        "```bash\n"
                        "retention.sh relay start --adb\n"
                        "```\n"
                        "This bridges your local ADB to our backend. Then retry this command.\n\n"
                        "**Option C — Use web QA instead:**\n"
                        "If your app has a web version, I can test that immediately with `run_web_qa` — no emulator needed."
                    )
                run_id = result.get("run_id", "")
                if run_id:
                    _QA_RUN_CACHE["latest_qa"] = {"run_id": run_id, "type": "mobile"}
                    return f"Mobile QA pipeline started (run_id: {run_id}). The QA tab will show live progress."
            return f"Mobile QA returned unexpected result: {str(result)[:200]}"
        except ImportError:
            return "QA pipeline not available — mcp_pipeline module not found."
        except Exception as e:
            return f"Failed to start mobile QA: {e}"

    if name == "check_qa_status":
        run_id = args.get("run_id", "")
        if not run_id:
            # Try latest
            latest = _QA_RUN_CACHE.get("latest_qa", {})
            run_id = latest.get("run_id", "")
        if not run_id:
            return "No QA run in progress. Start one with run_web_qa or run_mobile_qa."
        try:
            from .mcp_pipeline import dispatch_pipeline
            result = await dispatch_pipeline("ta.pipeline.status", {"run_id": run_id})
            if isinstance(result, dict):
                status = result.get("status", "unknown")
                stage = result.get("current_stage", "")
                events = result.get("event_count", 0)
                return f"QA run {run_id}: {status} — stage: {stage}, events: {events}"
            return f"Status: {result}"
        except Exception as e:
            return f"Failed to check status: {e}"

    return f"Unknown tool: {name}"


# ─── Convert Anthropic tool schema → OpenAI function schema ───────────────

def _anthropic_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert PLAYGROUND_TOOLS (Anthropic format) to OpenAI function calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


_OPENAI_TOOLS = _anthropic_tools_to_openai(PLAYGROUND_TOOLS)


async def _stream_openai_with_tools(
    messages: list[dict],
    forced_tool: str | None = None,
) -> AsyncGenerator[Any, None]:
    """Agentic loop: OpenAI tool use → execute tools → stream final response.

    Args:
        messages: Conversation history.
        forced_tool: If set, forces the model to call this specific tool on the first round.
                     After tool execution, subsequent rounds use "auto".
                     Use "required" to force ANY tool (model picks which one).

    Yields str tokens + dict events (tool_call, tool_result, scaffold_data, qa_started, token_usage).
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return

    _MODEL = "gpt-5.4-mini"
    _INPUT_PRICE = 0.30 / 1_000_000
    _OUTPUT_PRICE = 1.20 / 1_000_000

    conversation: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}] + list(messages)
    total_input = 0
    total_output = 0
    t_start = time.time()

    for _round in range(3):
        # First round: use forced tool choice if specified. After that: auto.
        if _round == 0 and forced_tool:
            if forced_tool == "required":
                tc_param: Any = "required"
            else:
                tc_param = {"type": "function", "function": {"name": forced_tool}}
        else:
            tc_param = "auto"

        logger.info("OpenAI round %d: tool_choice=%s", _round, tc_param)

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    json={
                        "model": _MODEL,
                        "messages": conversation,
                        "tools": _OPENAI_TOOLS,
                        "tool_choice": tc_param,
                        "max_completion_tokens": 2000,
                    },
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )

            if not resp.is_success:
                logger.warning("OpenAI tool call failed: %s %s", resp.status_code, resp.text[:300])
                return

            data = resp.json()
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "stop")

            # Accumulate usage
            usage = data.get("usage", {})
            total_input += usage.get("prompt_tokens", 0)
            total_output += usage.get("completion_tokens", 0)

            tool_calls = msg.get("tool_calls", [])

            if tool_calls:
                # Stream any text content that came before tool calls
                text_content = msg.get("content") or ""
                if text_content:
                    for word in text_content.split(" "):
                        yield word + " "

                # Emit tool_call events
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    tc_name = fn.get("name", "")
                    try:
                        tc_args = json.loads(fn.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        tc_args = {}
                    yield {"type": "tool_call", "name": tc_name, "args": tc_args}

                # Add assistant message with tool calls to conversation
                conversation.append(msg)

                # Execute tools
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    tc_name = fn.get("name", "")
                    try:
                        tc_args = json.loads(fn.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        tc_args = {}

                    _SCAFFOLD_CACHE.pop("latest", None)
                    t_tool = time.time()
                    summary = await _execute_tool(tc_name, tc_args)
                    tool_ms = int((time.time() - t_tool) * 1000)

                    if tc_name == "scaffold_project" and "latest" in _SCAFFOLD_CACHE:
                        yield {"type": "scaffold_data", "scaffold": _SCAFFOLD_CACHE.pop("latest")}
                    if tc_name in ("run_web_qa", "run_mobile_qa") and "latest_qa" in _QA_RUN_CACHE:
                        qa_info = _QA_RUN_CACHE["latest_qa"]
                        yield {"type": "qa_started", "run_id": qa_info["run_id"], "qa_type": qa_info["type"]}

                    yield {"type": "tool_result", "name": tc_name, "summary": summary[:300], "ms": tool_ms}

                    conversation.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": summary,
                    })
                # Continue loop for next LLM turn

            else:
                # Final response — stream word by word
                text = msg.get("content") or ""
                words = text.split(" ")
                for i, word in enumerate(words):
                    yield word + (" " if i < len(words) - 1 else "")

                cost = total_input * _INPUT_PRICE + total_output * _OUTPUT_PRICE
                yield {
                    "type": "token_usage",
                    "model": _MODEL,
                    "provider": "openai",
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "cost_usd": round(cost, 6),
                    "ms": int((time.time() - t_start) * 1000),
                }
                return

        except Exception as e:
            logger.warning("OpenAI tool loop error: %s", e)
            return


async def _stream_anthropic_with_tools(messages: list[dict]) -> AsyncGenerator[Any, None]:
    """Agentic loop: Anthropic tool use → execute tools → stream final response.

    Yields:
    - str tokens (text streaming)
    - dict {"type": "tool_call", "name": ..., "args": ...}
    - dict {"type": "tool_result", "name": ..., "summary": ..., "ms": ...}
    - dict {"type": "scaffold_data", "scaffold": ...}
    - dict {"type": "token_usage", "input_tokens": ..., "output_tokens": ..., "cost_usd": ..., "ms": ...}
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return

    _MODEL = "claude-sonnet-4-5-20251001"
    # Anthropic pricing (per 1M tokens): sonnet-4-5 input=$3, output=$15
    _INPUT_PRICE = 3.0 / 1_000_000
    _OUTPUT_PRICE = 15.0 / 1_000_000

    conversation = list(messages)
    total_input = 0
    total_output = 0
    t_start = time.time()

    # Allow up to 3 tool-use rounds before forcing a final response
    for _round in range(3):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    json={
                        "model": _MODEL,
                        "max_tokens": 2000,
                        "system": SYSTEM_PROMPT,
                        "messages": conversation,
                        "tools": PLAYGROUND_TOOLS,
                        "tool_choice": {"type": "auto"},
                    },
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                )

            if not resp.is_success:
                logger.debug("Anthropic tool call failed: %s %s", resp.status_code, resp.text[:200])
                return

            data = resp.json()
            stop_reason = data.get("stop_reason", "end_turn")
            content_blocks = data.get("content", [])

            # Accumulate token usage
            usage = data.get("usage", {})
            total_input += usage.get("input_tokens", 0)
            total_output += usage.get("output_tokens", 0)

            if stop_reason == "tool_use":
                assistant_content: list[dict] = []
                tool_calls: list[dict] = []

                for block in content_blocks:
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            assistant_content.append(block)
                            for word in text.split(" "):
                                yield word + " "
                    elif block.get("type") == "tool_use":
                        assistant_content.append(block)
                        tool_calls.append(block)
                        yield {"type": "tool_call", "name": block["name"], "args": block.get("input", {})}

                conversation.append({"role": "assistant", "content": assistant_content})

                tool_results: list[dict] = []
                for tc in tool_calls:
                    _SCAFFOLD_CACHE.pop("latest", None)
                    t_tool = time.time()
                    summary = await _execute_tool(tc["name"], tc.get("input", {}))
                    tool_ms = int((time.time() - t_tool) * 1000)
                    if tc["name"] == "scaffold_project" and "latest" in _SCAFFOLD_CACHE:
                        yield {"type": "scaffold_data", "scaffold": _SCAFFOLD_CACHE.pop("latest")}
                    if tc["name"] in ("run_web_qa", "run_mobile_qa") and "latest_qa" in _QA_RUN_CACHE:
                        qa_info = _QA_RUN_CACHE["latest_qa"]
                        yield {"type": "qa_started", "run_id": qa_info["run_id"], "qa_type": qa_info["type"]}
                    yield {"type": "tool_result", "name": tc["name"], "summary": summary[:300], "ms": tool_ms}
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": summary,
                    })

                conversation.append({"role": "user", "content": tool_results})

            else:
                # Final response — stream word by word
                for block in content_blocks:
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        words = text.split(" ")
                        for i, word in enumerate(words):
                            yield word + (" " if i < len(words) - 1 else "")

                # Emit final token usage
                cost = total_input * _INPUT_PRICE + total_output * _OUTPUT_PRICE
                yield {
                    "type": "token_usage",
                    "model": _MODEL,
                    "provider": "anthropic",
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "cost_usd": round(cost, 6),
                    "ms": int((time.time() - t_start) * 1000),
                }
                return

        except Exception as e:
            logger.debug("Anthropic tool loop error: %s", e)
            return


async def _stream_anthropic(messages: list[dict]) -> AsyncGenerator[str, None]:
    """True token-by-token SSE stream from Anthropic (text only, no tools)."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                "https://api.anthropic.com/v1/messages",
                json={
                    "model": "claude-sonnet-4-5-20251001",
                    "max_tokens": 1500,
                    "system": SYSTEM_PROMPT,
                    "messages": messages,
                    "stream": True,
                },
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        parsed = json.loads(line[6:])
                        if parsed.get("type") == "content_block_delta":
                            delta = parsed.get("delta", {}).get("text", "")
                            if delta:
                                yield delta
                    except (json.JSONDecodeError, KeyError):
                        continue
    except Exception as e:
        logger.debug("Anthropic stream failed: %s", e)


# ─── Models ──────────────────────────────────────────────────────────────

# ─── Runnable demo (no API keys needed) ─────────────────────────────────────

_DEMO_PY = '''\
#!/usr/bin/env python3
"""retention.sh playground demo — runs without API keys."""
import json, time, hashlib, sys
from pathlib import Path

MEMORY_DIR = Path("/tmp/.retention_playground/{session_id}")
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

TASK = "Scan codebase, identify repeated tool patterns, estimate savings"

def run(task):
    key = hashlib.md5(task.encode()).hexdigest()[:12]
    cache = MEMORY_DIR / f"{key}.json"
    t0 = time.perf_counter()
    if cache.exists():
        sys.stdout.write("  [retention.sh] cache hit - replaying from memory\\n"); sys.stdout.flush()
        time.sleep(0.06)
        elapsed = time.perf_counter() - t0
        saved = 1847 - 31
        return dict(tokens=31, time=elapsed, source="memory",
                    result=json.loads(cache.read_text())["result"],
                    saved=saved, saved_pct=round(saved/1847*100))
    for label, delay in [("Loading context", 0.35), ("Scanning patterns", 0.40), ("Summarizing", 0.45)]:
        sys.stdout.write(f"  -> {label}...\\n"); sys.stdout.flush(); time.sleep(delay)
    result = "Found 3 repeated tool patterns. Replay saves ~98% of tokens."
    cache.write_text(json.dumps({"result": result}))
    elapsed = time.perf_counter() - t0
    return dict(tokens=1847, time=elapsed, source="exploration", result=result, saved=0, saved_pct=0)

print(f"Task: {TASK}")
print()
out = run(TASK)
print(f"Result:  {out['result']}")
print(f"Source:  {out['source']}")
print(f"Tokens:  {out['tokens']:,}")
print(f"Time:    {out['time']:.2f}s")
if out["source"] == "exploration":
    print()
    print("Memory saved. Run 2 will replay from cache ->")
else:
    print()
    print(f"Saved:   {out['saved']:,} tokens ({out['saved_pct']}% reduction)")
    print(f"Speedup: {1.2/out['time']:.0f}x faster than first run")
'''

_DEMO_SH = '''\
#!/bin/bash
echo "=================================="
echo "  retention.sh - memory demo"
echo "=================================="
echo ""
echo "-- Run 1: fresh exploration --"
python3 agent_demo.py
echo ""
echo "-- Run 2: memory replay --"
python3 agent_demo.py
'''


class RunRequest(BaseModel):
    session_id: str = "default"


@router.post("/run")
async def playground_run(req: RunRequest):
    """Run the retention.sh demo in a subprocess. Streams stdout line by line."""
    import tempfile

    session_id = req.session_id[:16] or "default"
    demo_py = _DEMO_PY.replace("{session_id}", session_id)

    async def stream():
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "agent_demo.py").write_text(demo_py)
            (Path(tmpdir) / "demo.sh").write_text(_DEMO_SH)

            try:
                proc = await asyncio.create_subprocess_exec(
                    "bash", "demo.sh",
                    cwd=tmpdir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )
                async for raw in proc.stdout:
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    yield f"data: {json.dumps({'output': line})}\n\n"
                code = await proc.wait()
                yield f"data: {json.dumps({'exit_code': code, 'done': True})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'output': f'Error: {e}', 'done': True})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no"},
    )


# ─── Models ──────────────────────────────────────────────────────────────

class CrawlRequest(BaseModel):
    url: str
    depth: int = 2


class AnalyzeRequest(BaseModel):
    jsonl_content: str


class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []
    context: str = ""
    session_id: str = ""


# ─── Endpoints ───────────────────────────────────────────────────────────

@router.get("/status")
async def playground_status():
    has_openai = bool(os.getenv("OPENAI_API_KEY"))
    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))
    has_openrouter = bool(os.getenv("OPENROUTER_API_KEY"))
    return {
        "status": "ok",
        "playground": True,
        "llm_available": has_openai or has_anthropic or has_openrouter,
        "tool_calling": has_openai or has_anthropic,
        "providers": {
            "openai": has_openai,
            "anthropic": has_anthropic,
            "openrouter": has_openrouter,
        },
    }


@router.get("/signals/summary")
async def signals_summary(days: int = 7):
    """Dev-facing aggregation of playground psychographic signals.

    Returns stacks, use cases, pain points, and feature gaps observed
    across playground conversations over the past N days.
    """
    _SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - (days * 86400)

    stacks: dict[str, int] = defaultdict(int)
    use_cases: dict[str, int] = defaultdict(int)
    pains: dict[str, int] = defaultdict(int)
    intents: dict[str, int] = defaultdict(int)
    gaps: list[str] = []
    total_signals = 0

    for f in sorted(_SIGNALS_DIR.glob("*.jsonl")):
        try:
            for line in f.read_text().splitlines():
                record = json.loads(line)
                ts = datetime.fromisoformat(record["ts"])
                if ts.timestamp() < cutoff:
                    continue
                total_signals += 1
                for s in record.get("stacks", []):
                    stacks[s] += 1
                for u in record.get("use_cases", []):
                    use_cases[u] += 1
                for p in record.get("pains", []):
                    pains[p] += 1
                for i in record.get("intents", []):
                    intents[i] += 1
                gaps.extend(record.get("gaps", []))
        except Exception:
            continue

    def _ranked(d: dict[str, int]) -> list[dict]:
        return [{"label": k, "count": v} for k, v in sorted(d.items(), key=lambda x: -x[1])]

    return {
        "days": days,
        "total_signals": total_signals,
        "stacks": _ranked(stacks),
        "use_cases": _ranked(use_cases),
        "pains": _ranked(pains),
        "intents": _ranked(intents),
        "gaps": list(dict.fromkeys(gaps))[:20],  # deduplicated, capped at 20
    }


@router.post("/crawl")
async def playground_crawl(req: CrawlRequest):
    """Crawl a URL with Playwright and return QA findings."""
    import time as _time
    url = req.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    start = _time.time()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail="Playwright not available. Install: pip install playwright && playwright install chromium",
        )

    findings: dict[str, Any] = {
        "url": url,
        "title": "",
        "pages_found": 0,
        "console_errors": [],
        "broken_links": [],
        "a11y_issues": [],
        "screens": [],
    }

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            console_errors: list[str] = []
            page.on(
                "console",
                lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
            )

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            findings["title"] = await page.title()
            findings["console_errors"] = console_errors[:20]
            findings["pages_found"] = 1

            a11y_issues: list[str] = []
            imgs_no_alt = await page.query_selector_all("img:not([alt])")
            if imgs_no_alt:
                a11y_issues.append(f"{len(imgs_no_alt)} image(s) missing alt text")

            buttons_no_label = await page.eval_on_selector_all(
                "button",
                """els => els.filter(el =>
                    !el.textContent?.trim() &&
                    !el.getAttribute('aria-label') &&
                    !el.getAttribute('title')
                ).length""",
            )
            if buttons_no_label > 0:
                a11y_issues.append(f"{buttons_no_label} button(s) missing accessible label")
            findings["a11y_issues"] = a11y_issues

            links = await page.eval_on_selector_all(
                "a[href]", "els => els.slice(0, 10).map(el => el.href)"
            )
            broken: list[str] = []
            for link in links:
                if link.startswith("javascript:") or link.startswith("#"):
                    continue
                try:
                    resp = await page.request.head(link, timeout=5000)
                    if resp.status >= 400:
                        broken.append(f"{link} ({resp.status})")
                except Exception:
                    broken.append(f"{link} (timeout)")
            findings["broken_links"] = broken

            await browser.close()
    except Exception as e:
        logger.error(f"Playground crawl failed: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))

    findings["duration_seconds"] = round(time.time() - start, 1)
    return findings


@router.post("/chat/stream")
async def playground_chat_stream(req: ChatRequest):
    """True token-by-token SSE streaming chat.

    Architecture:
    1. Try Anthropic (Claude) first if key available — highest quality
    2. Fall back to OpenRouter (Llama-4-maverick free) — still real streaming
    3. True last resort: offline deterministic responses (no LLM available at all)

    Both LLM paths stream tokens as they arrive — same feel as Claude.ai or ChatGPT.
    Signal extraction runs silently in background without blocking the stream.
    """
    session_id = req.session_id or "anon"

    limit_msg = _check_rate_limit(session_id)
    if limit_msg:
        async def _rate_limited():
            yield f"data: {json.dumps({'content': limit_msg})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(_rate_limited(), media_type="text/event-stream")

    # Extract + persist signals silently — never blocks the stream
    signals = _extract_signals(req.message, req.history)
    try:
        _persist_signal(session_id, signals, req.message)
    except Exception:
        pass

    # Build conversation for LLM
    llm_messages: list[dict] = []
    for msg in req.history[-12:]:
        role = msg.get("role", "user")
        if role in ("user", "assistant"):
            llm_messages.append({"role": role, "content": msg.get("content", "")})

    user_content = req.message
    if req.context:
        user_content += f"\n\n[Context from analysis: {req.context}]"
    llm_messages.append({"role": "user", "content": user_content})

    # Tab switch hints (before streaming starts so frontend can react immediately)
    lower = req.message.lower()
    wants_analyze = any(p in lower for p in ["paste my", "analyze my", "show me my", "my jsonl", "my logs"])
    wants_crawl = any(p in lower for p in ["crawl my", "crawl a url", "crawl this", "check my site", "health check"])

    # QA intent detection
    import re as _re_mod
    wants_qa = any(p in lower for p in [
        "test my app", "qa my", "run tests", "find bugs", "full qa", "run qa",
        "test my site", "check my app", "test my web",
    ])
    wants_mobile = bool(_re_mod.search(r'com\.\w+\.\w+', lower)) or any(
        p in lower for p in ["android app", "mobile app", "native app", "apk", "test my android"]
    )

    # Pre-LLM scaffold detection
    _SCAFFOLD_PHRASES = [
        "don't know what to build", "dont know what to build", "idk what to build",
        "what should i build", "no idea what to build", "not sure what to make",
        "give me an idea", "what can i build", "what do i build", "help me build",
        "dont know", "don't know", "no idea", "not sure what",
    ]
    _HACKATHON_PHRASES = ["hackathon", "hack ", "judges", "demo", "prize", "hours left", "time pressure"]
    wants_scaffold = any(p in lower for p in _SCAFFOLD_PHRASES)
    is_hackathon = any(p in lower for p in _HACKATHON_PHRASES)
    # Also check recent history
    history_text = " ".join(m.get("content", "") for m in req.history[-4:]).lower()
    if not wants_scaffold:
        wants_scaffold = any(p in history_text for p in _SCAFFOLD_PHRASES)
    if not is_hackathon:
        is_hackathon = any(p in history_text for p in _HACKATHON_PHRASES)

    # Resolve framework
    _FRAMEWORK_MAP = {
        "agents-sdk": "agents-sdk",
        "ml-training": "ml-training",
        "mcp-server": "mcp-server",
        "langchain": "langchain", "langgraph": "langchain",
        "openai-sdk": "openai", "openai": "openai",
        "anthropic-sdk": "anthropic", "anthropic": "anthropic",
        "crewai": "crewai", "crew": "crewai",
        "pydantic-ai": "pydantic-ai",
        "claude-code": "claude-code",
    }
    resolved_fw = next(
        (_FRAMEWORK_MAP[s] for s in signals["stacks"] if s in _FRAMEWORK_MAP), None,
    )
    if not resolved_fw:
        for kw, fw in [("mcp server", "mcp-server"), ("mcp tool", "mcp-server"), ("tool server", "mcp-server"),
                       ("agents sdk", "agents-sdk"), ("openai agents", "agents-sdk"), ("openclaw", "agents-sdk"),
                       ("fine-tun", "ml-training"), ("training pipeline", "ml-training"), ("finetune", "ml-training"),
                       ("langchain", "langchain"), ("openai", "openai"), ("anthropic", "anthropic"),
                       ("crewai", "crewai"), ("pydantic", "pydantic-ai"), ("claude code", "claude-code")]:
            if kw in lower:
                resolved_fw = fw
                break

    # Also scaffold when: hackathon + known framework (even without explicit uncertainty)
    if is_hackathon and resolved_fw and not wants_scaffold:
        wants_scaffold = True

    pre_scaffold: dict | None = None
    if wants_scaffold and resolved_fw:
        pre_scaffold = _generate_scaffold(resolved_fw, req.message[:80], is_hackathon)
        # Tell LLM the scaffold already exists — don't ask for stack again
        llm_messages[-1]["content"] += (
            f"\n\n[System note: I already generated a runnable {resolved_fw} project scaffold for you. "
            f"Reference it in your response — say something like 'I've generated the project files above.' "
            f"Do NOT ask what framework they're using. Do NOT ask permission. Just narrate what was built.]"
        )

    async def generate():
        # ── Agent start — tell frontend which model/provider is active ────────
        tool_names = [t["name"] for t in PLAYGROUND_TOOLS]
        if os.getenv("OPENAI_API_KEY"):
            yield f"data: {json.dumps({'type': 'agent_start', 'model': 'gpt-5.4-mini', 'provider': 'openai', 'tools': tool_names})}\n\n"
        elif os.getenv("ANTHROPIC_API_KEY"):
            yield f"data: {json.dumps({'type': 'agent_start', 'model': 'claude-sonnet-4-5', 'provider': 'anthropic', 'tools': tool_names})}\n\n"
        elif os.getenv("OPENROUTER_API_KEY"):
            yield f"data: {json.dumps({'type': 'agent_start', 'model': _OR_MODELS[0].split('/')[1], 'provider': 'openrouter', 'tools': []})}\n\n"

        # ── Skill selected — pre-detection fired ─────────────────────────────
        if wants_analyze:
            yield f"data: {json.dumps({'switch_tab': 'analyze'})}\n\n"
            yield f"data: {json.dumps({'type': 'skill_selected', 'skill': 'cost_analysis', 'signals': signals['pains'] + signals['intents']})}\n\n"
        elif wants_qa or wants_mobile:
            yield f"data: {json.dumps({'switch_tab': 'qa'})}\n\n"
            skill = 'mobile_qa' if wants_mobile else 'web_qa'
            yield f"data: {json.dumps({'type': 'skill_selected', 'skill': skill, 'signals': signals['use_cases'] + signals['pains']})}\n\n"
        elif wants_crawl:
            yield f"data: {json.dumps({'switch_tab': 'crawl'})}\n\n"
            yield f"data: {json.dumps({'type': 'skill_selected', 'skill': 'crawl_url', 'signals': signals['use_cases']})}\n\n"
        elif pre_scaffold:
            scaffold_signals = []
            if is_hackathon:
                scaffold_signals.append("hackathon")
            if resolved_fw:
                scaffold_signals.append(resolved_fw)
            scaffold_signals += [p for p in signals["pains"] if "visibility" in p or "memory" in p]
            yield f"data: {json.dumps({'type': 'skill_selected', 'skill': 'scaffold_project', 'signals': scaffold_signals})}\n\n"
            # Emit file-by-file build events, then the full scaffold
            for f in pre_scaffold["files"]:
                yield f"data: {json.dumps({'type': 'scaffold_building', 'file': f['path']})}\n\n"
                await asyncio.sleep(0.3)
            yield f"data: {json.dumps({'type': 'scaffold_data', 'scaffold': pre_scaffold})}\n\n"

        has_tokens = False

        def _sse(event: Any) -> str:
            """Format a token or dict event as SSE data line."""
            if isinstance(event, str):
                return f"data: {json.dumps({'content': event})}\n\n"
            elif isinstance(event, dict):
                return f"data: {json.dumps(event)}\n\n"
            return ""

        # ── Intent → forced tool routing ─────────────────────────────
        # When we detect clear intent, force the model to call the right tool
        # instead of hoping it picks it up from the system prompt.
        _forced_tool: str | None = None
        _wants_integrate = any(p in lower for p in [
            "integrate", "how do i add", "set up retention", "install retention",
            "add retention", "wire retention", "hook up retention",
        ])
        if wants_qa or wants_mobile:
            _forced_tool = "run_web_qa" if not wants_mobile else "run_mobile_qa"
        elif _wants_integrate and resolved_fw:
            _forced_tool = "generate_integration_code"
        elif wants_scaffold and not pre_scaffold:
            # Scaffold wanted but no framework resolved yet — force scaffold tool
            _forced_tool = "scaffold_project"

        # Debug: emit routing decision
        if _forced_tool or _wants_integrate:
            logger.info("ROUTING: forced_tool=%s wants_integrate=%s resolved_fw=%s", _forced_tool, _wants_integrate, resolved_fw)

        # Path 1: OpenAI with tools (primary — key exists in .env, gpt-5.4-mini)
        if os.getenv("OPENAI_API_KEY"):
            async for event in _stream_openai_with_tools(llm_messages, forced_tool=_forced_tool):
                has_tokens = True
                yield _sse(event)

        # Path 2: Anthropic with tools (secondary)
        elif os.getenv("ANTHROPIC_API_KEY"):
            async for event in _stream_anthropic_with_tools(llm_messages):
                has_tokens = True
                yield _sse(event)

        # Path 3: OpenRouter free models (text only, no tools)
        elif os.getenv("OPENROUTER_API_KEY"):
            or_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + llm_messages
            async for event in _stream_openrouter(or_messages):
                has_tokens = True
                yield _sse(event)

        # Path 4: Offline fallback — only if ALL paths produced nothing
        if not has_tokens:
            fallback = _offline_response(req.message, req.context, signals)
            words = fallback.split(" ")
            for i, word in enumerate(words):
                chunk = word + (" " if i < len(words) - 1 else "")
                yield f"data: {json.dumps({'content': chunk})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ─── QA status/results endpoints ──────────────────────────────────────────

@router.get("/qa/status/{run_id}")
async def playground_qa_status(run_id: str):
    """Lightweight status for the QA tab to poll."""
    try:
        from .mcp_pipeline import _running_pipelines
    except ImportError:
        return {"error": "Pipeline module not available"}

    entry = _running_pipelines.get(run_id)
    if not entry:
        # Check local cache (demo mode)
        if run_id in _QA_RUN_CACHE:
            return _QA_RUN_CACHE[run_id]
        return {"error": "Unknown run_id", "run_id": run_id}

    return {
        "run_id": run_id,
        "status": entry.get("status", "unknown"),
        "current_stage": entry.get("current_stage", ""),
        "progress": entry.get("progress", {}),
        "event_count": len(entry.get("events", [])),
        "started_at": entry.get("started_at", ""),
        "error": entry.get("error"),
        "has_result": entry.get("result") is not None,
        "app_name": entry.get("app_name", ""),
        "app_url": entry.get("app_url", ""),
        "flow_type": entry.get("flow_type", ""),
    }


@router.get("/qa/results/{run_id}")
async def playground_qa_results(run_id: str):
    """Full QA results for a completed run."""
    try:
        from .mcp_pipeline import dispatch_pipeline
        result = await dispatch_pipeline("ta.pipeline.results", {"run_id": run_id})
        return result
    except ImportError:
        return {"error": "Pipeline module not available"}
    except Exception as e:
        return {"error": str(e)}


# ─── Offline fallback ─────────────────────────────────────────────────────

def _offline_response(message: str, context: str, signals: dict[str, Any]) -> str:
    """Discovery-led fallback when no LLM keys are available.

    Priority: scaffold → pain/cost signals → visibility → repetition → stack-specific → generic.
    """
    lower = message.lower()

    # ── Scaffold / "don't know what to build" ────────────────────────────
    if any(p in lower for p in ["don't know what to build", "idk what to build", "what should i build",
                                  "no idea what to build", "not sure what to make", "give me an idea"]):
        if signals["stacks"]:
            fw = signals["stacks"][0].replace("openai-sdk", "openai").replace("anthropic-sdk", "anthropic")
            return (
                f"Check the **Scaffold** tab — I generated a runnable {fw} project with retention.sh already wired in.\n\n"
                f"The files include `agent.py`, `requirements.txt`, and `demo.sh`. Run `bash demo.sh` and it'll show you "
                f"the before/after token count. That's your 2-minute judge demo."
            )
        return (
            "What framework are you using? (LangChain, OpenAI SDK, Anthropic SDK, CrewAI, PydanticAI...)\n\n"
            "Once I know your stack I'll generate a complete runnable project in the **Scaffold** tab — "
            "agent code, requirements, and a demo script that shows the before/after token savings."
        )

    # ── Divergence / reliability questions ────────────────────────────────
    if any(p in lower for p in ["what if", "change", "fail", "stale", "wrong", "diverge",
                                  "nondetermini", "confidence", "reliable", "break"]):
        return (
            "**This is the most important design decision in retention.sh.**\n\n"
            "Every task gets fingerprinted. On rerun, we compute a **confidence score** (0-100%):\n\n"
            "| Confidence | Action | Savings |\n"
            "|------------|--------|---------|\n"
            "| **>85%** | Full replay | 85-98% |\n"
            "| **50-85%** | Partial replay — reuse matching steps, re-explore divergent ones | 40-70% |\n"
            "| **<50%** | Full dynamic execution — agent thinks fresh | 0% (but no damage) |\n\n"
            "**The key guarantee**: retention.sh never silently replays the wrong trajectory. "
            "Low confidence = the agent falls back to dynamic execution. Your workflow works correctly, "
            "even if that run costs more tokens.\n\n"
            "Run the **Preview** demo to see all three cases: replay, partial cache, and divergence fallback."
        )

    # ── Hackathon mode ────────────────────────────────────────────────────
    if any(p in lower for p in ["hackathon", "hack ", "judges", "prize", "hours left"]):
        return (
            "Hackathon mode — fastest path to a working demo:\n\n"
            "**1. Install (60s)**\n```\ncurl -sL retention.sh/install.sh | bash\n```\n\n"
            "**2. Run your agent once** — baseline cost logged.\n\n"
            "**3. Run it again** — memory kicks in, 85–95% cheaper. That's your before/after.\n\n"
            "Tell me your stack and I'll generate the full project in the **Scaffold** tab. "
            "What framework are you using?"
        )

    # ── ROI / savings estimate (before generic cost-pain) ─────────────────
    if any(w in lower for w in ["roi", "justify", "how much would", "how much will", "save us", "worth it", "worth paying"]):
        # Do the math if we have a dollar amount
        import re as _re
        dollar_match = _re.search(r'\$(\d[\d,]*)', lower)
        if dollar_match:
            raw = dollar_match.group(1).replace(',', '')
            try:
                monthly = int(raw)
                low = int(monthly * 0.30)
                high = int(monthly * 0.85)
                net_low = low - 49   # after team plan cost
                return (
                    f"At ${monthly}/month, here's the math:\n\n"
                    f"- **30% savings** (conservative): ${low}/month saved\n"
                    f"- **85% savings** (repetitive agents): ${high}/month saved\n\n"
                    f"Solo tier is **free** — so the first ${low}-{high} in savings costs you nothing.\n"
                    f"Team tier is $49/month — you'd net ${net_low}-{high - 49}/month after the plan cost.\n\n"
                    f"The real number depends on how repetitive your agents are. "
                    f"Paste a JSONL from `~/.claude/projects/` in the **Analyze** tab and I'll show you the exact repetition percentage for your actual usage."
                )
            except ValueError:
                pass
        return (
            "The savings depend on how repetitive your agents are — typical range is 30-85% of token costs.\n\n"
            "Here's the formula: **current spend × repetition rate × 0.85 = monthly savings**\n\n"
            "Paste a JSONL from `~/.claude/projects/` in the **Analyze** tab to get your actual repetition rate. "
            "It takes about 10 seconds and runs entirely in your browser.\n\n"
            "Solo tier is free. Team tier is $49/month. The tool pays for itself on the first few reruns."
        )

    # ── Pain-first: cost / bill / spend ──────────────────────────────────
    if any(w in lower for w in ["bill", "expens", "too much", "3x", "jump", "spike"]):
        stack_hint = ""
        if "claude code" in lower or "claude-code" in lower:
            stack_hint = "\n\nFor Claude Code specifically: every JSONL file at `~/.claude/projects/` holds the full trace. Paste one in the **Analyze** tab and I'll show you exactly which tools are eating your budget."
        elif signals["stacks"]:
            stack_hint = f"\n\nFor {signals['stacks'][0]}, the SDK auto-patch is one line — `from retention_sh import track; track()` — and every call is attributed from that point."
        return (
            "A spike like that usually means one of three things: a new agent running more sessions, a workflow that started looping, or a task that's re-exploring something it already mapped.\n\n"
            "retention.sh shows you the breakdown per session: which tools ran, how many tokens each cost, and which calls were repeats of prior work.\n\n"
            "The fastest way to see it — paste a JSONL file from `~/.claude/projects/` into the **Analyze** tab. You'll see the cost split in about 10 seconds."
            + stack_hint
        )

    # ── Visibility / observability ────────────────────────────────────────
    if any(w in lower for w in ["no idea", "don't know", "can't see", "visibility", "observ", "which project", "who is"]):
        return (
            "No visibility into agent spend is the norm right now — there's no native breakdown by project or team member in Anthropic's billing.\n\n"
            "retention.sh fills that gap: every session gets a tool call trace with costs attributed at the tool level. You can see which project, which agent, which step is expensive.\n\n"
            "How many engineers are running Claude Code on your team? That'll tell us whether the team dashboard or individual install makes more sense for you."
        )

    if any(w in lower for w in ["repeat", "same thing", "again", "re-explore", "start over"]):
        return (
            "Let's figure out exactly where it's going.\n\n"
            "Paste your Claude Code JSONL in the **Analyze** tab — I'll show you the breakdown: "
            "which tools are eating the most tokens, how much is repeated work, and what you'd save with memory.\n\n"
            "Or tell me: what does a typical run cost right now?"
        )

    if any(w in lower for w in ["repeat", "same thing", "again", "re-explore", "start over"]):
        return (
            "That's the exact problem retention.sh was built for.\n\n"
            "When an agent re-explores from scratch every session, the first run is an investment "
            "that gets thrown away. Memory captures that exploration and replays it — so run 2 "
            "costs a fraction of run 1.\n\n"
            "How many times does your agent hit the same app per day?"
        )

    # ── Stack-specific (only when no pain signal present) ────────────────
    if signals["stacks"] and not signals["pains"]:
        stack = signals["stacks"][0]
        stack_msg = {
            "langchain": "LangChain agents burn a lot of tokens on tool selection and chain initialization — that's usually where the waste hides.",
            "crewai": "CrewAI multi-agent setups often repeat the same tool calls across agents in the same crew. retention.sh deduplicates at the crew level.",
            "openai-sdk": "OpenAI agents using function calling tend to re-discover the same state every session. Memory fixes that.",
            "anthropic-sdk": "Claude's tool use is efficient per call, but repeated exploration across sessions is still a major cost driver. That's what memory addresses.",
            "cursor": "Cursor runs are harder to instrument directly, but the MCP proxy path wraps it transparently — no code changes needed.",
            "claude-code": "Claude Code is the easiest integration — one hook in settings.json and every tool call is logged automatically.",
        }.get(stack, f"With {stack}, the PostToolUse hook or MCP proxy path is usually the cleanest integration.")
        return f"{stack_msg}\n\nWhat's the main pain right now — cost, lack of visibility, or repeated work?"

    # Intent: asking how it works (Calculus Made Easy pattern)
    if any(w in lower for w in ["how does", "what is", "what does", "explain"]):
        return (
            "Your agent starts from scratch every run. Same crawl, same discovery, same cost. Every time.\n\n"
            "But here's the thing — the *structure* of what it does is the same. "
            "Which pages to visit, which tools to call, in what order. Only the *data on those pages* changes.\n\n"
            "**retention.sh captures that structure after the first run and replays it on every run after** "
            "— filling in fresh data as it goes.\n\n"
            "- **First run**: ~1,800 tokens (the investment)\n"
            "- **Second run**: ~30 tokens (replayed from memory)\n"
            "- **Same answer. 98% cheaper.**\n\n"
            "And if the task changes enough that the old plan doesn't fit? "
            "It detects that and falls back to fresh execution. Never replays stale actions.\n\n"
            "Try the **Preview** tab to see all three cases — or tell me your stack and I'll generate a project."
        )

    # Intent: install
    if "install" in lower:
        return (
            "One command:\n\n"
            "```\ncurl -sL retention.sh/install.sh | bash\n```\n\n"
            "Restart Claude Code. That's it — every tool call is now logged to `~/.retention/activity.jsonl`.\n\n"
            "For Python agents: `pip install retention-sh` + `from retention_sh import track; track()`"
        )

    # Context from analysis
    if context:
        return (
            f"Based on your logs:\n\n{context}\n\n"
            "The repeated patterns are the waste. Each re-run replays the same exploration — "
            "retention.sh eliminates that by caching the paths after the first run.\n\n"
            "Want to see how much you'd save? Or ready to install?"
        )

    # Opening move: ask what they're building
    if signals["turn"] == 0 or not signals["intents"]:
        return (
            "Hey — what are you building?\n\n"
            "I can help you figure out where your agent costs are coming from, "
            "and whether memory would actually move the needle for your use case."
        )

    return (
        "Tell me more about your setup — what framework, how often the agent runs, "
        "and whether it revisits the same app or URL across sessions. "
        "That'll tell us whether memory would make a big difference for you."
    )


# ─── Integration code snippets ────────────────────────────────────────────

_INTEGRATION_CODE: dict[str, dict[str, str]] = {
    "claude-code": {
        "install": "curl -sL retention.sh/install.sh | bash",
        "integrate": "# Restart Claude Code — hook auto-installed\n# Every MCP tool call is now logged + cached",
        "demo_cmd": "ta.qa_check(url='http://localhost:3000')",
    },
    "langchain": {
        "install": "pip install retention-sh",
        "integrate": "from retention_sh import track\ntrack()  # auto-patches LangChain callbacks",
        "demo_cmd": "chain.invoke({\"input\": \"...\"})",
    },
    "openai": {
        "install": "pip install retention-sh",
        "integrate": "from retention_sh import track\ntrack()  # patches openai.chat.completions.create",
        "demo_cmd": "client.chat.completions.create(...)",
    },
    "anthropic": {
        "install": "pip install retention-sh",
        "integrate": "from retention_sh import track\ntrack()  # patches anthropic.messages.create",
        "demo_cmd": "client.messages.create(...)",
    },
    "crewai": {
        "install": "pip install retention-sh",
        "integrate": "from retention_sh import track\ntrack()  # hooks CrewAI task execution",
        "demo_cmd": "crew.kickoff()",
    },
    "pydantic-ai": {
        "install": "pip install retention-sh",
        "integrate": "from retention_sh import track\ntrack()  # hooks PydanticAI agent runs",
        "demo_cmd": "agent.run(\"...\")",
    },
    "rest": {
        "install": "# No install — HTTP ingest",
        "integrate": "curl -X POST https://retention.sh/api/analytics/ingest \\\n  -H \"Authorization: Bearer $RETENTION_KEY\" \\\n  -d '{\"tool_name\":\"search\",\"input_keys\":[\"query\"],\"duration_ms\":340}'",
        "demo_cmd": "POST /api/analytics/ingest",
    },
}


class DemoScriptRequest(BaseModel):
    stack: str = "claude-code"
    use_case: str = ""
    hours_left: int = 8


@router.post("/demo-script")
async def playground_demo_script(req: DemoScriptRequest):
    """Generate a 2-minute hackathon demo script.

    Uses LLM if available, otherwise returns a deterministic template
    tailored to the stack and use case.
    """
    stack_data = _INTEGRATION_CODE.get(req.stack, _INTEGRATION_CODE["claude-code"])
    use_case = req.use_case.strip() or "AI agent"
    hours_note = f"{req.hours_left} hours left" if req.hours_left <= 6 else "full hackathon day"

    # Try LLM-generated script
    prompt = f"""You are helping a hackathon participant win with a 2-minute demo of retention.sh.

Stack: {req.stack}
What they're building: {use_case}
Time left: {hours_note}

Generate a concise 2-minute demo script with exactly these fields (JSON):
- intro: opening narration (1-2 sentences, conversational, sets up the problem)
- step1_narration: "without retention.sh" narration (1 sentence)
- step1_code: the exact command/code to run (from their stack)
- step1_expected: what happens — costs and time (concise, specific numbers)
- step2_narration: "with retention.sh" narration (1 sentence, show the magic)
- step2_code: same command again + comment showing it's the same
- step2_expected: savings — specific numbers, 85-95% reduction
- closing: 1-sentence close that lands the value prop for judges

Return ONLY valid JSON, no markdown fences."""

    llm_messages = [{"role": "user", "content": prompt}]

    # Try to get LLM response
    llm_text = ""
    async for token in _stream_anthropic(llm_messages):
        llm_text += token
    if not llm_text:
        async for token in _stream_openrouter([
            {"role": "system", "content": (
                "You are a technical writer for retention.sh, a tool that gives AI agents memory. "
                "retention.sh works by caching agent exploration on the first run so every subsequent run "
                "replays from memory instead of re-exploring, saving 85-95% of tokens. "
                "Generate a hackathon demo script as valid JSON only. No markdown, no explanation."
            )},
            {"role": "user", "content": prompt},
        ]):
            llm_text += token

    if llm_text:
        # Strip markdown fences if model added them
        cleaned = llm_text.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.endswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[:-1])
        try:
            parsed = json.loads(cleaned.strip())
            # Sanity check: ensure it has required fields and isn't hallucinated garbage
            required = {"intro", "step1_narration", "step1_code", "step2_narration", "step2_code", "closing"}
            if required.issubset(parsed.keys()) and len(parsed.get("intro", "")) > 10:
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

    # Deterministic fallback
    stack_label = req.stack.replace("-", " ").title()
    return {
        "intro": f'"{use_case.capitalize()} — the problem is every run re-explores from scratch. Watch what happens when we add memory."',
        "step1_narration": f'"Here\'s a cold run — no memory, no cache. {stack_label} has to rediscover everything."',
        "step1_code": stack_data["demo_cmd"],
        "step1_expected": "Expected: ~$0.08–0.15 per run, 15–30s runtime, full exploration every time",
        "step2_narration": '"Same code, second run — retention.sh replayed from memory. Watch the cost."',
        "step2_code": stack_data["demo_cmd"] + "\n# (same code — memory is transparent)",
        "step2_expected": "Expected: ~$0.003–0.01 per run, 2–4s runtime, 85–95% savings vs run 1",
        "closing": f'"One install, permanent memory. Every {hours_note.split()[0]}-hour project ships faster with retention.sh."',
    }
