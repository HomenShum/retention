"""YouTube transcript extraction tool for the OpenClaw agent system.

Extracts captions/transcripts from YouTube videos so agents can
analyze video content. Uses youtube-transcript-api (no API key needed).

This is a self-evolution example: the agent system identified it
couldn't process YouTube links shared in Slack, so this tool was
created to expand its own capability surface.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from various URL formats."""
    patterns = [
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})",
        r"^([a-zA-Z0-9_-]{11})$",  # bare video ID
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


async def fetch_youtube_transcript(
    url: str,
    languages: list[str] | None = None,
    max_chars: int = 15000,
) -> dict[str, Any]:
    """Fetch transcript from a YouTube video.

    Args:
        url: YouTube URL or video ID
        languages: Preferred languages (default: ["en"])
        max_chars: Max transcript length to return

    Returns:
        Dict with video_id, title (if available), transcript text,
        duration, and language.
    """
    if languages is None:
        languages = ["en"]

    video_id = extract_video_id(url)
    if not video_id:
        return {"error": f"Could not extract video ID from: {url}"}

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api.formatters import TextFormatter

        ytt_api = YouTubeTranscriptApi()
        transcript = ytt_api.fetch(video_id, languages=languages)

        # Format as plain text
        formatter = TextFormatter()
        text = formatter.format_transcript(transcript)

        # Calculate duration from last entry
        if transcript:
            last = transcript[-1]
            duration_s = int(last.start + last.duration)
            duration_str = f"{duration_s // 60}:{duration_s % 60:02d}"
        else:
            duration_str = "unknown"

        # Truncate if needed
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[... transcript truncated]"

        return {
            "video_id": video_id,
            "url": f"https://youtube.com/watch?v={video_id}",
            "transcript": text,
            "duration": duration_str,
            "language": languages[0],
            "char_count": len(text),
        }

    except ImportError:
        return {
            "error": "youtube-transcript-api not installed. Run: pip install youtube-transcript-api",
            "video_id": video_id,
        }
    except Exception as e:
        error_msg = str(e)
        if "TranscriptsDisabled" in error_msg or "No transcripts" in error_msg:
            return {
                "error": f"No captions available for this video ({video_id}). "
                         "The video may not have subtitles enabled.",
                "video_id": video_id,
            }
        logger.error("YouTube transcript fetch failed: %s", e)
        return {
            "error": f"Failed to fetch transcript: {error_msg[:200]}",
            "video_id": video_id,
        }
