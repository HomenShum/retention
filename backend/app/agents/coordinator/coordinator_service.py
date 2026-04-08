"""
AI Agent Service using OpenAI Agents SDK with MCP Integration

This service provides an AI agent that can:
- Search and read test tasks from capabilities
- Execute simulations on multiple emulators via MCP Appium
- Monitor real-time execution
- Summarize results
- Perform autonomous device exploration with structured reporting

Token Usage Tracking:
- Uses OpenAI Agents SDK standard: result.context_wrapper.usage
- Usage object contains:
  - requests: Total number of LLM API requests
  - input_tokens: Total input tokens across all requests
  - output_tokens: Total output tokens across all requests
  - total_tokens: Sum of input and output tokens
  - input_tokens_details: Breakdown (e.g., cached_tokens)
  - output_tokens_details: Breakdown (e.g., reasoning_tokens)
- Reference: https://openai.github.io/openai-agents-python/usage
"""

import os
import asyncio
import json
import uuid
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import logging
from pydantic import BaseModel, ConfigDict
import httpx

# Import from organized agent structure
from ..search import VectorSearchService, create_search_agent
from ..device_testing import (
    create_device_testing_agent,
    UnifiedBugReproductionService,
    AutonomousExplorationService,
    TestScenarioInput,
    MobileMCPClient,
    GoldenBugService,
)
from ..device_testing.subagents import (
    create_screen_classifier_agent,
    create_action_verifier_agent,
    create_failure_diagnosis_agent,
)
from ..device_testing.infrastructure import (
    create_simulation_tools
)
from ..device_testing.tools import create_device_testing_tools, create_autonomous_navigation_tools, create_agentic_vision_tools
from ..test_generation import create_test_generation_agent, create_test_generation_tools
from ..qa_emulation import (
    create_qa_emulation_agent,
    create_bug_detection_agent,
    create_anomaly_detection_agent,
    create_verdict_assembly_agent,
)
from .coordinator_agent import create_coordinator_agent
from .coordinator_instructions import create_coordinator_instructions
from .manus_coordinator import create_manus_coordinator

logger = logging.getLogger(__name__)

# OpenAI Agents SDK
from agents import Agent, Runner, function_tool
try:
    from agents.mcp import MCPServerStreamableHttp
    MCP_AVAILABLE = True
except ImportError:
    logger.warning("MCPServerStreamableHttp not available in this version of agents SDK")
    MCP_AVAILABLE = False
from agents.model_settings import ModelSettings


# Pydantic Models
class ChatMessage(BaseModel):
    role: str
    content: str


class AgentMessage(BaseModel):
    """Pydantic model for agent messages sent to LLM"""
    model_config = ConfigDict(extra="allow")

    role: str
    content: str


class SimulationRequest(BaseModel):
    task_name: str
    device_ids: List[str]
    emulator_count: int = 1


class SimulationStatus(BaseModel):
    simulation_id: str
    task_name: str
    status: str  # queued, running, completed, failed, cancelled
    emulator_count: int
    completed_count: int
    failed_count: int
    results: List[Dict[str, Any]]
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


def _get_ui_context_value(ui_context: Optional[Any], key: str, default: Any):
    """Read UI context values from dict or model payloads."""
    if ui_context is None:
        return default
    if isinstance(ui_context, dict):
        return ui_context.get(key, default)
    return getattr(ui_context, key, default)


def _build_ui_context_info(ui_context: Optional[Any]) -> str:
    """Format UI-selected tickets/devices into coordinator instructions."""
    if not ui_context:
        return ""

    sections: List[str] = []
    selected_tickets = _get_ui_context_value(ui_context, "selectedTickets", [])
    selected_devices = _get_ui_context_value(ui_context, "selectedDevices", [])

    if selected_tickets:
        ticket_list = "\n".join([
            f"- {ticket.get('title', 'Unknown') if isinstance(ticket, dict) else ticket.title} (ID: {ticket.get('id', 'N/A') if isinstance(ticket, dict) else ticket.id})"
            for ticket in selected_tickets
        ])
        sections.append(f"**Currently Selected Tickets in UI:**\n{ticket_list}")

    if selected_devices:
        unique_devices = list(dict.fromkeys(selected_devices))
        device_list = "\n".join([f"- {device_id}" for device_id in unique_devices])
        sections.append(f"**Currently Selected Devices in UI:**\n{device_list}")

    current_page = _get_ui_context_value(ui_context, "currentPage", None)
    if current_page:
        sections.append(f"**User is currently viewing:** `{current_page}`")

    workspace_mode = bool(_get_ui_context_value(ui_context, "workspaceMode", False))
    workspace_channels = _get_ui_context_value(ui_context, "workspaceChannels", []) or []
    workspace_intent = _get_ui_context_value(ui_context, "workspaceIntent", None)

    if workspace_mode:
        sections.append(
            "**Workspace-aware mode:** Treat Slack activity, operating chatter, and usage telemetry as live context for this request."
        )

    if workspace_channels:
        normalized_channels = []
        for channel in workspace_channels:
            clean = str(channel).strip().lstrip('#')
            if clean and clean not in normalized_channels:
                normalized_channels.append(clean)
        if normalized_channels:
            channel_list = "\n".join([f"- #{channel}" for channel in normalized_channels])
            sections.append(f"**Preferred Slack / workspace channels:**\n{channel_list}")

    if workspace_intent:
        sections.append(f"**Workspace intent:** {workspace_intent}")

    if not sections:
        return ""

    joined_sections = "\n\n".join(sections)
    return f"\n\n{joined_sections}"


class AIAgentService:
    """
    AI Agent Service using OpenAI Agents SDK with MCP Appium integration
    """

    def set_chef_runner(self, runner):
        """Set the Chef runner reference for app generation tasks."""
        self._chef_runner_ref = runner
        logger.info("🍳 Chef runner linked to AIAgentService")

    def __init__(
        self,
        appium_mcp_streaming,
        capabilities_config: Dict[str, Any],
        mcp_server_url: str = "http://localhost:3000/mcp",
        vector_search_service: Optional[VectorSearchService] = None,
        bug_reproduction_service: Optional[UnifiedBugReproductionService] = None
    ):
        """
        Initialize AI Agent Service

        Args:
            appium_mcp_streaming: AppiumMCPStreamingManager instance
            capabilities_config: Test capabilities configuration
            mcp_server_url: URL of the MCP Appium server
            vector_search_service: Optional VectorSearchService instance for semantic search
            bug_reproduction_service: Optional UnifiedBugReproductionService for test execution
        """
        self.appium_mcp = appium_mcp_streaming
        self.capabilities = capabilities_config
        self.mcp_server_url = mcp_server_url
        self.simulations: Dict[str, SimulationStatus] = {}
        self._simulation_locks: Dict[str, asyncio.Lock] = {}  # P0-2: Per-simulation locks for thread safety
        self.vector_search = vector_search_service

        # Initialize unified bug reproduction service (handles both test execution and bug repro)
        self.bug_repro_service = bug_reproduction_service or UnifiedBugReproductionService()

        # Initialize golden bug evaluation service (uses unified bug repro + capabilities)
        self.golden_bug_service = GoldenBugService(self.bug_repro_service, self.capabilities)

        # Backend URL for internal API calls (configurable for hosted deployments)
        self._backend_url = os.environ.get("TA_BACKEND_URL", "http://localhost:8000")


        # Initialize exploration service
        self.exploration_service = AutonomousExplorationService()

        # Reference to ChefRunner (set by main.py)
        self._chef_runner_ref = None

        # Initialize Mobile MCP client for autonomous navigation
        self.mobile_mcp_client = MobileMCPClient()
        self._mobile_mcp_started = False

        # Check for OpenAI API key
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            logger.warning("OPENAI_API_KEY not set. AI Agent will not function.")

        # Model selection (configurable via env)
        # January 2026 Industry Standard:
        # - gpt-5-mini for routing (NOT nano - quality matters for classification)
        # - gpt-5.4 for high-thinking tasks (orchestration, complex reasoning)
        # - gpt-5-nano ONLY for MCP tool calls, distillation, search enhancement
        from ..model_fallback import PRIMARY_MODEL, THINKING_MODEL, DISTILL_MODEL
        self.model_name = os.getenv("OPENAI_MODEL", PRIMARY_MODEL)  # gpt-5-mini
        self.thinking_model = os.getenv("OPENAI_THINKING_MODEL", THINKING_MODEL)  # gpt-5.4
        self.distill_model = os.getenv("OPENAI_DISTILL_MODEL", DISTILL_MODEL)  # gpt-5-nano

        # Feature flag: auto-capture screenshots after every step
        self.auto_screenshot_every_step = os.getenv("AUTO_SCREENSHOT_EVERY_STEP", "false").lower() in ("1", "true", "yes", "on")
        logger.info(f"Auto screenshot every step feature: {self.auto_screenshot_every_step}")

        # Cache for coordinator agent (for visualization)
        self._coordinator_agent_cache = None

        # P0-1: Simulation cleanup configuration
        self._max_simulation_age_hours = int(os.getenv("SIMULATION_MAX_AGE_HOURS", "24"))
        self._max_simulation_count = int(os.getenv("SIMULATION_MAX_COUNT", "100"))

        logger.info("AI Agent Service initialized")

    async def _create_agent_session(self, user_message: str, device_id: Optional[str] = None) -> str:
        """Create a new agent session and return session ID"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self._backend_url}/api/ai-agent/sessions",
                    json={
                        "title": user_message[:100],  # Truncate long messages
                        "deviceId": device_id,
                        "goal": user_message
                    },
                    timeout=5.0
                )
                if response.status_code == 200:
                    session_data = response.json()
                    session_id = session_data.get("id")
                    logger.info(f"Created agent session: {session_id}")
                    return session_id
                else:
                    logger.error(f"Failed to create session: {response.status_code}")
                    return None
        except Exception as e:
            logger.error(f"Error creating agent session: {e}")
            return None

    async def _add_session_step(
        self,
        session_id: str,
        step_number: int,
        description: str,
        command: Optional[str] = None,
        thoughts: Optional[str] = None,
        action: Optional[str] = None,
        observation: Optional[str] = None,
        results: Optional[str] = None,
        status: str = "success",
        screenshot: Optional[str] = None,
        request_tokens: Optional[int] = None,
        response_tokens: Optional[int] = None,
    ):
        """Add a step to the agent session"""
        if not session_id:
            return

        try:
            async with httpx.AsyncClient() as client:
                step_data = {
                    "sessionId": session_id,
                    "step": {
                        "id": step_number,
                        "stepNumber": step_number,
                        "description": description,
                        "command": command,
                        "thoughts": thoughts,
                        "action": action,
                        "observation": observation,
                        "results": results,
                        "status": status,
                        "model": self.model_name,
                        "requestTokens": request_tokens,
                        "responseTokens": response_tokens,
                    }
                }
                if screenshot:
                    step_data["step"]["screenshot"] = screenshot
                response = await client.post(
                    f"{self._backend_url}/api/ai-agent/sessions/{session_id}/steps",
                    json=step_data,
                    timeout=5.0
                )
                if response.status_code != 200:
                    logger.error(f"Failed to add step to session: {response.status_code}")
        except Exception as e:
            logger.error(f"Error adding step to session: {e}")

    async def _update_session_status(self, session_id: str, status: str):
        """Update the session status (running, completed, failed)"""
        if not session_id:
            return

        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self._backend_url}/api/ai-agent/sessions/{session_id}/status",
                    json={"status": status},  # Use JSON body instead of query params
                    timeout=5.0
                )
                if response.status_code == 200:
                    logger.info(f"Updated session {session_id} status to {status}")
                else:
                    logger.error(f"Failed to update session status: {response.status_code}")
        except Exception as e:
            logger.error(f"Error updating session status: {e}")

    async def _update_session_tokens(
        self,
        session_id: str,
        total_request_tokens: int,
        total_response_tokens: int,
        total_tokens: int
    ):
        """Update the session token usage totals"""
        if not session_id:
            return

        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self._backend_url}/api/ai-agent/sessions/{session_id}/tokens",
                    json={
                        "totalRequestTokens": total_request_tokens,
                        "totalResponseTokens": total_response_tokens,
                        "totalTokens": total_tokens
                    },
                    timeout=5.0
                )
                if response.status_code == 200:
                    logger.info(f"Updated session {session_id} token usage")
                else:
                    logger.error(f"Failed to update session tokens: {response.status_code}")
        except Exception as e:
            logger.error(f"Error updating session tokens: {e}")

    async def _save_conversation_history(
        self,
        session_id: str,
        conversation_history: List[AgentMessage]
    ):
        """Save conversation history to session for resuming"""
        if not session_id:
            return

        try:
            # Convert Pydantic models to dicts
            history_dicts = [msg.model_dump() for msg in conversation_history]

            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self._backend_url}/api/ai-agent/sessions/{session_id}/conversation",
                    json={"conversationHistory": history_dicts},
                    timeout=5.0
                )
                if response.status_code == 200:
                    logger.info(f"Saved conversation history for session {session_id}")
                else:
                    logger.error(f"Failed to save conversation history: {response.status_code}")
        except Exception as e:
            logger.error(f"Error saving conversation history: {e}")

    async def _generate_report_card(self, session_id: str, final_output: str) -> Optional[dict]:
        """Score final_output against golden sessions and persist + return a report card.

        Scoring strategy (lexical):
          - For each golden, build reference text from goal + step observations.
          - Compute SequenceMatcher ratio between final_output and reference.
          - Average over all goldens → overall score.
        Returns the persisted report card dict, or None if no goldens / error.
        """
        if not session_id or not final_output:
            return None

        import difflib

        try:
            async with httpx.AsyncClient() as client:
                # 1. Fetch golden sessions
                resp = await client.get(f"{self._backend_url}/api/ai-agent/goldens", timeout=5.0)
                if resp.status_code != 200:
                    logger.warning("No golden sessions available for report card scoring")
                    return None
                goldens = resp.json().get("sessions", [])
                if not goldens:
                    logger.info("No golden sessions — skipping report card generation")
                    return None

                # 2. Score against each golden
                scores = []
                passed_checks: list[str] = []
                failed_checks: list[str] = []

                for g in goldens:
                    # Build reference text: goal + all step observations
                    ref_parts = [g.get("goal", "") or ""]
                    for step in (g.get("steps") or []):
                        obs = step.get("observation") or ""
                        if obs:
                            ref_parts.append(obs)
                    ref_text = " ".join(ref_parts).strip().lower()
                    candidate = final_output.lower()

                    ratio = difflib.SequenceMatcher(None, candidate, ref_text).ratio()
                    scores.append(ratio)

                avg_score = sum(scores) / len(scores) if scores else 0.0

                # 3. Build pass/fail checks
                if avg_score >= 0.70:
                    passed_checks.append("Lexical similarity ≥ 70% vs goldens")
                else:
                    failed_checks.append(f"Lexical similarity {avg_score*100:.0f}% < 70% threshold")

                if len(final_output) >= 100:
                    passed_checks.append("Response length sufficient (≥ 100 chars)")
                else:
                    failed_checks.append("Response length too short (< 100 chars)")

                notes = (
                    f"Scored against {len(goldens)} golden session(s). "
                    f"Avg lexical similarity: {avg_score*100:.1f}%."
                )

                # 4. POST report card to session
                card_payload = {
                    "score": round(avg_score, 3),
                    "strategy": "lexical_similarity",
                    "regressionDelta": None,
                    "notes": notes,
                    "passedChecks": passed_checks,
                    "failedChecks": failed_checks,
                }
                post_resp = await client.post(
                    f"{self._backend_url}/api/ai-agent/sessions/{session_id}/report",
                    json=card_payload,
                    timeout=5.0,
                )
                if post_resp.status_code == 200:
                    card = post_resp.json()
                    logger.info(f"Report card persisted for session {session_id}: score={avg_score:.3f}")
                    return card
                else:
                    logger.error(f"Failed to persist report card: {post_resp.status_code}")
                    return card_payload  # Return unpersisted card so SSE can still emit it

        except Exception as e:
            logger.error(f"Error generating report card for session {session_id}: {e}")
            return None

    async def _ensure_mobile_mcp_started(self):
        """Ensure Mobile MCP client is started."""
        if not self._mobile_mcp_started:
            try:
                await self.mobile_mcp_client.start()
                self._mobile_mcp_started = True
                logger.info("Mobile MCP client started successfully")
            except Exception as e:
                logger.error(f"Failed to start Mobile MCP client: {e}")
                raise

    async def _get_first_available_device(self) -> str:
        """Query ADB to get the first available device ID.

        Returns the first connected device, or 'emulator-5554' as fallback if none found.
        This fixes the hardcoded device ID issue where agent would fail on actual devices.
        """
        try:
            import asyncio
            proc = await asyncio.create_subprocess_exec(
                "adb", "devices",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)

            lines = stdout.decode().strip().split('\n')
            for line in lines[1:]:  # Skip "List of devices attached" header
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1] == 'device':
                    device_id = parts[0]
                    logger.info(f"Discovered available device: {device_id}")
                    return device_id

            logger.warning("No devices found via ADB, using fallback 'emulator-5554'")
            return "emulator-5554"
        except Exception as e:
            logger.warning(f"Failed to query ADB for devices: {e}, using fallback 'emulator-5554'")
            return "emulator-5554"

    def _extract_screenshot_url(self, tool_output: Any) -> Optional[str]:
        """Extract screenshot URL from a tool output string, if present.
        Looks for lines like 'Screenshot saved to: /path/to/file.png' or 'Screenshot saved to /path/to/file.png'.
        Returns a web path like '/static/screenshots/filename.png' or None.
        """
        try:
            text = str(tool_output) if tool_output is not None else ""
            if not text:
                return None
            import re, os
            m = re.search(r"Screenshot saved to:?\s*(.+?\.(?:png|jpg|jpeg|gif|webp))(?:\b|[\s\)])", text, flags=re.IGNORECASE)
            if not m:
                return None
            path = m.group(1).strip().rstrip('.')
            filename = os.path.basename(path)
            if not filename:
                return None
            return f"/static/screenshots/{filename}"
        except Exception:
            return None

    def get_available_scenarios(self) -> List[Dict[str, str]]:
        """Get list of available test scenarios"""
        scenarios = []

        if "instagram_test_scenarios" in self.capabilities:
            for name, config in self.capabilities["instagram_test_scenarios"].items():
                scenarios.append({
                    "name": name,
                    "description": config.get("description", "No description available"),
                    "category": "Instagram"
                })

        logger.info(f"📋 get_available_scenarios returning {len(scenarios)} scenarios: {[s['name'] for s in scenarios]}")
        return scenarios

    def search_tasks(self, query: str) -> List[Dict[str, str]]:
        """Search for tasks matching the query"""
        query_lower = query.lower()
        scenarios = self.get_available_scenarios()

        results = [
            s for s in scenarios
            if query_lower in s["name"].lower() or query_lower in s["description"].lower()
        ]

        return results

    def get_task_details(self, task_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific task"""
        if "instagram_test_scenarios" in self.capabilities:
            if task_name in self.capabilities["instagram_test_scenarios"]:
                config = self.capabilities["instagram_test_scenarios"][task_name]
                return {
                    "name": task_name,
                    "description": config.get("description", ""),
                    "steps": config.get("steps", []),
                    "category": "Instagram"
                }

        return None

    async def execute_simulation(
        self,
        task_name: str,
        device_ids: List[str],
        max_concurrent: int = 5
    ) -> str:
        """Execute a test simulation on multiple emulators (single task mode)"""
        simulation_id = str(uuid.uuid4())

        # Create simulation status
        self.simulations[simulation_id] = SimulationStatus(
            simulation_id=simulation_id,
            task_name=task_name,
            status="running",
            emulator_count=len(device_ids),
            completed_count=0,
            failed_count=0,
            results=[],
            started_at=datetime.now(timezone.utc).isoformat()
        )

        # Start simulation in background
        asyncio.create_task(self._run_simulation(simulation_id, task_name, device_ids, max_concurrent))

        return simulation_id

    async def execute_multi_task_simulation(
        self,
        device_tasks: List[Dict[str, str]],  # [{device_id, task_name}, ...]
        max_concurrent: int = 5
    ) -> str:
        """
        Execute multiple different tasks on multiple devices concurrently.

        Deep Agent Pattern: Enables parallel execution of different tasks on different devices,
        allowing complex multi-device test orchestration.

        Args:
            device_tasks: List of {device_id, task_name} mappings
            max_concurrent: Maximum concurrent executions

        Returns:
            simulation_id for tracking
        """
        simulation_id = str(uuid.uuid4())

        # Create task mapping
        task_mapping = {dt["device_id"]: dt["task_name"] for dt in device_tasks}

        # Get unique tasks for display
        unique_tasks = list(set(dt["task_name"] for dt in device_tasks))
        task_summary = ", ".join(unique_tasks) if len(unique_tasks) <= 3 else f"{len(unique_tasks)} tasks"

        # Create simulation status
        self.simulations[simulation_id] = SimulationStatus(
            simulation_id=simulation_id,
            task_name=f"Multi-task: {task_summary}",  # Summary for display
            status="running",
            emulator_count=len(device_tasks),
            completed_count=0,
            failed_count=0,
            results=[],
            started_at=datetime.now(timezone.utc).isoformat()
        )

        # Start multi-task simulation in background
        asyncio.create_task(self._run_multi_task_simulation(
            simulation_id,
            task_mapping,
            max_concurrent
        ))

        return simulation_id

    async def _run_simulation(
        self,
        simulation_id: str,
        task_name: str,
        device_ids: List[str],
        max_concurrent: int
    ):
        """Run the actual simulation with per-device concurrent execution and real-time updates (single task mode)."""
        # Use shared execution logic with task mapping
        task_mapping = {device_id: task_name for device_id in device_ids}
        await self._execute_device_tasks(simulation_id, task_mapping, max_concurrent)

    async def _run_multi_task_simulation(
        self,
        simulation_id: str,
        task_mapping: Dict[str, str],  # {device_id: task_name}
        max_concurrent: int
    ):
        """
        Run multi-task simulation with different tasks on different devices.

        Deep Agent Pattern: Executes multiple specialized sub-tasks in parallel,
        each device running its assigned task independently.
        """
        await self._execute_device_tasks(simulation_id, task_mapping, max_concurrent)

    async def _execute_device_tasks(
        self,
        simulation_id: str,
        task_mapping: Dict[str, str],  # {device_id: task_name}
        max_concurrent: int
    ):
        """
        Core execution logic for running tasks on devices with concurrency control.

        Deep Agent Pattern: Unified execution engine that handles both single-task
        and multi-task scenarios, with semaphore-based concurrency control.
        """
        sem = asyncio.Semaphore(max_concurrent)

        # P0-2 FIX: Create per-simulation lock for thread-safe results access
        if simulation_id not in self._simulation_locks:
            self._simulation_locks[simulation_id] = asyncio.Lock()
        sim_lock = self._simulation_locks[simulation_id]

        async def run_on_device(device_id: str, task_name: str):
            session_id = None  # Initialize to avoid undefined variable in finally

            # P0-3 FIX: Use context manager for proper semaphore acquire/release
            async with sem:
                try:
                    # Initialize per-device result entry for real-time updates
                    if simulation_id not in self.simulations:
                        return
                    sim = self.simulations[simulation_id]
                    device_result = {
                        "device_id": device_id,
                        "task_name": task_name,  # Store task name per device
                        "session_id": None,
                        "status": "running",
                        "steps": [],
                    }

                    # P0-2 FIX: Thread-safe append to results
                    async with sim_lock:
                        sim.results.append(device_result)

                    # Create Appium MCP session and start streaming
                    session_id = await self.appium_mcp.create_session(device_id=device_id, enable_streaming=True, fps=2)
                    device_result["session_id"] = session_id
                    if not session_id:
                        device_result["status"] = "failed"
                        device_result["error"] = "Failed to create session"
                        return

                    # Build simple actionable steps for known scenarios
                    steps: List[Dict[str, Any]] = []
                    if task_name == "feed_scrolling":
                        for i in range(5):
                            steps.append({"action": "scroll", "direction": "down"})
                            steps.append({"action": "screenshot"})
                    elif task_name == "login_test":
                        steps.append({"action": "screenshot"})
                        steps.append({"action": "tap", "x": 500, "y": 800})  # Example tap
                        steps.append({"action": "screenshot"})
                    elif task_name == "search_test":
                        steps.append({"action": "screenshot"})
                        steps.append({"action": "tap", "x": 500, "y": 200})  # Search bar
                        steps.append({"action": "screenshot"})
                    elif task_name == "settings_navigation":
                        steps.append({"action": "screenshot"})
                        steps.append({"action": "scroll", "direction": "down"})
                        steps.append({"action": "screenshot"})
                    else:
                        # Fallback: take an initial screenshot
                        steps.append({"action": "screenshot"})

                    async def on_step(step_result: Dict[str, Any]):
                        # Append step result for real-time UI updates
                        device_result["steps"].append(step_result)

                    # Execute the test sequence via unified bug reproduction service
                    scenario = TestScenarioInput(
                        scenario_name=task_name,
                        device_id=device_id,
                        test_steps=steps,
                        session_id=session_id
                    )

                    test_result = await self.bug_repro_service.execute_scenario(
                        scenario=scenario,
                        # MobileMCPStreamingManager sessions are lightweight records and do not expose a per-session
                        # client. Pass the shared MobileMCPClient instance instead.
                        mcp_client=self.appium_mcp.mcp_client if session_id in self.appium_mcp.sessions else None,
                        on_step=on_step
                    )

                    # Update final device status
                    device_result["status"] = "success" if test_result.status == "passed" else "failed"
                except Exception as e:
                    logger.error(f"Device {device_id} task '{task_name}' failed: {e}")
                    if simulation_id in self.simulations:
                        # P0-2 FIX: Thread-safe modification of results
                        async with sim_lock:
                            for r in self.simulations[simulation_id].results:
                                if r.get("device_id") == device_id:
                                    r["status"] = "failed"
                                    r["error"] = str(e)
                                    break
                finally:
                    # Close session if created
                    if session_id:
                        try:
                            await self.appium_mcp.close_session(session_id)
                        except Exception as close_err:
                            # P1-7 FIX: Log session close errors instead of silently swallowing
                            logger.warning(f"Failed to close session {session_id}: {close_err}")

        # Launch tasks for all devices
        tasks = [
            asyncio.create_task(run_on_device(device_id, task_name))
            for device_id, task_name in task_mapping.items()
        ]

        try:
            await asyncio.gather(*tasks)
            # Summarize results
            if simulation_id in self.simulations:
                sim = self.simulations[simulation_id]
                sim.status = "completed"
                sim.completed_count = sum(1 for r in sim.results if r.get("status") == "success")
                sim.failed_count = sum(1 for r in sim.results if r.get("status") == "failed")
                sim.completed_at = datetime.now(timezone.utc).isoformat()
                # Trigger cleanup of old simulations
                self.cleanup_old_simulations()
        except Exception as e:
            logger.error(f"Simulation {simulation_id} failed: {e}")
            if simulation_id in self.simulations:
                self.simulations[simulation_id].status = "failed"
                self.simulations[simulation_id].completed_at = datetime.now(timezone.utc).isoformat()

    def get_simulation_status(self, simulation_id: str) -> Optional[SimulationStatus]:
        """Get the current status of a simulation"""
        return self.simulations.get(simulation_id)

    def list_simulations(self) -> List[SimulationStatus]:
        """List all simulations (for polling from frontend)"""
        return list(self.simulations.values())

    def cancel_simulation(self, simulation_id: str) -> bool:
        """Cancel a running simulation"""
        if simulation_id in self.simulations:
            self.simulations[simulation_id].status = "cancelled"
            self.simulations[simulation_id].completed_at = datetime.now(timezone.utc).isoformat()
            return True
        return False

    def cleanup_old_simulations(self) -> int:
        """
        P0-1 FIX: Remove old simulations to prevent memory leak.

        Returns:
            Number of simulations removed
        """
        now = datetime.now(timezone.utc)
        to_remove = []

        # Find simulations older than max age
        for sim_id, sim in self.simulations.items():
            if sim.completed_at:
                try:
                    completed = datetime.fromisoformat(sim.completed_at.replace('Z', '+00:00'))
                    age_hours = (now - completed).total_seconds() / 3600
                    if age_hours > self._max_simulation_age_hours:
                        to_remove.append(sim_id)
                except (ValueError, TypeError):
                    pass  # Skip if timestamp is invalid

        # Also limit total count - remove oldest completed simulations if over limit
        completed_sims = [
            (sim_id, sim) for sim_id, sim in self.simulations.items()
            if sim.completed_at and sim_id not in to_remove
        ]
        if len(self.simulations) - len(to_remove) > self._max_simulation_count:
            # Sort by completion time and remove oldest
            completed_sims.sort(key=lambda x: x[1].completed_at or "")
            excess = len(self.simulations) - len(to_remove) - self._max_simulation_count
            to_remove.extend([s[0] for s in completed_sims[:excess]])

        # Remove simulations and their locks
        removed_count = 0
        for sim_id in set(to_remove):
            if sim_id in self.simulations:
                del self.simulations[sim_id]
                removed_count += 1
            if sim_id in self._simulation_locks:
                del self._simulation_locks[sim_id]

        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} old simulations")

        return removed_count

    async def generate_test(
        self,
        description: str,
        app_package: Optional[str] = None,
        device_id: Optional[str] = None
    ) -> str:
        """
        Generate test code from natural language description

        Args:
            description: Natural language description of the test
            app_package: Optional app package to test
            device_id: Optional device ID to use for context

        Returns:
            Generated test code
        """
        if not self.api_key:
            return "Error: OpenAI API key not configured"

        prompt = f"""Generate Python test code using Appium for the following test scenario:

Test Description: {description}
App Package: {app_package or 'Not specified'}
Device: {device_id or 'Any Android device'}

Generate complete, executable test code with:
1. Proper imports (pytest, appium)
2. Setup and teardown methods
3. Clear test steps with comments
4. Assertions
5. Error handling

Format the code as a complete Python test file."""

        try:
            # Use OpenAI to generate test code
            import openai
            from ...observability.tracing import get_traced_client
            client = get_traced_client(openai.OpenAI(api_key=self.api_key))

            # January 2026 Industry Standard:
            # Use gpt-5.4 (thinking_model) for test generation - high thinking budget
            # Test generation requires complex reasoning and planning
            response = client.chat.completions.create(
                model=self.thinking_model,  # gpt-5.4 for high-thinking tasks
                messages=[
                    {"role": "system", "content": "You are an expert test automation engineer specializing in mobile testing with Appium."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=2000
            )

            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Test generation failed: {e}")
            return f"Error generating test: {str(e)}"

    async def reproduce_bug(
        self,
        bug_description: str,
        steps: List[str],
        device_id: str,
        app_package: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Reproduce a bug automatically using AI agent

        Args:
            bug_description: Description of the bug
            steps: List of reproduction steps
            device_id: Device ID to use
            app_package: Optional app package

        Returns:
            Bug reproduction result with screenshots and evidence
        """
        if not self.api_key:
            return {"error": "OpenAI API key not configured"}

        try:
            # Create session on device
            session_id = await self.appium_mcp.create_session(
                device_id=device_id,
                app_package=app_package,
                enable_streaming=True,
                fps=2
            )

            if not session_id:
                return {"error": "Failed to create session"}

            results = {
                "bug_description": bug_description,
                "device_id": device_id,
                "session_id": session_id,
                "steps_executed": [],
                "screenshots": [],
                "success": False
            }

            # Execute each step
            for i, step in enumerate(steps):
                try:
                    # Get AI to interpret the step and execute it
                    prompt = f"""Given this bug reproduction step: "{step}"

Convert it to an Appium action. Return JSON with:
- action: click/scroll/input/wait
- target: element description or coordinates
- value: text to input (if applicable)"""

                    import openai
                    from ...observability.tracing import get_traced_client
                    client = get_traced_client(openai.OpenAI(api_key=self.api_key))

                    # January 2026 Industry Standard:
                    # Use gpt-5-nano (distill_model) for simple action conversion
                    # This is a valid use case for nano: extracting/distilling info
                    response = client.chat.completions.create(
                        model=self.distill_model,  # gpt-5-nano for distillation
                        messages=[
                            {"role": "system", "content": "You are an expert at converting manual test steps to Appium actions."},
                            {"role": "user", "content": prompt}
                        ]
                    )

                    # Execute the action via device_simulation API
                    action_result = {
                        "step": step,
                        "status": "executed",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }

                    # Take screenshot after each step
                    screenshot = await self.appium_mcp.get_screenshot(session_id)
                    if screenshot:
                        results["screenshots"].append({
                            "step": i + 1,
                            "screenshot": screenshot
                        })

                    results["steps_executed"].append(action_result)

                except Exception as e:
                    logger.error(f"Step {i+1} failed: {e}")
                    results["steps_executed"].append({
                        "step": step,
                        "status": "failed",
                        "error": str(e)
                    })

            results["success"] = True

            # Close session
            await self.appium_mcp.close_session(session_id)

            return results

        except Exception as e:
            logger.error(f"Bug reproduction failed: {e}")
            return {"error": str(e)}

    async def analyze_scenario(self, scenario_name: str) -> Dict[str, Any]:
        """
        Analyze a test scenario and generate insights

        Args:
            scenario_name: Name of the scenario to analyze

        Returns:
            Analysis with insights and recommendations
        """
        if not self.api_key:
            return {"error": "OpenAI API key not configured"}

        # Get scenario details
        task = self.get_task_details(scenario_name)
        if not task:
            return {"error": f"Scenario '{scenario_name}' not found"}

        prompt = f"""Analyze this test scenario and provide insights:

Scenario: {scenario_name}
Details: {json.dumps(task, indent=2)}

Provide:
1. Test coverage analysis
2. Potential edge cases
3. Risk areas
4. Recommendations for improvement
5. Suggested additional test cases"""

        try:
            import openai
            from ...observability.tracing import get_traced_client
            client = get_traced_client(openai.OpenAI(api_key=self.api_key))

            # January 2026 Industry Standard:
            # Use gpt-5.4 (thinking_model) for analysis - requires complex reasoning
            response = client.chat.completions.create(
                model=self.thinking_model,  # gpt-5.4 for high-thinking tasks
                messages=[
                    {"role": "system", "content": "You are an expert QA analyst specializing in mobile app testing."},
                    {"role": "user", "content": prompt}
                ]
            )

            return {
                "scenario": scenario_name,
                "analysis": response.choices[0].message.content,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Scenario analysis failed: {e}")
            return {"error": str(e)}

    async def chat(self, messages: List[ChatMessage], ui_context: Optional[Dict[str, Any]] = None) -> str:
        """
        Process a chat message using the AI agent with MCP integration

        Args:
            messages: List of chat messages
            ui_context: Optional context about the current UI state (selected tickets, etc.)

        Returns:
            Agent's response
        """
        if not self.api_key:
            return "Error: OpenAI API key not configured. Please set OPENAI_API_KEY environment variable."

        try:
            # Get the last user message
            user_message = messages[-1].content if messages else ""

            # Build conversation history for context
            # Convert ChatMessage list to format expected by Runner
            conversation_history = []
            for msg in messages[:-1]:  # All messages except the last one
                conversation_history.append({
                    "role": msg.role,
                    "content": msg.content
                })

            # Build UI context information for the system instructions
            ui_context_info = _build_ui_context_info(ui_context)

            # Create function tools for the agent using modularized tools
            service_ref = self  # Capture self for closures

            # Create simulation tools with service reference
            simulation_tools = create_simulation_tools(service_ref)

            # Create specialized agents (hierarchical multi-agent architecture)
            scenarios = self.get_available_scenarios()

            # 1. Search Agent - handles bug reports and test scenario searches
            search_agent = create_search_agent(
                vector_search_service=self.vector_search,
                test_scenarios=scenarios
            )

            # 2. Test Generation Agent - handles test generation and analysis
            test_generation_tools = create_test_generation_tools(self)
            test_generation_agent = create_test_generation_agent(
                generate_test_code_func=test_generation_tools['generate_test_code'],
                list_test_scenarios_func=test_generation_tools['list_test_scenarios'],
                analyze_coverage_func=test_generation_tools['analyze_coverage'],
                available_scenarios=scenarios
            )

            # 4. Device Testing Agent - unified agent for test execution, bug reproduction, exploration
            device_testing_tools = create_device_testing_tools(self)

            # Create OAVR sub-agents for device testing (non-streaming mode - basic version without autonomous nav)
            screen_classifier_agent = create_screen_classifier_agent()
            action_verifier_agent = create_action_verifier_agent()
            failure_diagnosis_agent = create_failure_diagnosis_agent()

            device_testing_agent = create_device_testing_agent(
                # Device discovery
                list_available_devices_func=device_testing_tools['list_available_devices'],
                # Test execution & bug reproduction
                execute_test_scenario_func=device_testing_tools['execute_test_scenario'],
                reproduce_bug_func=device_testing_tools['reproduce_bug'],
                get_execution_status_func=device_testing_tools['get_execution_status'],
                # Exploration
                start_autonomous_exploration_func=device_testing_tools['start_autonomous_exploration'],
                get_exploration_report_func=device_testing_tools['get_exploration_report'],
                list_explorations_func=device_testing_tools['list_explorations'],
                # Device control
                find_elements_on_device_func=device_testing_tools['find_elements_on_device'],
                click_element_by_text_func=device_testing_tools['click_element_by_text'],
                execute_device_action_func=device_testing_tools['execute_device_action'],
                # Golden bug evaluation
                list_golden_bugs_func=device_testing_tools['list_golden_bugs'],
                run_golden_bug_func=device_testing_tools['run_golden_bug'],
                run_all_golden_bugs_func=device_testing_tools['run_all_golden_bugs'],
                # OAVR sub-agents
                screen_classifier_agent=screen_classifier_agent,
                action_verifier_agent=action_verifier_agent,
                failure_diagnosis_agent=failure_diagnosis_agent,
                # Context
                available_scenarios=scenarios
            )

            # Create QA emulation agent with parallel subagents
            # reasoning_effort="high" is explicit so callers can override in the future
            qa_emulation_agent = create_qa_emulation_agent(
                prompt_version="v12",
                bug_detection_agent=create_bug_detection_agent(reasoning_effort="high"),
                anomaly_detection_agent=create_anomaly_detection_agent(),
                verdict_assembly_agent=create_verdict_assembly_agent(reasoning_effort="high"),
                reasoning_effort="high",
            )

            # Create coordinator agent using factory (hierarchical multi-agent pattern)
            coordinator = create_coordinator_agent(
                search_agent=search_agent,
                test_generation_agent=test_generation_agent,
                device_testing_agent=device_testing_agent,
                scenarios=scenarios,
                ui_context_info=ui_context_info,
                execute_simulation_func=simulation_tools['execute_simulation'],
                qa_emulation_agent=qa_emulation_agent,
            )

            # Pre-flight check whether MCP server is reachable; fall back if not
            mcp_ok = False
            if MCP_AVAILABLE:
                try:
                    import httpx  # local import to avoid hard dependency if unused
                    async with httpx.AsyncClient(timeout=2.0) as client:
                        await client.get(self.mcp_server_url)
                        mcp_ok = True
                except Exception as mcp_probe_err:
                    logger.warning(f"MCP server probe failed; continuing without MCP: {mcp_probe_err}")

            if mcp_ok and MCP_AVAILABLE:
                async with MCPServerStreamableHttp(
                    name="Appium MCP Server",
                    params={
                        "url": self.mcp_server_url,
                        "timeout": 10,
                    },
                    cache_tools_list=True,
                    max_retry_attempts=1,
                ) as mcp_server:
                    # Run coordinator with MCP server available
                    # Increase max_turns for complex autonomous navigation tasks (up to 100 steps)
                    # Pass conversation history + current message for context retention
                    agent_input = conversation_history + [{"role": "user", "content": user_message}] if conversation_history else user_message
                    result = await Runner.run(coordinator, agent_input, max_turns=1000)
                    return result.final_output

            # Fallback path: run coordinator without MCP (same hierarchical structure)
            # Increase max_turns for complex autonomous navigation tasks (up to 100 steps)
            # Pass conversation history + current message for context retention
            agent_input = conversation_history + [{"role": "user", "content": user_message}] if conversation_history else user_message
            result = await Runner.run(coordinator, agent_input, max_turns=1000)
            return result.final_output

        except Exception as e:
            logger.error(f"Error in chat: {e}")
            return f"Error: {str(e)}"

    async def chat_stream(self, messages: List[ChatMessage], ui_context: Optional[Dict[str, Any]] = None, resume_session_id: Optional[str] = None):
        """
        Process a chat message with streaming response

        Args:
            messages: List of chat messages
            ui_context: Optional context about the current UI state (selected tickets, etc.)
            resume_session_id: Optional session ID to resume (skips creating a new session)

        Yields:
            Streaming events from the agent
        """
        def estimate_tokens(text: str) -> int:
            """Rough estimation of tokens (4 chars per token)"""
            return len(text) // 4

        def truncate_tool_output(output: str, tool_name: str = "unknown", max_chars: int = 4000) -> str:
            """Smart compaction of tool output to prevent context bloat.

            Uses semantic compaction instead of raw truncation:
            - Element lists are grouped and summarized
            - Full data stored externally with reference IDs
            - Base64 data detected and replaced with reference

            Args:
                output: The tool output string
                tool_name: Name of the tool (for routing to specific compactors)
                max_chars: Maximum characters to keep (default 4000 = ~1000 tokens)

            Returns:
                Compacted output preserving semantic meaning
            """
            from app.agents.coordinator.context_compactor import compact_tool_output
            return compact_tool_output(output, tool_name, max_chars)

        def get_context_stats(history: List[AgentMessage], current_msg: str) -> Dict[str, Any]:
            """Calculate context statistics"""
            # Access Pydantic model attributes directly, not via .get()
            total_chars = sum(len(m.content) for m in history) + len(current_msg)
            return {
                "message_count": len(history) + 1,
                "total_chars": total_chars,
                "estimated_tokens": estimate_tokens(str(history) + current_msg)
            }

        if not self.api_key:
            yield {"type": "error", "content": "Error: OpenAI API key not configured"}
            return

        try:
            # Get the last user message
            user_message = messages[-1].content if messages else ""

            # Build conversation history for context
            # Convert ChatMessage list to Pydantic AgentMessage models
            conversation_history: List[AgentMessage] = []
            for msg in messages[:-1]:  # All messages except the last one
                conversation_history.append(
                    AgentMessage(role=msg.role, content=msg.content)
                )

            # Initialize full context history for visibility
            # Start with conversation history + current user message (as Pydantic models)
            full_context: List[AgentMessage] = [
                AgentMessage(role=msg.role, content=msg.content)
                for msg in messages[:-1]
            ]
            full_context.append(AgentMessage(role="user", content=user_message))

            # Initialize running context stats
            # We'll track the accumulated characters to estimate tokens dynamically
            # Access Pydantic model attributes directly, not via .get()
            initial_chars = sum(len(m.content) for m in conversation_history) + len(user_message)
            running_context = {
                "message_count": len(conversation_history) + 1,
                "total_chars": initial_chars,
                "estimated_tokens": estimate_tokens(str(conversation_history) + user_message)
            }

            def update_running_context(new_content: str, is_new_message: bool = False):
                """Update the running context stats with new content"""
                nonlocal running_context
                running_context["total_chars"] += len(new_content)
                # Add 4 chars overhead per message/turn approx
                running_context["estimated_tokens"] += estimate_tokens(new_content)
                if is_new_message:
                    running_context["message_count"] += 1
                return running_context

            # Create or resume agent session for tracking
            if resume_session_id:
                session_id = resume_session_id
                logger.info(f"Resuming existing session: {session_id}")
            else:
                session_id = await self._create_agent_session(user_message)
            step_counter = 0

            # Emit session ID to frontend
            if session_id:
                yield {"type": "session_created", "session_id": session_id}

            # Emit initial context info
            yield {
                "type": "context_info",
                "stats": running_context
            }

            # Build UI context information for the system instructions
            ui_context_info = _build_ui_context_info(ui_context)

            # Standard multi-agent orchestration flow


            # Create function tools using modularized tools (same as chat method)
            service_ref = self

            # Create simulation tools with service reference
            simulation_tools = create_simulation_tools(service_ref)

            # Create specialized agents (same as chat method)
            scenarios = self.get_available_scenarios()

            # 1. Search Agent
            search_agent = create_search_agent(
                vector_search_service=self.vector_search,
                test_scenarios=scenarios
            )

            # 2. Test Generation Agent - handles test generation and analysis
            test_generation_tools = create_test_generation_tools(self)
            test_generation_agent = create_test_generation_agent(
                generate_test_code_func=test_generation_tools['generate_test_code'],
                list_test_scenarios_func=test_generation_tools['list_test_scenarios'],
                analyze_coverage_func=test_generation_tools['analyze_coverage'],
                available_scenarios=scenarios
            )

            # 3. Device Testing Agent - unified agent for test execution, bug reproduction, exploration, and autonomous navigation
            # Ensure Mobile MCP is started for autonomous navigation tools
            await self._ensure_mobile_mcp_started()

            # Dynamically discover first available device instead of hardcoding
            default_device = await self._get_first_available_device()
            logger.info(f"Using default device: {default_device}")

            # Track current device for auto-screenshots; update from tool args when available
            current_device_id = default_device

            # Create autonomous navigation tools
            autonomous_nav_tools = create_autonomous_navigation_tools(
                self.mobile_mcp_client,
                default_device
            )
            
            # Create Agentic Vision tools (GenAI Gemini 3 Flash capabilities)
            agentic_vision_tools = create_agentic_vision_tools(
                self.mobile_mcp_client,
                default_device
            )

            # Create device testing tools
            device_testing_tools = create_device_testing_tools(self)

            # Create OAVR sub-agents for device testing
            screen_classifier_agent = create_screen_classifier_agent()
            action_verifier_agent = create_action_verifier_agent()
            failure_diagnosis_agent = create_failure_diagnosis_agent()

            # Create unified device testing agent with autonomous navigation capabilities and OAVR sub-agents
            device_testing_agent = create_device_testing_agent(
                # Device discovery
                list_available_devices_func=device_testing_tools['list_available_devices'],
                # Test execution & bug reproduction
                execute_test_scenario_func=device_testing_tools['execute_test_scenario'],
                reproduce_bug_func=device_testing_tools['reproduce_bug'],
                get_execution_status_func=device_testing_tools['get_execution_status'],
                # Exploration
                start_autonomous_exploration_func=device_testing_tools['start_autonomous_exploration'],
                get_exploration_report_func=device_testing_tools['get_exploration_report'],
                list_explorations_func=device_testing_tools['list_explorations'],
                # Device control
                find_elements_on_device_func=device_testing_tools['find_elements_on_device'],
                click_element_by_text_func=device_testing_tools['click_element_by_text'],
                execute_device_action_func=device_testing_tools['execute_device_action'],
                # Golden bug evaluation
                list_golden_bugs_func=device_testing_tools['list_golden_bugs'],
                run_golden_bug_func=device_testing_tools['run_golden_bug'],
                run_all_golden_bugs_func=device_testing_tools['run_all_golden_bugs'],
                # Autonomous navigation (Mobile MCP direct access)
                list_elements_on_screen_func=autonomous_nav_tools['list_elements_on_screen'],
                take_screenshot_func=autonomous_nav_tools['take_screenshot'],
                click_at_coordinates_func=autonomous_nav_tools['click_at_coordinates'],
                type_text_func=autonomous_nav_tools['type_text'],
                swipe_on_screen_func=autonomous_nav_tools['swipe_on_screen'],
                press_button_func=autonomous_nav_tools['press_button'],
                launch_app_func=autonomous_nav_tools['launch_app'],
                list_apps_func=autonomous_nav_tools['list_apps'],
                get_screen_size_func=autonomous_nav_tools['get_screen_size'],
                vision_click_func=autonomous_nav_tools['vision_click'],
                # Parallel multi-device tools (for concurrent device control)
                take_screenshots_parallel_func=autonomous_nav_tools['take_screenshots_parallel'],
                list_elements_parallel_func=autonomous_nav_tools['list_elements_parallel'],
                execute_parallel_actions_func=autonomous_nav_tools['execute_parallel_actions'],
                # Agentic Vision tools (Gemini 3 Flash)
                zoom_and_inspect_func=agentic_vision_tools.get('zoom_and_inspect'),
                annotate_screen_func=agentic_vision_tools.get('annotate_screen'),
                visual_math_func=agentic_vision_tools.get('visual_math'),
                multi_step_vision_func=agentic_vision_tools.get('multi_step_vision'),
                # OAVR sub-agents
                screen_classifier_agent=screen_classifier_agent,
                action_verifier_agent=action_verifier_agent,
                failure_diagnosis_agent=failure_diagnosis_agent,
                # Context
                available_scenarios=scenarios
            )

            # Create QA emulation agent with parallel subagents
            # reasoning_effort="high" is explicit so callers can override in the future
            qa_emulation_agent = create_qa_emulation_agent(
                prompt_version="v12",
                bug_detection_agent=create_bug_detection_agent(reasoning_effort="high"),
                anomaly_detection_agent=create_anomaly_detection_agent(),
                verdict_assembly_agent=create_verdict_assembly_agent(reasoning_effort="high"),
                reasoning_effort="high",
            )

            # Create self-test flywheel agent
            from ..self_testing import create_flywheel_tools, create_self_test_agent
            flywheel_tools = create_flywheel_tools()
            self_test_agent = create_self_test_agent(flywheel_tools)

            # Create coordinator agent using factory (hierarchical multi-agent pattern)
            # Note: autonomous_nav_agent removed - device_testing_agent now handles autonomous navigation
            coordinator = create_coordinator_agent(
                search_agent=search_agent,
                test_generation_agent=test_generation_agent,
                device_testing_agent=device_testing_agent,
                scenarios=scenarios,
                ui_context_info=ui_context_info,
                execute_simulation_func=simulation_tools['execute_simulation'],
                qa_emulation_agent=qa_emulation_agent,
                self_test_agent=self_test_agent,
            )

            # Create Manus Coordinator (Ultra-agent)
            manus_coordinator = create_manus_coordinator(
                service_ref=self,
                ui_context_info=ui_context_info
            )

            # Choose coordinator based on prompt or context
            # Direct routing: self-test requests go straight to Self-Test Specialist
            # (bypasses coordinator to avoid LLM non-determinism in handoff decisions)
            SELF_TEST_KEYWORDS = ['test my app', 'self-test', 'test our app', 'test the app',
                                  'test your app', 'test this app', 'run self-test', 'self test',
                                  'test at http', 'test at localhost', 'test http']
            is_self_test_request = any(kw in user_message.lower() for kw in SELF_TEST_KEYWORDS)

            # If user mentions 'manus', 'build', 'chef', use ManusCoordinator
            is_manus_request = any(kw in user_message.lower() for kw in ['manus', 'chef', 'build app', 'generate app', 'toaster'])

            if is_self_test_request:
                active_coordinator = self_test_agent
                logger.info(f"⚡ Direct routing to Self-Test Specialist (keyword match)")
            elif is_manus_request:
                active_coordinator = manus_coordinator
            else:
                active_coordinator = coordinator
            logger.info(f"Using active coordinator: {active_coordinator.name}")

            # Pre-flight check whether MCP server is reachable for streaming; fall back if not
            mcp_ok = False
            if MCP_AVAILABLE:
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=2.0) as client:
                        await client.get(self.mcp_server_url)
                        mcp_ok = True
                except Exception as mcp_probe_err:
                    logger.warning(f"MCP server probe failed for streaming; continuing without MCP: {mcp_probe_err}")

            if mcp_ok and MCP_AVAILABLE:
                async with MCPServerStreamableHttp(
                    name="Appium MCP Server",
                    params={
                        "url": self.mcp_server_url,
                        "timeout": 10,
                    },
                    cache_tools_list=True,
                    max_retry_attempts=1,
                ) as mcp_server:
                    # Run coordinator with MCP server available (streaming)
                    # Increase max_turns for complex autonomous navigation tasks (up to 100 steps)
                    # Pass conversation history + current message for context retention
                    # Convert Pydantic models to dicts for Runner
                    history_dicts = [msg.model_dump() for msg in conversation_history]
                    agent_input = history_dicts + [{"role": "user", "content": user_message}] if history_dicts else user_message
                    result = Runner.run_streamed(active_coordinator, agent_input, max_turns=1000)

                    # Track tool names by call_id (tool_call_output_item doesn't have name, only call_id)
                    tool_call_names = {}
                    current_agent_name = "Agent"

                    async for event in result.stream_events():
                        # Track current agent from handoff events
                        if hasattr(event, 'agent') and hasattr(event.agent, 'name'):
                            current_agent_name = event.agent.name

                        # Handle different event types
                        if event.type == "run_item_stream_event":
                            # Check if this is a tool call
                            item = event.item
                            if hasattr(item, 'type'):
                                if item.type == 'tool_call_output_item':
                                    # Tool call completed - extract tool name from tracked names
                                    tool_name = 'unknown'
                                    tool_output = ''
                                    call_id = None

                                    # Get call_id from raw_item
                                    if hasattr(item, 'raw_item'):
                                        raw = item.raw_item
                                        if isinstance(raw, dict) and 'call_id' in raw:
                                            call_id = raw['call_id']
                                            # Look up the tool name we saved earlier
                                            tool_name = tool_call_names.get(call_id, 'unknown')
                                        elif hasattr(raw, 'call_id'):
                                            call_id = raw.call_id
                                            tool_name = tool_call_names.get(call_id, 'unknown')

                                    # Get output
                                    if hasattr(item, 'output'):
                                        tool_output = item.output
                                    else:
                                        tool_output = str(item)

                                    # Emit handoff events to show sub-agent interactions
                                    if tool_name.startswith('transfer_to_') or tool_name.startswith('handoff_to_'):
                                        # Extract target agent name from tool name
                                        target_agent = tool_name.replace('transfer_to_', '').replace('handoff_to_', '').replace('_', ' ').title()
                                        yield {
                                            "type": "handoff",
                                            "from_agent": current_agent_name,
                                            "to_agent": target_agent,
                                            "status": "completed",
                                            "output": tool_output
                                        }
                                    else:
                                        # Log step to session
                                        step_counter += 1
                                        screenshot_url = self._extract_screenshot_url(tool_output)
                                        if (not screenshot_url) and getattr(self, "auto_screenshot_every_step", False) and tool_name != "take_screenshot":
                                            try:
                                                auto_output = await autonomous_nav_tools['take_screenshot'](current_device_id)
                                                auto_url = self._extract_screenshot_url(auto_output)
                                                if auto_url:
                                                    screenshot_url = auto_url
                                            except Exception as e:
                                                logger.warning(f"Auto-screenshot after {tool_name} failed: {e}")
                                        await self._add_session_step(
                                            session_id=session_id,
                                            step_number=step_counter,
                                            description=f"{current_agent_name}: {tool_name}",
                                            command=tool_name,
                                            action=f"Executed {tool_name}",
                                            observation=str(tool_output)[:500] if tool_output else "No output",
                                            results="Completed successfully",
                                            status="success",
                                            screenshot=screenshot_url,
                                        )

                                        # Update context with output (global stats)
                                        # Smart compaction to prevent context bloat (preserves semantic meaning)
                                        truncated_output = truncate_tool_output(tool_output, tool_name)
                                        current_stats = update_running_context(truncated_output, is_new_message=False)

                                        # Add TRUNCATED tool output to full context (for trace / step-level details)
                                        full_context.append({
                                            "role": "tool",
                                            "name": tool_name,
                                            "content": truncated_output
                                        })

                                        # Emit step-level tool_call event (used for ActionCard / trace)
                                        # Note: We send truncated output to prevent SSE bloat
                                        yield {
                                            "type": "tool_call",
                                            "agent_name": current_agent_name,
                                            "tool_name": tool_name,
                                            "tool_output": truncated_output,
                                            "status": "completed",
                                            "context_stats": current_stats
                                            # Removed full_context from event to reduce SSE payload size
                                        }

                                        # Also emit updated global context snapshot for HUD / AgentBrainCard
                                        yield {
                                            "type": "context_info",
                                            "stats": current_stats
                                        }
                                elif item.type == 'tool_call_item':
                                    # Tool call started - save the tool name by call_id for later
                                    tool_name = 'unknown'
                                    tool_input = '{}'
                                    call_id = None

                                    # Try to get tool name and call_id from raw_item (ToolCallItem has raw_item attribute)
                                    if hasattr(item, 'raw_item'):
                                        raw = item.raw_item
                                        # raw_item could be a Pydantic model or dict
                                        if hasattr(raw, 'name'):
                                            tool_name = raw.name
                                            if hasattr(raw, 'arguments'):
                                                tool_input = raw.arguments
                                            if hasattr(raw, 'call_id'):
                                                call_id = raw.call_id
                                        elif isinstance(raw, dict):
                                            # OpenAI format: function.name and function.arguments
                                            if 'function' in raw:
                                                func = raw['function']
                                                if isinstance(func, dict):
                                                    tool_name = func.get('name', 'unknown')
                                                    tool_input = func.get('arguments', '{}')
                                            # Direct format
                                            elif 'name' in raw:
                                                tool_name = raw['name']
                                                tool_input = raw.get('arguments', '{}')
                                            # Get call_id
                                            if 'call_id' in raw:
                                                call_id = raw['call_id']

                                    # Also try to get from item directly (for handoff tools)
                                    if hasattr(item, 'name'):
                                        tool_name = item.name
                                    if hasattr(item, 'arguments'):
                                        tool_input = item.arguments
                                    if hasattr(item, 'call_id'):
                                        call_id = item.call_id

                                    # Save the tool name for this call_id
                                    if call_id:
                                        tool_call_names[call_id] = tool_name

                                    # Try to update current device from tool arguments
                                    try:
                                        args_obj = json.loads(tool_input) if isinstance(tool_input, str) else tool_input
                                        if isinstance(args_obj, dict):
                                            did = args_obj.get('device_id') or args_obj.get('deviceId')
                                            if isinstance(did, str) and did:
                                                current_device_id = did
                                    except Exception:
                                        pass

                                    # Emit handoff events to show sub-agent interactions
                                    if tool_name.startswith('transfer_to_') or tool_name.startswith('handoff_to_'):
                                        # Extract target agent name from tool name
                                        target_agent = tool_name.replace('transfer_to_', '').replace('handoff_to_', '').replace('_', ' ').title()
                                        yield {
                                            "type": "handoff",
                                            "from_agent": current_agent_name,
                                            "to_agent": target_agent,
                                            "status": "started",
                                            "input": tool_input
                                        }
                                    else:
                                        # Log clean agent action
                                        logger.info(f"🤖 Agent Action: {tool_name}")
                                        
                                        # Add tool call to full context
                                        full_context.append({
                                            "role": "assistant", 
                                            "content": None, 
                                            "tool_calls": [{
                                                "function": {
                                                    "name": tool_name,
                                                    "arguments": tool_input
                                                }
                                            }]
                                        })

                                        yield {
                                            "type": "tool_call",
                                            "agent_name": current_agent_name,
                                            "tool_name": tool_name,
                                            "tool_input": tool_input,
                                            "status": "started",
                                            "context_stats": update_running_context(tool_input, is_new_message=True)
                                            # Removed full_context from event to reduce SSE payload size
                                        }
                                elif item.type == 'handoff_call_item':
                                    # Handoff to another agent - skip, we track agent name separately
                                    pass
                                elif item.type == 'message_item':
                                    # Regular message content
                                    if hasattr(item, 'content'):
                                        content = item.content
                                        if isinstance(content, list):
                                            # Extract text from content array
                                            for part in content:
                                                if hasattr(part, 'text'):
                                                    logger.info(f"💬 Agent: {part.text[:100]}...")
                                                    yield {"type": "content", "content": part.text}
                                                    # Update global context stats and emit updated snapshot
                                                    current_stats = update_running_context(part.text, is_new_message=False)
                                                    yield {"type": "context_info", "stats": current_stats}
                                        elif isinstance(content, str):
                                            logger.info(f"💬 Agent: {content[:100]}...")
                                            yield {"type": "content", "content": content}
                                            # Update global context stats and emit updated snapshot
                                            current_stats = update_running_context(content, is_new_message=False)
                                            yield {"type": "context_info", "stats": current_stats}
                                            # Note: We don't append partial content to full_context here to avoid spam
                                            # We should ideally append the full message when it's done, but for now
                                            # we rely on the tool calls/outputs which are the main context drivers.
                                            # If we want to track assistant text replies, we'd need to buffer them.
                                else:
                                    # Skip other item types (handoff, etc.)
                                    pass
                            else:
                                # Item without type - skip
                                pass
                    # Update session status to completed
                    await self._update_session_status(session_id, "completed")

                    # Extract token usage from result.context_wrapper.usage (OpenAI Agents SDK standard)
                    if hasattr(result, 'context_wrapper') and hasattr(result.context_wrapper, 'usage'):
                        usage = result.context_wrapper.usage
                        input_tokens = getattr(usage, 'input_tokens', 0) or 0
                        output_tokens = getattr(usage, 'output_tokens', 0) or 0
                        total_tokens = getattr(usage, 'total_tokens', 0) or (input_tokens + output_tokens)
                        requests = getattr(usage, 'requests', 0) or 0

                        # Update session with token totals
                        await self._update_session_tokens(
                            session_id=session_id,
                            total_request_tokens=input_tokens,
                            total_response_tokens=output_tokens,
                            total_tokens=total_tokens
                        )

                        logger.info(f"Session {session_id} token usage - Requests: {requests}, Input: {input_tokens}, Output: {output_tokens}, Total: {total_tokens}")

                    yield {"type": "final", "content": result.final_output}

                    # Generate and emit report card (scores vs golden sessions)
                    if session_id and result.final_output:
                        report_card = await self._generate_report_card(session_id, result.final_output)
                        if report_card:
                            yield {"type": "report_card", "report_card": report_card}
                return

            # Fallback: run coordinator without MCP servers (same hierarchical structure)
            # Increase max_turns for complex autonomous navigation tasks (up to 100 steps)
            # Pass conversation history + current message for context retention
            # Convert Pydantic models to dicts for Runner
            history_dicts = [msg.model_dump() for msg in conversation_history]
            agent_input = history_dicts + [{"role": "user", "content": user_message}] if history_dicts else user_message
            result = Runner.run_streamed(coordinator, agent_input, max_turns=1000)

            # Track tool names by call_id (tool_call_output_item doesn't have name, only call_id)
            tool_call_names_fallback = {}
            current_agent_name_fallback = "Agent"

            async for event in result.stream_events():
                # Track current agent from handoff events
                if hasattr(event, 'agent') and hasattr(event.agent, 'name'):
                    current_agent_name_fallback = event.agent.name

                # Handle different event types
                if event.type == "run_item_stream_event":
                    # Check if this is a tool call
                    item = event.item
                    if hasattr(item, 'type'):
                        if item.type == 'tool_call_output_item':
                            # Tool call completed - extract tool name from tracked names
                            tool_name = 'unknown'
                            tool_output = ''
                            call_id = None

                            # Get call_id from raw_item
                            if hasattr(item, 'raw_item'):
                                raw = item.raw_item
                                if isinstance(raw, dict) and 'call_id' in raw:
                                    call_id = raw['call_id']
                                    # Look up the tool name we saved earlier
                                    tool_name = tool_call_names_fallback.get(call_id, 'unknown')
                                elif hasattr(raw, 'call_id'):
                                    call_id = raw.call_id
                                    tool_name = tool_call_names_fallback.get(call_id, 'unknown')

                            # Get output
                            if hasattr(item, 'output'):
                                tool_output = item.output
                            else:
                                tool_output = str(item)

                            # Skip handoff/transfer tools (they're internal coordination)
                            if tool_name.startswith('transfer_to_') or tool_name.startswith('handoff_to_'):
                                pass  # Don't display handoff tool calls
                            else:
                                # Log step to session
                                step_counter += 1
                                screenshot_url = self._extract_screenshot_url(tool_output)
                                if (not screenshot_url) and getattr(self, "auto_screenshot_every_step", False) and tool_name != "take_screenshot":
                                    try:
                                        auto_output = await autonomous_nav_tools['take_screenshot'](current_device_id)
                                        auto_url = self._extract_screenshot_url(auto_output)
                                        if auto_url:
                                            screenshot_url = auto_url
                                    except Exception as e:
                                        logger.warning(f"Auto-screenshot after {tool_name} failed: {e}")
                                await self._add_session_step(
                                    session_id=session_id,
                                    step_number=step_counter,
                                    description=f"{current_agent_name_fallback}: {tool_name}",
                                    command=tool_name,
                                    action=f"Executed {tool_name}",
                                    observation=str(tool_output)[:500] if tool_output else "No output",
                                    results="Completed successfully",
                                    status="success",
                                    screenshot=screenshot_url,
                                )

                                # Smart compaction to prevent context bloat (preserves semantic meaning)
                                truncated_output = truncate_tool_output(tool_output, tool_name)
                                current_stats = update_running_context(truncated_output, is_new_message=False)

                                # Add TRUNCATED tool output to full context (for trace / step-level details)
                                full_context.append({
                                    "role": "tool",
                                    "name": tool_name,
                                    "content": truncated_output
                                })

                                # Emit step-level tool_call event (used for ActionCard / trace)
                                # Note: We send truncated output to prevent SSE bloat
                                yield {
                                    "type": "tool_call",
                                    "agent_name": current_agent_name_fallback,
                                    "tool_name": tool_name,
                                    "tool_output": truncated_output,
                                    "status": "completed",
                                    "context_stats": current_stats
                                    # Removed full_context from event to reduce SSE payload size
                                }

                                # Also emit updated global context snapshot for HUD / AgentBrainCard
                                yield {
                                    "type": "context_info",
                                    "stats": current_stats
                                }
                        elif item.type == 'tool_call_item':
                            # Tool call started - save the tool name by call_id for later
                            tool_name = 'unknown'
                            tool_input = '{}'
                            call_id = None

                            # Try to get tool name and call_id from raw_item (ToolCallItem has raw_item attribute)
                            if hasattr(item, 'raw_item'):
                                raw = item.raw_item
                                # raw_item could be a Pydantic model or dict
                                if hasattr(raw, 'name'):
                                    tool_name = raw.name
                                    if hasattr(raw, 'arguments'):
                                        tool_input = raw.arguments
                                    if hasattr(raw, 'call_id'):
                                        call_id = raw.call_id
                                elif isinstance(raw, dict):
                                    # OpenAI format: function.name and function.arguments
                                    if 'function' in raw:
                                        func = raw['function']
                                        if isinstance(func, dict):
                                            tool_name = func.get('name', 'unknown')
                                            tool_input = func.get('arguments', '{}')
                                    # Direct format
                                    elif 'name' in raw:
                                        tool_name = raw['name']
                                        tool_input = raw.get('arguments', '{}')
                                    # Get call_id
                                    if 'call_id' in raw:
                                        call_id = raw['call_id']

                            # Also try to get from item directly (for handoff tools)
                            if hasattr(item, 'name'):
                                tool_name = item.name
                            if hasattr(item, 'arguments'):
                                tool_input = item.arguments
                            if hasattr(item, 'call_id'):
                                call_id = item.call_id

                            # Save the tool name for this call_id
                            if call_id:
                                tool_call_names_fallback[call_id] = tool_name

                            # Try to update current device from tool arguments
                            try:
                                args_obj = json.loads(tool_input) if isinstance(tool_input, str) else tool_input
                                if isinstance(args_obj, dict):
                                    did = args_obj.get('device_id') or args_obj.get('deviceId')
                                    if isinstance(did, str) and did:
                                        current_device_id = did
                            except Exception:
                                pass

                            # Skip handoff/transfer tools (they're internal coordination)
                            if tool_name.startswith('transfer_to_') or tool_name.startswith('handoff_to_'):
                                pass  # Don't display handoff tool calls
                            else:
                                # Add tool call to full context
                                full_context.append({
                                    "role": "assistant", 
                                    "content": None, 
                                    "tool_calls": [{
                                        "function": {
                                            "name": tool_name,
                                            "arguments": tool_input
                                        }
                                    }]
                                })

                                yield {
                                    "type": "tool_call",
                                    "agent_name": current_agent_name_fallback,
                                    "tool_name": tool_name,
                                    "tool_input": tool_input,
                                    "status": "started",
                                    "context_stats": update_running_context(tool_input, is_new_message=True)
                                    # Removed full_context from event to reduce SSE payload size
                                }
                        elif item.type == 'handoff_call_item':
                            # Handoff to another agent - skip, we track agent name separately
                            pass
                        elif item.type == 'message_item':
                            # Regular message content
                            if hasattr(item, 'content'):
                                content = item.content
                                if isinstance(content, list):
                                    # Extract text from content array
                                    for part in content:
                                        if hasattr(part, 'text'):
                                            yield {"type": "content", "content": part.text}
                                            # Update global context stats and emit updated snapshot
                                            current_stats = update_running_context(part.text, is_new_message=False)
                                            yield {"type": "context_info", "stats": current_stats}
                                elif isinstance(content, str):
                                    yield {"type": "content", "content": content}
                                    # Update global context stats and emit updated snapshot
                                    current_stats = update_running_context(content, is_new_message=False)
                                    yield {"type": "context_info", "stats": current_stats}
                        else:
                            # Skip other item types (handoff, etc.)
                            pass
                    else:
                        # Item without type - skip
                        pass
            # Update session status to completed
            await self._update_session_status(session_id, "completed")

            # Extract token usage from result.context_wrapper.usage (OpenAI Agents SDK standard)
            if hasattr(result, 'context_wrapper') and hasattr(result.context_wrapper, 'usage'):
                usage = result.context_wrapper.usage
                input_tokens = getattr(usage, 'input_tokens', 0) or 0
                output_tokens = getattr(usage, 'output_tokens', 0) or 0
                total_tokens = getattr(usage, 'total_tokens', 0) or (input_tokens + output_tokens)
                requests = getattr(usage, 'requests', 0) or 0

                # Update session with token totals
                await self._update_session_tokens(
                    session_id=session_id,
                    total_request_tokens=input_tokens,
                    total_response_tokens=output_tokens,
                    total_tokens=total_tokens
                )

                logger.info(f"Session {session_id} token usage - Requests: {requests}, Input: {input_tokens}, Output: {output_tokens}, Total: {total_tokens}")

            yield {"type": "final", "content": result.final_output}

            # Generate and emit report card (scores vs golden sessions)
            if session_id and result.final_output:
                report_card = await self._generate_report_card(session_id, result.final_output)
                if report_card:
                    yield {"type": "report_card", "report_card": report_card}

        except Exception as e:
            import traceback
            import re
            error_details = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "traceback": traceback.format_exc()
            }
            logger.error(f"❌ CRITICAL ERROR in chat_stream:")
            logger.error(f"   Error Type: {error_details['error_type']}")
            logger.error(f"   Error Message: {error_details['error_message']}")
            logger.error(f"   Full Traceback:\n{error_details['traceback']}")

            # Write to file for debugging
            try:
                with open("agent_error_log.txt", "a") as f:
                    import datetime
                    f.write(f"\n{'='*80}\n")
                    f.write(f"Error at {datetime.datetime.now()}\n")
                    f.write(f"Error Type: {error_details['error_type']}\n")
                    f.write(f"Error Message: {error_details['error_message']}\n")
                    f.write(f"Full Traceback:\n{error_details['traceback']}\n")
                    f.write(f"{'='*80}\n")
            except (OSError, IOError):
                pass

            # Check if this is a rate limit error
            is_rate_limit = False
            retry_after_ms = None
            error_msg = str(e).lower()

            if "rate limit" in error_msg or error_details['error_type'] == 'RateLimitError':
                is_rate_limit = True
                # Try to extract retry time from error message
                # Example: "Please try again in 292ms"
                match = re.search(r'try again in (\d+)ms', str(e))
                if match:
                    retry_after_ms = int(match.group(1))
                else:
                    # Default to 60 seconds if not specified
                    retry_after_ms = 60000

            # Update session status and save conversation history for resuming
            if 'session_id' in locals():
                if is_rate_limit:
                    # Save conversation history for resuming
                    if 'full_context' in locals():
                        await self._save_conversation_history(session_id, full_context)

                    # Update session status to paused with error details
                    try:
                        async with httpx.AsyncClient() as client:
                            await client.patch(
                                f"{self._backend_url}/api/ai-agent/sessions/{session_id}/status",
                                json={
                                    "status": "paused",
                                    "lastError": str(e),
                                    "retryAfterMs": retry_after_ms
                                },
                                timeout=5.0
                            )
                    except Exception as update_err:
                        logger.error(f"Failed to update session status: {update_err}")
                else:
                    await self._update_session_status(session_id, "failed")

            if is_rate_limit:
                yield {
                    "type": "rate_limit",
                    "content": str(e),
                    "retry_after_ms": retry_after_ms
                }
            else:
                yield {"type": "error", "content": str(e)}

    def get_coordinator_agent(self):
        """
        Get or create the coordinator agent for visualization.

        Returns:
            Agent: The coordinator agent instance
        """
        if self._coordinator_agent_cache is not None:
            return self._coordinator_agent_cache

        # Create specialized agents
        scenarios = self.get_available_scenarios()

        # Create search agent
        search_agent = create_search_agent(
            vector_search_service=self.vector_search,
            test_scenarios=scenarios
        )

        # Create test generation agent with tools
        test_generation_tools = create_test_generation_tools(self)
        test_generation_agent = create_test_generation_agent(
            generate_test_code_func=test_generation_tools['generate_test_code'],
            list_test_scenarios_func=test_generation_tools['list_test_scenarios'],
            analyze_coverage_func=test_generation_tools['analyze_coverage'],
            available_scenarios=scenarios
        )

        # Create device testing agent with all tools
        device_testing_tools = create_device_testing_tools(self)

        # Create OAVR sub-agents for device testing
        screen_classifier_agent = create_screen_classifier_agent()
        action_verifier_agent = create_action_verifier_agent()
        failure_diagnosis_agent = create_failure_diagnosis_agent()

        device_testing_agent = create_device_testing_agent(
            # Device discovery
            list_available_devices_func=device_testing_tools['list_available_devices'],
            # Test execution & bug reproduction
            execute_test_scenario_func=device_testing_tools['execute_test_scenario'],
            reproduce_bug_func=device_testing_tools['reproduce_bug'],
            get_execution_status_func=device_testing_tools['get_execution_status'],
            # Exploration
            start_autonomous_exploration_func=device_testing_tools['start_autonomous_exploration'],
            get_exploration_report_func=device_testing_tools['get_exploration_report'],
            list_explorations_func=device_testing_tools['list_explorations'],
            # Device control
            find_elements_on_device_func=device_testing_tools['find_elements_on_device'],
            click_element_by_text_func=device_testing_tools['click_element_by_text'],
            execute_device_action_func=device_testing_tools['execute_device_action'],
            # Golden bug evaluation
            list_golden_bugs_func=device_testing_tools['list_golden_bugs'],
            run_golden_bug_func=device_testing_tools['run_golden_bug'],
            run_all_golden_bugs_func=device_testing_tools['run_all_golden_bugs'],
            # OAVR sub-agents
            screen_classifier_agent=screen_classifier_agent,
            action_verifier_agent=action_verifier_agent,
            failure_diagnosis_agent=failure_diagnosis_agent,
            # Context
            available_scenarios=scenarios
        )

        # Create simulation tools
        simulation_tools = create_simulation_tools(self)

        # Create QA emulation agent with parallel subagents
        qa_emulation_agent = create_qa_emulation_agent(
            prompt_version="v12",
            bug_detection_agent=create_bug_detection_agent(),
            anomaly_detection_agent=create_anomaly_detection_agent(),
            verdict_assembly_agent=create_verdict_assembly_agent(),
        )

        # Create coordinator agent
        coordinator = create_coordinator_agent(
            search_agent=search_agent,
            test_generation_agent=test_generation_agent,
            device_testing_agent=device_testing_agent,
            scenarios=scenarios,
            ui_context_info=None,  # No UI context for visualization
            execute_simulation_func=simulation_tools['execute_simulation'],
            qa_emulation_agent=qa_emulation_agent,
        )

        # Cache it
        self._coordinator_agent_cache = coordinator

        return coordinator

