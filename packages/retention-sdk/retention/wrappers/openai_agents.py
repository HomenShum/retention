"""Wrapper for OpenAI Agents SDK (openai-agents / agents package).

Implements an RetentionProcessor that hooks into the Agents SDK tracing
system to capture tool calls, handoffs, and agent runs.
"""

import time

_processor_registered = False


def patch():
    """Register an retention tracing processor with the OpenAI Agents SDK.

    Returns True if registered successfully, False if agents SDK is not installed.
    """
    global _processor_registered
    if _processor_registered:
        return True

    try:
        from agents.tracing import TracingProcessor, add_trace_processor
    except ImportError:
        return False

    class RetentionProcessor(TracingProcessor):
        """Tracing processor that emits retention telemetry for agent SDK events."""

        def on_trace_start(self, trace):
            """Called when a new trace (agent run) begins."""
            from retention.storage import append_event

            append_event({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "runtime": "openai_agents",
                "type": "trace_start",
                "trace_id": getattr(trace, "trace_id", None),
                "name": getattr(trace, "name", None),
            })

        def on_trace_end(self, trace):
            """Called when a trace completes."""
            from retention.storage import append_event

            append_event({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "runtime": "openai_agents",
                "type": "trace_end",
                "trace_id": getattr(trace, "trace_id", None),
            })

        def on_span_start(self, span):
            """Called when a span (tool call, LLM call, handoff) begins."""
            from retention.storage import append_event

            span_type = getattr(span, "span_type", "unknown")
            span_data = getattr(span, "span_data", None)

            event = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "runtime": "openai_agents",
                "type": "span_start",
                "span_type": span_type,
                "span_id": getattr(span, "span_id", None),
            }

            # Extract tool name if this is a function/tool span
            if span_data and hasattr(span_data, "name"):
                event["tool"] = span_data.name
            if span_data and hasattr(span_data, "input"):
                event["keys"] = sorted(span_data.input.keys()) if isinstance(span_data.input, dict) else []

            append_event(event)

        def on_span_end(self, span):
            """Called when a span completes."""
            from retention.storage import append_event

            append_event({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "runtime": "openai_agents",
                "type": "span_end",
                "span_type": getattr(span, "span_type", "unknown"),
                "span_id": getattr(span, "span_id", None),
            })

        def shutdown(self):
            """Cleanup on processor shutdown."""
            pass

        def force_flush(self):
            """Force flush any buffered events."""
            pass

    try:
        add_trace_processor(RetentionProcessor())
        _processor_registered = True
        return True
    except Exception:
        return False
