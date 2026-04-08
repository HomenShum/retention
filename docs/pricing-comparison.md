# retention.sh — Pricing Comparison: Device Farm vs Local-First

> For demo video section 3: Why local-first QA wins on cost.

---

## Device Farm Pricing (March 2026)

### AWS Device Farm

| Resource | Price | Notes |
|----------|-------|-------|
| Remote access (manual) | $0.17/min | Per device minute |
| Automated testing | $0.17/min | Per device minute |
| Private devices (dedicated) | $200/mo | Per device slot |
| Unmetered plan | $250/mo | Unlimited minutes, 1 device slot |

**Typical cost for a 10-task QA run:**
- 10 tasks x ~3 min each = 30 min x $0.17 = **$5.10 per run**
- 10 runs/day = **$51/day** = **$1,530/month**

### BrowserStack

| Plan | Price | Includes |
|------|-------|----------|
| Live (manual) | $29/mo | 1 parallel session |
| Automate Pro | $199/mo | 5 parallel, 3000 min |
| Automate Team | $599/mo | 25 parallel, unlimited |
| App Automate | $199/mo | 5 parallel mobile |
| Enterprise | Custom | Dedicated infra |

**Typical cost for CI/CD QA loop:**
- Automate Pro (5 parallel): **$199/mo** + overage at $0.07/min
- With heavy usage (10K min): **$199 + $490 overage = $689/mo**

### Sauce Labs

| Plan | Price | Includes |
|------|-------|----------|
| Free | $0 | Very limited |
| Team | $149/mo | 5 parallel, limited minutes |
| Enterprise | $349+/mo | Custom parallel, VMs |
| Real Devices | $199+/mo | Physical device access |

### Google Firebase Test Lab

| Resource | Price | Notes |
|----------|-------|-------|
| Virtual devices | $1/device/hr | Android emulators |
| Physical devices | $5/device/hr | Real phones |
| Spark plan | Free | 10 tests/day on virtual, 5 on physical |

**Typical cost for daily regression:**
- 10 virtual device hours/day = **$10/day = $300/month**
- With physical devices: **$50/day = $1,500/month**

---

## retention.sh Local-First Cost

| Resource | Price | Notes |
|----------|-------|-------|
| Emulator compute | **$0** | Runs on developer's machine |
| Browser (Playwright) | **$0** | Local headless Chromium |
| WebSocket relay | **$0** | Outbound connection — no tunnel needed |
| retention.sh MCP | **$0** | Open-source proxy |
| LLM tokens (verdict/judge) | **~$0.02-0.15/run** | GPT-5.4-mini for judging |
| Claude Code subscription | **$100-200/mo** | Already paid by developer |

**Typical cost for a 10-task QA run:**
- LLM judging: 10 tasks x ~2K tokens each = ~$0.05
- Compute: $0 (local)
- **Total: ~$0.05 per run**
- 10 runs/day = **$0.50/day = $15/month**

---

## Side-by-Side Comparison

```
Monthly cost for daily regression (10 tasks, 10 runs/day)

┌──────────────────────────┬────────────┬────────────────────────────┐
│ Provider                 │ Monthly $  │ What You Get               │
├──────────────────────────┼────────────┼────────────────────────────┤
│ AWS Device Farm          │  $1,530    │ Remote device minutes      │
│ BrowserStack Automate    │    $689    │ Cloud browser/device time  │
│ Sauce Labs Enterprise    │    $500+   │ Cloud VMs + real devices   │
│ Firebase Test Lab        │    $300    │ GCP-hosted emulators       │
├──────────────────────────┼────────────┼────────────────────────────┤
│ retention.sh (local-first)  │     $15    │ Local compute + LLM judge  │
└──────────────────────────┴────────────┴────────────────────────────┘

Savings: 95-99% vs cloud device farms
```

---

## Token Cost Breakdown (Per Run)

### No-TA Baseline (Raw Claude Code)

When Claude Code tries to QA an app without retention.sh, it typically:
- Takes screenshots manually (multiple tool calls)
- Reads DOM/HTML to understand state
- Retries blindly on failure (2-5 retries)
- No structured verdict — just "looks ok" or guesses

| Step | Input Tokens | Output Tokens | Model | Cost |
|------|-------------|---------------|-------|------|
| Screenshot + DOM read | ~4,000 | ~500 | claude-sonnet-4-6 | $0.020 |
| Analyze screenshot | ~8,000 | ~1,000 | claude-sonnet-4-6 | $0.039 |
| Retry 1 (blind) | ~6,000 | ~800 | claude-sonnet-4-6 | $0.030 |
| Retry 2 (blind) | ~6,000 | ~800 | claude-sonnet-4-6 | $0.030 |
| **Total per task** | **~24,000** | **~3,100** | | **$0.119** |
| **10 tasks** | **~240,000** | **~31,000** | | **$1.19** |

### TA-Assisted (retention.sh Fix Loop)

retention.sh uses cheaper models for judging, captures evidence once, and returns a compact bundle:

| Step | Input Tokens | Output Tokens | Model | Cost |
|------|-------------|---------------|-------|------|
| Run flow (Playwright) | 0 | 0 | — | $0.000 |
| Capture evidence | 0 | 0 | — | $0.000 |
| LLM judge verdict | ~1,500 | ~300 | gpt-5.4-mini | $0.002 |
| Failure summary | ~800 | ~200 | gpt-5.4-mini | $0.001 |
| Fix context | ~500 | ~150 | gpt-5.4-mini | $0.001 |
| **Total per task** | **~2,800** | **~650** | | **$0.004** |
| **10 tasks** | **~28,000** | **~6,500** | | **$0.04** |

### Token Cost Comparison

```
Per-run token cost (10 tasks):

  No-TA (raw Claude Code):     $1.19    ████████████████████████████████
  TA-Assisted:                 $0.04    █

  Savings: 97% fewer tokens wasted on blind retries
```

---

## What You're Really Paying For

| Device Farms | retention.sh |
|-------------|-----------|
| Renting hardware you don't need | Using hardware you already have |
| Per-minute billing on remote VMs | Zero compute cost (local) |
| Vendor lock-in to their infra | Works with any local setup |
| No fix loop — just test results | Judged fix loop feeds back to agent |
| Separate tool from coding workflow | Lives inside Claude Code |

---

## The Pitch Line

> "Cloud device farms charge $500-1,500/month for remote compute.
> retention.sh runs on your laptop for $15/month in LLM tokens.
> Same evidence, better fix loop, 99% less cost."

---

## Caveats (Be Honest)

- retention.sh currently supports 1 emulator at a time (no parallel device farm)
- Local-first means developer's machine must be on and running
- No physical device testing yet (emulator only)
- LLM judge accuracy depends on model quality
- Not suitable for massive test suites (100+ tests) yet — optimized for critical-path workflows

These are real limitations. The pitch is not "replace your device farm." The pitch is: **"For the coding-agent QA loop, local-first is faster, cheaper, and more precise."**
