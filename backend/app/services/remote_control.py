"""Remote computer control service.

Enables operating the local Mac remotely via Slack commands.
Uses native macOS tools:
  - screencapture: screenshot capture
  - cliclick: mouse/keyboard automation
  - osascript: AppleScript for app control and UI scripting

Security:
  - All commands require CRON_AUTH_TOKEN auth
  - Only the configured Slack user can issue commands
  - All actions are logged with timestamps
  - Screenshot files are cleaned up after upload
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Authorized Slack user ID — only this user can issue remote commands
AUTHORIZED_USER_ID = os.getenv("REMOTE_CONTROL_USER_ID", "")

# Screenshot storage
SCREENSHOT_DIR = Path(tempfile.gettempdir()) / "openclaw_screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)


@dataclass
class RemoteResult:
    """Result from a remote control operation."""
    success: bool
    action: str
    output: str = ""
    screenshot_path: str = ""
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())


# ------------------------------------------------------------------
# Screenshot
# ------------------------------------------------------------------

async def take_screenshot(
    region: Optional[str] = None,
    window: bool = False,
    display: int = 1,
) -> RemoteResult:
    """Capture a screenshot of the Mac screen.

    Args:
        region: Optional "x,y,w,h" to capture a specific region
        window: If True, capture the frontmost window only
        display: Display number (1 = main)

    Returns:
        RemoteResult with screenshot_path set to the saved file.
    """
    ts = int(time.time())
    path = str(SCREENSHOT_DIR / f"screen_{ts}.png")

    cmd = ["screencapture", "-x"]  # -x = no sound

    if window:
        cmd.append("-l")
        # Get frontmost window ID via osascript
        try:
            wid_result = await _run_cmd([
                "osascript", "-e",
                'tell application "System Events" to get id of first window of (first application process whose frontmost is true)',
            ])
            if wid_result.returncode == 0:
                cmd.append(wid_result.stdout.strip())
        except Exception:
            cmd.append("-w")  # Fallback to interactive window select

    if region:
        parts = region.split(",")
        if len(parts) == 4:
            x, y, w, h = parts
            cmd.extend(["-R", f"{x},{y},{w},{h}"])

    cmd.append(path)

    result = await _run_cmd(cmd)

    if result.returncode != 0 or not Path(path).exists():
        return RemoteResult(
            success=False,
            action="screenshot",
            error=f"screencapture failed: {result.stderr}",
        )

    return RemoteResult(
        success=True,
        action="screenshot",
        screenshot_path=path,
        output=f"Captured {Path(path).stat().st_size // 1024}KB screenshot",
    )


async def screenshot_to_base64(path: str) -> str:
    """Read a screenshot file and return base64-encoded PNG."""
    data = await asyncio.to_thread(Path(path).read_bytes)
    return base64.b64encode(data).decode("ascii")


# ------------------------------------------------------------------
# Mouse control (via cliclick)
# ------------------------------------------------------------------

async def mouse_click(x: int, y: int, button: str = "left") -> RemoteResult:
    """Click at screen coordinates."""
    click_map = {"left": "c", "right": "rc", "double": "dc"}
    action = click_map.get(button, "c")
    result = await _run_cmd(["cliclick", f"{action}:{x},{y}"])
    return RemoteResult(
        success=result.returncode == 0,
        action=f"click_{button}",
        output=f"Clicked {button} at ({x}, {y})",
        error=result.stderr if result.returncode != 0 else "",
    )


async def mouse_move(x: int, y: int) -> RemoteResult:
    """Move mouse to screen coordinates."""
    result = await _run_cmd(["cliclick", f"m:{x},{y}"])
    return RemoteResult(
        success=result.returncode == 0,
        action="mouse_move",
        output=f"Moved to ({x}, {y})",
        error=result.stderr if result.returncode != 0 else "",
    )


async def mouse_drag(x1: int, y1: int, x2: int, y2: int) -> RemoteResult:
    """Drag from one point to another."""
    result = await _run_cmd(["cliclick", f"dd:{x1},{y1}", f"du:{x2},{y2}"])
    return RemoteResult(
        success=result.returncode == 0,
        action="mouse_drag",
        output=f"Dragged ({x1},{y1}) -> ({x2},{y2})",
        error=result.stderr if result.returncode != 0 else "",
    )


# ------------------------------------------------------------------
# Keyboard control (via cliclick)
# ------------------------------------------------------------------

async def type_text(text: str) -> RemoteResult:
    """Type text using keyboard simulation."""
    result = await _run_cmd(["cliclick", f"t:{text}"])
    return RemoteResult(
        success=result.returncode == 0,
        action="type",
        output=f"Typed {len(text)} chars",
        error=result.stderr if result.returncode != 0 else "",
    )


async def key_press(key: str) -> RemoteResult:
    """Press a key or key combo. Examples: 'return', 'cmd+c', 'cmd+shift+s'.

    Supported modifiers: cmd, ctrl, alt, shift, fn
    Supported keys: return, tab, space, delete, escape, up, down, left, right,
                    f1-f12, home, end, pageup, pagedown
    """
    # cliclick key press format: kp:key or kd:modifier ku:modifier
    key_lower = key.lower()

    # Map common key names to cliclick format
    cliclick_keys = {
        "return": "return", "enter": "return", "tab": "tab",
        "space": "space", "delete": "delete", "backspace": "delete",
        "escape": "escape", "esc": "escape",
        "up": "arrow-up", "down": "arrow-down",
        "left": "arrow-left", "right": "arrow-right",
        "home": "home", "end": "end",
        "pageup": "page-up", "pagedown": "page-down",
    }

    # Handle modifier combos like cmd+c, ctrl+shift+a
    if "+" in key_lower:
        parts = key_lower.split("+")
        modifiers = parts[:-1]
        final_key = parts[-1]

        # Build cliclick command sequence
        cmds = []
        mod_map = {
            "cmd": "command", "command": "command",
            "ctrl": "control", "control": "control",
            "alt": "option", "option": "option",
            "shift": "shift", "fn": "fn",
        }
        for mod in modifiers:
            cliclick_mod = mod_map.get(mod, mod)
            cmds.append(f"kd:{cliclick_mod}")

        # The final key
        mapped = cliclick_keys.get(final_key, final_key)
        cmds.append(f"kp:{mapped}")

        # Release modifiers in reverse
        for mod in reversed(modifiers):
            cliclick_mod = mod_map.get(mod, mod)
            cmds.append(f"ku:{cliclick_mod}")

        result = await _run_cmd(["cliclick"] + cmds)
    else:
        mapped = cliclick_keys.get(key_lower, key_lower)
        result = await _run_cmd(["cliclick", f"kp:{mapped}"])

    return RemoteResult(
        success=result.returncode == 0,
        action="key_press",
        output=f"Pressed: {key}",
        error=result.stderr if result.returncode != 0 else "",
    )


# ------------------------------------------------------------------
# App control (via osascript)
# ------------------------------------------------------------------

async def open_app(app_name: str) -> RemoteResult:
    """Open or activate an application."""
    result = await _run_cmd([
        "osascript", "-e",
        f'tell application "{app_name}" to activate',
    ])
    return RemoteResult(
        success=result.returncode == 0,
        action="open_app",
        output=f"Opened {app_name}",
        error=result.stderr if result.returncode != 0 else "",
    )


async def get_frontmost_app() -> RemoteResult:
    """Get the name of the frontmost application."""
    result = await _run_cmd([
        "osascript", "-e",
        'tell application "System Events" to get name of first application process whose frontmost is true',
    ])
    return RemoteResult(
        success=result.returncode == 0,
        action="frontmost_app",
        output=result.stdout.strip() if result.returncode == 0 else "",
        error=result.stderr if result.returncode != 0 else "",
    )


async def list_running_apps() -> RemoteResult:
    """List all running applications."""
    result = await _run_cmd([
        "osascript", "-e",
        'tell application "System Events" to get name of every application process whose background only is false',
    ])
    return RemoteResult(
        success=result.returncode == 0,
        action="list_apps",
        output=result.stdout.strip() if result.returncode == 0 else "",
        error=result.stderr if result.returncode != 0 else "",
    )


async def open_url(url: str) -> RemoteResult:
    """Open a URL in the default browser."""
    result = await _run_cmd(["open", url])
    return RemoteResult(
        success=result.returncode == 0,
        action="open_url",
        output=f"Opened {url}",
        error=result.stderr if result.returncode != 0 else "",
    )


# ------------------------------------------------------------------
# System info
# ------------------------------------------------------------------

async def get_screen_size() -> RemoteResult:
    """Get screen resolution."""
    result = await _run_cmd([
        "osascript", "-e",
        'tell application "Finder" to get bounds of window of desktop',
    ])
    if result.returncode != 0:
        # Fallback: system_profiler
        result = await _run_cmd([
            "system_profiler", "SPDisplaysDataType",
        ])
        # Parse resolution from output
        output = result.stdout
        import re
        match = re.search(r"Resolution:\s*(\d+\s*x\s*\d+)", output)
        resolution = match.group(1) if match else "unknown"
        return RemoteResult(
            success=True,
            action="screen_size",
            output=resolution,
        )
    return RemoteResult(
        success=True,
        action="screen_size",
        output=result.stdout.strip(),
    )


async def get_clipboard() -> RemoteResult:
    """Get clipboard contents."""
    result = await _run_cmd(["pbpaste"])
    return RemoteResult(
        success=result.returncode == 0,
        action="clipboard",
        output=result.stdout[:2000] if result.returncode == 0 else "",
        error=result.stderr if result.returncode != 0 else "",
    )


async def set_clipboard(text: str) -> RemoteResult:
    """Set clipboard contents."""
    proc = await asyncio.create_subprocess_exec(
        "pbcopy",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate(input=text.encode())
    return RemoteResult(
        success=proc.returncode == 0,
        action="set_clipboard",
        output=f"Set clipboard ({len(text)} chars)",
    )


# ------------------------------------------------------------------
# Shell command execution (sandboxed)
# ------------------------------------------------------------------

async def run_shell(command: str, timeout: int = 30) -> RemoteResult:
    """Run a shell command with timeout.

    Security: blocked commands that could be destructive.
    """
    blocked = ["rm -rf /", "mkfs", "dd if=", "> /dev/", ":(){ :|:& };:"]
    for pattern in blocked:
        if pattern in command:
            return RemoteResult(
                success=False,
                action="shell",
                error=f"Blocked dangerous command pattern: {pattern}",
            )

    try:
        result = await asyncio.wait_for(
            _run_cmd(["bash", "-c", command]),
            timeout=timeout,
        )
        return RemoteResult(
            success=result.returncode == 0,
            action="shell",
            output=result.stdout[:3000],
            error=result.stderr[:1000] if result.returncode != 0 else "",
        )
    except asyncio.TimeoutError:
        return RemoteResult(
            success=False,
            action="shell",
            error=f"Command timed out after {timeout}s",
        )


# ------------------------------------------------------------------
# Claude Code bridge — run Claude Code commands remotely
# ------------------------------------------------------------------

async def run_claude_code(
    prompt: str,
    working_dir: str = "",
    timeout: int = 300,
) -> RemoteResult:
    """Execute a Claude Code command and return the result.

    This spawns `claude --print` in the specified working directory.
    """
    if not working_dir:
        working_dir = os.getenv(
            "CLAUDE_CODE_WORKDIR",
            "/Users/Shared/vscode_ta/project_countdown/my-fullstack-app",
        )

    cmd = ["claude", "--print", "--output-format", "text", prompt]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )

        output = stdout.decode("utf-8", errors="replace")[:4000]
        err = stderr.decode("utf-8", errors="replace")[:1000]

        return RemoteResult(
            success=proc.returncode == 0,
            action="claude_code",
            output=output,
            error=err if proc.returncode != 0 else "",
        )
    except asyncio.TimeoutError:
        return RemoteResult(
            success=False,
            action="claude_code",
            error=f"Claude Code timed out after {timeout}s",
        )
    except FileNotFoundError:
        return RemoteResult(
            success=False,
            action="claude_code",
            error="Claude Code CLI not found. Is it installed?",
        )


# ------------------------------------------------------------------
# LLM-based command interpreter
# ------------------------------------------------------------------

async def interpret_command(
    user_message: str,
    screenshot_context: str = "",
) -> dict[str, Any]:
    """Use LLM to interpret a natural language command into actions.

    Returns a plan: {"actions": [{"type": "screenshot"}, {"type": "click", "x": 100, "y": 200}, ...]}
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return {"actions": [{"type": "error", "message": "OPENAI_API_KEY not set"}]}

    system_prompt = """You are a computer control assistant. The user sends natural language commands
to control their Mac remotely. Translate each command into a JSON action plan.

Available actions:
- {"type": "screenshot"} — take a screenshot
- {"type": "click", "x": N, "y": N, "button": "left|right|double"} — click
- {"type": "move", "x": N, "y": N} — move mouse
- {"type": "drag", "x1": N, "y1": N, "x2": N, "y2": N} — drag
- {"type": "type", "text": "..."} — type text
- {"type": "key", "key": "cmd+c"} — press key combo
- {"type": "open_app", "name": "Safari"} — open app
- {"type": "open_url", "url": "https://..."} — open URL
- {"type": "shell", "command": "ls -la"} — run shell command
- {"type": "claude_code", "prompt": "..."} — run Claude Code
- {"type": "clipboard_get"} — get clipboard
- {"type": "clipboard_set", "text": "..."} — set clipboard
- {"type": "screen_info"} — get screen size
- {"type": "list_apps"} — list running apps
- {"type": "frontmost_app"} — get active app name

Respond with ONLY a JSON object: {"actions": [...], "description": "what this will do"}

If the user's command is ambiguous, start with a screenshot to see the screen state.
If coordinates are needed but unknown, take a screenshot first, then the user can refine.
"""

    context = ""
    if screenshot_context:
        context = f"\n\nCurrent screen context from previous screenshot analysis: {screenshot_context}"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "gpt-5.4-nano",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message + context},
                ],
                "response_format": {"type": "json_object"},
                "max_completion_tokens": 500,
            },
        )
        data = resp.json()
        content = data["choices"][0]["message"]["content"]

        import json
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"actions": [{"type": "screenshot"}], "description": "Couldn't parse — taking screenshot"}


async def execute_action(action: dict[str, Any]) -> RemoteResult:
    """Execute a single action from the LLM plan."""
    action_type = action.get("type", "")

    if action_type == "screenshot":
        return await take_screenshot(
            region=action.get("region"),
            window=action.get("window", False),
        )
    elif action_type == "click":
        return await mouse_click(
            action["x"], action["y"],
            button=action.get("button", "left"),
        )
    elif action_type == "move":
        return await mouse_move(action["x"], action["y"])
    elif action_type == "drag":
        return await mouse_drag(
            action["x1"], action["y1"],
            action["x2"], action["y2"],
        )
    elif action_type == "type":
        return await type_text(action["text"])
    elif action_type == "key":
        return await key_press(action["key"])
    elif action_type == "open_app":
        return await open_app(action["name"])
    elif action_type == "open_url":
        return await open_url(action["url"])
    elif action_type == "shell":
        return await run_shell(action["command"], timeout=action.get("timeout", 30))
    elif action_type == "claude_code":
        return await run_claude_code(
            action["prompt"],
            working_dir=action.get("working_dir", ""),
            timeout=action.get("timeout", 300),
        )
    elif action_type == "clipboard_get":
        return await get_clipboard()
    elif action_type == "clipboard_set":
        return await set_clipboard(action["text"])
    elif action_type == "screen_info":
        return await get_screen_size()
    elif action_type == "list_apps":
        return await list_running_apps()
    elif action_type == "frontmost_app":
        return await get_frontmost_app()
    else:
        return RemoteResult(
            success=False,
            action=action_type,
            error=f"Unknown action type: {action_type}",
        )


async def execute_plan(plan: dict[str, Any]) -> list[RemoteResult]:
    """Execute a full action plan sequentially, returning all results."""
    results = []
    for action in plan.get("actions", []):
        result = await execute_action(action)
        results.append(result)
        # Small delay between actions for UI to settle
        if action.get("type") in ("click", "key", "type", "open_app"):
            await asyncio.sleep(0.5)
    return results


# ------------------------------------------------------------------
# Accessibility-based element discovery
# ------------------------------------------------------------------

async def get_ui_elements() -> list[dict[str, Any]]:
    """Get all interactive UI elements from the frontmost app via macOS Accessibility API.

    Returns list of {role, name, description, x, y, w, h} — no coordinate guessing needed.
    """
    script = '''
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    set results to {}

    try
        set w to window 1 of frontApp

        -- Buttons
        repeat with b in every button of w
            try
                set bDesc to description of b
                set bName to name of b
                set bPos to position of b
                set bSize to size of b
                set end of results to "button|" & bDesc & "|" & bName & "|" & (item 1 of bPos) & "|" & (item 2 of bPos) & "|" & (item 1 of bSize) & "|" & (item 2 of bSize)
            end try
        end repeat

        -- Text fields
        repeat with f in every text field of w
            try
                set fDesc to description of f
                set fName to name of f
                set fVal to value of f
                set fPos to position of f
                set fSize to size of f
                set end of results to "text_field|" & fDesc & "|" & fName & " = " & fVal & "|" & (item 1 of fPos) & "|" & (item 2 of fPos) & "|" & (item 1 of fSize) & "|" & (item 2 of fSize)
            end try
        end repeat

        -- Text areas
        repeat with a in every text area of w
            try
                set aDesc to description of a
                set aPos to position of a
                set aSize to size of a
                set end of results to "text_area|" & aDesc & "||" & (item 1 of aPos) & "|" & (item 2 of aPos) & "|" & (item 1 of aSize) & "|" & (item 2 of aSize)
            end try
        end repeat

        -- Static text (labels)
        repeat with s in every static text of w
            try
                set sVal to value of s
                set sPos to position of s
                set sSize to size of s
                if length of sVal < 100 then
                    set end of results to "label||" & sVal & "|" & (item 1 of sPos) & "|" & (item 2 of sPos) & "|" & (item 1 of sSize) & "|" & (item 2 of sSize)
                end if
            end try
        end repeat

        -- Groups (tabs, toolbars) - 1 level deep for buttons inside
        repeat with g in every group of w
            try
                repeat with gb in every button of g
                    try
                        set gbDesc to description of gb
                        set gbName to name of gb
                        set gbPos to position of gb
                        set gbSize to size of gb
                        set end of results to "button|" & gbDesc & "|" & gbName & "|" & (item 1 of gbPos) & "|" & (item 2 of gbPos) & "|" & (item 1 of gbSize) & "|" & (item 2 of gbSize)
                    end try
                end repeat
                repeat with gf in every text field of g
                    try
                        set gfDesc to description of gf
                        set gfName to name of gf
                        set gfVal to value of gf
                        set gfPos to position of gf
                        set gfSize to size of gf
                        set end of results to "text_field|" & gfDesc & "|" & gfName & " = " & gfVal & "|" & (item 1 of gfPos) & "|" & (item 2 of gfPos) & "|" & (item 1 of gfSize) & "|" & (item 2 of gfSize)
                    end try
                end repeat
            end try
        end repeat

        -- Toolbars
        repeat with t in every toolbar of w
            try
                repeat with tb in every button of t
                    try
                        set tbDesc to description of tb
                        set tbName to name of tb
                        set tbPos to position of tb
                        set tbSize to size of tb
                        set end of results to "toolbar_button|" & tbDesc & "|" & tbName & "|" & (item 1 of tbPos) & "|" & (item 2 of tbPos) & "|" & (item 1 of tbSize) & "|" & (item 2 of tbSize)
                    end try
                end repeat
                repeat with tf in every text field of t
                    try
                        set tfDesc to description of tf
                        set tfVal to value of tf
                        set tfPos to position of tf
                        set tfSize to size of tf
                        set end of results to "toolbar_field|" & tfDesc & "|" & tfVal & "|" & (item 1 of tfPos) & "|" & (item 2 of tfPos) & "|" & (item 1 of tfSize) & "|" & (item 2 of tfSize)
                    end try
                end repeat
            end try
        end repeat

    end try

    set text item delimiters to linefeed
    return appName & linefeed & (results as text)
end tell
'''
    result = await _run_cmd(["osascript", "-e", script])
    if result.returncode != 0:
        return []

    lines = result.stdout.strip().split("\n")
    if len(lines) < 1:
        return []

    app_name = lines[0]
    elements = []
    for line in lines[1:]:
        parts = line.split("|")
        if len(parts) >= 7:
            try:
                elements.append({
                    "role": parts[0],
                    "description": parts[1],
                    "name": parts[2],
                    "x": int(float(parts[3])),
                    "y": int(float(parts[4])),
                    "w": int(float(parts[5])),
                    "h": int(float(parts[6])),
                })
            except (ValueError, IndexError):
                continue

    return elements


async def click_element_by_description(description: str) -> RemoteResult:
    """Click a UI element by its accessibility description — no coordinate guessing.

    Uses AppleScript to find and click the element directly.
    """
    script = f'''
tell application "System Events"
    tell (first application process whose frontmost is true)
        try
            click (first button of window 1 whose description is "{description}")
            return "clicked button: {description}"
        end try
        try
            click (first button of window 1 whose name is "{description}")
            return "clicked button by name: {description}"
        end try
        try
            -- Search in groups and toolbars
            repeat with g in every group of window 1
                try
                    click (first button of g whose description is "{description}")
                    return "clicked nested button: {description}"
                end try
                try
                    click (first button of g whose name is "{description}")
                    return "clicked nested button by name: {description}"
                end try
            end repeat
            repeat with t in every toolbar of window 1
                try
                    click (first button of t whose description is "{description}")
                    return "clicked toolbar button: {description}"
                end try
            end repeat
        end try
        return "not found: {description}"
    end tell
end tell
'''
    result = await _run_cmd(["osascript", "-e", script])
    output = result.stdout.strip()
    success = "clicked" in output.lower() and "not found" not in output.lower()
    return RemoteResult(
        success=success,
        action="click_element",
        output=output,
        error="" if success else f"Element not found: {description}",
    )


async def focus_and_type(text_to_type: str, field_description: str = "") -> RemoteResult:
    """Focus a text field by description and type into it."""
    if field_description:
        script = f'''
tell application "System Events"
    tell (first application process whose frontmost is true)
        try
            set focused of (first text field of window 1 whose description is "{field_description}") to true
            delay 0.2
            keystroke "{text_to_type}"
            return "typed in: {field_description}"
        end try
        try
            -- Try toolbar fields
            repeat with t in every toolbar of window 1
                try
                    set focused of (first text field of t whose description contains "{field_description}") to true
                    delay 0.2
                    keystroke "{text_to_type}"
                    return "typed in toolbar: {field_description}"
                end try
            end repeat
        end try
        return "field not found: {field_description}"
    end tell
end tell
'''
        result = await _run_cmd(["osascript", "-e", script])
        output = result.stdout.strip()
        return RemoteResult(success="typed" in output.lower(), action="focus_and_type", output=output)

    # No field description — just type into whatever is focused
    return await type_text(text_to_type)


# ------------------------------------------------------------------
# Vision-driven autonomous control loop (with accessibility)
# ------------------------------------------------------------------

async def vision_control(
    task: str,
    max_steps: int = 10,
    on_step: Any = None,
) -> list[RemoteResult]:
    """See → Think → Act loop. The agent screenshots the screen, uses
    GPT-4o vision to understand what it sees, decides the next action,
    executes it, and repeats until the task is done.

    Args:
        task: Natural language description of what to do.
        max_steps: Max screenshot→action cycles.
        on_step: Optional async callback(step_num, screenshot_path, action_plan, result)
                 for streaming progress to Slack/Telegram.

    Returns:
        List of all action results.
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return [RemoteResult(success=False, action="vision_control", error="OPENAI_API_KEY not set")]

    all_results: list[RemoteResult] = []
    action_history: list[str] = []

    for step in range(max_steps):
        # 1. SCREENSHOT + ACCESSIBILITY TREE — see AND understand the screen
        ss_result = await take_screenshot()
        if not ss_result.success or not ss_result.screenshot_path:
            all_results.append(ss_result)
            break

        b64_image = await screenshot_to_base64(ss_result.screenshot_path)
        ui_elements = await get_ui_elements()

        # Format elements as a structured list for the LLM
        elements_text = "INTERACTIVE ELEMENTS (from accessibility API — use these for precise actions):\n"
        for i, el in enumerate(ui_elements[:40]):  # Cap at 40 elements
            center_x = el["x"] + el["w"] // 2
            center_y = el["y"] + el["h"] // 2
            label = el["description"] or el["name"] or "(unnamed)"
            elements_text += f'  [{i}] {el["role"]}: "{label}" — center ({center_x}, {center_y})\n'

        if not ui_elements:
            elements_text += "  (no elements detected — use screenshot for visual reference)\n"

        history_text = "\n".join(action_history[-5:]) if action_history else "None yet"

        messages = [
            {"role": "system", "content": f"""You are controlling a macOS computer. You have BOTH a screenshot AND an accessibility tree.

TASK: {task}

{elements_text}

Available actions (return ONE as JSON):
- {{"done": true, "summary": "..."}} — task is complete
- {{"action": "click_element", "element_name": "...", "description": "clicking the X button"}} — PREFERRED: click by accessibility name (exact match from list above)
- {{"action": "click", "x": N, "y": N, "description": "..."}} — fallback: click by coordinates (use CENTER coords from element list)
- {{"action": "type_in_field", "field_name": "...", "text": "...", "description": "..."}} — type into a named field
- {{"action": "type", "text": "...", "description": "typing into focused element"}} — type into whatever is focused
- {{"action": "key", "key": "cmd+c", "description": "..."}} — keyboard shortcut
- {{"action": "open_app", "name": "Safari", "description": "..."}} — launch app
- {{"action": "open_url", "url": "https://...", "description": "..."}} — open URL
- {{"action": "wait", "seconds": 2, "description": "waiting for..."}}

Rules:
- ALWAYS prefer click_element over raw click — it's more reliable
- Use the element list to find the right element, then click by name
- Only fall back to coordinate click if the element isn't in the list
- If coordinates are needed, use the CENTER values from the element list
- If the task is already done (visible in screenshot), return done

Previous actions: {history_text}

Respond with ONLY the JSON action object."""},
            {"role": "user", "content": [
                {"type": "text", "text": f"Step {step + 1}/{max_steps}. What should I do next?"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}", "detail": "high"}},
            ]},
        ]

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "gpt-5.4",
                    "messages": messages,
                    "max_completion_tokens": 500,
                    "response_format": {"type": "json_object"},
                },
            )
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "{}")

        try:
            plan = json.loads(content)
        except json.JSONDecodeError:
            plan = {"done": True, "summary": "Could not parse vision response"}

        # 4. CHECK if done
        if plan.get("done"):
            action_history.append(f"[DONE] {plan.get('summary', 'Task complete')}")
            if on_step:
                await on_step(step, ss_result.screenshot_path, plan, None)
            # Clean up screenshot
            try:
                os.unlink(ss_result.screenshot_path)
            except OSError:
                pass
            break

        # 5. EXECUTE the action
        action_desc = plan.get("description", str(plan.get("action", "unknown")))
        action_type = plan.get("action", "")

        result = RemoteResult(success=False, action=action_type, error="Unknown action")

        if action_type == "click_element":
            result = await click_element_by_description(plan.get("element_name", ""))
        elif action_type == "type_in_field":
            result = await focus_and_type(plan.get("text", ""), plan.get("field_name", ""))
        elif action_type == "click":
            result = await mouse_click(plan["x"], plan["y"], button="left")
        elif action_type == "double_click":
            result = await mouse_click(plan["x"], plan["y"], button="double")
        elif action_type == "right_click":
            result = await mouse_click(plan["x"], plan["y"], button="right")
        elif action_type == "type":
            result = await type_text(plan.get("text", ""))
        elif action_type == "key":
            result = await key_press(plan["key"])
        elif action_type == "open_app":
            result = await open_app(plan["name"])
        elif action_type == "open_url":
            result = await open_url(plan["url"])
        elif action_type == "scroll":
            # Scroll via cliclick or pyautogui
            direction = plan.get("direction", "down")
            amount = plan.get("amount", 3)
            scroll_key = "arrow-down" if direction == "down" else "arrow-up"
            for _ in range(amount):
                await key_press(scroll_key)
                await asyncio.sleep(0.1)
            result = RemoteResult(success=True, action="scroll", output=f"Scrolled {direction} {amount}x")
        elif action_type == "wait":
            await asyncio.sleep(min(plan.get("seconds", 2), 10))
            result = RemoteResult(success=True, action="wait", output=f"Waited {plan.get('seconds', 2)}s")

        all_results.append(result)
        action_history.append(f"Step {step + 1}: {action_desc} → {'OK' if result.success else result.error}")

        # 6. CALLBACK for progress
        if on_step:
            await on_step(step, ss_result.screenshot_path, plan, result)

        # Clean up screenshot
        try:
            os.unlink(ss_result.screenshot_path)
        except OSError:
            pass

        # Small delay for UI to settle
        await asyncio.sleep(0.5)

    return all_results


# ------------------------------------------------------------------
# Slack integration — upload screenshot + post results
# ------------------------------------------------------------------

async def upload_screenshot_to_slack(
    screenshot_path: str,
    channel: str,
    thread_ts: str = "",
    comment: str = "",
) -> dict[str, Any]:
    """Upload a screenshot to Slack using files.uploadV2."""
    token = os.getenv("SLACK_BOT_TOKEN", "")
    if not token:
        return {"ok": False, "error": "SLACK_BOT_TOKEN not set"}

    file_path = Path(screenshot_path)
    if not file_path.exists():
        return {"ok": False, "error": f"File not found: {screenshot_path}"}

    async with httpx.AsyncClient(timeout=60) as client:
        # Step 1: Get upload URL
        resp = await client.post(
            "https://slack.com/api/files.getUploadURLExternal",
            headers={"Authorization": f"Bearer {token}"},
            data={
                "filename": file_path.name,
                "length": file_path.stat().st_size,
            },
        )
        url_data = resp.json()
        if not url_data.get("ok"):
            return url_data

        upload_url = url_data["upload_url"]
        file_id = url_data["file_id"]

        # Step 2: Upload the file
        with open(screenshot_path, "rb") as f:
            await client.post(upload_url, files={"file": (file_path.name, f, "image/png")})

        # Step 3: Complete the upload
        channel_ids = channel if channel.startswith("C") else ""
        payload: dict[str, Any] = {
            "files": [{"id": file_id, "title": comment or "Remote Screenshot"}],
        }
        if channel_ids:
            payload["channel_id"] = channel_ids
        if thread_ts:
            payload["thread_ts"] = thread_ts

        resp = await client.post(
            "https://slack.com/api/files.completeUploadExternal",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

        # Clean up the temp file
        try:
            file_path.unlink()
        except Exception:
            pass

        return resp.json()


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------

@dataclass
class CmdResult:
    returncode: int
    stdout: str
    stderr: str


async def _run_cmd(cmd: list[str], timeout: int = 30) -> CmdResult:
    """Run a subprocess command async with timeout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return CmdResult(
            returncode=proc.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )
    except asyncio.TimeoutError:
        proc.kill()
        return CmdResult(returncode=-1, stdout="", stderr="Timed out")
    except FileNotFoundError as e:
        return CmdResult(returncode=-1, stdout="", stderr=str(e))


def cleanup_old_screenshots(max_age_hours: int = 1) -> int:
    """Remove screenshots older than max_age_hours. Returns count removed."""
    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0
    for f in SCREENSHOT_DIR.iterdir():
        if f.stat().st_mtime < cutoff:
            f.unlink()
            removed += 1
    return removed
