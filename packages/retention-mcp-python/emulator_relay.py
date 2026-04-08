"""
retention.sh — Emulator relay.

Receives emulator commands from the retention.sh server via WebSocket and
executes them locally via ADB. Results are sent back through the same
connection.

Supported commands:
  - adb_shell: run an ADB shell command
  - adb_install: install an APK
  - launch_emulator: start an emulator AVD
  - list_devices: list connected ADB devices
  - tap / swipe / type_text / press_key: input events
  - screenshot: capture and return a screenshot
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def _adb_path() -> str:
    """Resolve ADB binary path."""
    android_home = os.environ.get(
        "ANDROID_HOME",
        os.path.expanduser("~/Library/Android/sdk"),
    )
    adb = os.path.join(android_home, "platform-tools", "adb")
    if os.path.isfile(adb):
        return adb
    # Fallback to PATH
    found = shutil.which("adb")
    return found or "adb"


async def _run_adb(*args: str, device: str | None = None) -> dict[str, Any]:
    """Run an ADB command and return stdout/stderr/returncode."""
    cmd = [_adb_path()]
    if device:
        cmd += ["-s", device]
    cmd += list(args)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return {
        "stdout": stdout.decode(errors="replace"),
        "stderr": stderr.decode(errors="replace"),
        "returncode": proc.returncode,
    }


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def handle_list_devices(_payload: dict[str, Any]) -> dict[str, Any]:
    """List connected ADB devices."""
    result = await _run_adb("devices", "-l")
    lines = result["stdout"].strip().split("\n")[1:]  # skip header
    devices = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 2:
            devices.append({"serial": parts[0], "state": parts[1]})
    return {"devices": devices}


async def handle_adb_shell(payload: dict[str, Any]) -> dict[str, Any]:
    """Run an ADB shell command."""
    command = payload.get("command", "")
    device = payload.get("device")
    if not command:
        return {"error": "No command provided"}
    return await _run_adb("shell", command, device=device)


async def handle_adb_install(payload: dict[str, Any]) -> dict[str, Any]:
    """Install an APK on a device."""
    apk_path = payload.get("apk_path", "")
    device = payload.get("device")
    if not apk_path or not os.path.isfile(apk_path):
        return {"error": f"APK not found: {apk_path}"}
    return await _run_adb("install", "-r", apk_path, device=device)


async def handle_launch_emulator(payload: dict[str, Any]) -> dict[str, Any]:
    """Launch an Android emulator AVD."""
    avd_name = payload.get("avd_name", "")
    android_home = os.environ.get(
        "ANDROID_HOME",
        os.path.expanduser("~/Library/Android/sdk"),
    )
    emulator_bin = os.path.join(android_home, "emulator", "emulator")
    if not os.path.isfile(emulator_bin):
        return {"error": "Emulator binary not found"}

    if not avd_name:
        # List available AVDs and pick the first
        proc = await asyncio.create_subprocess_exec(
            emulator_bin, "-list-avds",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        avds = [l.strip() for l in stdout.decode().strip().split("\n") if l.strip()]
        if not avds:
            return {"error": "No AVDs available"}
        avd_name = avds[0]

    # Launch in background
    proc = await asyncio.create_subprocess_exec(
        emulator_bin,
        "-avd", avd_name,
        "-no-snapshot",
        "-gpu", "swiftshader_indirect",
        "-no-boot-anim",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return {"avd_name": avd_name, "pid": proc.pid, "status": "launching"}


async def handle_screenshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Capture a screenshot and return as base64 PNG."""
    device = payload.get("device")
    result = await _run_adb("exec-out", "screencap", "-p", device=device)
    if result["returncode"] != 0:
        return {"error": result["stderr"]}
    # screencap -p via exec-out returns raw PNG bytes in stdout
    # Re-run with raw output
    cmd = [_adb_path()]
    if device:
        cmd += ["-s", device]
    cmd += ["exec-out", "screencap", "-p"]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return {"error": stderr.decode(errors="replace")}

    return {"image_base64": base64.b64encode(stdout).decode(), "format": "png"}


async def handle_tap(payload: dict[str, Any]) -> dict[str, Any]:
    """Tap at coordinates."""
    x, y = payload.get("x", 0), payload.get("y", 0)
    device = payload.get("device")
    return await _run_adb("shell", f"input tap {x} {y}", device=device)


async def handle_swipe(payload: dict[str, Any]) -> dict[str, Any]:
    """Swipe between coordinates."""
    x1, y1 = payload.get("x1", 0), payload.get("y1", 0)
    x2, y2 = payload.get("x2", 0), payload.get("y2", 0)
    duration = payload.get("duration", 300)
    device = payload.get("device")
    return await _run_adb(
        "shell", f"input swipe {x1} {y1} {x2} {y2} {duration}",
        device=device,
    )


async def handle_type_text(payload: dict[str, Any]) -> dict[str, Any]:
    """Type text on the device."""
    text = payload.get("text", "")
    device = payload.get("device")
    # Escape spaces for ADB input
    escaped = text.replace(" ", "%s")
    return await _run_adb("shell", f"input text '{escaped}'", device=device)


async def handle_press_key(payload: dict[str, Any]) -> dict[str, Any]:
    """Press a key event (e.g., KEYCODE_HOME)."""
    keycode = payload.get("keycode", "KEYCODE_HOME")
    device = payload.get("device")
    return await _run_adb("shell", f"input keyevent {keycode}", device=device)


# ---------------------------------------------------------------------------
# Command dispatcher
# ---------------------------------------------------------------------------

COMMAND_HANDLERS: dict[str, Any] = {
    "list_devices": handle_list_devices,
    "adb_shell": handle_adb_shell,
    "adb_install": handle_adb_install,
    "launch_emulator": handle_launch_emulator,
    "screenshot": handle_screenshot,
    "tap": handle_tap,
    "swipe": handle_swipe,
    "type_text": handle_type_text,
    "press_key": handle_press_key,
}


async def dispatch_command(msg: dict[str, Any]) -> dict[str, Any]:
    """Dispatch an incoming command message to the appropriate handler.

    Expected message format:
        {"type": "command", "command": "<name>", "request_id": "...", ...payload}

    Returns a response dict with the same request_id.
    """
    command = msg.get("command", "")
    request_id = msg.get("request_id", "")
    handler = COMMAND_HANDLERS.get(command)

    if not handler:
        return {
            "type": "response",
            "request_id": request_id,
            "error": f"Unknown command: {command}",
        }

    try:
        result = await handler(msg)
        return {"type": "response", "request_id": request_id, **result}
    except Exception as e:
        logger.exception("Command '%s' failed", command)
        return {
            "type": "response",
            "request_id": request_id,
            "error": str(e),
        }
