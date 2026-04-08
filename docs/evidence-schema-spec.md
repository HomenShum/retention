# retention.sh — Evidence Schema Specification (Locked)

> Every retention.sh run MUST emit this schema. No exceptions.
> This is the product backbone — without it, you cannot compare runs,
> build dashboards, or onboard outside teams.

---

## Version

**Schema version**: `2.0.0`
**Locked date**: 2026-03-19
**Breaking changes**: Require semver major bump + team approval

---

## Top-Level: `BenchmarkRunEvidence`

Every run emits one JSON file conforming to this schema.

```json
{
  "schema_version": "2.0.0",
  "run_id": "uuid",
  "suite_id": "optional-suite-uuid",
  "task_id": "app-login-001",
  "app_id": "khush-film-rating",
  "platform": "web | android-emulator",
  "environment": "local | staging",
  "agent_mode": "claude-baseline | test-assurance",

  "start_time": "2026-03-19T14:30:00Z",
  "end_time": "2026-03-19T14:30:12Z",

  "status": "pass | fail | blocked",

  "verdict": {
    "label": "success | bug-found | bug-found-deterministic | bug-found-flaky | wrong-output | timeout | infra-failure | flakiness-detected | needs-human-review",
    "confidence": 0.92,
    "reason": "Login form submitted invalid email without validation",
    "blocking_issue": null
  },

  "artifacts": {
    "trace_path": "artifacts/{suite_id}/{task_id}/trace.zip",
    "video_path": "artifacts/{suite_id}/{task_id}/recording.webm",
    "screenshots": [
      "artifacts/{suite_id}/{task_id}/before.png",
      "artifacts/{suite_id}/{task_id}/after.png"
    ],
    "logs_path": "artifacts/{suite_id}/{task_id}/logs.txt",
    "console_path": "artifacts/{suite_id}/{task_id}/console.json",
    "network_path": "artifacts/{suite_id}/{task_id}/network.json",
    "action_spans_path": "artifacts/{suite_id}/{task_id}/action_spans.json",
    "tool_calls_path": "artifacts/{suite_id}/{task_id}/tool_calls.json"
  },

  "task_metrics": {
    "duration_seconds": 12.4,
    "reruns": 1,
    "manual_interventions": 0,
    "artifact_completeness_score": 0.85
  },

  "cost": {
    "token_input": 2800,
    "token_output": 650,
    "token_cost_usd": 0.004,
    "compute_cost_usd": 0.0,
    "ci_minutes": 0.0,
    "ci_cost_usd": 0.0,
    "storage_gb": 0.002,
    "storage_cost_usd": 0.000046,
    "total_cost_usd": 0.004046,
    "platform_costs": {}
  },

  "failure_bundle": {
    "task_id": "form-submit-001",
    "verdict": "fail",
    "failure_step": "Submit login form with invalid email 'notanemail'",
    "summary": "Form submitted without client-side validation. No error message shown.",
    "screenshot_paths": ["artifacts/.../after.png"],
    "trace_path": "artifacts/.../trace.zip",
    "log_excerpt": "console.json:14 — no validation errors logged",
    "root_cause_candidates": [
      "No email regex in LoginForm.tsx:42",
      "onSubmit handler skips validation"
    ],
    "involved_files": [
      "src/components/LoginForm.tsx:42",
      "src/lib/validators.ts:18"
    ],
    "duration_seconds": 12.4,
    "confidence": 0.92
  },

  "quality_intelligence": {
    "structural_findings": [
      {
        "type": "layout_crowding",
        "severity": "medium",
        "element": "Login form — 6 fields visible above fold",
        "recommendation": "Group secondary fields behind 'Advanced' toggle"
      },
      {
        "type": "dead_element",
        "severity": "low",
        "element": "'Remember me' checkbox — no backend handler",
        "recommendation": "Remove or implement session persistence"
      }
    ],
    "ux_friction_score": 0.35,
    "interaction_depth": 3,
    "cognitive_load_estimate": "medium"
  }
}
```

---

## Required Fields

These fields MUST be present in every run evidence:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `schema_version` | string | YES | Semver, currently "2.0.0" |
| `run_id` | string | YES | UUID, unique per run |
| `task_id` | string | YES | Identifies the test task |
| `app_id` | string | YES | Target app identifier |
| `platform` | enum | YES | "web" or "android-emulator" |
| `environment` | enum | YES | "local" or "staging" |
| `agent_mode` | enum | YES | "claude-baseline" or "test-assurance" |
| `start_time` | ISO 8601 | YES | UTC timestamp |
| `end_time` | ISO 8601 | YES | Filled on finalize |
| `status` | enum | YES | "pass", "fail", or "blocked" |
| `verdict.label` | enum | YES | One of 9 verdict labels |
| `verdict.confidence` | float | YES | 0.0 to 1.0 |
| `verdict.reason` | string | YES | Human-readable explanation |
| `cost.token_input` | int | YES | Total input tokens |
| `cost.token_output` | int | YES | Total output tokens |
| `cost.total_cost_usd` | float | YES | Total run cost |

---

## Optional Fields

| Field | Type | When Present |
|-------|------|-------------|
| `suite_id` | string | When run is part of a benchmark suite |
| `artifacts.*` | paths | As available — completeness scored |
| `failure_bundle` | object | When status is "fail" |
| `quality_intelligence` | object | When TA mode with quality analysis |
| `cost.platform_costs` | dict | Platform-specific costs (device lease, etc.) |

---

## Artifact Completeness Scoring

Weighted by importance:

| Artifact | Weight | Why |
|----------|--------|-----|
| trace_path | 0.25 | Full replay capability |
| video_path | 0.20 | Visual regression evidence |
| screenshots | 0.15 | Quick visual reference |
| action_spans_path | 0.15 | Verification clips |
| logs_path | 0.10 | Application logs |
| console_path | 0.05 | Browser console |
| network_path | 0.05 | API call evidence |
| tool_calls_path | 0.05 | Agent action log |

Score = sum of weights for present artifacts. Target: >= 0.50 for "enough evidence."

---

## Compact Failure Bundle

The `failure_bundle` is extracted from full evidence for LLM consumption.
Design constraints:

- Summary: target <200 tokens
- Total bundle: target <500 tokens when serialized
- Must include: failing step, root cause candidates, involved files
- Screenshots referenced by path, not embedded
- Log excerpt truncated to relevant lines only

---

## Quality Intelligence (Layer 5)

When present, `quality_intelligence` adds structural UX findings beyond pass/fail:

| Finding Type | Description |
|-------------|-------------|
| `layout_crowding` | Too many elements competing for attention |
| `dead_element` | Element with no backend handler or broken state |
| `visual_hierarchy_broken` | Primary action not visually dominant |
| `state_transition_confusion` | Ambiguous state change after interaction |
| `interaction_depth_excessive` | Too many steps to complete a task |
| `cognitive_load_high` | Too much information on a single view |
| `ornamental_element` | Element that adds no user value |
| `repeated_friction` | Same UX issue across multiple flows |

---

## Scorecard Dimensions

Every run is scored on 4 binary dimensions:

| Dimension | Pass Criteria |
|-----------|--------------|
| `completed_correctly` | status=pass AND verdict=success |
| `caught_failure_correctly` | status=fail AND verdict in (bug-found, wrong-output) |
| `left_enough_evidence` | artifact_completeness_score >= 0.50 |
| `can_replay` | trace_path AND video_path both present |

---

## Model Pricing (March 2026)

Used for `cost` calculation:

| Model | Input ($/1M) | Output ($/1M) |
|-------|-------------|---------------|
| gpt-5.4 | $2.50 | $15.00 |
| gpt-5.4-mini | $0.75 | $4.50 |
| gpt-5.4-nano | $0.20 | $1.25 |
| claude-opus-4-6 | $15.00 | $75.00 |
| claude-sonnet-4-6 | $3.00 | $15.00 |
| claude-haiku-4-5 | $0.80 | $4.00 |

---

## Migration Notes

- v1.0 → v2.0: Added `schema_version`, `failure_bundle`, `quality_intelligence`, `cost.platform_costs`
- All v1.0 fields preserved — v2.0 is a superset
- Readers MUST check `schema_version` and handle missing optional fields gracefully
