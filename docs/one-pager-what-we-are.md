# retention.sh — What We Are / What We Are Not

---

## One Line

**retention.sh is a local-first QA assurance layer for coding-agent workflows.**

---

## What We Are

A judged QA fix loop that sits inside the coding-agent workflow. When a developer uses Claude Code (or any AI coding agent), retention.sh:

1. **Runs real app flows** in the developer's own local environment — browser or Android emulator
2. **Captures structured evidence** — Playwright traces, screenshots, console logs, network requests, video
3. **Localizes failure precisely** — exact failing step, root cause candidates, suggested files to patch
4. **Returns a compact failure bundle** — small enough to feed directly back into the coding agent
5. **Judges the result** — pass/fail/blocked verdict with confidence score
6. **Closes the loop** — rerun after fix, compare before/after, prove the fix works

---

## What We Are Not

| We are NOT | Why |
|-----------|-----|
| A device farm | We run on your laptop. Zero hosting cost. |
| A test framework | We don't replace Playwright or pytest. We orchestrate and judge. |
| Another MCP | MCP is the transport. We are the judged evidence loop on top. |
| A CI/CD pipeline | We run inside the dev loop, before code ever reaches CI. |
| A general agent platform | We do one thing: QA assurance for coding agents. |
| An enterprise dashboard | We return compact JSON. Dashboards are optional. |

---

## The Problem

Coding agents can write code, but they cannot verify it works on a real running app. When they try:

- They take screenshots and guess
- They retry blindly (2-5 attempts, wasting tokens)
- They have no structured failure signal
- They cannot tell you *which step* failed or *which file* to fix

**Result**: Developers still manually verify agent output. The "autonomous" loop breaks at QA.

---

## The Wedge

Anyone can wire Claude Code to Playwright via MCP. That gives you test execution.

We give you **judgment structure**:

| Capability | Raw MCP + Playwright | retention.sh |
|-----------|---------------------|-----------|
| Run a browser flow | Yes | Yes |
| Capture screenshots | Yes | Yes |
| Capture full Playwright trace | Manual | Automatic |
| Capture console + network logs | Manual | Automatic |
| Structured failure localization | No | Yes — exact step, root cause, files |
| Compact failure bundle for agent | No | Yes — <200 tokens |
| LLM-as-judge verdict | No | Yes — pass/fail/blocked + confidence |
| Fix → rerun → compare loop | No | Yes — before/after diff |
| Token-efficient evidence | No | Yes — 97% fewer tokens than blind retry |
| Session memory + self-healing | No | Yes — learns from past failures |

---

## How It Works

```
Developer asks Claude Code to fix a bug
         │
         ▼
Claude Code patches code, calls retention.sh MCP
         │
         ▼
retention.sh runs real app flow (Playwright / emulator)
         │
         ▼
Captures: trace, screenshots, logs, video
         │
         ▼
Returns compact failure bundle:
  - exact failing step
  - root cause candidates
  - suggested files
  - screenshots
         │
         ▼
Claude Code reads bundle, patches precisely
         │
         ▼
retention.sh reruns, compares before/after
         │
         ▼
Verdict: PASS ✓
```

---

## Business Model

**Local-first = no hosting costs for us or the customer.**

| Component | Who Pays |
|-----------|----------|
| Compute (browser, emulator) | Developer's local machine — $0 |
| WebSocket relay | Outbound connection — $0 |
| LLM judging tokens | ~$0.04/run (GPT-5.4-mini) |
| Claude Code subscription | Developer already pays — $100-200/mo |
| retention.sh | Free pilot → usage-based pricing |

**Comparison**: Cloud device farms charge $500-1,500/month for the same test runs.

---

## Current State (Honest)

**What works today:**
- MCP integration with Claude Code — verified
- Web QA flows via Playwright — working
- Android QA via emulator — working
- Evidence capture (trace, screenshots, logs, video) — working
- Compact failure bundles — working
- LLM-as-judge verdicts — working
- Fix context with root cause + file suggestions — working
- Before/after comparison — working
- Benchmark framework (baseline vs TA-assisted) — built, data collection in progress

**What is not ready yet:**
- No benchmarked proof of superiority over raw Claude Code (in progress)
- No external partner app validation (next: Khush, Jaynee)
- Single emulator only (no parallel device farm)
- No physical device testing
- No enterprise auth/SSO
- No hosted offering (local-first only)

---

## Next 2 Weeks

1. Run benchmark: No-TA vs TA-assisted on 3 frozen apps
2. Onboard 3 partner apps (Khush, our app, Jaynee)
3. Collect real success/fail/token data
4. Publish results
5. Ship pilot page: "Send us your staging app and 3 workflows"

---

## The Ask

We are looking for:
- **3-5 teams** willing to pilot retention.sh on their staging app
- **3 critical workflows** per app (login, checkout, data entry, etc.)
- **2 weeks** to run the comparison and deliver results

**CTA**: Send us your staging app URL and 3 workflows. We'll run the loop and show you the results.

---

## Contact

- Product: [team@retention.com]
- Demo: [retention.com/try]
- GitHub: [github.com/HomenShum]
