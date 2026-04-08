"""Slack digest — periodic summary of channel activity.

Runs hourly. Uses a boolean rubric to decide whether the current activity
level warrants posting a digest. Activity metrics + gates determine
whether to post, what to include, and at what detail level.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, asdict
from typing import Any, Optional

from .slack_client import SlackClient, CLAW_CHANNEL_ID, BOT_USER_ID
from .convex_client import ConvexClient
from .llm_judge import evaluate_gates_batch, GateResult, RubricResult, call_responses_api
from .usage_telemetry import upsert_daily_usage_message

logger = logging.getLogger(__name__)


@dataclass
class ActivityMetrics:
    """Computed activity metrics for the digest window."""
    total_messages: int
    human_messages: int
    bot_messages: int
    unique_humans: int
    threads_active: int
    questions_unanswered: int
    decisions_made: int
    window_hours: float


@dataclass
class DigestResult:
    """Result of a single digest run."""
    posted: bool
    activity_level: str
    decision: str
    decision_chain: str
    summary: str
    iteration: int


# ------------------------------------------------------------------
# Activity analysis
# ------------------------------------------------------------------

def _compute_activity(messages: list[dict], slack: SlackClient, window_hours: float) -> ActivityMetrics:
    """Compute activity metrics from messages."""
    human_msgs = [m for m in messages if slack.is_human_message(m)]
    bot_msgs = [m for m in messages if slack.is_bot_message(m)]

    unique_humans = len(set(m.get("user", "") for m in human_msgs if m.get("user")))
    threads = set(m.get("thread_ts", "") for m in messages if m.get("thread_ts"))

    questions = [m for m in human_msgs if m.get("text", "").rstrip().endswith("?")]
    threads_with_replies = set(m.get("thread_ts") for m in messages if m.get("thread_ts"))
    unanswered = sum(1 for q in questions if q.get("ts") not in threads_with_replies)

    decision_patterns = [r"let's go with", r"decided to", r"we'll use", r"approved", r"moving forward with"]
    decisions = sum(
        1 for m in human_msgs
        if any(re.search(p, m.get("text", ""), re.IGNORECASE) for p in decision_patterns)
    )

    return ActivityMetrics(
        total_messages=len(messages),
        human_messages=len(human_msgs),
        bot_messages=len(bot_msgs),
        unique_humans=unique_humans,
        threads_active=len(threads),
        questions_unanswered=unanswered,
        decisions_made=decisions,
        window_hours=window_hours,
    )


def _classify_activity(metrics: ActivityMetrics) -> str:
    """Classify activity level."""
    if metrics.human_messages == 0:
        return "none"
    elif metrics.human_messages >= 15 or metrics.unique_humans >= 4:
        return "high"
    elif metrics.human_messages >= 5 or metrics.unique_humans >= 2:
        return "medium"
    else:
        return "low"


# ------------------------------------------------------------------
# Digest rubric gates
# ------------------------------------------------------------------

REQUIRED_GATES = [
    {"name": "sufficient_activity", "question": "Is there enough activity to justify a digest? At least 5 human messages OR at least 2 active threads OR at least 1 unanswered question."},
    {"name": "new_information", "question": "Is there new information that wasn't in the previous digest? The activity includes topics, decisions, or questions not previously summarized."},
    {"name": "digest_would_help", "question": "Would someone who missed the last few hours benefit from a summary? There are actionable items, decisions, or important context to catch up on."},
    {"name": "timing_appropriate", "question": "Is the timing appropriate for a digest? Not too close to the previous one (at least 2 hours gap), and during a natural break in conversation."},
    {"name": "audience_present", "question": "Are the relevant audience members likely available? It's during working hours for the team."},
]

MODIFIERS = [
    {"name": "high_activity_burst", "question": "Was there a sudden burst of activity (5+ messages in 15 minutes)?"},
    {"name": "decision_made", "question": "Was at least one decision made that should be documented?"},
    {"name": "action_items_pending", "question": "Are there pending action items or unanswered questions that need attention?"},
]

DISQUALIFIERS = [
    {"name": "digest_too_recent", "question": "Was a digest already posted within the last 2 hours?"},
    {"name": "all_bot_traffic", "question": "Is the activity entirely bot messages with no human engagement?"},
    {"name": "sensitive_content", "question": "Does the conversation contain primarily sensitive, personal, or confidential content?"},
]


async def _compose_digest(
    metrics: ActivityMetrics,
    messages: list[dict],
    slack: SlackClient,
    modifiers: list[GateResult],
) -> str:
    """Compose a digest message using LLM with Calculus Made Easy style."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return _fallback_digest(metrics)

    human_msgs = [m for m in messages if slack.is_human_message(m)]
    msg_text = "\n".join(
        f"- [{m.get('user', '?')[:8]}]: {m.get('text', '')[:150]}"
        for m in human_msgs[:20]
    )

    modifier_notes = "\n".join(
        f"- {g.name}: {'YES' if g.value else 'NO'} — {g.reason}"
        for g in modifiers
    )

    prompt = f"""Write a Slack channel digest summarizing the last {metrics.window_hours:.0f} hours of activity.

ACTIVITY METRICS:
- {metrics.human_messages} human messages from {metrics.unique_humans} people
- {metrics.threads_active} active threads
- {metrics.questions_unanswered} unanswered questions
- {metrics.decisions_made} decisions made

MESSAGES:
{msg_text}

MODIFIERS:
{modifier_notes}

RULES:
1. "Calculus Made Easy" style: lead with the headline insight, explain what matters, details last
2. Under 250 words
3. Slack mrkdwn: *bold*, _italic_, `code`. No ## headings, no **bold**, no markdown tables.
4. Group by: decisions made, questions pending, key discussions
5. Tag unanswered questions with _(needs attention)_
6. End with: "Next digest in ~2h unless activity spikes"

Output ONLY the message text."""

    try:
        return await call_responses_api(prompt, task="digest_composition", timeout_s=20)
    except Exception as e:
        logger.error("Digest composition failed: %s", e)
        return _fallback_digest(metrics)


def _fallback_digest(metrics: ActivityMetrics) -> str:
    return (
        f"*Channel Digest* ({metrics.window_hours:.0f}h window)\n"
        f"• {metrics.human_messages} messages from {metrics.unique_humans} people\n"
        f"• {metrics.threads_active} active threads\n"
        f"• {metrics.questions_unanswered} unanswered questions\n"
        f"• {metrics.decisions_made} decisions recorded"
    )


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------

async def run_digest() -> DigestResult:
    """Main digest: compute activity -> evaluate rubric -> compose + post."""
    slack = SlackClient()
    convex = ConvexClient()
    iteration = 1
    window_hours = 2.0

    try:
        state = await convex.get_task_state("digest")
        if state:
            iteration = state.get("iterationCount", 0) + 1

        cutoff = time.time() - (window_hours * 3600)
        messages = await slack.get_channel_history(CLAW_CHANNEL_ID, limit=100, oldest=cutoff)

        metrics = _compute_activity(messages, slack, window_hours)
        activity_level = _classify_activity(metrics)

        if activity_level == "none":
            logger.info("No activity — skipping digest")
            await convex.update_task_state("digest", {
                "lastRunAt": time.time(), "iterationCount": iteration, "status": "idle",
            })
            await slack.close()
            await convex.close()
            return DigestResult(
                posted=False, activity_level="none", decision="SKIP",
                decision_chain="no_activity → SKIP", summary="No activity",
                iteration=iteration,
            )

        context = f"""ACTIVITY METRICS:
- Total messages: {metrics.total_messages}
- Human messages: {metrics.human_messages}
- Bot messages: {metrics.bot_messages}
- Unique humans: {metrics.unique_humans}
- Active threads: {metrics.threads_active}
- Unanswered questions: {metrics.questions_unanswered}
- Decisions made: {metrics.decisions_made}
- Window: {metrics.window_hours}h
- Activity level: {activity_level}"""

        required_results = await evaluate_gates_batch(REQUIRED_GATES, context)
        modifier_results = await evaluate_gates_batch(MODIFIERS, context)
        disqualifier_results = await evaluate_gates_batch(DISQUALIFIERS, context)

        rubric = RubricResult(
            required_gates=required_results,
            modifiers=modifier_results,
            disqualifiers=disqualifier_results,
        )

        decision_log = {
            "timestamp": time.time(),
            "iteration": iteration,
            "activityMetrics": asdict(metrics),
            "gates": rubric.to_dict().get("required_gates", {}),
            "modifiers": rubric.to_dict().get("modifiers", {}),
            "disqualifiers": rubric.to_dict().get("disqualifiers", {}),
            "decision": rubric.decision,
            "activityLevel": activity_level,
            "posted": False,
        }

        posted = False
        if rubric.should_post:
            digest_text = await _compose_digest(metrics, messages, slack, modifier_results)
            try:
                daily_ts = await slack.get_or_create_daily_thread(CLAW_CHANNEL_ID)
                if daily_ts:
                    await slack.post_message(CLAW_CHANNEL_ID, digest_text, thread_ts=daily_ts)
                else:
                    await slack.post_message(CLAW_CHANNEL_ID, digest_text)
                posted = True
                decision_log["posted"] = True
                logger.info("Posted digest (activity=%s)", activity_level)
            except Exception as e:
                logger.error("Failed to post digest: %s", e)

        try:
            daily_ts = await slack.get_or_create_daily_thread(CLAW_CHANNEL_ID)
            await upsert_daily_usage_message(
                slack=slack,
                convex=convex,
                channel=CLAW_CHANNEL_ID,
                thread_ts=daily_ts,
                days=1,
            )
        except Exception as e:
            logger.warning("Failed to upsert usage summary: %s", e)

        try:
            await convex.log_digest_decision(decision_log)
        except Exception as e:
            logger.warning("Failed to log digest to Convex: %s", e)
            ConvexClient.log_local_fallback("logs/slack-digest-decisions.jsonl", decision_log)

        await convex.update_task_state("digest", {
            "lastRunAt": time.time(), "iterationCount": iteration, "status": "idle",
        })

        await slack.close()
        await convex.close()

        return DigestResult(
            posted=posted, activity_level=activity_level,
            decision=rubric.decision, decision_chain=rubric.decision_chain,
            summary=rubric.blocking_gate or "All gates passed",
            iteration=iteration,
        )

    except Exception as e:
        logger.error("Digest run failed: %s", e)
        try:
            await convex.update_task_state("digest", {
                "lastRunAt": time.time(), "iterationCount": iteration,
                "status": "error", "lastError": str(e)[:200],
            })
            await slack.close()
            await convex.close()
        except Exception:
            pass
        return DigestResult(
            posted=False, activity_level="error", decision="ERROR",
            decision_chain=f"error: {str(e)[:100]}", summary=str(e)[:200],
            iteration=iteration,
        )
