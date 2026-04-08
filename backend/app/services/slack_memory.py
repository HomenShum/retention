"""Institutional memory — extract, store, and surface decisions and knowledge.

Accumulates knowledge from conversations: decisions made, recurring topics,
resolved issues, and team preferences. Surfaces relevant context when
topics recur ("This was discussed on [date] — the decision was [X]").
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from .slack_client import SlackClient
from .convex_client import ConvexClient
from .llm_judge import call_responses_api

logger = logging.getLogger(__name__)


@dataclass
class MemoryEntry:
    """A single memory entry extracted from conversation."""
    topic: str
    summary: str
    decision: Optional[str]
    participants: list[str]
    channel: str
    timestamp: float
    source_type: str  # "decision", "question", "discussion", "incident", "preference"


# ------------------------------------------------------------------
# Decision extraction
# ------------------------------------------------------------------

async def extract_decisions(messages: list[dict], channel: str) -> list[MemoryEntry]:
    """Use LLM to extract decisions and notable knowledge from conversation messages."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or not messages:
        return []

    msg_text = "\n".join(
        f"[{m.get('user', '?')[:8]}] ({m.get('ts', '')}): {m.get('text', '')[:200]}"
        for m in messages[:20]
    )

    prompt = f"""Analyze these Slack messages and extract any decisions, conclusions, or notable knowledge.

MESSAGES:
{msg_text}

For each finding, return a JSON array of objects:
[{{
  "topic": "short topic name",
  "summary": "one sentence summary",
  "decision": "the specific decision made (null if no decision)",
  "participants": ["user1", "user2"],
  "source_type": "decision|question|discussion|incident|preference"
}}]

Rules:
- Only extract clear decisions or notable information worth remembering
- Skip casual chat, greetings, and acknowledgments
- Include recurring topics (things discussed more than once)
- If no notable content, return an empty array []

Return ONLY the JSON array."""

    try:
        content = await call_responses_api(prompt, task="memory_extraction", timeout_s=20)

        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        entries = json.loads(content)
        return [
            MemoryEntry(
                topic=e.get("topic", ""),
                summary=e.get("summary", ""),
                decision=e.get("decision"),
                participants=e.get("participants", []),
                channel=channel,
                timestamp=time.time(),
                source_type=e.get("source_type", "discussion"),
            )
            for e in entries
            if e.get("topic")
        ]

    except Exception as e:
        logger.error("Decision extraction failed: %s", e)
        return []


# ------------------------------------------------------------------
# Memory storage
# ------------------------------------------------------------------

async def store_memories(entries: list[MemoryEntry], convex: ConvexClient) -> int:
    """Store extracted memories in Convex. Returns count of stored entries."""
    stored = 0
    for entry in entries:
        try:
            await convex.store_memory({
                "topic": entry.topic,
                "summary": entry.summary,
                "decision": entry.decision or "",
                "participants": entry.participants,
                "channel": entry.channel,
                "timestamp": entry.timestamp,
                "sourceType": entry.source_type,
            })
            stored += 1
        except Exception as e:
            logger.warning("Failed to store memory entry '%s': %s", entry.topic, e)
    return stored


# ------------------------------------------------------------------
# Memory surfacing
# ------------------------------------------------------------------

async def search_memory(topic: str, convex: ConvexClient, limit: int = 5) -> list[dict]:
    """Search institutional memory by topic."""
    try:
        return await convex.search_memory(topic, limit=limit)
    except Exception as e:
        logger.warning("Memory search failed for '%s': %s", topic, e)
        return []


async def surface_relevant(
    current_messages: list[dict],
    convex: ConvexClient,
) -> list[str]:
    """Check if current discussion topics have prior context in memory.

    Returns human-readable strings like:
    'This topic was discussed on [date] — the decision was [X]'
    """
    if not current_messages:
        return []

    topics = await _extract_topics(current_messages)
    if not topics:
        return []

    surfaces: list[str] = []
    for topic in topics[:3]:
        memories = await search_memory(topic, convex, limit=2)
        for mem in memories:
            ts = mem.get("timestamp", 0)
            date_str = datetime.fromtimestamp(ts).strftime("%b %d") if ts else "recently"
            decision = mem.get("decision", "")
            summary = mem.get("summary", "")

            if decision:
                surfaces.append(
                    f"Re: _{topic}_ — discussed on {date_str}. Decision: {decision}"
                )
            elif summary:
                surfaces.append(
                    f"Re: _{topic}_ — previously discussed on {date_str}: {summary}"
                )

    return surfaces


async def _extract_topics(messages: list[dict]) -> list[str]:
    """Use LLM to extract the main topics being discussed."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return []

    msg_text = "\n".join(
        m.get("text", "")[:100] for m in messages[:10]
    )

    prompt = f"""Extract the 1-3 main topics being discussed in these Slack messages.
Return a JSON array of short topic strings (2-4 words each).

MESSAGES:
{msg_text}

Return ONLY the JSON array, e.g.: ["deployment pipeline", "cost optimization"]"""

    try:
        content = await call_responses_api(prompt, task="topic_extraction", timeout_s=10)
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        return json.loads(content)
    except Exception as e:
        logger.error("Topic extraction failed: %s", e)
        return []


# ------------------------------------------------------------------
# FAQ detection
# ------------------------------------------------------------------

async def check_faq(
    topic: str,
    convex: ConvexClient,
    threshold: int = 3,
) -> Optional[str]:
    """Check if a topic has been asked about frequently.

    Returns a proactive message if the topic has appeared >= threshold times,
    or None if it's not a FAQ.
    """
    memories = await search_memory(topic, convex, limit=10)
    question_memories = [m for m in memories if m.get("sourceType") == "question"]

    if len(question_memories) >= threshold:
        latest = question_memories[0]
        return (
            f"This question about _{topic}_ has come up {len(question_memories)} times. "
            f"Previous answer: {latest.get('summary', 'see thread history')}"
        )
    return None
