# retention.sh QA Flow — Instructions for Your Claude Code Agent

## What This Does
Your Claude Code agent will use retention.sh's MCP tools to run a full QA pipeline on your app:
crawl → discover workflows → generate tests → execute → report bugs → rerun after fix.

## Prerequisites

### 1. Python 3.11+
```bash
python3 --version  # needs 3.11+
```

### 2. Android SDK + Emulator
```bash
# Install Android command-line tools
brew install --cask android-commandlinetools

# Accept licenses
yes | sdkmanager --licenses

# Install platform + emulator
sdkmanager "platform-tools" "platforms;android-36" "system-images;android-36;google_apis;arm64-v8a" "emulator"

# Create AVD
avdmanager create avd -n Pixel_7_API_36 -k "system-images;android-36;google_apis;arm64-v8a" -d pixel_7
```

### 3. Start the Emulator
```bash
emulator -avd Pixel_7_API_36 -no-audio &
# Wait for boot
adb wait-for-device && adb shell getprop sys.boot_completed | grep 1
```

### 4. Node.js 18+
```bash
node --version  # needs 18+
```

## Setup — Add retention.sh MCP to Claude Code

Add this to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "retention": {
      "command": "npx",
      "args": ["-y", "retention-mcp@latest"],
      "env": {
        "RETENTION_MCP_TOKEN": "sk-ret-demo-2026",
        "TA_SERVER_URL": "http://localhost:8000"
      }
    }
  }
}
```

For local dogfood (TA backend on your machine):
```bash
cd /path/to/my-fullstack-app/backend
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Run the QA Flow

Paste this into your Claude Code:

```
I want to test my app for bugs. My app is at [YOUR_APP_URL].

Use the retention.sh tools to:
1. Run ta.run_web_flow with my app URL
2. Wait for the pipeline to complete (poll ta.pipeline.status every 15s)
3. When done, get the failure bundle with ta.pipeline.failure_bundle
4. Show me all bugs found with steps to reproduce
5. After I fix a bug, run ta.pipeline.rerun_failures to verify the fix
```

## What Happens

```
Your Claude Code                    retention.sh Server
┌──────────────┐                   ┌──────────────────────┐
│ 1. Calls     │                   │                      │
│ ta.run_web_  │──── MCP ─────────▶│ 2. Opens Chrome on   │
│ flow(url)    │                   │    emulator, navigates│
│              │                   │    to your app URL    │
│              │                   │                      │
│ 3. Polls     │                   │ 4. BFS crawl:        │
│ ta.pipeline. │◀── status ────────│    registers screens, │
│ status       │                   │    discovers paths    │
│              │                   │                      │
│              │                   │ 5. Workflow analysis  │
│              │                   │ 6. Test generation    │
│              │                   │ 7. Test execution     │
│              │                   │                      │
│ 8. Gets      │                   │ 9. Returns:          │
│ failure      │◀── bundle ────────│    - bugs found      │
│ bundle       │                   │    - steps to repro  │
│              │                   │    - rerun command    │
│              │                   │                      │
│ 10. Fixes    │                   │                      │
│ the bug      │                   │                      │
│              │                   │                      │
│ 11. Calls    │                   │ 12. Re-executes      │
│ ta.pipeline. │──── MCP ─────────▶│     tests only       │
│ rerun_       │                   │     (10s, ~$0)       │
│ failures     │◀── result ────────│                      │
└──────────────┘                   └──────────────────────┘
```

## View Results

Open the Memory Dashboard to see compounded results:
```
http://localhost:5173/demo/memory
```

This shows:
- Tokens saved across runs (exploration memory)
- Run history with costs and durations
- Cached app memory (screens, workflows, test suites)
- Cost curve showing runs getting cheaper over time

## Available Tools

| Tool | What It Does |
|------|-------------|
| `ta.run_web_flow` | Full QA pipeline on a web app URL |
| `ta.run_android_flow` | Full QA pipeline on a native Android app |
| `ta.pipeline.status` | Check pipeline progress |
| `ta.pipeline.failure_bundle` | Get compact bug report |
| `ta.pipeline.rerun_failures` | Re-run only failed tests ($0, 10s) |
| `ta.collect_trace_bundle` | Get screenshots + traces |
| `ta.emit_verdict` | Get pass/fail verdict |
| `ta.suggest_fix_context` | Get fix suggestions for failures |

## Benchmark Your Own App

To compare costs with and without TA:

```
Run 1 (full pipeline): ~$0.008 (mini), ~175s, 22 tests generated
Run 2 (same app, memory hit): skips crawl+workflow+testgen, ~60s
Run 3 (after fix, rerun): ~$0, ~10s
```

TA harnesses constrain token usage to ~10-12K regardless of which model you use.
Opus 4.6 would cost $0.34/run for the same output that Mini produces at $0.008.
