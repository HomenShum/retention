"""
Mobile MCP Client

Client for Mobile MCP server - platform-agnostic mobile automation for iOS and Android.
Provides lightweight, accessibility-based device control and element inspection.

Mobile MCP Documentation: https://github.com/mobile-next/mobile-mcp

KNOWN ISSUE (Mobile MCP v0.0.36):
Mobile MCP's AndroidDeviceManager.getConnectedDevices() fails to detect Android emulators
when ANY device is offline. The bug is in the device detection logic:
1. It calls `adb devices` to get all devices (including offline ones)
2. It then calls `getDeviceType()` for EACH device via `.map()`
3. `getDeviceType()` runs `adb -s <device> shell pm list features` which FAILS for offline devices
4. The exception is caught by the outer try-catch and returns an empty array `[]`

WORKAROUND: This client implements comprehensive ADB fallback for all operations.
When Mobile MCP returns "Device not found", we fall back to direct ADB commands.
"""

import asyncio
import base64
import json
import logging
import os
import subprocess
from typing import Dict, Any, List, Optional
from datetime import datetime

from toon import encode as toon_encode

logger = logging.getLogger(__name__)


async def _adb_launch_app(device_id: str, package_name: str) -> Optional[str]:
    """Launch an app using ADB directly (fallback when Mobile MCP fails).

    Uses am start with the LAUNCHER intent, which is more reliable than monkey
    for launching specific apps.

    Returns:
        Success message, or None if failed
    """
    logger.info(f"[ADB FALLBACK] Attempting to launch {package_name} on {device_id}")
    try:
        # First, try to get the main activity using pm dump
        # Common activity patterns for popular apps
        # Note: Use shell=True approach to handle $ in activity names
        activity_map = {
            "com.google.android.youtube": "com.google.android.youtube/.app.honeycomb.Shell\\$HomeActivity",
            "com.android.chrome": "com.android.chrome/com.google.android.apps.chrome.Main",
            "com.google.android.gm": "com.google.android.gm/.ConversationListActivityGmail",
        }

        # Try known activity first
        if package_name in activity_map:
            component = activity_map[package_name]
            # Use shell command string to properly handle $ escaping
            cmd = f"adb -s {device_id} shell 'am start -n {component}'"
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            stdout_str = stdout.decode() if stdout else ""

            if "Starting:" in stdout_str or "Activity" in stdout_str or "brought to the front" in stdout_str:
                logger.info(f"[ADB FALLBACK] App launch successful via am start for {package_name} on {device_id}")
                return f"Successfully launched {package_name} on {device_id}"

        # Fallback to monkey command with verbose output
        proc = await asyncio.create_subprocess_exec(
            "adb", "-s", device_id, "shell", "monkey", "-p", package_name,
            "-c", "android.intent.category.LAUNCHER", "-v", "1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)

        stdout_str = stdout.decode() if stdout else ""
        stderr_str = stderr.decode() if stderr else ""
        logger.info(f"[ADB FALLBACK] Launch result for {device_id}: returncode={proc.returncode}, stdout_len={len(stdout_str)}")

        # Check for "Events injected: 1" which indicates success
        if proc.returncode == 0 or "Events injected: 1" in stdout_str:
            logger.info(f"[ADB FALLBACK] App launch successful for {package_name} on {device_id}")
            return f"Successfully launched {package_name} on {device_id}"
        else:
            error_msg = stderr_str if stderr_str else stdout_str if stdout_str else "unknown error"
            logger.warning(f"[ADB FALLBACK] App launch failed for {package_name} on {device_id}: {error_msg}")
            return None
    except asyncio.TimeoutError:
        logger.warning(f"[ADB FALLBACK] App launch timeout for {package_name} on {device_id}")
        return None
    except Exception as e:
        logger.warning(f"[ADB FALLBACK] App launch error for {package_name} on {device_id}: {e}")
        import traceback
        logger.warning(f"[ADB FALLBACK] Traceback: {traceback.format_exc()}")
        return None


async def _adb_click(device_id: str, x: int, y: int) -> Optional[str]:
    """Click at coordinates using ADB directly (fallback when Mobile MCP fails).

    Returns:
        Success message, or None if failed
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "-s", device_id, "shell", "input", "tap", str(x), str(y),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

        if proc.returncode == 0:
            logger.info(f"[ADB FALLBACK] Click successful at ({x}, {y}) on {device_id}")
            return f"Clicked at ({x}, {y}) on {device_id}"
        else:
            error_msg = stderr.decode() if stderr else "unknown error"
            logger.debug(f"[ADB FALLBACK] Click failed at ({x}, {y}) on {device_id}: {error_msg}")
            return None
    except asyncio.TimeoutError:
        logger.debug(f"[ADB FALLBACK] Click timeout at ({x}, {y}) on {device_id}")
        return None
    except Exception as e:
        logger.debug(f"[ADB FALLBACK] Click error at ({x}, {y}) on {device_id}: {e}")
        return None


async def _adb_type_text(device_id: str, text: str, submit: bool = False) -> Optional[str]:
    """Type text using ADB directly (fallback when Mobile MCP fails).

    Returns:
        Success message, or None if failed
    """
    try:
        # Escape special characters for shell
        escaped_text = text.replace(" ", "%s").replace("'", "\\'").replace('"', '\\"')

        proc = await asyncio.create_subprocess_exec(
            "adb", "-s", device_id, "shell", "input", "text", escaped_text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)

        if proc.returncode != 0:
            error_msg = stderr.decode() if stderr else "unknown error"
            logger.debug(f"[ADB FALLBACK] Type text failed on {device_id}: {error_msg}")
            return None

        # If submit is True, press Enter
        if submit:
            enter_proc = await asyncio.create_subprocess_exec(
                "adb", "-s", device_id, "shell", "input", "keyevent", "66",  # KEYCODE_ENTER
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(enter_proc.communicate(), timeout=5.0)

        logger.info(f"[ADB FALLBACK] Type text successful on {device_id}: '{text[:20]}...'")
        return f"Typed '{text}' on {device_id}" + (" and submitted" if submit else "")
    except asyncio.TimeoutError:
        logger.debug(f"[ADB FALLBACK] Type text timeout on {device_id}")
        return None
    except Exception as e:
        logger.debug(f"[ADB FALLBACK] Type text error on {device_id}: {e}")
        return None


async def _adb_swipe(device_id: str, direction: str, x: Optional[int] = None, y: Optional[int] = None) -> Optional[str]:
    """Swipe on screen using ADB directly (fallback when Mobile MCP fails).

    Returns:
        Success message, or None if failed
    """
    try:
        # Get screen size for calculating swipe coordinates
        size_proc = await asyncio.create_subprocess_exec(
            "adb", "-s", device_id, "shell", "wm", "size",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(size_proc.communicate(), timeout=5.0)

        # Parse "Physical size: 1080x1920"
        size_match = stdout.decode().strip().split(":")[-1].strip()
        width, height = map(int, size_match.split("x"))

        # Default to center of screen
        start_x = x if x is not None else width // 2
        start_y = y if y is not None else height // 2

        # Calculate end coordinates based on direction
        distance = min(width, height) // 3  # Swipe 1/3 of screen
        if direction == "up":
            end_x, end_y = start_x, start_y - distance
        elif direction == "down":
            end_x, end_y = start_x, start_y + distance
        elif direction == "left":
            end_x, end_y = start_x - distance, start_y
        elif direction == "right":
            end_x, end_y = start_x + distance, start_y
        else:
            return None

        proc = await asyncio.create_subprocess_exec(
            "adb", "-s", device_id, "shell", "input", "swipe",
            str(start_x), str(start_y), str(end_x), str(end_y), "300",  # 300ms duration
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

        if proc.returncode == 0:
            logger.info(f"[ADB FALLBACK] Swipe {direction} successful on {device_id}")
            return f"Swiped {direction} on {device_id}"
        else:
            error_msg = stderr.decode() if stderr else "unknown error"
            logger.debug(f"[ADB FALLBACK] Swipe failed on {device_id}: {error_msg}")
            return None
    except asyncio.TimeoutError:
        logger.debug(f"[ADB FALLBACK] Swipe timeout on {device_id}")
        return None
    except Exception as e:
        logger.debug(f"[ADB FALLBACK] Swipe error on {device_id}: {e}")
        return None


async def _adb_press_button(device_id: str, button: str) -> Optional[str]:
    """Press a button using ADB directly (fallback when Mobile MCP fails).

    Returns:
        Success message, or None if failed
    """
    # Map button names to Android keycodes
    button_map = {
        "HOME": "3",
        "BACK": "4",
        "ENTER": "66",
        "VOLUME_UP": "24",
        "VOLUME_DOWN": "25",
        "DPAD_CENTER": "23",
        "DPAD_UP": "19",
        "DPAD_DOWN": "20",
        "DPAD_LEFT": "21",
        "DPAD_RIGHT": "22",
    }

    keycode = button_map.get(button.upper())
    if not keycode:
        logger.debug(f"[ADB FALLBACK] Unknown button: {button}")
        return None

    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "-s", device_id, "shell", "input", "keyevent", keycode,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

        if proc.returncode == 0:
            logger.info(f"[ADB FALLBACK] Button press {button} successful on {device_id}")
            return f"Pressed {button} on {device_id}"
        else:
            error_msg = stderr.decode() if stderr else "unknown error"
            logger.debug(f"[ADB FALLBACK] Button press failed on {device_id}: {error_msg}")
            return None
    except asyncio.TimeoutError:
        logger.debug(f"[ADB FALLBACK] Button press timeout on {device_id}")
        return None
    except Exception as e:
        logger.debug(f"[ADB FALLBACK] Button press error on {device_id}: {e}")
        return None


async def _adb_open_url(device_id: str, url: str) -> Optional[str]:
    """Open a URL using ADB directly (fallback when Mobile MCP fails).

    Returns:
        Success message, or None if failed
    """
    try:
        # Use am start with VIEW action to open URL
        proc = await asyncio.create_subprocess_exec(
            "adb", "-s", device_id, "shell", "am", "start",
            "-a", "android.intent.action.VIEW",
            "-d", url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)

        if proc.returncode != 0:
            error_msg = stderr.decode() if stderr else "unknown error"
            logger.debug(f"[ADB FALLBACK] Open URL failed on {device_id}: {error_msg}")
            return None

        return f"Opened URL {url} on {device_id}"

    except asyncio.TimeoutError:
        logger.debug(f"[ADB FALLBACK] Open URL timeout on {device_id}")
        return None
    except Exception as e:
        logger.debug(f"[ADB FALLBACK] Open URL error on {device_id}: {e}")
        return None


async def _adb_screenshot(device_id: str) -> Optional[str]:
    """Take screenshot using ADB directly (fallback when Mobile MCP fails).

    Returns:
        Base64-encoded PNG image data, or None if failed
    """
    try:
        # Use exec-out screencap for direct binary output (faster, no temp file)
        proc = await asyncio.create_subprocess_exec(
            "adb", "-s", device_id, "exec-out", "screencap", "-p",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

        if proc.returncode == 0 and stdout:
            # Return base64-encoded PNG
            logger.debug(f"[ADB FALLBACK] Screenshot successful for {device_id} ({len(stdout)} bytes)")
            return base64.b64encode(stdout).decode('utf-8')
        else:
            logger.debug(f"[ADB FALLBACK] Screenshot failed for {device_id}: {stderr.decode() if stderr else 'unknown error'}")
            return None
    except asyncio.TimeoutError:
        logger.debug(f"[ADB FALLBACK] Screenshot timeout for {device_id}")
        return None
    except Exception as e:
        logger.debug(f"[ADB FALLBACK] Screenshot error for {device_id}: {e}")
        return None


async def _adb_list_elements(device_id: str) -> List[Dict[str, Any]]:
    """Get UI elements using ADB uiautomator dump (fallback when Mobile MCP fails).

    Uses two strategies:
    1. Fast: exec-out uiautomator dump /dev/tty (direct to stdout, no temp file)
    2. Fallback: Traditional dump to /sdcard then cat

    Returns:
        List of element dictionaries with name, x, y, width, height
    """
    import re
    import xml.etree.ElementTree as ET

    async def try_direct_dump() -> Optional[str]:
        """Try fast direct dump to /dev/tty."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "adb", "-s", device_id, "exec-out", "uiautomator", "dump", "/dev/tty",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
            if stdout:
                content = stdout.decode('utf-8', errors='ignore')
                # exec-out may have extra text before XML, find the XML start
                xml_start = content.find('<?xml')
                if xml_start == -1:
                    xml_start = content.find('<hierarchy')
                if xml_start != -1:
                    xml_content = content[xml_start:]
                    # CRITICAL: Strip trailing status message "UI hierchary dumped to: /dev/tty"
                    # This message is NOT valid XML and will cause parse errors
                    hierarchy_end = xml_content.find('</hierarchy>')
                    if hierarchy_end != -1:
                        xml_content = xml_content[:hierarchy_end + len('</hierarchy>')]
                    return xml_content
            return None
        except Exception as e:
            logger.debug(f"[ADB FALLBACK] Direct dump failed: {e}")
            return None

    async def try_file_dump() -> Optional[str]:
        """Fallback to traditional file-based dump."""
        try:
            dump_proc = await asyncio.create_subprocess_exec(
                "adb", "-s", device_id, "shell", "uiautomator", "dump", "/sdcard/ui_dump.xml",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(dump_proc.communicate(), timeout=10.0)

            if dump_proc.returncode != 0:
                return None

            cat_proc = await asyncio.create_subprocess_exec(
                "adb", "-s", device_id, "shell", "cat", "/sdcard/ui_dump.xml",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(cat_proc.communicate(), timeout=5.0)

            if stdout:
                return stdout.decode('utf-8', errors='ignore')
            return None
        except Exception as e:
            logger.debug(f"[ADB FALLBACK] File dump failed: {e}")
            return None

    try:
        # Try fast direct dump first
        xml_content = await try_direct_dump()

        # Fallback to file-based dump
        if not xml_content:
            logger.debug(f"[ADB FALLBACK] Direct dump failed, trying file dump for {device_id}")
            xml_content = await try_file_dump()

        if not xml_content:
            logger.warning(f"[ADB FALLBACK] Both dump methods failed for {device_id}")
            return []

        # Parse XML and extract elements
        root = ET.fromstring(xml_content)
        elements = []
        bounds_pattern = re.compile(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]')

        for node in root.iter('node'):
            bounds = node.get('bounds', '')
            text = node.get('text', '')
            content_desc = node.get('content-desc', '')
            resource_id = node.get('resource-id', '')
            class_name = node.get('class', '')
            clickable = node.get('clickable', 'false') == 'true'
            enabled = node.get('enabled', 'true') == 'true'
            focusable = node.get('focusable', 'false') == 'true'

            # Parse bounds "[x1,y1][x2,y2]"
            if bounds:
                match = bounds_pattern.match(bounds)
                if match:
                    x1, y1, x2, y2 = map(int, match.groups())
                    center_x = (x1 + x2) // 2
                    center_y = (y1 + y2) // 2
                    width = x2 - x1
                    height = y2 - y1

                    # Skip elements with no size
                    if width <= 0 or height <= 0:
                        continue

                    # Build element name with priority
                    name = text or content_desc
                    if not name and resource_id:
                        # Extract readable part from resource ID
                        name = resource_id.split('/')[-1].replace('_', ' ')
                    if not name:
                        name = class_name.split('.')[-1]
                    if not name:
                        continue  # Skip nameless elements

                    elements.append({
                        "name": name,
                        "text": text,
                        "content_desc": content_desc,
                        "resource_id": resource_id,
                        "x": center_x,
                        "y": center_y,
                        "width": width,
                        "height": height,
                        "clickable": clickable,
                        "enabled": enabled,
                        "focusable": focusable,
                        "class": class_name
                    })

        logger.info(f"[ADB FALLBACK] Found {len(elements)} elements via uiautomator for {device_id}")
        return elements

    except asyncio.TimeoutError:
        logger.warning(f"[ADB FALLBACK] UI dump timeout for {device_id}")
        return []
    except ET.ParseError as e:
        logger.warning(f"[ADB FALLBACK] XML parse error for {device_id}: {e}")
        return []
    except Exception as e:
        logger.warning(f"[ADB FALLBACK] UI dump error for {device_id}: {e}")
        return []


class MobileMCPClient:
    """Client for Mobile MCP server using stdio transport."""
    
    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.request_id = 0
        self.initialized = False
        
    async def start(self):
        """Start the Mobile MCP server process.

        Note: We explicitly set ANDROID_HOME environment variable to help Mobile MCP
        find ADB. However, due to a bug in Mobile MCP v0.0.36, device detection may
        still fail if any offline devices are present. Our ADB fallback handles this.
        """
        try:
            logger.info("Starting Mobile MCP server...")

            # Set up environment with ANDROID_HOME for Mobile MCP
            # Mobile MCP needs this to find ADB for Android device detection
            env = os.environ.copy()
            if "ANDROID_HOME" not in env:
                # Try common Android SDK locations
                home = os.path.expanduser("~")
                possible_paths = [
                    os.path.join(home, "Library", "Android", "sdk"),  # macOS
                    os.path.join(home, "Android", "Sdk"),  # Linux
                    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk"),  # Windows
                ]
                for path in possible_paths:
                    if os.path.exists(path):
                        env["ANDROID_HOME"] = path
                        logger.info(f"Set ANDROID_HOME to {path}")
                        break

            self.process = subprocess.Popen(
                ["npx", "-y", "@mobilenext/mobile-mcp@latest"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env  # Pass environment with ANDROID_HOME
            )
            
            # Wait for server to initialize
            await asyncio.sleep(2)
            
            # Send initialize request
            init_response = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "mobile-automation-backend",
                    "version": "1.0.0"
                }
            })
            
            if init_response:
                self.initialized = True
                logger.info("Mobile MCP server initialized successfully")
            else:
                raise Exception("Failed to initialize Mobile MCP server")
                
        except Exception as e:
            logger.error(f"Failed to start Mobile MCP server: {e}")
            raise
    
    async def stop(self):
        """Stop the Mobile MCP server process."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
                logger.info("Mobile MCP server stopped")
            except Exception as e:
                logger.error(f"Error stopping Mobile MCP server: {e}")
                self.process.kill()
            finally:
                self.process = None
                self.initialized = False

    def _truncate_image_data(self, data: Any, max_length: int = 100) -> Any:
        """Recursively truncate base64 image data in the result for logging."""
        if isinstance(data, dict):
            result = {}
            for key, value in data.items():
                if key == "data" and isinstance(value, str) and len(value) > max_length:
                    # Truncate base64 data
                    result[key] = f"{value[:max_length]}... [truncated {len(value) - max_length} chars]"
                elif isinstance(value, (dict, list)):
                    result[key] = self._truncate_image_data(value, max_length)
                else:
                    result[key] = value
            return result
        elif isinstance(data, list):
            return [self._truncate_image_data(item, max_length) for item in data]
        else:
            return data

    async def _send_request(self, method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Send JSON-RPC request to Mobile MCP server."""
        if not self.process or not self.process.stdin or not self.process.stdout:
            raise Exception("Mobile MCP server not running")

        self.request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": method,
            "params": params
        }

        try:
            # Send request
            request_json = json.dumps(request) + "\n"
            logger.debug(f"[MCP DEBUG] Sending request: {request_json.strip()}")
            self.process.stdin.write(request_json)
            self.process.stdin.flush()

            # Read response
            response_line = self.process.stdout.readline()
            logger.debug(f"[MCP DEBUG] Received response: {response_line.strip() if response_line else 'None'}")

            if not response_line:
                logger.error(f"[MCP DEBUG] No response from Mobile MCP server")
                return None

            response = json.loads(response_line)

            if "error" in response:
                logger.error(f"[MCP DEBUG] Mobile MCP error: {response['error']}")
                return None

            result = response.get("result")

            # Truncate base64 image data in logs to avoid flooding
            if result:
                log_result = self._truncate_image_data(result)
                logger.debug(f"[MCP DEBUG] Extracted result: {json.dumps(log_result, indent=2)}")
            else:
                logger.debug(f"[MCP DEBUG] Extracted result: None")

            return result

        except Exception as e:
            logger.error(f"[MCP DEBUG] Error sending request to Mobile MCP: {e}")
            import traceback
            logger.error(f"[MCP DEBUG] Traceback: {traceback.format_exc()}")
            return None
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call a Mobile MCP tool."""
        if not self.initialized:
            raise Exception("Mobile MCP client not initialized")

        logger.debug(f"[MCP DEBUG] Calling tool: {tool_name} with arguments: {arguments}")

        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })

        logger.debug(f"[MCP DEBUG] Tool {tool_name} returned: {type(result)}")
        if result:
            logger.debug(f"[MCP DEBUG] Result keys: {result.keys() if isinstance(result, dict) else 'not a dict'}")

        return result
    
    # ============================================================================
    # DEVICE MANAGEMENT
    # ============================================================================
    
    async def list_available_devices(self) -> str:
        """List all available iOS simulators, Android emulators, and physical devices."""
        result = await self.call_tool("mobile_list_available_devices", {"noParams": {}})
        if not result:
            return ""
        return result.get("content", [{}])[0].get("text", "")
    
    async def get_screen_size(self, device: str) -> str:
        """Get screen dimensions and scale factor for a device."""
        result = await self.call_tool("mobile_get_screen_size", {"device": device})
        return result.get("content", [{}])[0].get("text", "")
    
    async def get_orientation(self, device: str) -> str:
        """Get current screen orientation (portrait or landscape)."""
        result = await self.call_tool("mobile_get_orientation", {"device": device})
        return result.get("content", [{}])[0].get("text", "")
    
    async def set_orientation(self, device: str, orientation: str) -> str:
        """Set screen orientation to portrait or landscape."""
        result = await self.call_tool("mobile_set_orientation", {
            "device": device,
            "orientation": orientation
        })
        return result.get("content", [{}])[0].get("text", "")
    
    # ============================================================================
    # APP MANAGEMENT
    # ============================================================================
    
    async def list_apps(self, device: str) -> str:
        """List all installed applications on a device."""
        result = await self.call_tool("mobile_list_apps", {"device": device})
        return result.get("content", [{}])[0].get("text", "")
    
    async def launch_app(self, device: str, package_name: str) -> str:
        """Launch an application by package name or bundle ID.

        Falls back to ADB if Mobile MCP fails with "Device not found".
        """
        result = await self.call_tool("mobile_launch_app", {
            "device": device,
            "packageName": package_name
        })

        # Check for "Device not found" error - trigger ADB fallback
        text_result = result.get("content", [{}])[0].get("text", "")
        if "Device" in text_result and "not found" in text_result:
            logger.warning(f"[LAUNCH_APP] Mobile MCP device not found: {text_result}")
            logger.info(f"[LAUNCH_APP] Using ADB fallback for device {device}")
            adb_result = await _adb_launch_app(device, package_name)
            if adb_result:
                logger.info(f"[LAUNCH_APP] ADB fallback successful for {device}")
                return adb_result
            else:
                logger.error(f"[LAUNCH_APP] ADB fallback also failed for {device}")
                return f"Both Mobile MCP and ADB failed to launch {package_name}. MCP error: {text_result}"

        return text_result
    
    async def terminate_app(self, device: str, package_name: str) -> str:
        """Terminate a running application."""
        result = await self.call_tool("mobile_terminate_app", {
            "device": device,
            "packageName": package_name
        })
        return result.get("content", [{}])[0].get("text", "")
    
    async def install_app(self, device: str, path: str) -> str:
        """Install an application from a local file (.apk, .ipa, .zip, .app)."""
        result = await self.call_tool("mobile_install_app", {
            "device": device,
            "path": path
        })
        return result.get("content", [{}])[0].get("text", "")
    
    async def uninstall_app(self, device: str, bundle_id: str) -> str:
        """Uninstall an application by bundle ID or package name."""
        result = await self.call_tool("mobile_uninstall_app", {
            "device": device,
            "bundle_id": bundle_id
        })
        return result.get("content", [{}])[0].get("text", "")
    
    # ============================================================================
    # SCREEN INTERACTION
    # ============================================================================
    
    async def take_screenshot(self, device: str) -> Dict[str, Any]:
        """Take a screenshot and return as base64-encoded image.

        Returns:
            Dict with keys: type, data, mimeType (for images)
            Or dict with key: error (on failure)

        Expected Mobile MCP response structure:
        {
            "content": [
                {"type": "image", "data": "base64...", "mimeType": "image/jpeg"}
            ]
        }

        Fallback: If Mobile MCP fails with "Device not found", uses ADB direct screenshot.
        """
        result = await self.call_tool("mobile_take_screenshot", {"device": device})

        # Deep inspection per data-structure protocol (section 8)
        logger.debug(f"[SCREENSHOT] Raw result type: {type(result)}")
        if isinstance(result, dict):
            logger.debug(f"[SCREENSHOT] Raw result keys: {list(result.keys())}")

        # Check for "Device not found" error in text content - trigger ADB fallback
        should_use_adb_fallback = False
        error_message = ""

        if result:
            content = result.get("content", [])
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    if "Device" in text and "not found" in text:
                        logger.warning(f"[SCREENSHOT] Mobile MCP device not found: {text}")
                        should_use_adb_fallback = True
                        error_message = text
                        break

        if not result:
            logger.warning("[SCREENSHOT] No result from mobile_take_screenshot, trying ADB fallback")
            should_use_adb_fallback = True
            error_message = "No response from Mobile MCP"

        # ADB Fallback: When Mobile MCP can't find the device
        if should_use_adb_fallback:
            logger.info(f"[SCREENSHOT] Using ADB fallback for device {device}")
            adb_data = await _adb_screenshot(device)
            if adb_data:
                logger.info(f"[SCREENSHOT] ADB fallback successful for {device}")
                return {
                    "type": "image",
                    "data": adb_data,
                    "mimeType": "image/png"
                }
            else:
                logger.error(f"[SCREENSHOT] ADB fallback also failed for {device}")
                return {"error": f"Both Mobile MCP and ADB failed. MCP error: {error_message}"}

        content = result.get("content", [])
        if not content:
            logger.warning(f"[SCREENSHOT] Empty content array, trying ADB fallback")
            adb_data = await _adb_screenshot(device)
            if adb_data:
                return {"type": "image", "data": adb_data, "mimeType": "image/png"}
            return {"error": "Empty content array from Mobile MCP and ADB failed"}

        # Find image content (could be at any index in content array)
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type == "image":
                    # Validate required fields
                    if "data" in item:
                        logger.debug(f"[SCREENSHOT] Found image with mimeType: {item.get('mimeType', 'unknown')}")
                        return item  # Return the image item directly
                    else:
                        logger.warning(f"[SCREENSHOT] Image item missing 'data' field: {list(item.keys())}")

        # No image found - try ADB fallback before giving up
        content_types = [c.get('type', 'unknown') if isinstance(c, dict) else type(c).__name__ for c in content]
        logger.warning(f"[SCREENSHOT] No image in content (types: {content_types}), trying ADB fallback")

        adb_data = await _adb_screenshot(device)
        if adb_data:
            logger.info(f"[SCREENSHOT] ADB fallback successful after no image in MCP response")
            return {"type": "image", "data": adb_data, "mimeType": "image/png"}

        logger.error(f"[SCREENSHOT] All screenshot methods failed for {device}")
        return {"error": f"No image in response and ADB failed. Content types: {content_types}"}
    
    async def save_screenshot(self, device: str, save_to: str) -> str:
        """Save a screenshot to a file."""
        result = await self.call_tool("mobile_save_screenshot", {
            "device": device,
            "saveTo": save_to
        })
        return result.get("content", [{}])[0].get("text", "")
    
    async def list_elements_on_screen(self, device: str) -> List[Dict[str, Any]]:
        """List all interactive elements on screen from accessibility tree.
        Handles both text (JSON string) and json content types.
        Falls back to ADB uiautomator dump when Mobile MCP fails.

        Known Mobile MCP errors that trigger ADB fallback:
        - "Device not found" - device detection bug
        - "Cannot read properties of undefined (reading 'node')" - UI hierarchy parse bug
        - "Error" in text content - generic MCP errors
        """
        logger.debug(f"[MCP DEBUG] Calling mobile_list_elements_on_screen for device: {device}")
        result = await self.call_tool("mobile_list_elements_on_screen", {"device": device})

        # DEBUG: Log raw result
        logger.debug(f"[MCP DEBUG] Raw result type: {type(result)}")
        logger.debug(f"[MCP DEBUG] Raw result: {json.dumps(result, indent=2) if result else 'None'}")

        # Check for known MCP errors - use ADB fallback
        should_use_adb_fallback = False
        fallback_reason = ""

        if not result:
            logger.warning(f"[MCP DEBUG] No result from mobile_list_elements_on_screen")
            should_use_adb_fallback = True
            fallback_reason = "no result"
        else:
            content_items = result.get("content", []) or []
            for item in content_items:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    # Check for known MCP errors
                    if "Device" in text and "not found" in text:
                        logger.warning(f"[MCP DEBUG] Mobile MCP device not found: {text}")
                        should_use_adb_fallback = True
                        fallback_reason = "device not found"
                        break
                    # NEW: Catch "Cannot read properties of undefined (reading 'node')" error
                    if "Cannot read properties of undefined" in text or "reading 'node'" in text:
                        logger.warning(f"[MCP DEBUG] Mobile MCP UI hierarchy parse error: {text}")
                        should_use_adb_fallback = True
                        fallback_reason = "MCP node parse error"
                        break
                    # NEW: Catch generic "Error" responses
                    if text.startswith("Error:") or "error" in text.lower()[:50]:
                        logger.warning(f"[MCP DEBUG] Mobile MCP error response: {text[:200]}")
                        should_use_adb_fallback = True
                        fallback_reason = f"MCP error: {text[:100]}"
                        break

        if should_use_adb_fallback:
            logger.info(f"[MCP DEBUG] Using ADB uiautomator fallback for device {device}")
            adb_elements = await _adb_list_elements(device)
            if adb_elements:
                logger.info(f"[MCP DEBUG] ADB fallback returned {len(adb_elements)} elements")
                return adb_elements
            else:
                logger.warning(f"[MCP DEBUG] ADB fallback also returned no elements")
                return []

        content_items = result.get("content", []) or []
        logger.debug(f"[MCP DEBUG] Content items count: {len(content_items)}")

        if not content_items:
            logger.warning(f"[MCP DEBUG] No content items in result, trying ADB fallback")
            return await _adb_list_elements(device)

        item = content_items[0]
        logger.debug(f"[MCP DEBUG] First item type: {type(item)}")
        logger.debug(f"[MCP DEBUG] First item keys: {item.keys() if isinstance(item, dict) else 'not a dict'}")

        # Prefer structured JSON content if provided
        if isinstance(item, dict) and "json" in item and item.get("json") is not None:
            logger.debug(f"[MCP DEBUG] Found 'json' field in item")
            try:
                elements = item["json"] if isinstance(item["json"], list) else json.loads(item["json"])
                logger.debug(f"[MCP DEBUG] Parsed {len(elements)} elements from 'json' field")
                return elements
            except Exception as e:
                logger.warning(f"[MCP DEBUG] Failed to parse 'json' field: {e}")
                return await _adb_list_elements(device)

        # Fallback to parsing text content as JSON
        text = item.get("text", "") if isinstance(item, dict) else ""
        logger.debug(f"[MCP DEBUG] Text field length: {len(text) if text else 0}")
        logger.debug(f"[MCP DEBUG] Text field preview: {text[:200] if text else 'empty'}")

        if not text:
            logger.warning(f"[MCP DEBUG] No text content in item, trying ADB fallback")
            return await _adb_list_elements(device)

        # CRITICAL FIX: Mobile MCP wraps element JSON in a text prefix
        # Example: "Found these elements on screen: [{...}, {...}]"
        # We need to extract just the JSON array part

        # Try to find the JSON array in the text
        json_start = text.find('[')
        if json_start == -1:
            logger.warning(f"[MCP DEBUG] ❌ No JSON array found in text content, trying ADB fallback")
            logger.warning(f"[MCP DEBUG] Full text content: {text}")
            return await _adb_list_elements(device)

        # Extract everything from the first '[' to the end
        json_text = text[json_start:]
        logger.debug(f"[MCP DEBUG] 🔍 Found JSON array at position {json_start}")
        logger.debug(f"[MCP DEBUG] 🔍 Extracted JSON text (first 500 chars): {json_text[:500]}")

        try:
            elements = json.loads(json_text)
            if isinstance(elements, list):
                logger.debug(f"[MCP DEBUG] ✅ Successfully parsed {len(elements)} elements from text field")
                if len(elements) > 0:
                    logger.debug(f"[MCP DEBUG] ✅ First element preview: {json.dumps(elements[0], indent=2)}")
                return elements
            else:
                logger.warning(f"[MCP DEBUG] ❌ Parsed result is not a list: {type(elements)}, trying ADB fallback")
                return await _adb_list_elements(device)
        except Exception as e:
            logger.warning(f"[MCP DEBUG] ❌ Failed to parse extracted JSON: {e}, trying ADB fallback")
            logger.warning(f"[MCP DEBUG] Extracted JSON text (first 1000 chars): {json_text[:1000]}")
            return await _adb_list_elements(device)

    async def click_on_screen(self, device: str, x: int, y: int) -> str:
        """Click at specific screen coordinates.

        Falls back to ADB if Mobile MCP fails with "Device not found".
        """
        result = await self.call_tool("mobile_click_on_screen_at_coordinates", {
            "device": device,
            "x": x,
            "y": y
        })
        text_result = result.get("content", [{}])[0].get("text", "")

        # Check for "Device not found" error - trigger ADB fallback
        if "Device" in text_result and "not found" in text_result:
            logger.warning(f"[CLICK] Mobile MCP device not found: {text_result}")
            logger.info(f"[CLICK] Using ADB fallback for device {device}")
            adb_result = await _adb_click(device, x, y)
            if adb_result:
                logger.info(f"[CLICK] ADB fallback successful for {device}")
                return adb_result
            else:
                logger.error(f"[CLICK] ADB fallback also failed for {device}")
                return f"Both Mobile MCP and ADB failed to click. MCP error: {text_result}"

        return text_result
    
    async def long_press_on_screen(self, device: str, x: int, y: int) -> str:
        """Long press at specific screen coordinates."""
        result = await self.call_tool("mobile_long_press_on_screen_at_coordinates", {
            "device": device,
            "x": x,
            "y": y
        })
        return result.get("content", [{}])[0].get("text", "")
    
    async def double_tap_on_screen(self, device: str, x: int, y: int) -> str:
        """Double tap at specific screen coordinates."""
        result = await self.call_tool("mobile_double_tap_on_screen", {
            "device": device,
            "x": x,
            "y": y
        })
        return result.get("content", [{}])[0].get("text", "")
    
    async def swipe_on_screen(self, device: str, direction: str, x: Optional[int] = None,
                             y: Optional[int] = None, distance: Optional[int] = None) -> str:
        """Swipe on screen in a direction (up, down, left, right).

        Falls back to ADB if Mobile MCP fails with "Device not found".
        """
        params = {"device": device, "direction": direction}
        if x is not None:
            params["x"] = x
        if y is not None:
            params["y"] = y
        if distance is not None:
            params["distance"] = distance

        result = await self.call_tool("mobile_swipe_on_screen", params)
        text_result = result.get("content", [{}])[0].get("text", "")

        # Check for "Device not found" error - trigger ADB fallback
        if "Device" in text_result and "not found" in text_result:
            logger.warning(f"[SWIPE] Mobile MCP device not found: {text_result}")
            logger.info(f"[SWIPE] Using ADB fallback for device {device}")
            adb_result = await _adb_swipe(device, direction, x, y)
            if adb_result:
                logger.info(f"[SWIPE] ADB fallback successful for {device}")
                return adb_result
            else:
                logger.error(f"[SWIPE] ADB fallback also failed for {device}")
                return f"Both Mobile MCP and ADB failed to swipe. MCP error: {text_result}"

        return text_result

    async def type_keys(self, device: str, text: str, submit: bool = False) -> str:
        """Type text into focused input field.

        Falls back to ADB if Mobile MCP fails with "Device not found".
        """
        result = await self.call_tool("mobile_type_keys", {
            "device": device,
            "text": text,
            "submit": submit
        })
        text_result = result.get("content", [{}])[0].get("text", "")

        # Check for "Device not found" error - trigger ADB fallback
        if "Device" in text_result and "not found" in text_result:
            logger.warning(f"[TYPE] Mobile MCP device not found: {text_result}")
            logger.info(f"[TYPE] Using ADB fallback for device {device}")
            adb_result = await _adb_type_text(device, text, submit)
            if adb_result:
                logger.info(f"[TYPE] ADB fallback successful for {device}")
                return adb_result
            else:
                logger.error(f"[TYPE] ADB fallback also failed for {device}")
                return f"Both Mobile MCP and ADB failed to type. MCP error: {text_result}"

        return text_result

    async def press_button(self, device: str, button: str) -> str:
        """Press a device button (BACK, HOME, VOLUME_UP, etc.).

        Falls back to ADB if Mobile MCP fails with "Device not found".
        """
        result = await self.call_tool("mobile_press_button", {
            "device": device,
            "button": button
        })
        text_result = result.get("content", [{}])[0].get("text", "")

        # Check for "Device not found" error - trigger ADB fallback
        if "Device" in text_result and "not found" in text_result:
            logger.warning(f"[BUTTON] Mobile MCP device not found: {text_result}")
            logger.info(f"[BUTTON] Using ADB fallback for device {device}")
            adb_result = await _adb_press_button(device, button)
            if adb_result:
                logger.info(f"[BUTTON] ADB fallback successful for {device}")
                return adb_result
            else:
                logger.error(f"[BUTTON] ADB fallback also failed for {device}")
                return f"Both Mobile MCP and ADB failed to press button. MCP error: {text_result}"

        return text_result
    
    async def open_url(self, device: str, url: str) -> str:
        """Open a URL in the default browser or via deep link.

        Falls back to ADB if Mobile MCP fails with "Device not found".
        """
        result = await self.call_tool("mobile_open_url", {
            "device": device,
            "url": url
        })
        text_result = result.get("content", [{}])[0].get("text", "")

        # Check for "Device not found" error - trigger ADB fallback
        if "Device" in text_result and "not found" in text_result:
            logger.warning(f"[OPEN_URL] Mobile MCP device not found: {text_result}")
            logger.info(f"[OPEN_URL] Using ADB fallback for device {device}")
            adb_result = await _adb_open_url(device, url)
            if adb_result:
                logger.info(f"[OPEN_URL] ADB fallback successful for {device}")
                return adb_result
            else:
                logger.error(f"[OPEN_URL] ADB fallback also failed for {device}")
                return f"Both Mobile MCP and ADB failed to open URL. MCP error: {text_result}"

        return text_result


__all__ = ["MobileMCPClient"]

