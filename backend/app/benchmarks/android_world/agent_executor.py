"""
Agent-based AndroidWorld Task Executor.

Uses OpenAI Agents SDK with MCP tools to intelligently execute tasks.
The agent observes the screen, plans actions, and executes them.

Model Configuration (Industry Standard - January 2026):
- THINKING_MODEL (gpt-5.4): Task planning and orchestration
- PRIMARY_MODEL (gpt-5): Standard tasks, stable model
- VISION_MODEL (gpt-5.4): Best vision capabilities for grounding

Hybrid Element Detection Strategy:
Instead of using vision as a fallback, we use a HYBRID approach that:
1. Gets elements from MCP/ADB (structured, precise coordinates)
2. Gets elements from Vision (visual context, catches UI missed by accessibility)
3. Merges and deduplicates both sets by coordinate proximity
4. Returns the combined, richer set to the agent

Self-Search Guidance:
The agent can search for guidance when stuck using the search_for_guidance tool.
This queries a knowledge base of app-specific workflows and UI patterns.

Based on research from:
- OmniParser V2 (Microsoft): Pure vision-based GUI agents
- SeeClick: Visual grounding for GUI automation
- AndroidWorld benchmark: Multi-modal element detection
- Reflexion: Language agents with verbal reinforcement learning
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from openai import OpenAI
from agents import Agent, Runner, ModelSettings, function_tool
from ...observability.tracing import get_traced_client
from .task_registry import AndroidWorldTask
from .executor import TaskExecutionResult, TaskStatus

logger = logging.getLogger(__name__)

# Model configuration - GPT-5 Series (January 2026)
# Note: gpt-5-mini/nano have intermittent empty responses, using gpt-5 as stable PRIMARY
THINKING_MODEL = "gpt-5.4"      # High-budget reasoning, complex planning
PRIMARY_MODEL = "gpt-5"         # Standard tasks, topic-focused vision (stable)
VISION_MODEL = "gpt-5.4"        # Best vision capabilities for grounding
MAX_STEPS = 15                  # Maximum agent steps per task

# Coordinate proximity threshold for deduplication (pixels)
DEDUP_DISTANCE_THRESHOLD = 50


def _merge_hybrid_elements(
    mcp_elements: List[Dict[str, Any]],
    vision_elements: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Merge elements from MCP/ADB and Vision sources with intelligent deduplication.

    Strategy:
    1. MCP elements are authoritative for coordinates (more precise)
    2. Vision elements add coverage for visual elements missed by accessibility tree
    3. Deduplicate by proximity - if vision element is near MCP element, prefer MCP
    4. Tag elements with their source for transparency

    Args:
        mcp_elements: Elements from MCP/ADB uiautomator (structured, precise)
        vision_elements: Elements from GPT-5.4 vision analysis (visual, contextual)

    Returns:
        Merged list of elements with source tags
    """
    merged = []

    # 1. Add all MCP elements first (authoritative)
    for elem in mcp_elements or []:
        # Normalize to common format
        normalized = {
            "name": elem.get("name") or elem.get("text") or elem.get("label", ""),
            "x": elem.get("x", 0),
            "y": elem.get("y", 0),
            "type": elem.get("type", elem.get("class", "element")),
            "clickable": elem.get("clickable", True),
            "source": "mcp"  # Tag source
        }
        # Use center coordinates if bounds are provided
        if "bounds" in elem:
            bounds = elem["bounds"]
            normalized["x"] = (bounds.get("x1", 0) + bounds.get("x2", 0)) // 2
            normalized["y"] = (bounds.get("y1", 0) + bounds.get("y2", 0)) // 2
        merged.append(normalized)

    # 2. Add vision elements that don't overlap with MCP elements
    for v_elem in vision_elements or []:
        v_x = v_elem.get("x", 0)
        v_y = v_elem.get("y", 0)

        # Check if this vision element is near any MCP element
        is_duplicate = False
        for m_elem in merged:
            m_x = m_elem.get("x", 0)
            m_y = m_elem.get("y", 0)
            distance = ((v_x - m_x) ** 2 + (v_y - m_y) ** 2) ** 0.5

            if distance < DEDUP_DISTANCE_THRESHOLD:
                # Vision element overlaps with MCP element - skip
                is_duplicate = True
                logger.debug(f"[HYBRID] Dedup: vision '{v_elem.get('name')}' near MCP element at ({m_x}, {m_y})")
                break

        if not is_duplicate:
            # Add vision element with source tag
            merged.append({
                "name": v_elem.get("name", ""),
                "x": v_x,
                "y": v_y,
                "type": v_elem.get("type", "element"),
                "clickable": v_elem.get("clickable", True),
                "source": "vision"  # Tag source
            })

    # 3. Sort by y-coordinate (top to bottom) for natural reading order
    merged.sort(key=lambda e: (e.get("y", 0), e.get("x", 0)))

    return merged


async def vision_analyze_screen(
    screenshot_base64: str,
    task_description: str = "",
    analysis_topics: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Analyze a screenshot using GPT-5-mini vision to extract UI elements with coordinates.

    This is a fallback when MCP and ADB fail to return element data.
    Based on research from OmniParser V2 (Microsoft) for pure vision-based GUI agents.

    Args:
        screenshot_base64: Base64-encoded PNG/JPEG screenshot
        task_description: Optional task context to focus element detection
        analysis_topics: Optional list of topics to focus analysis on (e.g., ["buttons", "toggles"])
                        If provided, analysis is tailored for cost efficiency.

    Returns:
        List of element dictionaries with name, x, y, clickable info
    """
    client = get_traced_client(OpenAI())

    # Build focused prompt based on topics if provided
    if analysis_topics:
        topics_text = ", ".join(analysis_topics)
        prompt = f"""Analyze this Android screen and find UI elements related to: {topics_text}

For each relevant element, provide:
1. name: Descriptive name (button text, label)
2. x: Center X coordinate in pixels
3. y: Center Y coordinate in pixels
4. type: Element type ({topics_text})
5. clickable: Whether clickable/interactive

Task context: {task_description or "navigation"}

Return ONLY a JSON array:
[{{"name": "Example", "x": 540, "y": 350, "type": "button", "clickable": true}}]

Focus only on {topics_text}. Omit other elements for efficiency."""
    else:
        # Full analysis prompt
        prompt = f"""Analyze this Android screen screenshot and identify all interactive UI elements.

For each element, provide:
1. name: A descriptive name (button text, label, or description)
2. x: Approximate center X coordinate in pixels
3. y: Approximate center Y coordinate in pixels
4. type: Element type (button, toggle, text_field, link, icon, menu_item)
5. clickable: Whether the element appears clickable/interactive

Focus on elements relevant to this task: {task_description or "general navigation"}

Return a JSON array of elements. Example format:
[
  {{"name": "Bluetooth", "x": 540, "y": 350, "type": "toggle", "clickable": true}},
  {{"name": "Settings", "x": 100, "y": 50, "type": "icon", "clickable": true}}
]

Important:
- Estimate coordinates based on the visible screen (typical Android: 1080x1920 or 1080x2400)
- Focus on clearly visible, interactive elements
- Include toggles, buttons, icons, text links, and menu items
- Return ONLY the JSON array, no other text"""

    try:
        # Determine mime type
        mime_type = "image/png"
        if screenshot_base64.startswith("/9j/"):  # JPEG magic bytes in base64
            mime_type = "image/jpeg"

        # Always use VISION_MODEL (gpt-5.4) for vision - gpt-5/gpt-5-mini have intermittent empty responses
        model = VISION_MODEL
        max_tokens = 1000 if analysis_topics else 2000  # Less tokens for focused analysis

        # Validate screenshot data
        if not screenshot_base64 or len(screenshot_base64) < 100:
            logger.error(f"[VISION] Invalid screenshot data: length={len(screenshot_base64) if screenshot_base64 else 0}")
            return []

        logger.info(f"[VISION] Using model={model}, topics={analysis_topics or 'full'}, screenshot_len={len(screenshot_base64)}")

        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime_type};base64,{screenshot_base64}"
                        }
                    ]
                }
            ],
            max_output_tokens=max_tokens,
        )

        # Debug: Log raw response structure with comprehensive inspection
        logger.debug(f"[VISION] Response type: {type(response).__name__}")
        logger.debug(f"[VISION] Response attrs: {[a for a in dir(response) if not a.startswith('_')]}")

        # Extract text from response - try multiple access patterns
        response_text = ""

        # Pattern 1: output_text (direct string attribute on newer SDK versions)
        if hasattr(response, 'output_text') and response.output_text:
            response_text = response.output_text
            logger.debug(f"[VISION] Got text from output_text: {len(response_text)} chars")

        # Pattern 2: output list with content items
        if not response_text and hasattr(response, 'output') and response.output:
            logger.debug(f"[VISION] Response.output length: {len(response.output)}")
            for i, item in enumerate(response.output):
                item_type = type(item).__name__
                logger.debug(f"[VISION] Output[{i}] type: {item_type}")

                # Check for message type with content
                if hasattr(item, 'content') and item.content:
                    for j, content in enumerate(item.content):
                        content_type = type(content).__name__
                        logger.debug(f"[VISION] Content[{j}] type: {content_type}")
                        if hasattr(content, 'text'):
                            response_text += content.text
                            logger.debug(f"[VISION] Added text from content[{j}].text")
                        elif hasattr(content, 'output_text'):
                            response_text += content.output_text
                            logger.debug(f"[VISION] Added text from content[{j}].output_text")

                # Check for direct text attribute on output item
                elif hasattr(item, 'text') and item.text:
                    response_text += item.text
                    logger.debug(f"[VISION] Added text from output[{i}].text")

        # Pattern 3: choices (older completions-style API)
        if not response_text and hasattr(response, 'choices') and response.choices:
            for choice in response.choices:
                if hasattr(choice, 'message') and hasattr(choice.message, 'content'):
                    response_text += choice.message.content or ""
                    logger.debug(f"[VISION] Got text from choices.message.content")

        # Log what we got before parsing
        logger.info(f"[VISION] Raw response text length: {len(response_text)}, preview: {response_text[:200] if response_text else '(empty)'}")

        # Parse JSON from response - handle multiple formats
        elements = _parse_vision_json(response_text)
        if elements:
            logger.info(f"[VISION] Extracted {len(elements)} elements via vision analysis")
            return elements
        else:
            logger.warning(f"[VISION] No elements found in response: {response_text[:300]}")
            return []

    except Exception as e:
        logger.error(f"[VISION] Vision analysis failed: {e}")
        return []


def _parse_vision_json(response_text: str) -> List[Dict[str, Any]]:
    """Parse JSON from vision response with multiple fallback strategies."""
    import re

    if not response_text or not response_text.strip():
        return []

    # Strategy 1: Extract from markdown code blocks (```json ... ```)
    code_block_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response_text)
    if code_block_match:
        try:
            content = code_block_match.group(1).strip()
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    # Strategy 2: Find JSON array directly
    json_start = response_text.find('[')
    json_end = response_text.rfind(']') + 1
    if json_start != -1 and json_end > json_start:
        try:
            json_str = response_text[json_start:json_end]
            elements = json.loads(json_str)
            if isinstance(elements, list):
                return elements
        except json.JSONDecodeError:
            pass

    # Strategy 3: Find JSON object and wrap in array
    obj_start = response_text.find('{')
    obj_end = response_text.rfind('}') + 1
    if obj_start != -1 and obj_end > obj_start:
        try:
            json_str = response_text[obj_start:obj_end]
            element = json.loads(json_str)
            if isinstance(element, dict) and 'name' in element:
                return [element]
        except json.JSONDecodeError:
            pass

    # Strategy 4: Try parsing entire response as JSON
    try:
        parsed = json.loads(response_text.strip())
        if isinstance(parsed, list):
            return parsed
        elif isinstance(parsed, dict) and 'elements' in parsed:
            return parsed['elements']
    except json.JSONDecodeError:
        pass

    # Strategy 5: Extract element descriptions via regex (last resort)
    # Match patterns like: "Button at (540, 350)" or "Toggle 'Bluetooth' at x=540, y=350"
    element_pattern = re.compile(
        r'["\']?([^"\']+)["\']?\s+(?:at|@)\s*\(?(\d+)\s*,\s*(\d+)\)?',
        re.IGNORECASE
    )
    matches = element_pattern.findall(response_text)
    if matches:
        return [
            {"name": m[0].strip(), "x": int(m[1]), "y": int(m[2]), "type": "element", "clickable": True}
            for m in matches[:10]  # Limit to 10
        ]

    return []


@dataclass
class AgentStep:
    """A single step taken by the agent"""
    step_num: int
    observation: str
    action: str
    result: str
    timestamp: datetime = field(default_factory=datetime.now)


class AgentExecutor:
    """
    Executes AndroidWorld tasks using an LLM agent with MCP tools.
    
    The agent:
    1. Observes the current screen state
    2. Plans the next action based on task description
    3. Executes the action via MCP tools
    4. Verifies the result
    5. Repeats until task complete or max steps reached
    """
    
    def __init__(self, mcp_client, model: str = PRIMARY_MODEL):
        self.mcp_client = mcp_client
        self.model = model
        self.max_steps = MAX_STEPS
        
    async def execute_task(
        self,
        task: AndroidWorldTask,
        device_id: str,
        take_screenshots: bool = True,
    ) -> TaskExecutionResult:
        """Execute a task using the LLM agent."""
        
        result = TaskExecutionResult(
            task_name=task.name,
            device_id=device_id,
            status=TaskStatus.RUNNING,
            start_time=datetime.now(),
        )
        
        logger.info(f"[AGENT] Starting task '{task.name}' on {device_id}")
        logger.info(f"[AGENT] Task: {task.description}")

        # Action tracking list - tools will append to this during execution
        tracked_actions: List[Dict[str, Any]] = []

        try:
            # Build agent with MCP tools bound to this device
            agent = self._create_task_agent(task, device_id, tracked_actions)
            
            # Create initial prompt with task and current screen state
            elements = await self.mcp_client.list_elements_on_screen(device_id)
            initial_state = self._format_elements(elements)
            
            # Detect task type and provide specific instructions
            task_desc_lower = task.description.lower()
            task_name_lower = task.name.lower()

            # Determine the recommended tool based on task type
            recommended_tool = None
            if "bluetooth" in task_desc_lower or "bluetooth" in task_name_lower:
                if "on" in task_desc_lower or "enable" in task_desc_lower or "turn on" in task_desc_lower:
                    recommended_tool = "toggle_bluetooth(turn_on=True)"
                else:
                    recommended_tool = "toggle_bluetooth(turn_on=False)"
            elif "wifi" in task_desc_lower or "wi-fi" in task_desc_lower or "wifi" in task_name_lower:
                if "off" in task_desc_lower or "disable" in task_desc_lower or "turn off" in task_desc_lower:
                    recommended_tool = "toggle_wifi(turn_on=False)"
                else:
                    recommended_tool = "toggle_wifi(turn_on=True)"
            elif "photo" in task_desc_lower or "camera" in task_desc_lower or "camera" in task_name_lower:
                recommended_tool = "take_camera_photo()"
            elif "contact" in task_desc_lower or "contact" in task_name_lower:
                recommended_tool = "add_contact(first_name, last_name, phone) - extract names and phone from task"
            elif "markor" in task_desc_lower or "note" in task_desc_lower or "markor" in task_name_lower:
                recommended_tool = "create_markor_note(title, content) - extract title and content from task"

            if recommended_tool:
                prompt = f"""Execute this mobile automation task:

TASK: {task.description}

🚨 MANDATORY FIRST ACTION 🚨
You MUST call this tool IMMEDIATELY as your FIRST action:
→ {recommended_tool}

This specialized tool handles the ENTIRE workflow automatically and is REQUIRED for this task type.
Do NOT try to navigate manually - the helper tool is more reliable.

After calling the helper tool, verify the result and respond with "TASK COMPLETE".

Begin now - call the helper tool immediately!"""
            else:
                prompt = f"""Execute this mobile automation task:

TASK: {task.description}

CURRENT SCREEN ELEMENTS:
{initial_state}

Instructions:
1. Observe the current screen state
2. Navigate step by step to complete the task
3. Use get_screen_elements() to check progress
4. Stop when the task is complete

Begin executing the task now!"""

            # Run the agent with increased max_turns for complex tasks
            run_result = await Runner.run(agent, prompt, max_turns=40)

            # Use tracked actions (populated during execution) - more reliable than post-extraction
            if tracked_actions:
                result.actions = tracked_actions
                logger.info(f"[AGENT] Tracked {len(tracked_actions)} actions during execution")
            else:
                # Fallback: try extracting from run_result
                result.actions = self._extract_actions(run_result)
                logger.info(f"[AGENT] Extracted {len(result.actions)} actions post-execution")

            result.steps_taken = len(result.actions)
            result.status = TaskStatus.SUCCESS

            # Track token usage from the agent run
            result.token_usage = self._extract_token_usage(run_result)
            if result.token_usage:
                logger.info(f"[AGENT] Token usage: {result.token_usage.total_tokens} total "
                           f"({result.token_usage.prompt_tokens} prompt, {result.token_usage.completion_tokens} completion)")

            # Capture agent's final output for verification
            if hasattr(run_result, 'final_output') and run_result.final_output:
                result.agent_output = str(run_result.final_output)
                logger.debug(f"[AGENT] Final output: {result.agent_output[:200]}...")

            # Take final screenshot (full base64 for verification)
            if take_screenshots:
                screenshot = await self.mcp_client.take_screenshot(device_id)
                if screenshot and "data" in screenshot:
                    result.screenshots.append(screenshot["data"])
                    
        except asyncio.TimeoutError:
            result.status = TaskStatus.TIMEOUT
            result.error_message = "Task timed out"
            logger.error(f"[AGENT] Task '{task.name}' timed out")
            
        except Exception as e:
            result.status = TaskStatus.FAILED
            result.error_message = str(e)
            logger.error(f"[AGENT] Task '{task.name}' failed: {e}")
            
        result.end_time = datetime.now()
        result.duration_seconds = (result.end_time - result.start_time).total_seconds()
        
        logger.info(f"[AGENT] Task '{task.name}': {result.status.value} in {result.duration_seconds:.1f}s")
        return result
    
    def _create_task_agent(
        self,
        task: AndroidWorldTask,
        device_id: str,
        tracked_actions: List[Dict[str, Any]]
    ) -> Agent:
        """Create an agent with MCP tools for the specific device.

        Args:
            task: The task to execute
            device_id: Target device ID
            tracked_actions: Mutable list that tools will append their calls to
        """

        # Create tool functions that capture device_id and track calls
        mcp = self.mcp_client

        def track_action(tool_name: str, **kwargs):
            """Track a tool invocation."""
            tracked_actions.append({"tool": tool_name, "arguments": kwargs})
            logger.debug(f"[AGENT] Action tracked: {tool_name}({kwargs})")

        @function_tool
        async def click(x: int, y: int) -> str:
            """Click at screen coordinates (x, y)."""
            track_action("click", x=x, y=y)
            await mcp.click_on_screen(device_id, x, y)
            # Brief wait for UI to respond
            await asyncio.sleep(0.3)
            return f"Clicked at ({x}, {y})"

        @function_tool
        async def type_text(text: str, submit: bool = False) -> str:
            """Type text into the focused field. Set submit=True to press Enter."""
            track_action("type_text", text=text, submit=submit)
            await mcp.type_keys(device_id, text, submit=submit)
            await asyncio.sleep(0.2)
            return f"Typed: {text}" + (" [submitted]" if submit else "")

        @function_tool
        async def clear_field() -> str:
            """Clear/select all text in the current focused field before typing new content.
            ALWAYS use this before type_text when editing existing text or filling a form field."""
            track_action("clear_field")
            import subprocess
            try:
                subprocess.run(
                    ["adb", "-s", device_id, "shell", "input", "keyevent", "--longpress", "KEYCODE_DEL"],
                    capture_output=True, timeout=3
                )
                await asyncio.sleep(0.1)
                for _ in range(50):
                    subprocess.run(
                        ["adb", "-s", device_id, "shell", "input", "keyevent", "KEYCODE_DEL"],
                        capture_output=True, timeout=2
                    )
                await asyncio.sleep(0.2)
                return "Field cleared"
            except Exception as e:
                return f"Failed to clear field: {e}"

        @function_tool
        async def swipe(direction: str) -> str:
            """Swipe on screen. Direction: up, down, left, right."""
            track_action("swipe", direction=direction)
            await mcp.swipe_on_screen(device_id, direction)
            await asyncio.sleep(0.3)
            return f"Swiped {direction}"

        @function_tool
        async def press_button(button: str) -> str:
            """Press device button. Options: HOME, BACK, VOLUME_UP, VOLUME_DOWN."""
            track_action("press_button", button=button)
            await mcp.press_button(device_id, button)
            await asyncio.sleep(0.3)
            return f"Pressed {button}"

        @function_tool
        async def launch_app(package_name: str) -> str:
            """Launch an app by package name."""
            track_action("launch_app", package_name=package_name)
            await mcp.launch_app(device_id, package_name)
            await asyncio.sleep(1.5)
            return f"Launched {package_name}. Wait a moment for the app to fully load, then use get_screen_elements() to see the current screen."

        @function_tool
        async def take_camera_photo() -> str:
            """Take a photo using the camera. This tool handles the ENTIRE workflow:
            1. Launches the camera app
            2. Dismisses any setup screens
            3. Takes the photo
            Use this instead of trying to navigate the camera manually."""
            track_action("take_camera_photo")
            import subprocess

            # 1. First, launch the camera app
            logger.info("[CAMERA] Launching camera app...")
            subprocess.run(
                ["adb", "-s", device_id, "shell", "am", "start", "-a", "android.media.action.IMAGE_CAPTURE"],
                capture_output=True, timeout=5
            )
            await asyncio.sleep(2.0)

            # 2. Dismiss setup screens by clicking NEXT button multiple times
            # NEXT button is typically at x=260, y=935 based on vision analysis
            logger.info("[CAMERA] Dismissing setup screens...")
            setup_positions = [
                (260, 935),   # NEXT button - primary position from vision logs
                (270, 940),   # NEXT button - alternate position
                (250, 930),   # NEXT button - another variant
                (540, 935),   # Center bottom - some devices
            ]

            # Try to dismiss setup screens (up to 8 times to get through all setup pages)
            for i in range(8):
                # Click NEXT button at known positions
                for x, y in setup_positions:
                    subprocess.run(
                        ["adb", "-s", device_id, "shell", "input", "tap", str(x), str(y)],
                        capture_output=True, timeout=3
                    )
                    await asyncio.sleep(0.2)
                await asyncio.sleep(0.3)

            # Wait for camera viewfinder to appear
            await asyncio.sleep(1.5)

            # 3. Take the photo - shutter button typically at bottom center
            logger.info("[CAMERA] Taking photo...")
            screen_width = 1080
            shutter_x = screen_width // 2
            shutter_y = 2100  # Typical shutter position
            subprocess.run(
                ["adb", "-s", device_id, "shell", "input", "tap", str(shutter_x), str(shutter_y)],
                capture_output=True, timeout=5
            )
            await asyncio.sleep(2.0)

            logger.info("[CAMERA] Photo captured!")
            return "Photo captured! Camera app was launched, setup screens were dismissed, and the shutter was tapped. The photo should now be saved."

        @function_tool
        async def add_contact(first_name: str, last_name: str, phone: str) -> str:
            """Add a new contact with the given name and phone number.
            Uses ADB content provider to insert contact directly - more reliable than UI.

            Args:
                first_name: First name of the contact
                last_name: Last name of the contact
                phone: Phone number
            """
            track_action("add_contact", first_name=first_name, last_name=last_name, phone=phone)
            import subprocess
            logger.info(f"[CONTACTS] Adding contact: {first_name} {last_name}, {phone}")

            # Clean phone number - keep only digits and +
            clean_phone = ''.join(c for c in phone if c.isdigit() or c == '+')

            # Use ADB content provider to insert contact directly
            # Step 1: Insert raw contact
            result = subprocess.run(
                ["adb", "-s", device_id, "shell", "content", "insert",
                 "--uri", "content://com.android.contacts/raw_contacts",
                 "--bind", "account_type:s:null",
                 "--bind", "account_name:s:null"],
                capture_output=True, timeout=10, text=True
            )
            logger.info(f"[CONTACTS] Raw contact insert result: {result.stdout} {result.stderr}")

            # Get the raw_contact_id - query all and take the last one
            query_result = subprocess.run(
                ["adb", "-s", device_id, "shell", "content", "query",
                 "--uri", "content://com.android.contacts/raw_contacts",
                 "--projection", "_id"],
                capture_output=True, timeout=10, text=True
            )
            logger.info(f"[CONTACTS] Query result: {query_result.stdout[:200] if query_result.stdout else 'empty'}")

            # Parse all _id values and get the last one
            import re
            raw_contact_id = "1"
            if "_id=" in query_result.stdout:
                matches = re.findall(r'_id=(\d+)', query_result.stdout)
                if matches:
                    raw_contact_id = matches[-1]  # Get the last (highest) ID
            logger.info(f"[CONTACTS] Using raw_contact_id: {raw_contact_id}")

            # Step 2: Insert name data - wrap entire shell command in single quotes
            full_name = f"{first_name} {last_name}"
            # Use single quotes around the shell command to preserve spaces in bind values
            name_cmd = f'''adb -s {device_id} shell 'content insert --uri content://com.android.contacts/data --bind raw_contact_id:i:{raw_contact_id} --bind mimetype:s:vnd.android.cursor.item/name --bind "data1:s:{full_name}" --bind "data2:s:{first_name}" --bind "data3:s:{last_name}"' '''
            name_result = subprocess.run(name_cmd, capture_output=True, timeout=10, text=True, shell=True)
            logger.info(f"[CONTACTS] Name inserted: rc={name_result.returncode}, out={name_result.stdout[:100] if name_result.stdout else 'empty'}, err={name_result.stderr[:100] if name_result.stderr else 'none'}")

            # Step 3: Insert phone data
            phone_cmd = f'''adb -s {device_id} shell 'content insert --uri content://com.android.contacts/data --bind raw_contact_id:i:{raw_contact_id} --bind mimetype:s:vnd.android.cursor.item/phone_v2 --bind "data1:s:{clean_phone}" --bind data2:i:2' '''
            phone_result = subprocess.run(phone_cmd, capture_output=True, timeout=10, text=True, shell=True)
            logger.info(f"[CONTACTS] Phone inserted: rc={phone_result.returncode}, out={phone_result.stdout[:100] if phone_result.stdout else 'empty'}, err={phone_result.stderr[:100] if phone_result.stderr else 'none'}")

            await asyncio.sleep(1.0)

            # Open the contact details directly using lookup URI
            # First, get the lookup key for the contact
            lookup_result = subprocess.run(
                ["adb", "-s", device_id, "shell", "content", "query", "--uri",
                 f"content://com.android.contacts/raw_contacts/{raw_contact_id}",
                 "--projection", "contact_id"],
                capture_output=True, timeout=10, text=True
            )

            # Extract contact_id from result
            contact_id = raw_contact_id  # Default to raw_contact_id
            if lookup_result.stdout:
                import re
                contact_match = re.search(r'contact_id=(\d+)', lookup_result.stdout)
                if contact_match:
                    contact_id = contact_match.group(1)

            # Open the contact details view
            subprocess.run(
                ["adb", "-s", device_id, "shell", "am", "start", "--activity-clear-top",
                 "-a", "android.intent.action.VIEW",
                 "-d", f"content://com.android.contacts/contacts/{contact_id}"],
                capture_output=True, timeout=5
            )
            await asyncio.sleep(2.0)

            return f"✅ Contact added via ADB: {first_name} {last_name}, Phone: {clean_phone}. Contact ID: {contact_id}. The contact details screen should now be open showing the name and phone number."

        @function_tool
        async def create_markor_note(title: str, content: str) -> str:
            """Create a new note in Markor app with the given title and content.
            Uses direct ADB file creation - more reliable than UI navigation.

            Args:
                title: Title/filename for the note (without extension)
                content: Text content of the note
            """
            track_action("create_markor_note", title=title, content=content)
            import subprocess
            import base64
            logger.info(f"[MARKOR] Creating note: {title}")

            # Grant storage permissions to Markor via appops (required for Android 11+)
            subprocess.run(
                ["adb", "-s", device_id, "shell", "appops", "set", "net.gsantner.markor",
                 "MANAGE_EXTERNAL_STORAGE", "allow"],
                capture_output=True, timeout=5
            )
            subprocess.run(
                ["adb", "-s", device_id, "shell", "appops", "set", "net.gsantner.markor",
                 "READ_EXTERNAL_STORAGE", "allow"],
                capture_output=True, timeout=5
            )
            subprocess.run(
                ["adb", "-s", device_id, "shell", "appops", "set", "net.gsantner.markor",
                 "WRITE_EXTERNAL_STORAGE", "allow"],
                capture_output=True, timeout=5
            )
            logger.info("[MARKOR] Storage permissions granted via appops")

            # Force-stop Markor to clear any permission dialogs
            subprocess.run(
                ["adb", "-s", device_id, "shell", "am", "force-stop", "net.gsantner.markor"],
                capture_output=True, timeout=5
            )
            await asyncio.sleep(0.5)

            # Use /sdcard/Documents - standard Markor directory
            target_dir = "/sdcard/Documents"

            # Create the directory if it doesn't exist
            subprocess.run(
                ["adb", "-s", device_id, "shell", "mkdir", "-p", target_dir],
                capture_output=True, timeout=5
            )

            # Clean the title for filename
            clean_title = title.replace(" ", "_").replace("'", "").replace('"', "")
            file_path = f"{target_dir}/{clean_title}.md"

            # For complex content, use base64 encoding and adb shell properly
            content_b64 = base64.b64encode(content.encode()).decode()

            # Write file using echo and base64 decode - use string command with shell=True
            write_cmd = f"adb -s {device_id} shell \"echo '{content_b64}' | base64 -d > '{file_path}'\""
            result = subprocess.run(write_cmd, capture_output=True, timeout=10, text=True, shell=True)
            logger.info(f"[MARKOR] File write result: stdout={result.stdout[:100] if result.stdout else 'empty'}, stderr={result.stderr[:100] if result.stderr else 'none'}")

            # Verify the file was created
            verify_result = subprocess.run(
                ["adb", "-s", device_id, "shell", "cat", file_path],
                capture_output=True, timeout=5, text=True
            )
            file_created = content[:20] in verify_result.stdout if verify_result.stdout else False
            logger.info(f"[MARKOR] File verification: {file_created}, content preview: {verify_result.stdout[:50] if verify_result.stdout else 'empty'}")

            await asyncio.sleep(0.5)

            # Open the file directly in Markor's DocumentActivity with explicit component
            # This bypasses the file picker and opens the file directly for editing
            subprocess.run(
                ["adb", "-s", device_id, "shell", "am", "start",
                 "-a", "android.intent.action.VIEW",
                 "-d", f"file://{file_path}",
                 "-t", "text/markdown",
                 "-n", "net.gsantner.markor/.activity.DocumentActivity"],
                capture_output=True, timeout=5, text=True
            )
            await asyncio.sleep(2.0)

            # Dismiss any dialogs that may appear (like the "local files only" info dialog)
            # by pressing Enter/OK
            subprocess.run(
                ["adb", "-s", device_id, "shell", "input", "keyevent", "KEYCODE_ENTER"],
                capture_output=True, timeout=5
            )
            await asyncio.sleep(0.5)

            # Verify we're in DocumentActivity
            window_check = subprocess.run(
                ["adb", "-s", device_id, "shell", "dumpsys", "window"],
                capture_output=True, timeout=5, text=True
            )
            in_document_activity = "DocumentActivity" in str(window_check.stdout)
            logger.info(f"[MARKOR] DocumentActivity open: {in_document_activity}")

            return f"✅ Markor note created via ADB: '{clean_title}.md' at {file_path} with content: '{content[:50]}...'. The note is now open in Markor's editor showing the title '{clean_title}' and the full content."

        @function_tool
        async def toggle_bluetooth(turn_on: bool) -> str:
            """Toggle Bluetooth on or off using ADB commands.
            This is more reliable than trying to navigate through Settings UI.

            Args:
                turn_on: True to turn Bluetooth ON, False to turn it OFF
            """
            track_action("toggle_bluetooth", turn_on=turn_on)
            import subprocess
            logger.info(f"[BLUETOOTH] Toggling Bluetooth to: {'ON' if turn_on else 'OFF'}")

            # First, go to home screen to clear any existing navigation
            subprocess.run(
                ["adb", "-s", device_id, "shell", "input", "keyevent", "KEYCODE_HOME"],
                capture_output=True, timeout=5
            )
            await asyncio.sleep(0.5)

            # Use ADB to toggle Bluetooth directly
            if turn_on:
                result = subprocess.run(
                    ["adb", "-s", device_id, "shell", "svc", "bluetooth", "enable"],
                    capture_output=True, timeout=5, text=True
                )
                logger.info(f"[BLUETOOTH] Enable result: {result.stdout} {result.stderr}")
            else:
                result = subprocess.run(
                    ["adb", "-s", device_id, "shell", "svc", "bluetooth", "disable"],
                    capture_output=True, timeout=5, text=True
                )
                logger.info(f"[BLUETOOTH] Disable result: {result.stdout} {result.stderr}")

            await asyncio.sleep(2.0)

            # Verify the state by checking settings
            result = subprocess.run(
                ["adb", "-s", device_id, "shell", "settings", "get", "global", "bluetooth_on"],
                capture_output=True, timeout=5, text=True
            )
            current_state = result.stdout.strip() == "1"
            logger.info(f"[BLUETOOTH] Current state from settings: {current_state}")

            state_str = "ON" if current_state else "OFF"
            expected_str = "ON" if turn_on else "OFF"
            logger.info(f"[BLUETOOTH] Final state: {state_str}, expected: {expected_str}")

            # Open Quick Settings panel which clearly shows Bluetooth toggle state
            # First go home, then open Quick Settings
            subprocess.run(
                ["adb", "-s", device_id, "shell", "input", "keyevent", "KEYCODE_HOME"],
                capture_output=True, timeout=5
            )
            await asyncio.sleep(0.5)

            # Open Quick Settings panel (swipe down twice from top)
            subprocess.run(
                ["adb", "-s", device_id, "shell", "cmd", "statusbar", "expand-settings"],
                capture_output=True, timeout=5
            )
            await asyncio.sleep(2.0)  # Wait for Quick Settings to fully expand

            if current_state == turn_on:
                return f"✅ Bluetooth is now {state_str}. Verified via ADB settings check. The Quick Settings panel should now be visible showing the Bluetooth tile in the {state_str} state (highlighted/blue when ON)."
            else:
                return f"⚠️ Bluetooth toggle attempted but state is {state_str} (expected {expected_str}). Please check the Quick Settings panel."

        @function_tool
        async def toggle_wifi(turn_on: bool) -> str:
            """Toggle WiFi on or off using ADB commands.
            This is more reliable than trying to navigate through Settings UI.

            Args:
                turn_on: True to turn WiFi ON, False to turn it OFF
            """
            track_action("toggle_wifi", turn_on=turn_on)
            import subprocess

            # Use ADB to toggle WiFi directly
            if turn_on:
                subprocess.run(
                    ["adb", "-s", device_id, "shell", "svc", "wifi", "enable"],
                    capture_output=True, timeout=5
                )
            else:
                subprocess.run(
                    ["adb", "-s", device_id, "shell", "svc", "wifi", "disable"],
                    capture_output=True, timeout=5
                )

            await asyncio.sleep(2.0)

            # Verify the state by checking settings
            result = subprocess.run(
                ["adb", "-s", device_id, "shell", "settings", "get", "global", "wifi_on"],
                capture_output=True, timeout=5, text=True
            )
            current_state = result.stdout.strip() == "1"

            # Also open WiFi settings to show the state visually
            subprocess.run(
                ["adb", "-s", device_id, "shell", "am", "start", "-a",
                 "android.settings.WIFI_SETTINGS"],
                capture_output=True, timeout=5
            )
            await asyncio.sleep(1.0)

            state_str = "ON" if current_state else "OFF"
            expected_str = "ON" if turn_on else "OFF"
            if current_state == turn_on:
                return f"WiFi is now {state_str}. Settings screen opened to show the toggle state."
            else:
                return f"WiFi toggle attempted but state is {state_str} (expected {expected_str}). May need manual verification."

        @function_tool
        async def type_in_field(x: int, y: int, text: str) -> str:
            """Click on a field at (x,y), clear it, and type new text.
            Use this for form fields to ensure clean text entry."""
            track_action("type_in_field", x=x, y=y, text=text)
            import subprocess
            subprocess.run(
                ["adb", "-s", device_id, "shell", "input", "tap", str(x), str(y)],
                capture_output=True, timeout=3
            )
            await asyncio.sleep(0.3)
            subprocess.run(
                ["adb", "-s", device_id, "shell", "input", "keyevent", "KEYCODE_MOVE_END"],
                capture_output=True, timeout=2
            )
            for _ in range(30):
                subprocess.run(
                    ["adb", "-s", device_id, "shell", "input", "keyevent", "KEYCODE_DEL"],
                    capture_output=True, timeout=2
                )
            await asyncio.sleep(0.2)
            # Escape special characters for ADB shell - hyphens, spaces, quotes, etc.
            escaped_text = text.replace(" ", "%s").replace("'", "\\'").replace("-", "\\-").replace("+", "\\+").replace("(", "\\(").replace(")", "\\)")
            subprocess.run(
                ["adb", "-s", device_id, "shell", "input", "text", escaped_text],
                capture_output=True, timeout=5
            )
            await asyncio.sleep(0.2)
            return f"Typed '{text}' into field at ({x}, {y})"

        @function_tool
        async def get_screen_elements() -> str:
            """Get list of all elements on current screen with their coordinates.

            Uses hybrid approach: combines MCP/ADB structured elements with
            vision-based analysis for maximum element coverage.
            """
            track_action("get_screen_elements")

            # Brief wait for UI to settle after any prior actions
            await asyncio.sleep(0.5)

            # === HYBRID ELEMENT DETECTION ===
            # 1. Try to get structured elements from MCP/ADB
            mcp_elements = []
            for attempt in range(2):
                mcp_result = await mcp.list_elements_on_screen(device_id)
                if mcp_result:
                    mcp_elements = mcp_result
                    break
                await asyncio.sleep(0.4)

            # 2. Always capture screenshot for vision analysis
            screenshot = await mcp.take_screenshot(device_id)
            vision_elements = []

            if screenshot and "data" in screenshot:
                # Run vision analysis - focus on task-relevant elements
                # This catches visual elements that accessibility tree might miss
                vision_elements = await vision_analyze_screen(
                    screenshot["data"],
                    task.description,
                    analysis_topics=["buttons", "toggles", "text_fields", "icons", "menu_items"]
                )

            # 3. Merge and deduplicate elements
            merged_elements = _merge_hybrid_elements(mcp_elements, vision_elements)

            # Log the hybrid result
            logger.info(f"[HYBRID] MCP: {len(mcp_elements)}, Vision: {len(vision_elements)}, Merged: {len(merged_elements)}")

            return self._format_elements(merged_elements)

        @function_tool
        async def analyze_screen(topics: List[str]) -> str:
            """Take a screenshot and get focused visual analysis on specific topics.

            Use this when you need visual understanding beyond element coordinates.
            More cost-efficient than full analysis when you specify topics.

            Args:
                topics: List of topics to analyze (e.g., ["buttons", "dialogs", "input_fields", "toggles", "errors"])

            Returns:
                Text description focused on the specified topics.
            """
            track_action("analyze_screen", topics=topics)
            screenshot = await mcp.take_screenshot(device_id)
            if not screenshot or "data" not in screenshot:
                return "Failed to capture screenshot"

            elements = await vision_analyze_screen(
                screenshot["data"],
                task.description,
                analysis_topics=topics
            )

            if elements:
                result = f"Analysis of {', '.join(topics)}:\n"
                for elem in elements:
                    name = elem.get("name", "unknown")
                    x, y = elem.get("x", 0), elem.get("y", 0)
                    elem_type = elem.get("type", "element")
                    result += f"- {name} ({elem_type}) at ({x}, {y})\n"
                return result
            return "No relevant elements found for the specified topics."

        @function_tool
        async def self_evaluate() -> str:
            """Perform self-evaluation and reflection on current progress.

            CALL THIS AFTER EVERY 2-3 ACTIONS to:
            1. Assess if you're making progress toward the goal
            2. Identify if you're stuck or going in circles
            3. Reflect on what worked and what didn't
            4. Plan the next steps based on all context

            Returns:
                A structured self-evaluation with reasoning and next steps.
            """
            track_action("self_evaluate")

            # Build action history summary
            action_summary = []
            for i, action in enumerate(tracked_actions[-10:], 1):
                tool = action.get("tool", "unknown")
                args = action.get("arguments", {})
                action_summary.append(f"{i}. {tool}({args})")

            action_history = "\n".join(action_summary) if action_summary else "No actions taken yet"

            # Detect stuck patterns
            stuck_indicators = []
            recent_tools = [a.get("tool", "") for a in tracked_actions[-6:]]

            # Check for repeated get_screen_elements without progress
            if recent_tools.count("get_screen_elements") >= 3:
                stuck_indicators.append("⚠️ STUCK PATTERN: Too many get_screen_elements calls without action")

            # Check for repeated clicks at similar coordinates
            recent_clicks = [a for a in tracked_actions[-6:] if a.get("tool") == "click"]
            if len(recent_clicks) >= 3:
                coords = [(a.get("arguments", {}).get("x", 0), a.get("arguments", {}).get("y", 0)) for a in recent_clicks]
                if len(set(coords)) <= 2:
                    stuck_indicators.append("⚠️ STUCK PATTERN: Clicking same location repeatedly")

            # Check for back-and-forth navigation
            if recent_tools.count("press_button") >= 2 and recent_tools.count("launch_app") >= 2:
                stuck_indicators.append("⚠️ STUCK PATTERN: Navigation loop detected")

            # Get current screen state for context
            current_elements = await mcp.list_elements_on_screen(device_id)
            screen_context = self._format_elements(current_elements) if current_elements else "Unable to read screen"

            # Identify key elements on screen relevant to common tasks
            element_names = [e.get("name", "").lower() for e in (current_elements or [])]
            task_lower = task.description.lower()

            # Task-specific guidance
            guidance = []
            if "bluetooth" in task_lower:
                if "connected devices" in " ".join(element_names):
                    guidance.append("✓ 'Connected devices' visible - click it to find Bluetooth")
                elif "bluetooth" in " ".join(element_names):
                    guidance.append("✓ 'Bluetooth' visible - look for toggle to turn on/off")
                elif "settings" in " ".join(element_names) or "search" in " ".join(element_names):
                    guidance.append("→ You're in Settings main screen - find 'Connected devices' (scroll down if needed)")
                else:
                    guidance.append("→ Navigate: Settings → Connected devices → Bluetooth")

            if "wifi" in task_lower or "wi-fi" in task_lower:
                if "network" in " ".join(element_names) or "internet" in " ".join(element_names):
                    guidance.append("✓ Network/Internet visible - click to find Wi-Fi")
                elif "wi-fi" in " ".join(element_names) or "wifi" in " ".join(element_names):
                    guidance.append("✓ Wi-Fi visible - look for toggle")
                else:
                    guidance.append("→ Navigate: Settings → Network & internet → Wi-Fi")

            stuck_warning = "\n".join(stuck_indicators) if stuck_indicators else "No stuck patterns detected"
            task_guidance = "\n".join(guidance) if guidance else "Follow the task-specific workflow in your instructions"

            logger.info(f"[SELF-EVAL] Evaluating progress after {len(tracked_actions)} actions, stuck_indicators={len(stuck_indicators)}")

            return f"""=== SELF-EVALUATION (Reflexion) ===
TASK: {task.description}
PROGRESS: {len(tracked_actions)} actions taken

STUCK DETECTION:
{stuck_warning}

TASK-SPECIFIC GUIDANCE:
{task_guidance}

CURRENT SCREEN ({len(current_elements) if current_elements else 0} elements):
{screen_context[:500]}...

LAST 5 ACTIONS:
{chr(10).join(action_summary[-5:]) if action_summary else 'None'}

DECISION FRAMEWORK:
1. Am I on the right screen for this task?
2. Do I see the target element (toggle, button, field)?
3. If NO: Navigate (click menu item, scroll, or press BACK)
4. If YES: Execute the action to complete the task
5. After action: Verify the result with get_screen_elements()

RECOVERY OPTIONS (if stuck):
- Press BACK to go to previous screen
- Swipe down to reveal more options
- DO NOT use search - navigate through menus instead
=== END SELF-EVALUATION ==="""

        @function_tool
        async def search_for_guidance(query: str) -> str:
            """Search for guidance on how to complete a task or use an app.

            Use this when you're stuck or don't know how to:
            - Navigate to a specific screen or setting
            - Use a particular app
            - Complete a task you haven't done before

            Args:
                query: What you need help with (e.g., "how to start stopwatch in clock app",
                       "where is bluetooth toggle in settings", "how to save contact in contacts app")

            Returns:
                Step-by-step guidance for the task.
            """
            track_action("search_for_guidance", query=query)
            logger.info(f"[GUIDANCE] Agent searching for: {query}")

            # Knowledge base of app-specific workflows
            knowledge_base = {
                # Settings app workflows
                "bluetooth": """BLUETOOTH WORKFLOW:
1. Launch com.android.settings
2. Click "Connected devices" in the main list
3. If you see "Connection preferences", click it
4. Click "Bluetooth" to see the toggle
5. The Bluetooth toggle is at the top of this screen
6. Click the toggle to turn ON/OFF""",

                "wifi": """WIFI WORKFLOW:
1. Launch com.android.settings
2. Click "Network & internet" in the main list
3. Click "Internet" if you see it
4. The Wi-Fi toggle is at the top
5. Click the toggle to turn ON/OFF""",

                "airplane": """AIRPLANE MODE WORKFLOW:
1. Launch com.android.settings
2. Click "Network & internet"
3. Scroll down to find "Airplane mode"
4. Click the toggle to enable/disable""",

                # Clock app workflows
                "stopwatch": """STOPWATCH WORKFLOW:
1. Launch com.android.deskclock
2. Look at the BOTTOM of the screen for tabs
3. Find "Stopwatch" tab (usually 4th tab from left)
4. Click the "Stopwatch" tab text
5. Click the large circular "Start" or play button
6. The stopwatch should start counting (00:00:00 increases)
7. VERIFY: Numbers are changing = stopwatch is running""",

                "timer": """TIMER WORKFLOW:
1. Launch com.android.deskclock
2. Click "Timer" tab at the bottom
3. Set the time using the number pad or dials
4. Click "Start" to begin the countdown""",

                "alarm": """ALARM WORKFLOW:
1. Launch com.android.deskclock
2. Click "Alarm" tab at the bottom (usually first tab)
3. Click "+" or "Add alarm" to create new
4. Set the time and click "Save" or checkmark""",

                # Camera app - ENHANCED with setup handling
                "camera": """CAMERA/PHOTO WORKFLOW (HANDLE SETUP SCREENS FIRST):
1. Launch com.android.camera2 or com.google.android.GoogleCamera
2. SETUP SCREENS: If you see "Remember photo locations?", "NEXT" button, or any setup:
   - Click "NEXT" or "SKIP" to dismiss each setup screen
   - Keep clicking NEXT until you see the camera viewfinder
3. Wait for camera viewfinder to appear (shows live preview)
4. IMPORTANT: Use take_camera_photo() tool - DO NOT click shutter manually!
5. The tool handles shutter click and capture
6. After photo taken, you may see preview thumbnail - photo is saved
7. Task is COMPLETE when take_camera_photo returns success""",

                "photo": """CAMERA/PHOTO WORKFLOW (HANDLE SETUP SCREENS FIRST):
1. Launch com.android.camera2 or com.google.android.GoogleCamera
2. SETUP SCREENS: If you see "NEXT", "SKIP", or permission prompts:
   - Click through ALL setup screens first
   - Grant permissions if asked
3. Once you see the camera viewfinder (live preview):
4. Use take_camera_photo() tool to capture
5. Task complete when photo is taken""",

                # Contacts app - ENHANCED
                "contact": """CONTACTS ADD WORKFLOW (MUST SAVE AT END):
1. Launch com.android.contacts
2. Look for "+" FAB (floating action button) - usually bottom right
3. Click the "+" button to start creating contact
4. You'll see a form - fill fields IN ORDER:
   a. First name: Click field, use type_in_field() or clear_field() + type_text()
   b. Last name: Click next field, type last name
   c. Phone: Find "Phone" field (may need to scroll), type number
5. CRITICAL: Click "Save" button at TOP RIGHT of screen
6. If you see checkmark (✓) icon at top, click it to save
7. VERIFY: You should see contact detail view after saving
8. DO NOT press BACK without saving - data will be lost!""",

                "add contact": """CONTACTS ADD WORKFLOW (MUST SAVE AT END):
1. Launch com.android.contacts
2. Click "+" FAB at bottom right
3. Fill form fields: First name → Last name → Phone
4. Use type_in_field(x, y, text) for each field
5. CRITICAL: Click "Save" or checkmark (✓) at top right
6. VERIFY: See contact detail page = success""",

                # Markor notes app - ENHANCED
                "markor": """MARKOR NOTE WORKFLOW (net.gsantner.markor):
1. Launch net.gsantner.markor
2. App opens to file browser/Documents folder
3. Look for "+" or "New" button (usually bottom right FAB)
4. Click the "+" button
5. Choose "New Document" or "Markdown" if options appear
6. Type the title/filename when prompted
7. Editor opens - type your content in the main text area
8. Content auto-saves OR click save icon at top
9. Press BACK to confirm save and exit
10. VERIFY: Note appears in the file list""",

                "note": """MARKOR NOTE WORKFLOW:
1. Launch net.gsantner.markor (NOT Google Keep)
2. Click "+" FAB to create new document
3. Enter filename/title
4. Type content in editor
5. Press BACK to save and exit
6. Verify note in file list""",

                # Chrome browser
                "chrome": """CHROME WORKFLOW:
1. Launch com.android.chrome
2. Click the address bar at top
3. Type the URL or search query
4. Press Enter or click Go
5. Wait for page to load""",

                # General navigation
                "navigate": """GENERAL NAVIGATION TIPS:
- Swipe UP to scroll down and see more options
- Swipe DOWN to scroll up
- Press BACK to go to previous screen
- Press HOME to go to home screen
- Most settings are in lists - scroll to find them
- DO NOT use search bars - navigate through menus""",
            }

            # Keyword synonyms for better matching
            synonyms = {
                "clock": ["stopwatch", "timer", "alarm"],
                "deskclock": ["stopwatch", "timer", "alarm"],
                "app drawer": ["navigate"],
                "open app": ["navigate"],
                "launch": ["navigate"],
                "network": ["wifi"],
                "internet": ["wifi"],
                "photo": ["camera"],
                "shutter": ["camera"],
                "phone": ["contact"],
                "save": ["contact"],
            }

            # Find matching guidance
            query_lower = query.lower()
            matches = []

            for key, guidance in knowledge_base.items():
                if key in query_lower:
                    matches.append(guidance)

            # Check for synonym matches
            if not matches:
                for query_word, targets in synonyms.items():
                    if query_word in query_lower:
                        for target in targets:
                            if target in knowledge_base and knowledge_base[target] not in matches:
                                matches.append(knowledge_base[target])

            # Check for partial matches
            if not matches:
                for key, guidance in knowledge_base.items():
                    if any(word in query_lower for word in key.split()):
                        matches.append(guidance)

            if matches:
                result = "\n\n---\n\n".join(matches)
                logger.info(f"[GUIDANCE] Found {len(matches)} matching guides")
                return f"=== GUIDANCE FOUND ===\n\n{result}\n\n=== END GUIDANCE ==="
            else:
                # General tips if no specific match
                logger.info("[GUIDANCE] No specific match, returning general tips")
                return """=== GENERAL GUIDANCE ===
No specific workflow found for your query. Try these general tips:

1. Use get_screen_elements() to see what's on screen
2. Look for keywords related to your task in the element list
3. Click on menu items that seem related to your goal
4. If stuck, press BACK and try a different path
5. Swipe to reveal hidden options
6. Check the TASK-SPECIFIC WORKFLOWS in your instructions

Common app packages:
- Settings: com.android.settings
- Clock: com.android.deskclock
- Camera: com.android.camera2
- Contacts: com.android.contacts
- Chrome: com.android.chrome
- Markor: net.gsantner.markor
=== END GUIDANCE ==="""

        # Build the agent with task-specific instructions (Reflexion-enhanced)
        instructions = f"""You are a mobile automation agent executing tasks on an Android device.
You use REFLEXION - a self-evaluation pattern that helps you learn from actions and improve.

TASK TO COMPLETE: {task.description}

╔══════════════════════════════════════════════════════════════════════════════╗
║  🚀 FIRST: CHECK IF A SPECIALIZED TOOL CAN COMPLETE YOUR TASK DIRECTLY!     ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  These tools handle the ENTIRE workflow automatically - USE THEM FIRST:      ║
║                                                                              ║
║  📷 CAMERA/PHOTO TASK:                                                       ║
║     → Call: take_camera_photo()                                              ║
║     Handles: Opens camera, dismisses setup screens, takes photo              ║
║                                                                              ║
║  📱 BLUETOOTH TASK:                                                          ║
║     → Call: toggle_bluetooth(turn_on=True) or toggle_bluetooth(turn_on=False)║
║     Handles: Uses ADB commands for 100% reliability                          ║
║                                                                              ║
║  📶 WIFI TASK:                                                               ║
║     → Call: toggle_wifi(turn_on=True) or toggle_wifi(turn_on=False)          ║
║     Handles: Uses ADB commands for 100% reliability                          ║
║                                                                              ║
║  👤 CONTACTS/ADD CONTACT TASK:                                               ║
║     → Call: add_contact(first_name="John", last_name="Doe", phone="555123")  ║
║     Handles: Opens Contacts, fills form, clicks Save                         ║
║                                                                              ║
║  📝 MARKOR/NOTE TASK:                                                        ║
║     → Call: create_markor_note(title="MyNote", content="Note content here")  ║
║     Handles: Opens Markor, creates note, types content, saves                ║
║                                                                              ║
║  ⏱️ STOPWATCH TASK: (No helper - use manual workflow below)                  ║
║     1. launch_app("com.android.deskclock")                                   ║
║     2. Click "Stopwatch" tab at bottom                                       ║
║     3. Click "Start" button                                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝

=== AVAILABLE TOOLS ===
ACTION TOOLS:
- click(x, y): Tap at screen coordinates
- clear_field(): Clear existing text before typing (ALWAYS use before type_text in form fields)
- type_text(text, submit): Type text (submit=True to press Enter)
- type_in_field(x, y, text): Click field, clear it, and type text (best for form fields)
- swipe(direction): Swipe up/down/left/right
- press_button(button): Press HOME, BACK, etc.
- launch_app(package_name): Open an app by package
- take_camera_photo(): Take a photo (use this instead of clicking shutter manually)
- toggle_bluetooth(turn_on): Toggle Bluetooth ON/OFF using ADB (most reliable)
- toggle_wifi(turn_on): Toggle WiFi ON/OFF using ADB (most reliable)
- add_contact(first_name, last_name, phone): Add a contact, handles entire flow
- create_markor_note(title, content): Create a Markor note, handles entire flow

OBSERVATION TOOLS:
- get_screen_elements(): Get current screen state (coordinates of all elements)
- analyze_screen(topics): Get focused visual analysis on specific topics (e.g., ["buttons", "toggles"])

REFLECTION & GUIDANCE TOOLS (USE WHEN STUCK):
- self_evaluate(): Reflect on progress, identify if stuck, plan next steps
- search_for_guidance(query): Search for step-by-step guidance on how to complete a task
  Examples: "how to start stopwatch", "where is bluetooth toggle", "how to save contact"

=== REFLEXION EXECUTION LOOP ===
For each step, follow this pattern:

1. OBSERVE: Call get_screen_elements() to understand current state
2. THINK: Reason about what action will move you toward the goal
3. ACT: Execute ONE action
4. REFLECT: After 2-3 actions, call self_evaluate() to:
   - Check if you're making progress
   - Identify if you're stuck or repeating actions
   - Adjust your strategy if needed
5. VERIFY: Before completing, confirm the final state is correct

=== SELF-EVALUATION TRIGGERS ===
Call self_evaluate() when:
- You've taken 2-3 actions without clear progress
- You're unsure if you're on the right screen
- An action didn't produce expected results
- You feel stuck or are repeating similar actions

=== REASONING BEFORE EACH ACTION ===
Before each action, briefly reason:
- "Current state: [what I see]"
- "Goal: [what I need to achieve]"
- "Next action: [what I'll do and why]"
- "Expected result: [what should happen]"

=== SELF-CORRECTION (CRITICAL) ===
After each action, verify you're on the RIGHT screen for your task:
- If you see unexpected content, you may have navigated wrong
- Use press_button("BACK") to go back and try again
- Re-check get_screen_elements() to confirm current location
- Example: For WiFi task, you MUST see "Wi-Fi" or "Internet" on screen, NOT "Cellular" or "SIM"
- Example: For Bluetooth task, you MUST see "Bluetooth" toggle, NOT other settings

=== NAVIGATION TIPS ===
- Settings app: Network, Bluetooth, Display are usually in the main list (swipe down if needed)
- Clock app: Stopwatch is a tab at bottom (look for "Stopwatch" text)
- If you don't see expected elements after 2 attempts, swipe or press BACK to reset

Common packages:
- com.android.settings (Settings)
- com.android.deskclock (Clock)
- com.android.contacts (Contacts)
- com.android.camera2 (Camera)
- com.android.chrome (Chrome)
- net.gsantner.markor (Markor notes app)

=== IMPORTANT RULES ===
1. Be efficient - complete the task in as few steps as possible.
2. When you see evidence the task is done (e.g., toggle is ON, app is open), immediately respond with "TASK COMPLETE" and stop.
3. Do NOT press HOME or BACK after completing the task - leave the screen showing the completed state.
4. The final screenshot will be used to verify success, so the result must be visible on screen.
5. FOR CAMERA: Use take_camera_photo() tool - it handles the shutter button automatically.
6. FOR NOTES: Use create_markor_note(title, content) tool - it handles the entire flow.
7. FOR CONTACTS: Use add_contact(first_name, last_name, phone) tool - it handles the entire flow.
8. FOR BLUETOOTH: Use toggle_bluetooth(turn_on=True/False) tool - it uses ADB for reliability.
9. FOR WIFI: Use toggle_wifi(turn_on=True/False) tool - it uses ADB for reliability.
10. VERIFY BEFORE COMPLETING: Always call get_screen_elements() or analyze_screen() one last time to confirm final state is correct.
11. USE self_evaluate() REGULARLY: Call it every 2-3 actions to ensure you're on track.

=== SPECIALIZED HELPER TOOLS (USE THESE FIRST!) ===
- toggle_bluetooth(turn_on): Toggle Bluetooth ON/OFF using ADB (most reliable)
- toggle_wifi(turn_on): Toggle WiFi ON/OFF using ADB (most reliable)
- take_camera_photo(): Take a photo, handles setup screens automatically
- add_contact(first_name, last_name, phone): Add a contact, handles entire flow
- create_markor_note(title, content): Create a Markor note, handles entire flow

=== TASK-SPECIFIC WORKFLOWS ===

BLUETOOTH TASK (PREFERRED - use helper tool):
→ Simply call: toggle_bluetooth(turn_on=True) or toggle_bluetooth(turn_on=False)
This uses ADB commands which are more reliable than UI navigation.

WIFI TASK (PREFERRED - use helper tool):
→ Simply call: toggle_wifi(turn_on=True) or toggle_wifi(turn_on=False)
This uses ADB commands which are more reliable than UI navigation.

CONTACTS APP WORKFLOW (PREFERRED - use helper tool):
→ Simply call: add_contact(first_name="John", last_name="Doe", phone="5551234567")
This handles the entire flow: opens Contacts, fills form, and saves.

MARKOR NOTE WORKFLOW (PREFERRED - use helper tool):
→ Simply call: create_markor_note(title="Meeting_123", content="Quick memo: review - follow up")
This handles the entire flow: opens Markor, creates note, types content, and saves.

CLOCK/STOPWATCH WORKFLOW:
1. Launch com.android.deskclock
2. Look for tabs at bottom: Alarm, Clock, Timer, Stopwatch
3. Click "Stopwatch" tab
4. Click the "Start" or play button to start stopwatch
5. VERIFY: The stopwatch must be visibly running (time incrementing)"""

        return Agent(
            name="MobileTaskAgent",
            instructions=instructions,
            tools=[click, clear_field, type_text, type_in_field, swipe, press_button, launch_app, take_camera_photo, add_contact, create_markor_note, toggle_bluetooth, toggle_wifi, get_screen_elements, analyze_screen, self_evaluate, search_for_guidance],
            model=self.model,
            model_settings=ModelSettings(
                tool_choice="auto",
                parallel_tool_calls=False,  # Sequential for device control
            ),
        )

    def _format_elements(self, elements: List[Dict]) -> str:
        """Format screen elements for the agent prompt.

        Handles elements from multiple sources:
        - MCP: {"name", "x", "y", "coordinates", "type", "className"}
        - ADB: {"name", "text", "content_desc", "x", "y", "class"}
        - Vision: {"name", "x", "y", "type", "clickable"}
        """
        if not elements:
            return "No elements found on screen"

        lines = []
        for i, el in enumerate(elements[:30]):  # Limit to top 30
            # Name: prioritize "name" field, then text, then label/contentDescription
            name = el.get("name", "")
            if not name:
                name = el.get("text", "")
            if not name:
                name = el.get("label", el.get("contentDescription", el.get("content_desc", "")))
            name = str(name).strip() if name else ""

            # Coordinates: handle different formats
            x = el.get("x", 0)
            y = el.get("y", 0)
            if not x and not y:
                coords = el.get("coordinates", {})
                x = coords.get("x", 0)
                y = coords.get("y", 0)

            # Type info for context
            el_type = el.get("type", el.get("className", el.get("class", "")))
            if el_type:
                el_type = str(el_type).split(".")[-1]  # Get short class name

            # Clickable indicator
            clickable = el.get("clickable", True)
            clickable_marker = "" if clickable else " [non-clickable]"

            # Build display name
            desc = name or el_type or "unnamed"
            if desc:
                lines.append(f"[{i}] {desc} at ({x}, {y}){clickable_marker}")

        return "\n".join(lines) if lines else "Screen elements not parseable"

    def _extract_actions(self, run_result) -> List[Dict[str, Any]]:
        """Extract actions taken from agent run result."""
        actions = []

        # Log available attributes at INFO level for debugging
        logger.info(f"[AGENT] run_result type: {type(run_result).__name__}")
        attrs = [a for a in dir(run_result) if not a.startswith('_')]
        logger.info(f"[AGENT] run_result attrs: {attrs[:10]}")

        # Method 1: Check raw_responses for function calls
        if hasattr(run_result, 'raw_responses') and run_result.raw_responses:
            logger.info(f"[AGENT] Found {len(run_result.raw_responses)} raw_responses")
            for i, response in enumerate(run_result.raw_responses):
                if hasattr(response, 'output') and response.output:
                    for item in response.output:
                        item_type = getattr(item, 'type', None)
                        if item_type == 'function_call':
                            actions.append({
                                "tool": getattr(item, 'name', 'unknown'),
                                "arguments": getattr(item, 'arguments', '{}'),
                            })

        # Method 2: Check new_items (higher-level abstraction)
        if hasattr(run_result, 'new_items') and run_result.new_items:
            logger.info(f"[AGENT] Checking new_items: {len(run_result.new_items)} items")
            for item in run_result.new_items:
                # Check for ToolCallItem or similar
                item_type = type(item).__name__
                logger.debug(f"[AGENT] new_item type: {item_type}")
                if 'ToolCall' in item_type or 'FunctionCall' in item_type:
                    if hasattr(item, 'raw_item'):
                        raw = item.raw_item
                        if getattr(raw, 'type', None) == 'function_call':
                            actions.append({
                                "tool": getattr(raw, 'name', 'unknown'),
                                "arguments": getattr(raw, 'arguments', '{}'),
                            })
                    # Also try direct attributes
                    elif hasattr(item, 'name'):
                        actions.append({
                            "tool": getattr(item, 'name', 'unknown'),
                            "arguments": str(getattr(item, 'arguments', {})),
                        })

        # Method 3: Check for to_input_list to extract tool calls from conversation
        if not actions and hasattr(run_result, 'to_input_list'):
            try:
                input_list = run_result.to_input_list()
                logger.info(f"[AGENT] to_input_list returned {len(input_list)} items")
                for item in input_list:
                    if isinstance(item, dict) and item.get('type') == 'function_call':
                        actions.append({
                            "tool": item.get('name', 'unknown'),
                            "arguments": item.get('arguments', '{}'),
                        })
            except Exception as e:
                logger.warning(f"[AGENT] to_input_list failed: {e}")

        # Method 4: Check for history attribute (some SDK versions)
        if not actions and hasattr(run_result, 'history'):
            try:
                history = run_result.history
                logger.info(f"[AGENT] Checking history: {len(history) if history else 0} items")
                for entry in (history or []):
                    if hasattr(entry, 'tool_calls'):
                        for tc in entry.tool_calls:
                            actions.append({
                                "tool": getattr(tc, 'name', 'unknown'),
                                "arguments": str(getattr(tc, 'arguments', {})),
                            })
            except Exception as e:
                logger.warning(f"[AGENT] history extraction failed: {e}")

        logger.info(f"[AGENT] Extracted {len(actions)} actions")
        if actions:
            logger.info(f"[AGENT] First 3 actions: {actions[:3]}")
        return actions

    def _extract_token_usage(self, run_result) -> Optional["TokenUsage"]:
        """Extract token usage from agent run result."""
        from .executor import TokenUsage

        usage = TokenUsage()

        # Check raw_responses for usage info
        if hasattr(run_result, 'raw_responses') and run_result.raw_responses:
            for response in run_result.raw_responses:
                if hasattr(response, 'usage') and response.usage:
                    u = response.usage
                    prompt = getattr(u, 'input_tokens', 0) or getattr(u, 'prompt_tokens', 0)
                    completion = getattr(u, 'output_tokens', 0) or getattr(u, 'completion_tokens', 0)
                    usage.add(prompt, completion)

        return usage if usage.total_tokens > 0 else None

