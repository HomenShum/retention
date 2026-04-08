# retention.sh Strategy Agent — MEMORY Contract

## Memory Architecture

Memory follows the OpenViking-inspired layered pattern, adapted for Convex persistence:

### L0: Immediate Context (per-run)
- Current Slack messages in the 30-minute window
- Thread context for the active opportunity
- Recent bot posts (for rapid-fire limit checking)

### L1: Session Context (per-day)
- Last 48 decisions from Convex (monitor + digest)
- Health metrics from the last evolution review
- Active institutional memory entries (last 7 days)

### L2: Persistent Context (all time)
- Full institutional memory in Convex `institutionalMemory` table
- All decision logs in `slackMonitorDecisions` and `slackDigestDecisions`
- Evolution review history in `slackEvolveReviews`
- Task state in `slackTaskState`

## Memory Operations

### Decision Extraction
During every monitor run, conversations are scanned for decisions using LLM extraction. Patterns recognized:
- "we decided to..."
- "let's go with..."
- "approved..."
- "moving forward with..."

### FAQ Detection
When a topic appears >= 3 times in institutional memory as a "question" type, it triggers proactive FAQ surfacing.

### Memory Surfacing
When the monitor detects an opportunity, it checks institutional memory for prior context on the same topic. If found, the response includes: "This was discussed on [date] — the decision was [X]."

### Decay Policy
- Memories are never deleted, but relevance decays
- Search results are sorted by recency + relevance
- The evolution loop tracks whether surfaced memories were helpful (via engagement)

## Convex Tables

| Table | Purpose | Indexed By |
|---|---|---|
| `slackMonitorDecisions` | Every monitor decision with full gate trace | timestamp, decision |
| `slackDigestDecisions` | Every digest decision with activity metrics | timestamp |
| `slackEvolveReviews` | Daily health check results + proposals | timestamp |
| `institutionalMemory` | Extracted decisions + knowledge entries | topic, timestamp |
| `slackTaskState` | Runtime state for each service | taskName |
