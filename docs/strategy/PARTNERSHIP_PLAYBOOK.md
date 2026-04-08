# retention.sh: Partnership Playbook

*Target segments, outreach scripts, sales motion, and partnership strategy.*

---

## 1. Target Customer Segments

### Tier 1: First Revenue (Weeks 1-12)

#### A. Financial Compliance / KYB / AML Ops Teams
**Pain:** Repetitive browser-native workflows in compliance portals. Every rerun from scratch costs time, money, and audit risk.
**Workflow examples:** KYB verification, AML screening, document extraction from regulatory portals.
**Why TA:** Auditable trajectories, replay economics, structured evidence packages.
**Company examples:** Sphinx-like compliance automation firms, fintech compliance teams, banking ops.

#### B. Legacy Portal Operations (Logistics / Financial Services / Healthcare)
**Pain:** Browser agents on brittle legacy systems with no clean APIs. Every UI change breaks automation.
**Workflow examples:** Freight booking, insurance claim processing, EHR data entry, legacy CRM operations.
**Why TA:** Trajectory memory survives UI drift better than selector-based approaches. Checkpoint validation catches breakage early.
**Company examples:** Kaizen-like and Simplex-like operations automation firms, 3PL ops teams.

#### C. Healthcare Admin / EHR-Integrated Ops
**Pain:** Scheduling, insurance verification, intake, billing prep across old EHR systems.
**Workflow examples:** Patient scheduling, insurance eligibility checks, prior authorization, billing code entry.
**Why TA:** HIPAA-aware redaction in TCWP permissions, compliance tags, auditable provenance chain.
**Company examples:** Novoflow-like and Paratus-like healthcare automation firms, hospital IT teams.

### Tier 2: Growth (Months 4-6)

#### D. AI-Forward Product Teams with Mobile/Browser Workflows
**Pain:** Agent-driven mobile/browser testing is expensive and non-repeatable.
**Why TA:** Structured verification, replay savings, benchmark methodology.

#### E. Internal Platform / AI Tooling Teams
**Pain:** Have runtimes and device control, need harnesses, memory, verification.
**Why TA:** TCWP as the canonical workflow package for their existing agent infra.

### Tier 3: Partners, Not Customers

#### F. Research / Benchmark / Environment Groups
**Role:** Benchmark consumers, eval partners, infrastructure collaborators.
**Not our first ICP.** Do not try to sell them TA as a product. Offer benchmark data, TCWP spec collaboration, or research partnerships.

---

## 2. Ecosystem Partners (Not Customers)

| Partner Type | Example Direction | Our Role |
|-------------|-------------------|----------|
| **Agent runtimes** | Claude Code, OpenAI Agents SDK | Habit surface — we sit above as verification/memory |
| **Browser agent infra** | Browser Use, Notte | Execution substrate — complementors, not competitors |
| **Gateway / assistant** | OpenClaw | Ambient intake, notification, skill dispatch |
| **Research / environments** | Fleet-like firms | Benchmark consumers, eval partners |
| **Device clouds** | BrowserStack | Benchmark baseline, potential integration |

**Rule:** Partners provide surfaces and execution. We provide intelligence, memory, and verification.

---

## 3. Sales Motion

### Motion: Paid Pilot First

**Do NOT sell "replace your whole stack."**
**DO sell "keep your current runtime/browser/emulator, let TA reduce verification cost."**

#### Pilot Structure
- **Duration:** 4-6 weeks
- **Scope:** 1-2 high-friction workflows
- **Price:** $5K-$15K
- **Deliverable:** Proven savings report (TCWP sales_brief), trajectory registry, dashboard access

#### Pilot Promise
1. We set up TA on your workflow (day 1-3)
2. We capture the first full crawl (day 3-5)
3. We replay with trajectory (week 2)
4. We show savings (week 3)
5. We deliver ROI report (week 4-6)

#### Success Criteria for Pilot
- Token/time/cost savings >50% on replay
- N=5 pass rate >80%
- Customer sees dashboard and understands value
- Customer wants to expand to more workflows

---

## 4. Outreach Scripts

### Cold Outreach (Email / LinkedIn / DM)

**Subject:** Cheaper workflow verification for [Company]

**Body:**
> Hey [Name],
>
> We are building retention.sh, a workflow intelligence and verification layer for agent-driven browser/computer/device workflows. We are not another generic testing tool or browser agent.
>
> We sit above your existing stack and turn exploratory runs into reusable, audited trajectories. That means lower rerun cost, visible before/after state, and much less re-exploration from scratch.
>
> We are looking for a small number of design partners with one painful workflow in [compliance / legacy portal ops / healthcare admin]. If we can prove cheaper, more repeatable verification on one workflow in 4-6 weeks, would you be open to piloting with us?
>
> Happy to share our benchmark data and savings methodology.

### Warm Introduction Request

> Hey [Mutual], quick ask — do you know anyone on the ops/automation side at [Company]? We have a new approach to agent workflow verification that cuts rerun costs by 70-80%. Looking for 2-3 design partners to prove it on real workflows. Would love an intro if it makes sense.

### Follow-Up After Interest

> Thanks for your interest. Here is how we would structure a pilot:
>
> 1. Pick your highest-friction repetitive workflow
> 2. We capture the first exploratory run (full crawl)
> 3. We replay using saved trajectory with checkpoint validation
> 4. You see the savings (tokens, time, cost) in our dashboard
> 5. We deliver a structured ROI report after 4 weeks
>
> The pilot is $[X]K and includes setup, trajectory capture, replay optimization, and weekly savings reports. Would [date] work for a 30-min setup call?

### Demo Script (15 min)

1. **Hook (2 min):** "Every time you rerun a workflow from scratch, you are paying full exploration cost again. What if each rerun got cheaper?"
2. **Show baseline (3 min):** Full crawl — 1.4M tokens, 8 minutes, $0.06
3. **Show replay (3 min):** Trajectory replay — 310K tokens, 4 minutes, $0.01 — same verified outcome
4. **Show savings dashboard (3 min):** Before/after comparison, N=5/N=10 durability
5. **Show optimization (2 min):** Proposed shortcut, audit status, verified savings
6. **Close (2 min):** "We are looking for 2-3 design partners. Interested in a 4-week pilot?"

---

## 5. Marketing Positioning

### What We Say
> retention.sh captures agent workflows, finds cheaper validated paths, and audits those shortcuts before turning them into reusable automation.

### What We Do NOT Say
- "AI testing tool"
- "Agent wrapper"
- "MCP toolkit"
- "Browser operator"
- "We replace BrowserStack"

### Proof Points to Publish
- Benchmark pages with real savings tables
- Case studies with workflow-specific results
- Before/after screenshots with checkpoint proof
- N=1/5/10 durability results
- TCWP schema spec for transparency
- Short demo clips (under 3 min)

---

## 6. Partnership Establishment Process

### For Customers (Design Partners → Pilots → Contracts)

1. **Identify** — Find teams with repetitive high-friction workflows
2. **Qualify** — Do they have budget? Is the workflow painful enough? Can we access it?
3. **Propose** — 4-6 week pilot, specific scope, clear deliverable
4. **Execute** — Set up TA, capture workflow, prove savings
5. **Report** — TCWP sales_brief + dashboard access
6. **Convert** — Expand to more workflows, monthly SaaS

### For Ecosystem Partners

1. **Identify** — Complementary runtime, infra, or channel
2. **Align** — We provide intelligence/memory, they provide execution/distribution
3. **Integrate** — MCP tools, TCWP import/export, shared schemas
4. **Co-market** — Joint demos, case studies, benchmark pages

---

## 7. What Coming Out the Other Side Looks Like

After 12 weeks, the winning state is:

- **2-3 design partners** in compliance, legacy ops, or healthcare admin
- **1 paid pilot closed**, 2-3 more in pipeline
- **1 sharp category story:** workflow intelligence + verification, not generic AI testing
- **A dashboard** that clearly shows replay, savings, rerun, and memory compounding
- **1-2 flagship benchmark pages** people actually share
- **1 habit loop** where users naturally invoke TA from Claude Code / OpenClaw / local MCP
- **Clear proof** that over time TA makes the same workflow cheaper, shorter, and more reliable

We do not need to win "all agentic AI." We need to become the obvious system people add when workflows must be remembered, replayed, audited, and made cheaper over time.
