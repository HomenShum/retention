"""Generic manual tracking for any runtime.

Use this when your AI provider doesn't have an auto-patch wrapper,
or when you want to track custom tool calls explicitly.

Usage:
    from retention.wrappers.generic import track_event
    track_event("my_tool", {"query": "hello", "limit": 10})
"""

import time

from retention.scrub import scrub_dict
from retention.storage import append_event


def track_event(tool_name: str, input_data: dict = None, runtime: str = "generic",
                event_type: str = "tool_call", duration_ms: int = None):
    """Manually track a tool call or agent event.

    Args:
        tool_name: Name of the tool or action being tracked.
        input_data: Input arguments (will be scrubbed for privacy).
        runtime: Runtime identifier (default "generic").
        event_type: Event type (default "tool_call").
        duration_ms: Optional execution duration in milliseconds.
    """
    scrubbed = scrub_dict(input_data or {})
    event = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "runtime": runtime,
        "type": event_type,
        "tool": tool_name,
        "keys": sorted(scrubbed.keys()),
        "scrubbed": scrubbed,
    }
    if duration_ms is not None:
        event["duration_ms"] = duration_ms
    append_event(event)


def track_error(tool_name: str, error: str, runtime: str = "generic",
                duration_ms: int = None):
    """Manually track a tool error.

    Args:
        tool_name: Name of the tool that errored.
        error: Error message (truncated to 200 chars).
        runtime: Runtime identifier (default "generic").
        duration_ms: Optional execution duration in milliseconds.
    """
    append_event({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "runtime": runtime,
        "type": "tool_error",
        "tool": tool_name,
        "error": str(error)[:200],
        "duration_ms": duration_ms,
    })
