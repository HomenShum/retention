# Enterprise SDK Integration Guide

retention.sh is a **verification intelligence layer**, not a device driver. Enterprise teams bring their own Agent SDKs (Meta DevMate, Plugboard, OpenAI Agent SDK, custom frameworks), their own device farms, and their own CI/CD pipelines. They connect to retention.sh for the intelligence: BFS crawl, test generation, exploration memory, verdict engine, and rerun optimization.

```
Enterprise Agent SDK ──→ retention.sh API (REST / WSS / MCP) ──→ Intelligence Layer
                                                                ├── BFS Crawl Engine
                                                                ├── Test Case Generator
                                                                ├── Exploration Memory
                                                                ├── Verdict Engine
                                                                ├── Rerun Engine
                                                                └── Linkage Graph
```

---

## Integration Methods

### 1. MCP Tools (simplest)

Any MCP-compatible agent (Claude Code, Cursor, Devin, OpenClaw) can connect with a single config entry.

**Streamable HTTP (recommended, zero proxy):**

```json
{
  "mcpServers": {
    "retention": {
      "type": "http",
      "url": "https://YOUR_TA_BACKEND/mcp-stream/mcp"
    }
  }
}
```

**Stdio proxy (works behind firewalls):**

```json
{
  "mcpServers": {
    "retention": {
      "command": "python3",
      "args": ["~/.retention/proxy.py"],
      "env": {
        "RETENTION_URL": "https://YOUR_TA_BACKEND",
        "RETENTION_MCP_TOKEN": "your-token"
      }
    }
  }
}
```

One-liner install:
```bash
curl -s https://YOUR_TA_BACKEND/mcp/setup/install.sh?token=YOUR_TOKEN | bash
```

### 2. REST API (direct HTTP)

For agents that do not support MCP. Two endpoints:

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET`  | `/mcp/tools` | Bearer token | List all available tools with parameter schemas |
| `POST` | `/mcp/tools/call` | Bearer token | Invoke a tool by name |
| `GET`  | `/mcp/health` | Bearer token | Readiness probe |

**Request schema** (`POST /mcp/tools/call`):
```json
{
  "tool": "retention.run_web_flow",
  "arguments": {
    "url": "https://myapp.com",
    "app_name": "My App"
  }
}
```

**Response schema:**
```json
{
  "tool": "retention.run_web_flow",
  "status": "ok",
  "result": { "run_id": "web-abc123", "message": "Pipeline started" },
  "error": null,
  "duration_ms": 342
}
```

### 3. WebSocket Relay (real-time bidirectional)

For enterprise device farms that need to stream emulator commands and frames without opening inbound ports.

| Endpoint | Transport | Description |
|----------|-----------|-------------|
| `/ws/agent-relay` | WSS | Main relay -- client connects outbound, server sends commands |
| `/api/relay/command` | POST | Enqueue a command to a connected relay session |
| `/api/relay/command/{id}/result` | GET | Fetch command result |
| `/api/relay/command/{id}/stream` | GET | Stream command output |
| `/api/relay/status` | GET | List connected relay sessions |

The relay model is **outbound-only from the client side**: device farm nodes connect out to the TA backend via WSS. No ports opened on the client. The TA backend sends emulator commands (tap, swipe, screenshot) down the socket and receives results back.

### 4. Python Direct Import

For teams running retention.sh self-hosted, harness functions can be imported directly:

```python
from app.agents.qa_pipeline import QAPipelineService

service = QAPipelineService()
run_id = await service.run_pipeline(
    app_url="https://myapp.com",
    mode="playwright",
)
```

---

## Authentication

**Token generation:**

```bash
# Via Convex (hosted):
curl -s -X POST "https://CONVEX_SITE_URL/api/mcp/generate-token" \
  -H "Content-Type: application/json" \
  -d '{"email": "dev@company.com", "name": "CI Bot", "platform": "custom-sdk"}'
# Response: {"token": "abc123...", "isNew": true}

# Via local signup:
curl -s -X POST "https://YOUR_TA_BACKEND/api/signup" \
  -H "Content-Type: application/json" \
  -d '{"email": "dev@company.com", "name": "CI Bot"}'
```

**Using the token:**

```bash
curl -s https://YOUR_TA_BACKEND/mcp/tools \
  -H "Authorization: Bearer abc123..."
```

**Token verification:**

```bash
curl -s "https://CONVEX_SITE_URL/api/mcp/verify-token?token=abc123"
```

**Auth disabled in local dev:** If neither `RETENTION_MCP_TOKEN` nor `CONVEX_SITE_URL` environment variables are set, auth is skipped entirely (local-only mode).

---

## Security

- **Outbound WSS**: relay connections are outbound from client -- no ports exposed on device farm nodes
- **Bearer token auth** with HMAC comparison (`hmac.compare_digest`)
- **Tool allowlist**: only tools in `MCP_TOOL_ALLOWLIST` are callable. Denylisted tools (`retention.codebase.shell_command`, `ta.admin.reset`, `ta.admin.delete_all`) are blocked unconditionally
- **Injection scanning**: all tool arguments are scanned for prompt injection patterns (instruction override, XSS, template injection, path traversal, command injection) with Unicode NFKC normalization
- **SSRF protection**: `retention.pipeline.run` and `retention.run_web_flow` block cloud metadata endpoints (`169.254.169.254`, `metadata.google.internal`)
- **Per-user access control**: pipeline runs are owned by the caller; cross-user access is denied

**Phase 2 (enterprise):**
- Token rotation via Convex
- mTLS for self-hosted deployments
- Audit log for all tool invocations

---

## Tool Reference

### QA Pipeline

| Tool | Description |
|------|-------------|
| `retention.run_web_flow` | Run full QA pipeline on a web URL (crawl, generate tests, execute, collect evidence) |
| `retention.run_android_flow` | Run QA pipeline on an Android app via emulator |
| `retention.quickstart` | Smart entry point -- auto-detects environment and picks best mode |
| `retention.pipeline.run` | Start async pipeline with explicit config (app_url, mode, scope) |
| `retention.pipeline.run_catalog` | Run pipeline against a pre-configured demo app |
| `retention.pipeline.status` | Poll pipeline progress (stage, metrics, events) |
| `retention.pipeline.results` | Get full test suite results for a completed run |
| `retention.pipeline.failure_bundle` | Token-efficient failure summary (~500-1500 tokens vs 5000+ raw) |
| `retention.pipeline.run_log` | Persistent run log readable across sessions |
| `retention.pipeline.screenshot` | Live screenshot from emulator during a run |
| `retention.pipeline.rerun_failures` | Rerun only failed tests from a prior run |
| `retention.pipeline.list_apps` | List available demo apps in catalog |
| `retention.rerun` | Rerun tests from a prior run (skip crawl/discovery/generation). ~98% time savings |
| `retention.get_handoff` | Structured markdown QA report for a completed run |

### Exploration Memory

| Tool | Description |
|------|-------------|
| `retention.memory.status` | Check cached crawl/workflows/suites for an app, with timestamps and cost savings |
| `retention.memory.check` | Check which pipeline stages can be skipped for an app URL |
| `retention.memory.graph` | Screen fingerprint graph -- all screens, transitions, fingerprint hashes |
| `retention.memory.apps` | List all apps with stored exploration memory |
| `retention.memory.stats` | Cache hit rate, tokens saved, compounding value metrics |
| `retention.memory.invalidate` | Clear cached data for an app (force full re-exploration) |

### Verdict and Analysis

| Tool | Description |
|------|-------------|
| `retention.emit_verdict` | Pass/fail/blocked verdict with configurable pass threshold (0.0-1.0) |
| `retention.summarize_failure` | Token-efficient failure summary with root-cause hints |
| `retention.suggest_fix_context` | Root-cause candidates with source file paths |
| `retention.compare_before_after` | Diff two runs: new failures, fixes, metric deltas |
| `retention.collect_trace_bundle` | Evidence artifacts (screenshots, action spans, logs, video) |
| `ta.feedback_package` | Autonomous fix prompt: failure summary + file suggestions + fix-verify loop |

### Feedback and Annotations

| Tool | Description |
|------|-------------|
| `retention.feedback.annotate` | Attach flag/suggestion/approval/rejection to a test case or workflow |
| `retention.feedback.list` | List annotations for a run, filtered by target |
| `retention.feedback.summary` | Counts by type, flagged items, approval status |

### Linkage Graph

| Tool | Description |
|------|-------------|
| `ta.linkage.register_feature` | Register a feature with its source files and test coverage |
| `ta.linkage.affected_features` | Given changed files, find affected features |
| `ta.linkage.rerun_suggestions` | Get rerun recommendations based on code changes |
| `ta.linkage.stats` | Linkage graph statistics |

### Device Management

| Tool | Description |
|------|-------------|
| `retention.device.list` | List available emulators/devices with connection status |
| `retention.device.lease` | Lease a device for exclusive testing (default 30 min) |
| `ta.setup.status` | Check local Android SDK/ADB/AVD installation |
| `ta.setup.launch_emulator` | Launch an Android emulator by AVD name |
| `retention.system_check` | Full readiness check: backend, ADB, Playwright, WebSocket relay |

### Validation Gates (CI/CD)

| Tool | Description |
|------|-------------|
| `retention.request_validation_gate` | Open a pre-merge QA gate. Returns hook_id |
| `retention.get_hook_status` | Poll gate status: pending, running, released, blocked |
| `retention.get_evidence_manifest` | ActionSpan evidence for a test session |

### Web Demo (Playwright, no emulator)

| Tool | Description |
|------|-------------|
| `retention.web_demo.discover` | Discover testable tasks from a URL using Playwright |
| `retention.web_demo.run` | Execute discovered tasks in parallel browsers |
| `retention.web_demo.scorecard` | QA scorecard for completed suite |
| `retention.web_demo.status` | Poll running suite status |

### Benchmarking

| Tool | Description |
|------|-------------|
| `retention.benchmark.generate_app` | Generate app with planted bugs for QA evaluation |
| `retention.benchmark.run_case` | Run QA against a generated benchmark case |
| `retention.benchmark.score` | Precision/recall/F1 against planted bug manifest |
| `retention.benchmark.list_templates` | Available app templates (booking, ecommerce, etc.) |
| `retention.benchmark.list_cases` | List generated benchmark cases |
| `retention.benchmark.run_history` | All benchmark runs with scores |

---

## Example: OpenAI Agent SDK Integration

```python
"""retention.sh integration with OpenAI Agent SDK."""

import json
import httpx
from agents import Agent, Runner, function_tool

RETENTION_URL = "https://YOUR_TA_BACKEND"
TA_TOKEN = "your-token"

async def _call_ta(tool: str, **kwargs) -> dict:
    """Call a retention.sh tool via REST API."""
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{RETENTION_URL}/mcp/tools/call",
            headers={"Authorization": f"Bearer {TA_TOKEN}"},
            json={"tool": tool, "arguments": kwargs},
        )
        resp.raise_for_status()
        return resp.json()


@function_tool
async def run_qa(url: str, app_name: str = "My App") -> str:
    """Run full QA pipeline on a web app."""
    result = await _call_ta("retention.run_web_flow", url=url, app_name=app_name)
    return json.dumps(result["result"])


@function_tool
async def poll_qa(run_id: str) -> str:
    """Check QA pipeline status."""
    result = await _call_ta("retention.pipeline.status", run_id=run_id)
    return json.dumps(result["result"])


@function_tool
async def get_failures(run_id: str) -> str:
    """Get compact failure bundle for a completed run."""
    result = await _call_ta("retention.pipeline.failure_bundle", run_id=run_id)
    return json.dumps(result["result"])


@function_tool
async def get_fix_suggestions(run_id: str) -> str:
    """Get root-cause analysis with source file paths."""
    result = await _call_ta("retention.suggest_fix_context", run_id=run_id)
    return json.dumps(result["result"])


@function_tool
async def rerun_failures(run_id: str) -> str:
    """Rerun only failed tests after fixing bugs."""
    result = await _call_ta("retention.rerun", run_id=run_id)
    return json.dumps(result["result"])


@function_tool
async def compare_runs(baseline_run_id: str, current_run_id: str) -> str:
    """Diff baseline vs current run to verify fixes."""
    result = await _call_ta(
        "retention.compare_before_after",
        baseline_run_id=baseline_run_id,
        current_run_id=current_run_id,
    )
    return json.dumps(result["result"])


qa_agent = Agent(
    name="QA Agent",
    instructions=(
        "You are a QA agent. When the user provides an app URL, run QA using run_qa, "
        "poll with poll_qa until complete, then analyze failures with get_failures. "
        "Suggest fixes with get_fix_suggestions. After the team fixes bugs, "
        "rerun with rerun_failures and compare with compare_runs."
    ),
    tools=[run_qa, poll_qa, get_failures, get_fix_suggestions,
           rerun_failures, compare_runs],
)


async def main():
    result = await Runner.run(
        qa_agent,
        input="Run QA on https://staging.myapp.com and report failures",
    )
    print(result.final_output)
```

---

## Example: Custom REST Integration (curl)

Full QA flow from the command line:

```bash
# 1. Generate API token
TOKEN=$(curl -s -X POST "https://CONVEX_SITE_URL/api/mcp/generate-token" \
  -H "Content-Type: application/json" \
  -d '{"email": "ci@company.com", "platform": "ci-pipeline"}' \
  | jq -r '.token')

# 2. Verify connectivity
curl -s https://YOUR_TA_BACKEND/mcp/health \
  -H "Authorization: Bearer $TOKEN"

# 3. Start QA pipeline
RUN_ID=$(curl -s -X POST https://YOUR_TA_BACKEND/mcp/tools/call \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "retention.run_web_flow",
    "arguments": {"url": "https://staging.myapp.com", "app_name": "Staging"}
  }' | jq -r '.result.run_id')

echo "Pipeline started: $RUN_ID"

# 4. Poll until complete
while true; do
  STATUS=$(curl -s -X POST https://YOUR_TA_BACKEND/mcp/tools/call \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"tool\": \"retention.pipeline.status\", \"arguments\": {\"run_id\": \"$RUN_ID\"}}" \
    | jq -r '.result.stage')
  echo "Stage: $STATUS"
  [ "$STATUS" = "complete" ] && break
  [ "$STATUS" = "failed" ] && { echo "Pipeline failed"; exit 1; }
  sleep 15
done

# 5. Get failure bundle (token-efficient)
curl -s -X POST https://YOUR_TA_BACKEND/mcp/tools/call \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"tool\": \"retention.pipeline.failure_bundle\", \"arguments\": {\"run_id\": \"$RUN_ID\"}}" \
  | jq '.result'

# 6. Get structured handoff report
curl -s -X POST https://YOUR_TA_BACKEND/mcp/tools/call \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"tool\": \"retention.get_handoff\", \"arguments\": {\"run_id\": \"$RUN_ID\"}}" \
  | jq -r '.result.markdown'

# 7. After fixing bugs, rerun only failures
RERUN_ID=$(curl -s -X POST https://YOUR_TA_BACKEND/mcp/tools/call \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"tool\": \"retention.rerun\", \"arguments\": {\"run_id\": \"$RUN_ID\"}}" \
  | jq -r '.result.run_id')

# 8. Compare baseline vs rerun
curl -s -X POST https://YOUR_TA_BACKEND/mcp/tools/call \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"tool\": \"retention.compare_before_after\",
    \"arguments\": {
      \"baseline_run_id\": \"$RUN_ID\",
      \"current_run_id\": \"$RERUN_ID\"
    }
  }" | jq '.result'
```

---

## Example: CI/CD Validation Gate

Block merges until QA passes:

```bash
# Open a validation gate before merge
HOOK_ID=$(curl -s -X POST https://YOUR_TA_BACKEND/mcp/tools/call \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "retention.request_validation_gate",
    "arguments": {
      "agent_id": "github-actions",
      "task_description": "PR #142: Add checkout flow",
      "repo": "myorg/myapp",
      "branch": "feature/checkout",
      "pr_url": "https://github.com/myorg/myapp/pull/142"
    }
  }' | jq -r '.result.hook_id')

# Poll until released or blocked
while true; do
  GATE=$(curl -s -X POST https://YOUR_TA_BACKEND/mcp/tools/call \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"tool\": \"retention.get_hook_status\", \"arguments\": {\"hook_id\": \"$HOOK_ID\"}}" \
    | jq -r '.result.status')
  echo "Gate: $GATE"
  [ "$GATE" = "released" ] && { echo "QA passed -- safe to merge"; exit 0; }
  [ "$GATE" = "blocked" ] && { echo "QA blocked merge"; exit 1; }
  sleep 10
done
```

---

## Example: Exploration Memory in CI

Leverage cached exploration data to make reruns near-free:

```bash
# Check what's cached for your app
curl -s -X POST https://YOUR_TA_BACKEND/mcp/tools/call \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "retention.memory.check",
    "arguments": {"app_url": "https://staging.myapp.com"}
  }' | jq '.result'
# Response shows which stages (CRAWL, WORKFLOW, TESTCASE) can be skipped
# and estimated cost savings

# First run is expensive (full crawl + discovery)
# Subsequent runs reuse memory -- near-instant test execution

# Invalidate cache when UI changes significantly
curl -s -X POST https://YOUR_TA_BACKEND/mcp/tools/call \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "retention.memory.invalidate",
    "arguments": {"app_url": "https://staging.myapp.com"}
  }'
```

---

## Example: Linkage-Driven Reruns

Only rerun tests affected by your code changes:

```bash
# Register feature-to-file mappings
curl -s -X POST https://YOUR_TA_BACKEND/mcp/tools/call \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "ta.linkage.register_feature",
    "arguments": {
      "feature_name": "checkout",
      "source_files": ["src/checkout/Cart.tsx", "src/checkout/Payment.tsx"],
      "test_ids": ["tc_checkout_001", "tc_checkout_002"]
    }
  }'

# After a commit, find affected features
curl -s -X POST https://YOUR_TA_BACKEND/mcp/tools/call \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "ta.linkage.affected_features",
    "arguments": {
      "changed_files": ["src/checkout/Payment.tsx"]
    }
  }' | jq '.result'

# Get optimized rerun plan
curl -s -X POST https://YOUR_TA_BACKEND/mcp/tools/call \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "ta.linkage.rerun_suggestions",
    "arguments": {
      "changed_files": ["src/checkout/Payment.tsx"]
    }
  }' | jq '.result'
```

---

## Pricing

| Tier | Price | Includes |
|------|-------|----------|
| **Indie** | $20/month | $0.005-0.012 per run, exploration memory, 1 concurrent emulator |
| **Team** | $200/month | 5 parallel emulators, shared exploration memory, linkage graph |
| **Enterprise** | Custom | Self-hosted, SSO/mTLS, dedicated device farm, SLA |

Exploration memory is included at all tiers. Run 1 pays for crawl and discovery. Runs 2-N reuse cached memory and cost near-zero.

---

## Hosted vs Self-Hosted

| Capability | Hosted (SaaS) | Self-Hosted |
|------------|---------------|-------------|
| Endpoint | `https://ta-backend.onrender.com` | `http://localhost:8000` |
| Auth | Convex per-user tokens | `RETENTION_MCP_TOKEN` env var or disabled |
| Device farm | TA-managed emulators | Your own ADB-connected devices |
| Memory persistence | Convex cloud | Local `backend/data/exploration_memory/` |
| WebSocket relay | Always available | Run your own backend |

For self-hosted, clone the repo and run:
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## Agent Bootstrap Endpoint

For any AI agent that needs machine-readable setup instructions (no HTML parsing):

```
GET /mcp/setup/agent-instructions?platform=custom-sdk&app_url=https://myapp.com
```

Returns plain text with `RUN:` (shell commands) and `ACTION:` (agent actions) that any LLM can follow step-by-step.
