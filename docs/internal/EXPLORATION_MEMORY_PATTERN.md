# Exploration Memory Pattern

## What Problem This Solves

AI agents that crawl UIs or web apps are expensive. Every run re-discovers the same screens, re-generates the same workflows, and re-creates the same test cases — burning tokens that are identical to the last run.

**Exploration Memory** converts the first expensive run into durable, reusable cache. Subsequent runs skip every stage that hasn't changed, executing only the live device/browser check that actually needs to run fresh.

```
Run 1:  CRAWL ($) → WORKFLOW ($) → TESTCASE ($) → EXECUTION (device time)
Run N:  cache ──── cache ──────── cache ───────── EXECUTION only
```

Measured token savings per cached run (from live benchmark data):
| Stage skipped | Tokens saved | Approx cost |
|---|---|---|
| CRAWL | ~11,000 | ~$0.005 |
| WORKFLOW | ~8,000 | ~$0.003 |
| TESTCASE | ~12,000 | ~$0.005 |
| **Total per run** | **~31,000** | **~$0.013** |

At 50 runs/month on a single app that is $7.80/month saved on that app alone. At 100+ apps and 10+ runs each, this reaches hundreds of dollars.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                   LIVE RUN WRITE PATH                           │
│                                                                 │
│  1. register_screen()       ← crawl_tools.py (write-time)      │
│     └─ sanitizes parent_screen_id                               │
│     └─ computes navigation_depth from parent                    │
│     └─ incremental save to disk every 2+ screens                │
│                                                                 │
│  2. complete_crawl()        ← crawl_tools.py                   │
│     └─ calls store_crawl() → normalize_crawl_result()           │
│                                                                 │
│  3. store_crawl()           ← exploration_memory.py (persist)  │
│     └─ normalize_crawl_payload() — full normalization pass      │
│     └─ writes {app_key}.json to data/exploration_memory/crawl/  │
│     └─ archives previous crawl to crawl/history/               │
│     └─ updates memory_index.json                               │
│                                                                 │
│  4. GET /api/memory/app/{app_key}/graph ← main.py (read)       │
│     └─ normalize_crawl_payload() — normalizes again on read     │
│     └─ injects crawl_index (chronological discovery order)      │
│     └─ deduplicates hierarchy vs transition edges               │
│                                                                 │
│  5. MemoryGraph.tsx         ← frontend (render)                │
│     └─ sorts siblings by crawl_index (not heuristics)           │
│     └─ ancestor-tracing with cycle-safe seen Set                │
└─────────────────────────────────────────────────────────────────┘
```

---

## Hierarchy Normalization Rules

**`normalize_crawl_payload()`** enforces these invariants at every layer:

1. **No self-reference** — `parent_screen_id == screen_id` → cleared to `None`
2. **No forward reference** — parent must appear *earlier* in array order
3. **No orphan parents** — if parent ID is unknown, cleared to `None`
4. **Recomputed depth** — `navigation_depth = parent.navigation_depth + 1` (or 0 for roots)
5. **Ghost transitions dropped** — transitions to unknown screen IDs are removed
6. **Duplicate transitions deduplicated** — keyed by `(from, to, action, component_id, edge_type)`

This runs at **three layers**:
- **Write-time** (`register_screen`): immediate sanitization on discovery
- **Persist-time** (`store_crawl`): full normalization before writing to disk
- **Read-time** (graph API): re-normalization on every request (self-healing)

---

## Key Files

| File | Role |
|---|---|
| `backend/app/agents/qa_pipeline/exploration_memory.py` | Core: fingerprinting, cache r/w, normalization, delta crawl |
| `backend/app/agents/qa_pipeline/tools/crawl_tools.py` | Write-time: BFS crawler with incremental save and parent sanitization |
| `backend/app/main.py` (L914–998) | Read-time: graph API with crawl_index injection |
| `frontend/test-studio/src/components/MemoryGraph.tsx` | Render: React Flow graph, crawl_index-based sibling order |
| `backend/tests/test_exploration_memory_stats.py` | Tests: hierarchy repair and normalization correctness |
| `backend/tests/test_memory_graph_api.py` | Tests: graph API response shape and hierarchy invariants |

---

## Storage Layout

```
data/exploration_memory/
├── memory_index.json             # App registry + aggregate stats
├── crawl/
│   ├── {app_key}.json            # Latest crawl for each app
│   └── history/
│       └── {app_key}_{ts}.json   # Prior crawls (auto-archived)
├── workflows/
│   └── {app_key}_{crawl_fp}.json # Cached workflow analysis
└── test_suites/
    └── {app_key}_{crawl_fp}.json # Cached test cases
```

`app_key` = `sha256(url|package|name)[:16]` — stable, URL-normalized.

`crawl_fingerprint` = `sha256(sorted screen names + bucketed component counts)[:16]` — layout-stable, content-agnostic.

---

## Fingerprinting Design

Three independent fingerprints serve different purposes:

```python
# App-level: stable key for cache lookup
def app_fingerprint(url, package, name) -> str:
    key = (url or package or name).rstrip("/").lower()
    return sha256(key)[:16]

# Screen-level: detects if a screen changed
def screen_fingerprint(screen) -> str:
    sigs = sorted(f"{c.element_type}:{c.is_interactive}:{c.text[:20]}" for c in screen.components)
    return sha256(f"{screen.screen_name}|{'|'.join(sigs)}")[:12]

# Crawl-level: detects if the app layout changed (bucketed, content-agnostic)
def crawl_fingerprint(crawl) -> str:
    sigs = sorted(f"{s.screen_name}:{(len(s.components)//5)*5}" for s in crawl.screens)
    return sha256("|".join(sigs))[:16]
```

The bucketing in `crawl_fingerprint` (rounds component count to nearest 5) makes it resilient to minor content changes (e.g., different search results) while detecting real layout changes (added/removed screens).

---

## Cache Decision Logic

```python
result = check_memory(app_url=url)

if result.crawl_hit:     # skip CRAWL stage
if result.workflow_hit:  # skip WORKFLOW stage
if result.test_suite_hit: # skip TESTCASE stage
# EXECUTION always runs — that's the whole point
```

`check_memory()` returns `MemoryCheckResult` with:
- Which stages are cached
- Which stages must run
- Estimated tokens saved
- Estimated cost saved

---

## Delta Crawl (Surgical Re-processing)

When the app has changed, `delta_crawl()` compares old vs new crawl by screen fingerprints:

```python
delta = delta_crawl(old_crawl, new_crawl, old_workflows_json, old_test_suite)
# delta.added_screens / removed_screens / changed_screens / unchanged_screens
# delta.affected_workflows / affected_tests

# Only invalidate what touched changed screens:
invalidate_affected_only(app_key, crawl_fp, delta.affected_workflows, delta.affected_tests)

# Merge: keep unchanged from cache, take changed from new crawl:
merged = merge_crawl(old_crawl, new_crawl, delta)
```

This means a one-screen change invalidates only workflows and tests that reference that screen. Everything else stays cached.

---

## Crash Resilience: Incremental Save

The crawl agent saves to disk **every 2+ screens** during an active crawl:

```python
# In register_screen() — crawl_tools.py
if _app_key and len(screens) >= 2:
    store_crawl(_app_key, _build_result(), app_url=app_url, app_name=app_name)
```

This means if the backend OOMs, times out, or the emulator crashes mid-crawl, the partial result is already on disk and will be used as a cache hit on the next attempt (if fingerprint matches).

Crashed/empty crawls (0 screens) are actively rejected and deleted:

```python
# In load_crawl() — exploration_memory.py
if result.total_screens == 0 or len(result.screens) == 0:
    path.unlink(missing_ok=True)
    return None
```

---

## Frontend: Deterministic Visual Ordering

The graph uses `crawl_index` (discovery order, injected by the API) to sort siblings:

```typescript
// MemoryGraph.tsx — sibling sort
const sortedSiblings = siblings.sort((a, b) => {
  if (a.parent_index !== b.parent_index) return a.parent_index - b.parent_index;
  return a.crawl_index - b.crawl_index;   // ← stable, not heuristic
});
```

Ancestor tracing (to highlight path on node click) is cycle-safe:

```typescript
const seen = new Set<string>();
let current: ScreenData | undefined = selectedScreen;
while (current?.parent_screen_id) {
  if (seen.has(current.parent_screen_id)) break;  // cycle guard
  seen.add(current.parent_screen_id);
  current = screenMap[current.parent_screen_id];
  if (current) ancestors.add(current.screen_id);
}
```

Edge types are visually differentiated:
- `hierarchy` — solid blue (parent → child, always structural)
- `action` — dashed amber, animated
- `navigation` — dashed purple
- `tab` — dashed green, animated
- `reference` — dashed pink
- `recovery` — dashed orange, animated

Hierarchy edges deduplicate against transition edges so a parent→child relationship is only drawn once.

---

## Adapting for Other Codebases

### Minimum viable port

1. **Copy `exploration_memory.py`** — self-contained, only deps are `pydantic` and stdlib.
2. **Define your `CrawlResult` schema** — needs: `screens` (list with `screen_id`, `screen_name`, `navigation_depth`, `parent_screen_id`, `components`), `transitions` (list with `from_screen`, `to_screen`, `action`).
3. **Call `store_crawl()` after every agent run** — pass the agent's result.
4. **Call `check_memory()` before every agent run** — skip stages that are cached.

### For any BFS-style agent (not just mobile testing)

Replace "screen" with your entity type (page, node, state, document). The fingerprinting and normalization logic applies to any hierarchical graph where:
- Nodes have a parent reference
- Discovery order matters
- Re-exploration is expensive

### For visualization

The `MemoryGraph.tsx` component is a complete React Flow graph. Minimum data contract from the API:

```typescript
interface ScreenData {
  screen_id: string;
  screen_name: string;
  navigation_depth: number;
  parent_screen_id: string | null;
  crawl_index: number;          // ← must be injected by API
}
interface GraphEdge {
  from_screen: string;
  to_screen: string;
  edge_type: 'hierarchy' | 'action' | 'navigation' | 'tab' | 'reference' | 'recovery';
}
```

---

## Tests to Copy

```python
# test_store_crawl_normalizes_hierarchy_for_live_runs
# Verifies: broken parents cleared, depth recomputed, ghost transitions dropped
# See: backend/tests/test_exploration_memory_stats.py::test_store_crawl_normalizes_hierarchy_for_live_runs

# test_graph_api_hierarchy_is_acyclic
# Verifies: no screen is reachable from itself via hierarchy edges
# See: backend/tests/test_memory_graph_api.py
```

---

## What Changed vs Naive Implementation

| Naive | This Pattern |
|---|---|
| Re-crawl every run | Skip crawl if fingerprint matches |
| Re-generate workflows every run | Skip workflow gen if crawl unchanged |
| Store raw agent output | Normalize hierarchy before write |
| Trust agent's parent_screen_id | Validate and repair at 3 layers |
| Heuristic sibling sort (child count) | Deterministic `crawl_index` sort |
| No cycle protection | `seen` Set in ancestor trace |
| Crash loses all progress | Incremental save every 2 screens |
| Full invalidation on any change | Delta invalidation (per-screen) |

