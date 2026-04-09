# Remote Agent Access — Setup Guide

Connect Claude Code or OpenClaw on any machine to retention.sh's hosted QA infrastructure.

## Prerequisites

- Your machine: Claude Code or OpenClaw installed, Python 3.11+
- (Optional) Android emulator for mobile QA
- (Optional) Your app running locally (the relay connects outbound automatically)

## Step 1: Install MCP Proxy (one command)

```bash
curl -s https://retention-backend.onrender.com/mcp/setup/install.sh | bash
```

This downloads the MCP proxy and configures your agent to connect to retention.sh's hosted endpoint. No backend to run.

## Step 2: Restart Your Agent

Restart Claude Code or OpenClaw to pick up the new MCP server. Then verify:

```
> Run ta.system_check to verify everything works
```

## Step 3: Test Your App

```
> Test my app at http://localhost:3000
```

The MCP proxy connects outbound to retention.sh server via WebSocket so retention.sh can reach your localhost app. If your app is deployed, just pass the URL directly.

## Manual MCP Configuration

If the one-liner doesn't work, create the config manually:

**Claude Code** — add to `.mcp.json` in your project root:
```json
{
  "mcpServers": {
    "retention": {
      "command": "python3",
      "args": ["~/.retention/proxy.py"],
      "env": {
        "RETENTION_URL": "https://retention-backend.onrender.com",
        "RETENTION_MCP_TOKEN": ""
      }
    }
  }
}
```

**OpenClaw** — add to `.openclaw/mcp.json`:
```json
{
  "mcpServers": {
    "retention": {
      "command": "python3",
      "args": ["~/.retention/proxy.py"],
      "env": {
        "RETENTION_URL": "https://retention-backend.onrender.com",
        "RETENTION_MCP_TOKEN": ""
      },
      "description": "retention.sh QA automation — 45 MCP tools"
    }
  }
}
```

Download proxy manually:
```bash
mkdir -p ~/.retention
curl -s https://retention-backend.onrender.com/mcp/setup/proxy.py -o ~/.retention/proxy.py
chmod +x ~/.retention/proxy.py
```

## Available Tools

### Core QA
| Tool | Description |
|------|-------------|
| `ta.run_web_flow` | Run full QA verification on a web app URL |
| `ta.run_android_flow` | Run QA on an Android app via emulator |
| `ta.collect_trace_bundle` | Get compact evidence bundle for a run |
| `ta.summarize_failure` | Token-efficient failure summary with root-cause hints |
| `ta.emit_verdict` | Final pass/fail/blocked verdict |
| `ta.suggest_fix_context` | Root cause analysis + files to patch |
| `ta.compare_before_after` | Diff baseline vs current run |

### Pipeline
| Tool | Description |
|------|-------------|
| `ta.pipeline.list_apps` | List demo app catalog |
| `ta.pipeline.run` | Start pipeline on any URL |
| `ta.pipeline.run_catalog` | Start pipeline for catalog app |
| `ta.pipeline.status` | Poll running pipeline |
| `ta.pipeline.results` | Get completed results |

### Feedback
| Tool | Description |
|------|-------------|
| `ta.feedback.annotate` | Flag issues, suggest improvements, approve/reject |
| `ta.feedback.list` | List annotations |
| `ta.feedback.summary` | Summary of all feedback |

### Device
| Tool | Description |
|------|-------------|
| `ta.device.list` | List emulators/devices |
| `ta.device.lease` | Lease device for exclusive use |
| `ta.smoke_test` | Quick ADB connectivity check |

### System
| Tool | Description |
|------|-------------|
| `ta.system_check` | Full readiness check |
| `ta.meta.connection_info` | Server status + relay connection |

## Parallel Subagent Patterns

### Fan-out analysis on results
```
1. ta.pipeline.run → get run_id
2. Poll ta.pipeline.status until complete
3. ta.pipeline.results → get test cases
4. Spawn parallel agents:
   - Agent 1: UI review → ta.feedback.annotate (flag UI issues)
   - Agent 2: Security → ta.feedback.annotate (flag vulnerabilities)
   - Agent 3: Coverage → ta.feedback.annotate (suggest missing tests)
5. ta.feedback.summary → compile report
```

### Multi-URL comparison
```
1. ta.pipeline.run on URL-A → run_id_a
2. ta.pipeline.run on URL-B → run_id_b
3. Poll both until complete
4. Compare test suites, identify gaps
```

## View Results

All verification results are viewable at [test-studio-xi.vercel.app](https://test-studio-xi.vercel.app).

## Troubleshooting

| Issue | Fix |
|-------|-----|
| 401 Unauthorized | Check `RETENTION_MCP_TOKEN` in your MCP config |
| Connection refused | `curl https://retention-backend.onrender.com/api/health` |
| No tools in agent | Restart agent after adding MCP config |
| Pipeline not starting | Check emulator is running (`adb devices`) |
| Localhost app not reachable | Ensure your app is running, relay connects outbound |
