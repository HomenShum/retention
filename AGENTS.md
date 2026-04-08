# retention.sh Agent Instructions

> **For AI Coding Agents** — This file provides repository-level context for Claude Code, Gemini, Codex, Cursor, and other AI coding assistants.

## Project Overview

retention.sh is a **full-stack mobile test automation platform** with:
- **Backend**: FastAPI (Python 3.11+) at `backend/`
- **Frontend**: React + TypeScript + Vite at `frontend/test-studio/`
- **Mobile MCP**: Model Context Protocol for Android emulator control
- **AI Agents**: Hierarchical multi-agent system (Coordinator → Specialists)

---

## ⚡ Quick Commands

```bash
# Backend
cd backend && python -m uvicorn app.main:app --reload --port 8000

# Frontend  
cd frontend/test-studio && npm run dev

# Run all tests
cd backend && pytest
cd frontend/test-studio && npm run test

# E2E tests
npx playwright test

# Type checking
cd frontend/test-studio && npx tsc --noEmit
```

---

## 🔄 Closed-Loop Verification Architecture (Ralph Loop)

This repository follows the **Ralph Loop** pattern for autonomous, self-correcting development. AI agents should operate in a **closed-loop** cycle:

### The Think-Act-Verify Loop

```
┌─────────────────────────────────────────────────────────────┐
│                    CLOSED-LOOP CYCLE                        │
│                                                             │
│   1. THINK    → Analyze task, plan approach                │
│   2. ACT      → Make changes (code, tests, docs)           │
│   3. VERIFY   → Run tests, type-check, lint                │
│   4. OBSERVE  → Check results, analyze failures            │
│   5. ADAPT    → If failed, diagnose and retry from step 1  │
│   6. COMMIT   → Only when all checks pass                  │
│                                                             │
│   ⚠️  NEVER commit without running verification!            │
└─────────────────────────────────────────────────────────────┘
```

### Verification Commands (Run Before Every Commit)

```bash
# Backend verification sequence
cd backend
pytest --tb=short                    # Run tests
python -m mypy app/ --ignore-missing-imports  # Type check (if mypy configured)

# Frontend verification sequence
cd frontend/test-studio  
npm run build                        # Build check (catches TS errors)
npm run lint                         # Lint check
npm run test -- --run               # Unit tests

# E2E verification (critical features)
npx playwright test tests/e2e/golden-bugs.spec.ts
```

### AI-Driven Verification

When implementing complex features (e.g., AI logic, specialized algorithms), use an LLM-based verification script in addition to unit tests.

**Pattern:**
1. Create a verification script in `backend/scripts/ai_verify.py` (or similar).
2. Script reads the implementation files and test files.
3. Script sends code to a strong model (**GPT-5.4**) for analysis.
4. Script outputs a PASS/FAIL report based on architectural alignment and best practices.

**Example Usage:**
```bash
cd backend
# Ensure dependencies installed (including openai, python-dotenv)
python scripts/ai_verify.py
```

### Self-Correction Protocol

When verification fails:
1. **Read the error** — Understand what broke
2. **Diagnose root cause** — Don't just patch symptoms
3. **Fix systematically** — Update code, tests, and docs together
4. **Re-verify** — Run full verification again
5. **Iterate** — Repeat until green

---

## 📁 Codebase Map

### Backend (`backend/app/`)

| Path | Purpose |
|------|---------|
| `main.py` | FastAPI entry point, route registration |
| `agents/coordinator/` | Main orchestration agent (GPT-5) |
| `agents/device_testing/` | Mobile MCP client, navigation tools |
| `agents/device_testing/mobile_mcp_client.py` | Mobile MCP + ADB fallback |
| `agents/device_testing/tools/` | Autonomous navigation tools |
| `agents/device_testing/subagents/` | OAVR sub-agents |
| `agents/device_testing/flicker_detection_service.py` | 4-layer video-based flicker/glitch detection |
| `agents/device_testing/demo_walkthrough_service.py` | Screen-record + TTS narrated walkthrough generation |
| `api/` | FastAPI route handlers |
| `benchmarks/` | Android World benchmarks, PRD evaluation |
| `figma/` | Figma design analysis (flow extraction, clustering, CV overlay) |
| `figma/flow_analyzer.py` | 3-phase Figma flow analysis pipeline |
| `integrations/` | External tool integrations (stub) |
| `integrations/chef/` | Chef Convex integration (stub — modules not yet implemented) |
| `observability/tracing.py` | LangSmith tracing integration |

### Scripts (`backend/scripts/`)

| Path | Purpose |
|------|---------|
| `figma_cv_overlay.py` | Direct CV overlay — detects flow groups from Figma screenshots |
| `test_figma_flow_analyzer.py` | Figma flow analyzer test harness (demo + real file) |
| `test_flicker_detection.py` | Flicker detection PoC on real emulator |
| `generate_demo_walkthrough.py` | Narrated screen-record demo walkthrough generator |
| `annotate_real_device.py` | SoM annotation demo on real device screenshots |
| `generate_annotation_demo.py` | Generate annotation demo images |
| `ai_verify.py` | LLM-based code verification script |

### Chef Integration (`integrations/chef/`)

| Path | Purpose |
|------|---------|
| `test-kitchen/` | Braintrust evaluation harness (agentic loop, scoring) |
| `test-kitchen/initialGeneration.eval.ts` | Multi-model eval (GPT-5.x, Claude 4, Gemini) |
| `test-kitchen/main.ts` | Standalone runner (GPT-5.4 default) |
| `patches/@ai-sdk__openai@1.3.6.patch` | **CRITICAL** — treats GPT-5.x as reasoning models |
| `chef-agent/` | Core agent logic (system prompts, tool calls) |
| `convex/` | Chef's Convex schema and functions |
| `app/lib/common/annotations.ts` | **Zod-validated annotation parser** — usage, failure, model types |
| `app/lib/.server/usage.ts` | Usage recording + model annotation encoding |

> ⚠️ **Isolation**: Chef uses React 18 + ai SDK 4.x + Remix. TA frontend uses React 19 + ai SDK 5.x + Vite. **Never share node_modules.** Backend wraps Chef via subprocess/API.

#### Chef Annotation System

Three annotation types parsed by `parseAnnotations()`:
- **`usage`** — Token tracking per tool call. Payload is JSON-stringified inside `usage.payload`.
- **`failure`** — Repeated error detection (sets `failedDueToRepeatedErrors` flag).
- **`model`** — Provider metadata (Anthropic/OpenAI/XAI/Google/Bedrock/Unknown) + model choice.

**Critical rules:**
1. `JSON.parse()` on annotation payloads MUST be wrapped in `try-catch` — malformed payloads crash the UI render loop.
2. `encodeModelAnnotation()` must default provider to `'Unknown'` (not `null`) — Zod requires a valid `ProviderType` string.
3. `toolCallId` must never be `null` — use `call.toolCallId ?? 'unknown'` for null-coalescing.
4. `response.json()` can only be called ONCE — store in a variable and reuse.

### Frontend (`frontend/test-studio/src/`)

| Path | Purpose |
|------|---------|
| `pages/` | Main page components |
| `components/` | Reusable UI components |
| `components/emulator/` | Emulator HUD, streaming |
| `hooks/` | Custom React hooks |
| `lib/` | Utility functions |

### Key Configuration Files

| File | Purpose |
|------|---------|
| `.env` | Environment variables (copy from `.env.example`) |
| `backend/requirements.txt` | Python dependencies |
| `frontend/test-studio/package.json` | Node dependencies |
| `playwright.config.ts` | E2E test configuration |

---

## 🧠 Agent Architecture Patterns

### OAVR Pattern (Observe-Act-Verify-Reason)

The device testing agent uses OAVR for autonomous navigation:

```
Observe  → Screen Classifier Agent analyzes current screen
Act      → Execute action (click, swipe, type)
Verify   → Action Verifier confirms success
Reason   → Failure Diagnosis suggests recovery if failed
```

**Key files:**
- `agents/device_testing/subagents/screen_classifier_agent.py`
- `agents/device_testing/subagents/action_verifier_agent.py`
- `agents/device_testing/subagents/failure_diagnosis_agent.py`

### 👁️ Vision-Augmented Navigation (Sight-Based Interaction)

When the accessibility tree (`list_elements_on_screen`) is insufficient (e.g., canvas-based UI or loading states), the agent uses **Agentic Vision** (GPT-5.4) to find elements visually.

**Primary Tool**: `vision_click(query="...")`
- **Logic**: Captures screenshot → Identifies target pixels via GPT-5.4 reasoning → Executes coordinate click.
- **Workflow**: Think (analysis) → Act (determine coordinates) → Verify (coordinate click).
- **Precision**: Grounded using pixel-perfect grounding in the Agentic Vision loop.

### 📸 Screenshot Annotation (SoM-Style)

The screenshot annotation system uses **Set-of-Mark (SoM)** style from OmniParser for color-coded, type-aware bounding boxes.

**Two annotation systems:**
1. **PIL-based** (primary): `autonomous_navigation_tools.py` — draws on accessibility tree elements
2. **Agentic Vision**: `agentic_vision_service.py` — LLM-powered annotation utility

**9-color element type palette:**
| Type | Color | Tag | Example Classes |
|------|-------|-----|-----------------|
| button | Dodger blue | BTN | Button, ImageButton, FAB |
| input | Orange | INPUT | EditText, SearchView |
| toggle | Purple | TOGGLE | Switch, CheckBox, RadioButton |
| nav | Deep pink | NAV | BottomNavigationView, Toolbar |
| image | Dark cyan | IMG | ImageView |
| text | Gray | TXT | TextView |
| list | Forest green | LIST | RecyclerView, ListView |
| container | Dark gray | BOX | FrameLayout, LinearLayout |
| unknown | Green | ELEM | Unclassified elements |

**Critical rules:**
1. **Class map ordering**: Specific substrings (radiobutton, compoundbutton) MUST precede generic ones (button) — first match wins.
2. **Dual element format**: Support both MCP nested (`coordinates.width`) and ADB flat (`width`) keys.
3. **Resolution scaling**: Font = `width/54`, line = `width/360`. Never use fixed pixel sizes.
4. **Priority sorting**: Interactive elements first, then by area descending. Split min_area: 100 (interactive) / 400 (static).
5. **Container filtering**: Skip FrameLayout/LinearLayout/RelativeLayout to reduce visual noise.

### Golden Bug Evaluation

Deterministic test cases for regression testing. Bypass multi-agent orchestration.

**Key files:**
- `agents/device_testing/golden_bug_service.py`
- `agents/device_testing/golden_bug_models.py`
- `data/golden_bugs.json`

### 🎨 Figma Flow Analysis & CV Overlay

Analyzes Figma design files to extract, cluster, and visualize visual flows for test case generation.

**3-Phase Pipeline:**
1. **Extract** — Figma REST API (`depth=3`) pulls FRAME nodes inside SECTION nodes (DOC→CANVAS→SECTION→FRAME)
2. **Cluster** — Multi-signal priority cascade: Sections → Prototype connections → Name prefixes → Spatial (Y-bin + X-gap)
3. **Visualize** — PIL bounding boxes overlaid on real Figma canvas screenshots

**Direct CV Overlay** (`scripts/figma_cv_overlay.py`):
When the Figma Images API is rate-limited, uses pure computer vision on browser screenshots:
- Brightness thresholding (>80 for sections, >100 for frames)
- Morphological closing/opening (scipy.ndimage) to bridge intra-section gaps
- Connected component analysis to detect section groups
- Column brightness profiling for sub-frame detection (requires >25% zoom)

**Key files:**
- `app/figma/flow_analyzer.py` — Core 3-phase pipeline (FigmaFrame, FlowGroup dataclasses)
- `scripts/figma_cv_overlay.py` — Standalone CV overlay generator
- `scripts/test_figma_flow_analyzer.py` — Test harness (demo + real Figma file)

**Figma API gotchas:**
1. Use `depth=3` (not 2) to reach FRAMEs inside SECTIONs
2. Images API has plan-tier rate limits (can be 4+ day cooldowns)
3. `absoluteBoundingBox` on SECTION nodes includes padding — don't compute from child frames
4. Browser session API (`/api/files/:key`) returns metadata only, not document tree (loaded via WASM)

### 🔦 Flicker Detection Pipeline

4-layer architecture to detect screen flickers too fast for periodic screenshots (16-200ms).

**Layers:**
- **Layer 0**: SurfaceFlinger frame timing + logcat monitoring (always-on, zero cost)
- **Layer 1**: `adb screenrecord` triggered recording (60fps H.264)
- **Layer 2**: ffmpeg scene-filtered extraction + parallel SSIM analysis
- **Layer 3**: GPT-5.4 vision verification (semantic bug vs animation classification)

**Optimizations (19x speedup):**
- ffmpeg scene detection pre-filter (60-80% frame reduction)
- JPEG extraction (5-10x smaller than PNG)
- Parallel SSIM via ProcessPoolExecutor (3-5x speedup)
- Adaptive threshold (median - 2σ)

**Key files:**
- `agents/device_testing/flicker_detection_service.py` — Full pipeline (1117 lines)
- `scripts/test_flicker_detection.py` — PoC on real emulator

---

## 🛠️ Code Style Guidelines

### Python (Backend)

- **Imports**: Use absolute imports from `app.` prefix
- **Type hints**: Required for all function signatures
- **Docstrings**: Google style for public functions
- **Async**: Use `async/await` for I/O operations
- **Logging**: Use `logging.getLogger(__name__)`

```python
# ✅ Good
from app.agents.device_testing.mobile_mcp_client import MobileMCPClient

async def take_screenshot(device_id: str) -> str:
    """Take screenshot from device.
    
    Args:
        device_id: Android device identifier
        
    Returns:
        Base64-encoded screenshot data
    """
    pass
```

### TypeScript (Frontend)

- **Components**: Functional components with hooks
- **Types**: Explicit types, avoid `any`
- **Imports**: Use `@/` path alias for src imports
- **State**: React hooks for local state, no Redux

```typescript
// ✅ Good
interface DeviceProps {
  deviceId: string;
  onStreamStart: (id: string) => void;
}

const DeviceCard: React.FC<DeviceProps> = ({ deviceId, onStreamStart }) => {
  // ...
};
```

---

## 🔧 Workflow Documentation Protocol

> **CRITICAL**: When you discover or create a new workflow, ADD IT to this file or create a workflow file in `.agent/workflows/`.

### When to Update AGENTS.md

- ✅ New build/test command discovered
- ✅ New debugging pattern identified
- ✅ Architecture pattern that future agents need
- ✅ Common error and its fix
- ✅ Integration with new service/tool

### Workflow File Format

For complex workflows, create `.agent/workflows/{name}.md`:

```markdown
---
description: How to [specific task]
---

## Prerequisites
- [requirement 1]

## Steps
1. [step 1]
// turbo  ← Add this to auto-run safe commands
2. [step 2]

## Verification
- [ ] Check 1
- [ ] Check 2
```

---

## 🐛 Common Issues & Fixes

### Mobile MCP "Device not found"

**Symptom**: MobileMCPClient returns device not found errors

**Fix**: ADB fallback is automatic. Check:
```bash
adb devices  # Verify device is connected
```

### Rate Limit Errors (OpenAI)

**Symptom**: 429 errors from OpenAI API

**Fix**: TOON format reduces tokens by 30-60%. Already integrated in:
- `agents/device_testing/tools/autonomous_navigation_tools.py`

### Figma Images API Rate Limit (Plan-Tier)

**Symptom**: 429 on Figma REST API with `Retry-After: 396156` (4.6 days)

**Headers**: `x-figma-plan-tier: enterprise`, `x-figma-rate-limit-type: low`

**Workaround**: Use direct CV overlay (`scripts/figma_cv_overlay.py`) — captures browser screenshot via Playwright, detects flow groups via brightness thresholding + morphological connected components. No API calls needed.

**Note**: The browser session API (`/api/files/:key`) bypasses token rate limits but returns only metadata, not the document tree (loaded via WASM).

### E2E Tests Failing

**Debug sequence**:
```bash
# 1. Check services are running
curl http://localhost:8000/health
curl http://localhost:5173

# 2. Run with trace
npx playwright test --trace on

# 3. View trace
npx playwright show-report
```

### Backend API Router Import Error (Pre-existing)

**Symptom**: `cannot import name 'create_agentic_vision_tools' from 'app.agents.device_testing.tools'`

**Status**: Pre-existing issue, NOT caused by Chef integration. The export doesn't exist in `tools/__init__.py`.

**Impact**: Affects `app.api` module import chain. Non-blocking for most development.

### Integration Module Stub Gotcha

**Symptom**: `ImportError` when `__init__.py` tries to `from .module import Class` on non-existent files.

**Fix**: Use lazy/comment imports for stub modules. Never add `from .foo import Bar` until `foo.py` exists. See `backend/app/integrations/chef/__init__.py` for the correct pattern.

### Blog Post Update Path Missing Side Effects

**Symptom**: Blog post shows updated stats (100%) but timeline still shows old data (43.1%).

**Root cause**: `publishAndroidWorldBenchmarkPost` had an update path (for re-publishing same week) that updated the blog post but did NOT insert a timeline event. The create path had it; the update path didn't.

**Fix**: When adding side effects to idempotent Convex actions, always audit BOTH the create and update code paths. See `competitiveIntelligence.ts` lines 1185-1195.

### Chef Annotation Crashes (JSON.parse / Zod)

**Symptom**: UI render crashes, silent annotation drops, or `response.json()` failures.

**Three bugs, one fix pattern:**
1. **Unguarded `JSON.parse`** in `parseAnnotations()` — wrap in `try-catch`, `continue` on failure.
2. **Null provider/toolCallId** in `encodeModelAnnotation()` — default to `'Unknown'`/`'unknown'`, not `null`.
3. **Double `response.json()`** in `recordUsage()` — store `await response.json()` in a variable, add early return on error.

### Template Literals in Convex Actions

**Convention**: Use `\n` escape sequences (not multi-line template literals) for markdown content generated by Convex actions.

**Why**: Multi-line templates are harder to diff-review, auto-formatters change indentation, and concatenation with conditional variables (like `bonusLine`) is cleaner with escape sequences.

```typescript
// ✅ Good
const content = `# Title\n\nParagraph.\n`;

// ❌ Avoid in Convex actions
const content = `# Title

Paragraph.
`;
```

---

## 📋 Self-Maintenance Checklist

Before completing any task, verify:

- [ ] Code changes compile without errors
- [ ] All existing tests still pass
- [ ] New features have tests
- [ ] TypeScript has no type errors (`npm run build`)
- [ ] Python has no import errors
- [ ] Documentation updated if behavior changed
- [ ] **AGENTS.md updated if new workflow discovered**

---

## 🔗 Critical Documentation References

| Document | Purpose | When to Read |
|----------|---------|--------------|
| [README.md](./README.md) | Full project docs, API reference | Starting work |
| [AGENT_HANDOFF.md](./AGENT_HANDOFF.md) | In-progress work context | Resuming work |
| [FEATURE_SPEC_SHEET.md](./FEATURE_SPEC_SHEET.md) | Feature specifications | Adding features |
| [PROJECT_MASTER_DOCUMENT.md](./PROJECT_MASTER_DOCUMENT.md) | Architecture decisions | Major changes |

---

## 🤝 Parallel Agent Coordination Protocol

> Based on Anthropic's "Building a C Compiler with Parallel Claudes" (Feb 2026).
> Reference: https://www.anthropic.com/engineering/building-c-compiler

This section enables up to 4 AI agents to work on Project Countdown / retention.sh in parallel without conflicts.

### Task Locking Protocol

**Before starting any work**, claim your task to prevent duplicate effort:

1. Check `.parallel-agents/current_tasks/` for active claims.
2. Create a lock file: `.parallel-agents/current_tasks/<task_key>.lock`.
   - Content: `{ "agent": "<session_id>", "started": "<ISO timestamp>", "description": "<what you plan to do>" }`.
3. Do your work.
4. When done, delete the lock file and update `.parallel-agents/progress.md`.

**If using NodeBench MCP tools**: Use `claim_agent_task(taskKey="...")` and `release_agent_task(taskKey="...")`.

### Role Specialization

Recommended role assignments for parallel agents:

- **implementer** — Primary feature work.
- **test_writer** — Test coverage and edge cases.
- **code_quality_critic** — Refactoring and pattern enforcement.
- **documentation_maintainer** — Docs, progress files, READMEs.

### Oracle Testing Workflow

Use known-good reference outputs to validate changes:

1. **Capture oracle**: Run the reference implementation and save output.
2. **Compare**: After changes, run your implementation and diff against the golden file using `run_oracle_comparison`.
3. **Triage failures**: Each failing comparison is an independent work item.

### Context Budget Rules

- **DO NOT** print thousands of lines of test output — log to file, print summary only.
- **DO NOT** read entire large files — use targeted grep/search.
- **Budget guideline**: If a single tool output exceeds ~5,000 tokens, summarize it first. Use `log_context_budget`.

### Anti-Patterns to Avoid

- **Silent overwrites**: Always pull/rebase before pushing.
- **Stuck loops**: If stuck >30 minutes on one problem, mark as blocked and move on.
- **Scope creep**: Stay in your assigned role.

---

## 🏷️ Version

**Last Updated**: 2026-02-06
**Maintainers**: AI Agent (fully utilized via NodeBench-MCP)
**Compatible Tools**: Claude Code, Gemini, NodeBench-MCP
