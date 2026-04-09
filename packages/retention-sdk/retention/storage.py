"""Append-only JSONL storage for retention telemetry events.

Events are written to ~/.retention/activity.jsonl by default.
The log path can be changed via set_log_path() or configure(log_path=...).
"""

import json
import pathlib

_LOG_PATH = pathlib.Path.home() / ".retention" / "activity.jsonl"


def set_log_path(path: str):
    """Override the default log file location."""
    global _LOG_PATH
    _LOG_PATH = pathlib.Path(path)


def get_log_path() -> pathlib.Path:
    """Return the current log file path."""
    return _LOG_PATH


def append_event(event: dict):
    """Append one event to the activity JSONL log.

    Creates parent directories if they don't exist.
    Silently drops events that fail to serialize.
    """
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except (OSError, TypeError, ValueError):
        # Never crash the host application for telemetry failures
        pass


def read_events(limit: int = 1000) -> list:
    """Read the last `limit` events from the log. Returns empty list if file missing."""
    if not _LOG_PATH.exists():
        return []
    events = []
    try:
        with open(_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return events[-limit:]
    except OSError:
        return []
