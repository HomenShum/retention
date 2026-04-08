# Multi-Device Concurrent Simulation System

## 🎯 Overview

A comprehensive system for running multiple test tasks on multiple devices concurrently with real-time streaming visualization.

## 🏗️ Architecture

### Backend Components

#### 1. API Endpoints (`backend/app/api/ai_agent.py`)

**POST `/api/ai-agent/execute-simulation`**
- Starts a multi-device simulation
- Parameters:
  - `task_name`: Name of the test task to execute
  - `device_ids`: List of device IDs to run on
  - `max_concurrent`: Maximum concurrent executions (default: 5)
- Returns: `simulation_id` for tracking

**GET `/api/ai-agent/simulation/{simulation_id}/status`**
- Get current status of a running simulation
- Returns: SimulationStatus with progress and results

**WebSocket `/ws/ai-agent/simulation/{simulation_id}`**
- Real-time simulation status updates
- Sends updates every 1 second
- Includes per-device results and overall progress

#### 2. Coordinator Service (`backend/app/agents/coordinator/coordinator_service.py`)

**`execute_simulation()` Method**
- Creates simulation with unique ID
- Initializes SimulationStatus tracking
- Launches background task for execution

**`_run_simulation()` Method**
- Semaphore-based concurrency control (`asyncio.Semaphore(max_concurrent)`)
- Per-device execution with isolated sessions
- Real-time result updates via mutable references
- Parallel execution using `asyncio.gather()`

**SimulationStatus Model**
```python
class SimulationStatus(BaseModel):
    simulation_id: str
    task_name: str
    status: str  # queued, running, completed, failed, cancelled
    emulator_count: int
    completed_count: int
    failed_count: int
    results: List[Dict[str, Any]]
    started_at: Optional[str]
    completed_at: Optional[str]
```

#### 3. Device Streaming (`backend/app/api/device_simulation.py`)

**WebSocket `/api/device-simulation/sessions/{session_id}/stream`**
- Per-device screenshot streaming
- 2 FPS (0.5s interval)
- Base64-encoded JPEG frames

### Frontend Components

#### 1. MultiDeviceSimulationPage (`frontend/test-studio/src/pages/MultiDeviceSimulationPage.tsx`)

**Key Features:**
- Device selection sidebar with toggle selection
- Task configuration (task name, max concurrent)
- Real-time grid layout for device streams
- WebSocket connections for simulation status and device streams
- Responsive grid (1-4 columns based on screen size)

**State Management:**
```typescript
interface DeviceStream {
  device_id: string
  session_id: string | null
  status: 'idle' | 'running' | 'success' | 'failed'
  frame: string | null
  steps: any[]
  ws: WebSocket | null
}

interface SimulationStatus {
  simulation_id: string
  task_name: string
  status: string
  emulator_count: number
  completed_count: number
  failed_count: number
  results: any[]
}
```

**WebSocket Handling:**
- `statusWsRef`: Single WebSocket for simulation status
- `streamWsRefs`: Map of WebSockets for per-device streams
- Automatic connection management and cleanup

#### 2. Routing (`frontend/test-studio/src/App.tsx`)

- Route: `/demo/multi-device` (demo-gated)
- Back-compat alias: `/multi-device`
- Component: `<MultiDeviceSimulationPage />`

#### 3. Navigation (`frontend/test-studio/src/data/sidebar.json`)

- Menu item: "Multi-Device Simulation"
- Icon: Zap (⚡)
- URL: `/demo/multi-device`

## 🔄 Data Flow

### 1. Simulation Start
```
User clicks "Start Simulation"
  ↓
Frontend POST /api/ai-agent/execute-simulation
  ↓
Backend creates SimulationStatus
  ↓
Backend launches asyncio.create_task(_run_simulation)
  ↓
Returns simulation_id to frontend
  ↓
Frontend connects to WebSocket /ws/ai-agent/simulation/{id}
```

### 2. Concurrent Execution
```
_run_simulation() starts
  ↓
Creates asyncio.Semaphore(max_concurrent)
  ↓
For each device:
  - Acquire semaphore
  - Create Appium MCP session
  - Start screenshot streaming
  - Execute test task
  - Update device result (mutable reference)
  - Release semaphore
  ↓
asyncio.gather() waits for all devices
  ↓
Mark simulation as completed
```

### 3. Real-Time Updates
```
Backend updates SimulationStatus.results
  ↓
WebSocket sends status update (1 Hz)
  ↓
Frontend receives update
  ↓
Frontend connects to device stream WebSocket
  ↓
Backend streams screenshots (2 FPS)
  ↓
Frontend displays in grid layout
```

## 🎨 UI Layout

```
┌─────────────────────────────────────────────────────────────┐
│  Header: Multi-Device Simulation                            │
│  Status: 2/3 completed | [Stop Simulation]                  │
├──────────────┬──────────────────────────────────────────────┤
│              │                                               │
│ Configuration│  Device Streams Grid                          │
│              │  ┌──────┐ ┌──────┐ ┌──────┐                  │
│ Task Name    │  │ Dev1 │ │ Dev2 │ │ Dev3 │                  │
│ [Dropdown]   │  │ ✓    │ │ ⟳    │ │ ⏸    │                  │
│              │  │[img] │ │[img] │ │[img] │                  │
│ Max Concurrent│  │Steps:│ │Steps:│ │Steps:│                  │
│ [5]          │  │  12  │ │   8  │ │   0  │                  │
│              │  └──────┘ └──────┘ └──────┘                  │
│ Select Devices│                                              │
│ ☑ emulator-1 │                                               │
│ ☑ emulator-2 │                                               │
│ ☑ emulator-3 │                                               │
│ ☐ emulator-4 │                                               │
│              │                                               │
└──────────────┴──────────────────────────────────────────────┘
```

## 📊 Status Indicators

- **Idle** (⏸): Device waiting to start
- **Running** (⟳): Test executing with live stream
- **Success** (✓): Test completed successfully
- **Failed** (✗): Test failed with error

## 🚀 Usage

1. Navigate to http://localhost:5173/demo/multi-device (or /multi-device)
2. Select devices from left sidebar
3. Choose task name and max concurrent
4. Click "Start Simulation"
5. Watch real-time streams in grid layout
6. Monitor progress and per-device status

## 🔧 Configuration

### Runtime API base override (no rebuild)

You can point the frontend at a different backend without changing Vercel env vars by using:

- `?apiBase=https://your-server.example.com`

This value is persisted in `localStorage` under `ta_api_base`.

- **Max Concurrent**: 1-20 (default: 5)
- **Streaming FPS**: 2 FPS (0.5s interval)
- **Status Update Rate**: 1 Hz (1s interval)
- **Grid Columns**: 1-4 (responsive)

## 📝 Files Created/Modified

### Created
- `frontend/test-studio/src/pages/MultiDeviceSimulationPage.tsx` - Main UI component
- `MULTI_DEVICE_SIMULATION.md` - This documentation

### Modified
- `frontend/test-studio/src/App.tsx` - Added route
- `frontend/test-studio/src/data/sidebar.json` - Added navigation
- `backend/app/api/ai_agent.py` - Added endpoints
- `README.md` - Added usage documentation

