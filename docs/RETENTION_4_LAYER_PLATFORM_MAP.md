# retention.sh — 4-Layer Platform Map

## Architecture Principle

One canonical workflow graph → exposed through four surfaces.
All layers read and write the same objects. No forked truth.

```
┌─────────────────────────────────────────────────────────────┐
│                    TRUTH SPINE                               │
│  Workflow · Run · Trajectory · ActionEvent · PathNode/Edge  │
│  StateSnapshot · Artifact · FailureBundle · MemoryObject    │
│  BenchmarkCase · RollupSummary · Packet                     │
└──────┬──────────┬──────────────┬──────────────┬─────────────┘
       │          │              │              │
   ┌───▼───┐  ┌──▼────┐  ┌─────▼─────┐  ┌────▼──────┐
   │ HABIT │  │VISIBLE│  │ EXECUTION │  │DISTRIBUTE │
   │ LAYER │  │ LAYER │  │   LAYER   │  │   LAYER   │
   │       │  │       │  │           │  │           │
   │ MCP   │  │Dashbd │  │ Replay    │  │ Hosted    │
   │ Tools │  │ Web   │  │ Routing   │  │ Results   │
   │ CLI   │  │ API   │  │ Packets   │  │ Templates │
   └───────┘  └───────┘  └───────────┘  └───────────┘
```

---

## Canonical Objects — Implementation Status

| Object | Defined | MCP Tools | API | Convex | Dashboard | Status |
|--------|---------|-----------|-----|--------|-----------|--------|
| **Workflow** | schemas.py:58 | retention.pipeline.run | /api/workflow_registry | ❌ | TrajectoriesPage | PARTIAL — not synced to Convex |
| **Run** | run_session.py:49 | retention.pipeline.* | /api/benchmarks | benchmarkRuns | BenchmarkReportPage | ✅ FULL |
| **Trajectory** | trajectory_logger.py:52 | ta.trajectory.* | /api/trajectories | trajectories, trajectorySavings | TrajectoriesPage, MemoryDashboard | ✅ FULL |
| **ActionEvent** | trajectory_logger.py:28, action_span_models.py:113 | ta.action_spans.* | /api/action_spans | actionSpans | DeviceControlPage | ✅ FULL |
| **PathNode/Edge** | context_graph.py:86 | ta.graph.* (6 tools) | /api/mcp_context_graph | ❌ | JudgeDashboard | PARTIAL — filesystem only |
| **StateSnapshot** | action_span_models.py:140 | implicit | /api/action_spans | actionSpans | ActionSpan views | ✅ FULL |
| **Artifact** | evidence_schema.py:54 | retention.pipeline.screenshot | /api/artifacts | chefArtifacts | ReportPage | ✅ FULL |
| **FailureBundle** | evidence_schema.py:206 | retention.pipeline.failure_bundle | /api/failures | ❌ derived | ReportPage, Slack | ✅ FULL |
| **MemoryObject** | exploration_memory.py | retention.memory.* (5 tools) | /api/memory | institutionalMemory, teamMemory | MemoryDashboard | PARTIAL — siloed |
| **BenchmarkCase** | golden_bug_models.py:47 | implicit | /api/golden_bugs | testCaseTemplates | BenchmarkReport | ✅ FULL |
| **RollupSummary** | ❌ | ❌ | ❌ | ❌ | computed on-the-fly | ❌ MISSING |
| **Packet** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ MISSING |

---

## Layer 1: Habit Layer (MCP Tools)

### Current tools (deployed)
```
retention.qa_check(url)          — instant QA verdict
retention.sitemap(url)           — interactive site map with drill-down
retention.ux_audit(url)          — 21-rule UX audit
ta.crawl.url(url)         — full crawl with screenshots + findings
retention.diff_crawl(url)        — before/after delta
ta.suggest_tests(url)     — auto-generate test cases
ta.qa.redesign(url)       — full QA→fix→verify loop
ta.trajectory.list        — list saved trajectories
ta.trajectory.replay      — replay at 60-70% fewer tokens
ta.trajectory.compare     — A/B trajectory comparison
retention.savings.compare        — show token/time savings
retention.team.invite            — generate Slack onboarding message
retention.team.status            — team membership + dashboard URL
ta.onboard.status         — prerequisites check
retention.system_check           — health + version + update notification
```

### Target tools (from architecture vision)
```
retention.start_workflow(id, target, mode)    — start with trajectory awareness
ta.resume_workflow(run_id)             — resume interrupted run
ta.verify.checkpoint(run_id, id)       — validate specific checkpoint
ta.verify.failure_bundle(run_id)       — extract diagnostic bundle
ta.verify.before_after(a, b)           — state comparison
retention.memory.lookup(entity)               — contextual memory retrieval
retention.memory.handoff(run_id)              — session handoff summary
retention.memory.rollup(period, family)       — aggregated rollup
ta.profile.export()                    — memory bundle export
ta.profile.import()                    — memory bundle import
ta.profile.sync(mode)                  — local↔cloud sync
```

---

## Layer 2: Visibility Layer (Dashboard)

### Current dashboard sections
| Section | Route | Status |
|---------|-------|--------|
| Demo / Site Map | /demo | ✅ Live — crawl any URL |
| Walkthrough | /demo/walkthrough | ✅ Live — 7-step tour |
| QA Pipeline | /demo/curated | ✅ Live — live browser + generate |
| Benchmark | /report | ✅ Live — head-to-head comparison |
| Individual Memory | /memory | ✅ Live (empty state when no backend) |
| Team Memory | /memory/team | ✅ Live with Convex data |
| Local Memory | /memory/local | ✅ Live (empty state when no backend) |
| Hackathon | /hackathon | ✅ Live — 30s-to-value landing |
| Install Guide | /docs/install | ✅ Live |

### Target dashboard sections (from architecture vision)
| Section | Purpose | Status |
|---------|---------|--------|
| Workflow Home | Active workflows, replay readiness, drift alerts | ❌ Not built |
| Run View | Action timeline, tool calls, screenshots, verdict | PARTIAL (in TrajectoriesPage) |
| Trajectory View | Map, checkpoints, replay count, drift incidents | PARTIAL |
| Memory View | History, handoffs, stale events, reuse stats | PARTIAL (MemoryDashboard) |
| Profile/Sync View | Local vs cloud, conflicts, import/export | PARTIAL (LocalMemoryPage) |
| Rollup View | Daily/weekly/monthly trend charts | ❌ Not built |

---

## Layer 3: Execution Layer (Runtime Routing)

### Current state
- Replay engine: trajectory_replay.py with checkpoint validation + fallback
- Workflow compression: workflow_compression.py with CRUD shortcuts
- Multi-surface: multi_surface.py with 4 surface configs
- Longitudinal harness: longitudinal_harness.py with N=1/5/10/100

### Missing: Packet protocol
```python
@dataclass
class ExecutionPacket:
    workflow_id: str
    run_mode: str  # "replay" | "explore" | "replay_with_fallback"
    surface: str   # "browser" | "android" | "desktop" | "hybrid"
    trajectory_id: Optional[str]
    success_criteria: List[str]
    memory_context: Dict[str, Any]  # prior runs, drift points, entry paths
    budget: Dict[str, Any]  # max_requests, max_cost, max_duration
    runtime_target: str  # "claude_code" | "openclaw" | "custom_sdk"
```

---

## Layer 4: Distribution Layer (Shareable Results)

### Current state
- Founder demo video: retention.sh/videos/founder-demo.mp4
- Hackathon landing: retention.sh/hackathon
- Team dashboard: retention.sh/memory/team?team=CODE (shareable URL)
- Crawl results: persisted in Convex, shareable via ?crawl=ID
- Benchmark report: retention.sh/report (fallback data)

### Target state
- Hosted benchmark result pages per workflow family
- Public trajectory templates (reusable workflow packs)
- Savings report generator (PDF/image for sharing)
- Workflow case studies (KYB, EHR, Portal)

---

## Gaps to Close (Priority Order)

### P0 — Truth Spine Completeness
1. **RollupSummary table** in Convex — pre-computed daily/weekly/monthly snapshots
2. **Packet dataclass** in backend — formalize runtime handoffs
3. **Sync Workflows to Convex** — enable team workflow library

### P1 — Layer Integration
4. **Persist ContextGraph to Convex** — shared failure diagnosis
5. **Unify Memory subsystem** — bidirectional filesystem↔Convex sync
6. **Workflow Home dashboard** — single view of active workflows + replay readiness

### P2 — Distribution
7. **Hosted benchmark pages** per workflow family
8. **Savings report generator** (shareable image/PDF)
9. **Smithery.ai marketplace listing** (config ready)
10. **MCP Hackathon registration** as enabler tool

---

## Build Order

```
Week 1: RollupSummary + Packet + Workflow sync to Convex
Week 2: Workflow Home dashboard + Run View improvements
Week 3: ContextGraph Convex persistence + unified memory
Week 4: Distribution — hosted benchmarks + Smithery listing
```

This doc is the source of truth for retention.sh architecture.
All new features must map to one of the 4 layers and read/write canonical objects.
