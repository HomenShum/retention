/**
 * Codebase map — file purposes and architecture.
 */

export const CODEBASE_SECTIONS: Record<string, string> = {
  overview: `# retention.sh Codebase Map

Sections: backend, frontend, agents, scripts, integrations, config

## Directory Structure
my-fullstack-app/
├── backend/app/          # FastAPI backend (Python 3.11+)
├── frontend/test-studio/ # React + TypeScript + Vite frontend
├── integrations/chef/    # Chef AI agent integration (Remix)
│   ├── device_testing/        # Mobile test execution logic
│   │   ├── subagents/          # OAVR specialist agents (Classifier, Verifier, Diagnosis)
│   │   ├── tools/              # MCP tool implementations (Navigation, Agentic Vision)
│   │   ├── mobile_mcp_client.py # Mobile MCP client with ADB fallback
│   │   ├── golden_bug_service.py # Evaluates agent reliability metrics
│   │   └── autonomous_exploration_service.py # Goal-agnostic curiosity
│   ├── api/                    # API endpoints
│   └── observability/          # Tracing and metrics
├── packages/             # Local npm packages (retention-mcp)
├── scripts/              # Utility scripts
├── tests/                # E2E and manual tests
└── screenshots/          # Agent screenshots output`,

  backend: `# Backend (backend/app/)

| Path | Purpose |
|------|---------|
| main.py | FastAPI entry point, route registration |
| agents/coordinator/ | Coordinator Agent (GPT-5.4) — orchestration |
| agents/coordinator/coordinator_agent.py | Agent definition + handoffs |
| agents/coordinator/coordinator_service.py | Main orchestration service |
| agents/device_testing/ | Device Testing Agent (GPT-5-mini) |
| agents/device_testing/device_testing_agent.py | Agent config (parallel_tool_calls=False) |
| agents/device_testing/mobile_mcp_client.py | Mobile MCP + ADB fallback |
| agents/device_testing/tools/ | Navigation tools (SoM, vision_click) |
| agents/device_testing/tools/autonomous_navigation_tools.py | Core navigation + bbox annotation |
| agents/device_testing/tools/agentic_vision_tools.py | GPT-5.4 vision-based click |
| agents/device_testing/subagents/ | OAVR sub-agents (classifier, verifier, diagnosis) |
| agents/device_testing/flicker_detection_service.py | 4-layer flicker detection |
| agents/device_testing/golden_bug_service.py | Golden bug evaluation |
| api/ | FastAPI route handlers |
| api/ai_agent.py | SSE streaming AI chat endpoint |
| api/device_simulation.py | Device/emulator discovery (2s cache) |
| benchmarks/ | Android World benchmarks |
| figma/ | Figma design analysis pipeline |
| figma/flow_analyzer.py | 3-phase Figma flow analysis |
| observability/tracing.py | LangSmith tracing |`,

  frontend: `# Frontend (frontend/test-studio/src/)

| Path | Purpose |
|------|---------|
| App.tsx | Main app with React Router |
| pages/DemoPage.tsx | Split-screen demo (60% viewer, 40% chat) |
| pages/LandingPage.tsx | Marketing homepage |
| pages/PricingPage.tsx | Cloud pricing + enterprise |
| pages/ChangelogPage.tsx | Version changelog |
| components/emulator/ | Emulator HUD + WebSocket streaming |
| components/DemoGate.tsx | Email gate for demo access |
| hooks/ | Custom React hooks |

Tech stack: React 18.3.1, TypeScript, Vite, TailwindCSS 4.x, shadcn/ui
State: TanStack Query (server), React Context (client)
Streaming: SSE for AI chat, WebSocket for emulator frames`,

  agents: `# Agent Architecture

## Hierarchy
Coordinator (GPT-5.4) → Dynamic handoffs → Specialists
├── Search Assistant — bug/scenario search
├── Test Generation Specialist — test code generation
└── Device Testing Specialist (GPT-5-mini) — mobile automation
    └── OAVR Sub-agents
        ├── Screen Classifier
        ├── Action Verifier
        └── Failure Diagnosis

## Key Patterns
- OpenAI Agents SDK with Runner.run_streamed()
- Dynamic handoffs via is_enabled callbacks
- SSE streaming to frontend
- parallel_tool_calls=False for Device Testing (sequential navigation)
- parallel_tool_calls=True for Coordinator (parallel orchestration)`,

  scripts: `# Scripts

| Path | Purpose |
|------|---------|
| backend/scripts/figma_cv_overlay.py | Direct CV overlay (no API calls) |
| backend/scripts/test_figma_flow_analyzer.py | Figma flow analyzer test harness |
| backend/scripts/test_flicker_detection.py | Flicker detection PoC |
| backend/scripts/annotate_real_device.py | SoM annotation demo |
| backend/scripts/ai_verify.py | LLM-based code verification |
| scripts/demo-tunnel.sh | Demo relay setup (outbound WSS) |
| scripts/setup-macos.sh | macOS dev environment setup |`,

  integrations: `# Integrations

## Chef (integrations/chef/)
- Remix + React 18 + ai SDK 4.x
- Convex backend
- Braintrust evaluation harness
- CRITICAL: Uses React 18 + ai SDK 4.x (TA frontend uses React 19 + ai SDK 5.x)
- NEVER share node_modules between Chef and TA frontend

## Chef Annotation System
Three annotation types: usage, failure, model
- JSON.parse on payloads MUST be wrapped in try-catch
- encodeModelAnnotation must default provider to 'Unknown' (not null)
- toolCallId must never be null — use call.toolCallId ?? 'unknown'
- response.json() can only be called ONCE — store in variable`,

  config: `# Key Configuration Files

| File | Purpose |
|------|---------|
| .env | Environment variables (OPENAI_API_KEY) |
| backend/requirements.txt | Python dependencies |
| frontend/test-studio/package.json | Node dependencies |
| playwright.config.ts | E2E test configuration |
| package.json (root) | Root deps (@playwright/test, mcp-appium) |`,
};

export const CODEBASE_SECTION_LIST = Object.keys(CODEBASE_SECTIONS);

