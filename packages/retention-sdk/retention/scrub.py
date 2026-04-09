"""Privacy scrubber for retention telemetry events.

Matches retention.sh's approach: redact secrets by key name and value prefix,
truncate long values, anonymize file paths.
"""

import pathlib

SENSITIVE_KEYS = {"password", "secret", "key", "token", "credential", "api_key", "auth"}
SECRET_PREFIXES = ("sk-", "ghp-", "xoxb-", "xoxp-", "Bearer ")


def scrub_value(key: str, value) -> str:
    """Scrub a single value based on its key name and value content."""
    key_lower = key.lower()
    if any(s in key_lower for s in SENSITIVE_KEYS):
        return "[REDACTED]"
    val_str = str(value)
    if any(val_str.startswith(p) for p in SECRET_PREFIXES):
        return "[REDACTED]"
    if key in ("file_path", "path"):
        return f"*{pathlib.PurePosixPath(val_str).suffix}"
    if len(val_str) <= 30:
        return val_str
    return f"[{len(val_str)}c]"


def scrub_dict(d) -> dict:
    """Scrub all values in a dict. Returns empty dict for non-dict inputs."""
    if not isinstance(d, dict):
        return {}
    return {k: scrub_value(k, v) for k, v in d.items()}
