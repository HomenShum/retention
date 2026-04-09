"""Shared AgentRunner — runs an OpenAI /v1/responses tool-calling loop for any registered agent.

Uses the Responses API (not Chat Completions) to support reasoning_effort + tools.
The runner is model-agnostic and tool-agnostic. It reads the AgentConfig to
determine the system prompt, tool set, turn budget, and lifecycle hooks.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from .base import AgentConfig
from .run_logger import log_agent_run
from .tool_schemas import (
    EXPAND_TOOLS_SCHEMA,
    FUNC_TO_MCP,
    get_categories_for_skill,
    get_tools_for_categories,
    get_tools_for_skill,
)
from ...services.usage_telemetry import estimate_cost_usd, record_usage_event

logger = logging.getLogger(__name__)

API_URL = "https://api.openai.com/v1/responses"


def _convert_tools_to_responses_format(chat_tools: List[dict]) -> List[dict]:
    """Convert chat-completions tool defs to /v1/responses format.

    Chat completions: {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    Responses API:    {"type": "function", "name": ..., "description": ..., "parameters": ...}
    """
    out = []
    for t in chat_tools:
        if t.get("type") == "function" and "function" in t:
            fn = t["function"]
            out.append({
                "type": "function",
                "name": fn["name"],
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            })
        else:
            out.append(t)
    return out


def _extract_text(output: List[dict]) -> str:
    """Extract text content from /v1/responses output items."""
    for item in output:
        if item.get("type") == "message":
            content = item.get("content", [])
            parts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "output_text":
                    parts.append(c.get("text", ""))
                elif isinstance(c, str):
                    parts.append(c)
            return "\n".join(parts)
    return "(no response)"


def _extract_tool_calls(output: List[dict]) -> List[dict]:
    """Extract function_call items from /v1/responses output."""
    return [item for item in output if item.get("type") == "function_call"]


class AgentRunner:
    """Runs a tool-calling loop for a given AgentConfig."""

    def __init__(self, config: AgentConfig):
        self.config = config

    async def run(self, question: str, **kwargs: Any) -> Dict[str, Any]:
        """Execute the agent loop and return a structured result dict."""
        t0 = time.time()

        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return {"text": "OPENAI_API_KEY not configured on backend", "error": "no_api_key"}

        model = kwargs.get("model", self.config.model)
        reasoning_effort = kwargs.get("reasoning_effort", self.config.reasoning_effort)
        max_turns = min(kwargs.get("max_turns", self.config.max_turns), 1000)
        tool_call_log: List[str] = []
        tool_results_log: List[Dict[str, Any]] = []
        files_changed_agg: List[str] = []  # aggregated across all claude_code tool calls
        total_tokens: Dict[str, int] = {"input": 0, "output": 0, "total": 0}
        telemetry_interface = kwargs.get("telemetry_interface") or os.path.splitext(os.path.basename(inspect.stack()[1].filename))[0]
        telemetry_operation = kwargs.get("telemetry_operation") or self.config.name

        # --- pre_run hook (e.g. strategy selection) ---
        pre_run_result: Dict[str, Any] = {}
        if self.config.pre_run:
            try:
                result = self.config.pre_run(question, api_key, model, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                pre_run_result = result or {}
            except Exception as e:
                logger.warning("pre_run hook failed for %s: %s", self.config.name, e)

        # --- Skill-based progressive tool disclosure ---
        skill = pre_run_result.get("skill", "full")
        active_categories = get_categories_for_skill(skill, self.config.tool_categories)
        pre_run_result["_active_categories"] = active_categories

        # --- Build system prompt (uses _active_categories) ---
        system_prompt = await self._resolve_prompt(pre_run_result, **kwargs)

        # --- Build tool list filtered by skill ---
        chat_tools = get_tools_for_skill(skill, self.config.tool_categories)
        is_subset = set(active_categories) != set(self.config.tool_categories)
        if is_subset:
            chat_tools.append(EXPAND_TOOLS_SCHEMA)
        tools = _convert_tools_to_responses_format(chat_tools)

        # Build input messages for /v1/responses
        # Include conversation context from thread history if provided
        context: List[Dict[str, Any]] = kwargs.get("context", [])
        input_items: List[Dict[str, Any]] = []
        for msg in context:
            if isinstance(msg, dict) and "role" in msg and "content" in msg:
                input_items.append({"role": msg["role"], "content": msg["content"]})
        input_items.append({"role": "user", "content": question})

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=30)) as client:
                for turn in range(max_turns):
                    body: Dict[str, Any] = {
                        "model": model,
                        "instructions": system_prompt,
                        "input": input_items,
                    }
                    if reasoning_effort:
                        body["reasoning"] = {"effort": reasoning_effort}
                    if tools:
                        body["tools"] = tools
                        body["tool_choice"] = "auto"

                    resp = await client.post(
                        API_URL,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {api_key}",
                        },
                        json=body,
                    )
                    if resp.status_code != 200:
                        duration_ms = int((time.time() - t0) * 1000)
                        record_usage_event(
                            interface=telemetry_interface,
                            operation=telemetry_operation,
                            model=model,
                            input_tokens=total_tokens["input"],
                            output_tokens=total_tokens["output"],
                            total_tokens=total_tokens["total"],
                            duration_ms=duration_ms,
                            success=False,
                            error=f"API {resp.status_code}",
                            metadata={"agent_name": self.config.name, "turns": turn + 1, "tool_calls": len(tool_call_log)},
                        )
                        return {
                            "text": f"API error: {resp.status_code} {resp.text[:300]}",
                            "error": f"API {resp.status_code}",
                            "tool_calls": tool_call_log,
                            "duration_ms": duration_ms,
                        }

                    data = resp.json()
                    usage = data.get("usage", {})
                    total_tokens["input"] += usage.get("input_tokens", 0)
                    total_tokens["output"] += usage.get("output_tokens", 0)
                    total_tokens["total"] += usage.get("total_tokens", 0)

                    output = data.get("output", [])
                    fn_calls = _extract_tool_calls(output)

                    if fn_calls:
                        # Synthesis nudge — dynamic threshold based on turn budget
                        nudge_at = max(int(max_turns * 0.05), 15)
                        if len(tool_call_log) >= nudge_at and len(tool_call_log) < nudge_at + 3:
                            input_items.append({
                                "role": "user",
                                "content": (
                                    "[SYSTEM] You have made {n} tool calls. STOP researching and write your answer NOW. "
                                    "Synthesize what you have — a good answer from current data is better than "
                                    "a perfect answer that never arrives. The user is waiting."
                                ).format(n=len(tool_call_log)),
                            })

                        # Add all output items to input for next turn
                        input_items.extend(output)

                        for fc in fn_calls:
                            fn_name = fc["name"]
                            fn_args = json.loads(fc.get("arguments", "{}"))
                            call_id = fc.get("call_id", "")
                            tool_call_log.append(fn_name)

                            # --- Handle expand_tools meta-tool ---
                            if fn_name == "request_additional_tools":
                                requested = fn_args.get("categories", [])
                                new_cats = list(set(active_categories) | set(requested))
                                new_cats = [c for c in new_cats if c in self.config.tool_categories]
                                active_categories = new_cats
                                pre_run_result["_active_categories"] = new_cats

                                new_chat_tools = get_tools_for_categories(new_cats)
                                still_subset = set(new_cats) != set(self.config.tool_categories)
                                if still_subset:
                                    new_chat_tools.append(EXPAND_TOOLS_SCHEMA)
                                tools = _convert_tools_to_responses_format(new_chat_tools)

                                # Rebuild prompt with expanded categories
                                system_prompt = await self._resolve_prompt(pre_run_result, **kwargs)

                                added = [c for c in requested if c in self.config.tool_categories]
                                tool_result_str = json.dumps({
                                    "expanded": True,
                                    "active_categories": new_cats,
                                    "added": added,
                                })
                                logger.info("Tool expansion: +%s → %s", added, new_cats)
                            else:
                                mcp_name = FUNC_TO_MCP.get(fn_name, fn_name)
                                try:
                                    result = await self._dispatch_tool(mcp_name, fn_args)
                                    tool_result_str = json.dumps(result, default=str)
                                except Exception as e:
                                    tool_result_str = json.dumps({"error": str(e)})

                                # Aggregate files_changed from claude_code tool results
                                if fn_name in ("claude_code", "run_command", "edit_file", "write_file"):
                                    try:
                                        _tr = json.loads(tool_result_str)
                                        _fc = _tr.get("files_changed") or _tr.get("changed_files") or []
                                        if isinstance(_fc, list):
                                            for _f in _fc:
                                                if isinstance(_f, str) and _f not in files_changed_agg:
                                                    files_changed_agg.append(_f)
                                    except Exception:
                                        pass

                                tool_results_log.append({
                                    "tool": fn_name,
                                    "mcp_name": mcp_name,
                                    "args": fn_args,
                                    "result_str": tool_result_str[:2000],
                                })

                            if len(tool_result_str) > self.config.max_tool_result_chars:
                                tool_result_str = (
                                    tool_result_str[: self.config.max_tool_result_chars]
                                    + "\n... (truncated)"
                                )

                            input_items.append({
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": tool_result_str,
                            })
                        continue

                    # Final text response — no more tool calls
                    final_text = _extract_text(output)
                    duration_ms = int((time.time() - t0) * 1000)
                    estimated_cost_usd = estimate_cost_usd(model, total_tokens["input"], total_tokens["output"])
                    result = {
                        "text": final_text,
                        "model": model,
                        "tool_calls": tool_call_log,
                        "turns": turn + 1,
                        "tokens": total_tokens,
                        "duration_ms": duration_ms,
                        "estimated_cost_usd": estimated_cost_usd,
                        "telemetry_interface": telemetry_interface,
                    }

                    if pre_run_result:
                        result["strategy"] = pre_run_result

                    if files_changed_agg:
                        result["files_changed"] = files_changed_agg

                    if self.config.post_run:
                        try:
                            post = self.config.post_run(
                                final_text, tool_results_log,
                                files_changed=files_changed_agg, **kwargs
                            )
                            if inspect.isawaitable(post):
                                post = await post
                            if post:
                                result.update(post)
                        except Exception as e:
                            logger.warning("post_run hook failed for %s: %s", self.config.name, e)

                    record_usage_event(
                        interface=telemetry_interface,
                        operation=telemetry_operation,
                        model=model,
                        input_tokens=total_tokens["input"],
                        output_tokens=total_tokens["output"],
                        total_tokens=total_tokens["total"],
                        duration_ms=result.get("duration_ms", 0),
                        success=not bool(result.get("error")),
                        error=result.get("error"),
                        metadata={"agent_name": self.config.name, "turns": result.get("turns", 0), "tool_calls": len(tool_call_log)},
                    )

                    # Log the run for historical tracking
                    log_agent_run(result, question, self.config.name)

                    return result

            # Exceeded max turns — attempt a final synthesis
            duration_ms = int((time.time() - t0) * 1000)
            estimated_cost_usd = estimate_cost_usd(model, total_tokens["input"], total_tokens["output"])

            # Force one last call with no tools so the model MUST produce text
            _synthesis_error: str = ""
            try:
                input_items.append({
                    "role": "user",
                    "content": "[SYSTEM] Turn limit reached. You MUST write your answer NOW using everything you've gathered. No more tool calls available.",
                })
                synthesis_body: Dict[str, Any] = {
                    "model": model,
                    "instructions": system_prompt,
                    "input": input_items,
                }
                if reasoning_effort:
                    synthesis_body["reasoning"] = {"effort": reasoning_effort}
                # No tools — forces text output
                async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=30)) as synth_client:
                    synth_resp = await synth_client.post(
                        API_URL,
                        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                        json=synthesis_body,
                    )
                    if synth_resp.status_code == 200:
                        synth_data = synth_resp.json()
                        synth_usage = synth_data.get("usage", {})
                        total_tokens["input"] += synth_usage.get("input_tokens", 0)
                        total_tokens["output"] += synth_usage.get("output_tokens", 0)
                        total_tokens["total"] += synth_usage.get("total_tokens", 0)
                        final_text = _extract_text(synth_data.get("output", []))
                        if final_text:
                            duration_ms = int((time.time() - t0) * 1000)
                            estimated_cost_usd = estimate_cost_usd(model, total_tokens["input"], total_tokens["output"])
                            result = {
                                "text": final_text,
                                "tool_calls": tool_call_log,
                                "turns": max_turns + 1,
                                "tokens": total_tokens,
                                "duration_ms": duration_ms,
                                "estimated_cost_usd": estimated_cost_usd,
                                "telemetry_interface": telemetry_interface,
                                "forced_synthesis": True,
                            }
                            record_usage_event(
                                interface=telemetry_interface,
                                operation=telemetry_operation,
                                model=model,
                                input_tokens=total_tokens["input"],
                                output_tokens=total_tokens["output"],
                                total_tokens=total_tokens["total"],
                                duration_ms=duration_ms,
                                success=True,
                                metadata={"agent_name": self.config.name, "turns": max_turns + 1, "tool_calls": len(tool_call_log), "forced_synthesis": True},
                            )
                            log_agent_run(result, question, self.config.name)
                            return result
            except Exception as synth_err:
                _synthesis_error = str(synth_err)
                logger.warning("Forced synthesis failed: %s — retrying with simpler prompt", synth_err)
                # One retry with a simpler prompt
                try:
                    simple_input = [
                        {"role": "user", "content": question},
                        {"role": "user", "content": (
                            "[SYSTEM] Previous synthesis attempt failed. Summarize your findings in 2-3 paragraphs. "
                            "No tool calls. Just write what you know."
                        )},
                    ]
                    simple_body: Dict[str, Any] = {
                        "model": model,
                        "instructions": system_prompt,
                        "input": simple_input,
                    }
                    if reasoning_effort:
                        simple_body["reasoning"] = {"effort": reasoning_effort}
                    async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15)) as retry_client:
                        retry_resp = await retry_client.post(
                            API_URL,
                            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                            json=simple_body,
                        )
                        if retry_resp.status_code == 200:
                            retry_data = retry_resp.json()
                            retry_usage = retry_data.get("usage", {})
                            total_tokens["input"] += retry_usage.get("input_tokens", 0)
                            total_tokens["output"] += retry_usage.get("output_tokens", 0)
                            total_tokens["total"] += retry_usage.get("total_tokens", 0)
                            retry_text = _extract_text(retry_data.get("output", []))
                            if retry_text:
                                duration_ms = int((time.time() - t0) * 1000)
                                estimated_cost_usd = estimate_cost_usd(model, total_tokens["input"], total_tokens["output"])
                                result = {
                                    "text": retry_text,
                                    "tool_calls": tool_call_log,
                                    "turns": max_turns + 1,
                                    "tokens": total_tokens,
                                    "duration_ms": duration_ms,
                                    "estimated_cost_usd": estimated_cost_usd,
                                    "telemetry_interface": telemetry_interface,
                                    "forced_synthesis": True,
                                    "synthesis_retry": True,
                                }
                                record_usage_event(
                                    interface=telemetry_interface,
                                    operation=telemetry_operation,
                                    model=model,
                                    input_tokens=total_tokens["input"],
                                    output_tokens=total_tokens["output"],
                                    total_tokens=total_tokens["total"],
                                    duration_ms=duration_ms,
                                    success=True,
                                    metadata={"agent_name": self.config.name, "turns": max_turns + 1, "tool_calls": len(tool_call_log), "forced_synthesis": True, "synthesis_retry": True},
                                )
                                log_agent_run(result, question, self.config.name)
                                return result
                except Exception as retry_err:
                    logger.warning("Forced synthesis retry also failed: %s", retry_err)
                    _synthesis_error = str(retry_err)

            record_usage_event(
                interface=telemetry_interface,
                operation=telemetry_operation,
                model=model,
                input_tokens=total_tokens["input"],
                output_tokens=total_tokens["output"],
                total_tokens=total_tokens["total"],
                duration_ms=duration_ms,
                success=False,
                error="turn_limit",
                metadata={"agent_name": self.config.name, "turns": max_turns, "tool_calls": len(tool_call_log)},
            )
            fallback_msg = "Agent reached turn limit."
            if _synthesis_error:
                fallback_msg += f" Synthesis error: {_synthesis_error[:300]}"
            return {
                "text": fallback_msg,
                "tool_calls": tool_call_log,
                "turns": max_turns,
                "tokens": total_tokens,
                "duration_ms": duration_ms,
                "estimated_cost_usd": estimated_cost_usd,
                "telemetry_interface": telemetry_interface,
            }

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.exception("AgentRunner failed for %s: %s\n%s", self.config.name, e, tb[-500:])
            duration_ms = int((time.time() - t0) * 1000)
            estimated_cost_usd = estimate_cost_usd(model, total_tokens["input"], total_tokens["output"])
            record_usage_event(
                interface=telemetry_interface,
                operation=telemetry_operation,
                model=model,
                input_tokens=total_tokens["input"],
                output_tokens=total_tokens["output"],
                total_tokens=total_tokens["total"],
                duration_ms=duration_ms,
                success=False,
                error=str(e)[:300] or type(e).__name__,
                metadata={"agent_name": self.config.name, "tool_calls": len(tool_call_log)},
            )
            return {
                "text": f"Agent error: {str(e)[:200] or type(e).__name__}",
                "error": str(e)[:300] or type(e).__name__,
                "traceback": tb[-300:],
                "tool_calls": tool_call_log,
                "duration_ms": duration_ms,
                "estimated_cost_usd": estimated_cost_usd,
                "telemetry_interface": telemetry_interface,
            }

    async def run_streaming(self, question: str, **kwargs: Any):
        """Async generator version of run() — yields SSE-style event dicts.

        Events emitted:
          {"event": "status",     "data": {"message": "...", "turn": N, "elapsed_s": X}}
          {"event": "tool_start", "data": {"tool": "name", "args_summary": "...", "turn": N}}
          {"event": "tool_done",  "data": {"tool": "name", "summary": "...", "turn": N, "elapsed_s": X}}
          {"event": "done",       "data": {<full result dict>}}
          {"event": "error",      "data": {"message": "..."}}
        """
        t0 = time.time()

        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            yield {"event": "error", "data": {"message": "OPENAI_API_KEY not configured"}}
            return

        model = kwargs.get("model", self.config.model)
        reasoning_effort = kwargs.get("reasoning_effort", self.config.reasoning_effort)
        max_turns = min(kwargs.get("max_turns", self.config.max_turns), 1000)
        tool_call_log: List[str] = []
        tool_results_log: List[Dict[str, Any]] = []
        files_changed_agg: List[str] = []  # aggregated across all claude_code tool calls
        total_tokens: Dict[str, int] = {"input": 0, "output": 0, "total": 0}
        telemetry_interface = kwargs.get("telemetry_interface") or os.path.splitext(os.path.basename(inspect.stack()[1].filename))[0]
        telemetry_operation = kwargs.get("telemetry_operation") or f"{self.config.name}-stream"

        yield {"event": "status", "data": {"message": "Selecting strategy...", "turn": 0, "elapsed_s": 0}}

        # --- pre_run hook ---
        pre_run_result: Dict[str, Any] = {}
        if self.config.pre_run:
            try:
                result = self.config.pre_run(question, api_key, model, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                pre_run_result = result or {}
                strategy_name = pre_run_result.get("strategy", "")
                skill = pre_run_result.get("skill", "")
                yield {"event": "status", "data": {
                    "message": f"Strategy: {strategy_name} · Skill: {skill}",
                    "turn": 0, "elapsed_s": round(time.time() - t0, 1),
                }}
            except Exception as e:
                logger.warning("pre_run hook failed for %s: %s", self.config.name, e)

        # --- Skill-based progressive tool disclosure ---
        skill = pre_run_result.get("skill", "full")
        active_categories = get_categories_for_skill(skill, self.config.tool_categories)
        pre_run_result["_active_categories"] = active_categories

        system_prompt = await self._resolve_prompt(pre_run_result, **kwargs)

        chat_tools = get_tools_for_skill(skill, self.config.tool_categories)
        is_subset = set(active_categories) != set(self.config.tool_categories)
        if is_subset:
            chat_tools.append(EXPAND_TOOLS_SCHEMA)
            logger.info("Progressive disclosure: skill=%s → categories=%s (%d tools)",
                        skill, active_categories, len(chat_tools))
        tools = _convert_tools_to_responses_format(chat_tools)

        # Include conversation context from thread history if provided
        context: List[Dict[str, Any]] = kwargs.get("context", [])
        input_items: List[Dict[str, Any]] = []
        for msg in context:
            if isinstance(msg, dict) and "role" in msg and "content" in msg:
                input_items.append({"role": msg["role"], "content": msg["content"]})
        input_items.append({"role": "user", "content": question})

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=30)) as client:
                for turn in range(max_turns):
                    yield {"event": "status", "data": {
                        "message": f"Turn {turn + 1}/{max_turns} · Calling model...",
                        "turn": turn + 1, "elapsed_s": round(time.time() - t0, 1),
                    }}

                    body: Dict[str, Any] = {
                        "model": model,
                        "instructions": system_prompt,
                        "input": input_items,
                    }
                    if reasoning_effort:
                        body["reasoning"] = {"effort": reasoning_effort}
                    if tools:
                        body["tools"] = tools
                        body["tool_choice"] = "auto"

                    # Fire API call as a task and yield keepalive while waiting
                    api_task = asyncio.create_task(client.post(
                        API_URL,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {api_key}",
                        },
                        json=body,
                    ))
                    while not api_task.done():
                        try:
                            await asyncio.wait_for(asyncio.shield(api_task), timeout=15)
                        except asyncio.TimeoutError:
                            yield {"event": "status", "data": {
                                "message": f"Turn {turn + 1}/{max_turns} · Model thinking...",
                                "turn": turn + 1, "elapsed_s": round(time.time() - t0, 1),
                            }}

                    resp = api_task.result()
                    if resp.status_code != 200:
                        yield {"event": "error", "data": {
                            "message": f"API error: {resp.status_code} {resp.text[:200]}",
                            "turn": turn + 1,
                        }}
                        return

                    data = resp.json()
                    usage = data.get("usage", {})
                    total_tokens["input"] += usage.get("input_tokens", 0)
                    total_tokens["output"] += usage.get("output_tokens", 0)
                    total_tokens["total"] += usage.get("total_tokens", 0)

                    output = data.get("output", [])
                    fn_calls = _extract_tool_calls(output)

                    if fn_calls:
                        input_items.extend(output)

                        for fc in fn_calls:
                            fn_name = fc["name"]
                            fn_args = json.loads(fc.get("arguments", "{}"))
                            call_id = fc.get("call_id", "")
                            tool_call_log.append(fn_name)

                            # Emit tool_start
                            args_summary = ", ".join(
                                f"{k}={json.dumps(v)[:40]}" for k, v in fn_args.items()
                            )
                            yield {"event": "tool_start", "data": {
                                "tool": fn_name, "args_summary": args_summary,
                                "turn": turn + 1, "elapsed_s": round(time.time() - t0, 1),
                            }}

                            # --- Handle expand_tools meta-tool ---
                            if fn_name == "request_additional_tools":
                                requested = fn_args.get("categories", [])
                                new_cats = list(set(active_categories) | set(requested))
                                new_cats = [c for c in new_cats if c in self.config.tool_categories]
                                added = [c for c in requested if c in self.config.tool_categories and c not in active_categories]
                                active_categories = new_cats
                                pre_run_result["_active_categories"] = new_cats

                                new_chat_tools = get_tools_for_categories(new_cats)
                                still_subset = set(new_cats) != set(self.config.tool_categories)
                                if still_subset:
                                    new_chat_tools.append(EXPAND_TOOLS_SCHEMA)
                                tools = _convert_tools_to_responses_format(new_chat_tools)

                                # Rebuild prompt with expanded categories
                                system_prompt = await self._resolve_prompt(pre_run_result, **kwargs)

                                tool_result_str = json.dumps({
                                    "expanded": True,
                                    "active_categories": new_cats,
                                    "added": added,
                                })
                                result_summary = f"Expanded: +{', '.join(added)}"
                                logger.info("Tool expansion: +%s → %s", added, new_cats)

                                yield {"event": "status", "data": {
                                    "message": f"🔄 Expanding tools: +{', '.join(added)}",
                                    "turn": turn + 1, "elapsed_s": round(time.time() - t0, 1),
                                }}
                            else:
                                mcp_name = FUNC_TO_MCP.get(fn_name, fn_name)
                                try:
                                    result = await self._dispatch_tool(mcp_name, fn_args)
                                    tool_result_str = json.dumps(result, default=str)
                                except Exception as e:
                                    tool_result_str = json.dumps({"error": str(e)})

                                # Aggregate files_changed from claude_code tool results
                                if fn_name in ("claude_code", "run_command", "edit_file", "write_file"):
                                    try:
                                        _tr = json.loads(tool_result_str)
                                        _fc = _tr.get("files_changed") or _tr.get("changed_files") or []
                                        if isinstance(_fc, list):
                                            for _f in _fc:
                                                if isinstance(_f, str) and _f not in files_changed_agg:
                                                    files_changed_agg.append(_f)
                                    except Exception:
                                        pass

                                result_summary = tool_result_str[:120]
                                if len(tool_result_str) > 120:
                                    result_summary += "..."

                                tool_results_log.append({
                                    "tool": fn_name, "mcp_name": mcp_name,
                                    "args": fn_args, "result_str": tool_result_str[:2000],
                                })

                            if len(tool_result_str) > self.config.max_tool_result_chars:
                                tool_result_str = (
                                    tool_result_str[: self.config.max_tool_result_chars]
                                    + "\n... (truncated)"
                                )

                            input_items.append({
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": tool_result_str,
                            })

                            # Emit tool_done
                            yield {"event": "tool_done", "data": {
                                "tool": fn_name, "summary": result_summary,
                                "turn": turn + 1, "elapsed_s": round(time.time() - t0, 1),
                                "total_tools": len(tool_call_log),
                            }}
                        continue

                    # Final text response
                    final_text = _extract_text(output)
                    duration_ms = int((time.time() - t0) * 1000)
                    estimated_cost_usd = estimate_cost_usd(model, total_tokens["input"], total_tokens["output"])
                    result = {
                        "text": final_text,
                        "model": model,
                        "tool_calls": tool_call_log,
                        "turns": turn + 1,
                        "tokens": total_tokens,
                        "duration_ms": duration_ms,
                        "estimated_cost_usd": estimated_cost_usd,
                        "telemetry_interface": telemetry_interface,
                    }
                    if pre_run_result:
                        result["strategy"] = pre_run_result

                    if files_changed_agg:
                        result["files_changed"] = files_changed_agg

                    if self.config.post_run:
                        try:
                            post = self.config.post_run(
                                final_text, tool_results_log,
                                files_changed=files_changed_agg, **kwargs
                            )
                            if inspect.isawaitable(post):
                                post = await post
                            if post:
                                result.update(post)
                        except Exception as e:
                            logger.warning("post_run hook failed: %s", e)

                    record_usage_event(
                        interface=telemetry_interface,
                        operation=telemetry_operation,
                        model=model,
                        input_tokens=total_tokens["input"],
                        output_tokens=total_tokens["output"],
                        total_tokens=total_tokens["total"],
                        duration_ms=result.get("duration_ms", 0),
                        success=not bool(result.get("error")),
                        error=result.get("error"),
                        metadata={"agent_name": self.config.name, "turns": result.get("turns", 0), "tool_calls": len(tool_call_log), "streaming": True},
                    )

                    # Log the run for historical tracking
                    log_agent_run(result, question, self.config.name)

                    yield {"event": "done", "data": result}
                    return

            duration_ms = int((time.time() - t0) * 1000)
            estimated_cost_usd = estimate_cost_usd(model, total_tokens["input"], total_tokens["output"])
            record_usage_event(
                interface=telemetry_interface,
                operation=telemetry_operation,
                model=model,
                input_tokens=total_tokens["input"],
                output_tokens=total_tokens["output"],
                total_tokens=total_tokens["total"],
                duration_ms=duration_ms,
                success=False,
                error="turn_limit",
                metadata={"agent_name": self.config.name, "turns": max_turns, "tool_calls": len(tool_call_log), "streaming": True},
            )
            yield {"event": "done", "data": {
                "text": "Agent reached turn limit.",
                "tool_calls": tool_call_log, "turns": max_turns,
                "tokens": total_tokens, "duration_ms": duration_ms,
                "estimated_cost_usd": estimated_cost_usd,
                "telemetry_interface": telemetry_interface,
            }}

        except Exception as e:
            logger.exception("AgentRunner streaming failed for %s", self.config.name)
            duration_ms = int((time.time() - t0) * 1000)
            record_usage_event(
                interface=telemetry_interface,
                operation=telemetry_operation,
                model=model,
                input_tokens=total_tokens["input"],
                output_tokens=total_tokens["output"],
                total_tokens=total_tokens["total"],
                duration_ms=duration_ms,
                success=False,
                error=str(e),
                metadata={"agent_name": self.config.name, "tool_calls": len(tool_call_log), "streaming": True},
            )
            yield {"event": "error", "data": {
                "message": str(e), "tool_calls": tool_call_log,
                "duration_ms": duration_ms,
            }}

    async def _resolve_prompt(self, pre_run_result: Dict[str, Any], **kwargs: Any) -> str:
        """Resolve the system prompt — static string or callable."""
        prompt = self.config.system_prompt
        if callable(prompt):
            result = prompt(pre_run_result, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return str(result)
        return str(prompt)

    async def _dispatch_tool(self, mcp_name: str, args: Dict[str, Any]) -> Any:
        """Route a tool call to the appropriate MCP dispatcher."""
        if mcp_name.startswith("retention.codebase."):
            from ...api.mcp_server import _dispatch_codebase
            return await _dispatch_codebase(mcp_name, args)

        if mcp_name.startswith("ta.investor_brief."):
            from ...api.mcp_server import _dispatch_investor_brief
            return await _dispatch_investor_brief(mcp_name, args)

        if mcp_name.startswith("ta.slack."):
            from ...api.mcp_server import _dispatch_slack
            return await _dispatch_slack(mcp_name, args)

        if mcp_name == "agent.spawn_deep_research":
            return await self._spawn_deep_research(args)

        if mcp_name == "agent.spawn_parallel_research":
            return await self._spawn_parallel_research(args)

        if mcp_name == "ta.media.youtube_transcript":
            from ...services.youtube_tool import fetch_youtube_transcript
            return await fetch_youtube_transcript(
                url=args.get("url", ""),
                languages=args.get("languages"),
            )

        if mcp_name == "ta.media.generate_slides":
            from ...services.slide_generator import generate_and_post_to_slack
            return await generate_and_post_to_slack(
                topic=args.get("topic", ""),
                slides_content=args.get("slides", []),
                style=args.get("style", "brutalist-mono"),
            )

        raise ValueError(f"No dispatcher registered for tool: {mcp_name}")

    async def _spawn_deep_research(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Spawn a child AgentRunner with full orchestrator config.

        This lets any agent role independently investigate a sub-question
        using the same tool suite as the OpenClaw orchestrator (strategy-brief).
        The child gets codebase, investor_brief, web_search, and slack tools
        with up to 8 tool-calling turns.
        """
        from .base import AgentRegistry

        question = args.get("question", "")
        if not question:
            return {"error": "No question provided"}

        try:
            # Get the strategy-brief agent config (full orchestrator)
            config = AgentRegistry.get("strategy-brief")
            child_runner = AgentRunner(config)
            result = await child_runner.run(question, max_turns=1000)

            return {
                "text": result.get("text", ""),
                "tool_calls": result.get("tool_calls", []),
                "turns": result.get("turns", 0),
                "confidence": result.get("confidence", ""),
                "evidence": result.get("evidence", []),
            }
        except Exception as e:
            logger.error("spawn_deep_research failed: %s", e)
            return {"error": str(e), "text": f"Research failed: {e}"}

    async def _spawn_parallel_research(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Spawn multiple deep-research sub-agents concurrently.

        Accepts a list of questions and runs them all via asyncio.gather(),
        each using the same pattern as _spawn_deep_research.
        """
        questions = args.get("questions", [])
        if not questions:
            return {"error": "No questions provided"}

        max_per_question = args.get("max_per_question", 200)

        async def _research_one(question: str) -> Dict[str, Any]:
            return await self._spawn_deep_research({
                "question": question,
                "max_words": max_per_question,
            })

        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    *[_research_one(q) for q in questions],
                    return_exceptions=True,
                ),
                timeout=600,
            )
        except asyncio.TimeoutError:
            logger.warning("_spawn_parallel_research timed out after 600s")
            return {
                "results": [{"question": q, "error": "timed out", "text": "Research timed out"} for q in questions],
                "total": len(questions),
                "note": "Parallel research timed out after 10 minutes; partial results may be missing.",
            }

        output = []
        for q, r in zip(questions, results):
            if isinstance(r, Exception):
                output.append({"question": q, "error": str(r), "text": f"Research failed: {r}"})
            else:
                output.append({"question": q, **r})

        return {"results": output, "total": len(output)}
