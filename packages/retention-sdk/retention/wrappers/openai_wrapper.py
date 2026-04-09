"""Wrapper for the OpenAI Python SDK.

Monkey-patches openai.resources.chat.completions.Completions.create to
log tool_call events from chat completion responses.
"""

import json
import time

_original_create = None
_original_acreate = None


def patch():
    """Patch OpenAI's Completions.create to emit telemetry events.

    Returns True if patched successfully, False if openai is not installed.
    """
    global _original_create, _original_acreate
    try:
        from openai.resources.chat.completions import Completions

        if _original_create is not None:
            return True  # Already patched

        _original_create = Completions.create

        def wrapped_create(self, *args, **kwargs):
            start = time.monotonic()
            result = _original_create(self, *args, **kwargs)
            duration_ms = int((time.monotonic() - start) * 1000)
            try:
                _log_openai_response(result, kwargs, duration_ms)
            except Exception:
                pass  # Never crash the host app
            return result

        Completions.create = wrapped_create

        # Also patch async create if available
        try:
            from openai.resources.chat.completions import AsyncCompletions
            _original_acreate = AsyncCompletions.create

            async def wrapped_acreate(self, *args, **kwargs):
                start = time.monotonic()
                result = await _original_acreate(self, *args, **kwargs)
                duration_ms = int((time.monotonic() - start) * 1000)
                try:
                    _log_openai_response(result, kwargs, duration_ms)
                except Exception:
                    pass
                return result

            AsyncCompletions.create = wrapped_acreate
        except (ImportError, AttributeError):
            pass

        return True
    except ImportError:
        return False


def _log_openai_response(response, kwargs, duration_ms=None):
    """Extract tool calls from an OpenAI chat completion and log them."""
    from retention.storage import append_event

    model = kwargs.get("model", "unknown")

    for choice in getattr(response, "choices", []):
        msg = getattr(choice, "message", None)
        if msg and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                try:
                    args_dict = json.loads(tc.function.arguments or "{}")
                    keys = sorted(args_dict.keys())
                except (json.JSONDecodeError, AttributeError):
                    keys = []

                append_event({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "runtime": "openai",
                    "type": "tool_call",
                    "tool": tc.function.name,
                    "keys": keys,
                    "model": model,
                    "duration_ms": duration_ms,
                })
