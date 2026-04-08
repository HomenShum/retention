# Autonomous Agent Platform Playbook — NemoClaw Edition

> **Purpose**: A complete, self-contained instruction set for reconstructing the entire OpenClaw/retention.sh autonomous agent infrastructure on any machine (Windows, macOS, Linux) using NemoClaw (Nemotron models via OpenRouter free tier) or Claude Code.
>
> **How to use this**: Paste this file into your Claude Code or NemoClaw agent:
> *"Read docs/AGENT_INFRASTRUCTURE.md. Self-examine my codebase. Implement every pattern that is missing. Report progress in Slack."*

---

## Table of Contents

1. [Architecture: Model + Runtime + Harness](#architecture-model--runtime--harness)
2. [Stack Comparison: OpenClaw vs NemoClaw](#stack-comparison-openclaw-vs-nemoclaw)
3. [Pattern 1: LLM Intent Classification (Self-Orchestration DNA)](#pattern-1-llm-intent-classification)
4. [Pattern 2: Agent Runner with Tool-Calling Loop](#pattern-2-agent-runner-with-tool-calling-loop)
5. [Pattern 3: Progressive Tool Disclosure](#pattern-3-progressive-tool-disclosure)
6. [Pattern 4: Coordinator → Specialist Handoff](#pattern-4-coordinator--specialist-handoff)
7. [Pattern 5: Parallel Subagent Spawning](#pattern-5-parallel-subagent-spawning)
8. [Pattern 6: Multi-Agent Swarm Deliberation](#pattern-6-multi-agent-swarm-deliberation)
9. [Pattern 7: Workspace Context Awareness](#pattern-7-workspace-context-awareness)
10. [Pattern 8: Communication Channel Integration](#pattern-8-communication-channel-integration)
11. [Pattern 9: Remote Desktop Control via Chat](#pattern-9-remote-desktop-control-via-chat)
12. [Pattern 10: Screenshot & Video Composition](#pattern-10-screenshot--video-composition)
13. [Pattern 11: Codebase CRUD Operations](#pattern-11-codebase-crud-operations)
14. [Pattern 12: QA Pipeline & Benchmarks](#pattern-12-qa-pipeline--benchmarks)
15. [Pattern 13: Safety, Security & Privacy](#pattern-13-safety-security--privacy)
16. [Pattern 14: Self-Healing & Housekeeping](#pattern-14-self-healing--housekeeping)
17. [Pattern 15: Observability & Telemetry](#pattern-15-observability--telemetry)
18. [Pattern 16: Frontend as Agent Interface](#pattern-16-frontend-as-agent-interface)
19. [Pattern 17: State Management (Convex)](#pattern-17-state-management-convex)
20. [Pattern 18: Deployment & Connectivity](#pattern-18-deployment--connectivity)
21. [Environment Variables Reference](#environment-variables-reference)
22. [Cron Schedule Reference](#cron-schedule-reference)
23. [Windows Setup Guide](#windows-setup-guide)
24. [macOS Setup Guide](#macos-setup-guide)
25. [Self-Examination Checklist](#self-examination-checklist)

---

## Architecture: Model + Runtime + Harness

Every autonomous agent system — Claude Code, Manus, OpenClaw, NemoClaw — reduces to three primitives:

```
┌─────────────────────────────────────────────────────┐
│                    HARNESS                           │
│  (orchestration, context, evidence, telemetry)       │
│  ┌───────────────────────────────────────────────┐   │
│  │                 RUNTIME                        │   │
│  │  (execution, permissions, sandbox, tools)      │   │
│  │  ┌─────────────────────────────────────────┐   │   │
│  │  │              MODEL                       │   │   │
│  │  │  (reasoning, generation, classification) │   │   │
│  │  └─────────────────────────────────────────┘   │   │
│  └───────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

**The moat is not the model. The moat is the harness** — context management, evidence collection, orchestration patterns, workspace awareness, and release control.

### What Each Layer Does

| Layer | Responsibility | Our Implementation |
|-------|---------------|-------------------|
| **Model** | Reasoning, generation, classification | Tiered: nano (routing $0.0001) → mini (execution $0.01) → full (synthesis $0.08) |
| **Runtime** | Execution sandbox, file I/O, shell, permissions | FastAPI backend, MCP server, Playwright, Android emulator, Slack API |
| **Harness** | Orchestration, context, memory, evidence, telemetry | Coordinator agent, progressive disclosure, workspace context, ActionSpan clips, spend guards |

---

## Stack Comparison: OpenClaw vs NemoClaw

| Component | OpenClaw (Current) | NemoClaw (Open Alternative) |
|-----------|-------------------|----------------------------|
| **Orchestration Model** | GPT-5.4 | Nemotron 3 Super 120B (via OpenRouter free tier) |
| **Routing Model** | GPT-5.4-nano | Nemotron 3 Super or Mistral Small 3.2 24B (free) |
| **Execution Model** | GPT-5.4-mini | Nemotron 3 Super (free) or Qwen 3 235B (free) |
| **Runtime** | FastAPI + MCP + custom tools | OpenShell (secure sandbox) + MCP |
| **Harness** | Custom registry + runner | LangChain Deep Agents (filesystem tools, subagents, summarization) |
| **Agent Framework** | OpenAI Agents SDK | LangChain/LangGraph or direct API calls |
| **Coding Agent** | Claude Code | Claude Code or Codex CLI |
| **Cost** | ~$0.10/complex query | $0.00 (free tier models) |

### NemoClaw Model Rotation Pool (Free Tier)

```python
NEMOCLAW_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b",      # Primary — fast, accurate
    "mistralai/mistral-small-3.2-24b-instruct:free",  # Fast fallback
    "qwen/qwen3-235b-a22b:free",              # Strong reasoning
    "deepseek/deepseek-r1:free",              # Deep reasoning
    "google/gemma-3-27b-it:free",             # Balanced
]
```

### How NemoClaw Connects

```
Your Claude Code / NemoClaw Agent
        │
        ├── MCP Protocol ──→ retention.sh Backend (localhost:8000)
        │                         ├── /mcp/tools (48 tools)
        │                         ├── /mcp/tools/call
        │                         └── /api/slack/* (cron endpoints)
        │
        ├── OpenRouter API ──→ Free Nemotron/Mistral/Qwen models
        │
        ├── Slack Web API ──→ Channel observer, notifications
        │
        └── Local Tools ──→ Shell, filesystem, screenshot, click
```

---

## Pattern 1: LLM Intent Classification

> **DNA**: LLM classifiers replace regex routing everywhere. Agents self-direct with tools. No hardcoded if/else.

### Why

Regex routing breaks on enriched content. Example: `"transcribe this youtube.com/watch?v=..."` was enriched with the YouTube transcript text, which contained the word "deeper", triggering a false positive on `grep -qiE 'deep sim|go deeper'`.

### Implementation

```python
# POST /api/slack/classify
INTENT_SCHEMA = {
    "direct": "Direct question/request — route to strategy-brief agent",
    "deep_sim": "User EXPLICITLY requests deep simulation/research",
    "transcribe": "User wants YouTube video or audio transcribed",
    "code_review": "User wants code reviewed",
    "build": "User wants something built or implemented",
    "status": "User asks about project/system status",
}

async def classify_intent(message: str, has_youtube_url: bool) -> dict:
    # Layer 1: Heuristic fast-path (obvious cases only)
    if has_youtube_url and any(w in message.lower() for w in ["transcribe", "transcript"]):
        return {"intent": "transcribe", "confidence": 0.95, "method": "heuristic"}

    # Layer 2: LLM classification (nano model, ~50ms, ~$0.0001)
    response = await call_llm(
        model="gpt-5.4-nano",  # or nemotron-3-super for NemoClaw
        prompt=f"Classify this message into one intent: {json.dumps(INTENT_SCHEMA)}\nMessage: {message}",
        max_tokens=50
    )

    # Layer 3: Graceful fallback
    return parse_intent(response) or {"intent": "direct", "confidence": 0.5, "method": "fallback"}
```

### Key Rules
- Classify against the **original user message**, never the enriched/expanded text
- Always have a fallback intent (`direct`)
- Log every classification for drift detection
- Cost: ~$0.0001/classification with nano model, $0.00 with NemoClaw free tier

### For NemoClaw
Replace `gpt-5.4-nano` with any free model via OpenRouter:
```bash
curl -s https://openrouter.ai/api/v1/chat/completions \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -d '{"model":"nvidia/nemotron-3-super-120b-a12b","messages":[...]}'
```

---

## Pattern 2: Agent Runner with Tool-Calling Loop

The core execution loop: model calls tools, tools return results, model decides next action.

### Implementation

```python
class AgentRunner:
    """Tool-calling loop with budget, synthesis deadline, and token tracking."""

    async def run(self, agent, messages, max_turns=1000):
        turn = 0
        while turn < max_turns:
            response = await self.call_model(agent.model, messages, agent.tools)

            if response.has_tool_calls():
                results = await self.execute_tools(response.tool_calls)
                messages.extend(results)
                turn += 1

                # Synthesis nudge at dynamic threshold
                nudge_at = max(int(max_turns * 0.05), 15)
                if turn == max_turns - nudge_at:
                    messages.append(system_msg("You are running low on turns. Begin synthesizing."))
            else:
                return response.text  # Final answer

        # Forced synthesis on turn limit
        return await self.force_synthesis(messages)

    async def force_synthesis(self, messages):
        """Retry with simpler prompt if synthesis fails."""
        try:
            return await self.call_model(self.model, messages + [
                system_msg("Synthesize all findings into a final answer NOW.")
            ])
        except Exception:
            return await self.call_model(self.model, [
                system_msg("Summarize the key findings from this conversation.")
            ] + messages[-10:])  # Last 10 messages only
```

### Key Rules
- `max_turns=1000` not `100` — let agents work
- Dynamic synthesis nudge at `max(max_turns * 0.05, 15)` turns before limit
- Forced synthesis retry with simpler prompt on failure
- Track token usage per turn for spend accounting
- Never hard-timeout benchmarks — just measure time

---

## Pattern 3: Progressive Tool Disclosure

Don't give every agent every tool. Start narrow, expand on demand.

### Implementation

```python
# Skill → tool category mapping
SKILL_TO_CATEGORIES = {
    "financial": ["codebase", "investor_brief"],
    "content": ["codebase", "web_search", "media"],
    "comparison": ["codebase", "web_search", "investor_brief"],
    "codebase": ["codebase"],
    "slack": ["codebase", "slack"],
    "market": ["codebase", "web_search"],
    "full": ["codebase", "investor_brief", "web_search", "slack", "media", "spawn"],
}

# Meta-tool: agent can request more tools mid-run
EXPAND_TOOLS = {
    "name": "expand_tools",
    "description": "Request additional tool categories if current set is insufficient",
    "parameters": {
        "categories": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"}
    }
}

def get_tools_for_skill(skill: str, agent_categories: list[str]) -> list[Tool]:
    """Intersect skill needs with agent permissions."""
    allowed = set(SKILL_TO_CATEGORIES.get(skill, ["codebase"]))
    permitted = allowed & set(agent_categories)
    return [t for t in ALL_TOOLS if t.category in permitted]
```

### 3-Level Loading (Progressive Disclosure Loader)
1. **Level 1**: `metadata.yaml` only — loaded at startup for all skills
2. **Level 2**: Full `SKILL.md` — loaded when skill is matched
3. **Level 3**: Templates + linked files — loaded on demand during execution

---

## Pattern 4: Coordinator → Specialist Handoff

Hierarchical orchestration: one coordinator routes to specialist agents.

### Architecture

```
                    ┌──────────────┐
                    │  Coordinator  │  (gpt-5.4 / nemotron-super)
                    │  Agent        │
                    └──────┬───────┘
                           │ handoff()
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
    ┌──────────────┐ ┌──────────┐ ┌────────────────┐
    │ Search Agent │ │ Device   │ │ Test Generation │
    │              │ │ Testing  │ │ Agent           │
    └──────────────┘ └──────────┘ └────────────────┘
                           │
                    ┌──────┼──────┐
                    ▼      ▼      ▼
              ┌────────┐ ┌────┐ ┌──────────┐
              │Screen  │ │Act │ │Failure   │
              │Classif.│ │Ver.│ │Diagnosis │
              └────────┘ └────┘ └──────────┘
```

### Dynamic Handoff Callbacks

```python
def create_coordinator_agent():
    return Agent(
        name="Coordinator",
        model="gpt-5.4",  # or nemotron for NemoClaw
        tools=[plan_task, get_app_context, get_workspace_context, ...],
        handoffs=[
            handoff(search_agent, is_enabled=search_agent_enabled),
            handoff(device_testing_agent, is_enabled=device_testing_enabled),
            handoff(test_generation_agent, is_enabled=test_generation_enabled),
        ],
    )

def search_agent_enabled(context) -> bool:
    """Only enable search handoff if message mentions search/find/lookup."""
    msg = context.last_message.lower()
    return any(kw in msg for kw in ["search", "find", "lookup", "bug report"])
```

### Key Rules
- Coordinator has direct tools for simple tasks (no handoff needed)
- Specialists have domain-specific tools only
- `is_enabled` callbacks prevent unnecessary handoff menu pollution
- Each specialist can have its own sub-agents (e.g., Device Testing → Screen Classifier)

---

## Pattern 5: Parallel Subagent Spawning

For complex research, spawn multiple sub-investigations concurrently.

### Implementation

```python
# Tool schema
SPAWN_PARALLEL_RESEARCH = {
    "name": "agent.spawn_parallel_research",
    "description": "Run multiple deep-research sub-investigations concurrently",
    "parameters": {
        "questions": {"type": "array", "items": {"type": "string"}},
        "max_per_question": {"type": "integer", "default": 8}
    }
}

# Execution
async def spawn_parallel_research(questions: list[str], max_per_question: int = 8):
    tasks = [
        run_subagent(
            model="gpt-5.4-mini",  # or nemotron for NemoClaw
            tools=["codebase", "web_search"],
            prompt=question,
            max_turns=max_per_question,
        )
        for question in questions
    ]
    results = await asyncio.wait_for(
        asyncio.gather(*tasks, return_exceptions=True),
        timeout=600  # 10 min max for all subagents
    )
    return [r if not isinstance(r, Exception) else str(r) for r in results]
```

### PRD Parser Example (Orchestrator-Worker)
```
PRD Parser Orchestrator
    ├── Criteria Extractor (subagent)
    ├── Story Extractor (subagent)
    ├── Edge Case Analyzer (subagent)
    └── Test Case Generator (subagent)

All run in parallel via asyncio.gather(), results merged by orchestrator.
```

---

## Pattern 6: Multi-Agent Swarm Deliberation

Multiple AI personas discuss strategic topics, simulating a team.

### Implementation

```python
AGENCY_ROLES = [
    {"name": "Strategy Architect", "emoji": "chess_pawn", "focus": "strategic alignment"},
    {"name": "Engineering Lead", "emoji": "gear", "focus": "technical feasibility"},
    {"name": "Growth Analyst", "emoji": "chart_with_upwards_trend", "focus": "market fit"},
    {"name": "Design Steward", "emoji": "art", "focus": "UX coherence"},
    {"name": "Security Auditor", "emoji": "shield", "focus": "risk assessment"},
    {"name": "Ops Coordinator", "emoji": "satellite", "focus": "operational readiness"},
]

DISCUSSION_TOPICS = [
    "What is our strongest competitive advantage right now?",
    "Where are we most vulnerable to disruption?",
    "What should we stop doing?",
    # ... 20 topic templates
]

async def run_swarm_discussion(topic: str, roles: list[dict], rounds: int = 3):
    """Each role contributes one message per round, building on previous."""
    messages = []
    for round_num in range(rounds):
        for role in roles:
            prompt = f"You are {role['name']}. Focus: {role['focus']}.\n"
            prompt += f"Topic: {topic}\nPrevious discussion:\n{format_messages(messages)}\n"
            prompt += "Add your perspective in 2-3 sentences."

            response = await call_llm(model="gpt-5.4-mini", prompt=prompt)
            messages.append({"role": role["name"], "text": response})

    # Synthesize
    synthesis = await call_llm(
        model="gpt-5.4",
        prompt=f"Synthesize this discussion into 3 actionable insights:\n{format_messages(messages)}"
    )
    return {"messages": messages, "synthesis": synthesis}
```

### Dedup (3-Layer)
1. **In-memory cache** — `asyncio.Lock()` + dict keyed by date
2. **Convex state** — persistent store checked if cache misses
3. **Channel scan** — search Slack for existing thread if both miss

### Rate Limit
- Maximum 1 swarm per hour (`_MIN_INTERVAL_SECONDS = 3600`)
- Discussion threads posted to dedicated daily thread (not main channel)

---

## Pattern 7: Workspace Context Awareness

The harness sees the entire workspace, not just the current message.

### Implementation

```python
# Tool: get_workspace_context
async def get_workspace_context(channels: list[str] = None, hours: int = 24):
    """Pull workspace activity as first-class operating context."""
    channels = channels or ["claw-communications", "general"]
    context = {
        "recent_activity": [],
        "active_threads": [],
        "usage_telemetry": get_usage_stats(hours),
    }

    for channel in channels:
        history = await slack.get_channel_history(channel, limit=50)
        context["recent_activity"].extend([
            {"channel": channel, "user": m["user"], "text": m["text"][:200], "ts": m["ts"]}
            for m in history
        ])

        # Find active threads (replies > 2)
        for msg in history:
            if msg.get("reply_count", 0) > 2:
                replies = await slack.get_thread(channel, msg["ts"])
                context["active_threads"].append({
                    "topic": msg["text"][:100],
                    "replies": len(replies),
                    "last_activity": replies[-1]["ts"]
                })

    return context
```

### Why This Matters
The workspace is already an operator surface:
- Command-center posts
- Benchmark/result posts
- Transcript/deep-sim requests
- Human follow-ups in threads

The right move is not "add another model." It is: **make the harness see and compress workspace signal into decision-ready context.**

---

## Pattern 8: Communication Channel Integration

### Slack Observer (Autonomous Polling Loop)

```bash
#!/usr/bin/env bash
# slack-channel-observer.sh — polls Slack, classifies, routes

while true; do
    # 1. Fetch new messages since last check
    messages=$(curl -s "https://slack.com/api/conversations.history?channel=$CHANNEL&oldest=$LAST_TS" \
        -H "Authorization: Bearer $SLACK_BOT_TOKEN")

    for msg in $(echo "$messages" | jq -r '.messages[]'); do
        # 2. Skip bot messages (self-reply guard)
        [[ $(echo "$msg" | jq -r '.bot_id // empty') ]] && continue

        # 3. LLM intent classification (NOT regex)
        intent=$(curl -s -X POST "$BACKEND_URL/api/slack/classify?message=$encoded_msg" \
            -H "Authorization: Bearer $CRON_AUTH_TOKEN" | jq -r '.intent')

        # 4. Route by intent
        case "$intent" in
            deep_sim) trigger_swarm "$msg" ;;
            transcribe) transcribe_youtube "$msg" ;;
            *) query_agent_streaming "strategy-brief" "$msg" ;;
        esac
    done

    sleep 5  # Poll interval
done
```

### Thread Context Enrichment
```bash
# Short follow-ups (<30 chars) carry parent message context
if [ ${#reply_text} -lt 30 ]; then
    parent_text=$(curl -s "conversations.replies?limit=1" | jq -r '.messages[0].text')
    parent_urls=$(echo "$parent_text" | grep -oE 'https?://[^ ]+')
    enriched_reply="$reply_text (referring to: $parent_urls from parent: $parent_text)"
fi
```

### Streaming Agent Responses to Slack

```python
# stream-agent-to-slack.py — updates Slack messages in-place as agent generates
async def stream_to_slack(agent_response_stream, channel, thread_ts):
    message_ts = None
    buffer = ""

    async for chunk in agent_response_stream:
        buffer += chunk
        if len(buffer) > 200 or chunk == "[DONE]":  # Batch updates
            if message_ts is None:
                resp = await slack.post_message(channel, buffer, thread_ts=thread_ts)
                message_ts = resp["ts"]
            else:
                await slack.update_message(channel, message_ts, buffer)

    # Final update with evidence blocks
    await slack.update_message(channel, message_ts, buffer + evidence_footer)
```

### Rate Limiting
- Max 5 replies per 10-minute window
- Exponential backoff on Slack 429/500/502/503 (3 retries)
- Command-word gating: respect per-user "only respond when I say X" settings

### For NemoClaw on Windows
Replace `bash` scripts with PowerShell:
```powershell
# PowerShell equivalent of observer loop
while ($true) {
    $messages = Invoke-RestMethod -Uri "https://slack.com/api/conversations.history?channel=$Channel" `
        -Headers @{Authorization = "Bearer $env:SLACK_BOT_TOKEN"}
    # ... same logic, PowerShell syntax
    Start-Sleep -Seconds 5
}
```

Or use the Python-based observer (cross-platform):
```python
# backend/app/services/slack_monitor.py — works on all platforms
```

---

## Pattern 9: Remote Desktop Control via Chat

Operate any laptop remotely via chat commands. No VNC, no tunnels, no exposed ports.

### Architecture

```
Phone (Slack app)
    │
    ├── Type command in thread ──→ Slack API
    │                                │
    │                                ▼
    │                          Remote Control Daemon
    │                          (polls Slack every 5s)
    │                                │
    │                                ├── screenshot → screencapture/nircmd
    │                                ├── click X Y → cliclick/pyautogui
    │                                ├── type "text" → cliclick/pyautogui
    │                                ├── open App → osascript/PowerShell
    │                                ├── shell cmd → subprocess
    │                                └── claude "prompt" → Claude Code CLI
    │                                │
    │                                ▼
    └── Result + screenshot ◀──── Upload to Slack thread
```

### Intelligent Evidence Strategy

Not every command needs a screenshot. The agent decides:

```python
EVIDENCE_RULES = {
    "none": ["status", "shell ls", "git status"],     # Text-only response
    "after": ["open App", "navigate"],                  # Single screenshot after action
    "slim": ["click", "type", "drag"],                  # Before + after burst (ActionSpan)
    "full": ["demo", "walkthrough", "record"],          # Continuous video recording
}

def classify_evidence(command: str) -> str:
    """LLM classifies what evidence strategy to use."""
    # Heuristic fast-path
    if command.startswith("status") or command.startswith("shell"):
        return "none"
    if command.startswith("demo") or "video" in command:
        return "full"
    # LLM fallback for ambiguous commands
    return llm_classify(command, EVIDENCE_RULES)
```

### macOS Implementation
```bash
# Screenshot
screencapture -x /tmp/screenshot.png

# Click
cliclick c:200,400

# Type
cliclick t:"Hello world"

# Open app
open -a "Visual Studio Code"

# Key combo
cliclick kp:"cmd+space"
```

### Windows Implementation (NemoClaw)
```powershell
# Screenshot (PowerShell)
Add-Type -AssemblyName System.Windows.Forms
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bitmap = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)
$bitmap.Save("$env:TEMP\screenshot.png")

# Alternative: nircmd (lighter)
nircmd savescreenshot "$env:TEMP\screenshot.png"

# Click (pyautogui — cross-platform)
python -c "import pyautogui; pyautogui.click(200, 400)"

# Type
python -c "import pyautogui; pyautogui.typewrite('Hello world', interval=0.02)"

# Open app
Start-Process "code"  # VS Code
Start-Process "chrome" "http://localhost:5173"

# Key combo
python -c "import pyautogui; pyautogui.hotkey('win', 's')"  # Windows search
```

### Windows Dependencies
```powershell
pip install pyautogui Pillow
choco install nircmd ffmpeg
```

### Security
- `REMOTE_CONTROL_USER_ID` env var restricts who can issue commands
- `CRON_AUTH_TOKEN` required on all endpoints
- All actions logged with timestamps
- Bot messages are skipped (self-reply guard)

---

## Pattern 10: Screenshot & Video Composition

### Slim Mode (ActionSpan Burst)
For most tasks — 3 screenshots stitched into a 1-2 second video. ~20-50KB.

```bash
# Capture before state
screencapture -x /tmp/frame_before.png  # macOS
# ... execute action ...
sleep 0.3
screencapture -x /tmp/frame_during.png
# ... action completes ...
screencapture -x /tmp/frame_after.png

# Stitch into video
ffmpeg -y -framerate 2 -i /tmp/frame_%*.png \
    -c:v libx264 -pix_fmt yuv420p -crf 28 \
    /tmp/actionspan.mp4
```

### Full Mode (Continuous Recording)
For demos and walkthroughs — continuous screen capture.

```bash
# macOS
ffmpeg -y -f avfoundation -framerate 10 -capture_cursor 1 \
    -i "2:none" -c:v libx264 -preset ultrafast -crf 28 \
    /tmp/demo.mp4 &
REC_PID=$!

# ... perform actions ...

kill -INT $REC_PID
wait $REC_PID
```

```powershell
# Windows (GDI grab)
ffmpeg -y -f gdigrab -framerate 10 -i desktop `
    -c:v libx264 -preset ultrafast -crf 28 `
    "$env:TEMP\demo.mp4"
# Stop with Ctrl+C or taskkill
```

### Upload to Slack (New API)
```python
# Step 1: Get upload URL
resp = requests.post("https://slack.com/api/files.getUploadURLExternal",
    headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
    data={"filename": "demo.mp4", "length": file_size})
upload_url = resp.json()["upload_url"]
file_id = resp.json()["file_id"]

# Step 2: Upload binary
requests.post(upload_url, files={"file": open(filepath, "rb")})

# Step 3: Complete + share to channel/thread
requests.post("https://slack.com/api/files.completeUploadExternal",
    headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
    json={"files": [{"id": file_id, "title": "Demo"}],
          "channel_id": channel, "thread_ts": thread_ts})
```

---

## Pattern 11: Codebase CRUD Operations

### Tool Allowlist (Security-First)

```python
SHELL_ALLOWLIST = [
    "wc", "sort", "uniq", "head", "tail", "jq", "date", "cal",
    "ls", "cat", "grep", "find", "du", "df", "echo",
    "awk", "sed", "tr", "cut", "paste", "column",
]

SHELL_BLOCKLIST = [
    "rm", "mv", "cp", "chmod", "sudo", "curl", "wget", "ssh", "python",
]

# Timeout: 30 seconds per command
```

### Git Operations (Agent-Safe)

```python
CODEBASE_TOOLS = {
    "recent_commits": "git log --oneline -20",
    "commit_diff": "git diff {commit_hash}",
    "search": "grep -rn {pattern} --include={glob}",
    "read_file": "cat {filepath}",
    "list_directory": "ls -la {path}",
    "file_tree": "find . -type f -not -path './.git/*' | head -200",
    "git_status": "git status --short",
    "write_file": "write content to filepath",
    "run_tests": "cd backend && python -m pytest {path}",
    "create_pull_request": "gh pr create ...",
    "git_commit_and_push": "git add, commit, push",
}
```

### Key Rules
- Never `rm -rf` or `rm -f` without explicit human approval
- Never force push to main
- Never modify golden_bugs.json without approval
- Never commit secrets (.env, credentials)
- Always create branches for changes, never commit directly to main
- Always run tests before committing

---

## Pattern 12: QA Pipeline & Benchmarks

### Pipeline Flow

```
Crawl (discover screens)
  → Workflow Analysis (identify user flows)
    → Test Case Generation (P0 test cases)
      → Execution (run on emulator/Playwright)
        → Results (evidence + verdict)
```

### MCP Tools (48 tools total)

| Category | Key Tools |
|----------|-----------|
| Setup | `ta.system_check`, `ta.setup.status`, `ta.setup.launch_emulator` |
| Pipeline | `ta.run_web_flow`, `ta.run_android_flow`, `ta.pipeline.status`, `ta.pipeline.results` |
| Analysis | `ta.pipeline.failure_bundle`, `ta.suggest_fix_context`, `ta.emit_verdict` |
| Rerun | `ta.rerun`, `ta.pipeline.rerun_failures`, `ta.compare_before_after` |
| Evidence | `ta.collect_trace_bundle`, `ta.pipeline.screenshot` |
| Benchmark | `ta.benchmark.run_suite`, `ta.benchmark.scorecard`, `ta.benchmark.generate_app` |
| NemoClaw | `ta.nemoclaw.run` (autonomous QA via free models) |

### Fix-Verify Loop

```
1. ta.run_web_flow(url) → run_id
2. ta.pipeline.failure_bundle(run_id) → failures + suggested files
3. Fix code based on suggestions
4. ta.rerun(run_id, failures_only=true) → rerun_id
5. ta.compare_before_after(run_id, rerun_id) → delta
```

**Measured performance**: Full run 505s → Rerun 10s = **98% time savings**

### Timeouts
- **Never timeout benchmarks** — just measure how long they take
- Default: 3600s (1 hour) for all pipeline operations
- At Meta, runs went as long as 200 minutes

---

## Pattern 13: Safety, Security & Privacy

### Spend Guard

```python
_daily_spend_usd: float = 0.0
_DAILY_SPEND_LIMIT_USD = 100.0
_spend_lock: asyncio.Lock = asyncio.Lock()

async def check_spend(estimated_cost: float, critical: bool = False) -> bool:
    """Thread-safe daily spend tracking with double-checked locking."""
    if critical:
        return True  # Critical operations bypass spend guard

    async with _spend_lock:
        if _daily_spend_usd + estimated_cost > _DAILY_SPEND_LIMIT_USD:
            logger.warning(f"Daily spend limit reached: ${_daily_spend_usd:.2f}")
            return False
        _daily_spend_usd += estimated_cost
        return True
```

### Rate Limiting

```python
class RateLimitState:
    max_retries: int = 5
    max_delay: float = 120.0  # 2 minutes

    async def wait_with_backoff(self, attempt: int):
        delay = min(2 ** attempt + random.uniform(0, 1), self.max_delay)
        await asyncio.sleep(delay)

class TokenBudget:
    """Track TPM/RPM with 80% safety margin."""
    safety_margin: float = 0.8
```

### Shell Command Security
- Allowlist-only execution (see Pattern 11)
- 30-second timeout per command
- No `sudo`, `rm`, `curl`, `wget`, `ssh`

### MCP Token Auth
```python
async def verify_mcp_token(token: str) -> bool:
    # 1. Check per-user tokens in Convex
    # 2. HMAC constant-time comparison for shared env token
    # 3. Disabled auth fallback in local dev only
```

### Validation Hooks (PR Gating)
```python
# Block external AI agents from merging until TA confirms
class ValidationHook:
    status: Literal["PENDING", "RUNNING", "RELEASED", "BLOCKED"]

    async def check(self, pr_url: str) -> str:
        results = await run_qa_pipeline(pr_url)
        return "RELEASED" if results.pass_rate > 0.8 else "BLOCKED"
```

### Approval Gates
- **ACT IMMEDIATELY** (no permission needed): Launch emulators, run tests, run benchmarks
- **ASK FIRST** (needs human approval): Delete data, modify golden bugs, force-stop pipelines, modify auth/security code

---

## Pattern 14: Self-Healing & Housekeeping

### Health Check (Every 30 Minutes)

```bash
# Check backend
backend_status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/health)
# Check frontend
frontend_status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5173)

if [ "$backend_status" != "200" ] || [ "$frontend_status" != "200" ]; then
    # Attempt restart
    # If restart fails → alert user via Slack + iMessage/SMS
fi
```

### Housekeeping (Every 4 Hours)

```python
async def housekeep_slack():
    """Summarize old verbose messages, delete error messages, retry abandoned."""
    messages = await slack.get_channel_history(channel, limit=100)

    for msg in messages:
        if is_error_message(msg):
            await slack.delete_message(channel, msg["ts"])
        elif is_verbose(msg) and len(msg["text"]) > 2000:
            summary = await llm_summarize(msg["text"], max_tokens=200)
            await slack.update_message(channel, msg["ts"], summary)
        elif is_abandoned_question(msg):
            await retry_question(msg)
```

### Dead Letter Queue

```python
async def _bg(coro, name: str):
    """Fire-and-forget with timeout and error logging."""
    try:
        return await asyncio.wait_for(coro, timeout=1500)  # 25 min
    except Exception as e:
        _log_error_to_file(name, str(e))

def _log_error_to_file(name: str, error: str):
    """Write to /tmp/cron_errors.jsonl as dead letter queue."""
    try:
        with open("/tmp/cron_errors.jsonl", "a") as f:
            f.write(json.dumps({"name": name, "error": error, "ts": time.time()}) + "\n")
    except Exception:
        logger.error(f"Dead letter failed for {name}: {error}")
```

---

## Pattern 15: Observability & Telemetry

### Token Usage Tracking

```python
# usage_telemetry.py
async def record_usage(model: str, input_tokens: int, output_tokens: int, cost_usd: float):
    """Append to rolling usage log for spend tracking and optimization."""
    entry = {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "timestamp": datetime.utcnow().isoformat(),
    }
    # Persist to Convex or local file
```

### LLM Judge (Quality Scoring)

```python
JUDGE_CRITERIA = [
    "completeness",      # Did the answer address all parts?
    "accuracy",          # Are facts correct?
    "actionability",     # Can the user act on this?
    "evidence_quality",  # Are claims supported?
    "conciseness",       # Is it appropriately brief?
]

async def judge_response(question: str, answer: str) -> dict:
    """5-criteria evaluation using gpt-5.4 as judge."""
    prompt = f"""Score this response 1-5 on each criterion:
    {json.dumps(JUDGE_CRITERIA)}
    Question: {question}
    Answer: {answer}
    Return JSON: {{"scores": {{"completeness": N, ...}}, "overall": N, "reasoning": "..."}}"""

    return await call_llm(model="gpt-5.4", prompt=prompt, max_tokens=500)
```

### Lineage Tracking

```python
# lineage.py — track chain of benchmark runs
class BenchmarkLineage:
    def chain(self, run_id: str, parent_run_id: str = None): ...
    def compare(self, run_a: str, run_b: str) -> dict: ...
    def delta(self, baseline: str, current: str) -> dict: ...
```

### ActionSpan Evidence
- 2-3 second verification clips per test step
- ~7x cheaper than full session review
- Stored in Convex with run metadata

---

## Pattern 16: Frontend as Agent Interface

### Key Pages

| Route | Component | Purpose |
|-------|-----------|---------|
| `/demo/agent` | UnifiedAgentPage | Main orchestrator + chat |
| `/demo/ai-chat` | AIAgentChatPage | Conversational QA with device selector |
| `/demo/test-generation` | TestCaseGenerationPage | P0 test case generation |
| `/demo/action-spans` | ActionSpanDashboard | Visual evidence per test step |
| `/demo/benchmarks` | BenchmarkComparisonPage | Model comparison scorecards |
| `/demo/devices` | DeviceControlPage | Emulator management |
| `/cockpit` | CockpitPage | System overview dashboard |
| `/demo/try` | TryDemoPage | Full pipeline: Crawl → Workflow → TestCase → Execution |

### Design System
- Monochrome sand diffusion effect for all pills/badges/cards
- No colored gradients
- Dark theme with accent colors (orange #FF5722, emerald #34d399, blue #60a5fa)

### Code Splitting
```typescript
// vite.config.ts
manualChunks: {
    'vendor-react': ['react', 'react-dom', 'react-router-dom'],
    'vendor-convex': ['convex/react', '@convex-dev/auth/react'],
    'vendor-misc': ['framer-motion', 'lucide-react', 'recharts'],
}
```

---

## Pattern 17: State Management (Convex)

### Key Tables

| Table | Purpose |
|-------|---------|
| `mcpTokens` | Per-user MCP auth tokens (indexed by email) |
| `slackMonitorDecisions` | Logged monitor decisions |
| `slackDigestDecisions` | Logged digest decisions |
| `institutionalMemory` | Extracted knowledge from conversations |
| `benchmarkRuns` | Benchmark result storage |
| `actionSpans` | Verification clip metadata |
| `leads` | Lead tracking from website |
| `demoSessions` | Demo session tracking |

### Backend Client (Singleton Pattern)

```python
class ConvexClient:
    _shared_http_client: httpx.AsyncClient = None

    @classmethod
    def get_shared(cls):
        if cls._shared_http_client is None:
            cls._shared_http_client = httpx.AsyncClient(
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
            )
        return cls._shared_http_client

    def close(self):
        pass  # No-op — shared client stays alive

    @classmethod
    async def close_shared(cls):
        if cls._shared_http_client:
            await cls._shared_http_client.aclose()
```

---

## Pattern 18: Deployment & Connectivity

### Architecture

```
                                    Internet
                                       │
                    ┌──────────────────┼──────────────────┐
                    │                  │                    │
              Vercel (Frontend)   Render (Backend)    Convex (State)
              test-studio-xi      FastAPI + MCP       Real-time DB
              .vercel.app         /api/* /mcp/*        Crons, Auth
                    │                  │                    │
                    └──────────────────┼──────────────────┘
                                       │
                              Cloudflare Tunnel
                              (for local dev → remote emulator)
                                       │
                              Local Machine
                              ├── Android Emulator
                              ├── Slack Observer
                              ├── Remote Control Daemon
                              └── Claude Code / NemoClaw
```

### Tunnel for Local Dev

```bash
# Expose local app to remote emulator
cloudflared tunnel --url http://localhost:3000
# Returns: https://random-words.trycloudflare.com

# Or use MCP tool:
# ta.expose_local_app(port=3000)
```

---

## Environment Variables Reference

### Required (All Platforms)

| Variable | Purpose | Example |
|----------|---------|---------|
| `OPENAI_API_KEY` | OpenAI API access | `sk-...` |
| `SLACK_BOT_TOKEN` | Slack Web API auth | `xoxb-...` |
| `CRON_AUTH_TOKEN` | Internal endpoint auth | `random-uuid` |
| `CONVEX_SITE_URL` | Convex HTTP endpoint | `https://xxx.convex.site` |

### NemoClaw-Specific

| Variable | Purpose | Example |
|----------|---------|---------|
| `OPENROUTER_API_KEY` | OpenRouter API (free tier models) | `sk-or-...` |
| `NVIDIA_API_KEY` | NVIDIA NIM direct access (optional) | `nvapi-...` |
| `LANGSMITH_API_KEY` | LangSmith tracing (optional) | `lsv2_...` |

### Remote Control

| Variable | Purpose | Example |
|----------|---------|---------|
| `REMOTE_CONTROL_USER_ID` | Authorized Slack user ID | `U0ALSPANA1G` |

### Android/Device

| Variable | Purpose | macOS | Windows |
|----------|---------|-------|---------|
| `ANDROID_HOME` | Android SDK path | `~/Library/Android/sdk` | `%LOCALAPPDATA%\Android\Sdk` |

### Optional

| Variable | Purpose |
|----------|---------|
| `RETENTION_MCP_TOKEN` | Shared MCP auth token |
| `GOOGLE_AI_API_KEY` | Gemini vision API |
| `FIGMA_ACCESS_TOKEN` | Figma API access |
| `BACKEND_URL` | Backend API base (default `http://localhost:8000`) |

---

## Cron Schedule Reference

| Name | Schedule | Purpose |
|------|----------|---------|
| Health Check | Every 5 min | Verify deployment |
| Slack Monitor | Every 30 min | Scan for opportunities |
| Slack Digest | Every 1 hour | Summarize activity |
| Slack Swarm | Every 2 hours | Multi-role discussion |
| Slack Housekeeping | Every 4 hours | Clean up old messages |
| Slack Standup | Daily 7AM PT | Standup synthesis |
| Slack Evolve | Daily 6AM PT | Self-improvement review |
| Changelog | Daily 6AM PT | Auto-generate changelog |
| News Scrape | Daily 6AM UTC | Refresh news content |
| Eval Benchmark | Daily 10:30AM PT | Structured judge eval |
| Backup | Daily 10PM PT | Data export |
| Competitive Intel | Monday 8AM UTC | Weekly competitor analysis |
| Swarm Evolve | Monday 9AM PT | Swarm self-improvement |
| Swarm Competitive | Wednesday 9AM PT | Competitive swarm |
| Drift Detection | Friday 10AM PT | Weekly drift check |
| Model Monitor | Sunday 4PM UTC | Model allocation eval |

---

## Windows Setup Guide

### Prerequisites

```powershell
# 1. Install Chocolatey (package manager)
Set-ExecutionPolicy Bypass -Scope Process -Force
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))

# 2. Install dependencies
choco install python nodejs-lts git ffmpeg nircmd -y
pip install pyautogui Pillow httpx uvicorn fastapi websockify

# 3. Install Android SDK
choco install android-sdk -y
# Or download Android Studio: https://developer.android.com/studio

# 4. Install Claude Code (or NemoClaw)
npm install -g @anthropic-ai/claude-code
# Or for NemoClaw:
pip install langchain-openai langgraph

# 5. Set up environment
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
$env:PATH += ";$env:ANDROID_HOME\emulator;$env:ANDROID_HOME\platform-tools"
```

### Create Android Emulator

```powershell
# Install system image
sdkmanager "system-images;android-35;google_apis;x86_64"

# Create AVD
avdmanager create avd -n "test_device" -k "system-images;android-35;google_apis;x86_64" --device "pixel_7"

# Launch
emulator -avd test_device -no-audio -no-boot-anim &
```

### Windows Remote Control Daemon

```powershell
# Save as scripts/remote-control-daemon.ps1
$ErrorActionPreference = "Continue"

# Load env
Get-Content backend\.env | ForEach-Object {
    if ($_ -match '^([^#][^=]+)=(.+)$') {
        [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim())
    }
}

$CHANNEL = $env:CLAW_CHANNEL
$TOKEN = $env:SLACK_BOT_TOKEN
$LAST_TS = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

function Take-Screenshot {
    Add-Type -AssemblyName System.Windows.Forms
    $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bitmap = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)
    $path = "$env:TEMP\openclaw_screenshot_$(Get-Date -Format 'yyyyMMddHHmmss').png"
    $bitmap.Save($path)
    $graphics.Dispose()
    $bitmap.Dispose()
    return $path
}

function Click-At($x, $y) {
    python -c "import pyautogui; pyautogui.click($x, $y)"
}

function Type-Text($text) {
    python -c "import pyautogui; pyautogui.typewrite('$text', interval=0.02)"
}

while ($true) {
    # Poll Slack for new messages in remote control thread
    $headers = @{Authorization = "Bearer $TOKEN"}
    $resp = Invoke-RestMethod -Uri "https://slack.com/api/conversations.replies?channel=$CHANNEL&ts=$THREAD_TS&oldest=$LAST_TS" -Headers $headers

    foreach ($msg in $resp.messages) {
        if ($msg.bot_id) { continue }
        $text = $msg.text

        switch -Regex ($text) {
            '^screenshot' { $path = Take-Screenshot; Upload-ToSlack $path }
            '^click (\d+) (\d+)' { Click-At $matches[1] $matches[2] }
            '^type (.+)' { Type-Text $matches[1] }
            '^shell (.+)' { $output = Invoke-Expression $matches[1]; Post-Reply $output }
            default { Post-Reply "Unknown command: $text" }
        }
    }

    Start-Sleep -Seconds 5
}
```

### NemoClaw Setup

```bash
# Install NemoClaw agent
pip install langchain-openai langgraph

# Or use ta.nemoclaw.run MCP tool:
# It auto-rotates best free model on OpenRouter
```

```python
# nemoclaw_agent.py — minimal NemoClaw setup
import os
from langchain_openai import ChatOpenAI

model = ChatOpenAI(
    model="nvidia/nemotron-3-super-120b-a12b",
    openai_api_base="https://openrouter.ai/api/v1",
    openai_api_key=os.environ["OPENROUTER_API_KEY"],
    temperature=0,
)

# Connect to retention.sh MCP
# POST http://localhost:8000/mcp/tools/call
# Authorization: Bearer $RETENTION_MCP_TOKEN
```

---

## macOS Setup Guide

```bash
# 1. Install Homebrew packages
brew install ffmpeg cliclick cloudflared node python@3.11

# 2. Install Android SDK
brew install --cask android-studio
# Or: brew install android-commandlinetools

# 3. Create emulator
sdkmanager "system-images;android-35;google_apis;arm64-v8a"
avdmanager create avd -n "test_device" -k "system-images;android-35;google_apis;arm64-v8a"

# 4. Install Claude Code
npm install -g @anthropic-ai/claude-code

# 5. Grant permissions
# System Settings → Privacy & Security → Accessibility → add Terminal.app
# System Settings → Privacy & Security → Screen Recording → add Terminal.app

# 6. Start backend
cd backend && pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 7. Start frontend
cd frontend/test-studio && npm install && npm run dev

# 8. Start daemons
nohup bash scripts/slack-channel-observer.sh &
nohup bash scripts/remote-control-daemon.sh &
```

---

## Self-Examination Checklist

Instruct your agent: *"Read this checklist. For each item, search my codebase. Report what exists and what's missing."*

### Model Layer
- [ ] Multi-model tiering (cheap routing → medium execution → expensive synthesis)
- [ ] Model fallback chains (primary → fallback → last resort)
- [ ] Intent classification via LLM (not regex)
- [ ] Spend guard with daily limit
- [ ] Token usage tracking per request

### Runtime Layer
- [ ] MCP server with tool registry
- [ ] Shell command allowlist (not blocklist)
- [ ] File read/write with security boundaries
- [ ] Git operations (commit, push, PR, issue)
- [ ] Android emulator management
- [ ] Playwright/browser automation
- [ ] Screenshot and video capture

### Harness Layer
- [ ] Coordinator → Specialist handoff architecture
- [ ] Progressive tool disclosure (start narrow, expand on demand)
- [ ] Parallel subagent spawning with `asyncio.gather`
- [ ] Workspace context awareness (Slack activity as operating context)
- [ ] Thread context enrichment for short follow-ups
- [ ] Synthesis deadline with forced synthesis retry
- [ ] Dead letter queue for failed background tasks

### Communication Layer
- [ ] Slack channel observer (polling loop)
- [ ] Streaming responses to Slack (update in-place)
- [ ] Thread management (daily threads, discussion threads, progress threads)
- [ ] Rate limiting (5 replies per 10 min)
- [ ] Bot self-reply guard
- [ ] 3-layer dedup (memory → database → channel scan)
- [ ] Notification on errors (Slack + SMS/iMessage)

### Remote Control Layer
- [ ] Screenshot capture (cross-platform)
- [ ] Mouse click/type automation (cross-platform)
- [ ] App launching
- [ ] Shell command execution
- [ ] Intelligent evidence strategy (none/after/slim/full)
- [ ] Video recording for demos

### QA Pipeline Layer
- [ ] Crawl → Workflow → TestCase → Execution → Results
- [ ] Failure bundle (compact, token-efficient)
- [ ] Fix suggestion with real file paths
- [ ] Rerun-after-fix (98% time savings)
- [ ] Before/after comparison
- [ ] Benchmark harness with N-run consistency
- [ ] Golden bug precision/recall/F1

### Safety Layer
- [ ] MCP token authentication
- [ ] Shell command allowlist
- [ ] Daily spend guard ($100/day)
- [ ] Rate limiting with exponential backoff
- [ ] Validation hooks for PR gating
- [ ] Approval gates for destructive operations
- [ ] No secrets in commits

### Observability Layer
- [ ] Token usage telemetry
- [ ] LLM judge scoring (5-criteria)
- [ ] Benchmark lineage tracking
- [ ] ActionSpan evidence clips
- [ ] Cron error logging (dead letter queue)
- [ ] Health checks (every 5 min)

### State Layer
- [ ] Persistent state store (Convex/Supabase/Firebase)
- [ ] Session management
- [ ] Institutional memory extraction
- [ ] Lead tracking
- [ ] MCP token management

### Deployment Layer
- [ ] Frontend deployed (Vercel/Netlify)
- [ ] Backend deployed (Render/Railway/fly.io)
- [ ] Cloudflare Tunnel for local dev
- [ ] CORS configuration
- [ ] Environment variable management

---

> **Final Note**: This playbook documents patterns, not a specific app. The reference implementation is retention.sh, but every pattern applies universally. The key insight from the NemoClaw video: your moat is the harness, not the model. Build the harness well, and you can swap models freely — from GPT-5.4 to Nemotron to whatever comes next.
