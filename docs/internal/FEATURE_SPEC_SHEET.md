# AndroidWorld Benchmark Integration - Feature Spec Sheet

**Project:** Multi-Emulator Streaming with Mobile MCP & AI Agents v3
**Status:** ✅ PRODUCTION READY (agent_test_and_eval_v3)
**Last Updated:** 2026-01-19

---

## 📋 Executive Summary

Comprehensive integration of Google DeepMind's **AndroidWorld** benchmark suite with our Mobile MCP infrastructure, enabling automated testing of autonomous device agents across 39 tasks spanning 8+ real-world Android applications.

---

# 🔬 INDUSTRY GAP ANALYSIS & ROADMAP TO PARITY

## Research Sources (January 2026)
- **Anthropic**: Claude Sonnet 4.5, Computer Use (computer_20251124), MCP Code Execution Mode
- **OpenAI**: Operator, CUA (Computer-Using Agent), Agents SDK, Responses API
- **Google**: Gemini 2.0, Project Mariner, Project Astra
- **LangChain**: LangGraph, LangMem SDK, LangSmith
- **Manus AI**: Fully autonomous general AI agent
- **Cursor**: Agent mode, autonomous coding
- **BrowserStack**: Test Case Generator Agent (Dec 2025)
- **Academic**: AndroidWorld (ICLR 2025), AITW (NeurIPS 2023), SWE-bench, OSWorld, MobileAgentBench

---

## 🚨 CRITICAL GAPS (Immediate Priority)

### 1. Ground Truth & Evaluation Framework
| Current State | Industry Standard | Gap Severity |
|--------------|-------------------|--------------|
| Basic success/fail metrics | Programmatic state verification | 🔴 CRITICAL |
| No trajectory comparison | Expected vs actual action sequences | 🔴 CRITICAL |
| No state verification | DB/filesystem state checking | 🔴 CRITICAL |
| No ground truth dataset | Annotated expected outcomes | 🔴 CRITICAL |

**Industry Examples:**
- **AndroidWorld (ICLR 2025)**: Dynamic state verification via AndroidEnv
- **SWE-bench**: Test-based ground truth for code tasks
- **OSWorld**: Full OS state matching
- **MobileAgentBench**: Standardized mobile agent evaluation

**What's Needed:**
```python
# Ground Truth Verification Engine
class GroundTruthVerifier:
    async def verify_state(self, device_id: str, expected_state: Dict) -> VerificationResult:
        # Check Android state programmatically
        # Compare DB, files, UI elements, settings
        pass

    async def compare_trajectory(self, actual: List[Action], expected: List[Action]) -> float:
        # Action sequence similarity scoring
        pass

    async def llm_judge(self, task: str, outcome: str) -> JudgmentResult:
        # LLM-as-judge for subjective verification
        pass
```

### 2. Agent Memory & Context Management
| Current State | Industry Standard | Gap Severity |
|--------------|-------------------|--------------|
| No persistent memory | Long-term memory (LangMem, Mem0) | 🔴 CRITICAL |
| No context management | Session + persistent context | 🔴 CRITICAL |
| No learning from history | Pattern recognition from past runs | 🔴 CRITICAL |

**Industry Examples:**
- **LangMem SDK (Feb 2025)**: Agent long-term memory
- **AWS AgentCore**: Short-term + long-term memory deep dive
- **Mem0**: Production-ready memory platform
- **Letta**: Agent memory as context management

**What's Needed:**
```python
# Memory Management System
class AgentMemory:
    short_term: ConversationBuffer  # Current session
    long_term: VectorStore          # Past executions, patterns
    episodic: TrajectoryStore       # Past task trajectories
    semantic: KnowledgeGraph        # Domain knowledge

    async def recall_similar_tasks(self, task: AndroidWorldTask) -> List[PastExecution]:
        # RAG retrieval of similar past executions
        pass
```

### 3. Observability & Tracing
| Current State | Industry Standard | Gap Severity |
|--------------|-------------------|--------------|
| Basic logging | Structured traces (LangSmith, Phoenix) | 🟠 HIGH |
| No evaluation dashboard | Real-time metrics visualization | 🟠 HIGH |
| No debugging tools | Trace replay, step-through | 🟠 HIGH |

**Industry Examples:**
- **LangSmith**: Full agent tracing + evaluation
- **Arize Phoenix**: Open-source LLM tracing
- **OpenAI Agents SDK**: Built-in tracing
- **Langfuse**: OSS observability

---

## 🟠 MAJOR GAPS (High Priority)

### 4. Multi-Agent Orchestration
| Current State | Industry Standard | Gap Severity |
|--------------|-------------------|--------------|
| Single executor | Coordinator + specialized agents | 🟠 HIGH |
| No handoffs | Agent-to-agent delegation | 🟠 HIGH |
| No dynamic routing | Task-based agent selection | 🟠 HIGH |

**Industry Examples:**
- **OpenAI Agents SDK**: Handoffs, multi-agent workflows
- **LangGraph**: State graphs, checkpointing, parallel execution
- **Anthropic MCP**: Code execution for 98.7% token reduction
- **Manus AI**: Fully autonomous async cloud execution

**Architecture Needed:**
```
┌─────────────────────────────────────────────────────────────┐
│                     COORDINATOR AGENT                        │
│  (Task decomposition, routing, result aggregation)          │
└─────────────────┬───────────────────┬───────────────────────┘
                  │                   │
    ┌─────────────▼───────┐  ┌────────▼────────────┐
    │   PLANNER AGENT     │  │   EXECUTOR AGENT    │
    │ (Strategy, steps)   │  │ (Device control)    │
    └─────────────────────┘  └─────────────────────┘
                  │                   │
    ┌─────────────▼───────┐  ┌────────▼────────────┐
    │   VERIFIER AGENT    │  │   REFLECTION AGENT  │
    │ (State checking)    │  │ (Error analysis)    │
    └─────────────────────┘  └─────────────────────┘
```

### 5. Self-Correction & Reflection Loop
| Current State | Industry Standard | Gap Severity |
|--------------|-------------------|--------------|
| Basic retry on failure | Reflection + retry with context | 🟠 HIGH |
| No self-assessment | Post-action verification | 🟠 HIGH |
| No error analysis | Root cause identification | 🟠 HIGH |

**Industry Examples:**
- **LangGraph Self-Correcting RAG**: Retry loops with feedback
- **OODA Loop Pattern**: Observe, Orient, Decide, Act
- **Reflection Agents**: Self-critique before final answer

**Pattern Needed:**
```python
class ReflectionLoop:
    async def execute_with_reflection(self, task: Task) -> Result:
        for attempt in range(max_retries):
            result = await self.execute(task)
            reflection = await self.reflect(task, result)

            if reflection.is_satisfactory:
                return result

            # Learn from failure and retry
            task = self.incorporate_feedback(task, reflection)

        return self.best_attempt()
```

### 6. Human-in-the-Loop (HITL)
| Current State | Industry Standard | Gap Severity |
|--------------|-------------------|--------------|
| No approval workflow | Interrupt + approve + modify | 🟠 HIGH |
| No intervention hooks | Pause at critical points | 🟠 HIGH |
| No confirmation UI | Approval dashboard | 🟡 MEDIUM |

**Industry Examples:**
- **LangGraph HITL**: Interrupt, approve, modify tool calls
- **AWS Bedrock Agents**: Human confirmation patterns
- **Permit.io**: HITL frameworks and best practices

---

## 🟡 SIGNIFICANT GAPS (Medium Priority)

### 7. Design-to-Test (Figma Integration)
| Current State | Industry Standard | Gap Severity |
|--------------|-------------------|--------------|
| PRD text parsing only | Figma file → test cases | 🟡 MEDIUM |
| No visual understanding | Component hierarchy extraction | 🟡 MEDIUM |
| No design tokens | Color, spacing, typography checks | 🟡 MEDIUM |

**Industry Examples:**
- **BrowserStack Test Case Generator (Dec 2025)**: Jira + PRD + Figma + screenshots → test cases
- **Figma API**: Design token extraction, component tree
- **Testsigma Generator Agent**: Visual design understanding

**What's Needed:**
```python
class FigmaIntegration:
    async def extract_from_figma(self, file_id: str) -> DesignSpec:
        # Figma API: get components, frames, interactions
        pass

    async def generate_tests_from_design(self, spec: DesignSpec) -> List[TestCase]:
        # Map UI components to test scenarios
        # Extract interaction flows
        # Generate accessibility checks
        pass
```

### 8. Unified Ticket Manager (Context Hub)
| Current State | Industry Standard | Gap Severity |
|--------------|-------------------|--------------|
| No centralized context | Unified PRD + Figma + tickets | 🟡 MEDIUM |
| No WYSIWYG editor | Rich markdown authoring | 🟡 MEDIUM |
| No issue tracker sync | Jira/Linear integration | 🟡 MEDIUM |

**Vision:**
```
┌────────────────────────────────────────────────────────────┐
│                   UNIFIED CONTEXT HUB                       │
├────────────────────────────────────────────────────────────┤
│  📄 PRD Documents     │  🎨 Figma Designs                  │
│  🎫 Jira/Linear       │  💬 Slack Conversations            │
│  📸 Screenshots       │  🧪 Test Results                   │
├────────────────────────────────────────────────────────────┤
│                  WYSIWYG MARKDOWN EDITOR                   │
│  - Rich text editing                                       │
│  - Embedded media                                          │
│  - Test case templates                                     │
│  - AI-assisted authoring                                   │
├────────────────────────────────────────────────────────────┤
│                    AI EXTRACTION ENGINE                    │
│  PRD → User Stories → Test Cases → Golden Bugs            │
└────────────────────────────────────────────────────────────┘
```

---

## 📊 OPEN SOURCE DATASETS GAP

### Currently Implemented
| Dataset | Coverage | Status |
|---------|----------|--------|
| AndroidWorld | 39/116 tasks (34%) | 🟡 Partial |

### Missing Datasets
| Dataset | Scale | Source | Priority |
|---------|-------|--------|----------|
| **AndroidWorld Full** | 116 tasks, 20 apps | ICLR 2025 | 🔴 HIGH |
| **AITW** | 30K+ tasks | NeurIPS 2023 | 🟠 MEDIUM |
| **MobileAgentBench** | Standardized eval | 2025 | 🟠 MEDIUM |
| **Rico** | 72K screenshots, 9.7K apps | UI Understanding | 🟡 LOW |
| **AndroidLab** | Systematic benchmarks | ACL 2025 | 🟡 LOW |
| **OSWorld** | Cross-OS tasks | 2024 | 🟡 LOW |

---

## 🛠️ IMPLEMENTATION ROADMAP

### Phase 1: Critical Foundations (4-6 weeks)
| Task | Effort | Impact | Dependencies |
|------|--------|--------|--------------|
| Ground Truth Verification Engine | HIGH | CRITICAL | None |
| State-based outcome checking | HIGH | CRITICAL | AndroidEnv |
| Trajectory recording & comparison | MEDIUM | HIGH | None |
| LangSmith/Phoenix integration | MEDIUM | HIGH | None |
| Short-term memory (session) | MEDIUM | HIGH | None |
| Long-term memory (RAG) | HIGH | HIGH | Vector DB |

### Phase 2: Orchestration Upgrade (4-6 weeks)
| Task | Effort | Impact | Dependencies |
|------|--------|--------|--------------|
| Coordinator agent pattern | HIGH | HIGH | Phase 1 |
| Planner/Executor/Verifier split | HIGH | HIGH | Phase 1 |
| Agent handoff protocol | MEDIUM | HIGH | Coordinator |
| Reflection loop | MEDIUM | HIGH | Verifier |
| Self-correction with context | MEDIUM | HIGH | Memory |
| HITL checkpoints | MEDIUM | MEDIUM | Coordinator |

### Phase 3: Input Sources (3-4 weeks)
| Task | Effort | Impact | Dependencies |
|------|--------|--------|--------------|
| Figma API client | MEDIUM | HIGH | None |
| Design token extraction | MEDIUM | HIGH | Figma client |
| Component → test mapping | HIGH | HIGH | Test generator |
| WYSIWYG markdown editor | MEDIUM | MEDIUM | Frontend |
| Jira/Linear integration | MEDIUM | MEDIUM | API access |

### Phase 4: Dataset Expansion (2-3 weeks)
| Task | Effort | Impact | Dependencies |
|------|--------|--------|--------------|
| Full AndroidWorld (116 tasks) | MEDIUM | MEDIUM | Executor |
| AITW integration | HIGH | MEDIUM | Data pipeline |
| MobileAgentBench | MEDIUM | MEDIUM | Benchmark harness |

---

## 📈 EFFORT SUMMARY

| Phase | Duration | FTE Required | Key Deliverables |
|-------|----------|--------------|------------------|
| Phase 1 | 4-6 weeks | 2-3 | Ground truth, observability, memory |
| Phase 2 | 4-6 weeks | 2-3 | Multi-agent, reflection, HITL |
| Phase 3 | 3-4 weeks | 1-2 | Figma, ticket manager |
| Phase 4 | 2-3 weeks | 1 | Dataset expansion |
| **TOTAL** | **13-19 weeks** | **2-3 avg** | **Full industry parity** |

---

## 🎯 QUICK WINS (< 1 week each)

1. **Add LangSmith tracing** - Immediate observability
2. **Implement basic trajectory logging** - Foundation for ground truth
3. **Add retry with reflection prompt** - Better error recovery
4. **Expand to 50 AndroidWorld tasks** - More coverage
5. **Add screenshot comparison** - Visual regression detection

---

---

## 🎯 Core Features

### 1. **Task Registry & Parameterization**
- **39 Implemented Tasks** across 8+ apps (Contacts, Clock, Camera, Settings, Markor, Calendar, Expense Tracker, Recipe Manager)
- **Dynamic Parameter Generation** - Random values for realistic task variation
- **Task Difficulty Levels** - EASY, MEDIUM, HARD classifications
- **Task Categories** - DATA_ENTRY, DATA_EDIT, SCREEN_READING, MULTI_APP, COMPLEX_UI, SEARCH, TRANSCRIPTION, PARAMETERIZED
- **Optimal Step Tracking** - Expected action count per task for performance benchmarking

### 2. **Execution Engine**
- **15+ Execution Strategies** - Specialized logic for different task types
- **Parallel Execution** - Run benchmarks across multiple emulators simultaneously
- **Task Status Tracking** - PENDING, RUNNING, SUCCESS, FAILED, TIMEOUT states
- **Screenshot Capture** - Base64-encoded images for visual verification
- **Action Logging** - Detailed action sequences with timestamps
- **Timeout Handling** - Configurable max steps per task (default: 20)

### 3. **PRD Ingestion Pipeline**
- **User Story Extraction** - Parse "As a... I want..." patterns from PRD text
- **Test Case Generation** - Convert user stories → test scenarios → AndroidWorld tasks
- **Golden Bug Creation** - Auto-generate test cases with priority mapping
- **Acceptance Criteria Mapping** - Link PRD requirements to executable tests
- **Metadata Tagging** - Track source PRD, category, app, and priority

### 4. **Test Case Generator**
- **Pattern-Based Extraction** - Regex parsing for user stories and acceptance criteria
- **App Mapping** - Intelligent mapping of keywords to Android packages
- **Category Classification** - Auto-detect task type (data_entry, screen_reading, multi_app, complex_ui)
- **Scenario Generation** - BDD-style Given/When/Then format
- **Test Step Conversion** - Transform scenarios into executable action sequences

### 5. **Metrics & Reporting**
- **Success Rate Calculation** - Completed tasks / total tasks
- **Duration Tracking** - Per-task and aggregate execution time
- **Failure Analysis** - Error messages and timeout tracking
- **Benchmark Results** - Comprehensive JSON output with task-level details
- **Performance Insights** - Steps taken vs. optimal steps comparison

---

## 🏗️ Architecture

```
backend/app/benchmarks/android_world/
├── task_registry.py          # Task definitions & parameterization
├── executor.py               # Task execution engine & strategies
├── test_generator.py         # PRD → Test case conversion
├── poc_runner.py             # Proof-of-concept CLI runner
└── __init__.py               # Public API exports

backend/app/benchmarks/
├── prd_ingestion.py          # PRD processing pipeline
├── comprehensive_test.py      # Integration test suite
└── __init__.py               # Benchmark module exports
```

---

## 🔌 Integration Points

- **Mobile MCP Client** - Device control via Model Context Protocol
- **Device Fleet** - Multi-emulator orchestration
- **LLM Agent** - Task interpretation and action generation
- **Storage** - Golden bug persistence and retrieval

---

## 📊 Task Coverage

| Category | Count | Examples |
|----------|-------|----------|
| **Data Entry** | 8 | ContactsAddContact, MarkorCreateNote, CalendarAddEvent |
| **Screen Reading** | 6 | CameraViewPhotos, SettingsCheckBluetooth |
| **Multi-App** | 4 | MultiAppBrowserToNotes, MultiAppPhotosToShare |
| **Complex UI** | 5 | BrowserSearchGoogle, SettingsChangeWifi |
| **System Control** | 8 | SystemBluetoothTurnOn, SystemWifiToggle |
| **Other** | 8 | ClockStopWatchRunning, CameraTakePhoto |

---

## ✨ Key Capabilities

✅ Automated task execution on real/emulated Android devices  
✅ Parallel benchmark runs across device fleet  
✅ PRD-driven test generation with golden bugs  
✅ Parameterized tasks for realistic variation  
✅ Comprehensive metrics and failure analysis  
✅ Screenshot-based visual verification  
✅ Timeout and error handling  
✅ JSON-serializable results for reporting  

---

## 🚀 Usage Examples

### Run POC Benchmark
```bash
python -m app.benchmarks.android_world.poc_runner \
  --devices emulator-5554 emulator-5555 \
  --tasks ClockStopWatchRunning OpenAppTaskEval SystemBluetoothTurnOn
```

### Ingest PRD & Generate Tests
```python
processor = PRDProcessor()
result = await processor.ingest_prd(prd_text, "CAL-PRD-001")
# Returns: user_stories, test_cases, golden_bugs, summary
```

### Execute Single Task
```python
executor = AndroidWorldExecutor(mcp_client)
result = await executor.execute_task(task, device_id="emulator-5554")
# Returns: TaskExecutionResult with status, actions, screenshots
```

---

## 📈 Metrics Output

```json
{
  "total_tasks": 3,
  "completed_tasks": 2,
  "failed_tasks": 1,
  "success_rate": "66.7%",
  "total_duration_seconds": 45.2,
  "task_results": [
    {
      "task_name": "ClockStopWatchRunning",
      "status": "success",
      "duration_seconds": 12.5,
      "steps_taken": 3
    }
  ]
}
```

---

## 🔮 Future Enhancements

- [ ] Full LLM-based task interpretation (currently rule-based)
- [ ] Additional benchmark datasets (AITW, Rico)
- [ ] Advanced failure recovery strategies
- [ ] Real device support (beyond emulators)
- [ ] Performance optimization for large-scale benchmarks
- [ ] Custom task definition UI

