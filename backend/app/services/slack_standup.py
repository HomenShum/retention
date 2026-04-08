"""Async standup synthesis — daily aggregation of activity.

Gathers commits + Slack messages + brief changes, synthesizes into
a "Calculus Made Easy" standup summary, and posts to Slack.
Runs daily at 7AM PT (2PM UTC).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from .slack_client import SlackClient, CLAW_CHANNEL_ID
from .convex_client import ConvexClient
from .llm_judge import call_responses_api

logger = logging.getLogger(__name__)


@dataclass
class StandupResult:
    posted: bool
    summary: str
    commits_count: int
    messages_count: int
    iteration: int


async def _fetch_recent_commits(limit: int = 20) -> list[dict]:
    try:
        from ..api.mcp_server import _dispatch_codebase
        result = await _dispatch_codebase("ta.codebase.recent_commits", {"limit": limit})
        if isinstance(result, str):
            return json.loads(result)
        elif isinstance(result, list):
            return result
        return []
    except Exception as e:
        logger.warning("Failed to fetch commits for standup: %s", e)
        return []


async def _fetch_brief_state() -> str:
    try:
        from ..api.mcp_server import _dispatch_investor_brief
        result = await _dispatch_investor_brief("ta.investor_brief.get_state", {})
        if isinstance(result, str):
            state = json.loads(result)
        elif isinstance(result, dict):
            state = result
        else:
            return ""
        sections = state.get("sections", [])
        summaries = []
        for s in sections[:5]:
            content = s.get("content", "")[:100]
            if content:
                summaries.append(f"- {s.get('id', '?')}: {content}")
        return "\n".join(summaries) if summaries else ""
    except Exception as e:
        logger.warning("Failed to fetch brief state: %s", e)
        return ""


async def _synthesize_standup(commits: list[dict], messages: list[dict], brief_state: str, slack: SlackClient) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return _fallback_standup(commits, messages)

    commit_text = "\n".join(
        f"- {c.get('sha', '')[:7]}: {c.get('message', '').split(chr(10))[0][:80]}"
        for c in commits[:15]
    )

    human_msgs = [m for m in messages if slack.is_human_message(m)]
    msg_text = "\n".join(
        f"- [{m.get('user', '?')[:8]}]: {m.get('text', '')[:100]}"
        for m in human_msgs[:10]
    )

    prompt = f"""Write a daily standup summary for the team in "Calculus Made Easy" style.

RECENT COMMITS ({len(commits)}):
{commit_text or "(no commits)"}

SLACK ACTIVITY ({len(human_msgs)} human messages):
{msg_text or "(no messages)"}

INVESTOR BRIEF STATE:
{brief_state or "(no brief data)"}

RULES:
1. Start with a one-line headline analogy: what did the team accomplish in plain English?
2. Group by: *What shipped*, *What's in progress*, *What needs attention*
3. Under 200 words
4. Slack mrkdwn: *bold*, _italic_, `code`. No ## headings, no markdown tables.
5. If there are blockers or unanswered questions, highlight them
6. End with: "Focus for today: [inferred from activity]"

Output ONLY the standup text."""

    try:
        return await call_responses_api(prompt, task="standup_synthesis", timeout_s=20)
    except Exception as e:
        logger.error("Standup synthesis failed: %s", e)
        return _fallback_standup(commits, messages)


def _fallback_standup(commits: list[dict], messages: list[dict]) -> str:
    return (
        f"*Daily Standup*\n"
        f"\u2022 {len(commits)} commits in the last 24h\n"
        f"\u2022 {len(messages)} Slack messages\n"
        f"_LLM synthesis unavailable \u2014 see raw activity above_"
    )


async def run_standup() -> StandupResult:
    slack = SlackClient()
    convex = ConvexClient()
    iteration = 1

    try:
        state = await convex.get_task_state("standup")
        if state:
            iteration = state.get("iterationCount", 0) + 1

        commits = await _fetch_recent_commits(limit=20)
        cutoff = time.time() - 86400
        messages = await slack.get_channel_history(CLAW_CHANNEL_ID, limit=100, oldest=cutoff)
        human_messages = [m for m in messages if slack.is_human_message(m)]

        brief_state = await _fetch_brief_state()

        if not commits and not human_messages:
            logger.info("No activity \u2014 skipping standup")
            await convex.update_task_state("standup", {
                "lastRunAt": time.time(), "iterationCount": iteration, "status": "idle",
            })
            await slack.close()
            await convex.close()
            return StandupResult(posted=False, summary="No activity", commits_count=0, messages_count=0, iteration=iteration)

        standup_text = await _synthesize_standup(commits, messages, brief_state, slack)

        posted = False
        try:
            daily_ts = await slack.get_or_create_daily_thread(CLAW_CHANNEL_ID)
            if daily_ts:
                await slack.post_message(CLAW_CHANNEL_ID, standup_text, thread_ts=daily_ts)
            else:
                await slack.post_message(CLAW_CHANNEL_ID, standup_text)
            posted = True
        except Exception as e:
            logger.error("Failed to post standup: %s", e)

        await convex.update_task_state("standup", {
            "lastRunAt": time.time(), "iterationCount": iteration, "status": "idle",
        })

        await slack.close()
        await convex.close()

        return StandupResult(posted=posted, summary=standup_text[:200], commits_count=len(commits), messages_count=len(human_messages), iteration=iteration)

    except Exception as e:
        logger.error("Standup failed: %s", e)
        try:
            await convex.update_task_state("standup", {
                "lastRunAt": time.time(), "iterationCount": iteration,
                "status": "error", "lastError": str(e)[:200],
            })
            await slack.close()
            await convex.close()
        except Exception:
            pass
        return StandupResult(posted=False, summary=f"Error: {e}", commits_count=0, messages_count=0, iteration=iteration)
