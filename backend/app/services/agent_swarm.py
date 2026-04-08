"""Autonomous agent swarm -- multi-role continuous conversation.

Implements a 'moltbook' experience where 6 Agency Agent roles continuously
converse in #claw-communications. Each agent has a distinct persona, area of
expertise, and decision-making style. They:

1. Discuss strategy and identify opportunities
2. Debate approaches (consensus + divergence tracking)
3. Propose code changes via Claude Code Bridge
4. Track decisions in institutional memory
5. Self-evolve their rubrics and behaviors

Roles:
    Strategy Architect  -- long-term vision, resource allocation, market moves
    Engineering Lead    -- technical feasibility, architecture, shipping velocity
    Growth Analyst      -- metrics, funnels, competitive landscape, GTM
    Design Steward      -- UX, accessibility, brand consistency, user empathy
    Security Auditor    -- risk, compliance, threat modeling, data governance
    Ops Coordinator     -- reliability, incident response, capacity, process

Architecture note: each agent message is generated via the OpenAI API using
the role's system prompt from agency_roles. The synthesis step uses the same
pattern as _synthesize_prediction from slack_predict.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

import httpx

from .slack_client import SlackClient, CLAW_CHANNEL_ID
from .convex_client import ConvexClient
from .agency_roles import ROLE_REGISTRY, AgencyRole, get_system_prompt
from .slack_memory import extract_decisions, store_memories, search_memory
from .claude_code_bridge import invoke_claude_code
from .slack_predict import _generate_perspective, _synthesize_prediction

logger = logging.getLogger(__name__)

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

# ------------------------------------------------------------------
# Role ordering and emoji mapping
# ------------------------------------------------------------------

ROLE_EMOJIS: dict[str, str] = {
    "strategy-architect": "\U0001f3af",   # target
    "engineering-lead":   "\U0001f3d7\ufe0f",   # building construction
    "growth-analyst":     "\U0001f4c8",   # chart increasing
    "design-steward":     "\U0001f3a8",   # palette
    "security-auditor":   "\U0001f512",   # lock
    "ops-coordinator":    "\u2699\ufe0f",   # gear
}

ROLE_ORDER = [
    "strategy-architect",
    "engineering-lead",
    "growth-analyst",
    "design-steward",
    "security-auditor",
    "ops-coordinator",
]

# Rate-limit: at most 1 swarm conversation per hour
_MIN_INTERVAL_SECONDS = 60


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------

@dataclass
class AgentMessage:
    """A single message from one agent in the swarm conversation."""

    role_id: str
    role_name: str
    emoji: str
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class SwarmResult:
    """Outcome of a swarm conversation."""

    posted: bool
    topic: str
    messages_count: int
    synthesis: str
    consensus_points: list[str] = field(default_factory=list)
    divergences: list[str] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    code_change_proposed: bool = False
    thread_ts: Optional[str] = None
    iteration: int = 0


@dataclass
class BuildResult:
    """Outcome of a propose-and-build cycle."""

    approved: bool
    description: str
    pr_url: Optional[str] = None
    error: Optional[str] = None


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _parse_json_response(raw: str) -> Any:
    """Strip markdown fences and parse JSON from an LLM response."""
    content = raw.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()
    return json.loads(content)


async def _llm_call(
    prompt: str,
    system: str = "",
    model: str = "gpt-4.1-mini",
    max_tokens: int = 600,
    temperature: float = 0.4,
) -> Optional[str]:
    """Make a single OpenAI chat completion call and return the content string."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set -- skipping LLM call")
        return None

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as client:
            resp = await client.post(
                OPENAI_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error("LLM call failed: %s", e)
        return None


async def _check_rate_limit(convex: ConvexClient) -> bool:
    """Return True if enough time has passed since the last swarm conversation."""
    state = await convex.get_task_state("swarm")
    if not state:
        return True
    last_run = state.get("lastRunAt", 0)
    return (time.time() - last_run) >= _MIN_INTERVAL_SECONDS


async def _generate_agent_response(
    role: AgencyRole,
    topic: str,
    conversation_so_far: list[AgentMessage],
    memory_context: str,
    model: str = "gpt-4.1-mini",
) -> Optional[AgentMessage]:
    """Generate a single agent's conversational response in the swarm thread.

    Unlike _generate_perspective (which returns structured JSON), this produces
    a natural-language message suitable for posting directly into a Slack thread
    as part of a multi-agent conversation.
    """
    role_id = role.id
    role_name = role.name
    emoji = ROLE_EMOJIS.get(role_id, "\U0001f916")

    # Build the conversation history for context
    history_lines: list[str] = []
    for msg in conversation_so_far:
        history_lines.append(f"{msg.emoji} *{msg.role_name}:* {msg.content}")
    history_text = "\n\n".join(history_lines) if history_lines else "(You are opening the discussion.)"

    system_prompt = get_system_prompt(role)

    user_prompt = f"""You are participating in an executive swarm discussion in the #claw-communications Slack channel.

TOPIC: {topic}

PRIOR INSTITUTIONAL MEMORY:
{memory_context}

CONVERSATION SO FAR:
{history_text}

INSTRUCTIONS:
- Respond as the {role_name} with your unique perspective
- Reference what other roles said (agree, disagree, build on their points)
- Use "Calculus Made Easy" style: plain English analogy first, then specifics
- Be concise (3-5 sentences max)
- If you see an opportunity for a concrete code change or tool, mention it
- End with a clear position or question to keep the discussion moving

Write your response as natural Slack message text (no JSON, no markdown headers)."""

    content = await _llm_call(
        prompt=user_prompt,
        system=system_prompt,
        model=model,
        max_tokens=400,
        temperature=0.5,
    )

    if not content:
        return None

    return AgentMessage(
        role_id=role_id,
        role_name=role_name,
        emoji=emoji,
        content=content,
    )


async def _synthesize_swarm(
    topic: str,
    messages: list[AgentMessage],
    model: str = "gpt-4.1-mini",
) -> Optional[dict]:
    """Synthesize the swarm conversation into consensus, divergences, and action items.

    Returns a dict with keys:
        synthesis, consensus_points, divergences, action_items, code_change
    """
    conversation_text = "\n\n".join(
        f"{m.emoji} *{m.role_name}:* {m.content}" for m in messages
    )

    prompt = f"""Synthesize this executive swarm discussion into a decision summary.

TOPIC: {topic}

DISCUSSION:
{conversation_text}

Return a JSON object:
{{
  "synthesis": "2-3 sentence executive summary in Calculus Made Easy style (plain English analogy, then specifics)",
  "consensus_points": ["point where roles agree", ...],
  "divergences": ["point where roles disagree", ...],
  "action_items": ["specific next step someone should take", ...],
  "code_change": {{
    "needed": true/false,
    "description": "what code change is needed (empty string if not needed)",
    "priority": "high|medium|low"
  }}
}}

Rules:
- Max 3 consensus points, max 2 divergences, max 3 action items
- code_change.needed = true ONLY if at least 2 roles explicitly suggested a code change
- Use Calculus Made Easy style throughout

Return ONLY the JSON object."""

    raw = await _llm_call(prompt=prompt, model=model, max_tokens=500, temperature=0.2)
    if not raw:
        return None

    try:
        return _parse_json_response(raw)
    except (json.JSONDecodeError, IndexError) as e:
        logger.error("Failed to parse swarm synthesis: %s", e)
        return None


def _format_synthesis_for_slack(topic: str, synthesis: dict, messages_count: int) -> str:
    """Format the synthesis dict as a Slack message."""
    text = synthesis.get("synthesis", "")
    consensus = synthesis.get("consensus_points", [])
    divergences = synthesis.get("divergences", [])
    action_items = synthesis.get("action_items", [])
    code_change = synthesis.get("code_change", {})

    parts: list[str] = [
        f"*\U0001f9e0 Swarm Synthesis* ({messages_count} perspectives)\n"
        f"_Topic: {topic[:120]}_\n",
        text,
    ]

    if consensus:
        parts.append("\n*Consensus:*")
        for c in consensus[:3]:
            parts.append(f"\u2022 {c}")

    if divergences:
        parts.append("\n*Where perspectives diverge:*")
        for d in divergences[:2]:
            parts.append(f"\u2022 {d}")

    if action_items:
        parts.append("\n*Action items:*")
        for a in action_items[:3]:
            parts.append(f"\u2022 {a}")

    if code_change.get("needed"):
        priority = code_change.get("priority", "medium")
        desc = code_change.get("description", "")
        parts.append(f"\n*\U0001f527 Code change proposed* ({priority} priority): {desc}")

    parts.append("\n_Autonomous swarm discussion -- Agency Agent executive team_")

    return "\n".join(parts)


# ------------------------------------------------------------------
# Core entry point: run a swarm conversation
# ------------------------------------------------------------------

async def run_swarm_conversation(
    topic: str,
    initiator_role: str | None = None,
    max_rounds: int = 4,
) -> SwarmResult:
    """Start and run a multi-agent swarm conversation on a topic.

    1. Post the opening message in a new Slack thread
    2. Each role responds in turn, building on prior messages
    3. After all roles have spoken (up to max_rounds cycles), synthesize
    4. If synthesis identifies an actionable code change, invoke Claude Code Bridge
    5. Log the full conversation to Convex institutional memory

    Args:
        topic: The discussion topic or question.
        initiator_role: Role ID to start the discussion. Defaults to strategy-architect.
        max_rounds: Maximum discussion rounds (each round = all 6 roles respond).
                    Capped at 4 to limit API costs.

    Returns:
        SwarmResult with conversation outcome.
    """
    slack = SlackClient()
    convex = ConvexClient()
    iteration = 0

    try:
        # Check rate limit
        if not await _check_rate_limit(convex):
            logger.info("Swarm rate-limited -- skipping (min interval: %ds)", _MIN_INTERVAL_SECONDS)
            return SwarmResult(
                posted=False, topic=topic, messages_count=0,
                synthesis="Rate-limited: less than 1 hour since last swarm conversation.",
            )

        # Read iteration count
        state = await convex.get_task_state("swarm")
        if state:
            iteration = state.get("iterationCount", 0) + 1
        else:
            iteration = 1

        # Gather context from institutional memory
        memory_results = await search_memory(topic[:80], convex, limit=5)
        memory_context = "\n".join(
            f"- [{m.get('sourceType', 'note')}] {m.get('summary', '')}"
            for m in memory_results
        ) if memory_results else "(No prior institutional memory on this topic.)"

        # Determine role order with initiator first
        role_order = list(ROLE_ORDER)
        if initiator_role and initiator_role in role_order:
            role_order.remove(initiator_role)
            role_order.insert(0, initiator_role)

        # Cap rounds
        max_rounds = min(max_rounds, 4)

        # ----- Round 0: Post the opening thread message -----
        opening_role_id = role_order[0]
        opening_role = ROLE_REGISTRY.get(opening_role_id)
        if not opening_role:
            return SwarmResult(
                posted=False, topic=topic, messages_count=0,
                synthesis=f"Unknown initiator role: {opening_role_id}",
            )

        opening_emoji = ROLE_EMOJIS.get(opening_role_id, "\U0001f916")
        thread_header = (
            f"*\U0001f300 Swarm Discussion* (iteration {iteration})\n"
            f"_Topic: {topic}_\n\n"
            f"Roles participating: {' '.join(ROLE_EMOJIS.get(r, '') for r in role_order)}\n"
            f"---"
        )

        # Post the thread header as the parent message
        thread_ts: Optional[str] = None
        try:
            result = await slack.post_message(CLAW_CHANNEL_ID, thread_header)
            thread_ts = result.get("ts")
        except Exception as e:
            logger.error("Failed to post swarm thread header: %s", e)
            return SwarmResult(posted=False, topic=topic, messages_count=0, synthesis=f"Slack post failed: {e}")

        if not thread_ts:
            return SwarmResult(posted=False, topic=topic, messages_count=0, synthesis="No thread_ts returned")

        # ----- Generate agent responses across rounds -----
        all_messages: list[AgentMessage] = []

        for round_num in range(max_rounds):
            # In round 0, all roles respond. In later rounds, only respond
            # if there is genuine divergence or new ground to cover.
            roles_this_round = role_order if round_num == 0 else role_order[:3]

            for role_id in roles_this_round:
                role = ROLE_REGISTRY.get(role_id)
                if not role:
                    continue

                msg = await _generate_agent_response(
                    role=role,
                    topic=topic,
                    conversation_so_far=all_messages,
                    memory_context=memory_context,
                )
                if not msg:
                    continue

                all_messages.append(msg)

                # Post to Slack thread
                slack_text = f"{msg.emoji} *{msg.role_name}:* {msg.content}"
                try:
                    await slack.post_thread_reply(CLAW_CHANNEL_ID, thread_ts, slack_text)
                except Exception as e:
                    logger.warning("Failed to post agent message for %s: %s", role_id, e)

                # Small delay to avoid rate limits and make the conversation feel natural
                await asyncio.sleep(1.5)

            # After round 0, check if we need more rounds
            if round_num == 0 and max_rounds > 1:
                # Quick LLM check: is there enough divergence to warrant another round?
                check_prompt = (
                    f"Given this swarm discussion so far, is there significant unresolved "
                    f"disagreement that warrants another round of discussion? "
                    f"Reply with just 'yes' or 'no'.\n\n"
                    + "\n".join(f"- {m.role_name}: {m.content[:100]}" for m in all_messages)
                )
                check_result = await _llm_call(check_prompt, max_tokens=10, temperature=0.0)
                if check_result and "no" in check_result.lower():
                    logger.info("Swarm converged after round 0 -- skipping additional rounds")
                    break

        # ----- Synthesize -----
        synthesis = await _synthesize_swarm(topic, all_messages)
        if not synthesis:
            synthesis = {
                "synthesis": "Synthesis generation failed. Review the thread for individual perspectives.",
                "consensus_points": [],
                "divergences": [],
                "action_items": [],
                "code_change": {"needed": False, "description": "", "priority": "low"},
            }

        synthesis_text = _format_synthesis_for_slack(topic, synthesis, len(all_messages))
        try:
            await slack.post_thread_reply(CLAW_CHANNEL_ID, thread_ts, synthesis_text)
        except Exception as e:
            logger.warning("Failed to post synthesis: %s", e)

        # ----- Code change proposal (if flagged) -----
        code_change = synthesis.get("code_change", {})
        code_change_proposed = False
        if code_change.get("needed"):
            code_desc = code_change.get("description", "")
            code_priority = code_change.get("priority", "medium")

            try:
                build_result = await propose_and_build(
                    description=code_desc, priority=code_priority,
                )
                code_change_proposed = True
                if build_result.pr_url:
                    await slack.post_thread_reply(
                        CLAW_CHANNEL_ID, thread_ts,
                        f"\U0001f527 *Code change submitted:* {build_result.pr_url}\n"
                        f"_Requires human review before merge._",
                    )
                elif build_result.error:
                    await slack.post_thread_reply(
                        CLAW_CHANNEL_ID, thread_ts,
                        f"\u26a0\ufe0f Code change proposal failed: {build_result.error[:200]}",
                    )
            except Exception as e:
                logger.warning("Code change proposal failed: %s", e)

        # ----- Persist to institutional memory -----
        try:
            decisions = await extract_decisions(
                [{"text": m.content, "user": m.role_id, "ts": str(m.timestamp)} for m in all_messages],
                channel="claw-communications",
            )
            if decisions:
                await store_memories(decisions, convex)
        except Exception as e:
            logger.warning("Failed to extract/store swarm decisions: %s", e)

        # Log conversation to Convex
        try:
            await convex.store_memory({
                "topic": f"swarm:{topic[:80]}",
                "summary": synthesis.get("synthesis", "")[:300],
                "decision": "; ".join(synthesis.get("action_items", []))[:300],
                "participants": [m.role_id for m in all_messages],
                "channel": "claw-communications",
                "timestamp": time.time(),
                "sourceType": "swarm_discussion",
            })
        except Exception as e:
            logger.warning("Failed to log swarm to memory: %s", e)

        # Update task state
        await convex.update_task_state("swarm", {
            "lastRunAt": time.time(),
            "iterationCount": iteration,
            "status": "idle",
            "lastTopic": topic[:200],
            "lastMessageCount": len(all_messages),
        })

        await slack.close()
        await convex.close()

        return SwarmResult(
            posted=True,
            topic=topic,
            messages_count=len(all_messages),
            synthesis=synthesis.get("synthesis", ""),
            consensus_points=synthesis.get("consensus_points", []),
            divergences=synthesis.get("divergences", []),
            action_items=synthesis.get("action_items", []),
            code_change_proposed=code_change_proposed,
            thread_ts=thread_ts,
            iteration=iteration,
        )

    except Exception as e:
        logger.error("Swarm conversation failed: %s", e)
        try:
            await convex.update_task_state("swarm", {
                "lastRunAt": time.time(),
                "iterationCount": iteration,
                "status": "error",
                "lastError": str(e)[:200],
            })
            await slack.close()
            await convex.close()
        except Exception:
            pass
        return SwarmResult(
            posted=False, topic=topic, messages_count=0,
            synthesis=f"Error: {str(e)[:200]}",
        )


# ------------------------------------------------------------------
# Continuous swarm mode
# ------------------------------------------------------------------

async def run_continuous_swarm(interval_minutes: int = 120) -> None:
    """Run the swarm continuously: scan for topics, discuss, repeat.

    Every *interval_minutes*, the swarm:
    1. Scans recent Slack messages + Convex memory for topics
    2. Uses LLM to identify the most strategic topic to discuss
    3. Triggers run_swarm_conversation on that topic
    4. Tracks discussed topics to avoid repetition

    This function is intended to be called by a cron endpoint. Each
    invocation runs a single cycle (find topic + discuss). The cron
    scheduler is responsible for calling it repeatedly.
    """
    slack = SlackClient()
    convex = ConvexClient()

    try:
        # Get recently discussed swarm topics to avoid repetition
        state = await convex.get_task_state("swarm")
        recent_topics: list[str] = []
        if state:
            recent_topics = state.get("recentTopics", [])

        # Scan recent Slack messages for potential discussion topics
        messages = await slack.get_channel_history(CLAW_CHANNEL_ID, limit=30)
        human_messages = [m for m in messages if slack.is_human_message(m)]

        # Also pull recent memories for strategic context
        memory_context = await search_memory("strategy roadmap priorities", convex, limit=5)
        memory_text = "\n".join(
            f"- {m.get('summary', '')}" for m in memory_context
        ) if memory_context else ""

        # Combine into a context block for topic selection
        msg_text = "\n".join(
            f"- {m.get('text', '')[:150]}" for m in human_messages[:15]
        )
        already_discussed = "\n".join(f"- {t}" for t in recent_topics[-10:]) if recent_topics else "(none)"

        topic_prompt = f"""You are a strategic topic selector for an autonomous agent executive team.

RECENT SLACK MESSAGES:
{msg_text}

INSTITUTIONAL MEMORY (strategic context):
{memory_text}

RECENTLY DISCUSSED TOPICS (avoid repeating):
{already_discussed}

Select the single most strategically important topic for the executive team to discuss right now.
Consider: unresolved technical decisions, competitive threats, growth opportunities,
operational risks, or capability gaps.

Return a JSON object:
{{
  "topic": "concise topic statement (one sentence)",
  "initiator_role": "role_id that should lead this discussion",
  "urgency": "high|medium|low",
  "reasoning": "why this topic matters now (one sentence)"
}}

Role IDs: strategy-architect, engineering-lead, growth-analyst, design-steward, security-auditor, ops-coordinator

Return ONLY the JSON object."""

        raw = await _llm_call(topic_prompt, max_tokens=200, temperature=0.3)
        if not raw:
            logger.warning("Continuous swarm: topic selection LLM call failed")
            await slack.close()
            await convex.close()
            return

        try:
            topic_selection = _parse_json_response(raw)
        except (json.JSONDecodeError, IndexError):
            logger.error("Continuous swarm: failed to parse topic selection")
            await slack.close()
            await convex.close()
            return

        topic = topic_selection.get("topic", "")
        initiator = topic_selection.get("initiator_role", "strategy-architect")

        if not topic:
            logger.info("Continuous swarm: no strategic topic identified -- skipping")
            await slack.close()
            await convex.close()
            return

        logger.info("Continuous swarm selected topic: %s (initiator: %s)", topic, initiator)

        # Close our clients before handing off (run_swarm_conversation creates its own)
        await slack.close()
        await convex.close()

        # Run the swarm conversation
        result = await run_swarm_conversation(topic=topic, initiator_role=initiator)

        # Update recent topics list (keep last 20)
        convex2 = ConvexClient()
        try:
            recent_topics.append(topic[:200])
            recent_topics = recent_topics[-20:]
            await convex2.update_task_state("swarm", {
                "recentTopics": recent_topics,
            })
        finally:
            await convex2.close()

        logger.info(
            "Continuous swarm completed: posted=%s, messages=%d, topic=%s",
            result.posted, result.messages_count, topic[:80],
        )

    except Exception as e:
        logger.error("Continuous swarm cycle failed: %s", e)
        try:
            await slack.close()
            await convex.close()
        except Exception:
            pass


# ------------------------------------------------------------------
# Competitive analysis mode
# ------------------------------------------------------------------

async def run_competitive_analysis() -> SwarmResult:
    """Roles analyze competitive landscape together.

    Growth Analyst leads. Other roles contribute domain expertise.
    Output: actionable competitive strategy posted to Slack.
    """
    slack = SlackClient()
    convex = ConvexClient()

    try:
        # Gather competitive intelligence from memory
        comp_memories = await search_memory("competitive landscape market competitors", convex, limit=5)
        comp_context = "\n".join(
            f"- {m.get('summary', '')}" for m in comp_memories
        ) if comp_memories else "(No prior competitive intelligence in memory.)"

        # Use Growth Analyst to frame the analysis
        framing_prompt = f"""You are the Growth Analyst preparing a competitive analysis brief
for the retention.sh executive team.

PRIOR COMPETITIVE INTELLIGENCE:
{comp_context}

Generate a focused competitive analysis topic that the team should discuss.
Consider: new competitor moves, market shifts, feature gaps, pricing changes,
or emerging threats/opportunities in the mobile testing / QA automation space.

Return a JSON object:
{{
  "topic": "specific competitive analysis question (one sentence)",
  "context": "2-3 sentences of market context to frame the discussion"
}}

Return ONLY the JSON object."""

        raw = await _llm_call(framing_prompt, max_tokens=200, temperature=0.4)
        await slack.close()
        await convex.close()

        if not raw:
            return SwarmResult(posted=False, topic="competitive analysis", messages_count=0, synthesis="LLM call failed")

        try:
            framing = _parse_json_response(raw)
        except (json.JSONDecodeError, IndexError):
            return SwarmResult(posted=False, topic="competitive analysis", messages_count=0, synthesis="Parse failed")

        topic = f"Competitive Analysis: {framing.get('topic', 'market landscape')}"

        # Run the swarm with Growth Analyst as initiator
        return await run_swarm_conversation(
            topic=topic,
            initiator_role="growth-analyst",
            max_rounds=2,
        )

    except Exception as e:
        logger.error("Competitive analysis failed: %s", e)
        try:
            await slack.close()
            await convex.close()
        except Exception:
            pass
        return SwarmResult(
            posted=False, topic="competitive analysis", messages_count=0,
            synthesis=f"Error: {str(e)[:200]}",
        )


# ------------------------------------------------------------------
# Self-evolution discussion
# ------------------------------------------------------------------

async def run_self_evolution_discussion() -> SwarmResult:
    """Roles review their own decision logs and discuss improvements.

    This is the "agents building tools for themselves" capability:
    1. Pull recent decision logs from Convex
    2. Each role reviews from their perspective
    3. Discuss what is working, what is not
    4. Propose rubric changes, new capabilities, or code changes
    """
    convex = ConvexClient()

    try:
        # Gather decision history across all tasks
        monitor_decisions = await convex.get_recent_decisions("monitor", limit=20)
        digest_decisions = await convex.get_recent_decisions("digest", limit=20)
        swarm_state = await convex.get_task_state("swarm")

        # Summarize decision patterns
        monitor_posted = sum(1 for d in monitor_decisions if d.get("posted"))
        digest_posted = sum(1 for d in digest_decisions if d.get("posted"))
        swarm_iterations = swarm_state.get("iterationCount", 0) if swarm_state else 0
        recent_swarm_topics = swarm_state.get("recentTopics", [])[-5:] if swarm_state else []

        decision_summary = (
            f"Monitor: {len(monitor_decisions)} decisions, {monitor_posted} posted\n"
            f"Digest: {len(digest_decisions)} decisions, {digest_posted} posted\n"
            f"Swarm: {swarm_iterations} conversations so far\n"
            f"Recent swarm topics: {', '.join(recent_swarm_topics) if recent_swarm_topics else 'none'}"
        )

        # Also check recent memories for patterns
        evolution_memories = await search_memory("self improvement evolution rubric", convex, limit=3)
        evolution_context = "\n".join(
            f"- {m.get('summary', '')}" for m in evolution_memories
        ) if evolution_memories else ""

        await convex.close()

        topic = (
            f"Self-Evolution Review: How are we performing? "
            f"Decision stats: {decision_summary}. "
            f"What rubric changes, new tools, or capability improvements should we propose?"
        )

        if evolution_context:
            topic += f"\n\nPrior evolution notes:\n{evolution_context}"

        # Strategy Architect leads self-evolution discussions
        return await run_swarm_conversation(
            topic=topic,
            initiator_role="strategy-architect",
            max_rounds=2,
        )

    except Exception as e:
        logger.error("Self-evolution discussion failed: %s", e)
        try:
            await convex.close()
        except Exception:
            pass
        return SwarmResult(
            posted=False, topic="self-evolution", messages_count=0,
            synthesis=f"Error: {str(e)[:200]}",
        )


# ------------------------------------------------------------------
# Propose and build
# ------------------------------------------------------------------

async def propose_and_build(
    description: str,
    priority: str = "medium",
) -> BuildResult:
    """When agents decide they need a new tool or capability.

    1. Create a proposal, discuss it briefly in the swarm
    2. If approved (require_approval=True), invoke Claude Code Bridge to build it
    3. Post PR link to Slack for human review

    The code change always requires human approval before merging --
    agents can propose, but humans must verify.

    Args:
        description: What to build or change.
        priority: "high", "medium", or "low".

    Returns:
        BuildResult with approval status and optional PR URL.
    """
    slack = SlackClient()

    try:
        # Post the proposal to Slack
        proposal_msg = (
            f"\U0001f527 *Build Proposal* ({priority} priority)\n"
            f"_{description}_\n\n"
            f"Invoking Claude Code Bridge with `require_approval=True`...\n"
            f"_A human must review and approve before any code is merged._"
        )

        try:
            result = await slack.post_message(CLAW_CHANNEL_ID, proposal_msg)
            thread_ts = result.get("ts")
        except Exception as e:
            logger.error("Failed to post build proposal: %s", e)
            await slack.close()
            return BuildResult(approved=False, description=description, error=f"Slack post failed: {e}")

        # Invoke Claude Code Bridge
        try:
            bridge_result = await invoke_claude_code(
                task=description,
                require_approval=True,
                context=f"Priority: {priority}. Proposed by agent swarm executive team.",
            )
        except Exception as e:
            logger.error("Claude Code Bridge invocation failed: %s", e)
            if thread_ts:
                try:
                    await slack.post_thread_reply(
                        CLAW_CHANNEL_ID, thread_ts,
                        f"\u274c Claude Code Bridge failed: {str(e)[:200]}",
                    )
                except Exception:
                    pass
            await slack.close()
            return BuildResult(approved=False, description=description, error=str(e)[:200])

        pr_url = bridge_result.get("pr_url") if isinstance(bridge_result, dict) else None

        if pr_url and thread_ts:
            try:
                await slack.post_thread_reply(
                    CLAW_CHANNEL_ID, thread_ts,
                    f"\u2705 *PR created:* {pr_url}\n_Awaiting human review._",
                )
            except Exception:
                pass

        await slack.close()
        return BuildResult(
            approved=True,
            description=description,
            pr_url=pr_url,
        )

    except Exception as e:
        logger.error("Propose and build failed: %s", e)
        try:
            await slack.close()
        except Exception:
            pass
        return BuildResult(approved=False, description=description, error=str(e)[:200])
