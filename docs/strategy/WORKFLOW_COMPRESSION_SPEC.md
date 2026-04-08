# retention.sh: Workflow Compression and Audit Engine Spec

*How TA finds cheaper valid paths and verifies shortcuts before promotion.*

---

## 1. Overview

The workflow compression engine is the core moat layer. It:

1. **Observes** the full action path from exploratory runs
2. **Compares** successful and unsuccessful trajectories
3. **Identifies** redundant or expensive steps
4. **Generates** a cheaper shortcut path
5. **Verifies** that the shortcut still reaches the same validated outcome
6. **Audits** the shortcut instead of blindly trusting it

The product is not "AI did the task." It is: "We can show how the task was done, optimize the path, and verify that the optimized path is still correct."

---

## 2. Compression Pipeline

```
capture → compare → propose → audit → approve → reuse
```

### Stage 1: Capture
- Record full exploratory run as TCWP events.jsonl
- Extract trajectory graph with state transitions
- Capture before/after state snapshots at each step
- Content-address all states for diffing

### Stage 2: Compare
- Align trajectories across multiple runs of same workflow
- Identify common successful paths
- Identify divergent steps (exploration noise vs required)
- Compute per-step cost (tokens, time, requests)
- Flag high-cost steps with low state change

### Stage 3: Propose Optimization
Generate `optimization_candidates.json` entries:

| Optimization Type | Description | Risk |
|-------------------|-------------|------|
| `step_elimination` | Remove steps that don't change state meaningfully | Low |
| `step_reordering` | Reorder independent steps for parallelism | Low |
| `parallel_execution` | Run independent branches simultaneously | Medium |
| `state_jump` | Skip intermediate states via deep links/intents | Medium |
| `checkpoint_skip` | Skip redundant checkpoint validations on stable steps | Low |
| `action_substitution` | Replace expensive action with cheaper equivalent | Medium |
| `composite` | Multiple optimizations combined | High |

### Stage 4: Audit
- Run the proposed shortcut as a new TCWP run (mode: `audit`)
- Compare end state against baseline end state
- Compare all checkpoint results
- Measure actual savings vs expected
- Generate audit verdict: `verified` | `rejected` | `needs_review`

### Stage 5: Approve
- If `verified`: promote shortcut to trajectory
- If `rejected`: record why, archive candidate
- If `needs_review`: flag for human review via annotation
- Update trajectory.compression_history

### Stage 6: Reuse
- Updated trajectory becomes the new replay source
- Next replay uses the compressed path
- Savings compound with each optimization iteration

---

## 3. Audit Engine Design

### Audit Run Structure
```json
{
  "run_id": "run_audit_001",
  "mode": "audit",
  "audit_target": {
    "candidate_id": "opt_01",
    "baseline_run_id": "run_00040",
    "proposed_trajectory_id": "traj_profile_v3_opt1"
  }
}
```

### Audit Checks

| Check | Description | Required |
|-------|-------------|----------|
| End state match | Final state fingerprint matches baseline | Yes |
| Checkpoint pass rate | All mandatory checkpoints pass | Yes |
| Intermediate state preservation | Key intermediate states still reachable | Yes |
| No side effects | No unintended state mutations | Yes |
| Cost within bounds | Actual savings within 20% of expected | No |
| Timing within bounds | Duration doesn't exceed baseline + 50% | No |
| Artifact completeness | All required screenshots/traces captured | Yes |

### Audit Verdict Logic
```
IF end_state_match AND all_mandatory_checkpoints_pass AND no_side_effects:
  IF intermediate_states_preserved:
    verdict = "verified"
  ELSE:
    verdict = "needs_review"
ELSE:
  verdict = "rejected"
```

---

## 4. Shortcut Validation for Claude Code (Local MCP)

When Claude Code proposes a shortcut via MCP tools:

1. Agent reads prior trajectory from local TCWP bundle
2. Agent proposes shorter path (e.g., deep link instead of navigation)
3. TA MCP tool `ta.trajectory.audit` runs the shortcut
4. Tool compares:
   - Baseline trajectory end state
   - Shortcut trajectory end state
   - All checkpoint results
   - Cost delta
5. Tool returns verdict + evidence
6. Claude Code can inspect the comparison before accepting

This prevents Claude Code from inventing shortcuts and blindly trusting them.

---

## 5. Cloud Workflow Compression (retention.sh Dashboard)

For cloud users, the dashboard provides:

### Automatic Analysis
- Detect repeated workflows (same workflow_id, 3+ runs)
- Analyze step cost distribution
- Identify optimization candidates automatically
- Rank by expected savings

### Recommendation Engine
- Propose shorter paths
- Suggest lower-cost cron schedules
- Recommend checkpoint frequency adjustments
- Identify obsolete workflow branches

### Audit Dashboard
- Show pending optimization candidates
- Display audit results (pass/fail/review)
- Visualize trajectory comparison (baseline vs shortcut)
- Present savings evidence

### Savings Reports
- Per-workflow compression history
- Cumulative savings over time
- Projection of future savings
- Team-level optimization summary

---

## 6. Data Model

### optimization_candidates.json
See `packages/tcwp/schemas/optimization_candidate.schema.json` for full schema.

Key fields:
- `optimization_type`: What kind of shortcut
- `steps_removed`: Steps eliminated
- `steps_substituted`: Steps replaced with cheaper alternatives
- `expected_savings`: Projected token/time/cost savings
- `audit_status`: pending → in_progress → verified/rejected/needs_review
- `risk_assessment`: safe/low/medium/high

### trajectory.compression_history
Array tracking each compression iteration:
- `version`: Compression iteration number
- `steps_before` / `steps_after`: Step count delta
- `tokens_before` / `tokens_after`: Token cost delta
- `audit_run_id`: Run that verified this compression
- `compressed_at`: Timestamp

---

## 7. Safety Rails

1. **Never auto-promote** an unaudited shortcut
2. **Never remove checkpoints** without explicit approval
3. **Always preserve** the full baseline trajectory as reference
4. **Always record** audit evidence in TCWP bundle
5. **Flag high-risk** optimizations for human review
6. **Roll back** if promoted shortcut fails on next replay
7. **Rate limit** optimization proposals to prevent thrashing

---

## 8. Metrics

| Metric | Description |
|--------|-------------|
| Compression ratio | steps_after / steps_before |
| Token savings % | (tokens_before - tokens_after) / tokens_before |
| Audit pass rate | verified / (verified + rejected) |
| Time to audit | avg seconds from proposal to verdict |
| Shortcut durability | replays until first drift after promotion |
| False positive rate | rejected shortcuts that were actually safe |
| Cumulative savings | total tokens saved across all compressed replays |

---

## 9. Implementation Priority

### Phase 1 (Weeks 3-6): Basic Compression
- Step elimination based on state fingerprint matching
- Simple audit: run shortcut, compare end state
- Manual approval flow

### Phase 2 (Weeks 7-12): Intelligent Compression
- Multi-run trajectory alignment
- Action substitution proposals
- Automated audit with structured verdicts
- Dashboard visualization

### Phase 3 (Months 4-6): Advanced Compression
- Parallel execution detection
- State jump optimization
- Cross-workflow pattern mining
- Team-level optimization recommendations
