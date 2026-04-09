"""Telegram Bot API routes — webhook + cron endpoints.

Supports both webhook mode (Telegram pushes updates) and polling mode
(daemon pulls updates). The webhook is preferred for production.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/telegram", tags=["telegram"])

CRON_AUTH_TOKEN = os.getenv("CRON_AUTH_TOKEN", "")


def _verify_auth(authorization: Optional[str] = Header(None)):
    if not CRON_AUTH_TOKEN:
        return
    if not authorization or not authorization.replace("Bearer ", "") == CRON_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Webhook endpoint ──────────────────────────────────────────────────

@router.post("/webhook")
async def telegram_webhook(request: Request):
    """Receive updates from Telegram via webhook.

    Set webhook with:
        curl -X POST "https://api.telegram.org/bot$TOKEN/setWebhook?url=https://your-server.com/api/telegram/webhook"
    """
    from ..services.telegram_client import get_telegram_client

    client = get_telegram_client()
    if not client:
        return {"ok": False, "error": "Telegram not configured"}

    body = await request.json()
    update_id = body.get("update_id", 0)
    message = body.get("message", {})

    if not message:
        return {"ok": True, "skipped": "no message"}

    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text", "")
    from_user = message.get("from", {})
    user_id = str(from_user.get("id", ""))
    message_id = message.get("message_id")

    # Skip bot's own messages
    if from_user.get("is_bot"):
        return {"ok": True, "skipped": "bot message"}

    # Authorized user check
    authorized_user = os.getenv("TELEGRAM_AUTHORIZED_USER_ID", "")
    if authorized_user and user_id != authorized_user:
        logger.warning(f"Unauthorized Telegram user: {user_id}")
        return {"ok": True, "skipped": "unauthorized"}

    logger.info(f"Telegram message from {user_id}: {text[:100]}")

    # Route commands
    response_text = await _handle_command(text, chat_id, message_id, client)
    if response_text:
        await client.send_message(response_text, chat_id=chat_id, reply_to_message_id=message_id)

    return {"ok": True, "update_id": update_id}


# ── Cron/manual endpoints ────────────────────────────────────────────

@router.post("/notify")
async def send_notification(
    authorization: Optional[str] = Header(None),
    message: str = "",
    chat_id: Optional[str] = None,
):
    """Send a notification to Telegram (used by cron jobs, scripts)."""
    _verify_auth(authorization)

    from ..services.telegram_client import get_telegram_client
    client = get_telegram_client()
    if not client:
        return {"ok": False, "error": "Telegram not configured"}

    result = await client.send_message(message, chat_id=chat_id)
    return {"ok": True, "message_id": result.get("message_id")}


@router.post("/send-photo")
async def send_photo(
    authorization: Optional[str] = Header(None),
    photo_path: str = "",
    caption: str = "",
    chat_id: Optional[str] = None,
):
    """Upload and send a photo to Telegram."""
    _verify_auth(authorization)

    from ..services.telegram_client import get_telegram_client
    client = get_telegram_client()
    if not client:
        return {"ok": False, "error": "Telegram not configured"}

    result = await client.send_photo(photo_path, chat_id=chat_id, caption=caption)
    return {"ok": True, "result": result}


@router.post("/send-file")
async def send_file(
    authorization: Optional[str] = Header(None),
    file_path: str = "",
    caption: str = "",
    chat_id: Optional[str] = None,
):
    """Upload and send a file to Telegram."""
    _verify_auth(authorization)

    from ..services.telegram_client import get_telegram_client
    client = get_telegram_client()
    if not client:
        return {"ok": False, "error": "Telegram not configured"}

    result = await client.send_document(file_path, chat_id=chat_id, caption=caption)
    return {"ok": True, "result": result}


@router.get("/health")
async def telegram_health():
    """Check Telegram bot connectivity."""
    from ..services.telegram_client import get_telegram_client
    client = get_telegram_client()
    if not client:
        return {"ok": False, "status": "not_configured"}

    try:
        me = await client.get_me()
        return {
            "ok": True,
            "bot_username": me.get("username", "unknown"),
            "bot_id": me.get("id"),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Command handler ──────────────────────────────────────────────────

async def _handle_command(
    text: str, chat_id: str, message_id: int, client
) -> Optional[str]:
    """Route Telegram commands to appropriate handlers."""
    text = text.strip()

    # /start — welcome
    if text == "/start":
        return (
            "OpenClaw Remote Agent connected.\n\n"
            "Commands:\n"
            "/status — system status\n"
            "/screenshot — capture screen\n"
            "/shell <cmd> — run shell command\n"
            "/health — check services\n"
            "/pipeline <url> — run QA pipeline\n"
            "Or just type naturally — the agent interprets."
        )

    # /status
    if text == "/status":
        import subprocess
        try:
            uptime = subprocess.check_output(["uptime"], text=True).strip()
            return f"*System Status*\n`{uptime}`"
        except Exception as e:
            return f"Status check failed: {e}"

    # /screenshot
    if text == "/screenshot":
        import subprocess, platform
        try:
            path = "/tmp/telegram_screenshot.png"
            if platform.system() == "Darwin":
                subprocess.run(["screencapture", "-x", path], check=True, timeout=10)
            elif platform.system() == "Windows":
                subprocess.run(
                    ["python", "-c", f"import pyautogui; pyautogui.screenshot('{path}')"],
                    check=True, timeout=10,
                )
            else:
                subprocess.run(
                    ["import", "-window", "root", path], check=True, timeout=10
                )
            await client.send_photo(path, chat_id=chat_id, caption="Current screen", reply_to_message_id=message_id)
            return None  # Photo sent directly
        except Exception as e:
            return f"Screenshot failed: {e}"

    # /shell <command>
    if text.startswith("/shell "):
        cmd = text[7:].strip()
        # Security: basic blocklist
        blocked = ["rm -rf", "sudo", "passwd", "mkfs", "dd if="]
        if any(b in cmd.lower() for b in blocked):
            return "Blocked: potentially dangerous command."
        import subprocess
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=30
            )
            output = (result.stdout or "") + (result.stderr or "")
            output = output[:3900]  # Telegram limit
            return f"```\n$ {cmd}\n{output}\n```" if output else f"Command executed (no output): `{cmd}`"
        except subprocess.TimeoutExpired:
            return f"Command timed out after 30s: `{cmd}`"
        except Exception as e:
            return f"Shell error: {e}"

    # /health
    if text == "/health":
        import httpx as _httpx
        checks = []
        async with _httpx.AsyncClient(timeout=5.0) as c:
            for name, url in [("Backend", "http://localhost:8000/api/health"), ("Frontend", "http://localhost:5173")]:
                try:
                    r = await c.get(url)
                    checks.append(f"✅ {name}: {r.status_code}")
                except Exception:
                    checks.append(f"❌ {name}: down")
        return "*Health Check*\n" + "\n".join(checks)

    # /pipeline <url>
    if text.startswith("/pipeline "):
        url = text[10:].strip()
        if not url.startswith("http"):
            return "Usage: /pipeline https://your-app.com"
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=30.0) as c:
                token = os.getenv("RETENTION_MCP_TOKEN", "sk-ret-de55f65c")
                r = await c.post(
                    "http://localhost:8000/mcp/tools/call",
                    json={"tool": "retention.run_web_flow", "arguments": {"url": url, "app_name": "Telegram QA"}},
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = r.json().get("result", {})
                run_id = data.get("run_id", "unknown")
                return f"Pipeline started: `{run_id}`\nURL: {url}\nPoll: `/status {run_id}`"
        except Exception as e:
            return f"Pipeline start failed: {e}"

    # Natural language fallback — forward to strategy-brief agent
    # (or just echo for now)
    return f"Received: _{text}_\n\n(Natural language routing coming soon — use /commands for now)"
