/**
 * Code conventions and style guidelines.
 */

export const CONVENTIONS = `# retention.sh Code Conventions

## Python (Backend)
- Imports: absolute imports from app. prefix
- Type hints: Required for ALL function signatures
- Docstrings: Google style for public functions
- Async: Use async/await for I/O operations
- Logging: Use logging.getLogger(__name__)
- Subagents: Use specialized agents for Perception (Screen Classifier), Action (Verifier), and Diagnosis.
- Concurrency: Use asyncio.Semaphore and asyncio.Lock for multi-device simulation safety.
- Model Tiering: GPT-5.4 (Thinking), GPT-5-mini (Core), GPT-5-nano (Utilities).
- Fallback: ALWAYS implement ADB fallback for Mobile MCP operations.

Example:
\`\`\`python
from app.agents.device_testing.mobile_mcp_client import MobileMCPClient

async def take_screenshot(device_id: str) -> str:
    """Take screenshot from device.
    
    Args:
        device_id: Android device identifier
        
    Returns:
        Base64-encoded screenshot data
    """
    pass
\`\`\`

## TypeScript (Frontend)
- Components: Functional components with hooks, one per file
- Types: Explicit types, avoid any
- Imports: Use @/ path alias for src imports
- State: React hooks for local state, TanStack Query for server state

Example:
\`\`\`typescript
interface DeviceProps {
  deviceId: string;
  onStreamStart: (id: string) => void;
}
const DeviceCard: React.FC<DeviceProps> = ({ deviceId, onStreamStart }) => { ... };
\`\`\`

## Agent Code Patterns
- Agent-as-tool pattern: coordinator delegates to specialized agents
- Colocation: agent code + tools + models together
- Factory pattern: agent creation via factory functions
- DRY: no duplicate code across modules

## Convex / Template Literals
- Use \\n escape sequences (not multi-line templates) in Convex actions
- Why: Easier to diff-review, auto-formatters don't mess indentation

## Mobile MCP Data Shapes
- Screenshots: { type: "image", data: "base64...", mimeType: "image/jpeg" }
- ALWAYS keep structured, NEVER JSON.stringify for model consumption
- Vision-ready: convert to data-URL: data:{mime};base64,{b64}

## Critical Rules
1. NEVER share node_modules between Chef (React 18) and TA frontend (React 19)
2. NEVER commit without running verification
3. ALWAYS auto-select first device (prefer emulator-5554)
4. ALWAYS scale coordinates before drawing bounding boxes
5. ALWAYS wrap JSON.parse in try-catch for external payloads
6. ALWAYS use keyword arguments for functions with *, syntax
7. NEVER pass async functions to asyncio.to_thread()
`;

export const AGENT_CONFIG_REFERENCE = `# Agent Configuration Reference

## Coordinator Agent
- Model: gpt-5.4
- parallel_tool_calls: True
- reasoning: Reasoning(effort="high")
- Handoffs: Search Assistant, Test Generation, Device Testing
- Instructions: General orchestration, task routing

## Device Testing Agent
- Model: gpt-5-mini (vision-capable)
- parallel_tool_calls: False (CRITICAL — navigation is sequential)
- reasoning: Reasoning(effort="medium")
- Tools: take_screenshot, list_elements, click, swipe, type, vision_click
- Instructions: OAVR pattern, auto-select device, never ask user

## Search Assistant
- Model: gpt-5-mini
- Purpose: Bug/scenario search in knowledge base

## Test Generation Specialist
- Model: gpt-5-mini
- Purpose: Generate test code from bug descriptions

## Streaming
- SSE (Server-Sent Events) for AI chat responses
- WebSocket for emulator frame streaming
- OpenAI Agents SDK Runner.run_streamed() for agent execution
`;

