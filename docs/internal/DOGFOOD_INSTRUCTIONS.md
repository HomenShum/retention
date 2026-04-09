# retention.sh MCP — Dogfooding Playbook

> **Audience:** Human developer OR autonomous agent (Claude Code, Cursor, OpenClaw).
> Every step is a shell command. No browser required. An agent reading this file can self-install.

---

## What This Is

retention.sh is an MCP server that gives your AI coding agent real QA superpowers — browser testing, Android emulator testing, screenshot verification, pre-merge validation gates — without writing test code. Your agent connects to retention.sh's hosted infrastructure via a lightweight local proxy.

---

## Quick Start (One Command)

```bash
curl -s "https://retention-backend.onrender.com/mcp/setup/init.sh" | bash
```

This handles everything: prompts for email, generates token, downloads proxy, writes `.mcp.json`. Then restart Claude Code and say "Test my app at localhost:3000".

---

## Manual Setup (Step-by-Step)

### Prerequisites

| Requirement | Check command |
|---|---|
| Python 3.10+ | `python3 --version` |
| curl | `curl --version` |
| Claude Code (or any MCP-capable agent) | Installed and can start a session |

---

### Step 0: Generate Your API Token

**Ask the user for their email**, then run:

```bash
curl -s -X POST "https://exuberant-ferret-263.convex.site/api/mcp/generate-token" \
  -H "Content-Type: application/json" \
  -d '{"email": "USER_EMAIL_HERE", "name": "USER_NAME_HERE", "company": "USER_COMPANY_HERE", "platform": "claude-code"}'
```

The response is JSON:

```json
{"token": "aad98294c73019c25083beae8f602f67", "isNew": true, "usageCount": 0}
```

Save the `token` value — you need it for Step 1.

**Notes:**
- `name` and `company` are optional (omit or set to `""`)
- Same email always returns the same token (idempotent) unless revoked
- Tokens don't expire during beta
- If you need a fresh token, use the rotate endpoint (see Troubleshooting)

---

## Step 1: Install the MCP Proxy

Run the install script with your token from Step 0:

### macOS / Linux

```bash
curl -s "https://retention-backend.onrender.com/mcp/setup/install.sh?token=YOUR_TOKEN_HERE" | bash
```

### Windows (PowerShell)

```powershell
irm "https://retention-backend.onrender.com/mcp/setup/install.ps1?token=YOUR_TOKEN_HERE" | iex
```

**What this does:**
1. Downloads `~/.retention/proxy.py` (lightweight MCP proxy)
2. Writes/merges MCP config into `.mcp.json` in the current directory
3. Embeds your token in the config's `env` block

### Manual alternative (if the script doesn't work)

Create or merge into `.mcp.json` in your project root.

**Important:** JSON does not expand `$HOME` or `~`. You must use the absolute path. Get it with:

```bash
echo "$HOME/.retention/proxy.py"
```

Then write the config (replace `/Users/yourname/` with your actual home directory):

```json
{
  "mcpServers": {
    "retention": {
      "command": "python3",
      "args": ["/Users/yourname/.retention/proxy.py"],
      "env": {
        "RETENTION_URL": "https://retention-backend.onrender.com",
        "RETENTION_MCP_TOKEN": "YOUR_TOKEN_HERE"
      }
    }
  }
}
```

Or generate it programmatically:

```bash
python3 -c "
import json, os
config = {'mcpServers': {'retention': {
    'command': 'python3',
    'args': [os.path.expanduser('~/.retention/proxy.py')],
    'env': {'RETENTION_URL': 'https://retention-backend.onrender.com', 'RETENTION_MCP_TOKEN': 'YOUR_TOKEN_HERE'}
}}}
json.dump(config, open('.mcp.json', 'w'), indent=2)
print('Wrote .mcp.json')
"
```

Then download the proxy:

```bash
mkdir -p ~/.retention && curl -s "https://retention-backend.onrender.com/mcp/setup/proxy.py" -o ~/.retention/proxy.py && chmod +x ~/.retention/proxy.py
```

---

## Step 2: Restart Claude Code & Verify

Claude Code picks up new MCP servers on restart. Close and reopen Claude Code (or start a new session).

Inside a Claude Code session, check MCP servers are loaded:

```
/mcp
```

You should see `retention` in the list. Then verify the connection works:

```
> Run ta.system_check to verify retention.sh is connected
```

**Expected:** A response listing available tools (ta.run_web_flow, ta.run_android_flow, etc.) and confirming the backend is reachable.

### If you're an agent following this playbook

You can verify the proxy works without restarting by sending a JSON-RPC initialize + tools/list via stdin:

```bash
echo '{"jsonrpc":"2.0","method":"initialize","id":1,"params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | \
  RETENTION_URL="https://retention-backend.onrender.com" RETENTION_MCP_TOKEN="YOUR_TOKEN_HERE" \
  python3 ~/.retention/proxy.py
```

**Expected:** JSON response with `"serverInfo": {"name": "retention-remote", ...}`

---

## Step 3: Run a Test

### Web testing (any URL your agent can reach)

```
> Test my app at https://my-app.vercel.app
```

Or for localhost apps:

```
> Test my app at http://localhost:3000
```

The agent calls `ta.run_web_flow`, which runs real browser interactions, captures screenshots, and returns a structured pass/fail verdict with evidence.

### Android testing (requires emulator)

```
> Run an Android test flow on the Instagram app — verify the login screen loads
```

The agent calls `ta.run_android_flow` to drive the emulator.

---

## Step 4: Verify Token Auth is Working

To confirm your token is valid from the command line:

```bash
curl -s "https://exuberant-ferret-263.convex.site/api/mcp/verify-token?token=YOUR_TOKEN_HERE"
```

**Expected:** `{"valid": true, "email": "your@email.com", ...}`

If you get `{"valid": false, "reason": "token_not_found"}`, regenerate via Step 0.

---

## Available MCP Tools

Once connected, your agent has these tools:

**Core QA tools:**

| Tool | What it does |
|---|---|
| `ta.system_check` | Verify retention.sh connection and list capabilities |
| `ta.run_web_flow` | Run a browser-based QA test on any URL |
| `ta.run_android_flow` | Run an Android emulator test on a mobile app |
| `ta.collect_trace_bundle` | Get ActionSpan evidence clips (2-3s video proof) |
| `ta.summarize_failure` | AI-summarize a test failure with root cause |
| `ta.suggest_fix_context` | Get fix suggestions scoped to your codebase |
| `ta.emit_verdict` | Record a structured pass/fail verdict |
| `ta.compare_before_after` | Compare two test runs to detect regressions |

**Validation gates (pre-merge QA blocks):**

| Tool | What it does |
|---|---|
| `ta.request_validation_gate` | Open a validation gate — blocks merge until QA passes |
| `ta.get_hook_status` | Poll a gate's status (pending / running / released / blocked) |

**Device & relay management:**

| Tool | What it does |
|---|---|
| `ta.device.list` | List available Android emulators |
| `ta.device.lease` | Lease an emulator for testing |
| `ta.relay.status` | Check outbound WebSocket relay connection status |
| `ta.relay.reconnect` | Force reconnect the outbound relay |

**Codebase tools (agent can read your repo):**

| Tool | What it does |
|---|---|
| `ta.codebase.read_file` | Read a file from the repo |
| `ta.codebase.search` | Search codebase for a pattern |
| `ta.codebase.git_status` | Get current git status |
| `ta.codebase.recent_commits` | List recent commits |

Run `ta.system_check` to see the full list of 51 available tools.

---

## Troubleshooting

### "Missing Authorization: Bearer <token> header"
Token isn't being sent. Verify `.mcp.json` has `"RETENTION_MCP_TOKEN"` in the `env` block:
```bash
cat .mcp.json | python3 -m json.tool
```

### "Invalid MCP token"
Token is wrong or doesn't exist. Regenerate:
```bash
curl -s -X POST "https://exuberant-ferret-263.convex.site/api/mcp/generate-token" \
  -H "Content-Type: application/json" \
  -d '{"email": "your@email.com"}'
```

### "MCP token has been revoked"
Rotate to get a fresh token:
```bash
curl -s -X POST "https://exuberant-ferret-263.convex.site/api/mcp/rotate-token" \
  -H "Content-Type: application/json" \
  -d '{"email": "your@email.com", "platform": "claude-code"}'
```
Then update `.mcp.json` with the new token.

### Backend cold start (first request takes ~30s)
The backend runs on Render free tier. First request after inactivity spins up the server. Subsequent requests are fast. To pre-warm:
```bash
curl -s "https://retention-backend.onrender.com/api/health"
```

### Claude Code doesn't see `retention` in `/mcp`
1. Check `.mcp.json` exists in project root: `ls -la .mcp.json`
2. Verify it's valid JSON: `python3 -m json.tool .mcp.json`
3. Check proxy exists: `ls -la ~/.retention/proxy.py`
4. Restart Claude Code completely (not just new conversation)

### Proxy crashes on startup
Check Python version (`python3 --version` must be 3.8+) and verify the proxy file loads without syntax errors:
```bash
python3 -c "import py_compile, os; py_compile.compile(os.path.expanduser('~/.retention/proxy.py'), doraise=True)" 2>&1 && echo "Proxy syntax OK" || echo "Proxy has errors"
```

Or test it end-to-end with env vars:
```bash
echo '{}' | RETENTION_URL="https://retention-backend.onrender.com" RETENTION_MCP_TOKEN="YOUR_TOKEN" python3 ~/.retention/proxy.py 2>&1 | head -5
```
You should see `[ta-proxy] Connected to retention.sh` on stderr.

---

## What to Report Back

After testing, share:

1. **Token generation** — Did Step 0 return a token?
2. **Install** — Did `curl | bash` complete without errors?
3. **Connection** — Did `ta.system_check` pass?
4. **Test run** — Did `ta.run_web_flow` return a verdict?
5. **Friction** — Anything confusing, slow, or broken?

Paste full error output — it helps.

---

## Questions?

Ping **Homin** on Slack or email **homen@retention.com**

Web install page (for humans who prefer a UI): https://test-studio-xi.vercel.app/docs/install
