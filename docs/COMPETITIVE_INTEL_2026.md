# Competitive Intelligence: Open-Source Agent Infrastructure (April 2026)

Research date: 2026-04-08. Sources: GitHub repos, official docs, comparison posts, press releases.

---

## 1. OpenAI Codex CLI (open-sourced, Apache-2.0)

**Repo**: https://github.com/openai/codex (74.2k stars, 10.5k forks, 5,233 commits)
**Structure**: `codex-cli/` (TypeScript), `codex-rs/` (Rust rewrite), `sdk/`, `docs/`, `.codex/skills/`

### What it does

Terminal-native coding agent. Reads repo, edits files, runs commands, iterates until tests pass. Ships as CLI, VS Code extension, desktop app, and cloud (Codex Web). Installs via `npm i -g @openai/codex` or `brew install --cask codex`.

### Key technical patterns

| Pattern | Detail | retention.sh relevance |
|---------|--------|----------------------|
| **OS-level sandbox** | macOS: Seatbelt (`sandbox-exec`). Linux: bubblewrap + seccomp (default), legacy Landlock fallback. Windows: WSL or native sandbox. Network access denied by default. | **ADOPT**: retention.sh runs agent actions on emulators -- sandbox isolation for Playwright/ADB steps would prevent lateral damage. Implement per-action sandboxing with network policies. |
| **Approval policies (4 levels)** | `untrusted` (ask everything), `on-request` (ask only for risky ops), `never` (fully autonomous), `granular` (per-category allow/deny for sandbox, rules, MCP, permissions, skills). Configurable in `config.toml`. | **ADOPT**: Map to retention.sh's validation gates. `ta_request_validation_gate` is the right primitive -- extend it with granular per-tool-category policies (crawl vs. test vs. deploy). |
| **TOML config with profiles** | `~/.codex/config.toml` + `.codex/config.toml` (project-scoped). Named profiles: `codex --profile full_auto` switches sandbox+approval combos. `requirements.toml` for org-level enforcement. | **ADOPT**: retention.sh config is scattered. Consolidate into a single `retention.toml` with profiles (dev, ci, strict) and org-level overrides via `requirements.toml`. |
| **`codex exec` (non-interactive mode)** | Runs agent headless, pipes results to stdout. Powers CI/CD integration. | **Already have**: `retention.run_web_flow` and `retention.quickstart` serve this role. But Codex's clean stdout piping is simpler -- consider adding `retention exec "QA my app"` CLI entry point. |
| **GitHub Action (`codex-action@v1`)** | First-party GH Action for CI. Installs CLI, starts proxy, runs `codex exec` under specified permissions. Auto-fix failing CI. Read-only sandbox mode for safe PR reviews. | **BUILD**: Ship `retention-action@v1` GitHub Action. Run `retention.quickstart` on PR open, post results as PR comment. This is the distribution wedge for CI/CD adoption. |
| **Subagents** | Configurable via `[agents]` in config.toml. Each subagent has its own config file, description, and nickname. Only spawns when explicitly asked. Each does own model+tool work. | **Already have**: retention.sh has NemoClaw subagent pattern. Codex's config-driven agent roles are cleaner -- consider adding `[agents]` section to `retention.toml`. |
| **Skills system** | Markdown files in `.codex/skills/`. Loaded as context for specific task types. | **Already have**: retention.sh has `.claude/skills/`. Compatible pattern -- ensure retention.sh skills are discoverable via the same directory convention. |
| **MCP integration** | STDIO or streaming HTTP MCP servers in config.toml. Can also run Codex itself AS an MCP server. `codex mcp` CLI for management. | **OPPORTUNITY**: retention.sh IS an MCP server. Codex users can already connect to it. Ship explicit `codex mcp add retention` one-liner in docs. |
| **Codex SDK (programmatic)** | Control Codex from CI, internal tools, or other agents. `codex exec` + SDK for building meta-agents. | **INTEGRATE**: Build a Codex SDK adapter so retention.sh can dispatch QA tasks to Codex agents, not just Claude Code agents. Model-agnostic QA. |
| **Rust rewrite (`codex-rs/`)** | Performance-critical paths in Rust. Parallel to TypeScript CLI. | **WATCH**: If retention.sh ever needs sub-100ms tool dispatch, Rust is the path. Current Node.js is fine for now. |

### Threat assessment
Codex itself is NOT a QA tool -- it's a coding agent. But `codex-action` in CI + auto-fix patterns could reduce the perceived need for dedicated QA by making "fix it yourself" the default response to bugs. retention.sh's wedge is that Codex can fix code but can't FIND visual/interaction bugs on real devices. The combo is: retention.sh finds, Codex fixes.

---

## 2. Anthropic Claude Code (open-sourced, official repo)

**Repo**: https://github.com/anthropics/claude-code (112k stars, 18.6k forks, 583 commits)
**Leaked source**: Full 512k-line TypeScript agent harness leaked via npm sourcemap on 2026-03-31

### What it does

Terminal-native agentic coding tool. Understands codebase, executes tasks, handles git workflows. Ships as CLI (`@anthropic-ai/claude-code`), VS Code extension, desktop app.

### Key technical patterns

| Pattern | Detail | retention.sh relevance |
|---------|--------|----------------------|
| **Hooks system (12 event types)** | `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStop`, `Notification`, etc. Configured in `.claude/settings.json`. Hooks can `allow`, `deny`, `ask`, or inject messages. Run as external scripts. | **ADOPT**: retention.sh's validation gate is a coarse version of this. Build a hooks system for the QA pipeline: `PreCrawl`, `PostCrawl`, `PreTestExec`, `PostTestExec`, `OnFailure`, `OnVerdict`. Let users inject custom validation at each stage. |
| **settings.json configuration** | Project-level (`.claude/settings.json`), user-level (`~/.claude/settings.json`), org-level. Controls permissions, allowed tools, denied tools, MCP servers, hooks. | **Already have**: retention.sh uses `launch.json` for preview configs. Extend to a full `settings.json` with tool permissions, hook registration, and org-level overrides. |
| **CLAUDE.md memory** | Project instructions loaded from `CLAUDE.md` or `.claude/CLAUDE.md`. Persistent across sessions. Rules in `.claude/rules/`. | **OPPORTUNITY**: retention.sh could ship a `RETENTION.md` convention -- project-level QA instructions that persist across runs. "Always test the checkout flow", "Skip the admin panel", "Critical flows: login, search, purchase". |
| **Subagent architecture** | Agent tool spawns focused sub-agents. Parent delegates, child reports back. `parent_tool_use_id` for tracking. Types: Explore (read-only, cheap), Plan (architecture), Bash (commands). | **ADOPT**: retention.sh's pipeline stages (crawl -> workflow -> testcase -> execute) could each be a subagent. Parent orchestrates, each stage agent has scoped permissions and tools. |
| **File checkpointing** | Git-based rewind of file changes. Checkpoint before risky operations, rollback on failure. | **ADOPT for trajectory replay**: retention.sh already has trajectory memory. Add explicit checkpoints at each pipeline stage so failed runs can resume from the last good checkpoint instead of re-crawling. |
| **Cost tracking** | Built-in token/cost tracking per session. Exposed via SDK. | **Already have**: retention.sh tracks costs per run. Ensure parity with Claude Code's granularity (per-tool-call cost attribution). |
| **Plugins system** | Extend with custom commands, agents, and MCP servers. Programmatic registration. `.claude-plugin/` directory. | **OPPORTUNITY**: Ship retention.sh as a Claude Code plugin. Auto-registers MCP server + skills + hooks when installed in a project. |
| **Worktrees** | Git worktree isolation for parallel work. Agent operates in isolated branch. | **Already have**: retention.sh pipeline runs are already isolated. But worktree support for "test this branch" would let retention.sh QA feature branches in isolation. |

### Source leak impact
The full Claude Code source was leaked via npm sourcemap (2026-03-31). This spawned Claw Code (172k+ GitHub stars) -- a clean-room Python/Rust rewrite. The leaked architecture reveals the exact agent loop, tool dispatch, and context management patterns. retention.sh should study the leaked harness for prompt engineering and tool orchestration patterns.

---

## 3. Claude Agent SDK (officially released)

**Repos**: 
- Python: https://github.com/anthropics/claude-agent-sdk-python
- TypeScript: https://github.com/anthropics/claude-agent-sdk-typescript
- npm: `@anthropic-ai/claude-agent-sdk`
- pip: `claude-agent-sdk`

**Docs**: https://platform.claude.com/docs/en/agent-sdk/overview
**Blog**: https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk

### What it does

Renamed from "Claude Code SDK" to "Claude Agent SDK" to reflect broader scope. Gives you the same agent loop, tools, and context management that powers Claude Code, as a programmable library. Supports Python and TypeScript.

### Key primitives

| Primitive | Detail | retention.sh relevance |
|-----------|--------|----------------------|
| **`query()` function** | Main entry point. Creates agentic loop, returns async iterator. Stream messages as Claude thinks, calls tools, observes results. Handles orchestration, retries, context management. | **ADOPT**: retention.sh's `retention.run_web_flow` could be built on top of `query()`. Replace the custom agent harness with Claude Agent SDK for the orchestration layer. Reduces maintenance, gets free improvements. |
| **Sessions** | `session_id` for multi-turn conversations with full context. Resume sessions across queries. | **ADOPT**: retention.sh runs are already session-based. Wire session IDs through Claude Agent SDK for stateful multi-turn QA conversations (e.g., "now test the edge case where the user enters invalid data"). |
| **Structured output** | `output_format` option with JSON Schema. Agent's final result is validated against schema. `structured_output` field on result message. | **ADOPT**: Force all QA verdicts through structured output. Define a JSON Schema for test results (pass/fail/blocked, evidence URLs, confidence scores). Eliminates parsing of free-text agent output. |
| **Custom tools (in-process MCP)** | Define functions as tools via SDK's in-process MCP server. Claude calls them during conversation. | **ALREADY DOING**: retention.sh tools are already MCP tools. But the SDK's in-process pattern is cleaner for tools that need access to local state (screenshots, DOM snapshots). |
| **Hooks in SDK** | Same hooks as Claude Code but programmatic. `PreToolUse`, `PostToolUse` as Python/TS callbacks, not external scripts. | **ADOPT**: Programmatic hooks > script-based hooks for retention.sh. Register Python callbacks that validate each tool action (e.g., reject any crawl action that leaves the scoped URL). |
| **Subagents in SDK** | Define `AgentDefinition` with custom instructions. Include `Agent` in `allowedTools`. Parent delegates, child reports. Messages include `parent_tool_use_id`. | **ADOPT**: Build retention.sh pipeline stages as AgentDefinitions: `CrawlAgent`, `WorkflowDiscoveryAgent`, `TestGenerationAgent`, `TestExecutionAgent`. Each with scoped tools and instructions. |
| **Skills in SDK** | Load `.claude/skills/*/SKILL.md` files. Skills provide specialized capabilities. | **ALREADY HAVE**: retention.sh skills exist. Ensure they're loadable by the Agent SDK. |
| **Plugins in SDK** | Extend with custom commands, agents, MCP servers. Programmatic registration. | **BUILD**: Ship retention.sh as a Claude Agent SDK plugin. `npm install @anthropic-ai/claude-agent-sdk retention-plugin`. Auto-registers all TA tools. |
| **Bedrock/Vertex/Azure support** | `CLAUDE_CODE_USE_BEDROCK=1`, `CLAUDE_CODE_USE_VERTEX=1`, `CLAUDE_CODE_USE_FOUNDRY=1`. | **OPPORTUNITY**: retention.sh customers on AWS/GCP/Azure can use their existing Claude deployments. No separate API key needed. |
| **Tool search** | Scale to many tools with built-in tool search. Agent discovers relevant tools from large catalogs. | **ALREADY HAVE**: NodeBench's progressive discovery with 350 tools is more sophisticated. But the SDK's tool search could be used to expose retention.sh's tools dynamically. |

### Strategic implication
The Agent SDK is the platform play. Anthropic wants every agent built on this SDK. retention.sh should be a FIRST-CLASS citizen of this ecosystem -- ship as an SDK plugin, not just an MCP server. This is the distribution channel.

---

## 4. Anthropic Managed Agents (public beta, 2026-04-08)

**Pricing**: $0.08/runtime hour + standard Claude model usage (~$58/month for 24/7 agent)
**Early adopters**: Notion, Rakuten, Asana, Sentry, Vibecode

### What it does

Cloud-hosted agent runtime. Define agent via natural language or YAML, set guardrails, deploy on Anthropic's infra. Handles sandboxing, error recovery, checkpoints, auth, persistent sessions. Reduces agent deployment from 3-6 months to days.

### Key features

| Feature | Detail | retention.sh relevance |
|---------|--------|----------------------|
| **Sandboxed execution** | Secure code execution environment managed by Anthropic. | **THREAT + OPPORTUNITY**: If Anthropic hosts the runtime, retention.sh's emulator infrastructure becomes the differentiator. Managed Agents can't run Android emulators or Playwright browsers -- retention.sh can. |
| **Checkpointing** | Automatic state persistence. Resume from last checkpoint on failure. | **ADOPT**: retention.sh's trajectory replay is the same concept. Ensure checkpoints are compatible with Managed Agents' format. |
| **Scoped permissions** | Per-agent permission boundaries. | **Already have**: retention.sh has tool-level permissions. Ensure they map cleanly to Managed Agents' permission model. |
| **YAML agent definitions** | Declarative agent config. | **ADOPT**: Ship retention.sh QA agents as YAML definitions deployable on Managed Agents. `retention-qa-agent.yaml` that customers can deploy with one click. |
| **Error recovery** | Automatic retry with context preservation. | **ADOPT**: retention.sh's rerun-failures pattern is manual. Wire automatic error recovery for transient failures (network timeouts, emulator flakiness). |

### Strategic implication
Managed Agents at $0.08/hr is extremely cheap infrastructure. retention.sh should run its QA agent loops ON Managed Agents instead of self-hosting. This eliminates the backend ops burden and lets retention.sh focus on QA-specific tools and judgment.

---

## 5. Microsoft Agent Governance Toolkit (open-sourced, MIT, 2026-04-02)

**Repo**: https://github.com/microsoft/agent-governance-toolkit
**Languages**: Python, TypeScript, Rust, Go, .NET (7 packages)
**Tests**: 9,500+ tests, continuous fuzzing via ClusterFuzzLite

### What it does

Runtime security governance for autonomous AI agents. Policy enforcement engine that intercepts every agent action before execution. Addresses all 10 OWASP Agentic AI Top 10 risks.

### Key components

| Component | Detail | retention.sh relevance |
|-----------|--------|----------------------|
| **Agent OS (policy engine)** | Stateless policy engine. Intercepts every action. Sub-millisecond p99 latency (<0.1ms). | **ADOPT**: retention.sh's validation gate is slow (network round-trip). Agent OS pattern could enforce policies locally before actions execute. Critical for enterprise customers who need audit trails. |
| **Agent Mesh (identity/trust)** | Cryptographic identity (DIDs + Ed25519). Inter-Agent Trust Protocol (IATP). Dynamic trust scoring (0-1000 scale, 5 behavioral tiers). | **WATCH**: retention.sh doesn't need DID-level identity yet. But trust scoring for agent actions (how confident are we this test result is correct?) maps directly to retention.sh's evidence quality scores. |
| **Agent Runtime (execution rings)** | Dynamic execution rings (CPU privilege level metaphor). Saga orchestration for multi-step transactions. Kill switch for emergency termination. | **ADOPT the saga pattern**: retention.sh QA pipelines ARE multi-step transactions (crawl -> workflow -> test -> verdict). Saga orchestration with compensating actions (rollback crawl data if workflow discovery fails) would improve reliability. |
| **Agent SRE (reliability)** | SLOs, error budgets, circuit breakers, chaos engineering, progressive delivery. | **ADOPT**: retention.sh should define SLOs for QA pipelines (p95 completion < 10 min, false positive rate < 5%). Circuit breakers for flaky emulator connections. Error budgets for acceptable test failures. |
| **Agent Compliance** | Automated governance verification. EU AI Act, HIPAA, SOC2 mapping. OWASP Agentic AI Top 10 evidence collection. | **OPPORTUNITY for enterprise**: retention.sh + Agent Governance Toolkit = compliant QA pipeline. Ship an integration guide for enterprise customers who need SOC2/AI Act compliance for their testing infrastructure. |

### Strategic implication
Microsoft is defining the governance layer for ALL agent frameworks (works with LangChain, AutoGen, etc.). retention.sh should integrate early. Being "Agent Governance Toolkit compliant" is a selling point for enterprise.

---

## 6. Claw Code (open-source Claude Code clone)

**Stars**: 172k+ GitHub stars (grew from 72k in days)
**Origin**: Clean-room Python/Rust rewrite based on Claude Code sourcemap leak (2026-03-31)
**GitHub**: https://github.com/openclaw/openclaw

### What it does

Open-source agent harness that replicates Claude Code's architecture. Uses OpenAI's Codex as orchestration layer. Reveals the exact agent loop patterns (tool dispatch, context management, error recovery) that were previously proprietary.

### retention.sh relevance

| Pattern | Detail | Action |
|---------|--------|--------|
| **Agent harness architecture** | Full tool dispatch, context window management, retry logic, permission system exposed in Python/Rust. | **STUDY**: The harness engineering patterns are directly applicable to retention.sh's QA agent loop. Specifically: how context is managed across long multi-tool sessions, how retries work, how permissions are checked inline. |
| **Multi-provider support** | Works with OpenRouter, DeepSeek, Gemini, local models. Not locked to one LLM. | **ADOPT**: retention.sh already has NemoClaw for free-tier models. Claw Code's provider abstraction pattern is cleaner -- use it to support any model backend for QA agent loops. |
| **172k stars = massive community** | Fastest-growing AI repo in 2026. | **OPPORTUNITY**: Ship a retention.sh plugin for Claw Code. Tap into the 172k-star community for distribution. |

---

## 7. Other Notable Frameworks

### LangGraph (24.8k stars, 34.5M monthly downloads)
- Graph-based agent orchestration. Nodes (agents/tools/checkpoints), edges (transitions/conditions).
- Built-in persistence, replay, and audit trails.
- 87% task success rate on benchmarks.
- LangSmith integration for observability.
- **retention.sh action**: LangGraph's graph abstraction maps well to QA pipelines. Consider offering a LangGraph adapter: `from retention import RetentionQAGraph`.

### CrewAI (44.3k stars, 5.2M monthly downloads)
- Role-based multi-agent orchestration. Define agents with roles, goals, backstory.
- Streaming tool call events (added Jan 2026).
- **retention.sh action**: CrewAI's "crew" concept maps to retention.sh's pipeline stages. Low priority -- LangGraph is more production-ready.

### OpenAI Agents SDK (separate from Codex)
- Agent definitions, guardrails, orchestration, results/state management.
- Agent Builder (visual) + ChatKit (embeddable UI).
- Voice agents support.
- **retention.sh action**: The Agents SDK's guardrails pattern (input/output validation on agent responses) should be adopted for QA verdict validation.

---

## 8. Synthesis: What retention.sh Should Build

### P0 -- Build immediately (this sprint)

1. **GitHub Action (`retention-action@v1`)**: Run `retention.quickstart` on PR open, post QA results as PR comment. This is the #1 distribution wedge based on Codex's `codex-action` success pattern.

2. **Structured output for verdicts**: Use Claude Agent SDK's `output_format` to force all QA verdicts through JSON Schema validation. Eliminates free-text parsing, makes verdicts machine-readable for CI gates.

3. **Pipeline hooks system**: `PreCrawl`, `PostCrawl`, `PreTestExec`, `PostTestExec`, `OnFailure`, `OnVerdict`. Let users inject custom validation. Pattern stolen directly from Claude Code's hooks.

### P1 -- Build this month

4. **`RETENTION.md` convention**: Project-level QA instructions that persist across runs. Users define critical flows, skip patterns, known flaky areas. Loaded automatically on every run. Mirrors Claude Code's `CLAUDE.md`.

5. **Claude Agent SDK plugin**: Ship retention.sh as a first-class SDK plugin. `npm install retention-agent-plugin`. Auto-registers all QA tools. This is the platform play -- be a native citizen of Anthropic's ecosystem.

6. **Codex SDK adapter**: Let retention.sh dispatch QA tasks to Codex agents (not just Claude). Model-agnostic QA pipeline.

7. **Saga orchestration for pipelines**: Adopt Agent Governance Toolkit's saga pattern. Each pipeline stage is a saga step with compensating actions. If workflow discovery fails, roll back crawl data and retry with different strategy.

### P2 -- Build next month

8. **Agent Governance Toolkit integration**: Be "AGT compliant" for enterprise customers. Policy enforcement, audit trails, compliance evidence collection.

9. **Per-action sandboxing**: Adopt Codex's OS-level sandbox pattern for retention.sh's agent actions. Each Playwright/ADB action runs in an isolated sandbox with scoped network access.

10. **Managed Agents deployment**: Ship retention.sh QA agents as YAML definitions deployable on Anthropic's Managed Agents ($0.08/hr). Customers get hosted QA without managing infra.

11. **LangGraph adapter**: `from retention import RetentionQAGraph`. Tap into LangGraph's 34.5M monthly download base.

12. **SLOs and error budgets**: Define QA pipeline SLOs (p95 < 10 min, false positive < 5%). Circuit breakers for emulator flakiness. Adopt Agent SRE patterns.

---

## 9. Threat Matrix

| Competitor | Threat Level | Why | Mitigation |
|-----------|-------------|-----|-----------|
| **Codex + codex-action** | MEDIUM | Auto-fix in CI reduces perceived need for separate QA. "Why find bugs when Codex can fix them?" | retention.sh finds VISUAL/INTERACTION bugs Codex can't see. Position as complementary: retention finds, Codex fixes. Ship the integration. |
| **Claude Managed Agents** | LOW-MEDIUM | Cheap hosted agents could commoditize the runtime layer. | retention.sh's value is the QA-specific tools (emulator, Playwright, evidence collection), not the runtime. Run ON Managed Agents. |
| **Claw Code** | LOW | Open-source harness commoditizes agent orchestration. | retention.sh's moat is QA domain expertise + device infrastructure, not the agent loop. Claw Code helps by growing the agent ecosystem. |
| **Agent Governance Toolkit** | OPPORTUNITY | Enterprise governance requirement creates a gate retention.sh can help customers pass. | Integrate early. Be the "compliant QA agent" story. |
| **LangGraph** | LOW | General-purpose orchestration, not QA-specific. | Offer adapter. LangGraph users become retention.sh users. |
| **Bug0/Canary/Momentic** | MEDIUM-HIGH | Direct QA competitors with similar "AI finds bugs" positioning. | retention.sh's trajectory replay + memory = compound cost savings. These competitors don't have durable path memory. |

---

## 10. Key Benchmarks (April 2026)

| Metric | Codex | Claude Code |
|--------|-------|-------------|
| GitHub stars | 74.2k | 112k |
| Terminal-Bench 2.0 | 77.3% (GPT-5.3-Codex) | 65.4% |
| SWE-bench Pro | ~similar range | ~similar range |
| VS Code Marketplace | Surpassed in installs | Higher installs + reviews |
| Token efficiency | ~2x more efficient than Sonnet | Opus 4.5/4.6 improved efficiency |
| Pricing sweet spot | $20/mo plan (generous limits) | $17/mo plan (tighter limits) |
| Open-source license | Apache-2.0 | Source leaked; repo is docs/examples |

---

*Generated 2026-04-08. Review quarterly -- this space moves fast.*
