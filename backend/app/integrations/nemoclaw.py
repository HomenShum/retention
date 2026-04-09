"""NemoClaw integration for retention.sh MCP.

This module keeps the runtime surface intentionally small:
- `NemotronClient` wraps an OpenAI-compatible Nemotron endpoint
- `DeepAgentBridge` fetches TA MCP tools and calls them over HTTP
- `NemoClawAgent` runs a compact tool-calling loop
- `dispatch_nemoclaw_run` exposes the agent to the MCP server

It is designed to fail clearly when optional dependencies are missing.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Provider endpoints (all OpenAI-compatible)
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Model IDs per provider
_OPENROUTER_NEMOTRON = "nvidia/nemotron-3-super-49b-v1:free"
_OPENROUTER_MISTRAL = "mistralai/mistral-small-3.2-24b-instruct:free"
_NIM_NEMOTRON = "nvidia/nemotron-3-super-120b-a12b"

# Default: OpenRouter (free tier) > NVIDIA NIM > fail
_DEFAULT_MODEL = _OPENROUTER_NEMOTRON


@dataclass
class NemotronClient:
    """Thin OpenAI-compatible client. Auto-detects provider from available keys.

    Priority: OPENROUTER_API_KEY (free models) > NVIDIA_API_KEY (NIM) > custom endpoint.
    """

    model: str = ""
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.3
    max_tokens: int = 4096

    def __post_init__(self) -> None:
        # Auto-detect provider from available keys
        if not self.api_key and not self.base_url:
            or_key = os.getenv("OPENROUTER_API_KEY", "")
            nv_key = os.getenv("NVIDIA_API_KEY", "")
            custom_url = os.getenv("NEMOTRON_BASE_URL", "")

            if or_key:
                self.api_key = or_key
                self.base_url = _OPENROUTER_BASE_URL
                self.model = self.model or os.getenv("NEMOCLAW_MODEL", _DEFAULT_MODEL)
                logger.info("NemoClaw using OpenRouter (%s)", self.model)
            elif nv_key:
                self.api_key = nv_key
                self.base_url = _NIM_BASE_URL
                self.model = self.model or _NIM_NEMOTRON
                logger.info("NemoClaw using NVIDIA NIM (%s)", self.model)
            elif custom_url:
                self.base_url = custom_url
                self.model = self.model or _NIM_NEMOTRON
                logger.info("NemoClaw using custom endpoint (%s)", self.base_url)
        else:
            self.base_url = self.base_url or os.getenv("NEMOTRON_BASE_URL", _OPENROUTER_BASE_URL)
            self.model = self.model or _DEFAULT_MODEL

    def _require_openai(self, async_mode: bool = False):
        try:
            if async_mode:
                from openai import AsyncOpenAI
                return AsyncOpenAI
            from openai import OpenAI
            return OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package is required for NemoClaw Nemotron calls. "
                "Install with: pip install openai"
            ) from exc

    async def achat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
    ) -> dict[str, Any]:
        client_cls = self._require_openai(async_mode=True)
        # OpenRouter requires extra headers for free tier
        extra_headers = {}
        if self.provider == "openrouter":
            extra_headers = {
                "HTTP-Referer": "https://retention.com",
                "X-Title": "retention.sh NemoClaw",
            }
        client = client_cls(
            api_key=self.api_key,
            base_url=self.base_url,
            default_headers=extra_headers,
        )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        response = await client.chat.completions.create(**kwargs)
        return response.model_dump()

    def is_configured(self) -> bool:
        """Any valid API key or a custom local endpoint counts as configured."""
        return bool(self.api_key) or self.base_url not in (_NIM_BASE_URL, _OPENROUTER_BASE_URL, "")

    @property
    def provider(self) -> str:
        if _OPENROUTER_BASE_URL in self.base_url:
            return "openrouter"
        if _NIM_BASE_URL in self.base_url:
            return "nvidia_nim"
        return "custom"


@dataclass
class OpenShellPolicy:
    """Minimal sandbox policy renderer for NemoClaw docs and setup flows."""

    ta_endpoint: str = "localhost:8000"
    emulator_host: str = "localhost:5554"
    nim_endpoint: str = "integrate.api.nvidia.com:443"
    extra_allowed_hosts: list[str] = field(default_factory=list)

    def to_yaml(self) -> str:
        routes = [
            self.nim_endpoint,
            self.ta_endpoint,
            self.emulator_host,
            *self.extra_allowed_hosts,
        ]
        network_routes = "\n".join(
            (
                f'    - destination: "{host}"\n'
                f'      protocol: "{"https" if ":443" in host else "tcp"}"'
            )
            for host in routes
        )
        return (
            "binaries:\n"
            "  - name: \"/usr/bin/python3\"\n"
            "    allowed: true\n"
            "  - name: \"/usr/bin/adb\"\n"
            "    allowed: true\n"
            "network:\n"
            "  default_deny: true\n"
            "  routes:\n"
            f"{network_routes}\n"
        )


@dataclass
class TAToolSpec:
    name: str
    description: str
    parameters: list[dict[str, Any]]


class DeepAgentBridge:
    """Fetch and invoke retention.sh MCP tools over HTTP."""

    def __init__(self, ta_endpoint: str = "", ta_token: str = ""):
        self.ta_endpoint = ta_endpoint or os.getenv("TA_MCP_ENDPOINT", "http://localhost:8000/mcp")
        self.ta_token = ta_token or os.getenv("RETENTION_MCP_TOKEN", "")
        self._tools_cache: list[TAToolSpec] | None = None
        self._internal_dispatch = None  # Set to avoid self-referencing HTTP

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.ta_token:
            headers["Authorization"] = f"Bearer {self.ta_token}"
        return headers

    def fetch_tools(self) -> list[TAToolSpec]:
        if self._tools_cache is not None:
            return self._tools_cache

        # When running in-process, read tools directly (avoids self-referencing HTTP)
        if self._internal_dispatch:
            try:
                from ..api.mcp_server import _TOOLS
                self._tools_cache = [
                    TAToolSpec(
                        name=t.name,
                        description=t.description,
                        parameters=[p.model_dump() for p in t.parameters],
                    )
                    for t in _TOOLS
                ]
                return self._tools_cache
            except ImportError:
                pass

        req = urllib.request.Request(f"{self.ta_endpoint}/tools", headers=self._headers())
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw_tools = json.loads(resp.read())
        self._tools_cache = [
            TAToolSpec(
                name=tool["name"],
                description=tool.get("description", ""),
                parameters=tool.get("parameters", []),
            )
            for tool in raw_tools
        ]
        return self._tools_cache

    def as_openai_tools(self) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in self.fetch_tools():
            properties = {
                param["name"]: {
                    "type": param.get("type", "string"),
                    "description": param.get("description", ""),
                }
                for param in tool.parameters
            }
            required = [param["name"] for param in tool.parameters if param.get("required")]
            converted.append({
                "type": "function",
                "function": {
                    "name": tool.name.replace(".", "_"),
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            })
        return converted

    def _tool_name_map(self) -> dict[str, str]:
        return {tool.name.replace(".", "_"): tool.name for tool in self.fetch_tools()}

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps({"tool": tool_name, "arguments": arguments}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.ta_endpoint}/tools/call",
            data=payload,
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())

    async def acall_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Async tool call — uses internal dispatch when running in-process."""
        if self._internal_dispatch:
            result = await self._internal_dispatch(tool_name, arguments)
            return {"tool": tool_name, "status": "ok", "result": result}
        # Fallback: HTTP call via thread pool (for remote endpoints)
        import asyncio
        return await asyncio.get_event_loop().run_in_executor(
            None, self.call_tool, tool_name, arguments,
        )

    def set_internal_dispatch(self, dispatch_fn) -> None:
        """Set internal dispatch function to avoid self-referencing HTTP calls."""
        self._internal_dispatch = dispatch_fn


class NemoClawAgent:
    """Compact tool-calling loop with auto-rotation on failures.

    On rate limits or errors, automatically rotates to the next free model
    via OpenRouterRotation. Falls back to paid models if all free are exhausted.
    """

    def __init__(
        self,
        ta_endpoint: str = "",
        ta_token: str = "",
        nemotron: NemotronClient | None = None,
        max_turns: int = 10,
    ):
        self.bridge = DeepAgentBridge(ta_endpoint=ta_endpoint, ta_token=ta_token)
        self.nemotron = nemotron or NemotronClient()
        self.max_turns = max_turns
        self.system_prompt = (
            "You are NemoClaw, a QA automation agent using retention.sh MCP tools. "
            "Use the available tools to inspect, execute, and summarize test activity."
        )

    def is_available(self) -> bool:
        return self.nemotron.is_configured()

    async def _call_with_rotation(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Call the model, auto-rotating on rate limit or error."""
        import asyncio
        from .openrouter_rotation import get_rotation

        rotation = get_rotation()
        max_retries = 3

        # Ensure models are loaded (non-blocking)
        if not rotation._models:
            await asyncio.get_event_loop().run_in_executor(None, rotation.refresh_models)

        for attempt in range(max_retries):
            # Use rotation's current model if on OpenRouter
            if self.nemotron.provider == "openrouter":
                rotated = rotation.get_current_model(auto_refresh=False)
                if rotated:
                    self.nemotron.model = rotated

            t0 = time.time()
            try:
                response = await self.nemotron.achat(messages, tools=tools)
                latency_ms = (time.time() - t0) * 1000

                # Estimate tokens from response
                usage = response.get("usage", {})
                tokens = usage.get("total_tokens", 0)
                rotation.record_success(self.nemotron.model, latency_ms, tokens)

                return response

            except Exception as exc:
                latency_ms = (time.time() - t0) * 1000
                err_str = str(exc)
                is_rate_limit = "429" in err_str or "rate" in err_str.lower()

                rotation.record_error(self.nemotron.model, err_str, is_rate_limit)
                logger.warning(
                    "NemoClaw model %s failed (attempt %d/%d): %s",
                    self.nemotron.model, attempt + 1, max_retries, err_str[:200],
                )

                # Rotate to next model
                next_model = rotation.rotate_next()
                if next_model and next_model != self.nemotron.model:
                    logger.info("Rotating to %s", next_model)
                    self.nemotron.model = next_model
                    continue

                # All free models exhausted — try OpenAI fallback
                openai_key = os.getenv("OPENAI_API_KEY", "")
                if openai_key and attempt == max_retries - 1:
                    logger.info("All free models exhausted, falling back to OpenAI")
                    self.nemotron.api_key = openai_key
                    self.nemotron.base_url = "https://api.openai.com/v1"
                    self.nemotron.model = "gpt-4o-mini"
                    continue

                raise

        raise RuntimeError("NemoClaw: all models exhausted after retries")

    async def run(self, user_message: str) -> str:
        if not self.is_available():
            return (
                "NemoClaw not configured. Set OPENROUTER_API_KEY (free models), "
                "NVIDIA_API_KEY (NIM), or NEMOTRON_BASE_URL (local endpoint)."
            )

        tools = self.bridge.as_openai_tools()
        name_map = self.bridge._tool_name_map()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]

        for turn in range(self.max_turns):
            response = await self._call_with_rotation(messages, tools)
            message = response["choices"][0]["message"]
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return message.get("content", "") or "NemoClaw completed without a final summary."

            messages.append(message)
            for tool_call in tool_calls:
                function_payload = tool_call.get("function", {})
                safe_name = function_payload.get("name", "")
                mcp_name = name_map.get(safe_name, safe_name)
                try:
                    arguments = json.loads(function_payload.get("arguments", "{}"))
                except json.JSONDecodeError:
                    arguments = {}
                logger.info("NemoClaw turn %d calling %s(%s)", turn, mcp_name, arguments)
                result = await self.bridge.acall_tool(mcp_name, arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", ""),
                    "content": json.dumps(result),
                })

        return "Max turns reached. Agent stopped."

    async def run_instrumented(self, user_message: str) -> dict[str, Any]:
        """Like run(), but returns structured metadata for benchmarking."""
        t0 = time.time()
        tools_called: list[str] = []
        turns_used = 0
        total_tokens = 0
        error: str | None = None
        response_text = ""

        if not self.is_available():
            return {"response": "", "error": "not_configured", "tools_called": [],
                    "turns": 0, "latency_ms": 0, "total_tokens": 0, "model": self.nemotron.model}

        tools = self.bridge.as_openai_tools()
        name_map = self.bridge._tool_name_map()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]

        try:
            for turn in range(self.max_turns):
                turns_used = turn + 1
                resp = await self._call_with_rotation(messages, tools)
                usage = resp.get("usage", {})
                total_tokens += usage.get("total_tokens", 0)
                message = resp["choices"][0]["message"]
                tool_calls = message.get("tool_calls") or []
                if not tool_calls:
                    response_text = message.get("content", "") or ""
                    break

                messages.append(message)
                for tool_call in tool_calls:
                    function_payload = tool_call.get("function", {})
                    safe_name = function_payload.get("name", "")
                    mcp_name = name_map.get(safe_name, safe_name)
                    tools_called.append(mcp_name)
                    try:
                        arguments = json.loads(function_payload.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        arguments = {}
                    result = await self.bridge.acall_tool(mcp_name, arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", ""),
                        "content": json.dumps(result),
                    })
            else:
                response_text = "Max turns reached."
                error = "max_turns"
        except Exception as exc:
            error = str(exc)[:500]

        latency_ms = (time.time() - t0) * 1000
        return {
            "response": response_text,
            "tools_called": tools_called,
            "turns": turns_used,
            "latency_ms": round(latency_ms, 1),
            "total_tokens": total_tokens,
            "tokens_per_sec": round(total_tokens / (latency_ms / 1000), 1) if latency_ms > 0 else 0,
            "model": self.nemotron.model,
            "error": error,
        }


async def dispatch_nemoclaw_run(args: dict[str, Any]) -> dict[str, Any]:
    """Entry point used by retention.nemoclaw.run."""
    prompt = args.get("prompt", "")
    ta_endpoint = args.get("ta_endpoint", "")
    model_override = args.get("model", "")
    if not prompt:
        return {"error": "prompt is required"}

    nemotron = None
    if model_override:
        nemotron = NemotronClient(model=model_override)

    agent = NemoClawAgent(ta_endpoint=ta_endpoint, nemotron=nemotron)

    # Wire internal dispatch to avoid self-referencing HTTP deadlock
    # when running inside the same server process
    try:
        from ..api.mcp_server import _dispatch
        agent.bridge.set_internal_dispatch(_dispatch)
    except ImportError:
        pass  # Running standalone — will use HTTP

    if not agent.is_available():
        return {
            "error": "NemoClaw is not configured",
            "setup": {
                "openrouter": "Set OPENROUTER_API_KEY (free tier: Nemotron Super, Mistral M2.7)",
                "nvidia_nim": "Or set NVIDIA_API_KEY from build.nvidia.com",
                "local_endpoint": "Or set NEMOTRON_BASE_URL to a local OpenAI-compatible endpoint",
            },
        }

    result = await agent.run(prompt)
    return {
        "response": result,
        "model": agent.nemotron.model,
        "provider": agent.nemotron.provider,
        "base_url": agent.nemotron.base_url,
    }


async def dispatch_nemoclaw_telemetry() -> dict[str, Any]:
    """Entry point used by retention.nemoclaw.telemetry."""
    from .openrouter_rotation import get_rotation
    return get_rotation().get_telemetry()


async def dispatch_nemoclaw_refresh() -> dict[str, Any]:
    """Entry point used by retention.nemoclaw.refresh — force re-scan free models."""
    import asyncio
    from .openrouter_rotation import get_rotation
    rotation = get_rotation()
    # Run blocking HTTP call in thread pool
    models = await asyncio.get_event_loop().run_in_executor(None, rotation.refresh_models)
    return {
        "refreshed": True,
        "total_discovered": rotation._total_discovered,
        "rotation_pool": len(models),
        "top_5": [
            {"id": m.id, "name": m.name, "context": m.context_length, "tools": m.supports_tools, "score": round(m.score, 1)}
            for m in models[:5]
        ],
        "current_model": rotation.get_current_model(),
    }
