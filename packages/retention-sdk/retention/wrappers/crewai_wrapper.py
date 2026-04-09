"""Wrapper for CrewAI.

Patches CrewAI's Tool class to emit telemetry on tool execution.
CrewAI uses a @tool decorator that creates Tool instances -- we patch
the _run method on the base Tool class to capture all tool invocations.
"""

import time

_original_run = None


def patch():
    """Patch CrewAI's Tool._run to emit telemetry events.

    Returns True if patched successfully, False if crewai is not installed.
    """
    global _original_run
    try:
        from crewai.tools import BaseTool
    except ImportError:
        try:
            from crewai_tools import BaseTool
        except ImportError:
            return False

    if _original_run is not None:
        return True  # Already patched

    _original_run = BaseTool._run

    def wrapped_run(self, *args, **kwargs):
        from retention.storage import append_event
        from retention.scrub import scrub_dict

        start = time.monotonic()
        try:
            result = _original_run(self, *args, **kwargs)
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            append_event({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "runtime": "crewai",
                "type": "tool_error",
                "tool": getattr(self, "name", "unknown"),
                "error": str(e)[:200],
                "duration_ms": duration_ms,
            })
            raise

        duration_ms = int((time.monotonic() - start) * 1000)
        scrubbed = scrub_dict(kwargs) if kwargs else {}
        append_event({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "runtime": "crewai",
            "type": "tool_call",
            "tool": getattr(self, "name", "unknown"),
            "keys": sorted(scrubbed.keys()),
            "duration_ms": duration_ms,
        })
        return result

    BaseTool._run = wrapped_run
    return True
