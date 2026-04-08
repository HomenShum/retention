"""
Autonomous Exploration Service

This service enables AI agents to autonomously explore Android emulators
and generate structured exploration reports with thought-action-observation loops.
"""

import os
import time
import asyncio
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from pydantic import BaseModel

from .mobile_mcp_client import MobileMCPClient
from .infrastructure.mcp_appium_client import Platform

logger = logging.getLogger(__name__)


class ThoughtPhase(BaseModel):
    """Represents the reasoning phase before an action"""
    content: str
    duration_ns: int


class ActionPhase(BaseModel):
    """Represents the action being executed"""
    command_name: str
    parameters: Dict[str, Any]


class ObservationPhase(BaseModel):
    """Represents the observation after executing an action"""
    content: str
    duration_ns: int
    formatted_content: Optional[Dict[str, Any]] = None
    screenshot_url: Optional[str] = None
    page_source_snippet: Optional[str] = None
    ui_elements: Optional[List[Dict[str, Any]]] = None


class ExplorationStep(BaseModel):
    """A single step in the exploration process"""
    step_number: int
    timestamp: str
    thought: ThoughtPhase
    action: ActionPhase
    observation: ObservationPhase


class ExplorationReport(BaseModel):
    """Complete exploration report"""
    exploration_id: str
    device_id: str
    start_time: str
    end_time: Optional[str] = None
    status: str  # running, completed, failed
    total_steps: int
    steps: List[ExplorationStep]
    summary: Optional[str] = None


class AutonomousExplorationService:
    """Service for autonomous device exploration"""
    
    def __init__(self):
        self.explorations: Dict[str, ExplorationReport] = {}
        self.mcp_clients: Dict[str, MobileMCPClient] = {}
    
    async def start_exploration(
        self,
        device_id: str,
        max_steps: int = 20,
        exploration_strategy: str = "comprehensive"
    ) -> str:
        """
        Start autonomous exploration of a device
        
        Args:
            device_id: The device to explore
            max_steps: Maximum number of exploration steps
            exploration_strategy: Strategy to use (comprehensive, quick, targeted)
        
        Returns:
            exploration_id: Unique ID for this exploration session
        """
        import uuid
        exploration_id = str(uuid.uuid4())
        
        # Initialize exploration report
        report = ExplorationReport(
            exploration_id=exploration_id,
            device_id=device_id,
            start_time=datetime.now(timezone.utc).isoformat(),
            status="running",
            total_steps=0,
            steps=[]
        )
        
        self.explorations[exploration_id] = report
        
        # Start exploration in background
        asyncio.create_task(
            self._run_exploration(exploration_id, device_id, max_steps, exploration_strategy)
        )
        
        return exploration_id
    
    async def _run_exploration(
        self,
        exploration_id: str,
        device_id: str,
        max_steps: int,
        strategy: str
    ):
        """Run the actual exploration process"""
        report = self.explorations[exploration_id]
        
        try:
            # Create MCP client for this exploration
            mcp_client = MobileMCPClient()
            
            # Start MCP server
            if not await mcp_client.start():
                report.status = "failed"
                report.summary = "Failed to start MCP Appium server"
                return
            
            self.mcp_clients[exploration_id] = mcp_client
            
            # Select platform
            await mcp_client.select_platform(Platform.ANDROID)
            
            # Create session
            capabilities = {
                "platformName": "Android",
                "appium:automationName": "UiAutomator2",
                "appium:deviceName": device_id,
                "appium:udid": device_id,
                "appium:noReset": True
            }
            
            session_id = await mcp_client.create_session(capabilities)
            if not session_id:
                report.status = "failed"
                report.summary = "Failed to create Appium session"
                return
            
            # Run exploration steps
            for step_num in range(1, max_steps + 1):
                step = await self._execute_exploration_step(
                    mcp_client,
                    step_num,
                    device_id,
                    strategy
                )
                
                if step:
                    report.steps.append(step)
                    report.total_steps = len(report.steps)
                
                # Small delay between steps
                await asyncio.sleep(2)
            
            # Close session
            await mcp_client.close_session()
            
            # Mark as completed
            report.status = "completed"
            report.end_time = datetime.now(timezone.utc).isoformat()
            report.summary = f"Completed {report.total_steps} exploration steps successfully"
            
        except Exception as e:
            logger.error(f"Exploration failed: {e}")
            report.status = "failed"
            report.end_time = datetime.now(timezone.utc).isoformat()
            report.summary = f"Exploration failed: {str(e)}"
        
        finally:
            # Cleanup
            if exploration_id in self.mcp_clients:
                await self.mcp_clients[exploration_id].close()
                del self.mcp_clients[exploration_id]
    
    async def _execute_exploration_step(
        self,
        mcp_client: MobileMCPClient,
        step_number: int,
        device_id: str,
        strategy: str
    ) -> Optional[ExplorationStep]:
        """Execute a single exploration step with thought-action-observation"""
        
        try:
            # THOUGHT PHASE
            thought_start = time.time_ns()
            
            # Get current page source to inform decision
            page_source = await mcp_client.get_source()
            
            # Determine next action based on strategy
            thought_content = await self._generate_thought(
                step_number,
                page_source,
                strategy
            )
            
            thought_duration = time.time_ns() - thought_start
            
            thought = ThoughtPhase(
                content=thought_content,
                duration_ns=thought_duration
            )
            
            # ACTION PHASE
            action = await self._determine_action(step_number, page_source, strategy)
            
            # OBSERVATION PHASE
            obs_start = time.time_ns()

            observation_content = await self._execute_action(mcp_client, action)

            obs_duration = time.time_ns() - obs_start

            # Take screenshot for observation
            screenshot_dir = "backend/screenshots/explorations"
            os.makedirs(screenshot_dir, exist_ok=True)
            screenshot_filename = f"exploration_{device_id}_step_{step_number}.png"
            screenshot_path = os.path.join(screenshot_dir, screenshot_filename)
            await mcp_client.screenshot(screenshot_path)

            # Get page source after action for detailed observation
            post_action_source = await mcp_client.get_source()

            # Extract UI elements from page source
            ui_elements = self._extract_ui_elements(post_action_source)

            # Create snippet of page source (first 500 chars)
            page_source_snippet = post_action_source[:500] if post_action_source else None

            observation = ObservationPhase(
                content=observation_content,
                duration_ns=obs_duration,
                screenshot_url=f"/api/exploration/screenshot/{device_id}/{step_number}",
                page_source_snippet=page_source_snippet,
                ui_elements=ui_elements[:10] if ui_elements else None,  # Top 10 elements
                formatted_content={
                    "screenshot_path": screenshot_path,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "total_elements": len(ui_elements) if ui_elements else 0
                }
            )
            
            return ExplorationStep(
                step_number=step_number,
                timestamp=datetime.now(timezone.utc).isoformat(),
                thought=thought,
                action=action,
                observation=observation
            )
            
        except Exception as e:
            logger.error(f"Failed to execute exploration step {step_number}: {e}")
            return None
    
    async def _generate_thought(
        self,
        step_number: int,
        page_source: Optional[str],
        strategy: str
    ) -> str:
        """Generate reasoning for the next action"""
        
        thoughts = []
        
        thoughts.append(f"## Step {step_number} - Planning Phase\n")
        
        if page_source:
            # Analyze page source
            element_count = page_source.count("<node") if page_source else 0
            thoughts.append(f"Current screen contains approximately {element_count} UI elements.")
            
            # Check for common elements
            if "scrollable=\"true\"" in page_source:
                thoughts.append("Detected scrollable content on the screen.")
            if "clickable=\"true\"" in page_source:
                clickable_count = page_source.count("clickable=\"true\"")
                thoughts.append(f"Found {clickable_count} clickable elements.")
        
        # Strategy-based reasoning
        if strategy == "comprehensive":
            thoughts.append("\nUsing comprehensive exploration strategy:")
            thoughts.append("- Systematically explore all interactive elements")
            thoughts.append("- Scroll through content to discover hidden elements")
            thoughts.append("- Navigate through different screens and menus")
        elif strategy == "quick":
            thoughts.append("\nUsing quick exploration strategy:")
            thoughts.append("- Focus on main navigation elements")
            thoughts.append("- Sample key interactions")
        
        thoughts.append(f"\nDeciding on action for step {step_number}...")
        
        return "\n".join(thoughts)
    
    async def _determine_action(
        self,
        step_number: int,
        page_source: Optional[str],
        strategy: str
    ) -> ActionPhase:
        """Determine the next action to take based on page source analysis"""

        # Analyze page source to find interactive elements
        clickable_elements = []
        scrollable_elements = []

        if page_source:
            import re
            # Find clickable elements
            clickable_pattern = r'clickable="true"[^>]*resource-id="([^"]*)"'
            clickable_elements = re.findall(clickable_pattern, page_source)

            # Find scrollable elements
            if 'scrollable="true"' in page_source:
                scrollable_elements.append("scrollable_view")

        # Intelligent action selection based on strategy and page analysis
        if strategy == "comprehensive":
            # Cycle through different exploration actions
            actions = [
                ("GenerateLocators", {}),  # Discover all interactive elements
                ("ScrollDown", {"distance": 500}),
                ("FindAndClickElement", {"index": 0}),  # Click first clickable element
                ("GetPageSource", {}),
                ("ScrollUp", {"distance": 500}),
                ("FindAndClickElement", {"index": 1}),  # Click second clickable element
                ("TakeScreenshot", {}),
                ("PressBack", {}),
            ]
        elif strategy == "quick":
            actions = [
                ("GenerateLocators", {}),
                ("ScrollDown", {"distance": 300}),
                ("GetPageSource", {}),
                ("TakeScreenshot", {}),
            ]
        else:  # targeted
            actions = [
                ("GenerateLocators", {}),
                ("FindAndClickElement", {"index": 0}),
                ("GetPageSource", {}),
            ]

        action_index = (step_number - 1) % len(actions)
        command_name, parameters = actions[action_index]

        return ActionPhase(
            command_name=command_name,
            parameters=parameters
        )
    
    async def _execute_action(
        self,
        mcp_client: MobileMCPClient,
        action: ActionPhase
    ) -> str:
        """Execute the action using MCP Appium tools and return observation"""

        try:
            if action.command_name == "GenerateLocators":
                locators = await mcp_client.generate_locators()
                if locators:
                    element_count = len(locators.get("elements", []))
                    return f"✅ Generated locators for {element_count} interactive elements"
                return "⚠️ No locators generated"

            elif action.command_name == "ScrollDown":
                distance = action.parameters.get("distance", 500)
                success = await mcp_client.scroll(x=500, y=distance)
                return f"✅ Scrolled down {distance}px" if success else "❌ Scroll failed"

            elif action.command_name == "ScrollUp":
                distance = action.parameters.get("distance", 500)
                success = await mcp_client.scroll(x=500, y=-distance)
                return f"✅ Scrolled up {distance}px" if success else "❌ Scroll failed"

            elif action.command_name == "GetPageSource":
                source = await mcp_client.get_source()
                if source:
                    element_count = source.count("<node")
                    clickable_count = source.count('clickable="true"')
                    return f"✅ Retrieved page source: {element_count} elements, {clickable_count} clickable"
                return "❌ Failed to get page source"

            elif action.command_name == "FindAndClickElement":
                index = action.parameters.get("index", 0)
                # Get page source to find clickable elements
                source = await mcp_client.get_source()
                if source:
                    import re
                    # Find clickable elements with resource-id
                    pattern = r'clickable="true"[^>]*resource-id="([^"]*)"'
                    elements = re.findall(pattern, source)

                    if elements and index < len(elements):
                        resource_id = elements[index]
                        element = await mcp_client.find_element("id", resource_id)
                        if element and "elementId" in element:
                            success = await mcp_client.click_element(element["elementId"])
                            return f"✅ Clicked element: {resource_id}" if success else f"❌ Failed to click {resource_id}"
                        return f"⚠️ Element not found: {resource_id}"
                    return f"⚠️ No clickable element at index {index}"
                return "❌ Failed to get page source for element search"

            elif action.command_name == "TakeScreenshot":
                # Screenshot is taken separately in the observation phase
                return "✅ Screenshot captured"

            elif action.command_name == "PressBack":
                # Note: MCP Appium might not have a direct back button command
                # We would need to use execute_script or key event
                return "✅ Pressed back button (simulated)"

            else:
                return f"⚠️ Unknown command: {action.command_name}"

        except Exception as e:
            logger.error(f"Action execution failed: {e}")
            return f"❌ Action failed: {str(e)}"
    
    def get_exploration_report(self, exploration_id: str) -> Optional[ExplorationReport]:
        """Get the current state of an exploration report"""
        return self.explorations.get(exploration_id)
    
    def list_explorations(self) -> List[ExplorationReport]:
        """List all exploration reports"""
        return list(self.explorations.values())

    def _extract_ui_elements(self, page_source: Optional[str]) -> List[Dict[str, Any]]:
        """Extract UI elements from page source XML"""
        if not page_source:
            return []

        elements = []
        try:
            import re
            # Simple regex to extract node attributes
            node_pattern = r'<node[^>]*>'
            nodes = re.findall(node_pattern, page_source)

            for node in nodes[:20]:  # Limit to first 20 nodes
                element = {}

                # Extract common attributes
                attrs = ['text', 'resource-id', 'class', 'content-desc', 'clickable', 'scrollable', 'bounds']
                for attr in attrs:
                    match = re.search(f'{attr}="([^"]*)"', node)
                    if match:
                        element[attr.replace('-', '_')] = match.group(1)

                if element:  # Only add if we found some attributes
                    elements.append(element)

        except Exception as e:
            logger.error(f"Failed to extract UI elements: {e}")

        return elements

