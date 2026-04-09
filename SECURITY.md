# retention.sh Security Architecture

## Authentication

### MCP Token Auth (Bearer)
- **Endpoint**: `/mcp/tools/call`, `/mcp-stream/mcp`
- **Flow**: `Authorization: Bearer <token>` → `verify_mcp_token()` → `request.state.mcp_user`
- **Token sources** (checked in order):
  1. Shared env token (`RETENTION_MCP_TOKEN`) — backward compat
  2. Local signup tokens (`backend/data/api_keys.json`) — SHA-256 hashed at rest
  3. Per-user tokens via Convex — for production SaaS

### Token Storage
- Tokens generated with `secrets.token_hex(8)` — 64-bit entropy (16 hex chars)
- Stored as SHA-256 hash — plaintext never persisted after initial generation
- Brute-force at 1000 req/s: ~292 million years average case

### WebSocket Relay Auth
- **Handshake**: Bearer token validated on connection open
- **Per-message**: HMAC-SHA256 signature on critical messages (`response`, `relay_ready`)
  - Session secret issued at handshake: `session_secret = secrets.token_hex(16)`
  - Signature: `HMAC(session_secret, "{msg_type}:{msg_id}")[:16]`
  - Currently in warn-only mode for backward compat; enforced once all clients sign
- **Session timeout**: 4-hour max age, then forced re-auth

## Multi-User Isolation

### Pipeline Data Isolation
- Every pipeline entry stamped with `owner_id` at creation (6 creation sites)
- `_check_run_access(run_id, caller_id)` guard on every read path:
  - `retention.pipeline.status`, `retention.pipeline.results`
  - `retention.collect_trace_bundle`, `retention.summarize_failure`
  - `retention.emit_verdict`, `retention.suggest_fix_context`
  - `retention.compare_before_after`, `retention.rerun`
- List endpoints (`retention.pipeline.results` without run_id) filter by owner
- `_persist_result()` auto-includes `owner_id` in disk-persisted JSON

### Caller Identity Threading
- `call_tool()` extracts `caller_id` from authenticated `request.state.mcp_user`
- `_dispatch(tool, args, caller_id=caller_id)` injects `args["_caller_id"]`
- All 12+ dispatchers pop `_caller_id` from args before processing
- Internal tool-to-tool calls (e.g., `_build_feedback_package` → `dispatch_qa_verification`) thread `_caller_id` explicitly

### Session Ownership (ta.agent.run)
- Session IDs prefixed with `u:{caller_id}:{raw_session_id}`
- Resume validation: session_id prefix must match authenticated caller
- Prevents cross-user session hijacking even if raw session_id is leaked

### Relay Command Isolation
- `POST /api/relay/command` — `req.user_id` overridden with authenticated `caller_id` (confused-deputy prevention)
- `GET /api/relay/command/{id}/result` — Bearer auth required + `owner_id` check
- `GET /api/relay/command/{id}/stream` — Bearer auth required + `owner_id` check
- Each command entry stamped with `owner_id` at creation

### Report Ownership
- `POST /api/reports` — stamps `created_by` from authenticated caller (Bearer token)
- `GET /api/reports` — filters by `created_by` matching caller (anonymous sees only anonymous reports)
- `DELETE /api/reports/{id}` — verifies `created_by` matches authenticated caller
- `GET /r/{report_id}` — intentionally public (shareable short-URL for report viewing)

### Run Log Isolation
- `retention.pipeline.run_log` (single): `_check_run_access` before disk read or `format_compact_bundle`
- `retention.pipeline.run_log` (list): filters by `owner_id` matching caller before returning entries

### Relay Status Isolation
- `GET /api/relay/status` requires Bearer auth, returns only the caller's own sessions

## Anti-Enumeration

### Generic Error Messages
- `_check_run_access()` returns identical "Run not found or access denied" for both missing and unauthorized runs
- Prevents run_id enumeration via error message differentiation

### Run ID Entropy
- Format: `{type}-{uuid4().hex[:16]}` — 64-bit random hex
- Birthday collision threshold: ~4.3 billion runs before 50% collision probability
- Types: `web-`, `android-`, `mcp-`, `rerun-`, `pw-`, `qa-bench-`

## Command Security

### MCP Tool Allowlist/Denylist
- **Allowlist**: 30+ explicitly permitted tools (fail-closed for unknown tools)
- **Denylist**: `retention.codebase.shell_command`, `ta.admin.*` always blocked
- Checked before every tool dispatch

### Injection Pattern Detection
- 40+ patterns checked against normalized JSON dump of tool args
- **Unicode normalization**: NFKC applied before matching (fullwidth `＜script＞` → `<script>`)
- **Null byte stripping**: `\x00` and `\u0000` removed before matching
- Categories: prompt injection, XSS/HTML event handlers, template injection (`{{`, `${`, `<%`), path traversal, command injection
- Applied to all MCP tool calls before dispatch

### Relay Command Allowlist
- 7 permitted commands: `run_flow`, `run_web_flow`, `run_android_flow`, `screenshot`, `device_list`, `system_check`, `stop_flow`
- Denylist keywords: `shell`, `exec`, `rm`, `delete`, `install`, `uninstall`, `adb_shell_raw`, `su`, `root`, `reboot`, `format`
- Denylist checked first (keyword match), then allowlist (exact match)
- Unknown commands rejected (fail-closed)

### Rate Limiting
- Per-user sliding window: 30 requests / 60 seconds
- Keyed by authenticated `caller_id`
- HTTP 429 with `Retry-After` hint on excess

## SSRF Protection

### URL Validation (`_validate_app_url`)
- Blocked metadata hosts:
  - `169.254.169.254` (AWS IMDSv1 / Azure IMDS)
  - `metadata.google.internal` (GCP)
  - `100.100.100.200` (Alibaba Cloud)
  - `169.254.170.2` (AWS ECS task metadata)
  - `fd00:ec2::254` (AWS IMDSv2 IPv6)
  - `host.docker.internal` (Docker host escape)
- Private IP ranges blocked
- Applied to all user-provided URLs before pipeline execution

## Network Architecture

### Outbound-Only Relay
- User's machine connects OUT to TA server via WSS — no inbound ports opened
- All traffic over port 443 (standard HTTPS)
- Server cannot initiate connections to user's network
- See `/security` page for full architecture diagram

### Multi-MCP Token Isolation
- Each MCP server gets its own scoped credential
- retention.sh's `sk-ret-*` token cannot access GitHub/Slack/DB resources
- Process-level isolation between MCP servers

## CORS Policy
- Allowed origins: `localhost:5173`, `localhost:8000`, `*.vercel.app`
- Credentials: enabled (with restrictive origin whitelist)
- Non-localhost mutation endpoints additionally require Bearer token

## Known Limitations

### Dashboard REST Endpoints (Intentionally Unauthenticated)
The following endpoint families serve the same-origin web dashboard and are **intentionally**
not behind Bearer token auth. They are protected by DemoGate (email signup gate on frontend)
+ CORS (same-origin policy restricts cross-site requests):

- `/api/demo/*` — pipeline streams, results viewer, device simulation
- `/api/feedback/*` — feedback package assembly/retrieval
- `/api/test-generation/*` — test case CRUD
- `/api/benchmarks/*` — benchmark comparison, comprehensive, golden bugs
- `/api/figma/*`, `/api/chef/*`, `/api/slack/*` — integration UIs
- `/api/action-spans/*`, `/api/agent-sessions/*` — observability dashboards
- `/api/device-simulation/*`, `/api/perception/*` — device control UI
- `/api/health/*` — health probes (public by design)
- `/api/setup/*` — install scripts (public by design)

**External agents** (Claude Code, Cursor, OpenClaw) use the MCP path (`/mcp/tools/call`)
which has full Bearer token auth + per-user ownership isolation. Dashboard endpoints
are never exposed to external agents.

### WebSocket HMAC Enforcement
- Per-message HMAC signatures are currently in **warn-only mode** for backward compatibility
- Once all retention-mcp clients support message signing, enforcement will be enabled
- Session timeout (4h) provides a hard bound on compromise window

## Memory Safety

### Command Result Eviction
- `_command_results` and `_command_subscribers` entries are evicted on WebSocket disconnect
- TTL: 300 seconds after command completion
- `_evict_stale_commands()` called in `finally:` block of relay WebSocket handler
- Prevents unbounded memory growth from accumulated relay commands

## Automated Security Scanning
- **Scheduled task** (`security-scan`): runs every 4 hours, checks 6 categories
- **Checks**: unguarded reads, missing `_caller_id` pop, plaintext tokens, unprotected endpoints, pipeline entry ownership, WebSocket cleanup
- **Alerts**: iMessage notification on any FAIL via `scripts/notify-imessage.sh`

## Incident Response
- Security issues: security@retention.com
- 48-hour acknowledgment SLA
- Responsible disclosure program
