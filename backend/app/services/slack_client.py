"""Shared async Slack API client.

Centralizes all Slack API calls used by the autonomous agent services.
Extracted from mcp_server.py's _slack_api helper pattern.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

BOT_USER_ID = "U0ALSPANA1G"
CLAW_CHANNEL_ID = "C0AM2J4G6S0"  # #claw-communications
SLACK_API_BASE = "https://slack.com/api"


class SlackClient:
    """Async Slack Web API client using httpx."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("SLACK_BOT_TOKEN", "")
        if not self.token:
            raise RuntimeError("SLACK_BOT_TOKEN not set — cannot create SlackClient")
        self._client = httpx.AsyncClient(
            base_url=SLACK_API_BASE,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=httpx.Timeout(30, connect=10),
        )
        # Channel name -> ID cache (populated lazily)
        self._channel_cache: dict[str, str] = {}

    async def close(self):
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Low-level API call
    # ------------------------------------------------------------------

    _RETRYABLE_STATUS_CODES = {429, 500, 502, 503}
    _MAX_RETRIES = 3

    async def _api(self, method: str, **kwargs: Any) -> dict:
        """Call a Slack Web API method. Returns the parsed JSON response.

        Retries up to 3 times on 429/500/502/503 with exponential backoff
        (1s, 2s, 4s) plus small random jitter.
        """
        for attempt in range(self._MAX_RETRIES + 1):
            resp = await self._client.get(f"/{method}", params=kwargs)
            if resp.status_code in self._RETRYABLE_STATUS_CODES and attempt < self._MAX_RETRIES:
                delay = (2 ** attempt) + random.uniform(0, 0.5)
                logger.warning("Slack API %s returned %d, retrying in %.1fs (attempt %d/%d)",
                               method, resp.status_code, delay, attempt + 1, self._MAX_RETRIES)
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            break
        data = resp.json()
        if not data.get("ok"):
            error = data.get("error", "unknown")
            logger.error("Slack API %s failed: %s", method, error)
            raise SlackAPIError(method, error)
        return data

    async def _post_api(self, method: str, payload: dict) -> dict:
        """POST to a Slack Web API method with JSON body.

        Retries up to 3 times on 429/500/502/503 with exponential backoff
        (1s, 2s, 4s) plus small random jitter.
        """
        for attempt in range(self._MAX_RETRIES + 1):
            resp = await self._client.post(
                f"/{method}",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code in self._RETRYABLE_STATUS_CODES and attempt < self._MAX_RETRIES:
                delay = (2 ** attempt) + random.uniform(0, 0.5)
                logger.warning("Slack API %s returned %d, retrying in %.1fs (attempt %d/%d)",
                               method, resp.status_code, delay, attempt + 1, self._MAX_RETRIES)
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            break
        data = resp.json()
        if not data.get("ok"):
            error = data.get("error", "unknown")
            logger.error("Slack API %s failed: %s", method, error)
            raise SlackAPIError(method, error)
        return data

    # ------------------------------------------------------------------
    # Channel operations
    # ------------------------------------------------------------------

    async def list_channels(self, limit: int = 50) -> list[dict]:
        """List public channels the bot is a member of."""
        data = await self._api(
            "conversations.list", types="public_channel", limit=limit
        )
        channels = data.get("channels", [])
        member_channels = [c for c in channels if c.get("is_member")]
        for ch in member_channels:
            self._channel_cache[ch["name"]] = ch["id"]
        return member_channels

    async def resolve_channel(self, channel: str) -> str:
        """Resolve a channel name (e.g. '#general') or ID to a channel ID."""
        clean = channel.lstrip("#")
        if clean.startswith("C") and len(clean) > 8:
            return clean
        if clean in self._channel_cache:
            return self._channel_cache[clean]
        await self.list_channels()
        if clean in self._channel_cache:
            return self._channel_cache[clean]
        raise ValueError(f"Channel not found: {channel}")

    # ------------------------------------------------------------------
    # Message reading
    # ------------------------------------------------------------------

    async def get_channel_history(
        self,
        channel: str,
        limit: int = 50,
        oldest: Optional[float] = None,
    ) -> list[dict]:
        """Get recent messages from a channel."""
        channel_id = await self.resolve_channel(channel)
        params: dict[str, Any] = {"channel": channel_id, "limit": limit}
        if oldest is not None:
            params["oldest"] = str(oldest)
        data = await self._api("conversations.history", **params)
        return data.get("messages", [])

    async def get_thread(self, channel: str, thread_ts: str) -> list[dict]:
        """Get all replies in a thread."""
        channel_id = await self.resolve_channel(channel)
        data = await self._api(
            "conversations.replies", channel=channel_id, ts=thread_ts, limit=100
        )
        return data.get("messages", [])

    async def search_messages(self, query: str, count: int = 10) -> list[dict]:
        """Search Slack messages across channels."""
        data = await self._api("search.messages", query=query, count=count, sort="timestamp")
        return data.get("messages", {}).get("matches", [])

    # ------------------------------------------------------------------
    # Message writing
    # ------------------------------------------------------------------

    @staticmethod
    def _md_to_slack(text: str) -> str:
        """Convert Markdown to Slack mrkdwn.

        Slack doesn't support: **bold**, ## headings, markdown tables.
        Slack uses: *bold*, _italic_, `code`.

        Applied automatically to all outgoing messages so LLM output
        renders correctly regardless of which model/prompt generated it.
        """
        import re
        # **bold** → *bold* (but not inside URLs or code blocks)
        text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
        # ## Heading → *Heading* (bold line)
        text = re.sub(r'^#{1,4}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
        # [text](url) → <url|text> (Slack link format)
        text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', text)
        return text

    _SLACK_MAX_CHARS = 3800  # Slack limit is 4000, leave room for formatting

    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: Optional[str] = None,
        unfurl_links: bool = False,
    ) -> dict:
        """Post a message to a channel or thread.

        Automatically converts Markdown to Slack mrkdwn format.
        If the message exceeds Slack's character limit, splits it into
        multiple messages at clean paragraph/bullet boundaries.
        """
        text = self._md_to_slack(text)
        channel_id = await self.resolve_channel(channel)

        # If it fits, post as-is
        if len(text) <= self._SLACK_MAX_CHARS:
            payload: dict[str, Any] = {
                "channel": channel_id,
                "text": text,
                "unfurl_links": unfurl_links,
            }
            if thread_ts:
                payload["thread_ts"] = thread_ts
            return await self._post_api("chat.postMessage", payload)

        # Split long messages at clean boundaries
        parts = self._split_message(text)
        first_result = None
        for i, part in enumerate(parts):
            if i > 0:
                part = f"_... continued ({i+1}/{len(parts)})_\n\n{part}"
            payload = {
                "channel": channel_id,
                "text": part,
                "unfurl_links": unfurl_links,
            }
            if thread_ts:
                payload["thread_ts"] = thread_ts
            result = await self._post_api("chat.postMessage", payload)
            if i == 0:
                first_result = result
        return first_result or {}

    @classmethod
    def _split_message(cls, text: str) -> list[str]:
        """Split text at paragraph/bullet boundaries to fit Slack limits."""
        if len(text) <= cls._SLACK_MAX_CHARS:
            return [text]

        parts = []
        remaining = text
        while remaining:
            if len(remaining) <= cls._SLACK_MAX_CHARS:
                parts.append(remaining)
                break

            # Find a clean split point within the limit
            chunk = remaining[:cls._SLACK_MAX_CHARS]
            # Try double newline (paragraph break)
            split_at = chunk.rfind("\n\n")
            if split_at < cls._SLACK_MAX_CHARS // 3:
                # Try single newline with bullet
                split_at = chunk.rfind("\n- ")
                if split_at < cls._SLACK_MAX_CHARS // 3:
                    split_at = chunk.rfind("\n")
                    if split_at < cls._SLACK_MAX_CHARS // 3:
                        split_at = cls._SLACK_MAX_CHARS  # Hard split

            parts.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()

        return parts

    async def post_thread_reply(
        self, channel: str, thread_ts: str, text: str
    ) -> dict:
        """Post a reply to a specific thread."""
        return await self.post_message(channel, text, thread_ts=thread_ts)

    async def update_message(
        self, channel: str, ts: str, text: str
    ) -> dict:
        """Update an existing message."""
        channel_id = await self.resolve_channel(channel)
        return await self._post_api("chat.update", {
            "channel": channel_id, "ts": ts, "text": text,
        })

    async def delete_message(self, channel: str, ts: str) -> dict:
        """Delete a message. Only works for bot-posted messages."""
        channel_id = await self.resolve_channel(channel)
        return await self._post_api("chat.delete", {
            "channel": channel_id, "ts": ts,
        })

    # ------------------------------------------------------------------
    # Bot activity
    # ------------------------------------------------------------------

    async def get_bot_recent_posts(self, hours: int = 2) -> list[dict]:
        """Search for recent bot posts to check engagement."""
        try:
            data = await self._api(
                "search.messages",
                query=f"from:<@{BOT_USER_ID}>",
                count=20,
                sort="timestamp",
            )
            return data.get("messages", {}).get("matches", [])
        except SlackAPIError:
            return []

    # ------------------------------------------------------------------
    # Daily Command Center thread
    # ------------------------------------------------------------------

    # In-memory cache to prevent duplicate creation within the same process.
    # Multiple crons fire simultaneously on Render — this lock + cache
    # ensures only ONE header is created per day per process.
    _daily_thread_cache: dict[str, str] = {}  # date_iso -> thread_ts
    _daily_thread_lock: asyncio.Lock = asyncio.Lock()

    async def get_or_create_daily_thread(
        self, channel: str = CLAW_CHANNEL_ID
    ) -> str:
        """Return today's Command Center thread_ts, creating if needed.

        Uses a three-layer dedup strategy to prevent duplicate headers:
        1. In-memory cache (fast, prevents same-process races)
        2. Convex task state (persisted, prevents cross-deploy races)
        3. Slack channel scan (fallback, catches any remaining dupes)

        Returns the thread_ts string for use in subsequent post_message calls.
        """
        import asyncio
        from .convex_client import ConvexClient
        import datetime

        today = datetime.date.today().isoformat()  # "2026-03-17"

        # Layer 1: in-memory cache (instant, no network)
        cached = SlackClient._daily_thread_cache.get(today)
        if cached:
            return cached

        async with SlackClient._daily_thread_lock:
            # Double-check after acquiring lock
            cached = SlackClient._daily_thread_cache.get(today)
            if cached:
                return cached

            convex = ConvexClient()
            try:
                # Layer 2: Convex persisted state
                state = await convex.get_task_state("daily_thread")
                if state and state.get("dailyThreadDate") == today:
                    stored_ts = state.get("dailyThreadTs")
                    if stored_ts:
                        SlackClient._daily_thread_cache[today] = stored_ts
                        return stored_ts

                # Layer 3: scan recent Slack messages for an existing header
                # (catches cases where Convex write succeeded but cache missed)
                try:
                    recent = await self.get_channel_history(channel, limit=10)
                    for msg in recent:
                        if "Claw Command Center" in msg.get("text", ""):
                            existing_ts = msg.get("ts", "")
                            if existing_ts:
                                logger.info("Found existing Command Center: %s", existing_ts)
                                SlackClient._daily_thread_cache[today] = existing_ts
                                await convex.update_task_state("daily_thread", {
                                    "lastRunAt": time.time(),
                                    "iterationCount": 1,
                                    "status": "active",
                                    "dailyThreadTs": existing_ts,
                                    "dailyThreadDate": today,
                                })
                                return existing_ts
                except Exception as e:
                    logger.warning("Slack scan for existing header failed: %s", e)

                # No existing header found — create one
                weekday = datetime.date.today().strftime("%A")
                month_day = datetime.date.today().strftime("%B %d")
                header = (
                    f"*{weekday} {month_day} -- Claw Command Center*\n"
                    f"_All agent activity consolidated. "
                    f"Swarm and Deep Sim threads linked below._"
                )
                resp = await self.post_message(channel, header)
                thread_ts = resp.get("ts") or resp.get("message", {}).get("ts")

                if thread_ts:
                    SlackClient._daily_thread_cache[today] = thread_ts
                    await convex.update_task_state("daily_thread", {
                        "lastRunAt": time.time(),
                        "iterationCount": 1,
                        "status": "active",
                        "dailyThreadTs": thread_ts,
                        "dailyThreadDate": today,
                    })

                    # Post initial state summary so the thread isn't empty
                    try:
                        cron_summary = (
                            f"\u2699\ufe0f *Active Systems*\n"
                            f"\u2022 Monitor: every 30min \u2022 Digest: hourly\n"
                            f"\u2022 Swarm: every 2h \u2022 Discussion: every 3h\n"
                            f"\u2022 Housekeeping: every 4h \u2022 Benchmarks: every 6h\n"
                            f"\u2022 Health check: every 5min\n"
                            f"\u2022 Standup: 7AM PT \u2022 Evolve: 6AM PT \u2022 Drift: Fri 10AM PT\n"
                            f"\n_Activity will be consolidated here throughout the day._"
                        )
                        await self.post_message(
                            channel, cron_summary, thread_ts=thread_ts
                        )
                    except Exception:
                        pass

                return thread_ts or ""

            except Exception as e:
                logger.error("Failed to get/create daily thread: %s", e)
                return ""
            finally:
                await convex.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_bot_message(self, msg: dict) -> bool:
        """Check if a message is from a bot (including our own bot)."""
        return bool(
            msg.get("bot_id")
            or msg.get("subtype") == "bot_message"
            or msg.get("user") == BOT_USER_ID
        )

    def is_human_message(self, msg: dict) -> bool:
        """Check if a message is from a human (not bot, not system subtype)."""
        system_subtypes = {
            "channel_join", "channel_leave", "channel_topic",
            "channel_purpose", "channel_name", "tombstone",
            "bot_message",
        }
        if msg.get("subtype") in system_subtypes:
            return False
        if msg.get("bot_id"):
            return False
        if msg.get("user") == BOT_USER_ID:
            return False
        return True

    def messages_in_window(
        self, messages: list[dict], seconds: int
    ) -> list[dict]:
        """Filter messages to those within the last N seconds."""
        cutoff = time.time() - seconds
        return [m for m in messages if float(m.get("ts", 0)) >= cutoff]


class SlackAPIError(Exception):
    """Raised when a Slack API call fails."""

    def __init__(self, method: str, error: str):
        self.method = method
        self.error = error
        super().__init__(f"Slack API {method}: {error}")
