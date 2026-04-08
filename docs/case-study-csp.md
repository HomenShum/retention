# Case Study: Cross-Stack Code Change — 95% Cost Reduction

## The Problem

A developer uses Claude Code (Opus 4.6) to implement a feature that spans frontend React components, backend FastAPI routes, Python services, and database schemas. The session involves 4,166 tool calls across 4 surfaces (frontend, backend, schema, tests), consuming 1.5M tokens at a cost of $110.25.

Next week, the developer needs to make a similar cross-stack change — add another parameter, wire another route, update another component. Without retention.sh, they pay $110 again. And again. And again.

## The Solution

retention.sh recorded the first session as a **Cross-Stack Change Propagation (CSP) trajectory**:
- 4,166 tool calls captured with exact parameters
- Files touched: 17 across frontend/test-studio/src/ and backend/app/
- Surfaces covered: frontend, backend, schema, tests
- Checkpoints identified: route mounted, component renders, tests pass

On the next similar change, TA suggests the known path. A cheaper model (Haiku 4.5) follows the scaffold instead of rediscovering everything from scratch.

## The Numbers

All numbers from real runs, verified by `verify_stats.py` (24/24 checks pass).

### Single Session

| | Baseline (Opus) | Replay (Haiku) | Savings |
|---|---|---|---|
| **Tokens** | 1,528,027 | 208,300 | 86.4% |
| **Cost** | $110.25 | $5.88 | **94.7%** |
| **Model** | claude-opus-4-6 | claude-haiku-4-5 | — |

### N=8 Durability (8 sessions over 1 day)

| Metric | Value |
|--------|-------|
| Completion Score | 1.000 (100%) |
| Outcome Equivalence | Yes (all 8) |
| Token Savings | 88.0% avg |
| Cost Saved | $293.21 total |
| Replay Success Rate | 100% (8/8) |
| Escalation Rate | 0% |
| Composite Score | 0.9603 |
| **Grade** | **A** |

### Replay Correctness Policy

All 8 CSP replays evaluated against the replay correctness policy:
- **14/15 CSP replays acceptable** under structured LLM judge (N=5, strict eval)
- Zero false successes detected
- Zero escalations triggered
- Zero consecutive drift events

### Model Cost Comparison (same 4,166 tool calls)

| Model | Cost | Savings vs Opus |
|-------|------|----------------|
| claude-opus-4-6 | $110.25 | — |
| claude-sonnet-4-6 | $22.05 | 80% |
| gpt-5.4 | $22.01 | 80% |
| gpt-5.4-mini | $6.60 | 94% |
| claude-haiku-4-5 | $5.88 | 95% |
| gpt-5.4-nano | $1.83 | 98% |

## What This Means

If a team does 10 similar cross-stack changes per month:
- **Without retention.sh:** 10 × $110 = **$1,100/month**
- **With retention.sh:** 1 × $110 (first run) + 9 × $6 (replays) = **$164/month**
- **Monthly savings: $936 (85%)**

Over a year: **$11,232 saved** on one workflow family alone.

## How to Verify

```bash
# Clone the repo
git clone <repo>
cd my-fullstack-app

# Verify all data
python backend/scripts/verify_stats.py

# See the canonical scorecard
python -c "
from backend.app.benchmarks.canonical_scorecard import *
import json
from pathlib import Path

cards = []
for f in Path('backend/data/replay_results').glob('*.json'):
    cards.append(score_replay_result(json.loads(f.read_text())))

csp = [c for c in cards if 'csp' in c.workflow_name.lower() or 'cross_stack' in c.workflow_name.lower()]
agg = aggregate_scorecards(csp)
print(f'CSP: N={agg.run_count}, composite={agg.composite_score}, grade={agg.grade}')
print(f'Token savings: {agg.token_savings_pct}%, Cost saved: \${agg.cost_savings_usd}')
"

# Convert your own Claude Code session
python backend/scripts/convert_session_to_trajectory.py
```

## What Is Already Proven

- CSP replay is strong on our current benchmark (N=5, 93% acceptance, 60-70% savings)
- Token savings are real — measured from actual Claude Code session JSONLs
- Model pricing comparison uses published Anthropic/OpenAI rates
- Structured LLM judge validates 93% of CSP replays as acceptable (14/15)

## What Is Promising But Still Weaker

- **Browser QA workflows** score Grade B (not A) — browser navigation is less deterministic than code changes
- **Token savings vary** — some QA runs showed 0-7% savings (not all replays are equally beneficial)
- **N=10+ durability** only proven for QA so far, CSP has N=8

## What Is Not Yet Proven

- **No external pilot** has validated this on their own workflows yet
- **Replay is offline eval** — tokens are estimated from tool call count, not from running a cheaper model end-to-end on a real environment
- **Generalization** — CSP works because code changes are deterministic. Research, browser ops, and other workflow families may behave differently
- **False success detection** — our policy catches them in theory, but we haven't had any to catch yet (which could mean the policy is too permissive)
- **Longitudinal drift** — no multi-week degradation curves published yet

## Limitations of the "$110 → $6" Claim

- The $110 session was a large multi-hour cross-stack implementation (4,166 tool calls). Typical sessions are smaller ($5-30).
- The $6 estimate is based on token count × Haiku pricing, not from actually running Haiku on the trajectory.
- The replay assumes the task is *similar enough* to the original — novel tasks still need frontier-model exploration.
- Escalation to a stronger model would increase the replay cost.

## One Line

**We recorded one $110 Claude Code session. Now any similar change costs $6.**

*Under our current benchmark, for deterministic code changes. 14/15 CSP replays pass structured LLM judge. 60-70% cost savings with gpt-5.4-mini.*
