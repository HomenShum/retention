"""LLM evaluator for boolean rubric gates and response composition.

All calls use the OpenAI Responses API with gpt-5.4 and reasoning_effort
support. This is the core of the LLM-as-Judge pattern used across the
monitor, digest, evolution, swarm, and deep simulation tasks.

The shared `call_responses_api()` helper centralizes the Responses API
call pattern so every service file uses the same extraction logic.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import time
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

import httpx

from .usage_telemetry import estimate_cost_usd, record_usage_event

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Daily spend guard — hard cap to prevent runaway costs
# ---------------------------------------------------------------------------
_daily_spend_usd: float = 0.0
_daily_spend_date: str = ""
_DAILY_SPEND_LIMIT_USD: float = 100.0

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

# ---------------------------------------------------------------------------
# Model tiering — 2026 best practice: use the cheapest model that works
# ---------------------------------------------------------------------------
# Model tiers — see model_registry.py for the full catalog + task routing.
# These constants kept here for backward compat with existing imports.
FAST_MODEL = "gpt-5.4-nano"      # ~$0.0001/call — routing, classification
FAST_EFFORT = "low"
DEFAULT_JUDGE_MODEL = "gpt-5.4-mini"  # ~$0.01/call — fallback when no task routing
DEFAULT_EFFORT = "medium"
HIGH_JUDGE_MODEL = "gpt-5.4"     # ~$0.08/call — deliberation, synthesis
HIGH_EFFORT = "high"

# ---------------------------------------------------------------------------
# Rate limiter — prevent API burst errors
# ---------------------------------------------------------------------------
# Simple token-bucket: max N calls per window, with async sleep if exceeded.

import asyncio as _asyncio
import collections as _collections
import threading as _threading

_RATE_WINDOW_S = 60  # 1-minute window
_RATE_MAX_CALLS = 30  # Max 30 calls per minute (well under OpenAI limits)
_call_timestamps: _collections.deque = _collections.deque()
_rate_lock: _asyncio.Lock = _asyncio.Lock()

# Spend-tracking lock (double-checked init to be safe across threads)
_spend_lock: _asyncio.Lock | None = None
_spend_lock_init: _threading.Lock = _threading.Lock()


def _get_spend_lock() -> _asyncio.Lock:
    """Return the async spend lock, creating it once in a thread-safe way."""
    global _spend_lock
    if _spend_lock is None:
        with _spend_lock_init:
            if _spend_lock is None:
                _spend_lock = _asyncio.Lock()
    return _spend_lock


async def _rate_limit() -> None:
    """Wait if we're sending too many API calls per minute."""
    async with _rate_lock:
        now = time.time()
        # Remove timestamps outside the window
        while _call_timestamps and _call_timestamps[0] < now - _RATE_WINDOW_S:
            _call_timestamps.popleft()

        if len(_call_timestamps) >= _RATE_MAX_CALLS:
            # Wait until the oldest call falls outside the window
            wait_time = _call_timestamps[0] + _RATE_WINDOW_S - now + 0.1
            if wait_time > 0:
                logger.info("Rate limit: waiting %.1fs (%d calls in window)",
                           wait_time, len(_call_timestamps))
                await _asyncio.sleep(wait_time)

        _call_timestamps.append(time.time())

# ---------------------------------------------------------------------------
# Shared Responses API helper
# ---------------------------------------------------------------------------


def _extract_responses_text(data: dict) -> str:
    """Extract text from an OpenAI Responses API response body.

    Handles the case where the model exhausts max_output_tokens on reasoning
    and never produces a message. Returns a descriptive error string (prefixed
    with [INCOMPLETE]) instead of silent empty string.
    """
    # Check for incomplete response (reasoning exhausted token budget)
    if data.get("status") == "incomplete":
        reason = data.get("incomplete_details", {}).get("reason", "unknown")
        usage = data.get("usage", {})
        reasoning = usage.get("output_tokens_details", {}).get("reasoning_tokens", 0)
        total = usage.get("output_tokens", 0)
        return f"[INCOMPLETE:{reason}] Model used {reasoning}/{total} output tokens on reasoning, no message produced."

    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if isinstance(c, dict) and c.get("type") == "output_text":
                    return c.get("text", "").strip()
    return ""


def _strip_code_fence(text: str) -> str:
    """Strip markdown code fences from LLM output."""
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return text


async def call_responses_api(
    prompt: str,
    *,
    task: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    instructions: str | None = None,
    web_search: bool = False,
    timeout_s: int = 180,
    max_output_tokens: int = 2000,
    critical: bool = False,
    telemetry_interface: str | None = None,
    telemetry_operation: str | None = None,
) -> str:
    """Shared helper: call OpenAI Responses API and return the text output.

    This is the single point of contact for all LLM calls across every
    service. The model router dynamically picks the optimal model based on
    the task type, benchmarks, and cost — agents don't hardcode models.

    Model Selection Priority:
    1. If `task` is provided → model_registry picks model + reasoning_effort
    2. If `model` is explicitly passed → uses that model (override)
    3. Fallback → gpt-5.4-mini with medium reasoning

    Parameters
    ----------
    task : str | None
        Task type for dynamic routing (e.g. "gate_evaluation", "compose_response",
        "swarm_role_response"). See model_registry.TASK_MODEL_ALLOCATION.
    prompt : str
        The user-facing prompt.
    model : str
        Model name (default gpt-5.4).
    reasoning_effort : str
        "low", "medium", or "high".
    instructions : str | None
        System-level instructions (maps to Responses API `instructions` field).
    web_search : bool
        Whether to include web_search_preview as an available tool.
    timeout_s : int
        HTTP timeout in seconds.

    Returns
    -------
    str
        The model's text response (extracted from Responses API output format).
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return "[OPENAI_API_KEY not configured]"

    # ── Daily spend guard ──
    global _daily_spend_usd, _daily_spend_date
    import datetime as _dt
    today = _dt.date.today().isoformat()
    async with _get_spend_lock():
        if _daily_spend_date != today:
            _daily_spend_usd = 0.0
            _daily_spend_date = today
        if _daily_spend_usd >= _DAILY_SPEND_LIMIT_USD and not critical:
            logger.error(
                "Daily spend limit ($%.2f) exceeded — skipping non-critical call "
                "(task=%s, accumulated=$%.2f)",
                _DAILY_SPEND_LIMIT_USD, task or "?", _daily_spend_usd,
            )
            return "[DAILY_SPEND_LIMIT_EXCEEDED]"

    # ── Dynamic model routing ──
    # If task is provided, let the model registry pick the optimal model.
    # Explicit model/reasoning_effort params override the registry.
    if task and (model is None or reasoning_effort is None):
        from .model_registry import get_model_for_task
        routed_model, routed_effort = get_model_for_task(task)
        if model is None:
            model = routed_model
        if reasoning_effort is None:
            reasoning_effort = routed_effort
        logger.debug("Task %s → model=%s, effort=%s", task, model, reasoning_effort)

    # Final fallback
    if model is None:
        model = DEFAULT_JUDGE_MODEL
    if reasoning_effort is None:
        reasoning_effort = DEFAULT_EFFORT

    # Rate limiting — prevent burst errors
    await _rate_limit()

    body: dict[str, Any] = {
        "model": model,
        "input": [{"role": "user", "content": prompt}],
        "reasoning": {"effort": reasoning_effort},
        "max_output_tokens": max_output_tokens,
    }
    if instructions:
        body["instructions"] = instructions
    if web_search:
        body["tools"] = [{"type": "web_search_preview", "search_context_size": "medium"}]
        body["tool_choice"] = "auto"

    # ── Model fallback chain ──
    # If the primary model fails (429, 500, 502, 503, timeout), try cheaper tiers.
    MODEL_FALLBACK_CHAIN: dict[str, list[str]] = {
        "gpt-5.4":      ["gpt-5.4-mini", "gpt-5.4-nano"],
        "gpt-5.4-mini": ["gpt-5.4-nano"],
        "gpt-5.4-nano": [],
        "o3":           ["o4-mini", "gpt-5.4-mini"],
        "o4-mini":      ["gpt-5.4-mini"],
    }
    models_to_try = [model] + MODEL_FALLBACK_CHAIN.get(model, [])

    t0 = time.time()
    telemetry_interface = telemetry_interface or Path(inspect.stack()[1].filename).stem
    telemetry_operation = telemetry_operation or task or "responses_api"
    last_error: Exception | None = None
    data: dict[str, Any] = {}
    fallback_from: str | None = None

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=15)) as client:
      for attempt_model in models_to_try:
        try:
            body["model"] = attempt_model
            resp = await client.post(
                OPENAI_RESPONSES_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            if attempt_model != model:
                fallback_from = model
                logger.warning("Model fallback: %s → %s (original failed)", model, attempt_model)
                _fallback_log.append({
                    "timestamp": time.time(),
                    "original_model": model,
                    "fallback_model": attempt_model,
                    "error": str(last_error),
                    "task": task or "unknown",
                })
            break  # Success — exit retry loop
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.ConnectError) as e:
            last_error = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            retryable = status in (429, 500, 502, 503) or isinstance(e, (httpx.TimeoutException, httpx.ConnectError))
            if retryable and attempt_model != models_to_try[-1]:
                logger.warning("Model %s failed (%s), trying fallback...", attempt_model, e)
                continue
            raise  # No more fallbacks — propagate

    elapsed_ms = int((time.time() - t0) * 1000)

    # Extract usage for telemetry
    usage = data.get("usage", {})
    actual_model = data.get("model", model)
    estimated_cost_usd = estimate_cost_usd(
        actual_model,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
    )
    async with _get_spend_lock():
        _daily_spend_usd += estimated_cost_usd
    _last_call_meta.update({
        "model": actual_model,
        "task": task or "unknown",
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "reasoning_tokens": usage.get("output_tokens_details", {}).get("reasoning_tokens", 0),
        "elapsed_ms": elapsed_ms,
        "reasoning_effort": reasoning_effort,
        "estimated_cost_usd": estimated_cost_usd,
        "telemetry_interface": telemetry_interface,
        "telemetry_operation": telemetry_operation,
    })
    if fallback_from:
        _last_call_meta["fallback_from"] = fallback_from
    logger.info(
        "LLM call: task=%s model=%s in=%d out=%d reason=%d %dms%s",
        task or "?", actual_model,
        usage.get("input_tokens", 0), usage.get("output_tokens", 0),
        usage.get("output_tokens_details", {}).get("reasoning_tokens", 0),
        elapsed_ms,
        f" (fallback from {fallback_from})" if fallback_from else "",
    )

    record_usage_event(
        interface=telemetry_interface,
        operation=telemetry_operation,
        model=actual_model,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        total_tokens=usage.get("total_tokens", 0),
        reasoning_tokens=usage.get("output_tokens_details", {}).get("reasoning_tokens", 0),
        duration_ms=elapsed_ms,
        success=True,
        metadata={
            "task": task or "unknown",
            "web_search": web_search,
            "reasoning_effort": reasoning_effort,
            "fallback_from": fallback_from,
        },
    )

    text = _extract_responses_text(data)

    # ── Auto-retry on incomplete response (reasoning exhausted token budget) ──
    # Root cause: model spends all max_output_tokens on reasoning, never produces
    # the message. Fix: double the token budget and retry once.
    if text.startswith("[INCOMPLETE:"):
        doubled = min(max_output_tokens * 2, 16000)
        logger.warning(
            "Incomplete response (reasoning exhausted %d tokens). "
            "Retrying with max_output_tokens=%d (was %d).",
            usage.get("output_tokens", 0), doubled, max_output_tokens,
        )
        body["max_output_tokens"] = doubled
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=15)) as retry_client:
            try:
                retry_resp = await retry_client.post(
                    OPENAI_RESPONSES_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                retry_resp.raise_for_status()
                retry_data = retry_resp.json()
                retry_text = _extract_responses_text(retry_data)

                # Update telemetry for the retry
                retry_usage = retry_data.get("usage", {})
                retry_cost = estimate_cost_usd(
                    retry_data.get("model", model),
                    retry_usage.get("input_tokens", 0),
                    retry_usage.get("output_tokens", 0),
                )
                async with _get_spend_lock():
                    _daily_spend_usd += retry_cost

                if not retry_text.startswith("[INCOMPLETE:"):
                    logger.info("Retry succeeded with %d tokens.", doubled)
                    return retry_text
                else:
                    logger.error("Retry still incomplete at %d tokens.", doubled)
            except Exception as retry_err:
                logger.error("Retry failed: %s", retry_err)

    return text


# Last call metadata — accessible for telemetry/eval without changing return type
_last_call_meta: dict[str, Any] = {}

# Fallback log — tracks every model fallback event for the model monitor
_fallback_log: list[dict[str, Any]] = []


def get_fallback_log() -> list[dict[str, Any]]:
    """Return the fallback event log. Used by model-monitor to track reliability."""
    return list(_fallback_log)


def get_last_call_meta() -> dict[str, Any]:
    """Return metadata from the most recent call_responses_api invocation.

    Includes: model, task, input_tokens, output_tokens, reasoning_tokens,
    elapsed_ms, reasoning_effort. Used by telemetry footers and eval harness.
    """
    return dict(_last_call_meta)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class GateResult:
    """Result of evaluating a single boolean gate."""
    name: str
    value: bool
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RubricResult:
    """Full rubric evaluation with all gates."""
    required_gates: list[GateResult] = field(default_factory=list)
    contextual_gates: list[GateResult] = field(default_factory=list)
    disqualifiers: list[GateResult] = field(default_factory=list)
    modifiers: list[GateResult] = field(default_factory=list)

    @property
    def all_required_pass(self) -> bool:
        return all(g.value for g in self.required_gates)

    @property
    def any_disqualifier(self) -> bool:
        return any(g.value for g in self.disqualifiers)

    @property
    def should_post(self) -> bool:
        return self.all_required_pass and not self.any_disqualifier

    @property
    def decision(self) -> str:
        return "POST" if self.should_post else "SKIP"

    @property
    def decision_chain(self) -> str:
        """Build a human-readable chain of gate results."""
        parts = []
        for g in self.required_gates:
            parts.append(f"{g.name}={'TRUE' if g.value else 'FALSE'}")
        for g in self.disqualifiers:
            if g.value:
                parts.append(f"DISQUALIFY:{g.name}=TRUE")
        return " → ".join(parts) + f" → {self.decision}"

    @property
    def blocking_gate(self) -> Optional[str]:
        """Return the first gate that blocked the decision, or None."""
        for g in self.required_gates:
            if not g.value:
                return f"{g.name}=FALSE: {g.reason}"
        for g in self.disqualifiers:
            if g.value:
                return f"DISQUALIFY:{g.name}=TRUE: {g.reason}"
        return None

    def to_dict(self) -> dict:
        return {
            "required_gates": {g.name: {"value": g.value, "reason": g.reason} for g in self.required_gates},
            "disqualifiers": {g.name: {"value": g.value, "reason": g.reason} for g in self.disqualifiers},
            "modifiers": {g.name: {"value": g.value, "reason": g.reason} for g in self.modifiers},
            "contextual_gates": {g.name: {"value": g.value, "reason": g.reason} for g in self.contextual_gates},
            "decision": self.decision,
            "decision_chain": self.decision_chain,
        }


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------


async def evaluate_gate(
    gate_name: str,
    gate_question: str,
    context: str,
    model: str | None = None,
    reasoning_effort: str | None = None,
    web_search: bool = False,
) -> GateResult:
    """Evaluate a single boolean gate using gpt-5.4 via Responses API."""
    prompt = f"""You are a boolean gate evaluator. Reason carefully through the question.

QUESTION: {gate_question}

CONTEXT:
{context}

Respond with ONLY a JSON object (no markdown, no explanation):
{{"value": true or false, "reason": "2-3 sentences explaining your judgment"}}"""

    try:
        text = await call_responses_api(
            prompt, task="gate_evaluation",
            web_search=web_search, timeout_s=120,
        )
        if not text:
            return GateResult(name=gate_name, value=False, reason="No response from model")

        text = _strip_code_fence(text)
        result = json.loads(text)
        return GateResult(
            name=gate_name,
            value=bool(result.get("value", False)),
            reason=str(result.get("reason", "No reason provided")),
        )

    except Exception as e:
        logger.error("Gate evaluation failed for %s: %s", gate_name, e)
        return GateResult(
            name=gate_name,
            value=False,
            reason=f"Evaluation error: {str(e)[:100]}",
        )


# Keep evaluate_gate_deep as an alias with web_search=True default
async def evaluate_gate_deep(
    gate_name: str,
    gate_question: str,
    context: str,
    model: str | None = None,
    reasoning_effort: str | None = None,
    web_search: bool = True,
) -> GateResult:
    """Evaluate a boolean gate with deep reasoning + optional web search."""
    return await evaluate_gate(
        gate_name, gate_question, context,
        web_search=web_search,
    )


async def evaluate_gates_batch(
    gates: list[dict[str, str]],
    context: str,
    model: str | None = None,
) -> list[GateResult]:
    """Evaluate multiple gates in a single LLM call for efficiency."""
    gates_text = "\n".join(
        f'{i+1}. GATE "{g["name"]}": {g["question"]}'
        for i, g in enumerate(gates)
    )

    prompt = f"""You are a boolean gate evaluator. For EACH gate below, evaluate the question against the context.

GATES:
{gates_text}

CONTEXT:
{context}

Respond with ONLY a JSON array (no markdown, no explanation). Each element must have:
{{"name": "gate_name", "value": true or false, "reason": "one sentence"}}"""

    try:
        text = await call_responses_api(
            prompt, task="gate_batch", timeout_s=120,
        )
        if not text:
            return [GateResult(name=g["name"], value=False, reason="No response") for g in gates]

        text = _strip_code_fence(text)
        results = json.loads(text)
        return [
            GateResult(
                name=r.get("name", gates[i]["name"]),
                value=bool(r.get("value", False)),
                reason=str(r.get("reason", "No reason")),
            )
            for i, r in enumerate(results)
        ]

    except Exception as e:
        logger.error("Batch gate evaluation failed: %s", e)
        return [
            GateResult(name=g["name"], value=False, reason=f"Batch eval error: {str(e)[:80]}")
            for g in gates
        ]


async def compose_response(
    opportunity_type: str,
    context: str,
    response_guidelines: str = "",
    model: str | None = None,
    system_prompt_override: Optional[str] = None,
) -> str:
    """Compose a Slack response using gpt-5.4, following the Calculus Made Easy pattern."""
    user_prompt = f"""Compose a Slack message responding to the conversation below.

OPPORTUNITY TYPE: {opportunity_type}
CONTEXT:
{context}

RESPONSE RULES:
1. Follow "Calculus Made Easy" structure: plain English analogy first, then specifics, technical footnotes last
2. Default to the shortest useful answer: 3-6 sentences or 3-5 bullets, usually under 150 words
3. Expand only if the user explicitly asks for detail or the situation requires it; hard cap 300 words
4. Use one analogy at most and do not repeat the same conclusion in multiple ways
5. Slack mrkdwn format: *bold*, _italic_, `code`. No ## headings, no **bold**, no markdown tables.
6. Lead with actionable insight — no preamble ("Hi team!", "I noticed that...")
7. Include source citation: _(from `file/path`)_ or _(investor brief, section X)_
8. End with concrete next step if applicable
{response_guidelines}

Write the message now. Output ONLY the message text, no quotes or wrapping."""

    try:
        return await call_responses_api(
            user_prompt,
            task="compose_response",
            instructions=system_prompt_override,
            web_search=True,  # Enable web search for response composition
            timeout_s=120,
        )
    except Exception as e:
        logger.error("Response composition failed: %s", e)
        return f"Unable to compose response: {str(e)[:100]}"
