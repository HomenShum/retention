"""Privacy scrubbing for retention.sh — log structure, not secrets.

Every tool call event passes through scrub_event() before writing to disk.
The goal: enough signal for pattern detection + savings insights, zero
exposure of sensitive content.

Rules:
  - Tool names: always kept
  - Input VALUES: scrubbed to structural signals (file ext, command binary, domain)
  - Input KEYS: kept (they describe shape, not content)
  - Outputs: NEVER logged
  - Sensitive fields (password, secret, key, token, credential): always REDACTED
  - Duration, token counts: always kept (numeric, not sensitive)
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

# Keys whose values are ALWAYS redacted regardless of tool
_SENSITIVE_KEYS = re.compile(
    r"(password|secret|key|token|credential|authorization|auth|bearer|api_key|apikey|"
    r"private|session_id|cookie|jwt|access_token|refresh_token|ssn|credit_card)",
    re.IGNORECASE,
)

# Env-var-like patterns in strings
_ENV_PATTERN = re.compile(r"(sk-[a-zA-Z0-9]{10,}|ghp_[a-zA-Z0-9]+|xoxb-[a-zA-Z0-9-]+)")


def scrub_event(event: dict[str, Any]) -> dict[str, Any]:
    """Scrub a tool call event for privacy-safe storage.

    Modifies event in-place and returns it.
    """
    tool_name = event.get("tool_name", "")

    # Scrub tool_input values
    tool_input = event.get("tool_input")
    if isinstance(tool_input, dict):
        event["tool_input"] = _scrub_input(tool_name, tool_input)

    # NEVER keep tool output
    event.pop("tool_output", None)
    event.pop("tool_output_preview", None)

    # Scrub input_summary if present
    if "input_summary" in event:
        event["input_summary"] = "[scrubbed]"

    return event


def _scrub_input(tool_name: str, inp: dict[str, Any]) -> dict[str, str]:
    """Scrub input dict — keep structure, remove content."""
    scrubbed: dict[str, str] = {}

    for key, value in inp.items():
        # Always redact sensitive keys
        if _SENSITIVE_KEYS.search(key):
            scrubbed[key] = "[REDACTED]"
            continue

        val_str = str(value)

        # Check for embedded secrets in values
        if _ENV_PATTERN.search(val_str):
            scrubbed[key] = "[REDACTED]"
            continue

        # Apply tool-specific scrubbing
        scrubbed[key] = _scrub_value(tool_name, key, val_str)

    return scrubbed


def _scrub_value(tool_name: str, key: str, value: str) -> str:
    """Scrub a single input value based on tool type and key name."""

    # ── File paths → extension + depth + filename hash ──
    if key in ("file_path", "path", "file", "filename", "filepath"):
        return _scrub_path(value)

    # ── Bash commands → binary + subcommand only ──
    if tool_name == "Bash" and key in ("command", "cmd"):
        return _scrub_command(value)

    # ── Search queries → word count + topic hash ──
    if key in ("query", "search", "q", "prompt", "question"):
        return _scrub_query(value)

    # ── URLs → domain + path depth ──
    if key in ("url", "uri", "href", "endpoint", "base_url"):
        return _scrub_url(value)

    # ── Code/text content → length only ──
    if key in ("content", "text", "body", "data", "code", "expression",
               "old_string", "new_string", "new_str", "old_str",
               "description", "instructions", "system"):
        return f"[{len(value)} chars]"

    # ── Pattern/regex → length only ──
    if key in ("pattern", "regex", "selector", "css_selector"):
        return f"[pattern:{len(value)}c]"

    # ── Numeric values → keep as-is ──
    if value.replace(".", "").replace("-", "").isdigit():
        return value

    # ── Boolean → keep ──
    if value.lower() in ("true", "false"):
        return value

    # ── Short enum-like values → keep (likely a mode/type selector) ──
    if len(value) <= 20 and value.replace("_", "").replace("-", "").isalnum():
        return value

    # ── Everything else → length + type hint ──
    return f"[{len(value)} chars]"


def _scrub_path(path: str) -> str:
    """Extract extension + depth + hashed filename."""
    try:
        p = PurePosixPath(path)
        ext = p.suffix or ".none"
        depth = len(p.parts) - 1
        name_hash = hashlib.md5(p.name.encode()).hexdigest()[:6]
        return f"*{ext}:d{depth}:{name_hash}"
    except Exception:
        return "[path]"


def _scrub_command(cmd: str) -> str:
    """Extract command binary + subcommand, drop arguments."""
    # Split on chain operators, take last meaningful segment
    segments = [s.strip() for s in re.split(r"&&|;|\|\|", cmd)]
    skip = {"cd", "export", "source", "sleep", "echo", "true", "false"}

    for segment in reversed(segments):
        parts = segment.split()
        if not parts:
            continue
        binary = parts[0].split("/")[-1]
        if binary in skip:
            continue

        # Known commands — keep subcommand
        known = {"git", "npm", "npx", "python3", "python", "pip", "curl",
                 "tsc", "uvicorn", "pytest", "node", "docker", "make",
                 "cargo", "go", "rustc", "ruby", "java", "mvn", "gradle"}
        if binary in known and len(parts) > 1:
            sub = parts[1].split("/")[-1]
            if sub and not sub.startswith("-"):
                return f"{binary} {sub}"
            return binary
        return binary

    return "[cmd]"


def _scrub_query(query: str) -> str:
    """Replace query with word count + topic hash."""
    words = query.split()
    word_count = len(words)
    # Topic hash — stable across same queries for pattern detection
    topic_hash = hashlib.md5(query.lower().strip().encode()).hexdigest()[:8]
    return f"[{word_count}w:{topic_hash}]"


def _scrub_url(url: str) -> str:
    """Extract domain + path depth, drop query params and fragments."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path.split("/")[0]
        # Count meaningful path segments
        path_parts = [p for p in parsed.path.split("/") if p]
        depth = len(path_parts)
        return f"{domain}:d{depth}"
    except Exception:
        return "[url]"
