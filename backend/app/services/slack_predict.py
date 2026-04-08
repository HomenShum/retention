"""Prediction service — MiroFish-inspired scenario simulation for Slack.

Adapts the MiroFish pattern (knowledge graph -> agent simulation -> report)
into a lightweight prediction service for retention.sh. Instead of running
full OASIS social simulations, uses LLM-powered multi-perspective analysis:

1. Extract entities and relationships from the scenario context
2. Generate agent perspectives (inspired by Agency role templates)
3. Simulate interactions between perspectives
4. Synthesize a prediction report with confidence signals

Runs on-demand when the monitor detects a decision support (Type E) or
timeline awareness (Type H) opportunity with sufficient complexity.

Architecture reference: MiroFish uses Zep for knowledge graphs + CAMEL-OASIS
for social simulation. We adapt this pattern using our existing LLM judge +
institutional memory + agency roles, keeping it lightweight and Slack-native.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .slack_client import SlackClient, CLAW_CHANNEL_ID
from .convex_client import ConvexClient
from .slack_memory import search_memory
from .agency_roles import ROLE_REGISTRY, AgencyRole
from .llm_judge import call_responses_api

logger = logging.getLogger(__name__)


@dataclass
class PerspectiveResult:
    """A single agent perspective on a scenario."""

    role_id: str
    role_name: str
    assessment: str
    confidence: str  # "high", "medium", "low"
    key_risks: list[str]
    key_opportunities: list[str]


@dataclass
class PredictionReport:
    """The synthesized prediction output."""

    scenario: str
    perspectives: list[PerspectiveResult]
    synthesis: str
    consensus_points: list[str]
    disagreements: list[str]
    recommended_action: str
    confidence_level: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class PredictResult:
    """Result of a prediction run."""

    posted: bool
    scenario: str
    perspectives_count: int
    confidence: str
    summary: str


# ------------------------------------------------------------------
# Perspective generation — simulate multi-agent analysis
# ------------------------------------------------------------------

async def _generate_perspective(
    role: AgencyRole,
    scenario: str,
    context: str,
) -> Optional[PerspectiveResult]:
    """Generate one agent's perspective on a scenario.

    This is the MiroFish-inspired pattern: instead of running a full social
    simulation, we have each Agency role independently analyze the scenario,
    then look for convergence and divergence in a separate synthesis step.
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None

    prompt = f"""You are the {role.name} ({role.division} division).

{role.persona}

SCENARIO TO ANALYZE:
{scenario}

ADDITIONAL CONTEXT:
{context}

Analyze this scenario from your role's perspective. Return a JSON object:
{{
  "assessment": "2-3 sentence analysis from your perspective",
  "confidence": "high|medium|low",
  "key_risks": ["risk 1", "risk 2"],
  "key_opportunities": ["opportunity 1", "opportunity 2"]
}}

Rules:
- Stay in character for your role
- Be specific and actionable
- If you lack data for high confidence, say so honestly
- Max 2 risks and 2 opportunities

Return ONLY the JSON object."""

    try:
        content = await call_responses_api(prompt, task="swarm_role_response", timeout_s=30)

        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        result = json.loads(content)
        return PerspectiveResult(
            role_id=role.id,
            role_name=role.name,
            assessment=result.get("assessment", ""),
            confidence=result.get("confidence", "low"),
            key_risks=result.get("key_risks", [])[:2],
            key_opportunities=result.get("key_opportunities", [])[:2],
        )

    except Exception as e:
        logger.error("Perspective generation failed for %s: %s", role.name, e)
        return None


# ------------------------------------------------------------------
# Synthesis — combine perspectives into a prediction report
# ------------------------------------------------------------------

async def _synthesize_prediction(
    scenario: str,
    perspectives: list[PerspectiveResult],
) -> Optional[PredictionReport]:
    """Synthesize multiple perspectives into a unified prediction report.

    This is the "swarm prediction" step from MiroFish, adapted:
    instead of averaging social simulation outputs, we identify
    convergence and divergence across role perspectives.
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None

    perspectives_text = "\n\n".join(
        f"**{p.role_name}** (confidence: {p.confidence}):\n"
        f"Assessment: {p.assessment}\n"
        f"Risks: {', '.join(p.key_risks)}\n"
        f"Opportunities: {', '.join(p.key_opportunities)}"
        for p in perspectives
    )

    prompt = f"""Synthesize these {len(perspectives)} agent perspectives on a scenario into a prediction report.

SCENARIO: {scenario}

PERSPECTIVES:
{perspectives_text}

Return a JSON object:
{{
  "synthesis": "3-4 sentence executive summary in Calculus Made Easy style (plain English analogy first, then specifics)",
  "consensus_points": ["point 1", "point 2"],
  "disagreements": ["disagreement 1"],
  "recommended_action": "One clear recommended next step",
  "confidence_level": "high|medium|low"
}}

Rules:
- If all perspectives agree, confidence = high
- If majority agree but with caveats, confidence = medium
- If perspectives diverge significantly, confidence = low
- The synthesis should start with a plain English analogy
- Max 3 consensus points, max 2 disagreements

Return ONLY the JSON object."""

    try:
        content = await call_responses_api(prompt, task="deep_sim_synthesis", timeout_s=30)

        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        result = json.loads(content)
        return PredictionReport(
            scenario=scenario,
            perspectives=perspectives,
            synthesis=result.get("synthesis", ""),
            consensus_points=result.get("consensus_points", []),
            disagreements=result.get("disagreements", []),
            recommended_action=result.get("recommended_action", ""),
            confidence_level=result.get("confidence_level", "low"),
        )

    except Exception as e:
        logger.error("Prediction synthesis failed: %s", e)
        return None


# ------------------------------------------------------------------
# Report formatting for Slack
# ------------------------------------------------------------------

def _format_report_for_slack(report: PredictionReport) -> str:
    """Format a prediction report as a Slack message."""
    confidence_emoji = {
        "high": "\u2705",
        "medium": "\u26a0\ufe0f",
        "low": "\u2753",
    }.get(report.confidence_level, "\u2753")

    # Perspectives summary
    perspective_lines = []
    for p in report.perspectives:
        conf = {"high": "\u2705", "medium": "\u26a0\ufe0f", "low": "\u2753"}.get(p.confidence, "\u2753")
        perspective_lines.append(
            f"\u2022 *{p.role_name}* {conf}: {p.assessment[:120]}"
        )

    perspectives_text = "\n".join(perspective_lines)

    # Consensus
    consensus_text = "\n".join(f"\u2022 {c}" for c in report.consensus_points[:3])

    # Disagreements
    disagree_text = ""
    if report.disagreements:
        disagree_text = "\n\n*Where perspectives diverge:*\n" + "\n".join(
            f"\u2022 {d}" for d in report.disagreements[:2]
        )

    msg = (
        f"*\U0001f52e Prediction Report* {confidence_emoji} ({report.confidence_level} confidence)\n"
        f"_Scenario: {report.scenario[:100]}_\n\n"
        f"{report.synthesis}\n\n"
        f"*Agent Perspectives ({len(report.perspectives)}):*\n"
        f"{perspectives_text}"
        f"\n\n*Consensus:*\n{consensus_text}"
        f"{disagree_text}\n\n"
        f"*Recommended action:* {report.recommended_action}\n\n"
        f"_Multi-perspective analysis inspired by MiroFish swarm prediction_"
    )

    return msg


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------

async def run_prediction(
    scenario: str,
    thread_ts: Optional[str] = None,
    roles: Optional[list[str]] = None,
) -> PredictResult:
    """Run a multi-perspective prediction on a scenario.

    Args:
        scenario: The scenario or question to analyze
        thread_ts: If provided, post the report as a thread reply
        roles: If provided, only use these role IDs. Otherwise uses all 6.

    Returns:
        PredictResult with the outcome
    """
    slack = SlackClient()
    convex = ConvexClient()

    try:
        # Select roles to use
        if roles:
            selected_roles = [
                ROLE_REGISTRY[r] for r in roles if r in ROLE_REGISTRY
            ]
        else:
            selected_roles = list(ROLE_REGISTRY.values())

        if not selected_roles:
            return PredictResult(
                posted=False, scenario=scenario, perspectives_count=0,
                confidence="none", summary="No valid roles selected",
            )

        # Gather context from institutional memory
        memory_results = await search_memory(scenario[:50], convex, limit=3)
        memory_context = "\n".join(
            f"- Prior: {m.get('summary', '')}" for m in memory_results
        ) if memory_results else "(no prior context)"

        # Generate perspectives from each role
        perspectives: list[PerspectiveResult] = []
        for role in selected_roles[:4]:  # Cap at 4 to manage API costs
            perspective = await _generate_perspective(
                role, scenario, memory_context
            )
            if perspective:
                perspectives.append(perspective)

        if not perspectives:
            return PredictResult(
                posted=False, scenario=scenario, perspectives_count=0,
                confidence="none", summary="No perspectives generated",
            )

        # Synthesize into prediction report
        report = await _synthesize_prediction(scenario, perspectives)
        if not report:
            return PredictResult(
                posted=False, scenario=scenario,
                perspectives_count=len(perspectives),
                confidence="low", summary="Synthesis failed",
            )

        # Format and post to Slack
        slack_msg = _format_report_for_slack(report)
        posted = False
        try:
            if thread_ts:
                await slack.post_thread_reply(CLAW_CHANNEL_ID, thread_ts, slack_msg)
            else:
                await slack.post_message(CLAW_CHANNEL_ID, slack_msg)
            posted = True
        except Exception as e:
            logger.error("Failed to post prediction: %s", e)

        await slack.close()
        await convex.close()

        return PredictResult(
            posted=posted, scenario=scenario,
            perspectives_count=len(perspectives),
            confidence=report.confidence_level,
            summary=report.synthesis[:200],
        )

    except Exception as e:
        logger.error("Prediction run failed: %s", e)
        try:
            await slack.close()
            await convex.close()
        except Exception:
            pass
        return PredictResult(
            posted=False, scenario=scenario, perspectives_count=0,
            confidence="error", summary=f"Error: {str(e)[:150]}",
        )
