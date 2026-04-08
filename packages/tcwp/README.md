# TCWP — TA Canonical Workflow Package

A vendor-neutral, future-proof package format for recording, replaying, auditing, and selling agent workflow intelligence.

## Purpose

TCWP supports four use cases with one schema:

1. **Record** a first run (full crawl)
2. **Rerun / Replay** a prior workflow (trajectory replay)
3. **Re-ingest** the package into TA or another runtime
4. **Handoff / Sell** the result to customers, partners, and pilots

## Package Layout

```
tcwp/
  manifest.json          # Envelope and compatibility contract
  workflow.json          # Stable identity of the business process
  run.json               # One execution attempt
  trajectory.json        # Reusable path (the core replay asset)
  checkpoints.json       # Checkpoint summary
  events.jsonl           # Append-only ground truth event log
  state_snapshots.jsonl  # Before/after UI/DOM state
  tool_calls.jsonl       # Normalized tool I/O across vendors
  evals.jsonl            # Machine and human evaluations
  annotations.jsonl      # Figure Eight-style review and labeling
  replay_plan.json       # What to rerun and why
  optimization_candidates.json  # Proposed shortcuts (moat object)
  provenance.json        # Data lineage and chain of custody
  permissions.json       # Access control and sharing rules
  handoff.json           # Operational handoff
  sales_brief.json       # Buyer-facing package
  sales_brief.md         # Human-readable sales report
  # --- Learning Extension (dual-use: ops + model improvement) ---
  training_examples.jsonl  # Distilled SFT examples
  preferences.jsonl        # Pairwise reward/preference data
  policy_labels.jsonl      # Control-policy and audit labels
  reward_signals.jsonl     # Online RL signals from production
  dataset_card.json        # Package-level metadata for research/training
  export_profiles/
    ops.json               # Replay/rerun export filter
    training.json          # Fine-tuning/eval export filter
    sales.json             # Buyer proof export filter
  artifacts/
    screenshots/         # Visual evidence
    traces/              # Execution traces
    logs/                # Runtime logs
    diffs/               # State diffs
    reports/             # Generated reports
  extensions/
    anthropic.json       # Claude Code / Anthropic-specific fields
    openai.json          # OpenAI Agents SDK-specific fields
    google.json          # Gemini / Google-specific fields
    xai.json             # xAI-specific fields
```

## Design Principles

1. **Vendor-neutral core, vendor-specific extensions** — Core schema never depends on one provider. Provider details go under `extensions/`.
2. **Events first, summaries second** — Raw event stream is the source of truth. Summaries derive from events.
3. **Graph + log + artifact** — Event log for replay, trajectory graph for reasoning, artifact store for evidence.
4. **Human review is first-class** — Annotations support labels, adjudication, agreement metadata (Figure Eight pattern).
5. **Compression-aware storage** — JSON/JSONL for hot path, Parquet for analytics rollups later.
6. **Dual-use by design** — Every workflow can be a replay asset, benchmark asset, sales proof asset, AND training/eval asset. The learning extension turns validated runs into vertical model improvement data.

## Schemas

All JSON Schemas are in `schemas/` and follow JSON Schema 2020-12:

| Schema | Description |
|--------|-------------|
| `manifest.schema.json` | Package envelope |
| `workflow.schema.json` | Business process identity |
| `run.schema.json` | Execution attempt |
| `trajectory.schema.json` | Reusable path |
| `events.schema.json` | Event log line |
| `state_snapshot.schema.json` | State snapshot line |
| `tool_call.schema.json` | Tool call line |
| `eval.schema.json` | Evaluation line |
| `annotation.schema.json` | Annotation/review line |
| `replay_plan.schema.json` | Replay instructions |
| `optimization_candidate.schema.json` | Shortcut proposal (moat) |
| `handoff.schema.json` | Operational handoff |
| `sales_brief.schema.json` | Buyer-facing package |
| `provenance.schema.json` | Data lineage |
| `permissions.schema.json` | Access control |
| `training_example.schema.json` | SFT training example |
| `preference.schema.json` | Pairwise preference/reward |
| `policy_label.schema.json` | Control-policy label |
| `reward_signal.schema.json` | Online RL signal |
| `dataset_card.schema.json` | Dataset metadata |
| `export_profile.schema.json` | Export mode filter |

## Required Fields

For reliable replay, audit, and sales-grade evidence, these fields are mandatory across files:

- `schema_version`, `package_id`, `workflow_id`, `run_id`
- `parent_run_id` and/or `rerun_of_run_id`
- `mode`, `runtime`, `model`, `surface`
- `started_at`, `ended_at`
- `tokens_in`, `tokens_out`, `estimated_cost_usd`
- `trajectory_id` (when replay exists)
- `state_before`, `state_after` on every state-mutating action
- `artifact_refs`
- `eval_id` or explicit `no_eval_reason`
- `provenance`, `permissions`

## Example Bundle

See `examples/profile-edit-flow/` for a complete example of the Profile Edit Flow workflow with:
- 12-step trajectory (compressed from 18)
- 78.5% token savings over baseline
- 7/7 checkpoints passed
- 2 optimization candidates (1 verified, 1 needs review)
- Full event log, annotations, evals, and sales brief

## Vendor Extensions

Put provider-specific fields only under `extensions/`:

- **Anthropic**: Hook payloads, subagent IDs, MCP server names, memory hints
- **OpenAI**: Response item IDs, trace/span IDs, conversation references
- **Google**: Thought signatures, Interactions API metadata
- **xAI**: Reasoning-content chunks, tool-call streaming details

## Storage Recommendations

- **Hot path**: JSON/JSONL for fast ingest and replay
- **Cold archive**: Parquet for analytics rollups
- **Artifacts**: Content-addressed in object storage (S3, GCS, local FS)
- **Content addressing**: SHA-256 fingerprints in `hashes` and `fingerprint` fields

## Industry Alignment

Built around converging agent ecosystem primitives:
- Structured outputs / JSON Schema
- Function / tool calls
- Typed traces and handoffs
- MCP connectivity
- Memory and replay
- Human review and annotations

Compatible with Claude Agent SDK, OpenAI Agents SDK, Gemini Interactions API, and xAI tool calling.

## Export Profiles

TCWP supports three export modes via `ta.tcwp.export_profile`:

| Profile | Purpose | Includes | Consent Required |
|---------|---------|----------|-----------------|
| **ops** | Replay, rerun, re-ingest, dashboard | Core operational files | None |
| **training** | Fine-tuning, evals, reward modeling | Learning extension files | `allowed_for_training: true` on each record |
| **sales** | Buyer proof, GTM, benchmarks | Sales brief + eval evidence | `allowed_for_external_sharing: true` on each record |

Profiles enforce:
- **Redaction**: `training` and `sales` profiles apply PII redaction before export
- **Consent filtering**: `training` profile skips records without training consent
- **File selection**: Each profile includes only the relevant subset of files

## Learning Extension

The learning extension turns validated workflow runs into vertical model improvement data:

| Object | Purpose | Use Case |
|--------|---------|----------|
| `training_examples.jsonl` | Distilled (task, context, target behavior, outcome) tuples | Supervised fine-tuning for workflow-specific models |
| `preferences.jsonl` | Pairwise/listwise comparisons (baseline vs optimized) | RLHF, reward modeling, shortcut-selection policy |
| `policy_labels.jsonl` | Safe/valid/reusable/escalate labels per action | Control policy training, safety guardrails |
| `reward_signals.jsonl` | Implicit/explicit/computed signals from production | Online RL, replay-vs-explore optimization |
| `dataset_card.json` | Domain, collection process, PII status, licensing | Research partnerships, compliance, model cards |

### Why This Matters

The market is moving toward vertical post-trained models built from last-mile usage data (Intercom Fin Apex, Cursor Composer 2, Decagon's specialized model network). TCWP should be the substrate that turns operational workflow data into model improvement signals.

### Internal framing

> TCWP is not only our replay and handoff bundle. It is the canonical workflow data package that turns validated runs into reusable operational memory, benchmark evidence, and future vertical-model training data.
