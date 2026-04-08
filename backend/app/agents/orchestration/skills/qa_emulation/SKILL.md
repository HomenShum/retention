# QA Emulation Skill

## Overview
Reproduce mobile app bugs across multiple builds using parallel subagent analysis.

## Workflow
1. **LEASE_DEVICE** → Acquire test device
2. **LOGIN** → Authenticate into the app
3. **LOAD_BUILD_OG** → Load Original build
4. **REPRO_ON_OG** → Attempt bug reproduction on OG
5. **LOAD_BUILD_RB1-RB3** → Load each Regression Build
6. **REPRO_ON_RB1-RB3** → Attempt reproduction on each RB
7. **GATHER_EVIDENCE** → Collect all evidence
8. **ASSEMBLE_VERDICT** → Final structured verdict

## Parallel Analysis Pattern
After each build test, two specialists run in parallel via `asyncio.gather`:
- **Bug Detection Specialist** (gpt-5.4): Classifies whether the expected bug was reproduced
- **Anomaly Detection Specialist** (gpt-5-mini): Monitors for unexpected issues

## Verdict Types
- `REPRODUCIBLE`: Bug reproduced on OG + at least one RB
- `NOT_REPRODUCIBLE`: Bug not found on any build
- `BLOCKED_NEW_BUG`: A different bug blocks reproduction
- `INSUFFICIENT_EVIDENCE`: Evidence too weak to determine

## Evidence Requirements
- At least one screenshot per build tested
- Element dump for screens where bug should manifest
- Logs if crash or error occurs
- Network trace if relevant

## Agent Variants
- **v11_compact**: All skills pre-loaded, no on-demand loading
- **v12**: Lean base with on-demand skill loading via `load_skill()`
- **v12_compaction**: v12 + aggressive context compaction between builds

## Key Tools
- `set_phase(phase)`: Track workflow phase
- `store_evidence(id, build_id, type, description)`: Record evidence
- `load_skill(name)`: Load repo-native skill context via progressive disclosure (v12/v12_compaction only)

## Skill Loading Conventions
- Prefer repo-native skill names:
  - `device_testing` → device control, navigation, screenshots
  - `qa_emulation` → build sequence, anomaly analysis, verdict rules
- Legacy aliases from older prompt drafts (for example `device_setup`, `bug_detection`, `verdict_assembly`) may still resolve, but should not be used for new workflow wiring.

## Critical Rules
- NEVER skip a build in the sequence
- ALWAYS capture evidence before moving to next build
- NEVER emit a verdict without using the Verdict Assembly Specialist
- All decisions are LLM-based — no hard-coded heuristics

