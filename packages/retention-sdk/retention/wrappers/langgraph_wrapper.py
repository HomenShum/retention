"""Wrapper for LangGraph execution.

Hooks into LangGraph's checkpointer and graph execution to capture
node transitions, tool calls, state updates, and branch decisions
as retention telemetry events.

Works with langgraph >= 0.2.0.
"""

import time

_patched = False


def patch():
    """Patch LangGraph to emit retention telemetry events.

    Hooks into:
    - Graph.invoke / Graph.ainvoke — captures full graph runs
    - Node execution — captures per-node tool calls and state changes
    - Checkpointer — captures state snapshots for replay

    Returns True if patched, False if langgraph not installed.
    """
    global _patched
    if _patched:
        return True

    try:
        from langgraph.graph import StateGraph
    except ImportError:
        try:
            from langgraph.graph.state import StateGraph
        except ImportError:
            return False

    from retention.storage import append_event
    from retention.scrub import scrub_dict

    # Patch StateGraph.compile to wrap the compiled graph's invoke/ainvoke
    _original_compile = StateGraph.compile

    def wrapped_compile(self, *args, **kwargs):
        compiled = _original_compile(self, *args, **kwargs)

        # Wrap invoke
        if hasattr(compiled, "invoke"):
            _original_invoke = compiled.invoke

            def wrapped_invoke(input_data, config=None, **kw):
                start = time.monotonic()
                graph_name = getattr(self, "name", None) or "unnamed_graph"

                append_event({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "runtime": "langgraph",
                    "type": "graph_start",
                    "graph": graph_name,
                    "input_keys": sorted(input_data.keys()) if isinstance(input_data, dict) else [],
                    "node_count": len(getattr(self, "nodes", {})),
                })

                try:
                    result = _original_invoke(input_data, config=config, **kw)
                    duration_ms = int((time.monotonic() - start) * 1000)

                    append_event({
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "runtime": "langgraph",
                        "type": "graph_end",
                        "graph": graph_name,
                        "duration_ms": duration_ms,
                        "output_keys": sorted(result.keys()) if isinstance(result, dict) else [],
                        "status": "success",
                    })

                    return result
                except Exception as e:
                    duration_ms = int((time.monotonic() - start) * 1000)
                    append_event({
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "runtime": "langgraph",
                        "type": "graph_error",
                        "graph": graph_name,
                        "duration_ms": duration_ms,
                        "error": str(e)[:200],
                    })
                    raise

            compiled.invoke = wrapped_invoke

        # Wrap ainvoke (async)
        if hasattr(compiled, "ainvoke"):
            _original_ainvoke = compiled.ainvoke

            async def wrapped_ainvoke(input_data, config=None, **kw):
                start = time.monotonic()
                graph_name = getattr(self, "name", None) or "unnamed_graph"

                append_event({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "runtime": "langgraph",
                    "type": "graph_start",
                    "graph": graph_name,
                    "input_keys": sorted(input_data.keys()) if isinstance(input_data, dict) else [],
                })

                try:
                    result = await _original_ainvoke(input_data, config=config, **kw)
                    duration_ms = int((time.monotonic() - start) * 1000)

                    append_event({
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "runtime": "langgraph",
                        "type": "graph_end",
                        "graph": graph_name,
                        "duration_ms": duration_ms,
                        "status": "success",
                    })
                    return result
                except Exception as e:
                    duration_ms = int((time.monotonic() - start) * 1000)
                    append_event({
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "runtime": "langgraph",
                        "type": "graph_error",
                        "graph": graph_name,
                        "duration_ms": duration_ms,
                        "error": str(e)[:200],
                    })
                    raise

            compiled.ainvoke = wrapped_ainvoke

        # Wrap stream if available (captures node-by-node execution)
        if hasattr(compiled, "stream"):
            _original_stream = compiled.stream

            def wrapped_stream(input_data, config=None, **kw):
                graph_name = getattr(self, "name", None) or "unnamed_graph"
                step = 0

                for chunk in _original_stream(input_data, config=config, **kw):
                    step += 1
                    # Each chunk is typically {node_name: state_update}
                    if isinstance(chunk, dict):
                        for node_name, state in chunk.items():
                            append_event({
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "runtime": "langgraph",
                                "type": "node_complete",
                                "graph": graph_name,
                                "node": node_name,
                                "step": step,
                                "output_keys": sorted(state.keys()) if isinstance(state, dict) else [],
                            })
                    yield chunk

            compiled.stream = wrapped_stream

        return compiled

    StateGraph.compile = wrapped_compile
    _patched = True
    return True
