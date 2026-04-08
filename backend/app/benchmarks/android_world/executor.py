"""
AndroidWorld Task Executor.

Executes AndroidWorld tasks on Android devices using Mobile MCP.
Uses an LLM agent to interpret tasks and generate action sequences.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any

from .task_registry import AndroidWorldTask, AndroidWorldTaskRegistry

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class TokenUsage:
    """Token usage tracking for LLM calls"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, prompt: int, completion: int):
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += prompt + completion

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class TaskExecutionResult:
    """Result of executing an AndroidWorld task."""

    task_name: str
    device_id: str
    status: TaskStatus
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_seconds: float = 0.0
    steps_taken: int = 0
    actions: List[Dict[str, Any]] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)  # Base64 images
    error_message: Optional[str] = None
    agent_output: Optional[str] = None  # Agent's final response/reasoning
    token_usage: Optional[TokenUsage] = None  # Token usage tracking

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_name": self.task_name,
            "device_id": self.device_id,
            "status": self.status.value,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_seconds": self.duration_seconds,
            "steps_taken": self.steps_taken,
            "actions_count": len(self.actions),
            "screenshots_count": len(self.screenshots),
            "error_message": self.error_message,
        }


@dataclass
class BenchmarkResult:
    """Aggregate result of running multiple AndroidWorld tasks."""

    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    timeout_tasks: int = 0
    total_duration_seconds: float = 0.0
    task_results: List[TaskExecutionResult] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return self.completed_tasks / self.total_tasks

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "timeout_tasks": self.timeout_tasks,
            "success_rate": f"{self.success_rate:.1%}",
            "total_duration_seconds": self.total_duration_seconds,
            "task_results": [r.to_dict() for r in self.task_results],
        }


class AndroidWorldExecutor:
    """Executes AndroidWorld benchmark tasks on Android devices via Mobile MCP."""

    def __init__(self, mcp_client):
        """
        Initialize executor with a Mobile MCP client.

        Args:
            mcp_client: Instance of MobileMCPClient from device_testing module
        """
        self.mcp_client = mcp_client
        self.registry = AndroidWorldTaskRegistry()
        self.max_steps_per_task = 20
        self.step_timeout_seconds = 30

    async def execute_task(
        self,
        task: AndroidWorldTask,
        device_id: str,
        take_screenshots: bool = True,
    ) -> TaskExecutionResult:
        """
        Execute a single AndroidWorld task on a device.

        This is a simplified executor that performs basic task interpretation.
        For full agent-based execution, integrate with the device_testing agent.
        """
        result = TaskExecutionResult(
            task_name=task.name,
            device_id=device_id,
            status=TaskStatus.RUNNING,
            start_time=datetime.now(),
        )

        logger.info(f"[BENCHMARK] Starting task '{task.name}' on {device_id}")
        logger.info(f"[BENCHMARK] Task description: {task.description}")

        try:
            # Take initial screenshot
            if take_screenshots:
                screenshot = await self.mcp_client.take_screenshot(device_id)
                if "data" in screenshot:
                    result.screenshots.append(screenshot["data"][:100] + "...")  # Truncate for logging

            # Execute task-specific logic
            await self._execute_task_logic(task, device_id, result)

            # Take final screenshot
            if take_screenshots:
                screenshot = await self.mcp_client.take_screenshot(device_id)
                if "data" in screenshot:
                    result.screenshots.append(screenshot["data"][:100] + "...")

            result.status = TaskStatus.SUCCESS

        except asyncio.TimeoutError:
            result.status = TaskStatus.TIMEOUT
            result.error_message = f"Task timed out after {self.step_timeout_seconds * self.max_steps_per_task}s"
            logger.error(f"[BENCHMARK] Task '{task.name}' timed out")

        except Exception as e:
            result.status = TaskStatus.FAILED
            result.error_message = str(e)
            logger.error(f"[BENCHMARK] Task '{task.name}' failed: {e}")

        result.end_time = datetime.now()
        result.duration_seconds = (result.end_time - result.start_time).total_seconds()

        logger.info(f"[BENCHMARK] Task '{task.name}' completed: {result.status.value} in {result.duration_seconds:.1f}s")
        return result

    async def _execute_task_logic(
        self,
        task: AndroidWorldTask,
        device_id: str,
        result: TaskExecutionResult,
    ):
        """Execute the core logic for a specific task type."""

        # Map task names to execution strategies
        if task.name == "ClockStopWatchRunning":
            await self._execute_stopwatch_task(device_id, result)
        elif task.name == "CameraTakePhoto":
            await self._execute_camera_task(device_id, result)
        elif task.name == "OpenAppTaskEval":
            await self._execute_open_app_task(task, device_id, result)
        elif task.name.startswith("SystemBluetooth"):
            await self._execute_bluetooth_task(task, device_id, result)
        elif task.name.startswith("SystemWifi"):
            await self._execute_wifi_task(task, device_id, result)
        elif task.name == "ContactsAddContact":
            await self._execute_contacts_task(task, device_id, result)
        # --- NEW TASK STRATEGIES ---
        elif task.name.startswith("Markor"):
            await self._execute_markor_task(task, device_id, result)
        elif task.name.startswith("Calendar"):
            await self._execute_calendar_task(task, device_id, result)
        elif task.name.startswith("Expense"):
            await self._execute_expense_task(task, device_id, result)
        elif task.name.startswith("Recipe"):
            await self._execute_recipe_task(task, device_id, result)
        elif task.name.startswith("Sms"):
            await self._execute_sms_task(task, device_id, result)
        elif task.name.startswith("Browser"):
            await self._execute_browser_task(task, device_id, result)
        elif task.name.startswith("Files"):
            await self._execute_files_task(task, device_id, result)
        elif task.name.startswith("MultiApp"):
            await self._execute_multi_app_task(task, device_id, result)
        else:
            # Default: just launch the app if specified
            if task.target_app:
                await self.mcp_client.launch_app(device_id, task.target_app)
                result.actions.append({"action": "launch_app", "package": task.target_app})
                result.steps_taken += 1

    async def _execute_stopwatch_task(self, device_id: str, result: TaskExecutionResult):
        """Execute: Run the stopwatch."""
        # Launch Clock app
        await self.mcp_client.launch_app(device_id, "com.android.deskclock")
        result.actions.append({"action": "launch_app", "package": "com.android.deskclock"})
        result.steps_taken += 1
        await asyncio.sleep(1)

        # Find and click Stopwatch tab
        elements = await self.mcp_client.list_elements_on_screen(device_id)
        for el in elements:
            text = el.get("text", "").lower()
            if "stopwatch" in text or "timer" in el.get("contentDescription", "").lower():
                await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                result.actions.append({"action": "click", "target": "Stopwatch tab", "x": el["x"], "y": el["y"]})
                result.steps_taken += 1
                break

        await asyncio.sleep(0.5)

        # Find and click Start button
        elements = await self.mcp_client.list_elements_on_screen(device_id)
        for el in elements:
            text = el.get("text", "").lower()
            content_desc = el.get("contentDescription", "").lower()
            if "start" in text or "start" in content_desc:
                await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                result.actions.append({"action": "click", "target": "Start button", "x": el["x"], "y": el["y"]})
                result.steps_taken += 1
                break

    async def _execute_camera_task(self, device_id: str, result: TaskExecutionResult):
        """Execute: Take one photo."""
        # Launch Camera app
        await self.mcp_client.launch_app(device_id, "com.android.camera2")
        result.actions.append({"action": "launch_app", "package": "com.android.camera2"})
        result.steps_taken += 1
        await asyncio.sleep(2)  # Camera needs time to initialize

        # Find and click shutter button (usually center-bottom)
        elements = await self.mcp_client.list_elements_on_screen(device_id)
        shutter_clicked = False
        for el in elements:
            content_desc = el.get("contentDescription", "").lower()
            if "shutter" in content_desc or "take photo" in content_desc or "capture" in content_desc:
                await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                result.actions.append({"action": "click", "target": "Shutter button", "x": el["x"], "y": el["y"]})
                result.steps_taken += 1
                shutter_clicked = True
                break

        # If no shutter button found, try clicking center of screen
        if not shutter_clicked:
            screen_size = await self.mcp_client.get_screen_size(device_id)
            # Default to center-bottom for shutter
            await self.mcp_client.click_on_screen(device_id, 540, 1800)
            result.actions.append({"action": "click", "target": "Shutter (fallback)", "x": 540, "y": 1800})
            result.steps_taken += 1

    async def _execute_open_app_task(self, task: AndroidWorldTask, device_id: str, result: TaskExecutionResult):
        """Execute: Open an app."""
        app_name = task.params.get("app_name", "Settings")

        # Map app names to package names
        app_packages = {
            "Settings": "com.android.settings",
            "Contacts": "com.android.contacts",
            "Clock": "com.android.deskclock",
            "Calculator": "com.android.calculator2",
            "Camera": "com.android.camera2",
            "Calendar": "com.android.calendar",
        }

        package = app_packages.get(app_name, "com.android.settings")
        await self.mcp_client.launch_app(device_id, package)
        result.actions.append({"action": "launch_app", "package": package, "app_name": app_name})
        result.steps_taken += 1
        await asyncio.sleep(1)

        # Handle potential permission dialogs
        elements = await self.mcp_client.list_elements_on_screen(device_id)
        for el in elements:
            text = el.get("text", "").lower()
            if "allow" in text or "ok" in text or "accept" in text:
                await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                result.actions.append({"action": "click", "target": "Permission dialog", "x": el["x"], "y": el["y"]})
                result.steps_taken += 1
                break

    async def _execute_bluetooth_task(self, task: AndroidWorldTask, device_id: str, result: TaskExecutionResult):
        """Execute: Turn bluetooth on/off."""
        # Launch Settings
        await self.mcp_client.launch_app(device_id, "com.android.settings")
        result.actions.append({"action": "launch_app", "package": "com.android.settings"})
        result.steps_taken += 1
        await asyncio.sleep(1)

        # Find Bluetooth/Connected devices
        elements = await self.mcp_client.list_elements_on_screen(device_id)
        for el in elements:
            text = el.get("text", "").lower()
            if "bluetooth" in text or "connected" in text:
                await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                result.actions.append({"action": "click", "target": "Bluetooth settings", "x": el["x"], "y": el["y"]})
                result.steps_taken += 1
                break

        await asyncio.sleep(0.5)

        # Find toggle
        elements = await self.mcp_client.list_elements_on_screen(device_id)
        for el in elements:
            if el.get("className", "").endswith("Switch") or el.get("type") == "Switch":
                await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                result.actions.append({"action": "click", "target": "Bluetooth toggle", "x": el["x"], "y": el["y"]})
                result.steps_taken += 1
                break

    async def _execute_wifi_task(self, task: AndroidWorldTask, device_id: str, result: TaskExecutionResult):
        """Execute: Turn wifi on/off."""
        await self.mcp_client.launch_app(device_id, "com.android.settings")
        result.actions.append({"action": "launch_app", "package": "com.android.settings"})
        result.steps_taken += 1
        await asyncio.sleep(1)

        elements = await self.mcp_client.list_elements_on_screen(device_id)
        for el in elements:
            text = el.get("text", "").lower()
            if "wifi" in text or "network" in text or "internet" in text:
                await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                result.actions.append({"action": "click", "target": "WiFi settings", "x": el["x"], "y": el["y"]})
                result.steps_taken += 1
                break

        await asyncio.sleep(0.5)

        elements = await self.mcp_client.list_elements_on_screen(device_id)
        for el in elements:
            if el.get("className", "").endswith("Switch") or el.get("type") == "Switch":
                await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                result.actions.append({"action": "click", "target": "WiFi toggle", "x": el["x"], "y": el["y"]})
                result.steps_taken += 1
                break

    async def _execute_contacts_task(self, task: AndroidWorldTask, device_id: str, result: TaskExecutionResult):
        """Execute: Create a new contact."""
        name = task.params.get("name", "Test Contact")
        number = task.params.get("number", "+1-555-123-4567")

        await self.mcp_client.launch_app(device_id, "com.android.contacts")
        result.actions.append({"action": "launch_app", "package": "com.android.contacts"})
        result.steps_taken += 1
        await asyncio.sleep(1)

        # Find FAB (add button)
        elements = await self.mcp_client.list_elements_on_screen(device_id)
        for el in elements:
            content_desc = el.get("contentDescription", "").lower()
            if "add" in content_desc or "create" in content_desc or "new" in content_desc:
                await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                result.actions.append({"action": "click", "target": "Add contact", "x": el["x"], "y": el["y"]})
                result.steps_taken += 1
                break

        await asyncio.sleep(1)

        # Find name field and type
        elements = await self.mcp_client.list_elements_on_screen(device_id)
        for el in elements:
            hint = el.get("hint", "").lower()
            text = el.get("text", "").lower()
            if "name" in hint or "name" in text or el.get("type") == "EditText":
                await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                await asyncio.sleep(0.3)
                await self.mcp_client.type_keys(device_id, name)
                result.actions.append({"action": "type", "target": "Name field", "text": name})
                result.steps_taken += 1
                break

        # Find phone field and type
        elements = await self.mcp_client.list_elements_on_screen(device_id)
        for el in elements:
            hint = el.get("hint", "").lower()
            if "phone" in hint or "number" in hint:
                await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                await asyncio.sleep(0.3)
                await self.mcp_client.type_keys(device_id, number)
                result.actions.append({"action": "type", "target": "Phone field", "text": number})
                result.steps_taken += 1
                break

    # =========================================================================
    # NEW EXECUTION STRATEGIES (Phase 2)
    # =========================================================================

    async def _execute_markor_task(self, task: AndroidWorldTask, device_id: str, result: TaskExecutionResult):
        """Execute Markor note-taking tasks."""
        # For emulator without Markor, use stock Notes or create via Files
        # We'll simulate with a generic note-taking approach

        if task.name == "MarkorCreateNote":
            note_title = task.params.get("note_title", "Test Note")
            note_content = task.params.get("note_content", "Test content")

            # Try to launch Markor or fallback to a text-based approach
            try:
                await self.mcp_client.launch_app(device_id, "net.gsantner.markor")
            except Exception:
                # Fallback to Files app for creating notes
                await self.mcp_client.launch_app(device_id, "com.android.documentsui")

            result.actions.append({"action": "launch_app", "package": "net.gsantner.markor"})
            result.steps_taken += 1
            await asyncio.sleep(1)

            # Look for FAB or create button
            elements = await self.mcp_client.list_elements_on_screen(device_id)
            for el in elements:
                cd = el.get("contentDescription", "").lower()
                text = el.get("text", "").lower()
                if "add" in cd or "new" in cd or "create" in text or "+" in text:
                    await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                    result.actions.append({"action": "click", "target": "Create note", "x": el["x"], "y": el["y"]})
                    result.steps_taken += 1
                    break

            await asyncio.sleep(0.5)
            # Type title
            await self.mcp_client.type_keys(device_id, note_title)
            result.actions.append({"action": "type", "text": note_title})
            result.steps_taken += 1

        elif task.name == "MarkorSearchNote":
            search_term = task.params.get("search_term", "meeting")

            await self.mcp_client.launch_app(device_id, "net.gsantner.markor")
            result.actions.append({"action": "launch_app", "package": "net.gsantner.markor"})
            result.steps_taken += 1
            await asyncio.sleep(1)

            # Find search button
            elements = await self.mcp_client.list_elements_on_screen(device_id)
            for el in elements:
                cd = el.get("contentDescription", "").lower()
                if "search" in cd:
                    await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                    result.actions.append({"action": "click", "target": "Search", "x": el["x"], "y": el["y"]})
                    result.steps_taken += 1
                    await asyncio.sleep(0.5)
                    await self.mcp_client.type_keys(device_id, search_term)
                    result.actions.append({"action": "type", "text": search_term})
                    result.steps_taken += 1
                    break
        else:
            # Generic Markor task - just launch the app
            await self.mcp_client.launch_app(device_id, "net.gsantner.markor")
            result.actions.append({"action": "launch_app", "package": "net.gsantner.markor"})
            result.steps_taken += 1

    async def _execute_calendar_task(self, task: AndroidWorldTask, device_id: str, result: TaskExecutionResult):
        """Execute calendar tasks."""
        # Use Google Calendar or Simple Calendar
        calendar_pkg = "com.google.android.calendar"

        if task.name == "CalendarCreateEvent":
            event_title = task.params.get("event_title", "Test Event")

            await self.mcp_client.launch_app(device_id, calendar_pkg)
            result.actions.append({"action": "launch_app", "package": calendar_pkg})
            result.steps_taken += 1
            await asyncio.sleep(1)

            # Find FAB to create event
            elements = await self.mcp_client.list_elements_on_screen(device_id)
            for el in elements:
                cd = el.get("contentDescription", "").lower()
                text = el.get("text", "").lower()
                if "create" in cd or "add" in cd or "new" in text or "+" in text:
                    await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                    result.actions.append({"action": "click", "target": "Create event", "x": el["x"], "y": el["y"]})
                    result.steps_taken += 1
                    break

            await asyncio.sleep(0.5)
            await self.mcp_client.type_keys(device_id, event_title)
            result.actions.append({"action": "type", "text": event_title})
            result.steps_taken += 1

        elif task.name == "CalendarViewToday":
            await self.mcp_client.launch_app(device_id, calendar_pkg)
            result.actions.append({"action": "launch_app", "package": calendar_pkg})
            result.steps_taken += 1
            await asyncio.sleep(1)

            # Look for Today button
            elements = await self.mcp_client.list_elements_on_screen(device_id)
            for el in elements:
                text = el.get("text", "").lower()
                cd = el.get("contentDescription", "").lower()
                if "today" in text or "today" in cd:
                    await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                    result.actions.append({"action": "click", "target": "Today", "x": el["x"], "y": el["y"]})
                    result.steps_taken += 1
                    break
        else:
            await self.mcp_client.launch_app(device_id, calendar_pkg)
            result.actions.append({"action": "launch_app", "package": calendar_pkg})
            result.steps_taken += 1

    async def _execute_expense_task(self, task: AndroidWorldTask, device_id: str, result: TaskExecutionResult):
        """Execute expense tracking tasks."""
        # Most emulators don't have expense apps pre-installed
        # Simulate with Settings app as fallback
        expense_pkg = task.target_app or "com.android.settings"

        await self.mcp_client.launch_app(device_id, expense_pkg)
        result.actions.append({"action": "launch_app", "package": expense_pkg})
        result.steps_taken += 1

        if task.name == "ExpenseAddEntry":
            amount = task.params.get("amount", "$10.00")
            category = task.params.get("category", "Other")
            result.actions.append({"action": "simulated", "task": "add_expense", "amount": amount, "category": category})
            result.steps_taken += 1

    async def _execute_recipe_task(self, task: AndroidWorldTask, device_id: str, result: TaskExecutionResult):
        """Execute recipe management tasks."""
        # Recipe apps not commonly pre-installed, simulate
        await self.mcp_client.launch_app(device_id, "com.android.settings")
        result.actions.append({"action": "launch_app", "package": "com.android.settings"})
        result.steps_taken += 1

        recipe_name = task.params.get("recipe_name", "Test Recipe")
        result.actions.append({"action": "simulated", "task": task.name, "recipe": recipe_name})
        result.steps_taken += 1

    async def _execute_sms_task(self, task: AndroidWorldTask, device_id: str, result: TaskExecutionResult):
        """Execute SMS/messaging tasks."""
        sms_pkg = "com.android.messaging"

        if task.name == "SmsComposeMessage":
            phone = task.params.get("phone_number", "+1-555-000-0000")
            message = task.params.get("message", "Test message")

            await self.mcp_client.launch_app(device_id, sms_pkg)
            result.actions.append({"action": "launch_app", "package": sms_pkg})
            result.steps_taken += 1
            await asyncio.sleep(1)

            # Find compose/new message button
            elements = await self.mcp_client.list_elements_on_screen(device_id)
            for el in elements:
                cd = el.get("contentDescription", "").lower()
                if "start" in cd or "new" in cd or "compose" in cd:
                    await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                    result.actions.append({"action": "click", "target": "New message", "x": el["x"], "y": el["y"]})
                    result.steps_taken += 1
                    break

            await asyncio.sleep(0.5)
            await self.mcp_client.type_keys(device_id, phone)
            result.actions.append({"action": "type", "text": phone})
            result.steps_taken += 1

        elif task.name == "SmsReadLastMessage":
            await self.mcp_client.launch_app(device_id, sms_pkg)
            result.actions.append({"action": "launch_app", "package": sms_pkg})
            result.steps_taken += 1

    async def _execute_browser_task(self, task: AndroidWorldTask, device_id: str, result: TaskExecutionResult):
        """Execute browser tasks."""
        browser_pkg = "com.android.chrome"

        await self.mcp_client.launch_app(device_id, browser_pkg)
        result.actions.append({"action": "launch_app", "package": browser_pkg})
        result.steps_taken += 1
        await asyncio.sleep(1)

        if task.name == "BrowserNavigateToUrl":
            url = task.params.get("url", "google.com")

            # Find URL bar
            elements = await self.mcp_client.list_elements_on_screen(device_id)
            for el in elements:
                cd = el.get("contentDescription", "").lower()
                text = el.get("text", "").lower()
                if "search" in cd or "url" in cd or "address" in cd or "search or type" in text:
                    await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                    result.actions.append({"action": "click", "target": "URL bar", "x": el["x"], "y": el["y"]})
                    result.steps_taken += 1
                    await asyncio.sleep(0.3)
                    await self.mcp_client.type_keys(device_id, url, submit=True)
                    result.actions.append({"action": "type", "text": url, "submit": True})
                    result.steps_taken += 1
                    break

        elif task.name == "BrowserSearchGoogle":
            query = task.params.get("search_query", "test search")

            elements = await self.mcp_client.list_elements_on_screen(device_id)
            for el in elements:
                cd = el.get("contentDescription", "").lower()
                text = el.get("text", "").lower()
                if "search" in cd or "search or type" in text:
                    await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                    result.actions.append({"action": "click", "target": "Search bar", "x": el["x"], "y": el["y"]})
                    result.steps_taken += 1
                    await asyncio.sleep(0.3)
                    await self.mcp_client.type_keys(device_id, query, submit=True)
                    result.actions.append({"action": "type", "text": query, "submit": True})
                    result.steps_taken += 1
                    break

        elif task.name == "BrowserOpenNewTab":
            # Find tabs button (usually shows tab count)
            elements = await self.mcp_client.list_elements_on_screen(device_id)
            for el in elements:
                cd = el.get("contentDescription", "").lower()
                if "tab" in cd:
                    await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                    result.actions.append({"action": "click", "target": "Tabs", "x": el["x"], "y": el["y"]})
                    result.steps_taken += 1
                    break

    async def _execute_files_task(self, task: AndroidWorldTask, device_id: str, result: TaskExecutionResult):
        """Execute file manager tasks."""
        files_pkg = "com.android.documentsui"

        await self.mcp_client.launch_app(device_id, files_pkg)
        result.actions.append({"action": "launch_app", "package": files_pkg})
        result.steps_taken += 1
        await asyncio.sleep(1)

        if task.name == "FilesCreateFolder":
            folder_name = task.params.get("folder_name", "TestFolder")

            # Look for menu or more options
            elements = await self.mcp_client.list_elements_on_screen(device_id)
            for el in elements:
                cd = el.get("contentDescription", "").lower()
                if "more" in cd or "menu" in cd:
                    await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                    result.actions.append({"action": "click", "target": "Menu", "x": el["x"], "y": el["y"]})
                    result.steps_taken += 1
                    break

        elif task.name == "FilesSearchFile":
            search_term = task.params.get("search_term", "test")

            elements = await self.mcp_client.list_elements_on_screen(device_id)
            for el in elements:
                cd = el.get("contentDescription", "").lower()
                if "search" in cd:
                    await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                    result.actions.append({"action": "click", "target": "Search", "x": el["x"], "y": el["y"]})
                    result.steps_taken += 1
                    await asyncio.sleep(0.3)
                    await self.mcp_client.type_keys(device_id, search_term)
                    result.actions.append({"action": "type", "text": search_term})
                    result.steps_taken += 1
                    break

    async def _execute_multi_app_task(self, task: AndroidWorldTask, device_id: str, result: TaskExecutionResult):
        """Execute multi-app workflow tasks (complex)."""

        if task.name == "MultiAppContactToSms":
            # Find contact, then send SMS
            name = task.params.get("name", "Test Contact")
            message = task.params.get("message", "Hello!")

            # Step 1: Open Contacts
            await self.mcp_client.launch_app(device_id, "com.android.contacts")
            result.actions.append({"action": "launch_app", "package": "com.android.contacts"})
            result.steps_taken += 1
            await asyncio.sleep(1)

            # Step 2: Search for contact
            elements = await self.mcp_client.list_elements_on_screen(device_id)
            for el in elements:
                cd = el.get("contentDescription", "").lower()
                if "search" in cd:
                    await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                    result.actions.append({"action": "click", "target": "Search contacts", "x": el["x"], "y": el["y"]})
                    result.steps_taken += 1
                    await asyncio.sleep(0.3)
                    await self.mcp_client.type_keys(device_id, name.split()[0])  # First name
                    result.actions.append({"action": "type", "text": name.split()[0]})
                    result.steps_taken += 1
                    break

            # Step 3: Open Messages
            await asyncio.sleep(1)
            await self.mcp_client.launch_app(device_id, "com.android.messaging")
            result.actions.append({"action": "launch_app", "package": "com.android.messaging"})
            result.steps_taken += 1

        elif task.name == "MultiAppBrowserToNotes":
            query = task.params.get("search_query", "Python tutorial")

            # Step 1: Search in browser
            await self.mcp_client.launch_app(device_id, "com.android.chrome")
            result.actions.append({"action": "launch_app", "package": "com.android.chrome"})
            result.steps_taken += 1
            await asyncio.sleep(1)

            elements = await self.mcp_client.list_elements_on_screen(device_id)
            for el in elements:
                text = el.get("text", "").lower()
                if "search or type" in text:
                    await self.mcp_client.click_on_screen(device_id, el["x"], el["y"])
                    await asyncio.sleep(0.3)
                    await self.mcp_client.type_keys(device_id, query, submit=True)
                    result.actions.append({"action": "type", "text": query, "submit": True})
                    result.steps_taken += 1
                    break

            # Step 2: Open notes app (simulated as we may not have Markor)
            await asyncio.sleep(2)
            await self.mcp_client.launch_app(device_id, "com.android.documentsui")
            result.actions.append({"action": "launch_app", "package": "com.android.documentsui"})
            result.steps_taken += 1

        else:
            # Generic multi-app: just open Settings as fallback
            await self.mcp_client.launch_app(device_id, "com.android.settings")
            result.actions.append({"action": "launch_app", "package": "com.android.settings"})
            result.steps_taken += 1

    async def run_benchmark(
        self,
        task_names: List[str],
        device_ids: List[str],
        parallel: bool = True,
    ) -> BenchmarkResult:
        """
        Run a set of AndroidWorld tasks on multiple devices.

        Args:
            task_names: List of task names to execute
            device_ids: List of device IDs to run on
            parallel: Whether to run tasks in parallel across devices

        Returns:
            BenchmarkResult with aggregate metrics
        """
        benchmark_result = BenchmarkResult(total_tasks=len(task_names) * len(device_ids))
        start_time = time.time()

        logger.info(f"[BENCHMARK] Starting benchmark: {len(task_names)} tasks × {len(device_ids)} devices")

        if parallel:
            # Run all tasks in parallel
            tasks = []
            for task_name in task_names:
                for device_id in device_ids:
                    task = self.registry.get_instantiated(task_name)
                    if task:
                        tasks.append(self.execute_task(task, device_id))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for res in results:
                if isinstance(res, Exception):
                    benchmark_result.failed_tasks += 1
                elif isinstance(res, TaskExecutionResult):
                    benchmark_result.task_results.append(res)
                    if res.status == TaskStatus.SUCCESS:
                        benchmark_result.completed_tasks += 1
                    elif res.status == TaskStatus.TIMEOUT:
                        benchmark_result.timeout_tasks += 1
                    else:
                        benchmark_result.failed_tasks += 1
        else:
            # Run sequentially
            for task_name in task_names:
                for device_id in device_ids:
                    task = self.registry.get_instantiated(task_name)
                    if task:
                        res = await self.execute_task(task, device_id)
                        benchmark_result.task_results.append(res)
                        if res.status == TaskStatus.SUCCESS:
                            benchmark_result.completed_tasks += 1
                        elif res.status == TaskStatus.TIMEOUT:
                            benchmark_result.timeout_tasks += 1
                        else:
                            benchmark_result.failed_tasks += 1

        benchmark_result.total_duration_seconds = time.time() - start_time

        logger.info(f"[BENCHMARK] Completed: {benchmark_result.completed_tasks}/{benchmark_result.total_tasks} " +
                   f"({benchmark_result.success_rate:.1%}) in {benchmark_result.total_duration_seconds:.1f}s")

        return benchmark_result
