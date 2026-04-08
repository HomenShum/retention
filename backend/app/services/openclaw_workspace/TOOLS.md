# retention.sh Strategy Agent — TOOLS Contract

## Tool Access Policy

Each agent role has explicit tool permissions. Tools not listed are denied by default. This follows the OpenClaw principle: "minimize tool blast radius."

## Available Tool Categories

### Slack Operations (all roles)
- `slack.get_channel_history` — Read channel messages (scoped to #claw-communications)
- `slack.get_thread` — Read thread replies
- `slack.post_message` — Post to channel (requires rubric approval)
- `slack.post_thread_reply` — Reply in thread (requires rubric approval)
- `slack.search_messages` — Search across channels

### Codebase Operations (Engineering Lead, Design Steward, Security Auditor)
- `ta.codebase.search` — Full-text search across codebase
- `ta.codebase.read_file` — Read file contents
- `ta.codebase.recent_commits` — Last N commits with messages
- `ta.codebase.git_status` — Current working tree status

### Investor Brief Operations (Strategy Architect only)
- `ta.investor_brief.get_state` — Read current brief state
- `ta.investor_brief.update_section` — Update a brief section

### Competitive Intelligence (Growth Analyst only)
- `ta.competitive.search` — Search competitive data
- `ta.competitive.report` — Generate competitive report

### Memory Operations (all roles, via ConvexClient)
- `memory.store` — Write memory entries
- `memory.search` — Search institutional memory
- `memory.surface_relevant` — Find prior context for current discussion

### Prediction Operations (Strategy Architect, Growth Analyst)
- `predict.run` — Run multi-perspective prediction analysis

### Claude Code Operations (Engineering Lead only, requires human approval)
- `claude_code.invoke` — Run Claude Code to make code changes (CLI headless mode via `npx @anthropic-ai/claude-code`)
- `claude_code.request_approval` — Post Slack Block Kit approval message with Approve/Reject buttons
- `claude_code.execute_with_rollback` — Create feature branch, checkpoint, run Claude Code, auto-revert on failure
- `claude_code.create_pr` — Push branch and create GitHub PR via `gh pr create`

### Agent Swarm Operations (all roles)
- `swarm.run_conversation` — Multi-role deliberation on a topic in a Slack thread
- `swarm.propose_and_build` — Propose code changes (requires ≥4/6 role consensus + human approval)
- `swarm.competitive_analysis` — Growth Analyst-led competitive landscape discussion
- `swarm.self_evolution` — Ops Coordinator-led review of decision quality and rubric health

## Execution Policy

- **Sandbox mode:** All tool calls are logged. No direct filesystem writes except via Claude Code bridge.
- **Approval required:** Posting to Slack requires boolean rubric approval (all required gates TRUE, no disqualifiers TRUE).
- **Claude Code approval:** All code changes require explicit human approval via Slack interactive buttons. Changes are isolated on feature branches with automatic rollback on failure.
- **Rate limits:** Max 1 post per 15-minute window per agent role.
- **Credential isolation:** Each role uses the same service credentials but logs independently.
- **Audit trail:** All Claude Code invocations are logged to Convex with prompt, result, files changed, and duration.

## Denied Operations

- No direct filesystem writes (use Claude Code bridge with approval gate)
- No direct API calls to external services (use provided tool wrappers)
- No credential access or management
- No user impersonation
- No message deletion or editing
- No force pushes to main/master branches
- No Claude Code invocations without human approval (approval timeout: 10 minutes)
