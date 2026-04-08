"""OpenRouter Model Rotation — zero-friction free model discovery and failover.

Automatically discovers, ranks, and rotates through free and cheap models on
OpenRouter. When a model hits rate limits or goes offline, seamlessly rotates
to the next best option. Falls back to the project's default paid models
(OpenAI/Anthropic) if no free models are usable.

Key behaviors:
  - Refreshes the free model list from OpenRouter API every 6 hours
  - Ranks models by context window, speed, and tool-calling support
  - On rate limit (429) or timeout, rotates to the next model instantly
  - Tracks per-model telemetry: latency, tokens/sec, error rate, rate limits
  - Falls back to OPENAI_API_KEY models if all free models are exhausted
  - Reports status via ta.nemoclaw.status MCP tool

Env vars:
  OPENROUTER_API_KEY  — Required for free tier access
  NEMOCLAW_MODEL      — Override: pin to a specific model (skips rotation)
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

logger = logging.getLogger(__name__)

_OPENROUTER_API = "https://openrouter.ai/api/v1"

# Minimum requirements for a model to be usable by NemoClaw
_MIN_CONTEXT = 8_000
_PREFER_TOOL_CALLING = True

# How often to refresh the model list (seconds)
_REFRESH_INTERVAL = 6 * 3600  # 6 hours

# Only rotate through the top N ranked models — ignore the long tail
_MAX_ROTATION_POOL = 10

# Models we know work well for tool calling — prefer these when free
_PREFERRED_FAMILIES = [
    "nvidia/nemotron",
    "mistralai/mistral-small",
    "qwen/qwen3-coder",
    "qwen/qwen-2.5",
    "meta-llama/llama-3",
    "google/gemma",
    "deepseek/deepseek",
]


@dataclass
class ModelTelemetry:
    """Per-model tracking for rotation decisions."""
    model_id: str
    calls: int = 0
    errors: int = 0
    rate_limits: int = 0
    total_latency_ms: float = 0.0
    total_tokens: int = 0
    last_error: str = ""
    last_error_at: float = 0.0
    last_success_at: float = 0.0
    disabled_until: float = 0.0  # Unix timestamp — skip until this time

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / max(self.calls, 1)

    @property
    def tokens_per_sec(self) -> float:
        if self.total_latency_ms == 0:
            return 0.0
        return self.total_tokens / (self.total_latency_ms / 1000)

    @property
    def error_rate(self) -> float:
        return self.errors / max(self.calls, 1)

    @property
    def is_available(self) -> bool:
        return time.time() >= self.disabled_until

    def record_success(self, latency_ms: float, tokens: int) -> None:
        self.calls += 1
        self.total_latency_ms += latency_ms
        self.total_tokens += tokens
        self.last_success_at = time.time()

    def record_error(self, error: str, is_rate_limit: bool = False) -> None:
        self.calls += 1
        self.errors += 1
        self.last_error = error
        self.last_error_at = time.time()
        if is_rate_limit:
            self.rate_limits += 1
            # Back off: 60s after first rate limit, doubles each time (max 30min)
            backoff = min(60 * (2 ** (self.rate_limits - 1)), 1800)
            self.disabled_until = time.time() + backoff
            logger.warning(
                "Model %s rate limited (%d times), disabled for %ds",
                self.model_id, self.rate_limits, backoff,
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "calls": self.calls,
            "errors": self.errors,
            "rate_limits": self.rate_limits,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "tokens_per_sec": round(self.tokens_per_sec, 1),
            "error_rate": round(self.error_rate, 3),
            "available": self.is_available,
            "disabled_until": self.disabled_until if not self.is_available else None,
        }


@dataclass
class FreeModel:
    """A free model discovered from OpenRouter."""
    id: str
    name: str
    context_length: int
    supports_tools: bool
    pricing_input: float  # per token
    pricing_output: float
    family_rank: int = 99  # lower = preferred

    @property
    def score(self) -> float:
        """Higher is better. Prefer: tool support, large context, known families."""
        s = 0.0
        if self.supports_tools:
            s += 100
        s += min(self.context_length / 1000, 50)  # Up to 50 pts for context
        s += max(0, 20 - self.family_rank)  # Up to 20 pts for preferred family
        return s


class OpenRouterRotation:
    """Singleton manager for free model discovery and rotation."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._models: list[FreeModel] = []
        self._current_idx: int = 0
        self._telemetry: dict[str, ModelTelemetry] = {}
        self._last_refresh: float = 0.0
        self._api_key = os.getenv("OPENROUTER_API_KEY", "")
        self._pinned_model = os.getenv("NEMOCLAW_MODEL", "")
        self._total_discovered: int = 0  # All free models found before top-N filter

    def _should_refresh(self) -> bool:
        return time.time() - self._last_refresh > _REFRESH_INTERVAL

    def refresh_models(self) -> list[FreeModel]:
        """Fetch free models from OpenRouter API and rank them."""
        if not self._api_key:
            return []

        try:
            req = urllib.request.Request(
                f"{_OPENROUTER_API}/models",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "HTTP-Referer": "https://retention.com",
                    "X-Title": "retention.sh NemoClaw",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except Exception as exc:
            logger.warning("Failed to fetch OpenRouter models: %s", exc)
            return self._models  # Keep existing list

        free_models = []
        for m in data.get("data", []):
            pricing = m.get("pricing", {})
            input_price = float(pricing.get("prompt", "1") or "1")
            output_price = float(pricing.get("completion", "1") or "1")

            # Only free models (both input and output cost = 0)
            if input_price > 0 or output_price > 0:
                continue

            ctx = m.get("context_length", 0)
            if ctx < _MIN_CONTEXT:
                continue

            model_id = m.get("id", "")
            supports_tools = "tools" in (m.get("supported_parameters", []) or [])

            # Rank by preferred family
            family_rank = 99
            for i, fam in enumerate(_PREFERRED_FAMILIES):
                if model_id.startswith(fam):
                    family_rank = i
                    break

            free_models.append(FreeModel(
                id=model_id,
                name=m.get("name", model_id),
                context_length=ctx,
                supports_tools=supports_tools,
                pricing_input=input_price,
                pricing_output=output_price,
                family_rank=family_rank,
            ))

        # Sort by score descending, keep only top 10
        free_models.sort(key=lambda m: m.score, reverse=True)
        total_discovered = len(free_models)
        free_models = free_models[:_MAX_ROTATION_POOL]

        with self._lock:
            self._models = free_models
            self._total_discovered = total_discovered
            self._current_idx = 0
            self._last_refresh = time.time()

        logger.info(
            "OpenRouter refresh: %d free models found, kept top %d. Pool: %s",
            total_discovered, len(free_models),
            [m.id for m in free_models],
        )
        return free_models

    def get_current_model(self, auto_refresh: bool = True) -> str | None:
        """Get the current best model, optionally refreshing if stale.

        Set auto_refresh=False to avoid blocking the async event loop.
        """
        if self._pinned_model:
            return self._pinned_model

        if auto_refresh and (self._should_refresh() or not self._models):
            self.refresh_models()

        with self._lock:
            if not self._models:
                return None
            # Find next available model
            for _ in range(len(self._models)):
                if self._current_idx >= len(self._models):
                    self._current_idx = 0
                model = self._models[self._current_idx]
                telemetry = self._telemetry.get(model.id)
                if telemetry and not telemetry.is_available:
                    self._current_idx += 1
                    continue
                return model.id

        return None  # All models exhausted

    def rotate_next(self) -> str | None:
        """Move to the next model in the ranked list."""
        if self._pinned_model:
            return self._pinned_model

        with self._lock:
            self._current_idx += 1
            if self._current_idx >= len(self._models):
                self._current_idx = 0

        return self.get_current_model(auto_refresh=False)

    def record_success(self, model_id: str, latency_ms: float, tokens: int) -> None:
        with self._lock:
            if model_id not in self._telemetry:
                self._telemetry[model_id] = ModelTelemetry(model_id=model_id)
            self._telemetry[model_id].record_success(latency_ms, tokens)

    def record_error(self, model_id: str, error: str, is_rate_limit: bool = False) -> None:
        with self._lock:
            if model_id not in self._telemetry:
                self._telemetry[model_id] = ModelTelemetry(model_id=model_id)
            self._telemetry[model_id].record_error(error, is_rate_limit)

    def get_telemetry(self) -> dict[str, Any]:
        """Full telemetry report — never blocks on refresh."""
        with self._lock:
            current = self.get_current_model(auto_refresh=False)
            return {
                "current_model": current,
                "pinned": bool(self._pinned_model),
                "total_discovered": self._total_discovered,
                "rotation_pool_size": len(self._models),
                "max_pool": _MAX_ROTATION_POOL,
                "last_refresh": self._last_refresh,
                "models_ranked": [
                    {
                        "id": m.id,
                        "name": m.name,
                        "context": m.context_length,
                        "tools": m.supports_tools,
                        "score": round(m.score, 1),
                        "telemetry": self._telemetry[m.id].to_dict()
                        if m.id in self._telemetry else None,
                    }
                    for m in self._models[:10]  # Top 10
                ],
                "fallback": "OpenAI (OPENAI_API_KEY)" if os.getenv("OPENAI_API_KEY") else "none",
            }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_rotation: OpenRouterRotation | None = None


def get_rotation() -> OpenRouterRotation:
    global _rotation
    if _rotation is None:
        _rotation = OpenRouterRotation()
    return _rotation
