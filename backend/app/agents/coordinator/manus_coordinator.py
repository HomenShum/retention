"""
Manus Coordinator - Unified Orchestrator for the "Manus AI" Persona.

This agent handles end-to-end autonomous workflows:
1. Generates apps via Chef (agentic-chef)
2. Manages emulators and device provisioning
3. Executes and verifies tests using SoM and Agentic Vision
4. Reports final outcomes in the unified chat.

GPT-5.4 Features:
- Reasoning Effort: high (orchestrator)
- Parallel Tool Calls: enabled
"""

import logging
from typing import Any, List, Optional
from agents import Agent, function_tool, RunContextWrapper
from agents.model_settings import ModelSettings
from openai.types.shared import Reasoning

from .coordinator_instructions import create_coordinator_instructions
from ..model_fallback import get_model_fallback_chain
from ..device_testing.tools.device_testing_tools import create_device_testing_tools
from ..device_testing.tools.agentic_vision_tools import create_agentic_vision_tools

logger = logging.getLogger(__name__)

def manus_instructions() -> str:
    return """
You are 'Manus AI', a state-of-the-art autonomous test engineer.
Your goal is to fulfill user requests by orchestrating various tools across the stack.

Core Capabilities:
1. **App Generation**: Use `generate_chef_app` to build a new React/Convex application from a prompt.
2. **Device Provisioning**: Use `launch_emulators` to ensure test devices are available.
3. **Autonomous Testing**: Use `run_device_test` or `annotate_screen` to verify your creations.
4. **Agentic Vision**: Use `zoom_and_inspect` to debug visual issues with extreme precision.

Workflow Pattern:
1. **Plan**: Analyze the request and create a multi-step plan using the `plan_task` tool.
2. **Execute**: Run tools in parallel where possible (e.g., launching an emulator while generating the app).
3. **Verify**: Use screenshots and visual analysis to confirm the app matches the user's vision.
4. **Report**: provide a concise summary of your successes and findings.

Persona:
- Highly capable, proactive, and "agentic".
- Use 'I' will do X, 'I' have done Y.
- Avoid explaining your tools; focus on the outcome.
"""

async def plan_task(task_description: str, subtasks: list[str]) -> str:
    """Planning tool for breaking down complex tasks into subtasks."""
    subtask_list = "\n".join(f"  {i+1}. {task}" for i, task in enumerate(subtasks))
    return f"✅ Manuscript Task Plan:\n\nTask: {task_description}\n\nSubtasks:\n{subtask_list}"

def create_manus_coordinator(
    service_ref,
    ui_context_info: str = ""
) -> Agent:
    """
    Create the Manus Coordinator agent.
    
    Args:
        service_ref: Reference to AIAgentService
        ui_context_info: Optional UI context
    """
    
    # 1. Gather Tools
    device_tools = create_device_testing_tools(service_ref)
    
    # Extract specific tools the coordinator uses directly
    launch_emulators = device_tools.get("launch_emulators")
    list_devices = device_tools.get("list_available_devices")
    execute_test = device_tools.get("execute_test_scenario")
    
    # Vision Tools
    vision_tools = create_agentic_vision_tools(
        mobile_mcp_client=getattr(service_ref, 'mobile_mcp_client', None),
        device_id="emulator-5554" # Fallback, will be overridden in dynamic calls
    )
    
    # Chef Tools
    async def generate_chef_app(prompt: str) -> str:
        """
        Generate a new full-stack application using Chef.
        This builds the frontend (Vite/React) and backend (Convex).
        """
        try:
            # For Manus mode, we might wait or return the run_id
            runner = getattr(service_ref, '_chef_runner_ref', None) 
            if not runner:
                return "Error: Chef runner not available in service_ref"
            
            # Use deterministic model for generation if specified in config, otherwise gpt-5.4
            import uuid
            run_id = str(uuid.uuid4())
            result = await runner.run(prompt=prompt, run_id=run_id, model="gpt-5.4")
            
            if result.success:
                return f"Success! App generated. Run ID: {run_id}. Files created: {len(result.files)}"
            else:
                return f"Failed to generate app: {result.error}"
        except Exception as e:
            return f"Error in generate_chef_app: {str(e)}"

    # Assemble Toolset
    manus_tools = [
        function_tool(plan_task),
        function_tool(generate_chef_app),
    ]
    
    if launch_emulators: manus_tools.append(function_tool(launch_emulators))
    if list_devices: manus_tools.append(function_tool(list_devices))
    if execute_test: manus_tools.append(function_tool(execute_test))
    
    for tool in vision_tools.values():
        manus_tools.append(function_tool(tool))

    # 2. Select Model
    model_chain = get_model_fallback_chain("orchestration")
    primary_model = model_chain[0] # gpt-5.4

    # 3. Instantiate Agent
    manus = Agent(
        name="Manus AI Coordinator",
        instructions=manus_instructions(),
        tools=manus_tools,
        model=primary_model,
        model_settings=ModelSettings(
            tool_choice="auto",
            parallel_tool_calls=True,
            reasoning=Reasoning(effort="high"),
            verbosity="medium"
        )
    )
    
    return manus

__all__ = ["create_manus_coordinator"]
