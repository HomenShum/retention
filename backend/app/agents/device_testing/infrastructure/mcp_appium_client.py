import asyncio
import json
import logging
import os
import subprocess
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class Platform(str, Enum):
    ANDROID = "android"
    IOS = "ios"

class TestStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    RUNNING = "RUNNING"
    SKIPPED = "SKIPPED"

@dataclass
class TestResult:
    test_name: str
    platform: Platform
    device_name: str
    status: TestStatus
    duration_ms: int
    error_message: Optional[str] = None
    screenshot_path: Optional[str] = None

class MCPAppiumClient:
    def __init__(self, capabilities_config: Optional[str] = None, android_home: Optional[str] = None, timeout: int = 30):
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self.process: Optional[subprocess.Popen] = None
        self.request_id = 0
        self.capabilities_config = capabilities_config or os.getenv("CAPABILITIES_CONFIG")
        self.android_home = android_home or os.getenv("ANDROID_HOME")
        self._lock = asyncio.Lock()

    async def start(self) -> bool:
        try:
            env = os.environ.copy()
            if self.android_home:
                env["ANDROID_HOME"] = self.android_home
            if self.capabilities_config:
                env["CAPABILITIES_CONFIG"] = self.capabilities_config

            self.process = subprocess.Popen(
                ["npx", "mcp-appium"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                text=True,
                bufsize=1,
            )

            await asyncio.sleep(1)

            try:
                startup_msg = self.process.stdout.readline()
                logger.info(f"MCP Appium startup: {startup_msg.strip()}")
            except (OSError, ValueError):
                pass

            logger.info("MCP Appium server started")
            return True
        except Exception as e:
            logger.error(f"Failed to start MCP Appium server: {e}")
            return False

    async def _send_request(self, method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.process or self.process.poll() is not None:
            logger.error("MCP Appium server is not running")
            return None

        async with self._lock:
            self.request_id += 1
            request = {
                "jsonrpc": "2.0",
                "id": self.request_id,
                "method": method,
                "params": params
            }

            try:
                # Send request
                request_str = json.dumps(request) + "\n"
                self.process.stdin.write(request_str)
                self.process.stdin.flush()
                logger.debug(f"Sent request: {request_str.strip()}")

                # Read response (may need to skip non-JSON lines)
                max_attempts = 10
                for attempt in range(max_attempts):
                    response_str = self.process.stdout.readline()
                    if not response_str:
                        logger.error("No response from MCP server")
                        return None

                    response_str = response_str.strip()
                    if not response_str:
                        continue

                    # Try to parse as JSON
                    try:
                        response = json.loads(response_str)
                        logger.debug(f"Received response: {response_str}")

                        if "error" in response:
                            logger.error(f"MCP error: {response['error']}")
                            return None

                        return response.get("result")
                    except json.JSONDecodeError:
                        # Not JSON, might be a log message - skip it
                        logger.debug(f"Skipping non-JSON line: {response_str}")
                        continue

                logger.error("Failed to get valid JSON response after multiple attempts")
                return None
            except Exception as e:
                logger.error(f"Failed to send request: {e}")
                return None

    async def health_check(self) -> bool:
        """Check if MCP Appium server is healthy"""
        return self.process is not None and self.process.poll() is None

    async def select_platform(self, platform: Platform) -> bool:
        """Select the platform (android or ios)"""
        try:
            result = await self._send_request(
                "tools/call",
                {
                    "name": "select_platform",
                    "arguments": {"platform": platform.value}
                }
            )
            return result is not None
        except Exception as e:
            logger.error(f"Failed to select platform: {e}")
            return False

    async def list_tools(self) -> Optional[List[Dict[str, Any]]]:
        """List available tools from MCP server"""
        try:
            result = await self._send_request("tools/list", {})
            if result and "tools" in result:
                return result["tools"]
            return None
        except Exception as e:
            logger.error(f"Failed to list tools: {e}")
            return None

    async def create_session(self, capabilities: Dict[str, Any]) -> Optional[str]:
        """Create a new mobile automation session"""
        try:
            result = await self._send_request(
                "tools/call",
                {
                    "name": "create_session",
                    "arguments": {"capabilities": capabilities}
                }
            )
            if result and "content" in result:
                for content in result["content"]:
                    if content.get("type") == "text":
                        data = json.loads(content.get("text", "{}"))
                        self.session_id = data.get("sessionId")
                        logger.info(f"Session created: {self.session_id}")
                        return self.session_id
            return None
        except Exception as e:
            logger.error(f"Failed to create session: {e}")
            return None

    async def generate_locators(self) -> Optional[Dict[str, Any]]:
        """Generate intelligent locators for all interactive elements"""
        try:
            result = await self._send_request(
                "tools/call",
                {
                    "name": "generate_locators",
                    "arguments": {"sessionId": self.session_id}
                }
            )
            if result and "content" in result:
                for content in result["content"]:
                    if content.get("type") == "text":
                        return json.loads(content.get("text", "{}"))
            return None
        except Exception as e:
            logger.error(f"Failed to generate locators: {e}")
            return None

    async def get_source(self) -> Optional[str]:
        """Get the XML representation of the current view"""
        try:
            result = await self._send_request(
                "tools/call",
                {
                    "name": "appium_get_source",
                    "arguments": {}
                }
            )
            if result and "content" in result:
                for content in result["content"]:
                    if content.get("type") == "text":
                        return content.get("text", "")
            return None
        except Exception as e:
            logger.error(f"Failed to get source: {e}")
            return None

    async def find_element(
        self, strategy: str, selector: str
    ) -> Optional[Dict[str, Any]]:
        """Find an element using specified strategy (using='id', value='com.example:id/button')"""
        try:
            result = await self._send_request(
                "tools/call",
                {
                    "name": "appium_find_element",
                    "arguments": {
                        "using": strategy,
                        "value": selector
                    }
                }
            )
            if result and "content" in result:
                for content in result["content"]:
                    if content.get("type") == "text":
                        data = json.loads(content.get("text", "{}"))
                        # MCP Appium returns element in format: {"ELEMENT": "element-id"}
                        if "ELEMENT" in data:
                            return {"elementId": data["ELEMENT"]}
                        return data
            return None
        except Exception as e:
            logger.error(f"Failed to find element: {e}")
            return None

    async def click_element(self, element_id: str) -> bool:
        """Click on an element"""
        try:
            result = await self._send_request(
                "tools/call",
                {
                    "name": "appium_click_element",
                    "arguments": {
                        "elementId": element_id
                    }
                }
            )
            return result is not None
        except Exception as e:
            logger.error(f"Failed to click element: {e}")
            return False

    async def set_value(self, element_id: str, value: str) -> bool:
        """Set value for an input element"""
        try:
            result = await self._send_request(
                "tools/call",
                {
                    "name": "appium_send_keys",
                    "arguments": {
                        "elementId": element_id,
                        "text": value
                    }
                }
            )
            return result is not None
        except Exception as e:
            logger.error(f"Failed to set value: {e}")
            return False

    async def get_text(self, element_id: str) -> Optional[str]:
        """Get text content of an element"""
        try:
            result = await self._send_request(
                "tools/call",
                {
                    "name": "appium_get_text",
                    "arguments": {
                        "sessionId": self.session_id,
                        "elementId": element_id
                    }
                }
            )
            if result and "content" in result:
                for content in result["content"]:
                    if content.get("type") == "text":
                        data = json.loads(content.get("text", "{}"))
                        return data.get("text")
            return None
        except Exception as e:
            logger.error(f"Failed to get text: {e}")
            return None

    async def screenshot(self, output_path: str) -> bool:
        """Take a screenshot and save to file"""
        try:
            result = await self._send_request(
                "tools/call",
                {
                    "name": "appium_take_screenshot",
                    "arguments": {}
                }
            )

            if result and "content" in result:
                for content in result["content"]:
                    if content.get("type") == "text":
                        # MCP Appium returns base64 screenshot
                        import base64
                        screenshot_data = content.get("text", "")

                        # Save to file
                        with open(output_path, "wb") as f:
                            f.write(base64.b64decode(screenshot_data))

                        logger.info(f"Screenshot saved to {output_path}")
                        return True

            return False
        except Exception as e:
            logger.error(f"Failed to take screenshot: {e}")
            return False

    async def scroll(self, x: int = 500, y: int = 1000) -> bool:
        """Scroll to specific coordinates on the screen"""
        try:
            result = await self._send_request(
                "tools/call",
                {
                    "name": "appium_scroll",
                    "arguments": {
                        "x": x,
                        "y": y
                    }
                }
            )
            return result is not None
        except Exception as e:
            logger.error(f"Failed to scroll: {e}")
            return False

    async def generate_tests(
        self, scenario: str, test_framework: str = "JAVA"
    ) -> Optional[str]:
        """Generate automated test code from natural language"""
        try:
            # Build arguments; sessionId is optional if MCP supports sessionless generation
            args = {
                "scenario": scenario,
                "testFramework": test_framework
            }
            if self.session_id:
                args["sessionId"] = self.session_id

            result = await self._send_request(
                "tools/call",
                {
                    "name": "appium_generate_tests",
                    "arguments": args
                }
            )
            if result and "content" in result:
                for content in result["content"]:
                    if content.get("type") == "text":
                        data = json.loads(content.get("text", "{}"))
                        return data.get("testCode")
            return None
        except Exception as e:
            logger.error(f"Failed to generate tests: {e}")
            return None

    async def close_session(self) -> bool:
        """Close the current session"""
        try:
            result = await self._send_request(
                "tools/call",
                {
                    "name": "close_session",
                    "arguments": {"sessionId": self.session_id}
                }
            )
            if result:
                self.session_id = None
                logger.info("Session closed")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to close session: {e}")
            return False

    async def close(self):
        """Close the client and stop the MCP server"""
        if self.session_id:
            await self.close_session()
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
            logger.info("MCP Appium server stopped")

