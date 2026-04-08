"""
Unified Bug Reproduction & Test Execution Service

This service provides unified test execution and bug reproduction capabilities:

**Two Modes:**
1. **Scenario Mode** - Execute predefined test scenarios (regression testing)
2. **Bug Reproduction Mode** - Reproduce manual bug reports with evidence collection

**Shared Capabilities:**
- Execute steps on devices via Appium MCP
- Capture evidence (screenshots, element states, logs)
- Parse XML page source for element discovery
- AI-powered step interpretation
- Detailed reporting with visual proof
"""

import os
import base64
import json
import uuid
import asyncio
import xml.etree.ElementTree as ET
from typing import Dict, List, Any, Optional, Callable
from datetime import datetime, timezone
from pathlib import Path
from enum import Enum
import logging
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ExecutionMode(str, Enum):
    """Execution mode for the unified service"""
    SCENARIO = "scenario"  # Predefined test scenarios
    BUG_REPRODUCTION = "bug_reproduction"  # Manual bug reproduction


class BugReportInput(BaseModel):
    """Input model for bug report submission"""
    model_config = {"extra": "forbid"}

    title: str
    description: str
    reproduction_steps: List[str]  # Manual steps like ["Open app", "Tap Settings", "Scroll down"]
    expected_behavior: str
    actual_behavior: str
    device_id: str
    app_package: Optional[str] = None
    severity: str = "medium"  # low, medium, high, critical
    tags: List[str] = []


class BugEvidence(BaseModel):
    """Evidence collected during bug reproduction"""
    model_config = {"extra": "forbid"}

    step_number: int
    step_description: str
    screenshot_base64: Optional[str] = None
    screenshot_path: Optional[str] = None
    page_source: Optional[str] = None
    visible_elements: List[Dict[str, Any]] = []
    element_states: Dict[str, Any] = {}
    logs: List[str] = []
    timestamp: str


class BugReproductionResult(BaseModel):
    """Result of automated bug reproduction"""
    model_config = {"extra": "forbid"}

    bug_id: str
    reproduction_successful: bool
    steps_executed: int
    steps_failed: int
    evidence: List[BugEvidence]
    ai_analysis: str
    recommendations: List[str]
    created_at: str


class TestScenarioInput(BaseModel):
    """Input model for predefined test scenario execution"""
    model_config = {"extra": "forbid"}

    scenario_name: str
    device_id: str
    test_steps: List[Dict[str, Any]]  # Structured steps like [{"action": "scroll", "direction": "down"}]
    session_id: Optional[str] = None


class TestExecutionResult(BaseModel):
    """Result of test scenario execution"""
    model_config = {"extra": "forbid"}

    test_id: str
    scenario_name: str
    device_id: str
    status: str  # passed, failed, error
    steps_executed: int
    steps_failed: int
    steps: List[Dict[str, Any]]
    evidence: List[BugEvidence]
    created_at: str


class UnifiedBugReproductionService:
    """
    Unified service for test execution and bug reproduction

    Supports two modes:
    1. Scenario Mode - Execute predefined test scenarios
    2. Bug Reproduction Mode - Reproduce manual bug reports with evidence
    """

    def __init__(self, screenshots_dir: Optional[str] = None):
        """Initialize unified bug reproduction service"""
        if screenshots_dir:
            # Use provided path as-is (for backward compatibility)
            self.screenshots_dir = Path(screenshots_dir)
        else:
            # Default: resolve to backend/bug_screenshots regardless of CWD
            # Path hierarchy: bug_reproduction_service.py -> bug_reproduction/ -> agents/ -> app/ -> backend/
            backend_dir = Path(__file__).resolve().parents[3]
            self.screenshots_dir = backend_dir / "bug_screenshots"

        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.bug_reports: Dict[str, Dict[str, Any]] = {}
        self.test_results: Dict[str, Dict[str, Any]] = {}
        logger.info(f"Unified Bug Reproduction Service initialized (screenshots: {self.screenshots_dir})")
    
    def parse_elements_from_xml(self, xml_source: str) -> List[Dict[str, Any]]:
        """Parse interactive elements from XML page source"""
        elements = []
        try:
            root = ET.fromstring(xml_source)
            
            # Find all interactive elements
            for elem in root.iter():
                # Check if element is interactive
                clickable = elem.get('clickable', 'false') == 'true'
                enabled = elem.get('enabled', 'false') == 'true'
                
                if clickable or enabled:
                    element_info = {
                        'class': elem.get('class', ''),
                        'resource_id': elem.get('resource-id', ''),
                        'text': elem.get('text', ''),
                        'content_desc': elem.get('content-desc', ''),
                        'clickable': clickable,
                        'enabled': enabled,
                        'bounds': elem.get('bounds', ''),
                        'package': elem.get('package', '')
                    }
                    
                    # Only add if it has some identifying information
                    if any([element_info['resource_id'], element_info['text'], element_info['content_desc']]):
                        elements.append(element_info)
            
            logger.info(f"Parsed {len(elements)} interactive elements from XML")
        except Exception as e:
            logger.error(f"Failed to parse XML: {e}")
        
        return elements
    
    def save_screenshot(self, screenshot_base64: str, bug_id: str, step_number: int) -> str:
        """Save screenshot to file and return path"""
        try:
            screenshot_path = self.screenshots_dir / f"{bug_id}_step_{step_number}.png"
            
            # Decode base64 and save
            screenshot_data = base64.b64decode(screenshot_base64)
            with open(screenshot_path, 'wb') as f:
                f.write(screenshot_data)
            
            logger.info(f"Saved screenshot: {screenshot_path}")
            return str(screenshot_path)
        except Exception as e:
            logger.error(f"Failed to save screenshot: {e}")
            return ""
    
    async def execute_scenario(
        self,
        scenario: TestScenarioInput,
        mcp_client,
        on_step: Optional[Callable[[Dict[str, Any]], Any]] = None
    ) -> TestExecutionResult:
        """
        Execute a predefined test scenario (Scenario Mode)

        Args:
            scenario: Test scenario with structured steps
            mcp_client: MCP Appium client instance
            on_step: Optional callback invoked after each step

        Returns:
            TestExecutionResult with execution details
        """
        test_id = str(uuid.uuid4())
        evidence_list: List[BugEvidence] = []
        steps_executed = 0
        steps_failed = 0
        step_results: List[Dict[str, Any]] = []

        logger.info(f"🧪 Starting test scenario: {test_id} - {scenario.scenario_name}")

        try:
            # Set session ID on MCP client if provided
            if scenario.session_id:
                mcp_client.session_id = scenario.session_id

            # Execute each test step
            for step_num, step in enumerate(scenario.test_steps, start=1):
                logger.info(f"📋 Step {step_num}: {step.get('action', 'unknown')}")

                step_evidence = BugEvidence(
                    step_number=step_num,
                    step_description=f"{step.get('action', 'unknown')} - {step}",
                    timestamp=datetime.now(timezone.utc).isoformat()
                )

                try:
                    # Execute the structured step
                    step_result = await self._execute_structured_step(
                        step,
                        mcp_client,
                        scenario.device_id,
                        test_id,
                        step_num
                    )

                    step_results.append(step_result)

                    if step_result.get('success'):
                        steps_executed += 1
                        logger.info(f"   ✅ Step executed successfully")
                    else:
                        steps_failed += 1
                        logger.warning(f"   ❌ Step failed: {step_result.get('error', 'Unknown error')}")
                        step_evidence.logs.append(f"Error: {step_result.get('error', 'Unknown error')}")

                    # Add screenshot path if captured
                    if step_result.get('screenshot_path'):
                        step_evidence.screenshot_path = step_result['screenshot_path']

                    evidence_list.append(step_evidence)

                    # Notify callback
                    if on_step is not None:
                        try:
                            maybe_coro = on_step(step_result)
                            if asyncio.iscoroutine(maybe_coro):
                                await maybe_coro
                        except Exception as cb_err:
                            logger.debug(f"on_step callback error ignored: {cb_err}")

                    # Wait between steps
                    await asyncio.sleep(1)

                except Exception as e:
                    logger.error(f"   ❌ Step {step_num} failed with exception: {e}")
                    steps_failed += 1
                    step_evidence.logs.append(f"Exception: {str(e)}")
                    evidence_list.append(step_evidence)
                    step_results.append({
                        'success': False,
                        'error': str(e),
                        'step': step
                    })

            result = TestExecutionResult(
                test_id=test_id,
                scenario_name=scenario.scenario_name,
                device_id=scenario.device_id,
                status="passed" if steps_failed == 0 else "failed",
                steps_executed=steps_executed,
                steps_failed=steps_failed,
                steps=step_results,
                evidence=evidence_list,
                created_at=datetime.now(timezone.utc).isoformat()
            )

            # Store test result
            self.test_results[test_id] = {
                'input': scenario.model_dump(),
                'result': result.model_dump()
            }

            logger.info(f"✅ Test scenario complete: {test_id}")
            return result

        except Exception as e:
            logger.error(f"Test scenario execution failed: {e}")
            raise

    async def reproduce_bug(
        self,
        bug_report: BugReportInput,
        mcp_client,
        session_id: str
    ) -> BugReproductionResult:
        """
        Automatically reproduce a bug using AI agent
        
        Args:
            bug_report: Bug report with reproduction steps
            mcp_client: MCP Appium client instance
            session_id: Appium session ID
            
        Returns:
            BugReproductionResult with evidence and analysis
        """
        bug_id = str(uuid.uuid4())
        evidence_list: List[BugEvidence] = []
        steps_executed = 0
        steps_failed = 0
        
        logger.info(f"🐛 Starting bug reproduction: {bug_id} - {bug_report.title}")
        
        try:
            # Set session ID on MCP client
            mcp_client.session_id = session_id
            
            # Execute each reproduction step
            for step_num, step_description in enumerate(bug_report.reproduction_steps, start=1):
                logger.info(f"📋 Step {step_num}: {step_description}")
                
                step_evidence = BugEvidence(
                    step_number=step_num,
                    step_description=step_description,
                    timestamp=datetime.now(timezone.utc).isoformat()
                )
                
                try:
                    # 1. Get page source before action
                    page_source = await mcp_client.get_source()
                    if page_source:
                        step_evidence.page_source = page_source
                        step_evidence.visible_elements = self.parse_elements_from_xml(page_source)
                        logger.info(f"   📄 Found {len(step_evidence.visible_elements)} interactive elements")
                    
                    # 2. Execute the step using AI reasoning
                    step_result = await self._execute_step_with_ai(
                        step_description,
                        step_evidence.visible_elements,
                        mcp_client
                    )
                    
                    if step_result['success']:
                        steps_executed += 1
                        logger.info(f"   ✅ Step executed successfully")
                    else:
                        steps_failed += 1
                        logger.warning(f"   ❌ Step failed: {step_result.get('error', 'Unknown error')}")
                        step_evidence.logs.append(f"Error: {step_result.get('error', 'Unknown error')}")
                    
                    # 3. Take screenshot after action
                    screenshot_result = await mcp_client.screenshot("")
                    if screenshot_result:
                        # MCP Appium returns screenshot in response
                        # We need to extract it from the result
                        # For now, we'll use ADB as fallback
                        import subprocess
                        screenshot_path = self.screenshots_dir / f"{bug_id}_step_{step_num}.png"
                        subprocess.run(
                            ["adb", "-s", bug_report.device_id, "exec-out", "screencap", "-p"],
                            stdout=open(screenshot_path, 'wb'),
                            timeout=10
                        )
                        step_evidence.screenshot_path = str(screenshot_path)
                        logger.info(f"   📸 Screenshot saved: {screenshot_path}")
                    
                    # 4. Get element states after action
                    page_source_after = await mcp_client.get_source()
                    if page_source_after:
                        elements_after = self.parse_elements_from_xml(page_source_after)
                        step_evidence.element_states = {
                            'elements_before': len(step_evidence.visible_elements),
                            'elements_after': len(elements_after),
                            'new_elements': [e for e in elements_after if e not in step_evidence.visible_elements]
                        }
                    
                    evidence_list.append(step_evidence)
                    
                    # Wait between steps
                    import asyncio
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    logger.error(f"   ❌ Step {step_num} failed with exception: {e}")
                    steps_failed += 1
                    step_evidence.logs.append(f"Exception: {str(e)}")
                    evidence_list.append(step_evidence)
            
            # Generate AI analysis
            ai_analysis = self._generate_ai_analysis(
                bug_report,
                evidence_list,
                steps_executed,
                steps_failed
            )
            
            # Generate recommendations
            recommendations = self._generate_recommendations(
                bug_report,
                evidence_list,
                steps_executed,
                steps_failed
            )
            
            result = BugReproductionResult(
                bug_id=bug_id,
                reproduction_successful=(steps_failed == 0),
                steps_executed=steps_executed,
                steps_failed=steps_failed,
                evidence=evidence_list,
                ai_analysis=ai_analysis,
                recommendations=recommendations,
                created_at=datetime.now(timezone.utc).isoformat()
            )
            
            # Store bug report
            self.bug_reports[bug_id] = {
                'input': bug_report.model_dump(),
                'result': result.model_dump()
            }

            logger.info(f"✅ Bug reproduction complete: {bug_id}")
            return result
            
        except Exception as e:
            logger.error(f"Bug reproduction failed: {e}")
            raise
    
    async def _execute_structured_step(
        self,
        step: Dict[str, Any],
        mcp_client,
        device_id: str,
        test_id: str,
        step_num: int
    ) -> Dict[str, Any]:
        """
        Execute a structured test step (for Scenario Mode)

        Args:
            step: Structured step dict with 'action' and parameters
            mcp_client: MCP client instance (Appium-style client OR MobileMCPClient)
            device_id: Device ID
            test_id: Test ID for screenshot naming
            step_num: Step number

        Returns:
            Step result dict with success status
        """
        action = step.get('action', '')

        def _text_success(text: Any) -> bool:
            """Best-effort success heuristic for Mobile MCP string responses."""
            if text is None:
                return False
            if isinstance(text, bool):
                return text
            if not isinstance(text, str):
                return True
            lowered = text.lower()
            if "both" in lowered and "failed" in lowered:
                return False
            if "error" in lowered and "failed" in lowered:
                return False
            return True

        try:
            if action == 'scroll':
                direction = step.get('direction', 'down')

                # Appium-style clients expose scroll(x, y). MobileMCPClient exposes swipe_on_screen(device, direction,...)
                if hasattr(mcp_client, 'scroll'):
                    if direction == 'down':
                        success = await mcp_client.scroll(x=500, y=1500)
                    elif direction == 'up':
                        success = await mcp_client.scroll(x=500, y=500)
                    else:
                        success = await mcp_client.scroll(x=500, y=1000)
                    return {'success': bool(success), 'action': 'scroll', 'direction': direction}

                if hasattr(mcp_client, 'swipe_on_screen'):
                    # Scroll down = swipe up (content moves up)
                    swipe_dir = {"down": "up", "up": "down", "left": "right", "right": "left"}.get(direction, "up")
                    result = await mcp_client.swipe_on_screen(device_id, swipe_dir)
                    return {
                        'success': _text_success(result),
                        'action': 'scroll',
                        'direction': direction,
                        'result': result,
                    }

                return {'success': False, 'action': 'scroll', 'direction': direction, 'error': 'Unsupported client'}

            elif action == 'screenshot':
                # Take screenshot using ADB
                import subprocess

                screenshot_path = self.screenshots_dir / f"{test_id}_step_{step_num}.png"
                with open(screenshot_path, 'wb') as f:
                    subprocess.run(
                        ["adb", "-s", device_id, "exec-out", "screencap", "-p"],
                        stdout=f,
                        timeout=10,
                    )
                return {'success': True, 'action': 'screenshot', 'screenshot_path': str(screenshot_path)}

            elif action == 'tap':
                x = step.get('x', 500)
                y = step.get('y', 1000)
                # Appium-style: tap(x,y). MobileMCPClient: click_on_screen(device,x,y)
                if hasattr(mcp_client, 'tap'):
                    success = await mcp_client.tap(x=x, y=y)
                    return {'success': bool(success), 'action': 'tap', 'x': x, 'y': y}
                if hasattr(mcp_client, 'click_on_screen'):
                    result = await mcp_client.click_on_screen(device_id, int(x), int(y))
                    return {'success': _text_success(result), 'action': 'tap', 'x': x, 'y': y, 'result': result}
                return {'success': False, 'action': 'tap', 'x': x, 'y': y, 'error': 'Unsupported client'}

            elif action == 'swipe':
                start_x = step.get('start_x', 500)
                start_y = step.get('start_y', 1500)
                end_x = step.get('end_x', 500)
                end_y = step.get('end_y', 500)
                if hasattr(mcp_client, 'swipe'):
                    success = await mcp_client.swipe(start_x=start_x, start_y=start_y, end_x=end_x, end_y=end_y)
                    return {'success': bool(success), 'action': 'swipe'}
                if hasattr(mcp_client, 'swipe_on_screen'):
                    dx = float(end_x) - float(start_x)
                    dy = float(end_y) - float(start_y)
                    if abs(dx) > abs(dy):
                        direction = 'left' if dx < 0 else 'right'
                    else:
                        direction = 'up' if dy < 0 else 'down'
                    distance = int((dx * dx + dy * dy) ** 0.5)
                    result = await mcp_client.swipe_on_screen(device_id, direction, int(start_x), int(start_y), distance)
                    return {'success': _text_success(result), 'action': 'swipe', 'direction': direction, 'result': result}
                return {'success': False, 'action': 'swipe', 'error': 'Unsupported client'}

            elif action == 'type':
                text = step.get('text', '')
                # Appium-style: send_keys(text). MobileMCPClient: type_keys(device,text,submit)
                if hasattr(mcp_client, 'send_keys'):
                    success = await mcp_client.send_keys(text)
                    return {'success': bool(success), 'action': 'type', 'text': text}
                if hasattr(mcp_client, 'type_keys'):
                    result = await mcp_client.type_keys(device_id, str(text), False)
                    return {'success': _text_success(result), 'action': 'type', 'text': text, 'result': result}
                return {'success': False, 'action': 'type', 'text': text, 'error': 'Unsupported client'}

            elif action == 'click':
                element_text = step.get('text', '')
                # Appium-style: find_element/click_element. MobileMCPClient: list_elements_on_screen + click_on_screen
                if hasattr(mcp_client, 'find_element') and hasattr(mcp_client, 'click_element'):
                    element = await mcp_client.find_element('xpath', f"//*[@text='{element_text}']")
                    if element:
                        success = await mcp_client.click_element(element['elementId'])
                        return {'success': bool(success), 'action': 'click', 'element_text': element_text}
                    return {'success': False, 'action': 'click', 'error': f'Element not found: {element_text}'}

                if hasattr(mcp_client, 'list_elements_on_screen') and hasattr(mcp_client, 'click_on_screen'):
                    elements = await mcp_client.list_elements_on_screen(device_id)
                    # Best-effort match: prefer text/label fields
                    match = None
                    needle = str(element_text).strip().lower()
                    for e in elements or []:
                        hay = (e.get('text') or e.get('label') or e.get('content_desc') or '').strip().lower()
                        if needle and needle in hay:
                            match = e
                            break
                    if not match:
                        return {'success': False, 'action': 'click', 'error': f'Element not found: {element_text}'}
                    # Try center-point click if bbox is present
                    x = match.get('x')
                    y = match.get('y')
                    w = match.get('width')
                    h = match.get('height')
                    if x is not None and y is not None and w is not None and h is not None:
                        cx = int(x + w / 2)
                        cy = int(y + h / 2)
                        result = await mcp_client.click_on_screen(device_id, cx, cy)
                        return {'success': _text_success(result), 'action': 'click', 'element_text': element_text, 'result': result}
                    return {'success': False, 'action': 'click', 'error': 'Matched element has no coordinates'}

                return {'success': False, 'action': 'click', 'error': 'Unsupported client'}

            else:
                # Unknown action - just wait
                await asyncio.sleep(1)
                return {'success': True, 'action': 'wait'}

        except Exception as e:
            logger.error(f"Step execution failed: {e}")
            return {'success': False, 'error': str(e), 'action': action}

    async def _execute_step_with_ai(
        self,
        step_description: str,
        visible_elements: List[Dict[str, Any]],
        mcp_client
    ) -> Dict[str, Any]:
        """Execute a single step using AI reasoning"""
        # Parse the step description to determine action
        step_lower = step_description.lower()
        
        # Simple keyword-based action detection
        if any(keyword in step_lower for keyword in ['tap', 'click', 'press', 'select']):
            # Find element to click
            for keyword in ['settings', 'button', 'menu', 'search', 'home']:
                if keyword in step_lower:
                    # Find matching element
                    for elem in visible_elements:
                        if keyword in elem.get('text', '').lower() or \
                           keyword in elem.get('content_desc', '').lower() or \
                           keyword in elem.get('resource_id', '').lower():
                            # Try to find and click this element
                            element = await mcp_client.find_element('xpath', f"//*[@text='{elem['text']}']")
                            if element:
                                success = await mcp_client.click_element(element['elementId'])
                                return {'success': success, 'action': 'click', 'element': elem}
            
            return {'success': False, 'error': f'Could not find element for: {step_description}'}
        
        elif any(keyword in step_lower for keyword in ['scroll', 'swipe']):
            # Scroll action
            if hasattr(mcp_client, 'scroll'):
                if 'down' in step_lower:
                    success = await mcp_client.scroll(x=500, y=1500)
                elif 'up' in step_lower:
                    success = await mcp_client.scroll(x=500, y=500)
                else:
                    success = await mcp_client.scroll(x=500, y=1000)
                return {'success': bool(success), 'action': 'scroll'}

            # NOTE: MobileMCPClient needs an explicit device_id for swipe. This AI-mode helper
            # does not currently have that context, so we fail gracefully instead of throwing.
            return {
                'success': False,
                'action': 'scroll',
                'error': 'Scroll not supported for this client in AI-mode (missing device context)'
            }
        
        elif any(keyword in step_lower for keyword in ['type', 'enter', 'input']):
            # Text input action
            # Extract text to type (simplified)
            return {'success': False, 'error': 'Text input not yet implemented'}
        
        else:
            # Unknown action - just wait
            import asyncio
            await asyncio.sleep(1)
            return {'success': True, 'action': 'wait'}
    
    def _generate_ai_analysis(
        self,
        bug_report: BugReportInput,
        evidence: List[BugEvidence],
        steps_executed: int,
        steps_failed: int
    ) -> str:
        """Generate AI analysis of bug reproduction"""
        analysis = f"""
Bug Reproduction Analysis:
- Title: {bug_report.title}
- Severity: {bug_report.severity}
- Steps Attempted: {len(bug_report.reproduction_steps)}
- Steps Executed: {steps_executed}
- Steps Failed: {steps_failed}
- Reproduction Success: {'Yes' if steps_failed == 0 else 'No'}

Evidence Summary:
"""
        for ev in evidence:
            analysis += f"\nStep {ev.step_number}: {ev.step_description}\n"
            analysis += f"  - Elements found: {len(ev.visible_elements)}\n"
            analysis += f"  - Screenshot: {'Yes' if ev.screenshot_path else 'No'}\n"
            if ev.logs:
                analysis += f"  - Logs: {', '.join(ev.logs)}\n"
        
        return analysis
    
    def _generate_recommendations(
        self,
        bug_report: BugReportInput,
        evidence: List[BugEvidence],
        steps_executed: int,
        steps_failed: int
    ) -> List[str]:
        """Generate recommendations based on reproduction results"""
        recommendations = []

        if steps_failed > 0:
            recommendations.append("Review failed steps and update element locators")
            recommendations.append("Check if app state changed between steps")

        if steps_executed == len(bug_report.reproduction_steps):
            recommendations.append("Bug successfully reproduced - proceed with fix")
            recommendations.append("Add automated regression test")

        recommendations.append("Review screenshots for visual verification")
        recommendations.append("Analyze element states for unexpected changes")

        return recommendations


# Backward compatibility alias
BugReproductionService = UnifiedBugReproductionService

