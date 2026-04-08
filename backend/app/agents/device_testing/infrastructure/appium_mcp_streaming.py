import asyncio
import logging
import subprocess
import base64
from typing import Dict, Optional, List, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

from ..mobile_mcp_client import MobileMCPClient

logger = logging.getLogger(__name__)


async def _adb_screenshot(device_id: str) -> Optional[str]:
    """Take screenshot using ADB directly (fallback when Mobile MCP is unavailable)."""
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
            return base64.b64encode(stdout).decode('utf-8')
        else:
            logger.debug(f"ADB screenshot failed for {device_id}: {stderr.decode() if stderr else 'unknown error'}")
            return None
    except asyncio.TimeoutError:
        logger.debug(f"ADB screenshot timeout for {device_id}")
        return None
    except Exception as e:
        logger.debug(f"ADB screenshot error for {device_id}: {e}")
        return None

def _get_utc_now():
    return datetime.now(timezone.utc)

@dataclass
class MobileMCPSession:
    session_id: str
    device_id: str
    created_at: datetime = field(default_factory=_get_utc_now)
    is_active: bool = True
    streaming_enabled: bool = False
    fps: int = 2
    last_frame: Optional[str] = None
    test_results: Dict[str, Any] = field(default_factory=dict)

class MobileMCPStreamingManager:
    def __init__(self, mcp_client: MobileMCPClient):
        self.mcp_client = mcp_client
        self.sessions: Dict[str, MobileMCPSession] = {}
        self.streaming_tasks: Dict[str, asyncio.Task] = {}
        self.lock = asyncio.Lock()
        self._sessions = self.sessions  # Alias for backward compatibility

    async def create_session(
        self,
        device_id: str,
        capabilities: Optional[Dict[str, Any]] = None,
        enable_streaming: bool = True,
        fps: int = 2,
        system_port: Optional[int] = None,
        auto_assign_system_port: bool = False,
    ) -> Optional[str]:
        """Create a Mobile MCP session for a device.

        Note: capabilities, system_port, and auto_assign_system_port are ignored
        in strict mobile-mcp mode. Mobile MCP manages device connections directly.
        """
        async with self.lock:
            # Check if session already exists
            for session in self.sessions.values():
                if session.device_id == device_id and session.is_active:
                    logger.warning(f"Session already exists for {device_id}")
                    return session.session_id

            logger.info(f"Creating Mobile MCP session for device {device_id}")

            # Create our session object (Mobile MCP manages the device connection)
            session_id = str(uuid.uuid4())
            session = MobileMCPSession(
                session_id=session_id,
                device_id=device_id,
                streaming_enabled=enable_streaming,
                fps=fps,
            )

            self.sessions[session_id] = session
            logger.info(f"Created session {session_id} for device {device_id}")

            # Start streaming if requested
            if enable_streaming:
                session.streaming_enabled = True
                task = asyncio.create_task(self._streaming_loop(session_id))
                self.streaming_tasks[session_id] = task
                logger.info(f"Started streaming for {session_id}")

            return session_id

    async def start_streaming(self, session_id: str) -> bool:
        """Start real-time streaming for a session."""
        async with self.lock:
            if session_id not in self.sessions:
                logger.error(f"Session {session_id} not found")
                return False

            session = self.sessions[session_id]
            if session.streaming_enabled:
                logger.warning(f"Streaming already enabled for {session_id}")
                return True

            # Start streaming using Appium screenshots
            session.streaming_enabled = True

            # Create streaming task
            task = asyncio.create_task(self._streaming_loop(session_id))
            self.streaming_tasks[session_id] = task
            logger.info(f"Started streaming for {session_id}")
            return True

    async def stop_streaming(self, session_id: str) -> bool:
        """Stop streaming for a session."""
        async with self.lock:
            if session_id not in self.sessions:
                return False

            session = self.sessions[session_id]
            session.streaming_enabled = False

            # Cancel streaming task
            if session_id in self.streaming_tasks:
                self.streaming_tasks[session_id].cancel()
                del self.streaming_tasks[session_id]

            logger.info(f"Stopped streaming for {session_id}")
            return True

    async def _streaming_loop(self, session_id: str):
        """Background task for continuous frame capture using Appium screenshots."""
        try:
            while True:
                if session_id not in self.sessions:
                    break

                session = self.sessions[session_id]
                if not session.streaming_enabled:
                    break

                # Get latest frame via Appium screenshot
                try:
                    frame = await self.get_screenshot(session_id)
                    if frame:
                        session.last_frame = frame
                except Exception as e:
                    logger.debug(f"Screenshot failed for {session_id}: {e}")

                # Sleep based on FPS
                await asyncio.sleep(1.0 / session.fps)

        except asyncio.CancelledError:
            logger.info(f"Streaming loop cancelled for {session_id}")
        except Exception as e:
            logger.error(f"Error in streaming loop for {session_id}: {e}")

    async def get_frame(self, session_id: str) -> Optional[str]:
        """Get latest frame for a session."""
        if session_id not in self.sessions:
            return None
        return self.sessions[session_id].last_frame

    async def close_session(self, session_id: str) -> bool:
        """Close a Mobile MCP session."""
        if session_id not in self.sessions:
            return False

        session = self.sessions[session_id]

        # Stop streaming
        if session.streaming_enabled:
            session.streaming_enabled = False

            # Cancel streaming task
            if session_id in self.streaming_tasks:
                self.streaming_tasks[session_id].cancel()
                del self.streaming_tasks[session_id]

        session.is_active = False
        logger.info(f"Closed session {session_id}")
        return True

    async def list_sessions(self) -> List[Dict[str, Any]]:
        """List all active sessions."""
        return [
            {
                "session_id": s.session_id,
                "device_id": s.device_id,
                "streaming_enabled": s.streaming_enabled,
                "fps": s.fps,
                "created_at": s.created_at.isoformat(),
                "is_active": s.is_active,
            }
            for s in self.sessions.values()
            if s.is_active
        ]

    async def execute_test(
        self,
        session_id: str,
        test_name: str,
        test_steps: List[Dict[str, Any]],
        on_step: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a test sequence on a device via Appium MCP.

        Args:
            session_id: Session ID
            test_name: Name of the test
            test_steps: List of test steps (find, click, set_value, etc.)
            on_step: Optional async callback invoked after each step with the step_result

        Returns:
            Test results
        """
        if session_id not in self.sessions:
            return {"status": "error", "message": "Session not found"}

        session = self.sessions[session_id]
        results = {
            "test_name": test_name,
            "device_id": session.device_id,
            "steps": [],
            "status": "passed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            for step in test_steps:
                step_result = await self._execute_step(session, step)
                results["steps"].append(step_result)

                # Notify listener after each step
                if on_step is not None:
                    try:
                        maybe_coro = on_step(step_result)
                        if asyncio.iscoroutine(maybe_coro):
                            await maybe_coro
                    except Exception as cb_err:
                        logger.debug(f"on_step callback error ignored: {cb_err}")

                if not step_result.get("success"):
                    results["status"] = "failed"
                    break


            session.test_results[test_name] = results
            return results

        except Exception as e:
            logger.error(f"Test execution failed: {e}")
            results["status"] = "error"
            results["error"] = str(e)
            return results

    async def _execute_step(
        self,
        session: MobileMCPSession,
        step: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a single test step using Mobile MCP."""
        action = step.get("action")

        try:
            device_id = session.device_id

            if action == "find":
                # List elements and find matching one
                elements = await self.mcp_client.list_elements_on_screen(device_id)
                selector = step.get("selector", "")
                matching = [e for e in elements if selector in e.get("text", "") or selector in e.get("label", "")]
                return {"action": action, "success": len(matching) > 0, "elements": matching}

            elif action == "click":
                # Click at coordinates
                x = step.get("x", 0)
                y = step.get("y", 0)
                result = await self.mcp_client.click_on_screen(device_id, x, y)
                return {"action": action, "success": True, "result": result}

            elif action == "type":
                # Type text
                text = step.get("text", "")
                result = await self.mcp_client.type_keys(device_id, text)
                return {"action": action, "success": True, "result": result}

            elif action == "screenshot":
                # Take screenshot
                result = await self.mcp_client.take_screenshot(device_id)
                return {"action": action, "success": True, "screenshot": result}

            elif action == "scroll":
                # Swipe to scroll
                direction = step.get("direction", "down")
                # Map scroll direction to swipe direction (scroll down = swipe up)
                swipe_dir = {"down": "up", "up": "down", "left": "right", "right": "left"}.get(direction, "up")
                result = await self.mcp_client.swipe_on_screen(device_id, swipe_dir)
                return {"action": action, "success": True, "result": result}

            else:
                return {"action": action, "success": False, "error": "Unknown action"}

        except Exception as e:
            logger.error(f"Step execution failed: {e}")
            return {"action": action, "success": False, "error": str(e)}


    async def execute_action(self, session_id: str, action_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single action using Mobile MCP.

        Supported action types:
        - tap: x, y
        - swipe: direction (up/down/left/right) or startX, startY, endX, endY
        - type: text
        - press_key: button (BACK, HOME, etc.)
        - open_app: package/appId
        - close_app: package/appId
        - wait: duration (seconds)
        """
        if session_id not in self.sessions:
            return {"status": "error", "message": "Session not found"}

        session = self.sessions[session_id]
        device_id = session.device_id

        # Local-only wait helper
        if action_type == "wait":
            dur = float(params.get("duration", 0))
            await asyncio.sleep(max(0.0, dur))
            return {
                "status": "success",
                "action": action_type,
                "session_id": session_id,
                "message": f"Waited {dur}s"
            }

        try:
            if action_type == "tap":
                x = int(params.get("x", 0))
                y = int(params.get("y", 0))
                result = await self.mcp_client.click_on_screen(device_id, x, y)
                return {"status": "success", "action": action_type, "message": result}

            elif action_type == "swipe":
                # Support both direction-based and coordinate-based swipes
                if "direction" in params:
                    direction = params["direction"]
                    result = await self.mcp_client.swipe_on_screen(device_id, direction)
                else:
                    # Calculate direction from coordinates
                    sx = int(params.get("startX", 0))
                    sy = int(params.get("startY", 0))
                    ex = int(params.get("endX", 0))
                    ey = int(params.get("endY", 0))
                    dx = ex - sx
                    dy = ey - sy
                    if abs(dx) > abs(dy):
                        direction = "left" if dx < 0 else "right"
                    else:
                        direction = "up" if dy < 0 else "down"
                    distance = int((dx**2 + dy**2)**0.5)
                    result = await self.mcp_client.swipe_on_screen(device_id, direction, sx, sy, distance)
                return {"status": "success", "action": action_type, "message": result}

            elif action_type == "press_key":
                button = params.get("button") or params.get("text", "")
                result = await self.mcp_client.press_button(device_id, button)
                return {"status": "success", "action": action_type, "message": result}

            elif action_type == "type":
                text = str(params.get("text", ""))
                submit = params.get("submit", False)
                result = await self.mcp_client.type_keys(device_id, text, submit)
                return {"status": "success", "action": action_type, "message": result}

            elif action_type == "open_app":
                app_id = params.get("package") or params.get("appId")
                if not app_id:
                    return {"status": "error", "message": "package/appId required"}
                result = await self.mcp_client.launch_app(device_id, app_id)
                return {"status": "success", "action": action_type, "message": result}

            elif action_type == "close_app":
                app_id = params.get("package") or params.get("appId")
                if not app_id:
                    return {"status": "error", "message": "package/appId required"}
                result = await self.mcp_client.terminate_app(device_id, app_id)
                return {"status": "success", "action": action_type, "message": result}

            else:
                return {"status": "error", "message": f"Unknown action type: {action_type}"}

        except Exception as e:
            logger.error(f"Mobile MCP action failed: {e}")
            return {"status": "error", "message": str(e)}

    async def get_screenshot(self, session_id: str) -> Optional[str]:
        """Get a screenshot using Mobile MCP with ADB fallback.

        Returns:
            Base64-encoded PNG image data, or None if failed
        """
        if session_id not in self.sessions:
            return None

        session = self.sessions[session_id]
        device_id = session.device_id

        # Try Mobile MCP first
        try:
            result = await self.mcp_client.take_screenshot(device_id)
            # Mobile MCP returns {"type": "image", "data": "base64...", "mimeType": "image/png"}
            if isinstance(result, dict) and result.get("type") == "image":
                data = result.get("data")
                if data:
                    return data
        except Exception as e:
            logger.debug(f"Mobile MCP screenshot failed for {session_id}: {e}")

        # Fallback to ADB direct screenshot
        try:
            adb_result = await _adb_screenshot(device_id)
            if adb_result:
                logger.debug(f"ADB screenshot successful for {device_id}")
                return adb_result
        except Exception as e:
            logger.debug(f"ADB screenshot fallback failed for {session_id}: {e}")

        return None
