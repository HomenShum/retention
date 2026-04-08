"""
Coordinator Agent - Hierarchical Multi-Agent Orchestrator

This agent analyzes user intent and delegates to specialized agents:
- Search Assistant (bug reports and test scenarios)
- Test Generation Specialist (test generation and analysis)
- Device Testing Specialist (test execution, bug reproduction, exploration, device discovery, autonomous navigation)

The coordinator also has direct access to the launch_emulators tool for quick emulator management.

GPT-5.4 Features (Mar 2026):
- Dynamic Handoffs: Uses is_enabled callbacks for context-aware agent routing
- Reasoning Effort: High thinking budget for complex orchestration (xhigh available)
- Native Computer-Use: Built-in screen interaction capabilities
- Verbosity Control: Balanced output for coordination tasks
"""

import logging
from typing import Any
from agents import Agent, AgentBase, function_tool, handoff, RunContextWrapper
from agents.model_settings import ModelSettings
from openai.types.shared import Reasoning

from .coordinator_instructions import create_coordinator_instructions
from .context_tools import create_context_tools
from ..model_fallback import get_model_fallback_chain
from ..device_testing.tools.device_testing_tools import create_device_testing_tools
from ..figma.tools.figma_tools import create_figma_tools

logger = logging.getLogger(__name__)


# =============================================================================
# DYNAMIC HANDOFF CALLBACKS (GPT-5.4 Feature: is_enabled)
# =============================================================================
# Agent routing: all agents are always enabled. The LLM decides which to use,
# guided by deterministic routing score hints injected into the system prompt.
# This replaces the previous keyword-matching is_enabled callbacks (claw-code pattern).
#
# See routing_score.py for the token-overlap scoring module.

from app.agents.coordinator.routing_score import compute_all_scores, format_routing_hint


def _get_last_user_message(ctx: RunContextWrapper[Any]) -> str:
    """Extract the last user message from the context for routing score computation."""
    try:
        if hasattr(ctx, 'input') and ctx.input:
            if isinstance(ctx.input, str):
                return ctx.input
            elif isinstance(ctx.input, list):
                for item in reversed(ctx.input):
                    if hasattr(item, 'role') and item.role == 'user':
                        if hasattr(item, 'content'):
                            return str(item.content)
        return ""
    except Exception:
        return ""


def _always_enabled(ctx: RunContextWrapper[Any], agent: AgentBase) -> bool:
    """All agents are always enabled — the LLM decides with routing score hints."""
    return True


# Deep Agents Pattern: Planning Tool (No-op for context engineering)
def plan_task(task_description: str, subtasks: list[str]) -> str:
    """
    Planning tool for breaking down complex tasks into subtasks.

    Deep Agent Pattern: This is a no-op tool (like Claude Code's Todo list) that helps
    the agent maintain focus and plan over longer time horizons. It doesn't execute
    anything - it's purely for context engineering and keeping the agent on track.

    Args:
        task_description: High-level description of the task
        subtasks: List of subtasks to accomplish the goal

    Returns:
        Confirmation message
    """
    subtask_list = "\n".join(f"  {i+1}. {task}" for i, task in enumerate(subtasks))
    return f"""✅ Task plan created:

Task: {task_description}

Subtasks:
{subtask_list}

This plan will guide execution. Proceed with the subtasks in order."""


def create_coordinator_agent(
    search_agent: Agent,
    test_generation_agent: Agent,
    device_testing_agent: Agent,
    scenarios: list = None,
    ui_context_info: str = "",
    execute_simulation_func=None,
    qa_emulation_agent: Agent = None,
    self_test_agent: Agent = None,
) -> Agent:
    """
    Create the coordinator agent with handoffs to specialized agents.

    This agent uses the hierarchical multi-agent pattern where it analyzes
    user intent and delegates to the appropriate specialist agent.

    Args:
        search_agent: Search Assistant agent
        test_generation_agent: Test Generation Specialist agent
        device_testing_agent: Device Testing Specialist agent (unified test execution, bug reproduction, exploration, device discovery, autonomous navigation)
        scenarios: List of available test scenarios
        ui_context_info: Optional UI context information
        execute_simulation_func: Optional function to execute multi-device simulations
        qa_emulation_agent: Optional QA Emulation agent for bug reproduction workflows

    Returns:
        Configured coordinator agent
    """

    # Create coordinator instructions
    instructions = create_coordinator_instructions(scenarios or [], ui_context_info)

    # All agents always enabled — LLM decides with routing score hints (claw-code pattern).
    # The previous keyword-matching is_enabled callbacks were effectively always-true
    # (device_testing had `or True`, test_generation had `or True`). Now explicit.
    handoffs = [
        handoff(
            agent=search_agent,
            is_enabled=_always_enabled,
            tool_description_override="Search internal bug reports and test scenarios database (NOT for web browsing)"
        ),
        handoff(
            agent=test_generation_agent,
            is_enabled=_always_enabled,
            tool_description_override="Generate test cases from PRDs, user stories, or specifications"
        ),
        handoff(
            agent=device_testing_agent,
            is_enabled=_always_enabled,
            tool_description_override="Control devices, execute tests, browse the web, and perform autonomous navigation"
        ),
    ]

    # Optional: QA Emulation handoff for bug reproduction workflows
    if qa_emulation_agent:
        handoffs.append(handoff(
            agent=qa_emulation_agent,
            tool_description_override="Reproduce bugs across multiple builds (OG, RB1-RB3) with parallel analysis and structured verdicts"
        ))

    # Optional: Self-Test Specialist for end-to-end app testing flywheel
    if self_test_agent:
        handoffs.append(handoff(
            agent=self_test_agent,
            tool_description_override="Self-test a web app by URL: discover screens, test interactions on emulator, detect anomalies, trace to source code, and suggest fixes with regression tests"
        ))

    logger.info(f"✅ Dynamic handoffs configured with is_enabled callbacks ({len(handoffs)} agents)")

    # Get device testing tools and extract launch_emulators
    # This allows the coordinator to launch emulators directly without delegation
    device_tools = create_device_testing_tools(service_ref=None)
    launch_emulators_func = device_tools.get("launch_emulators")

    # Figma tools (progressive disclosure; large outputs returned by ref_id)
    figma_tools = create_figma_tools()
    get_figma_snapshot_func = figma_tools.get("get_figma_snapshot")
    retrieve_figma_ref_func = figma_tools.get("retrieve_figma_ref")

    # Context-gathering tools (self-directing agent)
    ctx_tools = create_context_tools()

    # Create coordinator tools (Deep Agents Pattern)
    coordinator_tools = [
        function_tool(plan_task),  # Planning tool for complex task decomposition
        function_tool(ctx_tools["get_app_context"]),  # Live system state
        function_tool(ctx_tools["get_workspace_context"]),  # Slack + workspace pulse
        function_tool(ctx_tools["navigate_user"]),  # Route user to pages
        function_tool(ctx_tools["suggest_next_actions"]),  # Proactive suggestions
    ]
    logger.info("✅ Coordinator has context-gathering tools (get_app_context, get_workspace_context, navigate_user, suggest_next_actions)")

    if launch_emulators_func:
        coordinator_tools.append(function_tool(launch_emulators_func))
        logger.info("✅ Coordinator has direct access to launch_emulators tool")

    if execute_simulation_func:
        coordinator_tools.append(function_tool(execute_simulation_func))
        logger.info("✅ Coordinator has direct access to execute_simulation tool")

    if get_figma_snapshot_func:
        coordinator_tools.append(function_tool(get_figma_snapshot_func))
        logger.info("✅ Coordinator has direct access to get_figma_snapshot tool")

    if retrieve_figma_ref_func:
        coordinator_tools.append(function_tool(retrieve_figma_ref_func))
        logger.info("✅ Coordinator has direct access to retrieve_figma_ref tool")

    logger.info("✅ Coordinator has planning tool for Deep Agent pattern")

    # Create coordinator agent with handoffs to specialized agents
    # Use model fallback chain for resilience
    # Coordinator is an orchestrator → prefer HIGH THINKING BUDGET primary model.
    # Model Tiering (Mar 2026): orchestration = gpt-5.4 → gpt-5 → gpt-5-mini
    model_chain = get_model_fallback_chain("orchestration")
    primary_model = model_chain[0]
    logger.info(f"Coordinator using model chain: {model_chain}")

    # GPT-5.4 Prompting Guide (Mar 2026): Use reasoning_effort for thinking control
    # Orchestration tasks require HIGH reasoning effort for complex multi-agent coordination
    coordinator = Agent(
        name="Test Automation Coordinator",
        instructions=instructions,
        tools=coordinator_tools,  # Always provide tools (planning + launch_emulators + execute_simulation)
        handoffs=handoffs,
        model=primary_model,  # gpt-5.4 for orchestration
        model_settings=ModelSettings(
            tool_choice="auto",
            parallel_tool_calls=True,  # Allow batching independent tool calls (user requested)
            reasoning=Reasoning(effort="high"),  # GPT-5.4: High thinking budget for orchestration
            verbosity="medium",  # GPT-5.4: Balanced output verbosity
        ),
    )

    return coordinator


__all__ = ["create_coordinator_agent"]

