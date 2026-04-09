---
name: create-twin
description: Distill any person into a reusable Claude Code skill. Feed it chat logs, docs, emails, git history, and get back a digital twin that thinks, decides, and communicates like them.
user-invocable: true
---

# create-twin

Distill a person into a digital twin skill. Inspired by [colleague-skill](https://github.com/titanwings/colleague-skill), adapted for founders, engineers, and operators.

## Trigger

User says: "create twin", "distill me", "make a skill of me", "digitize [name]", "create a twin of [name]"

## Process

### Step 1: Intake (3-5 questions, < 2 min)

Ask these in rapid succession. All except alias are skippable.

1. **Alias**: What should this twin be called? (e.g., "homen", "sarah-cto")
2. **Identity in one sentence**: Role, company, domain, stage. (e.g., "Solo founder, agentic AI, pre-seed, banking background")
3. **Operating philosophy in one sentence**: How they make decisions. (e.g., "Prove first, plan second. Binary gates. Kill criteria before starting.")
4. **What they push back on**: 2-3 things that trigger them. (e.g., "Scope creep, band-aid fixes, passive AI that waits for instructions")
5. **What excites them**: 2-3 things they lean into. (e.g., "Self-building systems, compounding memory, speed as emotion")

### Step 2: Source Material Collection

Gather from every available source. The more material, the higher fidelity.

**Auto-collect** (if accessible):
- Git commit messages: `git log --author="[name]" --format="%s" | head -100`
- CLAUDE.md rules and .claude/rules/*.md
- Memory files in `~/.claude/projects/*/memory/`
- SOUL.md, STYLE.md if present
- Docs/ folder strategy docs, PRDs, specs

**User-provided** (ask for these):
- Chat logs (Slack, Discord, iMessage exports)
- Email samples (especially sent mail — tone, structure, priorities)
- LinkedIn posts or public writing
- Meeting transcripts
- Decision memos or strategy docs
- Code review comments

**Connected tools** (if MCP connectors available):
- Gmail: read sent mail for tone and priorities
- Slack: read message history for communication patterns
- Google Drive: read strategy docs and memos
- Linear/Jira: read issue comments for delegation style

### Step 3: Parallel Analysis

Run two extraction tracks on all collected material:

**Track A: Work Skill Extraction**
For each piece of source material, extract:
- How they decompose problems (top-down? bottom-up? constraint-first?)
- How they evaluate tradeoffs (frameworks used, what they weigh)
- How they structure plans (sequenced steps? parallel tracks? timebox?)
- How they benchmark claims (what evidence do they demand?)
- Output format preferences (tables? bullets? schemas? code?)
- Technical stack and tool preferences
- Quality bar (what's "done" to them?)

**Track B: Persona Extraction**
For each piece of source material, extract:

| Layer | Extract | Format |
|-------|---------|--------|
| Layer 0: Core Rules | Inviolable behavioral rules | "In situation X, does Y" — NOT adjectives |
| Layer 1: Identity | Role, domain, stage, background | One paragraph |
| Layer 2: Expression | Vocabulary, sentence length, connectors, formality, emoji usage, catchphrases. Plus 4-5 "sounds like them" examples. | Concrete patterns |
| Layer 3: Decision | Priority ranking, push triggers, avoidance triggers, how they say "no", how they handle uncertainty | Situation-behavior pairs |
| Layer 4: Interpersonal | Delegation style, feedback style, behavior toward superiors/peers/juniors, behavior under pressure | Scenario examples |
| Layer 5: Boundaries | Things they dislike, refuse, or avoid. What triggers pushback. | Evidence-based |

### Step 4: Generate Twin Skill

Write the skill to `~/.claude/skills/{alias}-twin/SKILL.md` with this structure:

```markdown
---
name: {alias}-twin
description: Digital twin of {name}. {one-sentence identity}.
trigger: when user says "{alias} mode", "be {alias}", "write as {alias}"
---

# {name} — Digital Twin Skill

## Identity
{Layer 1 output}

## Modes
{3-7 modes extracted from work patterns, each with: when to use, tone, output format}

## Communication Style
{Layer 2 output — voice rules, sentence patterns, vocabulary}

## Decision-Making
{Layer 3 output — frameworks, gates, triggers}

## Work Methodology
{Sequenced steps from Track A analysis}

## Push-Back Patterns
{Layer 5 output — what they interrupt and how}

## Excitement Signals
{What they lean into, from intake + material analysis}

## Calibration Set
### Sounds like {name}
{4-5 example sentences/paragraphs from real material}
### Does NOT sound like {name}
{4-5 anti-examples — common AI patterns they'd reject}

## Layer 0: Inviolable Behavioral Rules
{10-15 situation-behavior pairs, concrete, from Layer 0 extraction}

## Corrections
{Empty section — grows through use}

## Meta
Distilled from: {source list with counts}. Last updated: {date}.
```

### Step 5: Preview + Confirm

Show the user a summary before writing:
- Identity (1 sentence)
- Top 3 modes detected
- Top 5 Layer 0 rules
- 2 "sounds like" examples
- 2 "does not sound like" examples

Ask: "Does this feel right? Anything to correct?"

Apply corrections, then write the file.

### Step 6: Evolution

After initial creation, the twin improves through:

**Corrections**: User says "I wouldn't say that" or "that's not me" → extract [situation, wrong, correct] → add to Corrections section.

**New material**: User provides more source material → re-run analysis → merge new patterns via incremental update (check for conflicts, prompt user on contradictions).

**Calibration**: Periodically ask "does this sound like you?" on generated outputs. Track accuracy over time.

## Output

The skill file at `~/.claude/skills/{alias}-twin/SKILL.md` is immediately usable in any Claude Code session. Trigger with "{alias} mode" or "be {alias}".

## Anti-patterns

- Vague adjectives instead of behavioral rules ("decisive" → useless. "When presented with two options and no data, picks the faster-to-validate" → useful.)
- Overfitting to one data source (git commits show work, not personality. Emails show tone, not technical depth. Need multiple sources.)
- Copying colleague-skill's Chinese tech company level mapping (irrelevant for most users — skip it.)
- Generating without preview/confirmation (the user must validate Layer 0 rules before the skill is written.)
