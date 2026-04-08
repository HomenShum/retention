# retention.sh — Canonical Narrative Memo

**Date:** March 18, 2026
**Owners:** CEO + Marketing + Engineering
**Status:** Active — all product, marketing, and sales messaging must conform

---

## One-Line Positioning

**retention.sh is the verification, evidence, and control layer for autonomous software work.**

## Narrative Structure

### QA Is the Wedge
- Mobile and web test automation is how customers discover retention.sh
- ActionSpan verification, LLM-as-Judge evaluation, and Golden Bug benchmarks are the entry point
- This is where existing revenue and pipeline live — do not dilute

### Verification Is the Platform
- Every autonomous agent action — QA, deployment, research, workflow — passes through the same verification primitives: ActionSpan clips, evidence manifests, Validation Stop Hooks
- The platform story is: "We verify what agents did, produce tamper-evident proof, and enforce policy gates"

### Agent Governance Is the Expansion
- Validation Stop Hooks gate CI/CD pipelines
- Evidence manifests satisfy SOC 2, ISO, and internal audit requirements
- Policy enforcement for any autonomous action, not just testing

## What We Are NOT

- **Not a research lab.** We are a product company. Research informs the product; it is not the product.
- **Not an agent-building platform.** Microsoft, Google, and OpenAI are commoditizing agent infrastructure. We do not compete there.
- **Not "Beyond Testing."** Testing is not something we've moved past — it's the foundation we build on.

## Messaging Guidelines

### Use
- "Verification, evidence, and control for autonomous software work"
- "The verification layer for AI agents"
- "QA is the wedge; verification is the platform"
- "Tamper-evident proof for every agent action"
- "Agent governance" when describing policy enforcement

### Do Not Use
- "Autonomous Research" as a top-level product category
- "Full Workflow Platform" as a positioning statement
- "Research lab" in any customer-facing context
- "Beyond Testing" as a section header or tagline

## Decision Log

This memo was produced from Deep Simulation #2 (March 18, 2026) with consensus from:
- Strategy Architect, Engineering Lead, Growth Analyst, Design Steward, Security Auditor, Ops Coordinator
- Validated by 20-viewpoint MiroFish perspective burst

Key finding: 41% of backend files already sit in QA/verification/test surfaces (173/424), and 5 of the last 6 user-facing commits were QA/evidence/demo-oriented. The codebase already leans this way — the narrative now matches.

## Action Items

1. **CEO + Marketing + Eng (Mar 20):** This memo is the source of truth
2. **Marketing + RevOps (Mar 24):** Update homepage, pricing, and deck copy
3. **Design + Frontend (Mar 23):** Normalize marketing pages to one IA
4. **Engineering (ongoing):** Prioritize durable sessions + unified telemetry before broader claims
5. **Product + Marketing (Mar 24):** Ship one proof asset from live QA runs
