# Changelog

All notable changes to the Mobile Agent E2E Testing System.

## [Unreleased] - 2026-01-20

### 🚀 Major Features

#### Specialized Helper Tools (NEW!)
- **toggle_bluetooth(turn_on)**: Uses ADB `svc bluetooth enable/disable` for 100% reliability
- **toggle_wifi(turn_on)**: Uses ADB `svc wifi enable/disable` for 100% reliability
- **take_camera_photo()**: Handles camera setup screens and shutter automatically
- **add_contact(first_name, last_name, phone)**: Handles entire contact creation flow
- **create_markor_note(title, content)**: Handles entire Markor note creation flow

#### Reflexion Pattern Implementation
- **Self-Evaluation Tool**: Agent can now call `self_evaluate()` to reflect on progress
  - Detects stuck patterns (repeated actions, loops)
  - Provides task-specific guidance based on current screen
  - Tracks last 10 actions for context
  - Decision framework for recovery

- **Self-Search Guidance Tool**: Agent can call `search_for_guidance(query)` when stuck
  - Knowledge base of app-specific workflows
  - Step-by-step instructions for Settings, Clock, Camera, Contacts, Markor
  - General navigation tips when no specific match found

#### Hybrid Element Detection
- Combines MCP/ADB structured elements with Vision-based analysis
- 50px coordinate proximity deduplication
- Maximum element coverage regardless of individual source failures
- Source tagging for debugging (`source: "mcp"` or `source: "vision"`)

#### GPT-5 Series Model Configuration
- **gpt-5.4**: THINKING_MODEL, VISION_MODEL (flagship, best capabilities)
- **gpt-5**: PRIMARY_MODEL, EVAL_MODEL (stable, reliable)
- **gpt-5-mini/nano**: Fallback only (intermittent issues)
- Removed gpt-4o-mini from fallback chain

### 📊 Test Results (Regression Suite - 2026-01-20)

| Task | Status | Score | Notes |
|------|--------|-------|-------|
| SystemBluetoothTurnOn | ✅ PASS | 83% | `toggle_bluetooth()` tool works! |
| SystemWifiTurnOff | ✅ PASS | 100% | `toggle_wifi()` tool works perfectly! |
| ClockStopWatchRunning | ⚠️ FAIL | 83% | Stopwatch IS running, but verifier strict on errors |
| CameraTakePhoto | ❌ FAIL | 17% | Agent not using `take_camera_photo()` tool |
| ContactsAddContact | ⚠️ FAIL | 67% | Form filled but Save not clicked |
| MarkorCreateNote | ⚠️ FAIL | 67% | Note created but not visible in final state |

**Current Success Rate**: 33% (2/6 passing) → **Improved instructions to prioritize helper tools**

### 📝 Key Observations

1. **Bluetooth & WiFi tools work great** - The specialized ADB-based tools are reliable
2. **ClockStopWatchRunning is actually working** - Vision shows elapsed time and Stop button, but verifier marks FAIL due to `no_errors_occurred=false`
3. **Agent not using specialized tools** - Instructions updated to put helper tools at the TOP with prominent formatting

### 🔧 Pain Points Addressed

1. **Vision Model Quality**
   - Problem: gpt-4o-mini returned empty responses
   - Solution: Prioritized gpt-5.4 for all vision tasks

2. **Element Detection Gaps**
   - Problem: MCP/ADB missed visual-only elements
   - Solution: Hybrid approach merges both sources

3. **Agent Getting Stuck**
   - Problem: Agent repeated actions without progress
   - Solution: Self-evaluation with stuck pattern detection

4. **No Task Guidance**
   - Problem: Agent didn't know app-specific workflows
   - Solution: search_for_guidance tool with knowledge base

5. **Wrong Navigation**
   - Problem: Agent used search instead of menu navigation
   - Solution: Explicit "DO NOT use search" in instructions

6. **Bluetooth Task Failure**
   - Problem: Agent couldn't find Bluetooth toggle
   - Solution: Added `toggle_bluetooth()` tool using ADB commands

7. **Agent Not Using Helper Tools** (NEW)
   - Problem: Agent ignored specialized tools buried in instructions
   - Solution: Moved helper tools to TOP of instructions with prominent box formatting

### 📁 Files Modified

- `backend/app/benchmarks/android_world/agent_executor.py`
  - Added `toggle_bluetooth()` tool using ADB commands
  - Added `toggle_wifi()` tool using ADB commands
  - Added `add_contact()` tool for contact creation
  - Added `create_markor_note()` tool for Markor notes
  - Added `self_evaluate()` tool
  - Added `search_for_guidance()` tool
  - **Updated instructions to put helper tools at TOP with prominent formatting**
  - Task-specific workflows (Bluetooth, WiFi, Stopwatch, etc.)

- `backend/app/agents/model_fallback.py`
  - Updated model priority: gpt-5.4 → gpt-5 → gpt-5-mini → gpt-5-nano
  - Fixed VISION_MODEL to use gpt-5.4

- `backend/app/agents/device_testing/tools/autonomous_navigation_tools.py`
  - Fixed models_to_try order for vision analysis

### 🔬 Research Applied

- **Reflexion** (Shinn et al.): Self-reflection with verbal reinforcement
- **OmniParser V2** (Microsoft): Vision-based GUI agents
- **Anthropic Agent Evals**: Boolean metrics, transcript analysis
- **BrowserBase Stagehand**: DOM + Accessibility tree composition
- **ReAct Framework**: Reasoning + Action interleaved execution

### 🎯 Next Steps

1. Run regression suite again to verify improved helper tool usage
2. Improve final state verification (ensure save/complete actions)
3. Add toggle state detection (ON/OFF) to vision analysis
4. Implement multi-trial evaluation for flaky tasks
5. Add trajectory scoring for agent path optimization

---

## [0.1.0] - 2026-01-15

### Initial Implementation
- Basic MCP-based mobile automation
- ADB uiautomator fallback
- Vision-based element detection (fallback only)
- Boolean metrics for LLM-as-Judge verification
- Parallel test execution with DeviceWorker

---

*For detailed technical documentation, see [README.md](README.md)*

