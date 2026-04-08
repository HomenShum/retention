"""Automated Slack channel housekeeping.

Runs every 4 hours to keep #claw-communications clean and readable.
Does EVERYTHING a human reviewer would do:

1. Delete stale standalone bot posts (health alerts, agent-started noise)
2. Fix broken markdown in bot replies (**bold** -> *bold*, ## -> *heading*)
3. Remove error messages from threads (429s, SSL errors, timeouts)
4. Delete contradictory bot responses (duplicate command-word replies)
5. Condense verbose multi-part replies (… continued 2/3) into single compact messages
6. Clean up abandoned "Working on your question..." status messages
7. Summarize completed swarm/deep-sim threads into Command Center
8. Update the daily Command Center digest with current state
9. Consolidate old standalones into compact summaries

The goal: a human checking the channel sees ONE daily thread with everything
summarized, not 10+ separate bot messages competing for attention.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from .slack_client import SlackClient, CLAW_CHANNEL_ID, BOT_USER_ID
from .convex_client import ConvexClient
from .llm_judge import call_responses_api

logger = logging.getLogger(__name__)

# Bot messages older than this (in seconds) are candidates for summarization
STALE_THRESHOLD_S = 6 * 3600  # 6 hours

# Max standalone bot messages to summarize in one run
MAX_SUMMARIZE = 8

# Max chars for a single bot reply before we consider condensing
VERBOSE_THRESHOLD_CHARS = 3000

# Thread types we recognize by their header text
THREAD_MARKERS = {
    "swarm": "\U0001f300 *Agency Swarm Discussion*",
    "deep_sim": "\U0001f52c *Deep Simulation",
    "competitive": "Competitive Landscape",
}


async def _fix_markdown_in_thread(slack: SlackClient, channel: str, thread_ts: str) -> int:
    """Fix broken markdown in all bot replies in a thread.

    Converts **bold** -> *bold*, ## heading -> *heading*, ### heading -> *heading*.
    Returns count of messages fixed.
    """
    replies = await slack.get_thread(channel, thread_ts)
    fixed = 0
    for reply in replies:
        if reply.get("user") != BOT_USER_ID and not reply.get("bot_id"):
            continue
        text = reply.get("text", "")
        new_text = text
        new_text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', new_text)
        new_text = re.sub(r'^##\s+(.+)$', r'*\1*', new_text, flags=re.MULTILINE)
        new_text = re.sub(r'^###\s+(.+)$', r'*\1*', new_text, flags=re.MULTILINE)
        if new_text != text:
            await slack.update_message(channel, reply["ts"], new_text)
            fixed += 1
    return fixed


async def _clean_thread_errors(slack: SlackClient, channel: str, thread_ts: str) -> int:
    """Remove error messages and contradictory responses from a thread.

    Deletes: 429 errors, SSL errors, timeouts, "backend down", "turn limit",
    duplicate command-word responses that contradict each other.
    Returns count of messages deleted.
    """
    replies = await slack.get_thread(channel, thread_ts)
    deleted = 0
    ERROR_PATTERNS = [
        "Something went wrong:",
        "SSLV3_ALERT",
        "Backend is currently down",
        "request timed out",
        "Agent reached turn limit",
        "Rate limit",
    ]
    CONTRADICTION_PATTERNS = [
        "respond normally without a command word",
        "respond without a command word too",
    ]
    for reply in replies:
        if reply.get("ts") == thread_ts:
            continue
        if reply.get("user") != BOT_USER_ID and not reply.get("bot_id"):
            continue
        text = reply.get("text", "")
        should_delete = False
        # Check for error messages
        for pat in ERROR_PATTERNS:
            if pat in text:
                should_delete = True
                break
        # Check for contradictory command-word responses
        if not should_delete:
            for pat in CONTRADICTION_PATTERNS:
                if pat in text:
                    should_delete = True
                    break
        if should_delete:
            try:
                await slack.delete_message(channel, reply["ts"])
                deleted += 1
            except Exception:
                pass
    return deleted


async def _condense_verbose_replies(slack: SlackClient, channel: str, thread_ts: str) -> int:
    """Condense verbose multi-part bot replies in a thread.

    Handles:
    - "… continued (2/3)" multi-part messages → merge into one condensed reply
    - Single replies over VERBOSE_THRESHOLD_CHARS → LLM-condense in place
    - Abandoned "Working on your question..." status messages with no answer

    Returns count of messages condensed or deleted.
    """
    replies = await slack.get_thread(channel, thread_ts)
    cleaned = 0

    # 1. Handle abandoned status messages ("Working on your question...")
    # If the agent never produced an answer → retry the original question
    # If there IS a real answer after it → just delete the stale status
    for reply in replies:
        if reply.get("ts") == thread_ts:
            continue
        if reply.get("user") != BOT_USER_ID and not reply.get("bot_id"):
            continue
        text = reply.get("text", "")
        # Abandoned status: "Working on your question..." with step count but no real answer
        if re.search(r"Working on your question\.\.\.", text) and re.search(r"\d+ steps? · \d+s elapsed", text):
            reply_idx = replies.index(reply)
            has_followup = False
            for later in replies[reply_idx + 1:]:
                later_text = later.get("text", "")
                is_bot_later = later.get("user") == BOT_USER_ID or later.get("bot_id")
                if is_bot_later and len(later_text) > 100 and "Working on" not in later_text:
                    has_followup = True
                    break

            if has_followup:
                # Answer exists — just clean up the stale status message
                try:
                    await slack.delete_message(channel, reply["ts"])
                    cleaned += 1
                except Exception:
                    pass
            else:
                # No answer was ever produced — find the user's question and retry
                user_question = None
                for msg in reversed(replies[:reply_idx]):
                    if msg.get("user") != BOT_USER_ID and not msg.get("bot_id"):
                        user_question = msg.get("text", "").strip()
                        break
                if not user_question:
                    # Fall back to thread parent text
                    parent = replies[0] if replies else {}
                    user_question = parent.get("text", "").strip()

                if user_question:
                    # Delete the stale status message
                    try:
                        await slack.delete_message(channel, reply["ts"])
                    except Exception:
                        pass
                    # Retry via the agent
                    try:
                        from ..agents.registry.base import AgentRegistry
                        from ..agents.registry.runner import AgentRunner

                        config = AgentRegistry.get("strategy-brief")
                        runner = AgentRunner(config)
                        # Build thread context for the retry
                        context = []
                        for ctx_msg in replies[:reply_idx]:
                            if ctx_msg.get("ts") == thread_ts:
                                continue
                            ctx_user = ctx_msg.get("user", "")
                            ctx_text = ctx_msg.get("text", "").strip()
                            if not ctx_text:
                                continue
                            role = "assistant" if ctx_user == BOT_USER_ID or ctx_msg.get("bot_id") else "user"
                            context.append({"role": role, "content": ctx_text[:2000]})

                        result = await runner.run(
                            user_question,
                            context=context[-10:],
                            max_turns=1000,
                        )
                        answer = result.get("text", "")
                        if answer and answer != "(no response)":
                            turns = result.get("turns", 0)
                            duration_ms = result.get("duration_ms", 0)
                            duration_s = duration_ms // 1000
                            footer = f"\n_Retried by housekeeping · {turns} turns · {duration_s}s_"
                            await slack.post_message(
                                channel, answer + footer, thread_ts=thread_ts,
                            )
                            cleaned += 1
                            logger.info(
                                "Housekeeping retried abandoned question in thread %s: %d turns, %ds",
                                thread_ts, turns, duration_s,
                            )
                    except Exception as e:
                        logger.error("Housekeeping retry failed for thread %s: %s", thread_ts, e)

    # 2. Find multi-part "continued" messages and merge them
    # Pattern: "… continued (2/3)", "… continued (3/3)"
    continuation_groups: dict[str, list[dict]] = {}  # group by approximate timestamp window
    for reply in replies:
        if reply.get("user") != BOT_USER_ID and not reply.get("bot_id"):
            continue
        text = reply.get("text", "")
        if re.search(r"continued \(\d+/\d+\)", text):
            # Find the original message (within 5 seconds before)
            reply_ts = float(reply["ts"])
            group_key = None
            for candidate in replies:
                if candidate["ts"] == reply["ts"]:
                    continue
                cand_ts = float(candidate["ts"])
                if abs(cand_ts - reply_ts) < 10 and (candidate.get("user") == BOT_USER_ID or candidate.get("bot_id")):
                    cand_text = candidate.get("text", "")
                    if "continued" not in cand_text or re.search(r"continued \(1/\d+\)", cand_text):
                        group_key = candidate["ts"]
                        break
            if not group_key:
                group_key = reply["ts"]
            if group_key not in continuation_groups:
                continuation_groups[group_key] = []
            continuation_groups[group_key].append(reply)

    # For each group of continuations, condense into the original and delete parts
    for group_key, parts in continuation_groups.items():
        if not parts:
            continue
        # Find the original (first) message
        original = None
        for reply in replies:
            if reply["ts"] == group_key:
                original = reply
                break
        if not original:
            continue

        # Combine all text
        all_text = original.get("text", "")
        for part in sorted(parts, key=lambda p: float(p["ts"])):
            part_text = part.get("text", "")
            # Strip the "… continued (N/M)" prefix
            part_text = re.sub(r"^…\s*continued\s*\(\d+/\d+\)\s*", "", part_text).strip()
            all_text += "\n" + part_text

        # LLM-condense the combined text
        try:
            condensed = await call_responses_api(
                f"Condense this Slack bot reply to under 500 words. Keep the core answer, "
                f"key bullet points, and evidence section. Remove repetition, verbose "
                f"explanations, and filler. Use Slack mrkdwn (*bold*, _italic_). "
                f"Preserve any links.\n\n{all_text[:8000]}",
                reasoning_effort="low",
                timeout_s=30,
            )
            # Update original with condensed text
            await slack.update_message(channel, original["ts"], condensed)
            # Delete continuation parts
            for part in parts:
                try:
                    await slack.delete_message(channel, part["ts"])
                    cleaned += 1
                except Exception:
                    pass
        except Exception as e:
            logger.error("Failed to condense multi-part reply: %s", e)

    # 3. Condense single verbose bot replies (over threshold)
    for reply in replies:
        if reply.get("ts") == thread_ts:
            continue
        if reply.get("user") != BOT_USER_ID and not reply.get("bot_id"):
            continue
        text = reply.get("text", "")
        # Skip if already processed as part of a continuation group
        if reply["ts"] in continuation_groups:
            continue
        if any(reply in parts for parts in continuation_groups.values()):
            continue
        if len(text) > VERBOSE_THRESHOLD_CHARS:
            try:
                condensed = await call_responses_api(
                    f"Condense this Slack bot reply to under 500 words. Keep the core answer, "
                    f"key bullet points, and evidence/traceability section at the end. "
                    f"Remove repetition, verbose explanations, and filler. "
                    f"Use Slack mrkdwn (*bold*, _italic_). Preserve links.\n\n{text[:8000]}",
                    reasoning_effort="low",
                    timeout_s=30,
                )
                if condensed and len(condensed) < len(text):
                    await slack.update_message(channel, reply["ts"], condensed)
                    cleaned += 1
            except Exception as e:
                logger.error("Failed to condense verbose reply: %s", e)

    return cleaned


async def run_housekeeping() -> dict[str, Any]:
    """Main entry point for the housekeeping cron.

    Steps:
    1. Get today's daily Command Center thread (or create one)
    2. Delete stale standalone bot messages (health alerts, agent-started)
    3. Fix broken markdown in all thread replies
    4. Remove error messages and contradictions from threads
    5. Summarize completed swarm/deep-sim threads into Command Center
    6. Post consolidated updates into the daily thread
    7. Persist state to Convex

    Returns a summary dict of what was cleaned up.
    """
    slack = SlackClient()
    convex = ConvexClient()
    stats: dict[str, Any] = {
        "standalones_summarized": 0,
        "standalones_deleted": 0,
        "threads_digested": 0,
        "health_reports_cleaned": 0,
        "markdown_fixed": 0,
        "errors_cleaned": 0,
        "verbose_condensed": 0,
    }

    try:
        # 1. Get the daily thread
        daily_ts = await slack.get_or_create_daily_thread(CLAW_CHANNEL_ID)
        if not daily_ts:
            logger.warning("Housekeeping: no daily thread available, skipping")
            return stats

        # 2. Fetch recent channel history
        now = time.time()
        messages = await slack.get_channel_history(
            CLAW_CHANNEL_ID, limit=50, oldest=now - 86400,  # last 24h
        )

        # Track what we've already digested (stored in Convex)
        state = await convex.get_task_state("housekeeping")
        digested_threads: list[str] = []
        if state:
            digested_threads = state.get("digestedThreads", [])
            if isinstance(digested_threads, str):
                import json
                try:
                    digested_threads = json.loads(digested_threads)
                except Exception:
                    digested_threads = []

        # 3. Categorize messages
        stale_standalones: list[dict] = []
        undigested_threads: list[dict] = []
        duplicate_health_reports: list[dict] = []
        health_report_count = 0

        for msg in messages:
            ts = msg.get("ts", "")
            age = now - float(ts)
            text = msg.get("text", "")
            is_bot = (
                msg.get("user") == BOT_USER_ID
                or msg.get("bot_id")
                or msg.get("subtype") == "bot_message"
            )
            has_thread = msg.get("reply_count", 0) > 0

            # Skip the daily thread itself
            if ts == daily_ts:
                continue

            # Health reports — keep latest, mark duplicates
            if is_bot and "\U0001f3e5" in text or "Health Report" in text or "\U0001f9ea" in text or "\U0001fa7a" in text:
                health_report_count += 1
                if health_report_count > 1 and age > 3600:
                    duplicate_health_reports.append(msg)
                continue

            # Stale standalone bot messages (no thread, old enough)
            if is_bot and not has_thread and age > STALE_THRESHOLD_S:
                stale_standalones.append(msg)
                continue

            # Completed swarm/deep-sim threads (has replies, old enough)
            if is_bot and has_thread and age > STALE_THRESHOLD_S:
                if ts not in digested_threads:
                    # Check if it's a swarm or deep sim thread
                    for marker_type, marker_text in THREAD_MARKERS.items():
                        if marker_text in text:
                            undigested_threads.append({
                                **msg,
                                "_type": marker_type,
                            })
                            break

        # 4a. Delete stale standalone bot noise (health alerts, agent-started, etc.)
        NOISE_PATTERNS = [
            "HEALTH ALERT:",
            "Agent Started:",
            "has joined the channel",
            "Health Report",
        ]
        for msg in stale_standalones[:]:
            text = msg.get("text", "")
            for pat in NOISE_PATTERNS:
                if pat in text:
                    try:
                        await slack.delete_message(CLAW_CHANNEL_ID, msg["ts"])
                        stats["standalones_deleted"] += 1
                        stale_standalones.remove(msg)
                    except Exception:
                        pass
                    break

        # 4b. Delete duplicate health reports
        for msg in duplicate_health_reports:
            try:
                await slack.delete_message(CLAW_CHANNEL_ID, msg["ts"])
                stats["health_reports_cleaned"] += 1
            except Exception:
                pass

        # 4c. Fix broken markdown in all bot threads
        for msg in messages:
            if msg.get("reply_count", 0) > 0:
                is_bot = (msg.get("user") == BOT_USER_ID or msg.get("bot_id"))
                if is_bot or any(r.get("user") == BOT_USER_ID for r in messages):
                    try:
                        fixed = await _fix_markdown_in_thread(
                            slack, CLAW_CHANNEL_ID, msg["ts"]
                        )
                        stats["markdown_fixed"] += fixed
                    except Exception:
                        pass

        # 4d. Clean error messages and contradictions from threads
        for msg in messages:
            if msg.get("reply_count", 0) > 0:
                try:
                    cleaned = await _clean_thread_errors(
                        slack, CLAW_CHANNEL_ID, msg["ts"]
                    )
                    stats["errors_cleaned"] += cleaned
                except Exception:
                    pass

        # 4e. Condense verbose multi-part replies in threads
        for msg in messages:
            if msg.get("reply_count", 0) > 0:
                try:
                    condensed = await _condense_verbose_replies(
                        slack, CLAW_CHANNEL_ID, msg["ts"]
                    )
                    stats["verbose_condensed"] += condensed
                except Exception:
                    pass

        # 5. Summarize stale standalones into daily thread
        if stale_standalones:
            standalones_to_summarize = stale_standalones[:MAX_SUMMARIZE]
            standalone_texts = []
            for msg in standalones_to_summarize:
                text = msg.get("text", "")[:200]
                standalone_texts.append(f"\u2022 {text}")

            summary_bullets = "\n".join(standalone_texts)

            try:
                # Use LLM to create a compact summary
                compact = await call_responses_api(
                    f"Summarize these bot messages into 2-3 concise bullet points. "
                    f"Use Slack mrkdwn (*bold*, _italic_). Keep under 100 words total.\n\n"
                    f"{summary_bullets}",
                    reasoning_effort="low",
                    timeout_s=30,
                )
                await slack.post_message(
                    CLAW_CHANNEL_ID,
                    f"\U0001f9f9 *Housekeeping — {len(standalones_to_summarize)} older posts summarized:*\n{compact}",
                    thread_ts=daily_ts,
                )
                stats["standalones_summarized"] = len(standalones_to_summarize)
            except Exception as e:
                logger.error("Failed to summarize standalones: %s", e)

        # 5. Digest completed threads
        for thread_msg in undigested_threads[:3]:  # Max 3 per run
            ts = thread_msg.get("ts", "")
            thread_type = thread_msg.get("_type", "swarm")
            reply_count = thread_msg.get("reply_count", 0)
            text = thread_msg.get("text", "")[:100]

            try:
                # Read thread replies to get action items / synthesis
                replies = await slack.get_thread(CLAW_CHANNEL_ID, ts)

                # Find the last meaningful message (action items or synthesis)
                last_substance = ""
                for reply in reversed(replies):
                    reply_text = reply.get("text", "")
                    if any(kw in reply_text for kw in [
                        "Action Items", "Synthesis", "Consensus",
                        "Bottom Line", "Recommendation",
                    ]):
                        last_substance = reply_text[:400]
                        break

                if not last_substance:
                    # Just use LLM to summarize the thread
                    transcript = "\n".join(
                        r.get("text", "")[:150] for r in replies[:10]
                    )
                    last_substance = await call_responses_api(
                        f"Summarize this Slack thread in 1-2 sentences. "
                        f"What was decided? What are the next steps?\n\n{transcript}",
                        reasoning_effort="low",
                        timeout_s=30,
                    )

                # Build permalink
                link = f"https://retentions.slack.com/archives/{CLAW_CHANNEL_ID}/p{ts.replace('.', '')}"
                emoji = "\U0001f52c" if thread_type == "deep_sim" else "\U0001f300"
                short_text = text[:60] + ("..." if len(text) > 60 else "")

                await slack.post_message(
                    CLAW_CHANNEL_ID,
                    f"{emoji} *Completed:* {short_text} ({reply_count} replies) <{link}|View>\n"
                    f"_{last_substance[:300]}_",
                    thread_ts=daily_ts,
                )
                digested_threads.append(ts)
                stats["threads_digested"] += 1

            except Exception as e:
                logger.error("Failed to digest thread %s: %s", ts, e)

        # 6. Persist updated digested thread list
        import json
        try:
            await convex.update_task_state("housekeeping", {
                "lastRunAt": time.time(),
                "iterationCount": (state or {}).get("iterationCount", 0) + 1,
                "status": "idle",
                "digestedThreads": json.dumps(digested_threads[-50:]),  # Keep last 50
            })
        except Exception as e:
            logger.error("Failed to persist housekeeping state: %s", e)

        return stats

    except Exception as e:
        logger.exception("Housekeeping failed: %s", e)
        return stats
    finally:
        await slack.close()
        await convex.close()
