"""Slide deck generator for the OpenClaw agent system.

Uses the frontend-slides skill pattern to generate zero-dependency HTML
presentations that can be shared via Slack. Agents can create pitch decks,
status reports, competitive analyses, and deep sim summaries as visual
slide decks.

The generated HTML files are self-contained (no npm, no build tools)
and can be opened in any browser or uploaded to Slack as files.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# Path to the frontend-slides skill assets
SKILL_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".claude", "skills", "frontend-slides"
)

# Style presets from the skill
STYLE_PRESETS = [
    "midnight-aurora",    # Dark, cosmic gradients
    "paper-craft",        # Light, textured, editorial
    "neon-terminal",      # Hacker aesthetic, green-on-black
    "watercolor-wash",    # Soft, artistic, muted
    "brutalist-mono",     # Bold typography, high contrast
    "glass-morphism",     # Frosted glass, translucent layers
]


def _load_skill_asset(filename: str) -> str:
    """Load a skill asset file."""
    path = os.path.join(SKILL_DIR, filename)
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        logger.warning("Skill asset not found: %s", path)
        return ""


async def generate_slide_deck(
    topic: str,
    slides_content: list[dict[str, str]],
    style: str = "brutalist-mono",
    title: str = "",
) -> dict[str, Any]:
    """Generate an HTML slide deck from structured content.

    Args:
        topic: The presentation topic
        slides_content: List of dicts with 'title' and 'body' for each slide
        style: Visual style preset (from STYLE_PRESETS)
        title: Override title (defaults to topic)

    Returns:
        Dict with 'html' (the full HTML string) and 'filename'
    """
    from .llm_judge import call_responses_api

    viewport_css = _load_skill_asset("viewport-base.css")
    html_template = _load_skill_asset("html-template.md")
    animation_patterns = _load_skill_asset("animation-patterns.md")

    # Build slide content description
    slides_desc = "\n".join(
        f"Slide {i+1}: Title: {s.get('title', '')} | Content: {s.get('body', '')}"
        for i, s in enumerate(slides_content)
    )

    prompt = f"""Generate a complete, self-contained HTML presentation file.

TOPIC: {topic}
TITLE: {title or topic}
STYLE: {style}
NUMBER OF SLIDES: {len(slides_content)}

SLIDE CONTENT:
{slides_desc}

REQUIREMENTS:
1. Single HTML file with ALL CSS and JS inline
2. Zero dependencies — no CDN links, no external fonts (use system fonts or embed via base64)
3. Every slide MUST fit exactly in 100vh — no scrolling within slides
4. Use this viewport base CSS:
```css
{viewport_css[:1000]}
```
5. Navigation: arrow keys, click, or swipe to move between slides
6. Include slide counter (e.g., "3/8")
7. Use Slack mrkdwn-compatible formatting in any text meant for Slack
8. Make it visually distinctive — not generic "AI slop"

{f'ANIMATION PATTERNS:{chr(10)}{animation_patterns[:1500]}' if animation_patterns else ''}

Output ONLY the complete HTML file. No explanation, no markdown code fences."""

    try:
        html = await call_responses_api(
            prompt,
            task="compose_response",
            timeout_s=120,
        )

        # Clean up if wrapped in code fences
        if html.startswith("```"):
            html = html.split("```", 2)[1]
            if html.startswith("html"):
                html = html[4:]
            html = html.rsplit("```", 1)[0]
            html = html.strip()

        filename = f"slides-{topic[:30].replace(' ', '-').lower()}-{int(time.time())}.html"

        return {
            "html": html,
            "filename": filename,
            "slide_count": len(slides_content),
            "style": style,
        }

    except Exception as e:
        logger.error("Slide generation failed: %s", e)
        return {"error": str(e), "html": "", "filename": ""}


async def generate_and_post_to_slack(
    topic: str,
    slides_content: list[dict[str, str]],
    channel: str = "C0AM2J4G6S0",
    thread_ts: str | None = None,
    style: str = "brutalist-mono",
) -> dict[str, Any]:
    """Generate a slide deck, host it as a URL, and post a clickable link to Slack.

    The HTML file is saved to the /slides directory (served by FastAPI as
    static files with html=True). Non-technical users just click the link
    in Slack to view the presentation in their browser — no download needed.

    Args:
        topic: Presentation topic
        slides_content: Slide data
        channel: Slack channel ID
        thread_ts: Optional thread to post in
        style: Visual style

    Returns:
        Dict with upload result
    """
    import httpx

    result = await generate_slide_deck(topic, slides_content, style=style)
    if result.get("error") or not result.get("html"):
        return result

    html = result["html"]
    filename = result["filename"]

    # Always save locally — the backend serves /slides as static HTML
    output_dir = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "slides"
    )
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w") as f:
        f.write(html)

    # Build the URL where the slides are hosted
    backend_url = os.getenv("BACKEND_URL", "http://localhost:8000")
    slide_url = f"{backend_url}/slides/{filename}"

    slack_token = os.getenv("SLACK_BOT_TOKEN", "")
    if not slack_token:
        return {"saved_to": filepath, "url": slide_url, "filename": filename, **result}

    # Post a clickable link to Slack (not raw HTML)
    slide_count = result.get("slide_count", len(slides_content))
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            payload: dict[str, Any] = {
                "channel": channel,
                "text": (
                    f"*Slide Deck: {topic}*\n"
                    f"_{slide_count} slides, {style} style_\n\n"
                    f"<{slide_url}|Open Presentation>\n\n"
                    f"Use arrow keys to navigate. Works on any device."
                ),
                "unfurl_links": False,
            }
            if thread_ts:
                payload["thread_ts"] = thread_ts

            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {slack_token}",
                         "Content-Type": "application/json"},
                json=payload,
            )
            d = resp.json()
            return {
                "posted": d.get("ok", False),
                "url": slide_url,
                "filename": filename,
                **result,
            }

    except Exception as e:
        logger.error("Slack post failed: %s", e)
        return {"url": slide_url, "filename": filename, "error": str(e), **result}
