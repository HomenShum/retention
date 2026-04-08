# retention.sh: Buyer Trust Issues and Mitigations

*What enterprise buyers worry about, and how we address each concern.*

## Market Context

The trust landscape is harsh and getting harsher:

- Only **20% of executives** trust AI agents for financial transactions (PwC AI Agent Survey)
- **67% of healthcare orgs** admit they are not ready for stricter AI compliance standards (Jimerson Birr)
- Gartner predicts **40%+ of agentic AI projects will be canceled** by end of 2027 due to escalating costs, unclear ROI, or inadequate risk controls
- Only **1 in 5 companies** has a mature governance model for autonomous AI agents
- **32% of organizations** identify unsupervised data access by AI agents as a critical threat (Kiteworks)
- EU AI Act full enforcement hits **August 2026** for high-risk systems, with penalties up to 35M EUR or 7% of global revenue
- GitHub's April 2026 data policy change (training by default, opt-out required) caused significant enterprise backlash

We must lead with the trust stack in the first sales call, not as an afterthought.

---

## 1. Data Sovereignty and Residency

### The Objection
"You're capturing our workflow trajectories — screenshots, actions, UI state — from our internal systems. Where does that data go? Who can access it? Does it leave our infrastructure?"

### Why This Matters
Multi-step AI agent workflows often move data through multiple model providers and servers across different legal jurisdictions. Data sovereignty laws now cover 50%+ of world economies, driving $5B+ in compliance investment. Compliance/KYB/AML teams work in regulated environments. Healthcare/EHR teams are under HIPAA. Financial ops teams have SOX and data residency requirements.

### Our Mitigation
- **Local-first architecture.** TCWP bundles are generated and stored locally by default. The MCP tools run in the customer's own Claude Code environment.
- **Explicit upload.** Data only moves to retention.sh Cloud when the customer explicitly exports with `ta.tcwp.export_profile`. Nothing is phoned home automatically.
- **Permissions schema.** Every TCWP bundle has a `permissions.json` with `visibility`, `export_allowed`, `training_allowed`, and `redaction_rules`.
- **Export profiles.** `ops` mode keeps data internal. `sales` mode redacts before sharing. `training` mode requires explicit `allowed_for_training: true` on every record.
- **Provenance chain.** `provenance.json` tracks every action taken on the data — who created it, who exported it, when, and where.

### What We Ship
- Data residency documentation
- Self-hosted deployment option (Phase 3)
- SOC 2 Type II audit (timeline TBD — communicate honestly)
- Export audit log visible in dashboard

---

## 2. PII in Trajectory Data

### The Objection
"Your agent is taking screenshots and recording actions inside our EHR/banking portal/compliance system. Those screenshots contain patient names, account numbers, SSNs. How do you prevent PII leakage?"

### Why This Matters
Even a single leaked screenshot from an EHR system is a HIPAA violation. Financial portals contain account data subject to PCI-DSS. KYB workflows contain government IDs.

### Our Mitigation
- **Redaction rules in TCWP.** `permissions.json` supports `redaction_rules` with field-pattern matching and strategies: `remove`, `hash`, `mask`, `synthetic`.
- **PII status in dataset cards.** `dataset_card.json` has explicit `pii_status` (none/redacted/masked/synthetic_replaced/contains_pii) and `pii_types_present`.
- **Export profiles enforce redaction.** Both `training` and `sales` profiles have `redaction_required: true`.
- **State snapshots use fingerprints, not raw content.** Screen states are stored as content-addressed hashes (`sha256:...`), not full DOM dumps or unredacted screenshots.
- **Artifact redaction before export.** Screenshots can be excluded from export profiles or processed through redaction pipeline before sharing.

### What We Ship
- Automated PII detection in screenshots (OCR + pattern matching)
- Redaction pipeline that runs before any export
- "No-screenshot mode" for highly sensitive workflows (metadata-only trajectories)
- HIPAA BAA for healthcare customers (Phase 3)

---

## 3. "You'll Use Our Data to Train Models"

### The Objection
"If we give you workflow data, you'll use it to train your own models or sell it to model companies. We've seen this with every AI vendor."

### Why This Matters
This is the single biggest trust destroyer in enterprise AI adoption. Customers have been burned by vendors who quietly used their data for training. The backlash against OpenAI's default data retention policy and similar incidents made enterprise buyers hypersensitive.

### Our Mitigation
- **Explicit opt-in, never opt-out.** Every record in the learning objects (training_examples, preferences, policy_labels, reward_signals) has `allowed_for_training: boolean` that defaults to `false`.
- **Separate export modes.** Training data is only accessible via the `training` export profile, which requires `training_consent_required: true`.
- **Dataset card with prohibited uses.** Every package includes a `dataset_card.json` that explicitly lists prohibited uses.
- **No data commingling.** Customer trajectory data is never mixed with other customers' data. Each TCWP bundle is isolated.
- **Contractual guarantee.** Pilot agreements explicitly state: "retention.sh does not use customer workflow data for model training without written consent."

### What We Ship
- Data usage dashboard showing what data exists, what consent flags are set
- One-click "revoke training consent" that propagates across all bundles
- Contractual language in pilot agreements
- Annual data audit option for enterprise customers

---

## 4. Agent Autonomy and Blast Radius

### The Objection
"You're giving an AI agent control over our browser/device/portal. What if it does something destructive? What if it clicks 'Delete Account' or submits a fraudulent form?"

### Why This Matters
AI agents operating as "superusers" with broad system access are flagged as a critical threat by 32% of organizations. Only 6% of organizations have an advanced browser automation governance strategy. Analysts predict the first major lawsuits over autonomous agent actions by 2026. A misclick in a KYB portal could submit false verification. A wrong action in an EHR could alter patient records.

### Our Mitigation
- **Trajectory replay, not blind exploration.** TA replays a known-good path, not free-form exploration. The agent follows the exact sequence of validated steps.
- **Checkpoint validation at every critical step.** Before and after every state-mutating action, checkpoints verify the expected state matches.
- **Policy labels.** `policy_labels.jsonl` explicitly marks which actions are `safe_for_automation`, `needs_human_review`, or `never_automate`.
- **Read-only observation mode.** For initial assessment, TA can run in observation-only mode — capturing trajectories without executing actions.
- **Drift detection triggers fallback.** If the UI drifts beyond threshold (0.4), the agent stops and escalates rather than guessing.

### What We Ship
- "Dry run" mode for pilots — observe and record without executing
- Human-in-the-loop approval gates for high-risk steps
- Action audit log with before/after screenshots for every mutation
- Kill switch via MCP that immediately halts agent execution

---

## 5. Vendor Lock-In

### The Objection
"If we build our workflow verification around your tools, what happens if you go away? Can we take our data with us?"

### Why This Matters
Enterprise buyers have been burned by vendor lock-in. They want assurance that their investment in workflow capture and trajectory memory is portable.

### Our Mitigation
- **TCWP is vendor-neutral.** The canonical package format uses standard JSON/JSONL with no proprietary binary formats. Any system can read it.
- **Open schemas.** All 20 TCWP schemas are published, versioned, and use JSON Schema 2020-12. The spec is public.
- **Full export always available.** `ta.tcwp.export` produces a single self-contained JSON file that works without retention.sh.
- **Local-first storage.** TCWP bundles live on the customer's filesystem. They don't disappear if retention.sh Cloud goes down.
- **Vendor extensions are separate.** Provider-specific data (Anthropic hooks, OpenAI traces) is in `extensions/`, not in the core package. The core works without them.

### What We Ship
- Published TCWP spec on GitHub (open source)
- Migration guide for moving TCWP bundles to other systems
- CLI tool for TCWP generation/validation without retention.sh Cloud
- 90-day data export guarantee in contracts

---

## 6. Reproducibility and Trust in Savings Claims

### The Objection
"You say 78% token savings. How do I know that's real? Can I reproduce it? Are you cherry-picking the good runs?"

### Why This Matters
Buyers are skeptical of vendor benchmarks. Every AI company claims impressive numbers. Without reproducibility, savings claims are marketing, not proof.

### Our Mitigation
- **Published benchmark methodology.** Measurement protocol is documented: baseline (5 full crawls), replay (N=5), durability (N=10 over 3 days).
- **TCWP bundles are the proof.** Every benchmark produces a complete TCWP bundle the buyer can inspect — events, checkpoints, evals, cost metrics.
- **Run it yourself.** The MCP starter kit lets customers reproduce benchmarks on their own workflows.
- **N=1/5/10 durability, not single runs.** We report pass rates across multiple runs, not cherry-picked single results.
- **Sales brief includes raw numbers.** `sales_brief.json` has baseline AND replay metrics — not just the delta.

### What We Ship
- Hosted benchmark pages with downloadable TCWP bundles
- "Try it on your workflow" button in docs
- Weekly savings digest for pilot customers (automated from dashboard)
- Confidence intervals on savings estimates, not just point values

---

## 7. Model Dependency and Runtime Trust

### The Objection
"You use Claude/GPT/Gemini under the hood. If the model changes, does our workflow break? Are our trajectories tied to one model?"

### Why This Matters
Customers building on agent workflows worry about model version changes breaking their automation. They've seen LLM behavior change across versions.

### Our Mitigation
- **TCWP records the model used per run.** `run.json` has a `model` field. Trajectory durability is measured per model version.
- **Vendor-neutral core.** The TCWP core works with any model. Provider specifics are in `extensions/`.
- **Trajectory replay is model-agnostic.** The saved path is a sequence of actions and checkpoints, not model prompts. A different model can replay the same trajectory.
- **Drift detection catches model-induced breakage.** If a new model version produces different behavior, checkpoint validation catches it before it causes damage.
- **Multi-model benchmarking.** `ta.benchmark.model_compare` tests the same workflow across different models to measure robustness.

### What We Ship
- Model version tracking in all TCWP bundles
- Cross-model benchmark results
- "Model migration guide" for switching between providers
- Automatic re-validation when model version changes

---

## 8. Compliance Auditability

### The Objection
"We need to prove to our auditors exactly what the agent did, when, and why. Can you produce an audit trail that satisfies our compliance team?"

### Why This Matters
SOX, HIPAA, and financial regulations require audit trails for automated actions. If TA is automating workflows in regulated systems, the audit trail must be complete and tamper-evident.

### Our Mitigation
- **Append-only event log.** `events.jsonl` is the canonical ground truth — every action, checkpoint, tool call, and state change with timestamps.
- **Content-addressed integrity.** `manifest.json` contains SHA-256 hashes of all critical files. Tampering is detectable.
- **Provenance chain.** `provenance.json` tracks every action taken on the bundle — creation, replay, optimization, audit, export.
- **Eval and annotation trail.** `evals.jsonl` and `annotations.jsonl` provide machine and human review records.
- **Data classification.** `provenance.json` includes `data_classification` (public/internal/confidential/restricted).

### What We Ship
- Compliance-ready audit report generator (TCWP → PDF audit trail)
- Hash verification tool for bundle integrity checks
- Retention policy enforcement in `provenance.json`
- SOC 2 Type II audit (timeline TBD)

---

## 9. "Why Should We Trust a Startup With This?"

### The Objection
"You're a small team. How do we know you'll be around in 12 months? Why should we trust you with our critical workflow data?"

### Our Mitigation
- **Data portability eliminates the bus factor.** TCWP bundles are self-contained, open-format, and stored locally. If retention.sh disappears tomorrow, customer data still works.
- **Open schemas build trust.** Published spec means anyone can build tooling around TCWP.
- **Pilot-first revenue model.** We prove value before asking for commitment. 4-6 week pilots with clear deliverables.
- **The moat is in intelligence, not lock-in.** We win by making workflows cheaper over time, not by trapping data.

---

## 10. Summary: Trust Architecture

| Concern | TCWP Feature | MCP Tool |
|---------|-------------|----------|
| Data sovereignty | Local-first storage, explicit export | `ta.tcwp.export_profile` |
| PII protection | Redaction rules, fingerprints | Redaction pipeline |
| Training consent | Per-record opt-in, training profile | `ta.tcwp.export_profile profile=training` |
| Agent safety | Checkpoints, policy labels, drift detection | `ta.checkpoint.verify`, `ta.audit.drift_report` |
| Vendor lock-in | Open TCWP spec, full export | `ta.tcwp.export` |
| Reproducibility | N=1/5/10 methodology, TCWP proof | `ta.savings.compare`, benchmarks |
| Model dependency | Model tracking, cross-model benchmarks | `ta.benchmark.model_compare` |
| Audit trail | Append-only events, provenance, hashes | Full TCWP bundle |
| Startup risk | Self-contained packages, open spec | `ta.tcwp.list`, `ta.tcwp.export` |

**One-line answer:** Every concern maps to a concrete schema field, export control, or MCP tool. Trust is not a slide — it is built into the data architecture.

---

## The Trust Stack

Enterprise buyers evaluate against five layers. We must have an answer at each:

| Layer | What They Ask | Our Answer |
|-------|-------------|------------|
| **Legal** | DPAs, BAAs, training prohibition, breach liability | Contractual guarantees, BAA-ready templates |
| **Technical** | Encryption, data residency, session isolation, redaction | TCWP permissions, redaction rules, local-first storage |
| **Operational** | Kill switches, human approval gates, monitoring | Checkpoint validation, policy labels, drift detection |
| **Audit** | Immutable logs, third-party attestations, transparency | Append-only events, SHA-256 hashes, provenance chain |
| **Governance** | Named AI officer, escalation paths, incident playbooks | Handoff objects, urgency flags, compliance tags |

### Priority for First Pilots

Not everything needs to be built before the first pilot. Priority order:

1. **Local-first data storage + explicit export** (already built)
2. **PII redaction pipeline** (schema ready, pipeline needed)
3. **Training consent per-record** (already built)
4. **Checkpoint validation + kill switch** (MCP tools built)
5. **Compliance audit trail generator** (TCWP → PDF audit report)
6. **SOC 2 Type II** (timeline: 6-12 months, communicate honestly)
7. **Self-hosted deployment** (Phase 3, enterprise tier)

---

## Sources

- PwC AI Agent Survey — pwc.com/us/en/tech-effect/ai-analytics/ai-agent-survey
- Gartner Agentic AI Predictions 2027 — gartner.com/en/newsroom/press-releases/2025-06-25
- Kiteworks AI Agent Security — kiteworks.com/cybersecurity-risk-management
- Palo Alto Networks Agentic AI Security — paloaltonetworks.com/cyberpedia/what-is-agentic-ai-security
- Jimerson Birr Healthcare AI Regulation — jimersonfirm.com/blog/2026/02
- Jones Walker AI Legal Predictions 2026 — joneswalker.com/en/insights/blogs/ai-law-blog
- Forrester Predictions 2026 — forrester.com/blogs/predictions-2026
- Help Net Security GitHub Copilot Policy — helpnetsecurity.com/2026/03/26
- Anthropic FMTI Transparency Report — crfm.stanford.edu/fmti/December-2025
