# retention.sh — Claude Code Integration Guide

> **Audience**: Developers using Claude Code or OpenClaw who want QA assurance on their local app workflows.
> **Time to set up**: Under 2 minutes.

---

## What This Is

retention.sh is a hosted QA assurance layer that plugs into your coding agent (Claude Code or OpenClaw) via MCP (Model Context Protocol). It runs real app flows against your local or deployed app — browser or Android emulator — captures traces, screenshots, and logs, and returns a compact failure bundle that your agent can use to fix issues precisely.

**You are not installing another testing framework.** You are giving your coding agent the ability to verify its own work on a real running app.

---

## Architecture

```
Developer (you)
    │
    ▼
Claude Code / OpenClaw ── MCP ──► retention.sh (hosted)
    │                                   │
    │ (your local env)      ┌───────────┼───────────────┐
    │                       ▼           ▼               ▼
    │                  Playwright    Android         Evidence
    │                  (hosted)     Emulator         Storage
    │                       │           │
    ▼                       ▼           ▼
Your App ◄── Outbound ─── Real flows against your app
(localhost    WSS relay
 or deployed) (auto)

Results viewable at: test-studio-xi.vercel.app
```

---

## Prerequisites

| Tool | Required | Install |
|------|----------|---------|
| Python 3.11+ | Yes | `brew install python@3.11` |
| WSS relay | Auto-installed | Bundled with MCP proxy |
| Android SDK + Emulator | For mobile QA | Your agent helps set this up |

---

## Quick Start (2 minutes)

### Step 1: Connect to retention.sh MCP

```bash
curl -s https://retention-backend.onrender.com/mcp/setup/install.sh | bash
```

This does three things:
1. Downloads the MCP proxy to `~/.retention/proxy.py`
2. Writes the MCP config to `~/.claude/mcp.json` (or `.openclaw/mcp.json`)
3. Verifies the connection to retention.sh's hosted infrastructure

**You do not need to run any backend.** retention.sh is hosted — the proxy connects your agent to our QA infrastructure.

### Step 2: Set up your local environment

Your agent handles this. Just tell it:

```
> Set up retention.sh for testing my app at http://localhost:3000
```

Your agent will:
1. Start an Android emulator (if mobile testing is needed)
2. Connect outbound to retention.sh server via WebSocket so it can reach your localhost app
3. Verify connectivity with `ta.system_check`

If your app is already deployed (not localhost), skip this — retention.sh can reach it directly.

### Step 3: Restart Claude Code

Claude Code picks up new MCP servers on restart. After restarting, verify:

```
> Run ta.system_check to verify everything works
```

You should see retention.sh tools available:

```
I have access to these retention.sh tools:
- ta.run_web_flow       — Run QA verification on a web app
- ta.run_android_flow   — Run QA on an Android app
- ta.collect_trace_bundle — Get compact evidence bundle
- ta.summarize_failure  — Token-efficient failure summary
- ta.emit_verdict       — Pass/fail/blocked verdict
- ta.suggest_fix_context — Root cause + file suggestions
- ta.compare_before_after — Diff baseline vs current run
...
```

### Step 4: Test your app

```
> Test my app at http://localhost:3000
```

Claude Code will call `ta.run_web_flow`, capture evidence, and return a verdict with failure details if anything breaks.

View full results at [test-studio-xi.vercel.app](https://test-studio-xi.vercel.app).

---

## Manual MCP Configuration

If you prefer to configure manually, add to `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "retention": {
      "command": "python3",
      "args": ["~/.retention/proxy.py"],
      "env": {
        "TA_STUDIO_URL": "https://retention-backend.onrender.com",
        "RETENTION_MCP_TOKEN": ""
      }
    }
  }
}
```

Download the proxy manually:
```bash
mkdir -p ~/.retention
curl -s https://retention-backend.onrender.com/mcp/setup/proxy.py -o ~/.retention/proxy.py
chmod +x ~/.retention/proxy.py
```

---

## The QA Fix Loop — How It Works

### 1. Developer asks Claude Code to implement or fix something

```
> Fix the login form — it's not validating email format before submission
```

### 2. Claude Code patches the code, then calls retention.sh to verify

Claude Code automatically (or when prompted) calls:

```
ta.run_web_flow(url="http://localhost:5173", test_count=5)
```

The proxy connects outbound via WebSocket if the URL is localhost, so retention.sh's execution surface can reach your local app.

### 3. retention.sh runs real flows and captures evidence

- Crawls the app with Playwright
- Generates test cases from discovered elements
- Executes each test (click, navigate, fill forms)
- Captures: Playwright trace, screenshots, console logs, network logs, video

### 4. retention.sh returns a compact failure bundle

```json
{
  "run_id": "abc123",
  "execution_summary": {
    "total": 5,
    "passed": 3,
    "failed": 2,
    "pass_rate": 0.6
  },
  "failures": [
    {
      "test_id": "form-submit-001",
      "failing_step": "Submit login form with invalid email 'notanemail'",
      "expected": "Form shows validation error",
      "actual": "Form submitted without validation",
      "screenshot": "artifacts/form-submit-001/after.png",
      "root_cause_candidates": ["No client-side email regex", "Missing onSubmit handler"]
    }
  ]
}
```

### 5. Claude Code reads the bundle and patches precisely

Because the failure bundle includes:
- **Exact failing step** (not just "something broke")
- **Root cause candidates** (not just stack traces)
- **Suggested files** to change
- **Screenshots** of the failure state

Claude Code can patch the right code instead of guessing.

### 6. Rerun and compare

```
ta.compare_before_after(baseline_run_id="abc123", current_run_id="def456")
```

Returns a diff showing what improved, what regressed, and the final verdict.

---

## Outbound WebSocket Relay (Zero Config)

When you call `ta.run_web_flow` with a `localhost` URL, the MCP proxy automatically:

1. Detects the localhost URL
2. Connects outbound to TA server via WebSocket
3. Rewrites the URL to the relay endpoint
4. Sends the rewritten URL to retention.sh

**You never need to open inbound ports or configure DNS.** It just works.

---

## MCP Tools Reference

### Core QA Tools

| Tool | Purpose | Key Args |
|------|---------|----------|
| `ta.run_web_flow` | Run full QA flow on web app | `url`, `test_count`, `include_trace` |
| `ta.run_android_flow` | Run QA on Android app | `app_package`, `device_id`, `workflow` |
| `ta.collect_trace_bundle` | Get compact evidence | `run_id` |
| `ta.summarize_failure` | Token-efficient summary | `run_id`, `priority`, `max_tokens` |
| `ta.emit_verdict` | Final pass/fail | `run_id`, `pass_threshold` |
| `ta.suggest_fix_context` | Root cause + files | `run_id` |
| `ta.compare_before_after` | Before/after diff | `baseline_run_id`, `current_run_id` |

### System Tools

| Tool | Purpose |
|------|---------|
| `ta.system_check` | Full readiness check — backend, emulator, playwright, relay |
| `ta.smoke_test` | Quick ADB connectivity check |

### Benchmark Tools

| Tool | Purpose |
|------|---------|
| `ta.benchmark.run_suite` | Run baseline vs TA-assisted comparison |
| `ta.benchmark.scorecard` | Get latest benchmark metrics |

### Device Tools

| Tool | Purpose |
|------|---------|
| `ta.device.list` | List connected emulators |
| `ta.device.lease` | Lease a device for exclusive use |

### Codebase Tools

| Tool | Purpose |
|------|---------|
| `ta.codebase.recent_commits` | Recent git history |
| `ta.codebase.search` | Search code/files |
| `ta.codebase.read_file` | Read repo files |
| `ta.codebase.git_status` | Current git state |

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "MCP endpoint not responding" | `curl https://retention-backend.onrender.com/api/health` |
| "No tools showing in Claude Code" | Restart Claude Code after adding MCP config |
| "Localhost app not reachable" | Proxy relay connects outbound for localhost — ensure your app is running |
| "Playwright not installed" | `pip install playwright && playwright install chromium` |
| "No emulator found" | `emulator -avd Pixel_7_API_34 &` or ask your agent to set one up |

---

## What We Provide vs. What You Provide

| You Provide | retention.sh Provides |
|-------------|-------------------|
| Your local dev environment | Hosted QA orchestration |
| Your app running locally (or deployed) | Evidence capture (trace, screenshots, logs) |
| Your coding agent (Claude Code / OpenClaw) | Failure localization + root cause |
| Outbound WSS relay (auto-connected) | Compact failure bundle |
| | Judged verdict (pass/fail/blocked) |
| | Fix context for coding agent |
| | Before/after comparison |
| | Results dashboard at test-studio-xi.vercel.app |

**Your code, your agent, our QA judgment.**
