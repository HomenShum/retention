"""Wrapper for LangChain via callback handlers.

Registers a global callback handler that logs tool start/end events.
Works with both langchain and langchain-core.
"""

import time

_handler_registered = False


def patch():
    """Register an retention callback handler with LangChain.

    Returns True if registered successfully, False if langchain is not installed.
    """
    global _handler_registered
    if _handler_registered:
        return True

    try:
        from langchain_core.callbacks import BaseCallbackHandler
        from langchain_core.callbacks.manager import CallbackManager
    except ImportError:
        try:
            from langchain.callbacks.base import BaseCallbackHandler
            from langchain.callbacks.manager import CallbackManager
        except ImportError:
            return False

    class AttritionHandler(BaseCallbackHandler):
        """LangChain callback handler that emits retention telemetry events."""

        def __init__(self):
            self._tool_starts = {}  # run_id -> start_time

        def on_tool_start(self, serialized, input_str, *, run_id=None, **kwargs):
            """Log when a tool starts executing."""
            from retention.storage import append_event
            from retention.scrub import scrub_value

            tool_name = serialized.get("name", "unknown") if isinstance(serialized, dict) else "unknown"
            self._tool_starts[str(run_id)] = time.monotonic()

            scrubbed_input = scrub_value("input", input_str)
            append_event({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "runtime": "langchain",
                "type": "tool_start",
                "tool": tool_name,
                "input_preview": scrubbed_input,
            })

        def on_tool_end(self, output, *, run_id=None, **kwargs):
            """Log when a tool finishes executing."""
            from retention.storage import append_event

            start = self._tool_starts.pop(str(run_id), None)
            duration_ms = int((time.monotonic() - start) * 1000) if start else None

            append_event({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "runtime": "langchain",
                "type": "tool_end",
                "duration_ms": duration_ms,
            })

        def on_tool_error(self, error, *, run_id=None, **kwargs):
            """Log when a tool errors."""
            from retention.storage import append_event

            start = self._tool_starts.pop(str(run_id), None)
            duration_ms = int((time.monotonic() - start) * 1000) if start else None

            append_event({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "runtime": "langchain",
                "type": "tool_error",
                "error": str(error)[:200],
                "duration_ms": duration_ms,
            })

    # Register the handler globally
    try:
        # LangChain >= 0.2: use set_handler
        import langchain_core.callbacks
        handler = AttritionHandler()
        if hasattr(langchain_core.callbacks, "set_handler"):
            langchain_core.callbacks.set_handler(handler)
        _handler_registered = True
        return True
    except (ImportError, AttributeError):
        pass

    # Fallback: try to add to default callbacks
    try:
        import langchain
        handler = AttritionHandler()
        if hasattr(langchain, "callbacks"):
            # Store reference so it doesn't get GC'd
            langchain._retention_handler = handler
        _handler_registered = True
        return True
    except (ImportError, AttributeError):
        return False
