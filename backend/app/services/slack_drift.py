"""Drift detection — compare commit patterns against investor brief roadmap.

Surfaces when actual development activity diverges from what the investor
brief says the team should be focused on. Runs weekly.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Optional

from .slack_client import SlackClient, CLAW_CHANNEL_ID
from .convex_client import ConvexClient
from .llm_judge import call_responses_api

logger = logging.getLogger(__name__)

# Brief section categories (from strategy_brief.py CONNECTING TO THE BRIEF)
BRIEF_CATEGORIES = [
    "Platform Capabilities",
    "ActionSpan Technology",
    "Quality Assurance",
    "Integration Architecture",
    "User Experience",
    "Financial / Cost",
    "Strategy / Roadmap",
    "Other",
]


@dataclass
class DriftResult:
    posted: bool
    drift_detected: bool
    summary: str
    commit_distribution: dict[str, int]
    brief_focus: str
    iteration: int


async def _categorize_commits(commits: list[dict]) -> dict[str, int]:
    """Use LLM to categorize commits by brief section."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or not commits:
        return {}

    commit_text = "\n".join(
        f"- {c.get('sha', '')[:7]}: {c.get('message', '').split(chr(10))[0][:100]}"
        for c in commits[:30]
    )

    categories_text = ", ".join(BRIEF_CATEGORIES)

    prompt = f"""Categorize each commit into ONE category from: {categories_text}

COMMITS:
{commit_text}

Return a JSON object mapping category name to count of commits.
Example: {{"Platform Capabilities": 5, "Quality Assurance": 3, "Other": 2}}
Only include categories with >0 commits. Return ONLY the JSON, no markdown."""

    try:
        content = await call_responses_api(prompt, task="drift_categorize", timeout_s=20)
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        return json.loads(content)
    except Exception as e:
        logger.error("Commit categorization failed: %s", e)
        return {}


async def _get_brief_roadmap_focus() -> str:
    """Get the current week's focus from the investor brief roadmap."""
    try:
        from ..api.mcp_server import _dispatch_investor_brief
        result = await _dispatch_investor_brief("ta.investor_brief.get_state", {})
        if isinstance(result, str):
            state = json.loads(result)
        elif isinstance(result, dict):
            state = result
        else:
            return "unknown"

        # Try to extract current week focus from sections
        sections = state.get("sections", [])
        for s in sections:
            sid = s.get("id", "")
            if "roadmap" in sid.lower() or "timeline" in sid.lower() or "sprint" in sid.lower():
                return s.get("content", "")[:500]

        return "No specific roadmap section found"
    except Exception as e:
        logger.warning("Failed to get brief roadmap: %s", e)
        return "unknown"


async def _analyze_drift(
    distribution: dict[str, int],
    roadmap_focus: str,
) -> tuple[bool, str]:
    """Use LLM to analyze whether commit distribution aligns with roadmap."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return False, "Unable to analyze — API key not set"

    total = sum(distribution.values())
    dist_text = "\n".join(
        f"  {cat}: {count} commits ({count/total*100:.0f}%)"
        for cat, count in sorted(distribution.items(), key=lambda x: -x[1])
    )

    prompt = f"""Analyze whether the team's recent development activity aligns with the investor brief roadmap.

COMMIT DISTRIBUTION (last week):
{dist_text}

BRIEF ROADMAP FOCUS:
{roadmap_focus}

Answer in JSON:
{{
  "drift_detected": true/false,
  "summary": "One paragraph in Calculus Made Easy style — plain English, start with an analogy, explain what the drift means and whether it's concerning or expected"
}}

Rules:
- Minor drift is normal and expected — only flag significant misalignment
- If 60%+ of commits are in a category NOT mentioned in the roadmap focus, that's drift
- If the roadmap focus area has <10% of commits, that's drift
- Not all drift is bad — sometimes priorities shift for good reasons. Note this.

Return ONLY the JSON."""

    try:
        content = await call_responses_api(prompt, task="drift_categorize", timeout_s=20)
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        result = json.loads(content)
        return bool(result.get("drift_detected")), str(result.get("summary", ""))
    except Exception as e:
        logger.error("Drift analysis failed: %s", e)
        return False, f"Analysis error: {str(e)[:100]}"


async def run_drift_check() -> DriftResult:
    """Main entry: fetch commits -> categorize -> compare to roadmap -> post if drift detected."""
    slack = SlackClient()
    convex = ConvexClient()
    iteration = 1

    try:
        state = await convex.get_task_state("drift")
        if state:
            iteration = state.get("iterationCount", 0) + 1

        # Fetch recent commits
        try:
            from ..api.mcp_server import _dispatch_codebase
            result = await _dispatch_codebase("ta.codebase.recent_commits", {"limit": 30})
            commits = json.loads(result) if isinstance(result, str) else (result if isinstance(result, list) else [])
        except Exception as e:
            logger.warning("Failed to fetch commits for drift: %s", e)
            commits = []

        if not commits:
            logger.info("No commits found — skipping drift check")
            await convex.update_task_state("drift", {
                "lastRunAt": time.time(), "iterationCount": iteration, "status": "idle",
            })
            await slack.close()
            await convex.close()
            return DriftResult(posted=False, drift_detected=False, summary="No commits",
                             commit_distribution={}, brief_focus="", iteration=iteration)

        # Categorize commits
        distribution = await _categorize_commits(commits)

        # Get roadmap focus
        roadmap_focus = await _get_brief_roadmap_focus()

        # Analyze drift
        drift_detected, summary = await _analyze_drift(distribution, roadmap_focus)

        # Post to Slack if drift detected
        posted = False
        if drift_detected:
            dist_text = "\n".join(
                f"• *{cat}:* {count} commits ({count/sum(distribution.values())*100:.0f}%)"
                for cat, count in sorted(distribution.items(), key=lambda x: -x[1])
            )
            slack_msg = (
                f"*Weekly Drift Check*\n"
                f"_{len(commits)} commits analyzed against investor brief roadmap_\n\n"
                f"{summary}\n\n"
                f"*Commit distribution:*\n{dist_text}"
            )
            try:
                daily_ts = await slack.get_or_create_daily_thread(CLAW_CHANNEL_ID)
                if daily_ts:
                    await slack.post_message(CLAW_CHANNEL_ID, slack_msg, thread_ts=daily_ts)
                else:
                    await slack.post_message(CLAW_CHANNEL_ID, slack_msg)
                posted = True
            except Exception as e:
                logger.error("Failed to post drift report: %s", e)

        await convex.update_task_state("drift", {
            "lastRunAt": time.time(), "iterationCount": iteration, "status": "idle",
        })

        await slack.close()
        await convex.close()

        return DriftResult(
            posted=posted, drift_detected=drift_detected, summary=summary,
            commit_distribution=distribution, brief_focus=roadmap_focus[:100],
            iteration=iteration,
        )

    except Exception as e:
        logger.error("Drift check failed: %s", e)
        try:
            await convex.update_task_state("drift", {
                "lastRunAt": time.time(), "iterationCount": iteration,
                "status": "error", "lastError": str(e)[:200],
            })
            await slack.close()
            await convex.close()
        except Exception:
            pass
        return DriftResult(posted=False, drift_detected=False, summary=f"Error: {e}",
                         commit_distribution={}, brief_focus="", iteration=iteration)
