# retention.sh — Roadmap

Every push answers one question: **What painful thing became newly reliable, newly visible, or newly cheaper this week?**

## Pain Ladder (what we fix, in order)

1. Agent says "done" too early
2. User cannot see what actually happened
3. Repeated workflows cost too much
4. Cheaper replay is hard to trust
5. Team knowledge lives in one person's head

## Release Sequence

### Release 1: Workflow Judge (now through Friday)

The core moat. Without this, we're another trace viewer.

Ship:
- Workflow checklist with required steps
- Missing-step verdict (hard gate, not suggestion)
- Verdict enum: PASS / FAIL / BLOCKED
- One flagship workflow (CSP)
- Savings shown only with strict judge verdicts

Message: **"The agent thought it was done. retention.sh showed exactly what was missing."**

### Release 2: Run Anatomy (week 2)

Without visibility, nobody understands what we built.

Ship:
- Tool timeline with per-step cost/time
- Artifact viewer (screenshots, evidence)
- Nudge events in timeline
- Shareable link to a run
- "Missing recurring steps" summary card

Message: **"Here's what happened. Here's what got skipped. Here's how we fixed it."**

### Release 3: Cheap Replay (week 3-4)

Ship:
- Frontier vs replay comparison view
- Savings waterfall (cost per step)
- Strict verdict side-by-side
- N=5 CSP benchmark under strict judge
- DRX delta-refresh benchmark

Message: **"Run 1 is expensive. Run N reuses memory and is nearly free."**

### Release 4: Retention API (week 5-6)

Without this, we're trapped in Claude Code.

Ship:
- Canonical event schema (CanonicalEvent dataclass)
- Workflow package format (TCWP)
- Adapter interface definition
- Shared retention API endpoint
- Python SDK published to PyPI (`pip install retention`)

Message: **"retention.sh is not a Claude Code trick. It's a workflow intelligence layer."**

### Release 5: Cross-Runtime (week 7-8)

Ship:
- One non-Claude adapter (Cursor or OpenAI Agents SDK)
- Prove runtime-agnostic architecture
- Personal workflow memory (user-scoped)
- Workflow library (browsable, searchable)

Message: **"Works with Claude Code, Cursor, OpenAI Agents, LangChain, CrewAI."**

### Release 6: Digital Twin + Distillation (week 9-12)

Ship:
- `create-twin` tool (already built, needs polish)
- Paid distillation audit offer
- Cloud dashboard for teams
- Judge calibration dashboard
- Design partner onboarding flow

Message: **"Use frontier models for discovery once. Use retention.sh to make the repeatable parts cheaper and safer."**

## Push Schedule

### Daily (internal)
- Benchmark run
- Regression check
- One screenshot or trace review
- One truth audit on claims in UI

### Twice weekly (product)
- **Tuesday**: visible product improvement
- **Friday**: benchmark or judge reliability improvement

### Every 2 weeks (named milestone)
Each release contains:
1. One capability change (what became possible)
2. One visibility change (what became visible)
3. One metric change (what got measured)
4. One limitation note (what's still missing)

### Monthly (market-facing)
One proof artifact: benchmark page, case study, pilot one-pager, or reproducibility doc.

## What Each Release Must Contain

Copy Anthropic's discipline. They ship:
- the capability
- the control surface
- the operational framing
- the economic story

So each retention.sh release ships:
- one capability change ("replay can now block false completion on missing steps")
- one visibility change ("timeline now shows nudge events and evidence refs")
- one metric change ("verdict agreement improved from 67% to 78%")
- one limitation note ("still Claude Code only for live nudges")

## What NOT To Do

Do not spend the next month on:
- Broad multi-agent orchestration
- Giant generic benchmark expansion
- Training-data company positioning
- Too many verticals
- Polishing static rules systems
- Over-abstracting the product into platform language

## Go-To-Market: Founder-Led Instrumentation

First 5-10 users get:
- Direct setup help
- Direct workflow instrumentation
- Direct debugging of missed steps
- Direct follow-up after each run
- Direct benchmark/result walkthroughs

This is not a weakness. This is the advantage.

**External language** (use this):
- "We help your coding agent stop missing recurring steps"
- "We remember how you want work done"
- "We catch when the agent skipped part of your workflow"
- "We make repeated workflows cheaper over time"

**Internal language** (never show to users):
- "multi-surface orchestration graph with retained operation policy engine"
- "trajectory-contextualized workflow package"
- "distillation spine"

## One-Line Roadmap

Near term: always-on workflow judge + visible trace UI.
Mid term: runtime-agnostic retention API.
Then: cloud dashboard + paid workflow distillation service.
