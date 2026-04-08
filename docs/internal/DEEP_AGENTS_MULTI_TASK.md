# Deep Agents + Multi-Task Execution

## Overview

This system implements the **Deep Agents** pattern (inspired by [LangChain's Deep Agents blog post](https://blog.langchain.com/deep-agents/)) combined with **multi-task multi-device execution** for complex mobile test automation.

---

## Deep Agents Pattern Implementation

### 1. ✅ Detailed System Prompt

**Location:** `backend/app/agents/coordinator/coordinator_instructions.py`

- Comprehensive instructions for the coordinator agent
- Detailed delegation examples and use cases
- Clear guidelines for when to use each specialist agent
- Planning tool usage instructions

### 2. ✅ Planning Tool (No-op for Context Engineering)

**Location:** `backend/app/agents/coordinator/coordinator_agent.py`

```python
def plan_task(task_description: str, subtasks: list[str]) -> str:
    """
    Planning tool for breaking down complex tasks into subtasks.
    
    Deep Agent Pattern: This is a no-op tool (like Claude Code's Todo list) that helps
    the agent maintain focus and plan over longer time horizons.
    """
```

**Purpose:**
- Helps agent maintain focus on complex tasks
- Breaks down multi-step requests into manageable subtasks
- Provides context engineering for longer time horizons
- Does NOT execute anything - purely for planning

**Example Usage:**
```
User: "Test login on 5 devices with different scenarios"

Agent calls:
plan_task(
    task_description="Test login on 5 devices with different scenarios",
    subtasks=[
        "Launch 5 emulators",
        "Assign login_test to devices 1-3",
        "Assign feed_scrolling to devices 4-5",
        "Start multi-task simulation",
        "Monitor results and report"
    ]
)
```

### 3. ✅ Sub Agents (Hierarchical Multi-Agent)

**Architecture:**
```
Coordinator Agent
├── Search Assistant (bug reports, test scenarios)
├── Test Generation Specialist (test generation, analysis)
└── Device Testing Specialist (execution, bug reproduction, exploration, autonomous navigation)
```

**Delegation Pattern:**
- Coordinator analyzes user intent
- Routes to appropriate specialist
- Specialists have deep domain expertise
- Each specialist can spawn further sub-tasks

### 4. ✅ File System (Agent Sessions Storage)

**Location:** `backend/app/agents/coordinator/coordinator_service.py`

- Agent sessions are persisted in `self.sessions`
- Simulation results stored in `self.simulations`
- Device streams tracked in real-time
- Session memory for context retention across conversations

---

## Multi-Task Multi-Device Execution

### Architecture

**Two Execution Modes:**

#### 1. Single Task Mode (Backward Compatible)
Run the **same task** on **all devices**:

```json
{
  "task_name": "feed_scrolling",
  "device_ids": ["emulator-5554", "emulator-5556", "emulator-5558"],
  "max_concurrent": 3
}
```

#### 2. Multi-Task Mode (NEW - Deep Agent Pattern)
Run **different tasks** on **different devices**:

```json
{
  "device_tasks": [
    {"device_id": "emulator-5554", "task_name": "feed_scrolling"},
    {"device_id": "emulator-5556", "task_name": "login_test"},
    {"device_id": "emulator-5558", "task_name": "search_test"}
  ],
  "max_concurrent": 3
}
```

### Backend Implementation

**API Endpoint:** `POST /api/ai-agent/execute-simulation`

**Service Methods:**
- `execute_simulation()` - Single task mode
- `execute_multi_task_simulation()` - Multi-task mode (NEW)
- `_execute_device_tasks()` - Unified execution engine

**Key Features:**
- Semaphore-based concurrency control (`asyncio.Semaphore`)
- Per-device task assignment
- Real-time WebSocket streaming
- Independent task execution per device
- Shared execution logic for both modes

### Frontend Implementation

**Location:** `frontend/test-studio/src/pages/MultiDeviceSimulationPage.tsx`

**UI Components:**

1. **Task Mode Selector:**
   - Single Task (All Devices) - Same task for all
   - Multi-Task (Per Device) - Different tasks per device

2. **Single Task Mode:**
   - Global task selector (dropdown)
   - All devices run the same task

3. **Multi-Task Mode:**
   - Per-device task selectors
   - Each device card shows its assigned task
   - Task assignments persist in `deviceTasks` Map

**Device Card Display:**
```
┌─────────────────────────────┐
│ emulator-5554          [✓]  │
│ Task: Feed Scrolling        │
│ ┌─────────────────────────┐ │
│ │ Task: [Feed Scrolling ▼]│ │
│ └─────────────────────────┘ │
└─────────────────────────────┘
```

### Usage Examples

**Example 1: Complex Multi-Device Testing**
```
User: "Run feed scrolling on 3 devices and login test on 2 devices"

Agent:
1. Calls plan_task() to break down the request
2. Calls launch_emulators(count=5) if needed
3. Delegates to Device Testing Specialist with device_tasks:
   [
     {device_id: "emulator-5554", task_name: "feed_scrolling"},
     {device_id: "emulator-5556", task_name: "feed_scrolling"},
     {device_id: "emulator-5558", task_name: "feed_scrolling"},
     {device_id: "emulator-5560", task_name: "login_test"},
     {device_id: "emulator-5562", task_name: "login_test"}
   ]
```

**Example 2: UI-Based Multi-Task**
1. Navigate to http://localhost:5173/multi-device
2. Select "Multi-Task (Per Device)" mode
3. Select 3 devices
4. Assign tasks:
   - Device 1: Feed Scrolling
   - Device 2: Login Test
   - Device 3: Search Test
5. Click "Start Simulation"
6. Watch real-time execution with different tasks per device

---

## Benefits of Deep Agents + Multi-Task

1. **Complex Task Planning** - Break down multi-step requests
2. **Parallel Execution** - Run different tasks simultaneously
3. **Resource Optimization** - Use devices efficiently
4. **Flexible Testing** - Mix and match test scenarios
5. **Long-Running Tasks** - Maintain context over extended periods
6. **Autonomous Navigation** - Goal-driven device control
7. **Real-Time Monitoring** - WebSocket streaming for all devices

---

## Technical Details

**Concurrency Model:**
- `asyncio.Semaphore(max_concurrent)` limits parallel executions
- `asyncio.gather()` waits for all device tasks
- Per-device session isolation
- Independent WebSocket streams

**Task Execution Flow:**
```
1. User request → Coordinator
2. Coordinator calls plan_task() (if complex)
3. Coordinator delegates to Device Testing Specialist
4. Device Testing creates sessions for each device
5. Tasks execute in parallel (semaphore-controlled)
6. Real-time updates via WebSocket
7. Results aggregated and displayed
```

**Error Handling:**
- Per-device error tracking
- Partial success support (some devices succeed, others fail)
- Retry failed devices independently
- Error messages displayed on device cards

---

## Future Enhancements

1. **Dynamic Task Assignment** - AI-driven task distribution
2. **Task Dependencies** - Sequential task chains per device
3. **Resource-Based Scheduling** - Assign tasks based on device capabilities
4. **Cross-Device Coordination** - Tasks that require multiple devices
5. **Adaptive Concurrency** - Auto-adjust based on system load


