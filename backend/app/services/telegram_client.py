"""Telegram Bot Client — async wrapper for the Telegram Bot API.

Mirrors the Slack client pattern: retry logic, message splitting,
thread (reply_to_message_id) management, and file uploads.

No external dependencies — uses httpx directly against the Bot API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
DEFAULT_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Rate limit / retry
_MAX_RETRIES = 3
_RETRY_STATUSES = {429, 500, 502, 503}


class TelegramClient:
    """Async Telegram Bot API client with retry and message splitting."""

    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token or BOT_TOKEN
        self.chat_id = chat_id or DEFAULT_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self._client: Optional[httpx.AsyncClient] = None

        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def _request(
        self, method: str, data: Optional[Dict] = None, files: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make a Telegram Bot API request with retry."""
        url = f"{self.base_url}/{method}"
        client = await self._get_client()

        for attempt in range(_MAX_RETRIES):
            try:
                if files:
                    resp = await client.post(url, data=data or {}, files=files)
                else:
                    resp = await client.post(url, json=data or {})

                if resp.status_code == 200:
                    result = resp.json()
                    if result.get("ok"):
                        return result.get("result", {})
                    logger.warning(f"Telegram API error: {result.get('description', 'unknown')}")
                    return result

                if resp.status_code in _RETRY_STATUSES:
                    retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
                    jitter = random.uniform(0, 1)
                    delay = min(retry_after + jitter, 60)
                    logger.warning(f"Telegram {resp.status_code}, retry in {delay:.1f}s (attempt {attempt + 1})")
                    await asyncio.sleep(delay)
                    continue

                logger.error(f"Telegram API {resp.status_code}: {resp.text[:200]}")
                return {"ok": False, "error": resp.text[:200]}

            except Exception as e:
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.error(f"Telegram request failed: {e}")
                return {"ok": False, "error": str(e)}

        return {"ok": False, "error": "Max retries exceeded"}

    # ── Messages ──────────────────────────────────────────────────────

    async def send_message(
        self,
        text: str,
        chat_id: Optional[str] = None,
        reply_to_message_id: Optional[int] = None,
        parse_mode: str = "Markdown",
        disable_web_page_preview: bool = True,
    ) -> Dict[str, Any]:
        """Send a text message, auto-splitting if > 4096 chars."""
        target_chat = chat_id or self.chat_id
        if not target_chat:
            logger.error("No chat_id provided and TELEGRAM_CHAT_ID not set")
            return {}

        # Telegram max message length is 4096 chars
        if len(text) <= 4096:
            data: Dict[str, Any] = {
                "chat_id": target_chat,
                "text": text,
                "disable_web_page_preview": disable_web_page_preview,
            }
            if parse_mode:
                data["parse_mode"] = parse_mode
            if reply_to_message_id:
                data["reply_to_message_id"] = reply_to_message_id
            return await self._request("sendMessage", data)

        # Split into chunks
        chunks = _split_message(text, 4096)
        last_result = {}
        for i, chunk in enumerate(chunks):
            data = {
                "chat_id": target_chat,
                "text": chunk,
                "disable_web_page_preview": disable_web_page_preview,
            }
            if parse_mode:
                data["parse_mode"] = parse_mode
            # First chunk replies to original, rest reply to first chunk
            if i == 0 and reply_to_message_id:
                data["reply_to_message_id"] = reply_to_message_id
            elif i > 0 and last_result.get("message_id"):
                data["reply_to_message_id"] = last_result["message_id"]

            last_result = await self._request("sendMessage", data)
            if i < len(chunks) - 1:
                await asyncio.sleep(0.3)  # Avoid rate limit on multi-part

        return last_result

    async def edit_message(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        parse_mode: str = "Markdown",
    ) -> Dict[str, Any]:
        """Edit an existing message (for streaming updates)."""
        data: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text[:4096],
        }
        if parse_mode:
            data["parse_mode"] = parse_mode
        return await self._request("editMessageText", data)

    # ── Files ─────────────────────────────────────────────────────────

    async def send_photo(
        self,
        photo_path: str,
        chat_id: Optional[str] = None,
        caption: Optional[str] = None,
        reply_to_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Upload and send a photo."""
        target_chat = chat_id or self.chat_id
        data: Dict[str, Any] = {"chat_id": target_chat}
        if caption:
            data["caption"] = caption[:1024]
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id

        with open(photo_path, "rb") as f:
            return await self._request("sendPhoto", data=data, files={"photo": f})

    async def send_document(
        self,
        file_path: str,
        chat_id: Optional[str] = None,
        caption: Optional[str] = None,
        reply_to_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Upload and send a file/document."""
        target_chat = chat_id or self.chat_id
        data: Dict[str, Any] = {"chat_id": target_chat}
        if caption:
            data["caption"] = caption[:1024]
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id

        with open(file_path, "rb") as f:
            filename = os.path.basename(file_path)
            return await self._request("sendDocument", data=data, files={"document": (filename, f)})

    async def send_video(
        self,
        video_path: str,
        chat_id: Optional[str] = None,
        caption: Optional[str] = None,
        reply_to_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Upload and send a video."""
        target_chat = chat_id or self.chat_id
        data: Dict[str, Any] = {"chat_id": target_chat}
        if caption:
            data["caption"] = caption[:1024]
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id

        with open(video_path, "rb") as f:
            filename = os.path.basename(video_path)
            return await self._request("sendVideo", data=data, files={"video": (filename, f)})

    # ── Updates (polling) ─────────────────────────────────────────────

    async def get_updates(
        self,
        offset: Optional[int] = None,
        timeout: int = 30,
        allowed_updates: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Long-poll for new updates."""
        data: Dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            data["offset"] = offset
        if allowed_updates:
            data["allowed_updates"] = allowed_updates

        result = await self._request("getUpdates", data)
        if isinstance(result, list):
            return result
        return []

    async def get_me(self) -> Dict[str, Any]:
        """Get bot info (verify token works)."""
        return await self._request("getMe")

    # ── Cleanup ───────────────────────────────────────────────────────

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def _split_message(text: str, max_len: int = 4096) -> List[str]:
    """Split a long message into chunks, preferring line breaks."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        # Find a good split point (newline near max_len)
        split_at = remaining.rfind("\n", 0, max_len)
        if split_at < max_len // 2:
            split_at = max_len  # No good newline, hard split
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")

    return chunks


# ── Module-level singleton ────────────────────────────────────────────

_shared_client: Optional[TelegramClient] = None


def get_telegram_client() -> Optional[TelegramClient]:
    """Get or create the shared Telegram client. Returns None if not configured."""
    global _shared_client
    if _shared_client is not None:
        return _shared_client
    if not BOT_TOKEN:
        logger.info("TELEGRAM_BOT_TOKEN not set — Telegram integration disabled")
        return None
    try:
        _shared_client = TelegramClient()
        return _shared_client
    except Exception as e:
        logger.warning(f"Failed to create Telegram client: {e}")
        return None
