# retention.sh: Product Surface Map

*What surfaces where, what's gated, how it ties to monetization.*

---

## The Three Layers

Everything we built maps to three layers. The rule is simple:
- **Public** = trust builder, top of funnel
- **Free (email-gated)** = habit builder, activation
- **Paid** = intelligence layer, revenue

```
PUBLIC (anyone)        →  trust
FREE (email-gated)     →  habit
PAID (pilot/SaaS)      →  intelligence + revenue
```

---

## Surface Map

### Layer 1: PUBLIC — Trust Builders (no gate)

These pages exist to build credibility and drive inbound. Anyone can see them.

| Surface | Route | Purpose | What It Shows |
|---------|-------|---------|---------------|
| Landing page | `/` | Top of funnel | Value prop, team, how it works, install oneliner |
| Benchmark landing | `/benchmarks` | Proof | 5 workflows, savings tables, BrowserStack comparison, methodology |
| Pricing | `/pricing` | Conversion | Pilot pricing, SaaS tiers, what's included |
| Docs / Install | `/docs/install` | Activation | One-command setup, platform detection |
| Demo showcase | `/demo/showcase` | Proof | Interactive 3-step demo (explore → fix → rerun) |
| Competitive intel | `/competitive-intel` | Positioning | Market landscape, where we fit |
| Security | `/security` | Trust | Security posture, compliance roadmap |
| Blog / case studies | (future) | Trust | Published benchmarks, customer stories |
| TCWP schema spec | GitHub | Trust | Open spec, community adoption |

**Why public:** Buyers research before they talk to sales. If they can't find proof, they move on. Public benchmarks and methodology are the strongest trust signal.

### Layer 2: FREE — Habit Builders (email-gated)

These require email signup (via DemoGate / `/signup`) but no payment. The goal is daily usage from Claude Code / MCP.

| Surface | Route / Tool | Purpose | What It Shows |
|---------|-------------|---------|---------------|
| MCP tools (local) | `ta.*` via Claude Code | Daily habit | All 120+ tools run locally, free |
| Before / After comparison | `/compare` | Aha moment | Side-by-side savings, 100-run projection |
| Trajectory portfolio | `/trajectories` | Portfolio view | All saved trajectories, health, drift |
| Memory dashboard | `/memory` | Personal dashboard | Tokens saved, hit rate, replay count |
| Local memory | `/memory/local` | Cache visibility | What's cached locally, import/export |
| TCWP bundle viewer | `/tcwp` | Package inspection | Browse, validate, inspect bundles |
| Benchmark report | `/report` | Model comparison | Head-to-head model benchmarks |
| Curated demo | `/demo/curated` | Guided QA experience | Step-by-step live QA walkthrough |

**Why free:** This is where the habit loop lives. Users must be able to run `ta.crawl.url`, see savings, browse trajectories, and inspect TCWP bundles without paying. The moment they see 78% token savings on their own workflow, they're hooked.

**What makes it free, not paid:** All data stays local. No cloud intelligence, no longitudinal rollups, no team features, no export profiles beyond `ops`.

### Layer 3: PAID — Intelligence Layer (pilot / SaaS)

This is where revenue comes from. Requires active pilot or subscription.

| Surface | Route / Feature | Pricing Tier | What It Unlocks |
|---------|----------------|-------------|-----------------|
| Team dashboard | `/memory/team` | SaaS | Multi-user workflows, shared trajectories, contribution metrics |
| Savings timeline | (in dashboard) | SaaS | Cumulative savings over time, trend charts |
| Drift detection dashboard | (in dashboard) | SaaS | Per-trajectory health scoring, drift alerts |
| Workflow compression viz | (in dashboard) | SaaS | Original vs compressed side-by-side, stage-by-stage cost |
| Optimization candidates | (in dashboard) | SaaS | Proposed shortcuts, audit status, verified savings |
| Training export profile | `retention.tcwp.export_profile profile=training` | Enterprise | Fine-tuning data, preferences, reward signals |
| Sales export profile | `retention.tcwp.export_profile profile=sales` | SaaS | Buyer-facing proof packages |
| ROI reports | `retention.savings.roi` + dashboard | SaaS | Breakeven analysis, cumulative ROI, forecast |
| Longitudinal rollups | `retention.memory.rollup` + dashboard | SaaS | Day/week/month/quarter savings aggregation |
| Cross-model benchmarks | `retention.benchmark.model_compare` + dashboard | SaaS | Model-vs-model comparison dashboards |
| Audit trail PDF | (future) | Enterprise | Compliance-ready audit report from TCWP |
| Self-hosted deployment | (future) | Enterprise | On-prem / VPC, customer-managed keys |
| Team onboarding | `retention.team.invite` + dashboard | SaaS | Invite teammates, shared memory |

**Why paid:** This is where value compounds over time. Trajectory memory, compression history, team visibility, and longitudinal savings are the things competitors cannot clone from our open tools.

---

## How It Ties to the Strategy

### Open vs Closed (direct mapping)

| Strategy Doc Says | Product Surface |
|-------------------|----------------|
| Open: MCP tools, schemas, samples | Free: all `ta.*` MCP tools run locally |
| Open: benchmark methodology | Public: `/benchmarks` page |
| Open: TCWP schema spec | Public: GitHub + `/tcwp` viewer |
| Closed: dashboard + operating workspace | Paid: `/memory/team`, savings timeline, drift dashboard |
| Closed: trajectory memory graph | Paid: longitudinal rollups, portfolio analytics |
| Closed: replay optimization + audit engine | Paid: optimization candidates, compression viz |
| Closed: team memory + enterprise layer | Paid: team dashboard, training export, self-hosted |

### Monetization (direct mapping)

| Revenue Layer | Product Surface | Price |
|--------------|----------------|-------|
| Paid Pilot ($5K-$15K) | Setup + trajectory capture + dashboard access + ROI report | One-time |
| Hosted Intelligence (SaaS) | Dashboard + savings + team + compression + benchmarks | Monthly usage-based |
| Enterprise | Self-hosted + audit trail PDF + training export + BAA/HIPAA | Annual contract |

### 12-Week Roadmap (direct mapping)

| Phase | What Ships to UI |
|-------|-----------------|
| Weeks 1-2 | Before/After page, trajectory portfolio, savings dashboard (all free-tier) |
| Weeks 3-6 | `/benchmarks` public page, TCWP viewer, team dashboard (SaaS gate) |
| Weeks 7-12 | Drift dashboard, compression viz, optimization candidates, training export (paid tier) |

---

## Trust Signals in UI

These aren't separate pages — they're embedded trust indicators throughout the product:

| Signal | Where It Appears | What It Shows |
|--------|-----------------|---------------|
| Data residency indicator | Dashboard header | "Data: Local only" or "Cloud: US-East" |
| Training consent badge | TCWP viewer, trajectory cards | "Training: Opted out" / "Training: Consented" |
| Redaction status | Export dialogs | "PII: Redacted" / "PII: None detected" |
| Provenance chain | TCWP viewer | Who created, when, what happened to the data |
| Checkpoint pass rate | Trajectory cards, before/after | "7/7 checkpoints passed" with green indicator |
| Audit hash | TCWP viewer footer | SHA-256 integrity verification |
| Export profile badge | Export dialogs | "Ops mode" / "Training mode" / "Sales mode" |

---

## The Conversion Funnel

```
AWARENESS
  Landing page, benchmarks, demo showcase, GitHub
    ↓
ACTIVATION (email gate)
  Install MCP tools → run first crawl → see savings
    ↓
HABIT (free tier)
  Daily: ta.crawl.url → retention.savings.compare → /compare
  Weekly: check /trajectories portfolio, review drift
    ↓
AHA MOMENT
  "78% token savings on MY workflow, not a demo"
    ↓
PILOT CONVERSATION
  "Can we get this on 2-3 more workflows + dashboard?"
    ↓
PAID (SaaS)
  Team dashboard, longitudinal savings, compression, benchmarks
    ↓
ENTERPRISE
  Self-hosted, training export, audit trail, BAA
```

---

## What NOT to Gate

These should never be gated because gating them kills trust:

- Benchmark methodology and results
- TCWP schema spec
- Savings calculation logic
- Example TCWP bundles
- Security/compliance documentation
- MCP tool definitions (the tool list itself)

If someone has to sign up before they can see whether our savings claims are reproducible, they won't sign up.

---

## Implementation Priority

### Already built and routed:
- `/compare` (BeforeAfterPage) — free tier
- `/trajectories` (TrajectoryPortfolioPage) — free tier
- `/tcwp` (TCWPViewerPage) — free tier
- `/benchmarks` (BenchmarkLandingPage) — public
- `/memory` (MemoryDashboardPage) — free tier
- `/memory/team` (TeamDashboardPage) — paid tier
- All MCP tools — local/free

### Needs implementation:
1. **Email gate on free-tier pages** — wrap `/compare`, `/trajectories`, `/tcwp`, `/memory` in DemoGate
2. **Paid gate on team features** — `/memory/team` shows "upgrade" prompt for non-pilot users
3. **Trust indicators** — data residency badge in dashboard header, checkpoint pass rate on trajectory cards
4. **Savings timeline chart** — add to `/memory` dashboard (paid feature preview with blurred paywall)
5. **Drift detection section** — add to `/memory` dashboard
6. **Export profile selector** — add to `/tcwp` viewer with tier-appropriate options (ops=free, training/sales=paid)

### Future (post-pilot):
- Self-hosted deployment option
- Audit trail PDF generator
- Team contribution metrics
- Cross-workflow pattern mining
- Compliance dashboard (SOC 2, HIPAA, EU AI Act status)
