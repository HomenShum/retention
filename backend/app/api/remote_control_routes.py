"""FastAPI routes for remote computer control via Slack.

Endpoints:
  POST /api/remote/screenshot  — capture screen, return base64 or upload to Slack
  POST /api/remote/execute     — execute a single action (click, type, key, etc.)
  POST /api/remote/plan        — LLM interprets natural language → action plan → execute
  POST /api/remote/claude      — run Claude Code command remotely
  POST /api/remote/shell       — run a shell command
  POST /api/remote/status      — system status (running apps, screen size, etc.)

All endpoints require Bearer token auth (CRON_AUTH_TOKEN).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/remote", tags=["remote-control"])


def _verify_auth(authorization: str | None) -> None:
    """Verify the auth token."""
    expected = os.getenv("CRON_AUTH_TOKEN", "")
    if not expected:
        logger.warning("CRON_AUTH_TOKEN not set — rejecting remote control request")
        raise HTTPException(status_code=401, detail="Auth not configured")
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    token = authorization.replace("Bearer ", "")
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid auth token")


# ------------------------------------------------------------------
# Screenshot
# ------------------------------------------------------------------

@router.post("/screenshot")
async def screenshot(
    authorization: str | None = Header(None),
    region: str = Query("", description="x,y,w,h for region capture"),
    window: bool = Query(False, description="Capture frontmost window only"),
    upload_to_slack: bool = Query(True, description="Upload to Slack channel"),
    channel: str = Query("", description="Slack channel ID"),
    thread_ts: str = Query("", description="Slack thread timestamp"),
) -> dict[str, Any]:
    """Capture a screenshot and optionally upload to Slack."""
    _verify_auth(authorization)

    from ..services.remote_control import take_screenshot, upload_screenshot_to_slack, screenshot_to_base64

    result = await take_screenshot(
        region=region or None,
        window=window,
    )

    if not result.success:
        return {"success": False, "error": result.error}

    response: dict[str, Any] = {
        "success": True,
        "output": result.output,
        "path": result.screenshot_path,
    }

    if upload_to_slack and result.screenshot_path:
        from ..services.remote_control import SCREENSHOT_DIR
        from ..services.slack_client import CLAW_CHANNEL_ID

        target_channel = channel or CLAW_CHANNEL_ID
        upload_result = await upload_screenshot_to_slack(
            result.screenshot_path,
            channel=target_channel,
            thread_ts=thread_ts,
            comment="Remote Screenshot",
        )
        response["slack_upload"] = upload_result.get("ok", False)
    else:
        # Return base64 for API consumers
        if result.screenshot_path:
            response["base64"] = await screenshot_to_base64(result.screenshot_path)

    return response


# ------------------------------------------------------------------
# Execute single action
# ------------------------------------------------------------------

@router.post("/execute")
async def execute_action(
    authorization: str | None = Header(None),
    action_type: str = Query(..., description="Action type: click, type, key, open_app, etc."),
    x: int = Query(0), y: int = Query(0),
    x2: int = Query(0), y2: int = Query(0),
    button: str = Query("left"),
    text: str = Query(""),
    key: str = Query(""),
    name: str = Query(""),
    url: str = Query(""),
    command: str = Query(""),
) -> dict[str, Any]:
    """Execute a single remote control action."""
    _verify_auth(authorization)

    from ..services.remote_control import execute_action as _exec

    action_map: dict[str, dict] = {
        "click": {"type": "click", "x": x, "y": y, "button": button},
        "move": {"type": "move", "x": x, "y": y},
        "drag": {"type": "drag", "x1": x, "y1": y, "x2": x2, "y2": y2},
        "type": {"type": "type", "text": text},
        "key": {"type": "key", "key": key},
        "open_app": {"type": "open_app", "name": name},
        "open_url": {"type": "open_url", "url": url},
        "shell": {"type": "shell", "command": command},
        "clipboard_get": {"type": "clipboard_get"},
        "clipboard_set": {"type": "clipboard_set", "text": text},
        "screen_info": {"type": "screen_info"},
        "list_apps": {"type": "list_apps"},
        "frontmost_app": {"type": "frontmost_app"},
    }

    action = action_map.get(action_type)
    if not action:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action_type}")

    result = await _exec(action)
    return {
        "success": result.success,
        "action": result.action,
        "output": result.output,
        "error": result.error,
    }


# ------------------------------------------------------------------
# Natural language plan + execute
# ------------------------------------------------------------------

@router.post("/plan")
async def plan_and_execute(
    authorization: str | None = Header(None),
    message: str = Query(..., description="Natural language command"),
    channel: str = Query("", description="Slack channel for screenshots"),
    thread_ts: str = Query("", description="Thread timestamp"),
    dry_run: bool = Query(False, description="Return plan without executing"),
) -> dict[str, Any]:
    """Interpret a natural language command via LLM, then execute the actions."""
    _verify_auth(authorization)

    from ..services.remote_control import (
        interpret_command, execute_plan, upload_screenshot_to_slack,
    )
    from ..services.slack_client import CLAW_CHANNEL_ID

    plan = await interpret_command(message)

    if dry_run:
        return {"success": True, "plan": plan, "executed": False}

    results = await execute_plan(plan)

    # Upload any screenshots to Slack
    target_channel = channel or CLAW_CHANNEL_ID
    for r in results:
        if r.screenshot_path and r.success:
            await upload_screenshot_to_slack(
                r.screenshot_path,
                channel=target_channel,
                thread_ts=thread_ts,
                comment=plan.get("description", "Remote action result"),
            )

    return {
        "success": all(r.success for r in results),
        "plan": plan,
        "results": [
            {
                "action": r.action,
                "success": r.success,
                "output": r.output,
                "error": r.error,
            }
            for r in results
        ],
    }


# ------------------------------------------------------------------
# Claude Code bridge
# ------------------------------------------------------------------

@router.post("/claude")
async def run_claude_code(
    authorization: str | None = Header(None),
    prompt: str = Query(..., description="Prompt for Claude Code"),
    working_dir: str = Query("", description="Working directory"),
    timeout: int = Query(300, description="Timeout in seconds"),
) -> dict[str, Any]:
    """Run a Claude Code command remotely."""
    _verify_auth(authorization)

    from ..services.remote_control import run_claude_code as _run

    result = await _run(prompt, working_dir=working_dir, timeout=timeout)
    return {
        "success": result.success,
        "output": result.output,
        "error": result.error,
    }


# ------------------------------------------------------------------
# Vision-driven autonomous control
# ------------------------------------------------------------------

@router.post("/vision")
async def vision_control_endpoint(
    authorization: str = Header(None),
    task: str = Query(..., description="Natural language task to perform visually"),
    max_steps: int = Query(10, description="Max screenshot-action cycles"),
) -> dict:
    """See-Think-Act loop: screenshot → GPT-4o vision → click/type → repeat.

    The agent literally sees the screen and operates the laptop like a human.
    """
    _verify_auth(authorization)

    from ..services.remote_control import vision_control

    results = await vision_control(task, max_steps=max_steps)
    return {
        "success": all(r.success for r in results),
        "steps": len(results),
        "actions": [
            {"action": r.action, "success": r.success, "output": r.output, "error": r.error}
            for r in results
        ],
    }


# ------------------------------------------------------------------
# Shell command
# ------------------------------------------------------------------

@router.post("/shell")
async def run_shell(
    authorization: str | None = Header(None),
    command: str = Query(..., description="Shell command to run"),
    timeout: int = Query(30, description="Timeout in seconds"),
) -> dict[str, Any]:
    """Run a shell command remotely."""
    _verify_auth(authorization)

    from ..services.remote_control import run_shell as _run

    result = await _run(command, timeout=timeout)
    return {
        "success": result.success,
        "output": result.output,
        "error": result.error,
    }


# ------------------------------------------------------------------
# System status
# ------------------------------------------------------------------

@router.get("/status")
async def system_status(
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Get system status: running apps, screen size, frontmost app."""
    _verify_auth(authorization)

    from ..services.remote_control import (
        get_screen_size, get_frontmost_app, list_running_apps, get_clipboard,
    )

    import asyncio
    screen, front, apps, clipboard = await asyncio.gather(
        get_screen_size(),
        get_frontmost_app(),
        list_running_apps(),
        get_clipboard(),
    )

    return {
        "success": True,
        "screen_size": screen.output,
        "frontmost_app": front.output,
        "running_apps": apps.output,
        "clipboard_preview": clipboard.output[:200] if clipboard.success else "",
    }
