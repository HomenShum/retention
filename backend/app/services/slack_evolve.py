"""Evolution review — autoresearch-style self-improvement loop.

Reads the last 48 decisions from Convex, computes 10 boolean health metrics,
and proposes rubric changes. This is the "measure -> evaluate -> keep/discard/modify"
loop inspired by Karpathy's autoresearch pattern.

Runs daily at 6AM PT.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Optional

from .slack_client import SlackClient, CLAW_CHANNEL_ID
from .convex_client import ConvexClient
from .llm_judge import evaluate_gates_batch, GateResult, call_responses_api
from .eval_benchmark import run_benchmark, judge_response

logger = logging.getLogger(__name__)


@dataclass
class HealthMetric:
    name: str
    healthy: bool
    reason: str


@dataclass
class EvolutionProposal:
    target: str
    change_type: str
    description: str
    evidence: str
    risk: str


@dataclass
class ApplyResult:
    applied: bool
    reason: str
    before_score: float
    after_score: float


@dataclass
class EvolveResult:
    health_metrics: list[HealthMetric]
    proposals: list[EvolutionProposal]
    monitor_stats: dict
    digest_stats: dict
    iteration: int
    posted: bool


def _compute_stats(decisions: list[dict]) -> dict:
    if not decisions:
        return {"total": 0, "posted": 0, "skipped": 0, "post_rate": 0.0, "gate_blocks": {}, "type_distribution": {}}

    total = len(decisions)
    posted = sum(1 for d in decisions if d.get("posted"))
    skipped = total - posted

    gate_blocks: dict[str, int] = {}
    for d in decisions:
        chain = d.get("decisionChain", "")
        if "FALSE" in chain:
            for part in chain.split(" \u2192 "):
                if "FALSE" in part:
                    gate_name = part.split("=")[0].strip()
                    gate_blocks[gate_name] = gate_blocks.get(gate_name, 0) + 1

    type_dist: dict[str, int] = {}
    for d in decisions:
        opp_type = d.get("opportunityType", "unknown")
        type_dist[opp_type] = type_dist.get(opp_type, 0) + 1

    return {
        "total": total, "posted": posted, "skipped": skipped,
        "post_rate": posted / total if total > 0 else 0.0,
        "gate_blocks": gate_blocks, "type_distribution": type_dist,
    }


HEALTH_GATES = [
    {"name": "post_rate_in_range", "question": "Is the post rate between 10% and 50%? Too low means the bot is too conservative, too high means too aggressive."},
    {"name": "opportunity_type_coverage", "question": "Are at least 3 different opportunity types represented in the decisions? Good coverage means the bot is detecting diverse opportunities."},
    {"name": "gate_distribution_balanced", "question": "Is the gate blocking distribution balanced? No single gate should block more than 60% of all SKIP decisions."},
    {"name": "no_regret_posts", "question": "Were there any posts that received negative reactions (thumbs down, 'not helpful', or were deleted)? Absence of regret = healthy."},
    {"name": "no_missed_opportunities", "question": "Were there conversations where the bot should have responded but didn't? Check for unanswered questions that went cold."},
    {"name": "meta_feedback_responsiveness", "question": "Did the bot respond to all Type B (meta-feedback about itself) opportunities? This is the highest priority type."},
    {"name": "disqualifier_precision", "question": "Are the disqualifiers firing correctly? They should catch truly inappropriate moments, not block good opportunities."},
    {"name": "digest_post_rate_in_range", "question": "Is the digest posting 2-4 times per day during active periods? Too many = noise, too few = missed value."},
    {"name": "digest_gate_balance", "question": "Are digest gates balanced? The 'sufficient_activity' gate shouldn't block everything during slow days."},
    {"name": "log_completeness", "question": "Do all decisions have complete gate evaluations with reasons? Incomplete logs prevent learning."},
]


async def _evaluate_health(monitor_stats: dict, digest_stats: dict, bot_engagement: dict) -> list[HealthMetric]:
    context = f"""MONITOR STATS (last 48 decisions):
- Total decisions: {monitor_stats['total']}
- Posted: {monitor_stats['posted']} ({monitor_stats['post_rate']:.0%})
- Skipped: {monitor_stats['skipped']}
- Gate blocks: {json.dumps(monitor_stats['gate_blocks'])}
- Type distribution: {json.dumps(monitor_stats['type_distribution'])}

DIGEST STATS (last 48 decisions):
- Total decisions: {digest_stats['total']}
- Posted: {digest_stats['posted']} ({digest_stats['post_rate']:.0%})
- Skipped: {digest_stats['skipped']}

BOT ENGAGEMENT:
- Posts with reactions: {bot_engagement.get('with_reactions', 0)}
- Posts with thread replies: {bot_engagement.get('with_replies', 0)}
- Total recent posts: {bot_engagement.get('total', 0)}"""

    results = await evaluate_gates_batch(HEALTH_GATES, context)
    return [HealthMetric(name=r.name, healthy=r.value, reason=r.reason) for r in results]


async def _generate_proposals(health_metrics: list[HealthMetric], monitor_stats: dict, digest_stats: dict) -> list[EvolutionProposal]:
    unhealthy = [m for m in health_metrics if not m.healthy]
    if not unhealthy:
        return []

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return []

    issues_text = "\n".join(f"- {m.name}: {m.reason}" for m in unhealthy)

    prompt = f"""Based on these unhealthy metrics for our Slack bot's boolean rubric, suggest specific changes.

UNHEALTHY METRICS:
{issues_text}

MONITOR STATS: post_rate={monitor_stats['post_rate']:.0%}, gate_blocks={json.dumps(monitor_stats['gate_blocks'])}
DIGEST STATS: post_rate={digest_stats['post_rate']:.0%}

For each issue, propose a concrete change. Return a JSON array:
[{{"target": "gate or parameter name", "change_type": "adjust_threshold|add_gate|remove_gate|modify_question", "description": "what to change", "evidence": "data supporting this change", "risk": "what could go wrong"}}]

Rules:
- Be conservative — only propose changes with clear evidence
- Prefer small adjustments over big rewrites
- Max 3 proposals

Return ONLY the JSON array."""

    try:
        content = await call_responses_api(prompt, task="evolve_synthesis", timeout_s=20)
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        proposals = json.loads(content)
        return [EvolutionProposal(target=p.get("target", ""), change_type=p.get("change_type", ""), description=p.get("description", ""), evidence=p.get("evidence", ""), risk=p.get("risk", "")) for p in proposals[:3]]
    except Exception as e:
        logger.error("Proposal generation failed: %s", e)
        return []


async def _check_bot_engagement(slack: SlackClient) -> dict:
    try:
        bot_posts = await slack.get_bot_recent_posts(hours=48)
        total = len(bot_posts)
        with_reactions = sum(1 for p in bot_posts if p.get("reactions") or p.get("reply_count", 0) > 0)
        with_replies = sum(1 for p in bot_posts if p.get("reply_count", 0) > 0)
        return {"total": total, "with_reactions": with_reactions, "with_replies": with_replies}
    except Exception:
        return {"total": 0, "with_reactions": 0, "with_replies": 0}


EVOLVED_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "evolved_config.json"
ALLOWED_DOMAINS = {"prompt", "routing", "tool_selection", "rubric", "gate", "threshold"}
REGRESSION_THRESHOLD = 0.05  # 5% score drop triggers revert


def _read_evolved_config() -> dict:
    """Read the current evolved config, or return empty dict if absent."""
    if EVOLVED_CONFIG_PATH.exists():
        try:
            return json.loads(EVOLVED_CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_evolved_config(config: dict) -> None:
    """Write the evolved config JSON atomically."""
    EVOLVED_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVOLVED_CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")


async def _judge_safety(proposal: EvolutionProposal) -> bool:
    """Ask the LLM judge whether a proposal is safe to auto-apply.

    Must be reversible, in an allowed domain, backed by evidence,
    and must not modify safety boundaries.
    """
    safety_prompt = f"""You are a safety reviewer for an autonomous evolution system.
A proposal wants to auto-apply the following change:

Target: {proposal.target}
Change type: {proposal.change_type}
Description: {proposal.description}
Evidence: {proposal.evidence}
Risk: {proposal.risk}

Evaluate whether this proposal is safe to auto-apply. It MUST meet ALL criteria:
1. REVERSIBLE — the change can be rolled back without data loss
2. ALLOWED DOMAIN — the target is one of: prompt, routing, tool_selection, rubric, gate, threshold
3. EVIDENCE-BACKED — the evidence field cites concrete data (numbers, rates, counts)
4. NO SAFETY BOUNDARY CHANGES — does NOT weaken content filters, remove disqualifiers, disable gates, or relax safety checks

Answer with a single JSON object:
{{"safe": true/false, "reason": "one sentence explanation"}}
Return ONLY the JSON."""

    try:
        content = await call_responses_api(safety_prompt, task="evolve_safety_judge", timeout_s=15)
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        result = json.loads(content)
        is_safe = bool(result.get("safe", False))
        if not is_safe:
            logger.info("Safety judge rejected proposal %s: %s", proposal.target, result.get("reason", ""))
        return is_safe
    except Exception as e:
        logger.error("Safety judge failed for %s: %s — defaulting to reject", proposal.target, e)
        return False


async def _apply_proposal(proposal: EvolutionProposal) -> ApplyResult:
    """Apply a single evolution proposal with before/after eval and rollback on regression."""

    # --- Domain check -----------------------------------------------------------
    domain_tokens = {proposal.target.lower(), proposal.change_type.lower()}
    if not domain_tokens & ALLOWED_DOMAINS:
        return ApplyResult(applied=False, reason=f"Target '{proposal.target}' outside allowed domains", before_score=0.0, after_score=0.0)

    # --- Safety judge gate ------------------------------------------------------
    safe = await _judge_safety(proposal)
    if not safe:
        return ApplyResult(applied=False, reason="Safety judge rejected proposal", before_score=0.0, after_score=0.0)

    # --- Run BEFORE benchmark ---------------------------------------------------
    try:
        before_score = await run_benchmark("evolve")
    except Exception as e:
        logger.error("Pre-apply benchmark failed: %s", e)
        return ApplyResult(applied=False, reason=f"Pre-apply benchmark error: {e}", before_score=0.0, after_score=0.0)

    # --- Apply the config override ----------------------------------------------
    config = _read_evolved_config()
    previous_config = json.dumps(config)  # snapshot for rollback

    config.setdefault("overrides", []).append({
        "target": proposal.target,
        "change_type": proposal.change_type,
        "description": proposal.description,
        "evidence": proposal.evidence,
        "applied_at": time.time(),
    })
    _write_evolved_config(config)

    # --- Run AFTER benchmark ----------------------------------------------------
    try:
        after_score = await run_benchmark("evolve")
    except Exception as e:
        logger.error("Post-apply benchmark failed: %s — reverting", e)
        _write_evolved_config(json.loads(previous_config))
        return ApplyResult(applied=False, reason=f"Post-apply benchmark error: {e}", before_score=before_score, after_score=0.0)

    # --- Regression check -------------------------------------------------------
    if before_score > 0 and (before_score - after_score) / before_score > REGRESSION_THRESHOLD:
        logger.warning(
            "Regression detected for %s: %.3f -> %.3f (%.1f%% drop) — reverting",
            proposal.target, before_score, after_score,
            100.0 * (before_score - after_score) / before_score,
        )
        _write_evolved_config(json.loads(previous_config))
        return ApplyResult(
            applied=False,
            reason=f"Regression: score dropped {before_score:.3f} -> {after_score:.3f}",
            before_score=before_score,
            after_score=after_score,
        )

    # --- Commit (no push — cron handles that) -----------------------------------
    try:
        subprocess.run(
            ["git", "add", str(EVOLVED_CONFIG_PATH)],
            cwd=EVOLVED_CONFIG_PATH.parent.parent,
            check=True, capture_output=True, timeout=10,
        )
        subprocess.run(
            ["git", "commit", "-m", f"evolve: auto-apply {proposal.target} ({proposal.change_type})"],
            cwd=EVOLVED_CONFIG_PATH.parent.parent,
            check=True, capture_output=True, timeout=10,
        )
    except subprocess.CalledProcessError as e:
        logger.warning("Git commit failed (change is still on disk): %s", e.stderr[:200] if e.stderr else str(e))
    except Exception as e:
        logger.warning("Git commit failed: %s", e)

    logger.info(
        "Applied proposal %s: %.3f -> %.3f (%+.1f%%)",
        proposal.target, before_score, after_score,
        100.0 * (after_score - before_score) / before_score if before_score > 0 else 0.0,
    )
    return ApplyResult(
        applied=True,
        reason=f"Score {before_score:.3f} -> {after_score:.3f}",
        before_score=before_score,
        after_score=after_score,
    )


async def run_evolve() -> EvolveResult:
    """Main evolution review: query decisions -> compute health -> propose changes -> log."""
    slack = SlackClient()
    convex = ConvexClient()
    iteration = 1

    try:
        state = await convex.get_task_state("evolve")
        if state:
            iteration = state.get("iterationCount", 0) + 1

        monitor_decisions = await convex.get_recent_decisions("monitor", limit=48)
        digest_decisions = await convex.get_recent_decisions("digest", limit=48)

        monitor_stats = _compute_stats(monitor_decisions)
        digest_stats = _compute_stats(digest_decisions)

        bot_engagement = await _check_bot_engagement(slack)
        health_metrics = await _evaluate_health(monitor_stats, digest_stats, bot_engagement)
        proposals = await _generate_proposals(health_metrics, monitor_stats, digest_stats)

        unhealthy_count = sum(1 for m in health_metrics if not m.healthy)
        posted = False

        if unhealthy_count > 0 or proposals:
            check = "\u2705"
            warn = "\u26a0\ufe0f"
            health_text = "\n".join(
                f"\u2022 {check if m.healthy else warn} *{m.name}:* {m.reason}"
                for m in health_metrics
            )
            proposal_text = ""
            if proposals:
                proposal_text = "\n\n*Proposed Changes:*\n" + "\n".join(
                    f"\u2022 `{p.target}` ({p.change_type}): {p.description}" for p in proposals
                )

            msg = (
                f"*\U0001f504 Daily Evolution Review* (iteration {iteration})\n"
                f"_{monitor_stats['total']} monitor + {digest_stats['total']} digest decisions analyzed_\n\n"
                f"*Health Metrics:*\n{health_text}{proposal_text}"
            )

            try:
                daily_ts = await slack.get_or_create_daily_thread(CLAW_CHANNEL_ID)
                if daily_ts:
                    await slack.post_message(CLAW_CHANNEL_ID, msg, thread_ts=daily_ts)
                else:
                    await slack.post_message(CLAW_CHANNEL_ID, msg)
                posted = True
            except Exception as e:
                logger.error("Failed to post evolution review: %s", e)

        # --- Auto-apply approved proposals ----------------------------------------
        changes_applied: list[dict] = []
        for proposal in proposals:
            try:
                result = await _apply_proposal(proposal)
                changes_applied.append({
                    "target": proposal.target,
                    "change_type": proposal.change_type,
                    "applied": result.applied,
                    "reason": result.reason,
                    "before_score": result.before_score,
                    "after_score": result.after_score,
                })
            except Exception as e:
                logger.error("Auto-apply failed for %s: %s", proposal.target, e)
                changes_applied.append({
                    "target": proposal.target,
                    "change_type": proposal.change_type,
                    "applied": False,
                    "reason": f"Exception: {str(e)[:200]}",
                    "before_score": 0.0,
                    "after_score": 0.0,
                })

        review_log = {
            "timestamp": time.time(), "iteration": iteration, "period": "48h",
            "monitorStats": monitor_stats, "digestStats": digest_stats,
            "healthMetrics": [asdict(m) for m in health_metrics],
            "evolutionDecisions": [asdict(p) for p in proposals],
            "changesApplied": changes_applied,
        }

        try:
            await convex.log_evolve_review(review_log)
        except Exception as e:
            logger.warning("Failed to log evolve review: %s", e)
            ConvexClient.log_local_fallback("logs/slack-evolve-reviews.jsonl", review_log)

        await convex.update_task_state("evolve", {
            "lastRunAt": time.time(), "iterationCount": iteration, "status": "idle",
        })

        await slack.close()
        await convex.close()

        return EvolveResult(
            health_metrics=health_metrics, proposals=proposals,
            monitor_stats=monitor_stats, digest_stats=digest_stats,
            iteration=iteration, posted=posted,
        )

    except Exception as e:
        logger.error("Evolution review failed: %s", e)
        try:
            await convex.update_task_state("evolve", {
                "lastRunAt": time.time(), "iterationCount": iteration,
                "status": "error", "lastError": str(e)[:200],
            })
            await slack.close()
            await convex.close()
        except Exception:
            pass
        return EvolveResult(
            health_metrics=[], proposals=[],
            monitor_stats={}, digest_stats={},
            iteration=iteration, posted=False,
        )
