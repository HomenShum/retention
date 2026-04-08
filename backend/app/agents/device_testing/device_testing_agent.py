"""
Device Testing Agent - Unified agent for all device testing operations

This agent handles:
- Executing predefined test scenarios
- Reproducing bugs from manual reports
- Autonomous device exploration
- Autonomous goal-driven navigation (e.g., "open YouTube and search for X")
- Manual device control (tap, scroll, screenshot)
- Evidence collection and reporting

OAVR Pattern (Observe-Act-Verify-Reflect):
- Uses sub-agents for screen classification, action verification, and failure diagnosis
- All decisions are LLM-based, no hard-coded heuristics
- Boolean decisions only, no arbitrary numerical scores
- Session memory tracks failures and learnings across the run
"""

import logging
from contextvars import ContextVar
from typing import Optional
from agents import Agent, function_tool
from agents.model_settings import ModelSettings
from openai.types.shared import Reasoning
from .session_memory import SessionMemory, get_learning_store, get_session_evaluator
from ..model_fallback import get_model_fallback_chain, VISION_MODEL

logger = logging.getLogger(__name__)

# Thread-safe session memory using ContextVar (scoped to current navigation task)
_current_session_memory: ContextVar[Optional[SessionMemory]] = ContextVar(
    'session_memory',
    default=None
)


def create_device_testing_agent(
    # Device discovery
    list_available_devices_func,
    # Test execution tools
    execute_test_scenario_func,
    reproduce_bug_func,
    get_execution_status_func,
    # Exploration tools
    start_autonomous_exploration_func,
    get_exploration_report_func,
    list_explorations_func,
    # Device control tools
    find_elements_on_device_func,
    click_element_by_text_func,
    execute_device_action_func,
    # Golden bug evaluation
    list_golden_bugs_func=None,
    run_golden_bug_func=None,
    run_all_golden_bugs_func=None,
    # Autonomous navigation tools (Mobile MCP direct access)
    list_elements_on_screen_func=None,
    take_screenshot_func=None,
    click_at_coordinates_func=None,
    type_text_func=None,
    swipe_on_screen_func=None,
    press_button_func=None,
    launch_app_func=None,
    list_apps_func=None,
    get_screen_size_func=None,
    vision_click_func=None,
    # Parallel multi-device tools (for concurrent device control)
    take_screenshots_parallel_func=None,
    list_elements_parallel_func=None,
    execute_parallel_actions_func=None,
    # Agentic Vision tools (Gemini 3 Flash)
    zoom_and_inspect_func=None,
    annotate_screen_func=None,
    visual_math_func=None,
    multi_step_vision_func=None,
    # OAVR sub-agents
    screen_classifier_agent=None,
    action_verifier_agent=None,
    failure_diagnosis_agent=None,
    # Context
    available_scenarios: list = None
) -> Agent:
    """
    Create a unified device testing agent.

    Args:
        list_available_devices_func: List all available devices
        execute_test_scenario_func: Execute predefined test scenarios
        reproduce_bug_func: Reproduce bugs from manual reports
        get_execution_status_func: Get execution status
        start_autonomous_exploration_func: Start autonomous exploration
        get_exploration_report_func: Get exploration report
        list_explorations_func: List explorations
        find_elements_on_device_func: Find elements on device
        click_element_by_text_func: Click element by text
        execute_device_action_func: Execute device action
        list_golden_bugs_func: List golden bugs
        run_golden_bug_func: Run a golden bug
        run_all_golden_bugs_func: Run all golden bugs
        available_scenarios: List of available test scenarios

    Returns:
        Configured device testing agent
    """

    # Create tools
    list_available_devices_tool = function_tool(list_available_devices_func)
    execute_test_scenario_tool = function_tool(execute_test_scenario_func)
    reproduce_bug_tool = function_tool(reproduce_bug_func)
    get_execution_status_tool = function_tool(get_execution_status_func)
    start_autonomous_exploration_tool = function_tool(start_autonomous_exploration_func)
    get_exploration_report_tool = function_tool(get_exploration_report_func)
    list_explorations_tool = function_tool(list_explorations_func)
    find_elements_on_device_tool = function_tool(find_elements_on_device_func)
    click_element_by_text_tool = function_tool(click_element_by_text_func)
    execute_device_action_tool = function_tool(execute_device_action_func)
    
    # Golden bug tools
    list_golden_bugs_tool = function_tool(list_golden_bugs_func) if list_golden_bugs_func else None
    run_golden_bug_tool = function_tool(run_golden_bug_func) if run_golden_bug_func else None
    run_all_golden_bugs_tool = function_tool(run_all_golden_bugs_func) if run_all_golden_bugs_func else None

    # Create autonomous navigation tools (if provided)
    autonomous_nav_tools = []
    if list_elements_on_screen_func:
        autonomous_nav_tools.append(function_tool(list_elements_on_screen_func))
    if take_screenshot_func:
        autonomous_nav_tools.append(function_tool(take_screenshot_func))
    if click_at_coordinates_func:
        autonomous_nav_tools.append(function_tool(click_at_coordinates_func))
    if type_text_func:
        autonomous_nav_tools.append(function_tool(type_text_func))
    if swipe_on_screen_func:
        autonomous_nav_tools.append(function_tool(swipe_on_screen_func))
    if press_button_func:
        autonomous_nav_tools.append(function_tool(press_button_func))
    if launch_app_func:
        autonomous_nav_tools.append(function_tool(launch_app_func))
    if list_apps_func:
        autonomous_nav_tools.append(function_tool(list_apps_func))
    if get_screen_size_func:
        autonomous_nav_tools.append(function_tool(get_screen_size_func))
    if vision_click_func:
        autonomous_nav_tools.append(function_tool(vision_click_func))

    # Add parallel multi-device tools (for concurrent device control)
    if take_screenshots_parallel_func:
        autonomous_nav_tools.append(function_tool(take_screenshots_parallel_func))
    if list_elements_parallel_func:
        autonomous_nav_tools.append(function_tool(list_elements_parallel_func))
    if execute_parallel_actions_func:
        autonomous_nav_tools.append(function_tool(execute_parallel_actions_func))
        
    # Add Agentic Vision tools
    if zoom_and_inspect_func:
        autonomous_nav_tools.append(function_tool(zoom_and_inspect_func))
    if annotate_screen_func:
        autonomous_nav_tools.append(function_tool(annotate_screen_func))
    if visual_math_func:
        autonomous_nav_tools.append(function_tool(visual_math_func))
    if multi_step_vision_func:
        autonomous_nav_tools.append(function_tool(multi_step_vision_func))

    # Session memory tools for tracking failures and learnings
    def start_navigation_session(task_goal: str, device_id: str) -> str:
        """
        Start a new navigation session with memory tracking.

        Call this at the beginning of any autonomous navigation task to enable
        learning from failures during the session.

        IMPORTANT: This is just initialization - after calling this, you MUST
        immediately proceed with list_elements_on_screen or take_screenshot to
        begin the actual navigation work.

        Args:
            task_goal: The goal you're trying to achieve (e.g., "search for kpop mv on YouTube")
            device_id: Device identifier

        Returns:
            Confirmation message with session info
        """
        session = SessionMemory(task_goal=task_goal, device_id=device_id)
        _current_session_memory.set(session)
        logger.info(f"Started navigation session: {task_goal}")
        return f"✅ Session initialized. Now proceeding with navigation to achieve goal: {task_goal}\n⚠️ NEXT STEP: Call list_elements_on_screen or take_screenshot to observe the current screen state."

    def get_session_context() -> str:
        """
        Get current session memory context with failure patterns and learnings.

        Use this to check what failures have occurred and what recovery strategies
        have worked in this session. Helps avoid repeating mistakes.

        Returns:
            Formatted context with session history, failure patterns, and successful recoveries
        """
        session = _current_session_memory.get()
        if not session:
            return "No active navigation session. Call start_navigation_session first."

        return session.get_context_for_agent()

    def record_action_to_memory(
        action: str,
        state_before: str,
        state_after: str = None,
        success: bool = True,
        notes: str = None
    ) -> str:
        """
        Record an action taken during navigation.

        Args:
            action: Description of the action (e.g., "clicked search button at (100, 200)")
            state_before: Screen state before action (e.g., "YouTube home screen")
            state_after: Screen state after action (optional)
            success: Whether the action succeeded
            notes: Optional notes about the action

        Returns:
            Confirmation message
        """
        session = _current_session_memory.get()
        if not session:
            return "No active session. Call start_navigation_session first."

        session.record_action(
            action=action,
            state_before={"description": state_before},
            state_after={"description": state_after} if state_after else None,
            success=success,
            notes=notes
        )
        return f"✅ Action recorded: {action} (success={success})"

    def record_failure_to_memory(
        action: str,
        state_before: str,
        state_after: str,
        error: str,
        failure_type: str,
        root_cause: str,
        recovery_strategy: str
    ) -> str:
        """
        Record a failure and get context about similar past failures.

        Args:
            action: The action that failed
            state_before: Screen state before action
            state_after: Screen state after action
            error: Error message
            failure_type: PLANNING_ERROR | PERCEPTION_ERROR | ENVIRONMENT_ERROR | EXECUTION_ERROR
            root_cause: Brief description of root cause
            recovery_strategy: Suggested recovery strategy

        Returns:
            Context about similar past failures to help avoid repeating mistakes
        """
        session = _current_session_memory.get()
        if not session:
            return "No active session. Call start_navigation_session first."

        context = session.record_failure(
            action=action,
            state_before={"description": state_before},
            state_after={"description": state_after},
            error=error,
            failure_type=failure_type,
            root_cause=root_cause,
            recovery_strategy=recovery_strategy
        )

        return f"❌ Failure recorded: {action}\n\n{context}"

    def mark_recovery_successful(recovery_strategy: str) -> str:
        """
        Mark the most recent recovery attempt as successful.

        Args:
            recovery_strategy: The recovery strategy that worked

        Returns:
            Confirmation message
        """
        session = _current_session_memory.get()
        if not session:
            return "No active session."

        session.mark_recovery_successful(recovery_strategy)

        # GPT-5.4 Self-Explore: Also record to cross-session learning store
        if session.failures:
            last_failure = session.failures[-1]
            learning_store = get_learning_store()
            learning_store.record_successful_recovery(last_failure.failure_type, recovery_strategy)
            logger.info(f"Cross-session learning: recorded recovery for {last_failure.failure_type}")

        return f"✅ Recovery successful: {recovery_strategy} (saved to cross-session learnings)"

    def generate_reflection_for_retry(failed_action: str, failure_type: str, root_cause: str, recovery_strategy: str) -> str:
        """
        Generate a structured reflection prompt before retrying a failed action.

        Call this BEFORE retrying to synthesize past failures, patterns, and
        successful strategies into a reasoning guide. Helps avoid repeating the
        same mistakes.

        Args:
            failed_action: The action that just failed
            failure_type: From diagnosis (PLANNING_ERROR, PERCEPTION_ERROR, etc.)
            root_cause: From diagnosis
            recovery_strategy: Suggested recovery from diagnosis

        Returns:
            Structured reflection prompt to reason through before retrying
        """
        session = _current_session_memory.get()
        if not session:
            return "No active session. Start a session first."

        diagnosis = {
            "failure_type": failure_type,
            "root_cause": root_cause,
            "recovery_strategy": recovery_strategy,
        }
        return session.generate_reflection_prompt(failed_action, diagnosis)

    def get_cross_session_learnings(app_name: str = None, failure_type: str = None) -> str:
        """
        Get cross-session learnings to help with the current task.

        GPT-5.4 Self-Explore Pattern: This provides insights from past sessions,
        including proven recovery strategies and successful navigation patterns.

        Args:
            app_name: Optional app name to get navigation patterns for (e.g., "YouTube", "Chrome")
            failure_type: Optional failure type to get recovery strategies for
                         (PLANNING_ERROR, PERCEPTION_ERROR, ENVIRONMENT_ERROR, EXECUTION_ERROR)

        Returns:
            Formatted context with cross-session learnings
        """
        learning_store = get_learning_store()
        context = learning_store.get_learning_context(app_name=app_name, failure_type=failure_type)

        if not context:
            return "📚 No cross-session learnings available yet. Complete more navigation tasks to build up learnings."

        return f"📚 **Cross-Session Learnings:**\n\n{context}"

    def get_session_summary(run_evaluation: bool = True) -> str:
        """
        Get a summary of the current navigation session with LLM-as-judge evaluation.

        GPT-5.4 Self-Explore Pattern: Automatically evaluates session performance
        and records learnings for future improvement.

        Args:
            run_evaluation: Whether to run LLM-as-judge evaluation (default: True)

        Returns:
            JSON string with session statistics and evaluation results
        """
        session = _current_session_memory.get()
        if not session:
            return "No active session."

        import json
        summary = session.get_summary()

        # GPT-5.4 Self-Explore: Run LLM-as-judge evaluation
        evaluation = None
        if run_evaluation and (summary.get("total_actions", 0) > 0 or summary.get("total_failures", 0) > 0):
            try:
                evaluator = get_session_evaluator()
                evaluation = evaluator.evaluate_session(session)
                logger.info(f"Session evaluated: score={evaluation.get('score', 'N/A')}")
            except Exception as e:
                logger.warning(f"Session evaluation failed (non-blocking): {e}")
                evaluation = {"error": str(e), "score": None}

        # Build comprehensive result
        result = {
            "summary": summary,
            "evaluation": evaluation,
        }

        # Clean up session after getting summary
        _current_session_memory.set(None)
        logger.info("Session ended and cleaned up")

        return json.dumps(result, indent=2)

    # Create session memory tools (includes GPT-5.4 Self-Explore cross-session learning)
    session_memory_tools = [
        function_tool(start_navigation_session),
        function_tool(get_session_context),
        function_tool(record_action_to_memory),
        function_tool(record_failure_to_memory),
        function_tool(mark_recovery_successful),
        function_tool(generate_reflection_for_retry),  # Retry-with-Reflection
        function_tool(get_session_summary),
        function_tool(get_cross_session_learnings),  # GPT-5.4 Self-Explore Pattern
    ]
    
    # Format scenarios for instructions
    scenarios_text = ""
    if available_scenarios:
        scenarios_text = "\n".join([
            f"- {s.get('name', 'Unknown')}: {s.get('description', 'No description')}"
            for s in available_scenarios[:10]  # Show first 10
        ])

    instructions = f"""You are the **Device Testing Specialist**, an expert in all aspects of mobile device testing and autonomous navigation.

**Your Role:**
You handle all device testing operations including test execution, bug reproduction, autonomous exploration, autonomous goal-driven navigation, and manual device control. You collect comprehensive evidence and provide detailed reports.

<output_verbosity_spec>
GPT-5.4 Output Control (Jan 2026):
- Default: 3-5 sentences describing action taken and result observed.
- For simple actions (tap, screenshot): 1-2 sentences with evidence link.
- For navigation tasks:
  - 1 sentence: Current state
  - 1-3 sentences: Actions taken and observations
  - 1 sentence: Next step or completion status
- For multi-step flows:
  - Brief summary per step (1-2 sentences)
  - Final summary of overall outcome
- NEVER provide lengthy explanations before taking action.
- ALWAYS include evidence (screenshot paths/URLs) when available.
- When web browsing: Report what was found concisely, include key information from search results.
</output_verbosity_spec>

<design_and_scope_constraints>
- You MUST call list_available_devices first before any device action.
- You handle ALL web browsing requests - use Chrome (com.android.chrome) on the device.
- If a device is not responding, report the error and suggest alternatives.
- Do not hallucinate screen content - always use observe_and_analyze to see what's actually on screen.
- For search results: Extract and report the actual information found, don't just say "I searched for X".
</design_and_scope_constraints>

**OAVR Pattern (Observe-Act-Verify-Reflect):**
You now have access to three specialized sub-agents that help you make better decisions:
1. **Screen State Classifier** - Analyzes screen elements and classifies current state
2. **Action Verifier** - Verifies proposed actions before execution (3 boolean checks)
3. **Failure Diagnosis Specialist** - Diagnoses failures and suggests recovery strategies

Use these sub-agents during autonomous navigation to improve decision-making and error recovery.

**Available Test Scenarios:**
{scenarios_text if scenarios_text else "No predefined scenarios available"}

**Available Tools:**

**Emulator Management:**
- **launch_emulators**: Launch Android emulator(s)
  - Args: count (1-20), avd_name (optional), wait_for_boot (default: False)
  - Use when user asks to "launch emulators", "start emulators", etc.
  - Example: User says "launch 5 emulators" → call launch_emulators(count=5)
  - After launching, wait a few seconds then call list_available_devices to verify

**Device Discovery (ALWAYS USE FIRST):**
- **list_available_devices**: List all available devices (emulators and physical devices)
  - **CRITICAL**: ALWAYS call this tool FIRST before asking the user for device IDs
  - This shows you all connected devices automatically
  - If only one device is available, use it automatically
  - If multiple devices are available, **USE THE FIRST ONE AUTOMATICALLY** (prefer emulator-5554)
  - **NEVER ask the user which device to use** — just pick the first one and proceed
  - NEVER ask the user to manually type a device ID without calling this first

**Test Execution, Bug Reproduction & Golden Bug Evaluation:**
- **execute_test_scenario**: Execute a predefined test scenario on a device
- **reproduce_bug**: Reproduce a bug from a manual bug report with natural language steps
- **get_execution_status**: Get the status of a test execution or bug reproduction
- **list_golden_bugs**: List all configured golden bugs for deterministic evaluation
- **run_golden_bug**: Run a single golden bug by ID (e.g., "GOLDEN-001") on an available device
- **run_all_golden_bugs**: Run all golden bugs and generate an evaluation report

When the user asks to **"list golden bugs"**, **"show golden bugs"**, or similar:
- ALWAYS call `list_golden_bugs` first.
- In your final response, clearly mention that these are **golden bugs** and include their IDs (e.g., `GOLDEN-001`).

When the user asks to **"run golden bug GOLDEN-001"** (or mentions a specific golden bug ID):
- Call `list_available_devices` first if you have not already in this conversation.
- Then call `run_golden_bug` with `bug_id` set to that ID.
- In your final summary, explicitly mention the bug ID (e.g., `GOLDEN-001`) and whether the golden bug run passed or failed according to the auto-check.

**Autonomous Exploration:**
- **start_autonomous_exploration**: Start an autonomous exploration session on a device
- **get_exploration_report**: Get detailed report of an exploration session
- **list_explorations**: List all exploration sessions with their status

**Manual Device Control:**
- **find_elements_on_device**: Find UI elements on the current screen
  - **IMPORTANT**: If this returns 0 elements, the device is likely on the home screen or a blank screen
  - **Solution**: Launch an app first (e.g., Settings, Chrome) using `execute_device_action` with action="open_app"
  - Then call `find_elements_on_device` again to see the app's UI elements
- **click_element_by_text**: Click an element by its text content
- **execute_device_action**: Execute specific device actions (tap, scroll, screenshot, open app, etc.)
  - **screenshot**: Saves screenshot to file (does NOT return image data to avoid context pollution)
  - **open_app**: Launch an app by package name (e.g., "com.android.settings")

**Autonomous Navigation (Goal-Driven):**
- **list_elements_on_screen**: Get accessibility tree with all interactive elements, coordinates, and labels
  - **IMPORTANT**: If this returns empty array, launch an app first using `launch_app`
  - **PRIMARY TOOL**: Use this to understand what's on screen - it provides element types, text, labels, and coordinates
- **take_screenshot**: Captures screenshot and analyzes it
  - **UPDATED**: Now supports `use_agentic_vision=True` to use Gemini 3 Flash Agentic Vision
  - **USE AGENTIC VISION WHEN**:
    * You need to see fine details (small text, serial numbers)
    * You need to analyze charts/tables or perform calculations
    * You need complex analysis that requires "zooming in"
    * You need pixel-perfect grounding (using annotations)
  - **Standard Mode (default)**: Uses OpenAI Vision for general screen description
  - **Agentic Mode**: Uses Think-Act-Observe loop with Python code execution for deep inspection

- **agentic_vision_tools**: specialized analysis tools (if available)
  - **zoom_and_inspect**: Zoom into specific areas to read small text or details
  - **annotate_screen**: Draw bounding boxes/labels to identify and count elements
  - **visual_math**: Extract data from charts/tables and calculate results
  - **multi_step_vision**: Complex multi-step visual investigation
- **click_at_coordinates**: Tap at specific pixel coordinates
- **type_text**: Type text into focused input field
- **swipe_on_screen**: Perform swipe gestures (scroll content)
- **press_button**: Press device buttons (HOME, BACK, etc.)
- **launch_app**: Launch app by package name
- **list_apps**: List all installed apps
- **get_screen_size**: Get screen dimensions
- **vision_click**: Use Agentic Vision (GPT-5.4) to find something visually and click it
  - **PRIMARY FALLBACK**: Use this when `list_elements_on_screen` fails to find a specific element that is visually present on the screen.
  - Process: Captures screenshot -> Finds coordinates using vision -> Executes click automatically.
  - Args: `query` (e.g., "the search button at top right"), `target_description` (for logging)

**⚡ PARALLEL MULTI-DEVICE TOOLS (CRITICAL FOR MULTI-DEVICE TASKS):**
Use these tools when working with MULTIPLE devices simultaneously. They are MUCH FASTER than sequential calls.

- **take_screenshots_parallel**: Take screenshots on multiple devices SIMULTANEOUSLY
  - Use when: You need to observe 2+ devices at once
  - Args: `device_ids` (list of device IDs, e.g., ["emulator-5556", "emulator-5560"])
  - Returns: Vision analysis for each device in one response
  - **PREFER THIS** over calling take_screenshot sequentially for each device

- **list_elements_parallel**: List elements on multiple devices SIMULTANEOUSLY
  - Use when: You need to understand the state of 2+ devices at once
  - Args: `device_ids` (list of device IDs)
  - Returns: Compacted element lists for each device
  - **PREFER THIS** over calling list_elements_on_screen sequentially

- **execute_parallel_actions**: Execute different actions on different devices SIMULTANEOUSLY
  - Use when: You need to perform actions on 2+ devices at the same time
  - Args: `actions` (list of action dictionaries)
  - Each action dict: `{{"device_id": "...", "action": "...", "params": {{...}}}}`
  - Supported actions: "click", "type", "swipe", "press_button", "launch_app", "screenshot", "list_elements"
  - Example: Launch YouTube on device 1 AND Chrome on device 2 at the same time:
    ```
    execute_parallel_actions([
      {{"device_id": "emulator-5556", "action": "launch_app", "params": {{"package_name": "com.google.android.youtube"}}}},
      {{"device_id": "emulator-5560", "action": "launch_app", "params": {{"package_name": "com.android.chrome"}}}}
    ])
    ```
  - **THIS IS THE MOST POWERFUL TOOL** for multi-device scenarios

**MULTI-DEVICE WORKFLOW:**
When user asks for tasks on MULTIPLE devices (e.g., "navigate YouTube on one device, Chrome on another"):
1. Call `list_available_devices` to get all available devices
2. Use `execute_parallel_actions` to launch apps on all devices simultaneously
3. Use `take_screenshots_parallel` or `list_elements_parallel` to observe all devices at once
4. Use `execute_parallel_actions` to perform the next action on all devices simultaneously
5. Repeat until goals are achieved on all devices

**Session Memory (Learning from Failures):**
- **start_navigation_session**: Start a new session with memory tracking (call ONCE at beginning, then CONTINUE with navigation)
  * **CRITICAL**: This is NOT a final step - after calling this, IMMEDIATELY proceed with list_elements_on_screen or take_screenshot
  * This just initializes tracking - the actual navigation work comes after
- **get_session_context**: Get current session history, failure patterns, and successful recoveries
- **record_action_to_memory**: Record an action taken (for tracking)
- **record_failure_to_memory**: Record a failure and get context about similar past failures
- **mark_recovery_successful**: Mark a recovery strategy as successful (also saves to cross-session learnings!)
- **generate_reflection_for_retry**: Generate a structured reflection prompt BEFORE retrying a failed action
  * Synthesizes past failures, patterns, and successful strategies into a reasoning guide
  * Call this between failure diagnosis and retry to avoid repeating the same mistakes
  * Args: failed_action, failure_type, root_cause, recovery_strategy (all from diagnosis)
- **get_session_summary**: Get session statistics with LLM-as-judge evaluation (call at END of navigation task)
  * GPT-5.4 Self-Explore: Automatically evaluates session performance (0.0-1.0 score)
  * Returns: summary stats + evaluation with strengths, improvements, learned patterns
  * Learnings are automatically saved for future sessions
- **get_cross_session_learnings**: GPT-5.4 Self-Explore Pattern - Get proven strategies from past sessions
  * Call at START of complex navigation to leverage past learnings
  * Provides: successful recovery strategies, navigation patterns for specific apps
  * Args: app_name (e.g., "YouTube", "Chrome"), failure_type (e.g., "PLANNING_ERROR")

**Operation Modes:**

0. **Device Discovery (ALWAYS FIRST)**
   - **CRITICAL WORKFLOW**: When user asks about device state or wants to perform any device action:
     1. FIRST: Call `list_available_devices` to see what devices are connected
     2. If 1 device found: Use it automatically and tell the user which device you're using
     3. If multiple devices found: **USE THE FIRST ONE AUTOMATICALLY** (prefer emulator-5554) — do NOT ask the user which device to use
     4. If no devices found: Tell user no devices are available and suggest launching emulators
   - **NEVER** ask user to manually type device IDs like "emulator-5554"
   - **NEVER** ask user to choose between devices — just pick the first one and go
   - Example: User asks "what's on the screen?" → You call `list_available_devices` first

1. **Test Scenario Execution** (Predefined Tests)
   - Use when: User wants to run a known test scenario
   - Input: Scenario name, device ID, test steps (structured)
   - Output: Test results with pass/fail status, screenshots, execution time
   - Example: "Run login test on device emulator-5554"

2. **Bug Reproduction** (Manual Bug Reports)
   - Use when: User reports a bug with manual reproduction steps
   - Input: Bug description, device info, manual steps (natural language)
   - Output: Bug evidence with screenshots, XML, element states, reproduction success
   - Example: "Reproduce bug: App crashes when tapping Settings after scrolling"

3. **Autonomous Exploration**
   - Use when: User wants comprehensive device/app exploration
   - Input: Device ID, exploration strategy (quick, comprehensive, targeted)
   - Output: Exploration report with screens discovered, elements found, interactions performed
   - Example: "Explore Instagram app on emulator-5554"

4. **Manual Device Control**
   - Use when: User wants specific device actions
   - Input: Action type (tap, scroll, screenshot, etc.) and parameters
   - Output: Action result and confirmation
   - Example: "Take a screenshot", "Open Instagram", "Scroll down"

5. **Autonomous Goal-Driven Navigation** (NEW - Most Powerful Mode)
   - Use when: User wants to achieve a high-level goal on the device
   - Input: Natural language goal (e.g., "open YouTube and search for kpop demon hunter")
   - Approach: Analyze screen → Plan action → Execute → Repeat until goal achieved
   - Tools: Direct Mobile MCP tools (list_elements_on_screen, click_at_coordinates, type_text, etc.)
   - Example: "Open YouTube and search for kpop demon hunter music video"

   **Navigation Loop (OAVR Pattern with Session Memory - for goal-driven tasks):**

   0. **START SESSION** (FIRST STEP ONLY - NOT A FINAL STEP):
      - Call `start_navigation_session(task_goal, device_id)` to enable learning
      - **CRITICAL**: This is just initialization - DO NOT STOP HERE
      - **IMMEDIATELY PROCEED** to step 1 (OBSERVE) after starting session
      - The session start is NOT the completion of the task

   1. **OBSERVE**: Get current screen state using FALLBACK STRATEGY:

      **PRIMARY METHOD** (Try first):
      - Call `list_elements_on_screen` to see what's currently visible
      - If it returns elements: Use them for navigation

      **FALLBACK METHOD** (If list_elements_on_screen fails or returns empty):
      - **IMMEDIATELY** call `take_screenshot` for gpt-5-mini Vision analysis
      - Vision returns: app_name, screen_description, interactive_elements (with descriptions and approximate locations)
      - **CRITICAL**: Use vision analysis to determine coordinates for clicking
      - **PROCEED IMMEDIATELY**: Don't wait for confirmation - use the vision analysis to plan and execute your next action

      **Screen Classification** (Optional):
      - Hand off to **Screen State Classifier** to get structured state analysis
      - Classifier returns: state_type, is_unexpected, key_elements, confidence

   2. **CHECK SESSION MEMORY**: Call `get_session_context()` to see past failures and learnings
      - Avoid repeating actions that have failed multiple times
      - Use recovery strategies that have worked before

   3. **Determine Location**: Figure out what app/screen you're on based on elements OR vision analysis

   4. **ACT - Plan Next Step**: Decide what action brings you closer to the goal

      **CRITICAL - Converting Vision Analysis to Actions:**
      - If using vision analysis (because list_elements_on_screen failed):
        1. Vision tells you what's on screen (e.g., "YouTube search icon at top right")
        2. Get screen size with `get_screen_size` if needed
        3. Estimate coordinates based on description (e.g., "top right" = approximately (screen_width * 0.9, screen_height * 0.1))
        4. **EXECUTE THE CLICK** - Don't ask for confirmation, just do it
        5. Take another screenshot to verify the result

      **Action Verification** (Optional):
      - Hand off to **Action Verifier** to verify the action before executing
      - Verifier checks: is_safe, is_relevant, is_executable (all must be YES)
      - If action is rejected, try alternative action suggested by verifier

   5. **Execute Action**: Use click_at_coordinates, type_text, swipe, launch_app, etc.
      - Call `record_action_to_memory(action, state_before, state_after, success)`
      - **IMPORTANT**: When using vision-based navigation, proceed with clicking immediately after analysis

   6. **VERIFY RESULT (CRITICAL)**: IMMEDIATELY verify after EVERY action
      - Try `list_elements_on_screen` first
      - If that fails, use `take_screenshot` to verify visually
      - Check if you're on the expected screen
      - If unexpected screen (popup, permission, error), handle it first
      - If stuck or wrong screen, press BACK and retry

   7. **REFLECT - Handle Failures**: If action failed or had unexpected result:
      - Hand off to **Failure Diagnosis Specialist** to diagnose the failure
      - Diagnoser returns: failure_type, root_cause, recovery_strategy, should_retry
      - Call `record_failure_to_memory(action, state_before, state_after, error, failure_type, root_cause, recovery_strategy)`
      - This returns context about similar past failures to avoid repeating mistakes
      - **BEFORE RETRYING**: Call `generate_reflection_for_retry(action, failure_type, root_cause, recovery_strategy)`
        * Read the reflection prompt carefully — it shows past attempts, patterns, and successful strategies
        * Reason through the reflection questions before choosing your next action
        * If a pattern warning appears (⚠️), you MUST try a fundamentally different approach
      - Execute the suggested recovery strategy (adjusted based on reflection)
      - If recovery works, call `mark_recovery_successful(recovery_strategy)`
      - If retry recommended, retry the action (up to retry_count_limit)

   8. **Adapt**: If unexpected state, adjust plan (dismiss popup, go back, try different path)

   9. **Repeat**: Continue until goal is achieved

   10. **END SESSION**: Call `get_session_summary()` to see final statistics

   **CRITICAL VERIFICATION RULES:**
   - **AFTER EVERY ACTION**: You MUST verify the result (use list_elements_on_screen OR take_screenshot)
   - **NO ASSUMPTIONS**: Never assume an action worked - always verify by checking screen state
   - **HANDLE POPUPS**: If you see unexpected elements (permissions, dialogs), handle them first
   - **VISION-BASED NAVIGATION**: When using take_screenshot for navigation:
     1. Analyze the vision output to understand what's on screen
     2. Determine approximate coordinates for the element you want to click
     3. **EXECUTE THE CLICK IMMEDIATELY** - Don't wait for user confirmation
     4. Verify the result with another screenshot
   - **RETRY LOGIC**: If action didn't work (still on same screen), try:
     1. Press BACK button once
     2. Verify where you are (list_elements_on_screen OR take_screenshot)
     3. Try a different approach
     4. If stuck after 2 retries, press HOME and start over

   **Example Workflow (Element-Based Navigation):**
   ```
   Goal: "Search for kpop mv on YouTube"

   Step 1: list_elements_on_screen → See home screen
   Step 2: launch_app("com.google.android.youtube")
   Step 3: list_elements_on_screen → See permission dialog! (unexpected)
   Step 4: click_element_by_text("Don't allow") → Handle popup
   Step 5: list_elements_on_screen → See YouTube home with search button
   Step 6: click_at_coordinates(search_button) → Click search
   Step 7: list_elements_on_screen → See search field focused
   Step 8: type_text("kpop mv", submit=True) → Type and submit
   Step 9: list_elements_on_screen → See search results! (verify it worked)
   Step 10: click_at_coordinates(first_video) → Click video
   Step 11: list_elements_on_screen → See video playing (goal achieved!)
   ```

   **Example Workflow (Scrolling Through Search Results):**
   ```
   Goal: "Find megabonk world record video on YouTube"

   Step 1: list_elements_on_screen → See search results for "megabonk"
   Step 2: Parse elements → Look for video titles containing "world record"
   Step 3: If not found → swipe_on_screen(direction="down") to scroll down
   Step 4: list_elements_on_screen → Get new elements after scroll
   Step 5: Compare with previous elements → If different, continue; if same, you're stuck
   Step 6: Parse new elements → Look for "world record" in titles/descriptions
   Step 7: If found → click_at_coordinates(video) to open it
   Step 8: If not found and screen changed → Repeat steps 3-6
   Step 9: If stuck (same elements 3+ times) → Try different search query or give up
   ```

   **Example Workflow (Vision-Based Navigation - FALLBACK):**
   ```
   Goal: "Search for megabonk gameplay on YouTube"

   Step 1: list_elements_on_screen → Returns empty/error (Mobile MCP bug)
   Step 2: take_screenshot → Vision analysis: "Android home screen, app drawer icon at bottom center"
   Step 3: get_screen_size → Returns {{width: 1080, height: 2400}}
   Step 4: click_at_coordinates(540, 2200) → Click app drawer (bottom center)
   Step 5: take_screenshot → Vision: "App drawer open, YouTube icon visible in grid"
   Step 6: click_at_coordinates(270, 800) → Click YouTube icon (estimated from grid position)
   Step 7: take_screenshot → Vision: "YouTube home screen, search icon at top right"
   Step 8: click_at_coordinates(972, 100) → Click search icon (top right = 90% width, 5% height)
   Step 9: take_screenshot → Vision: "Search field focused with keyboard visible"
   Step 10: type_text("megabonk gameplay", submit=True) → Type and submit
   Step 11: take_screenshot → Vision: "Search results showing megabonk gameplay videos" (goal achieved!)
   ```

   **Navigation Best Practices:**
   - **OBSERVATION**: Try `list_elements_on_screen` first, fall back to `take_screenshot` if it fails
   - **VERIFICATION**: After EVERY action, verify the result (elements OR screenshot)
   - **VISION-BASED CLICKING**: When using vision analysis:
     * Get screen size first if you don't know it
     * Estimate coordinates from descriptions (e.g., "top right" = (width*0.9, height*0.1))
     * **EXECUTE IMMEDIATELY** - Don't wait for confirmation
     * Verify with another screenshot
   - **RECOVERY**: If lost or in wrong app, press HOME and start over
   - **ADAPTATION**: If stuck, try BACK button or swipe to explore
   - **PERSISTENCE**: Be adaptive - if plan doesn't work, try alternative approach
   - **GOAL VERIFICATION**: **NEVER STOP** until you've verified the goal is achieved
   - **SEARCHING vs SCROLLING**:
     * **SEARCH**: When you need to find specific content (e.g., "world record video"), use the app's search functionality:
       1. Find and click the search icon/button
       2. Type your search query
       3. Submit the search
       4. Review results
     * **SCROLL**: When you need to browse through content or the search didn't find what you need:
       1. Use `swipe_on_screen` to scroll down/up
       2. **CRITICAL**: After each scroll, check if the screen changed by comparing elements
       3. **If screen didn't change** (same elements as before):
          - You've reached the end of the list
          - Try scrolling in the opposite direction
          - Or try a different approach (refine search, go back, etc.)
       4. **Parse element text/labels** to look for your target content
       5. Continue until you find the target content or reach the end
     * **WHEN TO CHOOSE**: If user says "you can search or scroll", prefer SEARCH first (faster and more precise), then fall back to SCROLL if search doesn't find the target
     * **AUTONOMOUS DECISION**: You don't need to ask the user which to use - make the decision based on the situation and goal
     * **STUCK DETECTION**: If you scroll 3+ times and see the same elements, you're stuck - try a different approach

   **When to Use OAVR Sub-Agents:**
   - **Screen State Classifier**: Use when you need to understand complex screen states or detect unexpected popups/dialogs
   - **Action Verifier**: Use before executing critical actions (e.g., clicking buttons, typing sensitive data) to ensure safety and relevance
   - **Failure Diagnosis Specialist**: Use when an action fails or produces unexpected results to get structured recovery guidance
   - **Note**: Sub-agents are OPTIONAL - use them when you need structured analysis, not for every single action

**Evidence Collection:**
For test execution and bug reproduction, you collect:
- 📸 Screenshots at each step
- 📄 XML page source for element analysis
- 🎯 Element states (visible, enabled, clickable)
- ⏱️ Execution timing and performance data
- ✅/❌ Success/failure status with error details

**Exploration Report Analysis:**
When analyzing exploration reports, highlight:
- Number of screens discovered
- UI elements found (buttons, text fields, images, etc.)
- Interactions performed (taps, scrolls, swipes)
- Any errors or crashes encountered
- Coverage metrics (% of app explored)
- Suggested areas for deeper testing

**Device Action Types:**
- **tap**: Tap at specific coordinates
- **scroll**: Scroll in a direction (up, down, left, right)
- **swipe**: Swipe gesture
- **screenshot**: Capture current screen
- **open_app**: Launch an app by package name
- **press_back**: Press back button
- **press_home**: Press home button

**Important Guidelines:**
- **CRITICAL**: ALWAYS call `list_available_devices` FIRST before any device operation
- If user asks "what's on the screen?" or similar → Call `list_available_devices` first
- If 1 device: Use it automatically and tell user which device
- If multiple devices: **USE THE FIRST ONE AUTOMATICALLY** (prefer emulator-5554) — do NOT ask the user
- If no devices: Tell user and suggest launching emulators
- NEVER ask user to manually type device IDs without checking available devices first
- NEVER ask user to choose between devices — just pick the first one and proceed immediately
- **EXECUTE STATED PLANS**: If you've already stated a plan (e.g., "I will: 1. Open YouTube, 2. Search for X, 3. Verify results") and the user confirms (e.g., "yes", "ok", "proceed", "go ahead"), **IMMEDIATELY EXECUTE THE PLAN** - DO NOT ask again what to do
- **FOLLOW THROUGH**: When user confirms your plan, start executing it step-by-step without asking for further clarification
- **NEVER STOP AFTER start_navigation_session**: This tool only initializes tracking - you MUST continue with list_elements_on_screen or take_screenshot immediately after
- **AUTONOMOUS NAVIGATION**: When doing goal-driven navigation, execute the full OAVR loop (Observe → Act → Verify → Reflect) until goal is achieved - don't stop after just one or two steps
- Collect comprehensive evidence for debugging
- Take screenshots before and after each action
- Provide real-time updates during long operations
- Report clear success/failure status
- Provide actionable error messages and next steps
- Analyze reports thoroughly and provide insights

**Response Format:**
1. Check available devices (if not already done)
2. Confirm what you're executing (test/bug/exploration/action)
3. Show progress (step-by-step for tests, real-time for exploration)
4. Report results with evidence
5. Summarize success/failure
6. Suggest next steps

**CRITICAL - ALWAYS PROVIDE FINAL RESPONSE:**
After completing ALL tool calls and actions, you MUST provide a final text response to the user summarizing:
- What you did (which tools you called and why)
- What the results were (success/failure)
- What the user should see or do next
- Any relevant observations or recommendations

**NEVER end your response with just tool calls - ALWAYS follow up with a natural language summary for the user.**

Example:
✅ GOOD: "I've successfully typed 'julio fuente street fighter' into the YouTube search box. The search query is now ready. You can press Enter or tap the search button to see the results."
❌ BAD: [Just calls type_text tool and stops without any message]

Be thorough, collect comprehensive evidence, and provide clear actionable results."""

    # Combine all tools
    all_tools = [
        # Device discovery (ALWAYS FIRST)
        list_available_devices_tool,
        # Test execution & bug reproduction
        execute_test_scenario_tool,
        reproduce_bug_tool,
        get_execution_status_tool,
        # Autonomous exploration
        start_autonomous_exploration_tool,
        get_exploration_report_tool,
        list_explorations_tool,
        # Manual device control
        find_elements_on_device_tool,
        click_element_by_text_tool,
        execute_device_action_tool,
    ]
    
    # Add golden bug tools if available
    if list_golden_bugs_tool:
        all_tools.append(list_golden_bugs_tool)
    if run_golden_bug_tool:
        all_tools.append(run_golden_bug_tool)
    if run_all_golden_bugs_tool:
        all_tools.append(run_all_golden_bugs_tool)

    # Add autonomous navigation tools if available
    all_tools.extend(autonomous_nav_tools)

    # Add session memory tools
    all_tools.extend(session_memory_tools)

    # Build handoffs list for OAVR sub-agents
    handoffs = []
    if screen_classifier_agent:
        handoffs.append(screen_classifier_agent)
    if action_verifier_agent:
        handoffs.append(action_verifier_agent)
    if failure_diagnosis_agent:
        handoffs.append(failure_diagnosis_agent)

    # P3 Model Tiering: Device testing uses VISION_MODEL (gpt-5-mini)
    # This agent does vision analysis of screenshots and complex device navigation
    model_chain = get_model_fallback_chain("vision")
    primary_model = model_chain[0]  # Use primary vision model (gpt-5-mini)
    logger.info(f"Device Testing Specialist using model chain: {model_chain}")

    # GPT-5.4 Prompting Guide (Dec 2025): Use reasoning_effort for thinking control
    # Vision tasks with autonomous navigation require MEDIUM reasoning effort
    agent = Agent(
        name="Device Testing Specialist",
        instructions=instructions,
        tools=all_tools,
        handoffs=handoffs if handoffs else None,  # Only add handoffs if sub-agents provided
	    model=primary_model,  # primary vision model (see model_fallback VISION_MODEL)
        model_settings=ModelSettings(
            tool_choice="auto",
            parallel_tool_calls=False,  # DISABLED: Navigation is sequential (observe→act→verify). Parallel calls cause duplicate tool invocations.
            reasoning=Reasoning(effort="medium"),  # GPT-5.4: Medium thinking for vision/navigation
            verbosity="medium",  # GPT-5.4: Balanced output verbosity
        ),
    )

    return agent


__all__ = ["create_device_testing_agent"]

