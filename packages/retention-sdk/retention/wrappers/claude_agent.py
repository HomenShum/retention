"""Wrapper for Claude Agent SDK (claude-agent-sdk / claude_agent_sdk).

Hooks into the Claude Agent SDK's tool execution pipeline to capture
tool calls, results, and agent lifecycle events.
"""

import time

_patched = False


def patch():
    """Patch Claude Agent SDK to emit telemetry events.

    Returns True if patched successfully, False if the SDK is not installed.
    """
    global _patched
    if _patched:
        return True

    # Try claude_agent_sdk (the official package name may vary)
    try:
        import claude_agent_sdk
    except ImportError:
        try:
            import claude_code_sdk as claude_agent_sdk
        except ImportError:
            return False

    # Patch the tool execution hook if available
    if hasattr(claude_agent_sdk, "ToolExecutor"):
        _patch_tool_executor(claude_agent_sdk.ToolExecutor)
        _patched = True
        return True

    # Fallback: try to hook into the message handler
    if hasattr(claude_agent_sdk, "Client"):
        _patch_client(claude_agent_sdk.Client)
        _patched = True
        return True

    # SDK exists but no known hook point
    _patched = True
    return True


def _patch_tool_executor(executor_cls):
    """Patch a ToolExecutor class to emit events on execute()."""
    original_execute = getattr(executor_cls, "execute", None)
    if original_execute is None:
        return

    def wrapped_execute(self, *args, **kwargs):
        from retention.storage import append_event

        start = time.monotonic()
        try:
            result = original_execute(self, *args, **kwargs)
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            append_event({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "runtime": "claude_agent",
                "type": "tool_error",
                "tool": getattr(self, "name", "unknown"),
                "error": str(e)[:200],
                "duration_ms": duration_ms,
            })
            raise

        duration_ms = int((time.monotonic() - start) * 1000)
        append_event({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "runtime": "claude_agent",
            "type": "tool_call",
            "tool": getattr(self, "name", "unknown"),
            "duration_ms": duration_ms,
        })
        return result

    executor_cls.execute = wrapped_execute


def _patch_client(client_cls):
    """Patch a Client class to emit events on send_message()."""
    original_send = getattr(client_cls, "send_message", None)
    if original_send is None:
        return

    def wrapped_send(self, *args, **kwargs):
        from retention.storage import append_event

        append_event({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "runtime": "claude_agent",
            "type": "message_send",
        })
        return original_send(self, *args, **kwargs)

    client_cls.send_message = wrapped_send
