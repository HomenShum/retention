"""Wrapper for the Anthropic Python SDK.

Monkey-patches anthropic.resources.messages.Messages.create to
log tool_use content blocks from message responses.
"""

import time

_original_create = None
_original_acreate = None


def patch():
    """Patch Anthropic's Messages.create to emit telemetry events.

    Returns True if patched successfully, False if anthropic is not installed.
    """
    global _original_create, _original_acreate
    try:
        from anthropic.resources.messages import Messages

        if _original_create is not None:
            return True  # Already patched

        _original_create = Messages.create

        def wrapped_create(self, *args, **kwargs):
            start = time.monotonic()
            result = _original_create(self, *args, **kwargs)
            duration_ms = int((time.monotonic() - start) * 1000)
            try:
                _log_anthropic_response(result, kwargs, duration_ms)
            except Exception:
                pass
            return result

        Messages.create = wrapped_create

        # Also patch async create if available
        try:
            from anthropic.resources.messages import AsyncMessages
            _original_acreate = AsyncMessages.create

            async def wrapped_acreate(self, *args, **kwargs):
                start = time.monotonic()
                result = await _original_acreate(self, *args, **kwargs)
                duration_ms = int((time.monotonic() - start) * 1000)
                try:
                    _log_anthropic_response(result, kwargs, duration_ms)
                except Exception:
                    pass
                return result

            AsyncMessages.create = wrapped_acreate
        except (ImportError, AttributeError):
            pass

        return True
    except ImportError:
        return False


def _log_anthropic_response(response, kwargs, duration_ms=None):
    """Extract tool_use blocks from an Anthropic message and log them."""
    from retention.storage import append_event

    model = kwargs.get("model", "unknown")

    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "tool_use":
            tool_name = getattr(block, "name", "unknown")
            tool_input = getattr(block, "input", {})
            keys = sorted(tool_input.keys()) if isinstance(tool_input, dict) else []

            append_event({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "runtime": "anthropic",
                "type": "tool_call",
                "tool": tool_name,
                "keys": keys,
                "model": model,
                "duration_ms": duration_ms,
            })
