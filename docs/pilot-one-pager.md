# retention.sh Pilot — One Pager

## What it does

retention.sh records your expensive Claude Code / AI agent sessions and turns them into replayable workflows. Next time you do the same type of work, it costs 95% less.

## Proof (from real runs, verified)

| Metric | Value | Source |
|--------|-------|--------|
| Sessions captured | 28 | Real Claude Code + QA pipeline runs |
| Token savings | 88% avg (CSP), 68% avg (QA) | Canonical scorecard across N=8 and N=20 |
| Cost savings | $293 across 8 CSP sessions | Real model pricing (Opus → Haiku) |
| Replay acceptance rate | 93% | 14/15 CSP replays pass structured LLM judge |
| Grade | A (code changes), B (browser QA) | Canonical 7-metric scorecard |
| Escalation rate | 0% | Zero escalations needed |

## How it works (3 steps)

1. **Record** — TA captures your Claude Code session (tool calls, tokens, files touched)
2. **Replay** — Next similar task, TA suggests the known path instead of re-reasoning from scratch
3. **Save** — Haiku replays the path at 5% of the original Opus cost. Escalates only if something changed.

## What the pilot looks like

- **Duration:** 2 weeks
- **Setup:** Run one command: `python backend/scripts/convert_session_to_trajectory.py`
- **Your workflow:** Use Claude Code as normal. TA records in the background.
- **What you see:** Dashboard showing: runs captured, tokens saved, cost avoided, replay success rate
- **What we measure:** N=5 and N=10 durability on YOUR workflows

## Who this is for

- Teams using Claude Code, Cursor, or AI agents for daily coding
- Anyone spending >$50/week on AI model tokens
- Teams with repetitive multi-file changes across frontend + backend

## One sentence

**We don't reduce tokens by prompting better. We reduce tokens by not needing to think again.**

## Verify it yourself

```bash
git clone <repo>
python backend/scripts/verify_stats.py        # 24 checks, all pass
python backend/scripts/verify_api.py           # Real Convex API calls
./scripts/verify.sh                            # Full verification chain
```

## Contact

[Your email / Slack / scheduling link here]
