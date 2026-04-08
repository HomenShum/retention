"""Core Slack monitor — scan channels, detect opportunities, evaluate rubric, post.

Translates the boolean rubric into Python. Runs every 30 minutes.
Uses 8 opportunity types (A-H) with keyword detection + LLM gates.
All decisions are logged to Convex for the evolution loop.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Optional

from .slack_client import SlackClient, CLAW_CHANNEL_ID, BOT_USER_ID
from .convex_client import ConvexClient
from .llm_judge import evaluate_gates_batch, compose_response, GateResult, RubricResult
from .agency_roles import get_role_for_opportunity, get_system_prompt
from .slack_memory import extract_decisions, store_memories, surface_relevant

# ------------------------------------------------------------------
# Command-word gating
# ------------------------------------------------------------------
# If a user has requested "only respond when I say X", the monitor
# respects that. This is stored in Convex task state as
# {"commandWord": "claw", "commandWordSetBy": "U12345"}.
# Type B (Meta-Feedback) opportunities bypass the command-word gate
# so the bot can still process feedback about itself.
# ------------------------------------------------------------------

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Opportunity types A-H
# ------------------------------------------------------------------

class OpportunityType(str, Enum):
    DIRECT_QUESTION = "A"       # Someone asks a question the bot can answer
    META_FEEDBACK = "B"         # Feedback about the bot itself (HIGHEST PRIORITY)
    INCIDENT = "C"              # Something is broken / error reports
    BLOCKER = "D"               # Someone is blocked and needs help
    DECISION_SUPPORT = "E"      # A decision is being discussed, bot has relevant data
    KNOWLEDGE_SURFACING = "F"   # Bot knows something relevant not yet mentioned
    CROSS_THREAD = "G"          # Two threads discuss the same topic without knowing
    TIMELINE_AWARENESS = "H"    # Deadline / timeline context the team may be missing


# Command-word bypass: Type B (meta-feedback) always gets through
COMMAND_WORD_BYPASS_TYPES = {OpportunityType.META_FEEDBACK}

# Patterns for detecting command-word setup/clear requests
COMMAND_WORD_SET_PATTERNS = [
    "only respond if", "only respond when", "only reply if",
    "only reply when", "don't respond unless",
    "set trigger word", "set command word",
]
COMMAND_WORD_CLEAR_PATTERNS = [
    "clear trigger word", "clear command word",
    "remove trigger word", "remove command word",
    "respond to everything", "no trigger word",
    "disable trigger word", "disable command word",
]


# Keyword patterns for initial opportunity detection
OPPORTUNITY_PATTERNS: dict[OpportunityType, list[str]] = {
    OpportunityType.DIRECT_QUESTION: [
        r"\?$", r"anyone know", r"how do (we|i|you)", r"what('s| is) the",
        r"can someone", r"does anyone", r"help with",
    ],
    OpportunityType.META_FEEDBACK: [
        r"bot", r"claw", r"agent", r"automat", r"self.?post",
        r"too (much|many|noisy)", r"not enough", r"annoying",
        r"helpful", r"useful", r"love (the|this)", r"hate (the|this)",
    ],
    OpportunityType.INCIDENT: [
        r"(is )?broken", r"crash", r"error", r"bug", r"down",
        r"not working", r"failed", r"500", r"outage", r"incident",
    ],
    OpportunityType.BLOCKER: [
        r"block(ed|er|ing)", r"stuck", r"can't (get|make|figure)",
        r"help me", r"need help", r"struggling", r"plz fix", r"please fix",
    ],
    OpportunityType.DECISION_SUPPORT: [
        r"should we", r"what if we", r"trade.?off", r"option(s)?",
        r"pros? (and|&) cons?", r"let's (decide|go with)",
        r"vote", r"prefer", r"recommend",
    ],
    OpportunityType.KNOWLEDGE_SURFACING: [
        r"where (is|are|can i find)", r"documentation",
        r"(any|the) spec", r"who (knows|owns|manages)",
    ],
    OpportunityType.CROSS_THREAD: [],  # Detected via memory search, not keywords
    OpportunityType.TIMELINE_AWARENESS: [
        r"deadline", r"due (date|by)", r"launch", r"ship",
        r"sprint", r"milestone", r"timeline", r"when (do|is|are) we",
    ],
}


@dataclass
class Opportunity:
    """A detected opportunity in a message or thread."""
    type: OpportunityType
    channel: str
    message_ts: str
    message_preview: str
    thread_ts: Optional[str] = None
    context: str = ""
    keyword_match: str = ""


@dataclass
class MonitorResult:
    """Result of a single monitor run."""
    posted: bool
    opportunity_type: str
    decision: str
    decision_chain: str
    summary: str
    iteration: int
    candidates_found: int


# ------------------------------------------------------------------
# Opportunity detection
# ------------------------------------------------------------------

def _detect_opportunities(messages: list[dict], slack: SlackClient) -> list[Opportunity]:
    """Scan messages for opportunity patterns. Returns candidates sorted by priority."""
    candidates: list[Opportunity] = []

    for msg in messages:
        if slack.is_bot_message(msg):
            continue
        text = msg.get("text", "")
        if not text or len(text) < 5:
            continue

        ts = msg.get("ts", "")
        thread_ts = msg.get("thread_ts")
        preview = text[:150]

        for opp_type, patterns in OPPORTUNITY_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    candidates.append(Opportunity(
                        type=opp_type,
                        channel=CLAW_CHANNEL_ID,
                        message_ts=ts,
                        message_preview=preview,
                        thread_ts=thread_ts,
                        context=text[:500],
                        keyword_match=pattern,
                    ))
                    break  # One match per type per message

    # Sort by priority: B (meta) first, then C/D, then A, then E-H
    priority_order = {
        OpportunityType.META_FEEDBACK: 0,
        OpportunityType.INCIDENT: 1,
        OpportunityType.BLOCKER: 2,
        OpportunityType.DIRECT_QUESTION: 3,
        OpportunityType.DECISION_SUPPORT: 4,
        OpportunityType.KNOWLEDGE_SURFACING: 5,
        OpportunityType.CROSS_THREAD: 6,
        OpportunityType.TIMELINE_AWARENESS: 7,
    }
    candidates.sort(key=lambda c: priority_order.get(c.type, 99))
    return candidates


# ------------------------------------------------------------------
# Rubric evaluation
# ------------------------------------------------------------------

REQUIRED_GATES = [
    {"name": "opportunity_identified", "question": "Is there a clear opportunity where the bot can add value? The message contains a question, issue, decision point, or knowledge gap that the bot has relevant information about."},
    {"name": "agent_has_unique_value", "question": "Does the agent have information or perspective that is NOT already in the thread? It must add something new — not restate what's been said."},
    {"name": "actionable_outcome", "question": "Would the response lead to a concrete action, decision, or understanding? Not just acknowledgment or sympathy."},
    {"name": "right_audience_right_time", "question": "Is this the right moment to contribute? The conversation is still active (within 2 hours) and the people who need the information are likely present."},
    {"name": "information_would_be_lost", "question": "If the agent stays silent, would valuable information be lost or a suboptimal decision be made? The cost of NOT responding outweighs the cost of responding."},
]

DISQUALIFIERS = [
    {"name": "already_resolved", "question": "Has the question or issue already been resolved in the thread? Someone already provided the answer or fix."},
    {"name": "social_only", "question": "Is this message purely social with no actionable content? Examples: greetings, thanks, celebrations, casual chat. Note: a casual message WRAPPING an actionable request (e.g., 'hey can someone plz fix the bot') is NOT social-only."},
    {"name": "bot_already_replied", "question": "Has the bot already posted a substantive reply in this thread or to this message within the last 2 hours?"},
    {"name": "sensitive_topic", "question": "Does this involve personal, HR, compensation, or other sensitive topics where bot participation would be inappropriate?"},
    {"name": "rapid_fire_limit", "question": "Has the bot already posted 3+ messages in the last hour across all channels? (To prevent flooding)"},
    {"name": "command_word_required", "question": "Has a user explicitly requested that the bot only respond when addressed with a specific command word (e.g. 'Claw')? If so, does this message start with or contain that command word? If a command word is required but missing, this is a disqualifier."},
]


async def _has_recent_bot_reply(
    slack: SlackClient,
    opportunity: Opportunity,
    lookback_seconds: int = 7200,
) -> bool:
    """Hard guard: skip if the bot already replied recently in this thread."""
    thread_ts = opportunity.thread_ts or opportunity.message_ts
    if not thread_ts:
        return False

    try:
        replies = await slack.get_thread(opportunity.channel, thread_ts)
    except Exception:
        return False

    cutoff = time.time() - lookback_seconds
    source_ts = float(opportunity.message_ts or 0)
    for msg in replies:
        msg_ts = float(msg.get("ts", 0) or 0)
        if msg_ts <= source_ts or msg_ts < cutoff:
            continue
        if slack.is_bot_message(msg):
            return True
    return False


# ------------------------------------------------------------------
# Atomic monitor post-claim
# ------------------------------------------------------------------
# Prevents two simultaneous monitor invocations from both passing the
# _has_recent_bot_reply guard and both posting to the same thread.
# Uses O_CREAT|O_EXCL (kernel-atomic) — same primitive as claim_stream_request.
# ------------------------------------------------------------------

_MONITOR_CLAIM_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".claude", "monitor-claims",
)
_MONITOR_CLAIM_TTL_S = 10 * 60  # 10 min — generous for slow LLM compose


def _monitor_claim_key(opportunity: "Opportunity") -> str:
    """Stable key for an opportunity so concurrent monitors share the same lock."""
    raw = f"{opportunity.channel}:{opportunity.thread_ts or opportunity.message_ts}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _acquire_monitor_claim(opportunity: "Opportunity") -> bool:
    """Atomically claim the right to post for this opportunity.

    Returns True if this caller owns the slot, False if another process beat it.
    Stale claims (older than TTL) are reclaimed so crashes don't permanently block.
    """
    os.makedirs(_MONITOR_CLAIM_DIR, exist_ok=True)
    key = _monitor_claim_key(opportunity)
    lock_path = os.path.join(_MONITOR_CLAIM_DIR, f"{key}.lock")

    now = time.time()
    # Reclaim stale lock from a crashed monitor run
    if os.path.exists(lock_path):
        try:
            age = now - os.path.getmtime(lock_path)
        except OSError:
            age = 0
        if age <= _MONITOR_CLAIM_TTL_S:
            return False
        try:
            os.remove(lock_path)
        except OSError:
            return False  # another process got it first

    try:
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "w") as f:
            json.dump({"claimed_at": now, "key": key}, f)
        return True
    except FileExistsError:
        return False


def _release_monitor_claim(opportunity: "Opportunity") -> None:
    """Release the post-claim after posting (or on error) so the slot is reusable."""
    key = _monitor_claim_key(opportunity)
    lock_path = os.path.join(_MONITOR_CLAIM_DIR, f"{key}.lock")
    try:
        os.remove(lock_path)
    except OSError:
        pass


async def _evaluate_candidate(
    opportunity: Opportunity,
    thread_context: str,
    bot_recent_count: int,
) -> RubricResult:
    """Evaluate a single opportunity against the full boolean rubric."""
    # Check for command word in task state
    command_word_info = ""
    try:
        _convex = ConvexClient()
        _state = await _convex.get_task_state("monitor")
        if _state and _state.get("commandWord"):
            cw = _state["commandWord"]
            has_cw = cw.lower() in opportunity.context.lower()
            command_word_info = f"\nCOMMAND WORD GATE: Users have requested the bot only respond when '{cw}' is used. Message {'CONTAINS' if has_cw else 'DOES NOT CONTAIN'} the command word."
        await _convex.close()
    except Exception:
        pass

    context = f"""OPPORTUNITY TYPE: {opportunity.type.name} ({opportunity.type.value})
MESSAGE: {opportunity.message_preview}
FULL CONTEXT: {opportunity.context}
THREAD CONTEXT: {thread_context}
BOT POSTS LAST HOUR: {bot_recent_count}
KEYWORD MATCH: {opportunity.keyword_match}{command_word_info}"""

    required_results = await evaluate_gates_batch(REQUIRED_GATES, context)
    disqualifier_results = await evaluate_gates_batch(DISQUALIFIERS, context)

    return RubricResult(
        required_gates=required_results,
        disqualifiers=disqualifier_results,
    )


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------

async def run_monitor() -> MonitorResult:
    """Main monitor loop: read Slack -> detect -> evaluate -> post -> log."""
    slack = SlackClient()
    convex = ConvexClient()
    iteration = 1

    try:
        state = await convex.get_task_state("monitor")
        if state:
            iteration = state.get("iterationCount", 0) + 1

        # Check for command-word gating
        command_word = None
        if state:
            command_word = state.get("commandWord")

        # Fetch recent messages (last 30 minutes)
        cutoff = time.time() - 1800
        messages = await slack.get_channel_history(CLAW_CHANNEL_ID, limit=50, oldest=cutoff)
        human_messages = [m for m in messages if slack.is_human_message(m)]

        # Detect command-word setup/clear requests in recent messages
        for msg in human_messages:
            text = msg.get("text", "").lower()

            # Check CLEAR first (takes priority over SET)
            if any(phrase in text for phrase in COMMAND_WORD_CLEAR_PATTERNS):
                if command_word:
                    logger.info("Command word CLEARED by user %s", msg.get("user", ""))
                    command_word = None
                    await convex.update_task_state("monitor", {
                        "lastRunAt": time.time(), "iterationCount": iteration,
                        "status": "idle", "commandWord": None,
                        "commandWordSetBy": None,
                    })
                continue

            # Check SET patterns
            if any(phrase in text for phrase in COMMAND_WORD_SET_PATTERNS):
                # Extract the command word — look for quoted word or "say X" pattern
                quoted = re.findall(r'["\'](\w+)["\']', text)
                new_cw = None
                if quoted:
                    new_cw = quoted[-1].lower()
                else:
                    say_match = re.search(r'(?:say|use|write)\s+["\']?(\w+)["\']?', text)
                    if say_match:
                        new_cw = say_match.group(1).lower()
                # Filter out false positives for common words
                if new_cw and new_cw not in {"the", "a", "an", "it", "is", "to", "and", "or", "in"}:
                    command_word = new_cw
                    logger.info("Command word SET: '%s' by user %s", command_word, msg.get("user", ""))
                    await convex.update_task_state("monitor", {
                        "lastRunAt": time.time(), "iterationCount": iteration,
                        "status": "idle", "commandWord": command_word,
                        "commandWordSetBy": msg.get("user", ""),
                    })

        if not human_messages:
            logger.info("No human messages in window — skipping monitor")
            await convex.update_task_state("monitor", {
                "lastRunAt": time.time(), "iterationCount": iteration, "status": "idle",
            })
            await slack.close()
            await convex.close()
            return MonitorResult(
                posted=False, opportunity_type="none", decision="SKIP",
                decision_chain="no_messages → SKIP", summary="No human messages",
                iteration=iteration, candidates_found=0,
            )

        # Detect opportunities
        candidates = _detect_opportunities(human_messages, slack)

        # Apply command-word gate: filter out candidates that don't include
        # the command word, UNLESS they're Type B (meta-feedback about the bot)
        if command_word and candidates:
            cw_pattern = re.compile(r'\b' + re.escape(command_word) + r'\b', re.IGNORECASE)
            pre_filter_count = len(candidates)
            candidates = [
                c for c in candidates
                if c.type in COMMAND_WORD_BYPASS_TYPES
                or cw_pattern.search(c.context)
            ]
            filtered = pre_filter_count - len(candidates)
            if filtered:
                logger.info(
                    "Command-word gate filtered %d/%d candidates (word='%s')",
                    filtered, pre_filter_count, command_word,
                )

        if not candidates:
            logger.info("No opportunities detected in %d messages", len(human_messages))
            await convex.update_task_state("monitor", {
                "lastRunAt": time.time(), "iterationCount": iteration, "status": "idle",
            })
            await slack.close()
            await convex.close()
            return MonitorResult(
                posted=False, opportunity_type="none", decision="SKIP",
                decision_chain="no_opportunities → SKIP",
                summary=f"Scanned {len(human_messages)} messages, no opportunities",
                iteration=iteration, candidates_found=0,
            )

        # Count recent bot posts (for rapid-fire check)
        bot_posts = await slack.get_bot_recent_posts(hours=1)
        bot_recent_count = len(bot_posts)

        # Evaluate top candidate (highest priority)
        top = candidates[0]

        # Get thread context if available
        thread_context = ""
        if top.thread_ts:
            try:
                thread_msgs = await slack.get_thread(CLAW_CHANNEL_ID, top.thread_ts)
                thread_context = "\n".join(
                    f"[{m.get('user', 'unknown')}]: {m.get('text', '')[:200]}"
                    for m in thread_msgs[:10]
                )
            except Exception:
                pass

        rubric = await _evaluate_candidate(top, thread_context, bot_recent_count)

        # Log the decision
        decision_log = {
            "timestamp": time.time(),
            "iteration": iteration,
            "channel": CLAW_CHANNEL_ID,
            "messagePreview": top.message_preview[:200],
            "opportunityType": f"{top.type.value}_{top.type.name}",
            "gates": rubric.to_dict().get("required_gates", {}),
            "disqualifiers": rubric.to_dict().get("disqualifiers", {}),
            "decision": rubric.decision,
            "decisionChain": rubric.decision_chain,
            "posted": False,
        }

        posted = False
        if rubric.should_post:
            if await _has_recent_bot_reply(slack, top):
                rubric.decision = "SKIP"
                rubric.decision_chain = "hard_guard.bot_already_replied → SKIP"
                rubric.blocking_gate = "Bot already replied recently in this thread"
                decision_log["decision"] = rubric.decision
                decision_log["decisionChain"] = rubric.decision_chain
                logger.info("Skipping %s opportunity because bot already replied in thread", top.type.name)
            elif not _acquire_monitor_claim(top):
                rubric.decision = "SKIP"
                rubric.decision_chain = "atomic_claim.concurrent_monitor → SKIP"
                rubric.blocking_gate = "Another monitor invocation already claimed this slot"
                decision_log["decision"] = rubric.decision
                decision_log["decisionChain"] = rubric.decision_chain
                logger.info("Skipping %s opportunity — concurrent monitor claimed it first", top.type.name)
            else:
                # We own the atomic claim — proceed to compose and post
                try:
                    # Use agency role persona for response composition
                    role = get_role_for_opportunity(top.type.value)
                    role_prompt = get_system_prompt(role) if role else None
                    response_text = await compose_response(
                        opportunity_type=f"{top.type.value}_{top.type.name}",
                        context=f"{top.context}\n\nTHREAD:\n{thread_context}",
                        system_prompt_override=role_prompt,
                    )
                    try:
                        thread_ts = top.thread_ts or top.message_ts
                        await slack.post_thread_reply(CLAW_CHANNEL_ID, thread_ts, response_text)
                        posted = True
                        decision_log["posted"] = True
                        logger.info("Posted response for %s opportunity", top.type.name)
                    except Exception as e:
                        logger.error("Failed to post response: %s", e)
                finally:
                    _release_monitor_claim(top)

        # Extract decisions from conversation and store in memory
        try:
            memory_entries = await extract_decisions(human_messages, CLAW_CHANNEL_ID)
            if memory_entries:
                stored = await store_memories(memory_entries, convex)
                logger.info("Stored %d memory entries from monitor run", stored)
        except Exception as e:
            logger.warning("Memory extraction failed (non-fatal): %s", e)

        # Surface relevant prior context for future runs
        try:
            prior_context = await surface_relevant(human_messages, convex)
            if prior_context:
                decision_log["priorContext"] = prior_context[:3]
        except Exception as e:
            logger.warning("Memory surfacing failed (non-fatal): %s", e)

        # Persist decision to Convex
        try:
            await convex.log_monitor_decision(decision_log)
        except Exception as e:
            logger.warning("Failed to log to Convex: %s — using local fallback", e)
            ConvexClient.log_local_fallback("logs/slack-monitor-decisions.jsonl", decision_log)

        await convex.update_task_state("monitor", {
            "lastRunAt": time.time(), "iterationCount": iteration, "status": "idle",
        })

        await slack.close()
        await convex.close()

        return MonitorResult(
            posted=posted,
            opportunity_type=f"{top.type.value}_{top.type.name}",
            decision=rubric.decision,
            decision_chain=rubric.decision_chain,
            summary=rubric.blocking_gate or "All gates passed",
            iteration=iteration,
            candidates_found=len(candidates),
        )

    except Exception as e:
        logger.error("Monitor run failed: %s", e)
        try:
            await convex.update_task_state("monitor", {
                "lastRunAt": time.time(), "iterationCount": iteration,
                "status": "error", "lastError": str(e)[:200],
            })
            await slack.close()
            await convex.close()
        except Exception:
            pass
        return MonitorResult(
            posted=False, opportunity_type="error", decision="ERROR",
            decision_chain=f"error: {str(e)[:100]}", summary=str(e)[:200],
            iteration=iteration, candidates_found=0,
        )
