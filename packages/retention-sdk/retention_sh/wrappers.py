"""Provider-specific wrappers for retention.sh SDK.

Each patch_*() function monkey-patches a provider SDK to log tool calls.
All patching is idempotent and safe — calling twice does nothing.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any

logger = logging.getLogger("retention_sh")

_patched: set[str] = set()


def _already_patched(name: str) -> bool:
    if name in _patched:
        return True
    _patched.add(name)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 1. OpenAI SDK (chat completions with tool_use)
# ─────────────────────────────────────────────────────────────────────────────

def patch_openai() -> None:
    """Patch OpenAI SDK to log all chat completions including tool calls."""
    if _already_patched("openai"):
        return

    try:
        import openai
    except ImportError:
        return

    from .core import log_tool_call

    _original_create = openai.resources.chat.completions.Completions.create

    @functools.wraps(_original_create)
    def _patched_create(self: Any, *args: Any, **kwargs: Any) -> Any:
        t0 = time.time()
        model = kwargs.get("model", "unknown")
        tools = kwargs.get("tools", [])
        status = "ok"

        try:
            result = _original_create(self, *args, **kwargs)
        except Exception as e:
            status = "error"
            log_tool_call(
                tool_name="openai.chat.completions.create",
                tool_input={"model": model, "tools_count": len(tools)},
                status=status,
                duration_ms=int((time.time() - t0) * 1000),
                model=model,
                source="openai-sdk",
            )
            raise

        duration_ms = int((time.time() - t0) * 1000)
        usage = getattr(result, "usage", None)
        tokens_in = getattr(usage, "prompt_tokens", 0) if usage else 0
        tokens_out = getattr(usage, "completion_tokens", 0) if usage else 0

        # Log the completion itself
        log_tool_call(
            tool_name="openai.chat.completions.create",
            tool_input={"model": model, "tools_count": len(tools)},
            status=status,
            duration_ms=duration_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=model,
            source="openai-sdk",
        )

        # Log each tool call the model made
        for choice in getattr(result, "choices", []):
            msg = getattr(choice, "message", None)
            if not msg:
                continue
            for tc in getattr(msg, "tool_calls", []) or []:
                fn = getattr(tc, "function", None)
                if fn:
                    try:
                        import json
                        fn_args = json.loads(getattr(fn, "arguments", "{}"))
                    except Exception:
                        fn_args = {"raw": getattr(fn, "arguments", "")}
                    log_tool_call(
                        tool_name=f"openai.tool:{getattr(fn, 'name', 'unknown')}",
                        tool_input=fn_args,
                        duration_ms=0,
                        model=model,
                        source="openai-sdk",
                    )

        return result

    openai.resources.chat.completions.Completions.create = _patched_create  # type: ignore
    logger.debug("retention.sh: patched openai.chat.completions.create")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Anthropic SDK (messages with tool_use)
# ─────────────────────────────────────────────────────────────────────────────

def patch_anthropic() -> None:
    """Patch Anthropic SDK to log all message calls including tool use."""
    if _already_patched("anthropic"):
        return

    try:
        import anthropic
    except ImportError:
        return

    from .core import log_tool_call

    _original_create = anthropic.resources.messages.Messages.create

    @functools.wraps(_original_create)
    def _patched_create(self: Any, *args: Any, **kwargs: Any) -> Any:
        t0 = time.time()
        model = kwargs.get("model", "unknown")
        tools = kwargs.get("tools", [])
        status = "ok"

        try:
            result = _original_create(self, *args, **kwargs)
        except Exception as e:
            status = "error"
            log_tool_call(
                tool_name="anthropic.messages.create",
                tool_input={"model": model, "tools_count": len(tools)},
                status=status,
                duration_ms=int((time.time() - t0) * 1000),
                model=model,
                source="anthropic-sdk",
            )
            raise

        duration_ms = int((time.time() - t0) * 1000)
        usage = getattr(result, "usage", None)
        tokens_in = getattr(usage, "input_tokens", 0) if usage else 0
        tokens_out = getattr(usage, "output_tokens", 0) if usage else 0

        log_tool_call(
            tool_name="anthropic.messages.create",
            tool_input={"model": model, "tools_count": len(tools)},
            status=status,
            duration_ms=duration_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=model,
            source="anthropic-sdk",
        )

        # Log each tool_use block
        for block in getattr(result, "content", []):
            if getattr(block, "type", "") == "tool_use":
                log_tool_call(
                    tool_name=f"anthropic.tool:{getattr(block, 'name', 'unknown')}",
                    tool_input=getattr(block, "input", {}),
                    duration_ms=0,
                    model=model,
                    source="anthropic-sdk",
                )

        return result

    anthropic.resources.messages.Messages.create = _patched_create  # type: ignore
    logger.debug("retention.sh: patched anthropic.messages.create")


# ─────────────────────────────────────────────────────────────────────────────
# 3. LangChain (callback handler)
# ─────────────────────────────────────────────────────────────────────────────

class LangChainRetentionHandler:
    """LangChain callback handler that logs tool calls to retention.sh.

    Usage:
        from retention_sh import LangChainRetentionHandler
        handler = LangChainRetentionHandler()
        agent.invoke(input, config={"callbacks": [handler]})
    """

    def __init__(self) -> None:
        self._tool_starts: dict[str, float] = {}

    def on_tool_start(
        self, serialized: dict, input_str: str, *, run_id: Any = None, **kwargs: Any
    ) -> None:
        from .core import log_tool_call
        rid = str(run_id) if run_id else ""
        self._tool_starts[rid] = time.time()

    def on_tool_end(self, output: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        from .core import log_tool_call
        rid = str(run_id) if run_id else ""
        t0 = self._tool_starts.pop(rid, time.time())
        duration_ms = int((time.time() - t0) * 1000)

        # Try to get tool name from parent context
        parent = kwargs.get("parent_run_id", "")
        tool_name = kwargs.get("name", "langchain.tool")

        log_tool_call(
            tool_name=tool_name,
            tool_output=str(output)[:200],
            duration_ms=duration_ms,
            source="langchain",
        )

    def on_tool_error(self, error: Exception, *, run_id: Any = None, **kwargs: Any) -> None:
        from .core import log_tool_call
        rid = str(run_id) if run_id else ""
        t0 = self._tool_starts.pop(rid, time.time())
        log_tool_call(
            tool_name=kwargs.get("name", "langchain.tool"),
            tool_output=str(error)[:200],
            status="error",
            duration_ms=int((time.time() - t0) * 1000),
            source="langchain",
        )


def patch_langchain() -> None:
    """Register LangChain retention handler globally.

    Alternative: pass LangChainRetentionHandler() to callbacks manually.
    """
    if _already_patched("langchain"):
        return

    try:
        from langchain_core.callbacks import CallbackManager
        # Try to set global handler
        CallbackManager.configure(
            inheritable_callbacks=[LangChainRetentionHandler()],
        )
        logger.debug("retention.sh: registered LangChain global callback")
    except ImportError:
        pass
    except Exception as e:
        logger.debug("retention.sh: LangChain patch failed: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# 4. CrewAI (tool hooks)
# ─────────────────────────────────────────────────────────────────────────────

def patch_crewai() -> None:
    """Patch CrewAI to log tool calls via before/after hooks."""
    if _already_patched("crewai"):
        return

    try:
        from crewai.hooks import before_tool_call, after_tool_call
    except ImportError:
        return

    from .core import log_tool_call

    _tool_starts: dict[str, float] = {}

    @before_tool_call
    def _retention_before(context: Any) -> bool:
        _tool_starts[context.tool_name] = time.time()
        return True

    @after_tool_call
    def _retention_after(context: Any, result: Any) -> Any:
        t0 = _tool_starts.pop(context.tool_name, time.time())
        log_tool_call(
            tool_name=f"crewai.tool:{context.tool_name}",
            tool_input=context.tool_input if hasattr(context, "tool_input") else {},
            tool_output=str(result)[:200],
            duration_ms=int((time.time() - t0) * 1000),
            source="crewai",
        )
        return result

    logger.debug("retention.sh: registered CrewAI tool hooks")


# ─────────────────────────────────────────────────────────────────────────────
# 5. OpenAI Agents SDK (TracingProcessor)
# ─────────────────────────────────────────────────────────────────────────────

class OpenAIRetentionProcessor:
    """OpenAI Agents SDK TracingProcessor that logs to retention.sh.

    Usage:
        from retention_sh import OpenAIRetentionProcessor
        from agents.tracing import add_trace_processor
        add_trace_processor(OpenAIRetentionProcessor())
    """

    def on_trace_start(self, trace: Any) -> None:
        pass

    def on_trace_end(self, trace: Any) -> None:
        pass

    def on_span_start(self, span: Any) -> None:
        pass

    def on_span_end(self, span: Any) -> None:
        from .core import log_tool_call

        span_data = getattr(span, "span_data", None)
        if span_data is None:
            return

        class_name = type(span_data).__name__
        if class_name != "FunctionSpanData":
            return

        name = getattr(span_data, "name", "unknown")
        inp = getattr(span_data, "input", "")
        out = getattr(span_data, "output", "")
        start = getattr(span, "started_at", 0)
        end = getattr(span, "ended_at", 0)
        duration_ms = int((end - start) * 1000) if start and end else 0

        tool_input = {}
        if isinstance(inp, dict):
            tool_input = inp
        elif isinstance(inp, str):
            tool_input = {"input": inp[:200]}

        log_tool_call(
            tool_name=f"openai_agents.tool:{name}",
            tool_input=tool_input,
            tool_output=str(out)[:200],
            duration_ms=duration_ms,
            source="openai-agents-sdk",
        )

    def shutdown(self) -> None:
        pass

    def force_flush(self) -> None:
        pass


def patch_openai_agents() -> None:
    """Register retention.sh TracingProcessor with OpenAI Agents SDK."""
    if _already_patched("openai_agents"):
        return

    try:
        from agents.tracing import add_trace_processor
        add_trace_processor(OpenAIRetentionProcessor())
        logger.debug("retention.sh: registered OpenAI Agents SDK processor")
    except ImportError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# 6. Claude Agent SDK (hooks)
# ─────────────────────────────────────────────────────────────────────────────

def patch_claude_agent_sdk() -> None:
    """Patch Claude Agent SDK — provides hook config for ClaudeAgentOptions.

    Unlike other providers, Claude Agent SDK hooks must be passed during
    agent construction. This function registers a module-level hook factory.

    Usage:
        from retention_sh.wrappers import get_claude_agent_hooks
        options = ClaudeAgentOptions(hooks=get_claude_agent_hooks())
    """
    if _already_patched("claude_agent_sdk"):
        return
    # No auto-patch possible — hooks must be passed at construction time
    logger.debug("retention.sh: Claude Agent SDK requires manual hook registration")


def get_claude_agent_hooks() -> dict:
    """Return hook config dict for ClaudeAgentOptions.

    Usage:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
        from retention_sh.wrappers import get_claude_agent_hooks

        options = ClaudeAgentOptions(hooks=get_claude_agent_hooks())
        client = ClaudeSDKClient(options=options)
    """
    from .core import log_tool_call

    _starts: dict[str, float] = {}

    async def post_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        tool_name = input_data.get("tool_name", "unknown")
        tool_input = input_data.get("tool_input", {})
        t0 = _starts.pop(tool_use_id or "", time.time())

        log_tool_call(
            tool_name=f"claude_agent.tool:{tool_name}",
            tool_input=tool_input if isinstance(tool_input, dict) else {},
            duration_ms=int((time.time() - t0) * 1000),
            source="claude-agent-sdk",
        )
        return {}

    async def pre_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        _starts[tool_use_id or ""] = time.time()
        return {}

    try:
        from claude_agent_sdk import HookMatcher
        return {
            "PreToolUse": [HookMatcher(matcher=".*", hooks=[pre_tool_use])],
            "PostToolUse": [HookMatcher(matcher=".*", hooks=[post_tool_use])],
        }
    except ImportError:
        # Return a plain dict that can be used if HookMatcher isn't available
        return {
            "PreToolUse": [{"matcher": ".*", "hooks": [pre_tool_use]}],
            "PostToolUse": [{"matcher": ".*", "hooks": [post_tool_use]}],
        }


# ─────────────────────────────────────────────────────────────────────────────
# 7. PydanticAI (OTEL-based)
# ─────────────────────────────────────────────────────────────────────────────

def patch_pydantic_ai() -> None:
    """Instrument PydanticAI via its built-in logfire/OTEL integration."""
    if _already_patched("pydantic_ai"):
        return

    try:
        import logfire
        logfire.configure(send_to_logfire=False)  # local only
        logfire.instrument_pydantic_ai()
        logger.debug("retention.sh: instrumented PydanticAI via logfire")
    except ImportError:
        pass
    except Exception as e:
        logger.debug("retention.sh: PydanticAI patch failed: %s", e)
