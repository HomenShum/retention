/**
 * retention.sh methodology knowledge base.
 */

export const METHODOLOGY_TOPICS: Record<string, string> = {
   overview: `# retention.sh Methodologies — Overview

Available topics: oavr, som_annotation, coordinate_scaling, agent_config, flicker_detection, golden_bugs, mobile_mcp_fallback, vision_click, failure_diagnosis, self_correction, model_tiering, simulation_lifecycle, subagent_handoff, boolean_verification, hud_streaming, figma_pipeline

## Architecture
- **Backend**: FastAPI (Python 3.11+) at backend/
- **Frontend**: React + TypeScript + Vite at frontend/test-studio/
- **Mobile MCP**: Model Context Protocol for Android emulator control
- **AI Agents**: Hierarchical multi-agent (Coordinator → Specialists)
- **Models**: GPT-5.4 (coordinator, vision)
- **Streaming**: SSE for AI chat, WebSocket for emulator frames`,

   oavr: `# OAVR Pattern — Observe-Act-Verify-Reason

The device testing agent uses OAVR for autonomous navigation:

1. **Observe** → Screen Classifier Agent analyzes current screen state
2. **Act** → Execute action (click, swipe, type) via Mobile MCP
3. **Verify** → Action Verifier confirms the action succeeded
4. **Reason** → Failure Diagnosis suggests recovery if verification failed

Key file: agents/device_testing/subagents/README.md`,

   som_annotation: `# Set-of-Mark (SoM) Screenshot Annotation

Based on OmniParser's SoM approach — color-coded, type-aware bounding boxes.

## 9-Color Element Type Palette
| Type      | Color       | Tag    | Example Classes                   |
|-----------|-------------|--------|-----------------------------------|
| button    | Dodger blue | BTN    | Button, ImageButton, FAB          |
| input     | Orange      | INPUT  | EditText, SearchView              |
| toggle    | Purple      | TOGGLE | Switch, CheckBox, RadioButton     |
| nav       | Deep pink   | NAV    | BottomNavigationView, Toolbar     |
| image     | Dark cyan   | IMG    | ImageView                         |
| text      | Gray        | TXT    | TextView                          |
| list      | Forest green| LIST   | RecyclerView, ListView            |
| container | Dark gray   | BOX    | FrameLayout, LinearLayout         |
| unknown   | Green       | ELEM   | Unclassified elements             |

Key file: agents/device_testing/tools/autonomous_navigation_tools.py`,

   coordinate_scaling: `# Coordinate Scaling — Screenshot vs Device Resolution

Mobile MCP take_screenshot returns JPEG images scaled to ~45% of native resolution.

| Layer              | Resolution  | Source                           |
|--------------------|-------------|----------------------------------|
| Device screen      | 1080×2400   | Native resolution                |
| Screenshot image   | 486×1080    | Mobile MCP compresses to JPEG    |
| Element coordinates| 1080×2400   | list_elements_on_screen (native) |

## Implementation Details
1. **Parse Resolution**: Get screen size via get_screen_size() and parse with regex.
2. **Scaling Logic**: scale_x = img.width / screen_width.

Key file: autonomous_navigation_tools.py lines 397-595`,

   flicker_detection: `# Flicker Detection Pipeline — 4-Layer Architecture

Detects screen flickers too fast for periodic screenshots (16-200ms).

1. **Layer 1 (Trigger)**: adb shell screenrecord --time-limit 10.
2. **Layer 2 (Extraction)**: ffmpeg scene filtering (select='gt(scene,0.003)').
3. **Layer 3 (Analysis)**: SSIM calculated between consecutive pairs.
4. **Layer 4 (LLM)**: GPT-5.4 Vision verification.

Key file: agents/device_testing/flicker_detection_service.py`,

   golden_bugs: `# Golden Bug Evaluation Pipeline

A two-stage deterministic evaluation system for measuring agent reliability.

## 1. Pre-Device Planning (LLM Judge)
- **Static Checks**: Verifies device_id, app_package, and steps are present.
- **LLM Judge**: GPT-5-mini reviews the plan for logical consistency.

## 2. On-Device Execution
- **Reproduction**: Agent attempts task up to 3 times.
- **Verification**: AI analyzes screen state to confirm goal achievement.

Key file: agents/device_testing/golden_bug_service.py`,

   failure_diagnosis: `# Failure Taxonomy & Diagnosis (OAVR "Reason")

When Action Verifier fails, the Failure Diagnosis Specialist classifies the error.

## Failure Taxonomy
1. **PLANNING_ERROR**: Wrong action for current state.
2. **PERCEPTION_ERROR**: Misinterpreted UI (e.g., empty element list).
3. **ENVIRONMENT_ERROR**: App crash, OS dialog, or network timeout.
4. **EXECUTION_ERROR**: Action failed despite element presence.

Key file: agents/device_testing/subagents/failure_diagnosis_agent.py`,

   model_tiering: `# 2026 Model Tiering Standard

Model selection is strictly tiered by "Thinking Budget":

1. **Thinking Tier (GPT-5.4)**: Orchestration (Coordinator), Complex Reasoning, Test Generation.
2. **Core Tier (GPT-5-mini)**: Routing, Classification (OAVR), Planning.
3. **Utility Tier (GPT-5-nano)**: MCP tool calls, data distillation (JSON cleaning).

Key file: backend/app/agents/model_fallback.py`,

   mobile_mcp_fallback: `# Mobile MCP v0.0.36 ADB Fallback

Mobile MCP has a critical bug where it fails device detection if *any* device is offline.

## Workaround Logic
Comprehensive ADB bridge fallback for:
- **Launching**: am start -n with known activity mapping.
- **UI Dump**: uiautomator dump /dev/tty (direct to stdout for speed).
- **Screenshots**: exec-out screencap -p (fast PNG capture).

Key file: agents/device_testing/mobile_mcp_client.py`,

   simulation_lifecycle: `# Simulation Lifecycle & Safety

Managing parallel device executions at scale.

## Safety Controls
- **Concurrency**: asyncio.Semaphore(max_concurrent) limits active emulators.
- **Thread Safety**: Per-simulation asyncio.Lock ensures serial result indexing.
- **Retention**: Max 24h age or 100 total simulations before auto-purge.

Key file: agents/coordinator/coordinator_service.py`,

   subagent_handoff: `# Subagent Handoff Protocol

How the Coordinator orchestrates specialist subagents without context loss.

## The Chain of Custody
1. **Perceptor (Screen Classifier)**: Analyzes UI and returns a structured screen_state.
2. **Planner (Device Agent)**: Proposes an action based on the state.
3. **Guardrail (Action Verifier)**: Receives the action, screen_state, and task_goal. Returns boolean approval.
4. **Actor (Mobile MCP)**: Executes the approved action.
5. **Doctor (Failure Diagnosis)**: Only triggered if execution fails.

## Memory Strategy
We avoid passing giant raw XML. Instead, the Classifier distills UI into **TOON** elements, which are then carried through the Verifier/Diagnosis steps to save tokens.

Key file: agents/device_testing/device_testing_agent.py`,

   boolean_verification: `# Boolean Verification vs. Numerical Scoring

Based on the V-Droid approach (arxiv.org/html/2503.15937v4).

## The Three Checks
Every action must pass three binary checks:
1. **is_safe**: Does this action cause data loss or unauthorized access?
2. **is_relevant**: Does this move the needle on the task goal?
3. **is_executable**: Can the target realistically be clicked/typed on?

Logic: approved = (is_safe AND is_relevant AND is_executable).
If any check is NO, the agent must propose an alternative_action.

Key file: agents/device_testing/subagents/action_verifier_agent.py`,

   hud_streaming: `# Real-Time HUD Observation Pipeline

How we achieve <200ms lag between agent thought and UI rendering.

## The on_step Callback
The UnifiedBugReproductionService accepts an on_step async callback.
1. **Capture**: Screenshot saved.
2. **Signal**: Service emits a tool_call event via FastAPI SSE/WebSocket.
3. **Render**: Frontend React components update instantly.

## Parallel HUDs
Each device runs in its own asyncio.Task, allowing Frontend to display multiple live streams simultaneously, each with its own independent thinking drawer.

Key files: agents/coordinator/coordinator_service.py, api/device_simulation.py`,

   agent_config: `# Agent Configuration Patterns

## Coordinator Agent (GPT-5.4)
- parallel_tool_calls=True (orchestration tasks can be parallel)
- reasoning=Reasoning(effort="high")

## Device Testing Agent (GPT-5-mini)
- parallel_tool_calls=False ← CRITICAL

Sequential execution ensures session stability.

Key file: agents/device_testing/device_testing_agent.py`,

   vision_click: `# Vision Click — Agentic Vision (GPT-5.4)

When the accessibility tree (list_elements_on_screen) is insufficient — canvas-based UIs, loading states, or custom views — we use GPT-5.4 with code execution to find elements visually.

## Two-Layer Architecture
- **Layer 1**: SoM Structural Annotation (deterministic, <100ms, free) — accessibility tree → element classification → color-coded bounding boxes
- **Layer 2**: GPT-5.4 Agentic Vision (intelligent, Think-Act-Observe) — SoM-annotated image + element list → GPT-5.4 generates Python code → LocalCodeExecutor runs it → results fed back

## Workflow
1. Take screenshot via Mobile MCP
2. Get screen size for coordinate mapping
3. Call AgenticVisionClient.multi_step_vision() with image + query
4. GPT-5.4 Think-Act-Observe loop: analyze image → generate Python code → execute locally → feed results back
5. Parse COORDINATES: (x, y) from final analysis
6. Execute click at found coordinates

Key file: agents/device_testing/agentic_vision_service.py (847 lines)`,

   figma_pipeline: `# Figma Flow Analysis — 3-Phase Pipeline

## Phase 1: Extract (Figma REST API, depth=3)
DOC → CANVAS → SECTION → FRAME tree traversal. CRITICAL: depth=3 not depth=2 — depth=2 only gets SECTION nodes, missing FRAMEs inside them.

## Phase 2: Cluster (Multi-Signal Priority Cascade)
Tries each signal in order, uses first that produces ≥2 groups:
1. **Section-Based** (highest priority) — group by section_name
2. **Prototype Connections** — Union-Find on transitionNodeID links
3. **Name-Prefix Matching** — split by " / ", " - ", " — " separators
4. **Spatial Clustering** (lowest) — Y-binning + X-gap splitting

## Phase 3: Visualize (PIL Overlay)
12 distinct colors, semi-transparent fill (alpha=40), strong outline (alpha=200), group labels.

## CV Overlay Fallback (No API)
When Figma Images API is rate-limited (429 with Retry-After: 396156 = 4.6 days):
- Brightness thresholding (>80 for sections, >100 for frames)
- Morphological closing/opening (scipy.ndimage) to bridge gaps
- Connected component analysis for section groups
- Column brightness profiling for sub-frame detection

Key files: app/figma/flow_analyzer.py (707 lines), scripts/figma_cv_overlay.py (162 lines)`,

   self_correction: `# Self-Correction Protocol (Ralph Loop)

When verification fails:
1. **Read the error** — Understand what broke
2. **Diagnose root cause** — Don't just patch symptoms
3. **Fix systematically** — Update code, tests, and docs together
4. **Re-verify** — Run full verification again
5. **Iterate** — Repeat until green

## Verification Commands
- Backend: pytest --tb=short
- Frontend: npm run build && npm run lint
- E2E: npx playwright test

Key principle: NEVER commit without running verification.`,
};

export const METHODOLOGY_TOPIC_LIST = Object.keys(METHODOLOGY_TOPICS);
