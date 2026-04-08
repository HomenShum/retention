# retention.sh: 3-Month Operating Memo

*For internal team, CEO, and investors. Updated 2026-03-29.*

---

## 1. What We Are

retention.sh is the workflow intelligence and verification layer that sits above daily agent surfaces. We capture workflows, replay the cheapest valid path, and audit shortcuts before reuse.

We are NOT:
- A generic AI testing tool
- An agent runtime / browser agent
- A data brokerage company
- A replacement for BrowserStack / device clouds (day one)

## 2. What We Monetize

### Revenue Model

| Layer | Description | Pricing |
|-------|-------------|---------|
| **Paid Pilot** | 4-6 week engagement around 1-2 workflows | $5K-$15K per pilot |
| **Hosted Intelligence** | Dashboard + trajectory memory + replay + savings | Monthly SaaS (usage-based) |
| **Enterprise** | Private deployment + policy + audit controls | Annual contract |

### What the customer pays for:
- Verified trajectory memory
- Replay and rerun intelligence
- Workflow compression
- Shortcut validation
- Operational visibility
- Cheaper long-run execution

### What the customer does NOT pay for:
- Raw tool access (open MCP kit)
- Sample workflows and schemas
- Basic benchmark methodology

## 3. Cost Structure

| Category | Monthly Est. | Notes |
|----------|-------------|-------|
| Compute (inference) | $500-$2K | Model API calls for runs |
| Infrastructure | $200-$500 | Vercel, Render, storage |
| Team | Variable | Engineering + GTM |
| Device/Emulator | $100-$300 | Android emulator infra |

**Unit economics target:** Each replay should cost <25% of the original full crawl. By run 5+, the trajectory is stable enough that reruns approach near-zero marginal cost.

## 4. Revenue vs Cost Logic

```
Full crawl cost:     ~$0.058 per run (1.4M tokens)
Trajectory replay:   ~$0.013 per run (310K tokens) = 77.6% savings
Customer charge:     ~$0.05-$0.10 per verified run
Gross margin:        ~74-87% on replay runs
```

The product gets cheaper to operate over time while the customer value increases.

## 5. Moat

Our moat is NOT basic MCP tools. Our moat IS:

1. **Saved trajectories** — captured exploration paths, reusable across runs
2. **Replay intelligence** — checkpoint validation, partial replay, drift detection
3. **Workflow compression** — fewer steps, same verified outcome, audited shortcuts
4. **Longitudinal memory** — trajectory durability over N=1/5/10/50 runs
5. **Dashboard visibility** — savings, before/after, rerun economics
6. **Optimization audit** — propose shortcut, verify it works, approve before promotion

Competitors can clone the MCP tools in a weekend. They cannot clone 6 months of trajectory memory, compression history, and durability data.

## 6. Success Criteria (12-Week Runway)

### ON TRACK if:

**Product (weeks 1-4)**
- [ ] Cloud dashboard live and stable
- [ ] Saved trajectory replay working on 3+ workflows
- [ ] Before/after comparison visible in dashboard
- [ ] TCWP package format adopted internally

**Usage (weeks 4-8)**
- [ ] 5-10 external users actively running workflows
- [ ] 2-3 design partners or paid pilots engaged
- [ ] Weekly repeat usage from at least 3 users
- [ ] At least 1 workflow family where TA is becoming reflexive

**Revenue (weeks 8-12)**
- [ ] 1 paid pilot closed ($5K+)
- [ ] 2-3 more in pipeline
- [ ] 1 strong case study people actually share
- [ ] 1 clear "why TA" message that resonates in demos

**Benchmark**
- [ ] BrowserStack comparison benchmark published
- [ ] N=1/5/10 durability results published
- [ ] Token/time/cost savings tables public

### OFF TRACK if:

- Usage is only internal
- Still mainly demoing one-off wow moments
- No repeated workflow reuse from external users
- No one willing to pay for a pilot
- Team is arguing about whether we are a testing tool, agent shell, or data company

## 7. How the Moat Ties to Next Plans

### Weeks 1-2 (NOW)
Ship dashboard. Make replay savings legible. Finalize starter MCP workflow. Package one flagship demo.

### Weeks 3-6
Onboard first design partners. Run benchmark + ROI reports. Harden N=5/N=10 workflows. Publish one shareable case study.

### Weeks 7-12
Convert first pilot(s). Add trajectory audit/shortcut recommendation. Tighten local-to-cloud sync. Prove repeated weekly usage. Decide where to go deeper: mobile/browser ops vs enterprise workflow layer.

## 8. Ecosystem Connections

| Surface | Role | Integration |
|---------|------|-------------|
| **Claude Code** | Primary habit loop | MCP tools, subagents, hooks |
| **OpenClaw** | Ambient intake/notification | Gateway channel, skill dispatch |
| **Paperclip** | Distribution channel | Downstream execution surface |
| **3DClaw** | Execution surface | Device/spatial workflows |
| **GitHub OSS** | Distribution + trust | Starter kit, schemas, examples |

**Rule:** Daily habit lives in agent surfaces. Truth lives in TA. Execution packets move outward. Telemetry flows back into TA. Shareable artifacts publish outward again.

## 9. Resource Allocation (20/80)

| Allocation | % | What |
|-----------|---|------|
| Product Core | 40% | Trajectory, replay, checkpoints, failure bundles, savings dashboard |
| External Proof | 25% | Benchmark pages, flagship runbooks, demo flows, case studies |
| Pilot Conversion | 20% | Design partner onboarding, workflow setup, ROI reporting |
| Ecosystem + Distribution | 15% | MCP starter kit, GitHub, docs, integrations |

## 10. Team Alignment

Every team member must understand the same product truth:

> We are not selling execution. We are selling verified workflow memory and cheaper valid repetition.

### Internal rules:
- One canonical workflow graph
- One benchmark methodology
- One savings story
- One ICP for first revenue
- One dashboard narrative
- One open vs closed strategy

### Feature filter:
Every feature must improve at least one of:
- Replay quality
- Rerun economics
- Artifact quality
- Memory durability
- Pilot conversion
- Product legibility

If not, it is a side quest.

## 11. One-Line Answer for CEO/Investors

> retention.sh monetizes the intelligence layer above agent execution: we reduce verification cost over time by turning exploratory runs into reusable, audited workflows with visible savings, then sell that capability first through paid pilots and then through a hosted operating layer.

## 12. Customer Story

> retention.sh lowers the cost to achieve the same validated workflow outcome over time.

That is stronger than selling raw runs.
