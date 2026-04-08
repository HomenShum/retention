"""Model Registry — multi-provider model catalog with task-based routing.

Maintains a catalog of available LLM models across providers (OpenAI,
Anthropic, Google, DeepSeek) with pricing, capabilities, and benchmark
scores. The router picks the optimal model for each task type based on
quality/cost/speed trade-offs.

Updated for March 2026 pricing from:
- https://openai.com/api/pricing/
- https://platform.claude.com/docs/en/about-claude/pricing
- https://artificialanalysis.ai/leaderboards/models

The model_monitor cron (weekly) refreshes benchmarks via web search
and proposes allocation changes based on cost vs quality analysis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelSpec:
    """Specification for a single LLM model."""

    id: str                          # API model ID (e.g. "gpt-4.1-nano")
    provider: str                    # "openai" | "anthropic" | "google" | "deepseek"
    name: str                        # Human-readable name
    input_cost_per_mtok: float       # $ per 1M input tokens
    output_cost_per_mtok: float      # $ per 1M output tokens
    context_window: int              # Max tokens
    intelligence_score: int = 0      # 0-100 from benchmarks (Artificial Analysis)
    coding_score: int = 0            # 0-100 SWE-bench or equivalent
    speed_tps: int = 0               # Tokens per second (output)
    supports_reasoning: bool = False # Has reasoning_effort parameter
    supports_web_search: bool = False # Has web_search_preview tool
    supports_tools: bool = True      # Function calling support
    tier: str = "standard"           # "fast" | "standard" | "deep" | "ultra"
    notes: str = ""


# ── OpenAI Models (GPT-5 family — current generation, March 2026) ──────

# --- Nano tier (cheapest, fastest) ---

GPT_5_NANO = ModelSpec(
    id="gpt-5.4-nano",
    provider="openai",
    name="GPT-5 Nano",
    input_cost_per_mtok=0.05,
    output_cost_per_mtok=0.40,
    context_window=128_000,
    intelligence_score=28,
    speed_tps=400,
    supports_reasoning=False,
    tier="fast",
    notes="Cheapest GPT-5. Replaces gpt-4.1-nano. Routing, classification, extraction.",
)

GPT_5_4_NANO = ModelSpec(
    id="gpt-5.4-nano",
    provider="openai",
    name="GPT-5.4 Nano",
    input_cost_per_mtok=0.20,
    output_cost_per_mtok=1.25,
    context_window=128_000,
    intelligence_score=35,
    speed_tps=350,
    supports_reasoning=False,
    tier="fast",
    notes="Newest nano. 4x smarter than gpt-5.4-nano. Great for structured extraction.",
)

# --- Mini tier (fast + capable) ---

GPT_5_MINI = ModelSpec(
    id="gpt-5.4-mini",
    provider="openai",
    name="GPT-5 Mini",
    input_cost_per_mtok=0.25,
    output_cost_per_mtok=2.00,
    context_window=128_000,
    intelligence_score=40,
    speed_tps=200,
    supports_reasoning=True,
    tier="fast",
    notes="5x cheaper than GPT-5. Good for summaries, digests, analysis.",
)

GPT_5_4_MINI = ModelSpec(
    id="gpt-5.4-mini",
    provider="openai",
    name="GPT-5.4 Mini",
    input_cost_per_mtok=0.75,
    output_cost_per_mtok=4.50,
    context_window=400_000,
    intelligence_score=45,
    coding_score=60,
    speed_tps=150,
    supports_reasoning=True,
    supports_web_search=True,
    tier="standard",
    notes="Most capable mini. 400K context. Good for code review, composition.",
)

# --- Standard tier (full models) ---

GPT_5 = ModelSpec(
    id="gpt-5",
    provider="openai",
    name="GPT-5",
    input_cost_per_mtok=1.25,
    output_cost_per_mtok=10.00,
    context_window=128_000,
    intelligence_score=50,
    coding_score=70,
    speed_tps=80,
    supports_reasoning=True,
    supports_web_search=True,
    tier="standard",
    notes="Strong general model. Good for complex analysis.",
)

GPT_5_2 = ModelSpec(
    id="gpt-5.4",
    provider="openai",
    name="GPT-5.4",
    input_cost_per_mtok=1.75,
    output_cost_per_mtok=14.00,
    context_window=256_000,
    intelligence_score=53,
    coding_score=72,
    speed_tps=70,
    supports_reasoning=True,
    supports_web_search=True,
    tier="standard",
    notes="Previous flagship. Still strong for general tasks.",
)

GPT_5_3_CODEX = ModelSpec(
    id="gpt-5.3-codex",
    provider="openai",
    name="GPT-5.3 Codex",
    input_cost_per_mtok=1.75,
    output_cost_per_mtok=14.00,
    context_window=256_000,
    intelligence_score=54,
    coding_score=78,
    speed_tps=65,
    supports_reasoning=True,
    supports_web_search=True,
    tier="deep",
    notes="Agentic coding specialist. 91.5% GPQA. Best for long-running code tasks.",
)

# --- Deep tier (flagship) ---

GPT_5_4 = ModelSpec(
    id="gpt-5.4",
    provider="openai",
    name="GPT-5.4",
    input_cost_per_mtok=2.50,
    output_cost_per_mtok=15.00,
    context_window=1_050_000,
    intelligence_score=57,
    coding_score=75,
    speed_tps=60,
    supports_reasoning=True,
    supports_web_search=True,
    tier="deep",
    notes="Current flagship. 92% GPQA. Best for complex reasoning and agentic tasks.",
)

# --- Reasoning specialists (o-series) ---

O4_MINI = ModelSpec(
    id="o4-mini",
    provider="openai",
    name="o4-mini",
    input_cost_per_mtok=1.10,
    output_cost_per_mtok=4.40,
    context_window=200_000,
    intelligence_score=42,
    coding_score=60,
    speed_tps=80,
    supports_reasoning=True,
    tier="standard",
    notes="Best value reasoning. Math, logic, coding puzzles. Half the cost of o3.",
)

O3 = ModelSpec(
    id="o3",
    provider="openai",
    name="o3",
    input_cost_per_mtok=2.00,
    output_cost_per_mtok=8.00,
    context_window=200_000,
    intelligence_score=48,
    coding_score=68,
    speed_tps=50,
    supports_reasoning=True,
    tier="deep",
    notes="Reasoning specialist. Math, multi-step logic, scientific analysis.",
)

# ── Anthropic Models ───────────────────────────────────────────────────

CLAUDE_HAIKU_4_5 = ModelSpec(
    id="claude-haiku-4-5-20250214",
    provider="anthropic",
    name="Claude Haiku 4.5",
    input_cost_per_mtok=1.00,
    output_cost_per_mtok=5.00,
    context_window=200_000,
    intelligence_score=35,
    speed_tps=200,
    supports_reasoning=False,
    supports_tools=True,
    tier="fast",
    notes="Fast Anthropic model. Good for classification, extraction.",
)

CLAUDE_SONNET_4_6 = ModelSpec(
    id="claude-sonnet-4-6-20260220",
    provider="anthropic",
    name="Claude Sonnet 4.6",
    input_cost_per_mtok=3.00,
    output_cost_per_mtok=15.00,
    context_window=1_000_000,
    intelligence_score=52,
    coding_score=80,
    speed_tps=80,
    supports_reasoning=True,
    supports_web_search=False,
    supports_tools=True,
    tier="deep",
    notes="Best coding model at mid-tier price. 79.6% SWE-bench.",
)

CLAUDE_OPUS_4_6 = ModelSpec(
    id="claude-opus-4-6-20260220",
    provider="anthropic",
    name="Claude Opus 4.6",
    input_cost_per_mtok=5.00,
    output_cost_per_mtok=25.00,
    context_window=1_000_000,
    intelligence_score=53,
    coding_score=81,
    speed_tps=40,
    supports_reasoning=True,
    supports_web_search=False,
    supports_tools=True,
    tier="ultra",
    notes="Highest coding benchmark. 80.8% SWE-bench. Most expensive.",
)

# ── Google Models ──────────────────────────────────────────────────────

GEMINI_2_5_FLASH = ModelSpec(
    id="gemini-2.5-flash",
    provider="google",
    name="Gemini 2.5 Flash",
    input_cost_per_mtok=0.30,
    output_cost_per_mtok=2.50,
    context_window=1_000_000,
    intelligence_score=38,
    speed_tps=250,
    supports_reasoning=True,
    supports_web_search=True,
    tier="fast",
    notes="Very cheap with 1M context. Good for large-context extraction.",
)

GEMINI_2_5_PRO = ModelSpec(
    id="gemini-2.5-pro",
    provider="google",
    name="Gemini 2.5 Pro",
    input_cost_per_mtok=1.25,
    output_cost_per_mtok=10.00,
    context_window=1_000_000,
    intelligence_score=50,
    speed_tps=100,
    supports_reasoning=True,
    supports_web_search=True,
    tier="standard",
    notes="Strong competitor to GPT-5 at same price. 1M context.",
)

# ── NVIDIA Models ─────────────────────────────────────────────────────

NEMOTRON_3_SUPER = ModelSpec(
    id="nvidia/nemotron-3-super-120b-a12b",
    provider="nvidia",
    name="Nemotron 3 Super",
    input_cost_per_mtok=0.08,
    output_cost_per_mtok=0.64,
    context_window=1_000_000,
    intelligence_score=48,
    coding_score=55,
    speed_tps=120,
    supports_reasoning=False,
    supports_tools=True,
    tier="fast",
    notes="MoE 120B→12B active. 1M context. OpenAI-compatible API via NIM. Fast agentic QA.",
)

# ── OpenRouter Free/Cheap Models ─────────────────────────────────────

NEMOTRON_SUPER_FREE = ModelSpec(
    id="nvidia/nemotron-3-super-49b-v1:free",
    provider="openrouter",
    name="Nemotron 3 Super 49B (Free)",
    input_cost_per_mtok=0.0,
    output_cost_per_mtok=0.0,
    context_window=32_768,
    intelligence_score=42,
    coding_score=48,
    speed_tps=80,
    supports_reasoning=False,
    supports_tools=True,
    tier="fast",
    notes="Free via OpenRouter. 49B Nemotron Super. Good for QA tool calling and analysis.",
)

MISTRAL_SMALL_FREE = ModelSpec(
    id="mistralai/mistral-small-3.2-24b-instruct:free",
    provider="openrouter",
    name="Mistral Small 3.2 M2.7 (Free)",
    input_cost_per_mtok=0.0,
    output_cost_per_mtok=0.0,
    context_window=128_000,
    intelligence_score=40,
    coding_score=50,
    speed_tps=100,
    supports_reasoning=False,
    supports_tools=True,
    tier="fast",
    notes="Free via OpenRouter. 24B Mistral Small 3.2. 128K context. Fast tool calling.",
)

# ── DeepSeek Models ────────────────────────────────────────────────────

DEEPSEEK_V3_2 = ModelSpec(
    id="deepseek-chat",
    provider="deepseek",
    name="DeepSeek V3.2",
    input_cost_per_mtok=0.28,
    output_cost_per_mtok=0.42,
    context_window=128_000,
    intelligence_score=45,
    coding_score=55,
    speed_tps=150,
    supports_reasoning=False,
    supports_tools=True,
    tier="fast",
    notes="Extremely cheap. S-tier intelligence at 10x less cost.",
)


# ---------------------------------------------------------------------------
# Model catalog (all available models)
# ---------------------------------------------------------------------------

MODEL_CATALOG: dict[str, ModelSpec] = {
    m.id: m for m in [
        # OpenAI — GPT-5 family (current generation)
        GPT_5_NANO, GPT_5_4_NANO,               # Nano tier
        GPT_5_MINI, GPT_5_4_MINI,               # Mini tier
        GPT_5, GPT_5_2, GPT_5_3_CODEX,          # Standard tier
        GPT_5_4,                                  # Deep tier (flagship)
        O4_MINI, O3,                              # Reasoning specialists
        # Anthropic
        CLAUDE_HAIKU_4_5, CLAUDE_SONNET_4_6, CLAUDE_OPUS_4_6,
        # Google
        GEMINI_2_5_FLASH, GEMINI_2_5_PRO,
        # NVIDIA
        NEMOTRON_3_SUPER,
        # OpenRouter (free tier)
        NEMOTRON_SUPER_FREE, MISTRAL_SMALL_FREE,
        # DeepSeek
        DEEPSEEK_V3_2,
    ]
}


# ---------------------------------------------------------------------------
# Task → Model routing (the core allocation table)
# ---------------------------------------------------------------------------

# Each task type maps to a model ID. The model_monitor cron can update
# this table based on benchmark changes, cost analysis, and A/B results.

TASK_MODEL_ALLOCATION: dict[str, dict[str, str]] = {
    # Task name → { "model": model_id, "reasoning": effort, "reason": why }

    # Tier 1: NANO — routing, binary decisions ($0.20/M in)
    "speaker_selection":   {"model": "gpt-5.4-nano",  "reasoning": "low",    "reason": "Simple JSON routing, cheapest model"},
    "consensus_check":     {"model": "gpt-5.4-nano",  "reasoning": "low",    "reason": "Binary yes/no with one sentence"},

    # Tier 2: MINI — summaries, extraction, compaction, swarm deliberation ($0.75/M in)
    # gpt-5.4-mini: 54.4% SWE-Bench, 400K context, 2x faster than gpt-5 mini
    # "outperformed GPT-5.1 and GPT-4.1 with zero prompt changes" — Whoop
    "context_compression": {"model": "gpt-5.4-mini",  "reasoning": "medium", "reason": "Intent-residual extraction needs structured reasoning"},
    "memory_extraction":   {"model": "gpt-5.4-mini",  "reasoning": "low",    "reason": "Extract decisions from text"},
    "topic_extraction":    {"model": "gpt-5.4-mini",  "reasoning": "low",    "reason": "Extract topics from messages"},
    "digest_composition":  {"model": "gpt-5.4-mini",  "reasoning": "low",    "reason": "Summarize activity metrics"},
    "standup_synthesis":   {"model": "gpt-5.4-mini",  "reasoning": "low",    "reason": "Summarize commits + messages"},
    "evolve_synthesis":    {"model": "gpt-5.4-mini",  "reasoning": "low",    "reason": "Analyze health metrics"},
    "drift_categorize":    {"model": "gpt-5.4-mini",  "reasoning": "low",    "reason": "Categorize commits by section"},
    "housekeeping":        {"model": "gpt-5.4-mini",  "reasoning": "low",    "reason": "Summarize and clean old messages"},
    "swarm_role_response": {"model": "gpt-5.4-mini",  "reasoning": "medium", "reason": "MiroFish deliberation — mini is 3x cheaper, 2x faster, only 3.3pt below full on SWE-Bench"},
    "action_items":        {"model": "gpt-5.4-mini",  "reasoning": "medium", "reason": "Extract structured data from transcript"},
    "topic_selection":     {"model": "gpt-5.4-mini",  "reasoning": "medium", "reason": "Weigh priorities — mini sufficient with intent-residual context"},

    # Tier 3: FULL — gates, user-facing composition, final synthesis ($2.50/M in)
    # Reserve gpt-5.4 for tasks where quality delta justifies 3x cost:
    # boolean gates (nuanced judgment), user-facing responses, final synthesis
    "gate_evaluation":     {"model": "gpt-5.4",       "reasoning": "medium", "reason": "Boolean rubric needs nuanced judgment — accuracy critical"},
    "gate_batch":          {"model": "gpt-5.4",       "reasoning": "medium", "reason": "Multiple gates in one call — accuracy critical"},
    "compose_response":    {"model": "gpt-5.4",       "reasoning": "medium", "reason": "User-facing responses need highest quality"},
    "deep_sim_research":   {"model": "gpt-5.4-mini",  "reasoning": "medium", "reason": "Research with tool calling — mini has 400K context, sufficient"},
    "deep_sim_role":       {"model": "gpt-5.4-mini",  "reasoning": "medium", "reason": "Deep sim role responses — mini with intent-residual compaction"},
    "deep_sim_synthesis":  {"model": "gpt-5.4",       "reasoning": "high",   "reason": "Final synthesis — must be highest quality for decision-making"},
    "strategy_brief":      {"model": "gpt-5.4",       "reasoning": "high",   "reason": "Main orchestrator agent — user-facing, must be best"},
}


def get_model_for_task(task: str) -> tuple[str, str]:
    """Get the optimal model and reasoning effort for a task.

    Returns (model_id, reasoning_effort) tuple.
    Falls back to gpt-5.4-mini/medium if task not found.
    """
    alloc = TASK_MODEL_ALLOCATION.get(task, {})
    model = alloc.get("model", "gpt-5.4-mini")
    reasoning = alloc.get("reasoning", "medium")
    return model, reasoning


def estimate_monthly_cost() -> dict[str, Any]:
    """Estimate monthly cost based on current allocations and call frequencies.

    Returns a breakdown by task with total monthly estimate.
    """
    # Estimated calls per hour for each task
    CALL_FREQ_PER_HOUR: dict[str, float] = {
        "speaker_selection": 1.5,
        "consensus_check": 1.0,
        "context_compression": 1.0,
        "memory_extraction": 0.5,
        "topic_extraction": 0.5,
        "digest_composition": 1.0,
        "standup_synthesis": 0.04,
        "evolve_synthesis": 0.04,
        "drift_categorize": 0.006,
        "housekeeping": 0.25,
        "gate_evaluation": 2.0,
        "gate_batch": 1.0,
        "compose_response": 0.5,
        "action_items": 0.5,
        "topic_selection": 0.5,
        "swarm_role_response": 3.0,
        "deep_sim_research": 0.04,
        "deep_sim_role": 0.2,
        "deep_sim_synthesis": 0.04,
        "strategy_brief": 0.1,
    }

    # Average tokens per call (rough estimates)
    AVG_TOKENS: dict[str, tuple[int, int]] = {  # (input, output)
        "speaker_selection": (500, 50),
        "consensus_check": (800, 50),
        "context_compression": (2000, 300),
        "memory_extraction": (1000, 200),
        "topic_extraction": (500, 100),
        "digest_composition": (1000, 300),
        "standup_synthesis": (1500, 400),
        "evolve_synthesis": (2000, 600),
        "drift_categorize": (1500, 300),
        "housekeeping": (1000, 200),
        "gate_evaluation": (800, 100),
        "gate_batch": (1500, 300),
        "compose_response": (1000, 500),
        "action_items": (3000, 300),
        "topic_selection": (2000, 100),
        "swarm_role_response": (3000, 500),
        "deep_sim_research": (2000, 2000),
        "deep_sim_role": (3000, 800),
        "deep_sim_synthesis": (5000, 800),
        "strategy_brief": (3000, 1000),
    }

    breakdown = []
    total_monthly = 0.0

    for task, alloc in TASK_MODEL_ALLOCATION.items():
        model_id = alloc["model"]
        spec = MODEL_CATALOG.get(model_id)
        if not spec:
            continue

        freq = CALL_FREQ_PER_HOUR.get(task, 0.1)
        in_tok, out_tok = AVG_TOKENS.get(task, (1000, 200))

        cost_per_call = (
            (in_tok / 1_000_000) * spec.input_cost_per_mtok +
            (out_tok / 1_000_000) * spec.output_cost_per_mtok
        )
        monthly = cost_per_call * freq * 24 * 30

        breakdown.append({
            "task": task,
            "model": model_id,
            "tier": spec.tier,
            "cost_per_call": round(cost_per_call, 5),
            "calls_per_hour": freq,
            "monthly_cost": round(monthly, 2),
        })
        total_monthly += monthly

    breakdown.sort(key=lambda x: x["monthly_cost"], reverse=True)

    return {
        "total_monthly": round(total_monthly, 2),
        "breakdown": breakdown,
        "model_count": len(set(a["model"] for a in TASK_MODEL_ALLOCATION.values())),
        "provider_count": len(set(
            MODEL_CATALOG[a["model"]].provider
            for a in TASK_MODEL_ALLOCATION.values()
            if a["model"] in MODEL_CATALOG
        )),
    }


# ---------------------------------------------------------------------------
# Eval harness — dogfood model allocation with real samples
# ---------------------------------------------------------------------------

async def eval_harness(
    samples: list[dict],
    task: str = "gate_evaluation",
    models: list[str] | None = None,
) -> dict:
    """Run eval samples through multiple models and compare performance.

    Each sample: {"prompt": str, "expected": str, "context": str}
    Returns: {"results": [...], "recommendations": [...], "summary": {...}}
    """
    import time
    from .llm_judge import call_responses_api

    if models is None:
        models = ["gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.4"]

    results: list[dict] = []

    for model_id in models:
        spec = MODEL_CATALOG.get(model_id)
        if not spec:
            logger.warning("eval_harness: model %s not in catalog, skipping", model_id)
            continue

        correct = 0
        total_latency = 0.0
        total_input_tokens = 0
        total_output_tokens = 0

        for sample in samples:
            prompt = sample.get("prompt", "")
            expected = sample.get("expected", "")
            context = sample.get("context", "")

            full_prompt = f"{context}\n\n{prompt}" if context else prompt

            t0 = time.monotonic()
            try:
                output = await call_responses_api(
                    full_prompt,
                    task=task,
                    model=model_id,
                    reasoning_effort="low",
                    timeout_s=60,
                )
            except Exception as e:
                logger.error("eval_harness: %s failed on sample: %s", model_id, e)
                output = ""
            latency = time.monotonic() - t0
            total_latency += latency

            # Fuzzy correctness: check if expected answer appears in output
            is_correct = _fuzzy_match(expected, output)
            if is_correct:
                correct += 1

            # Estimate token counts from character lengths
            est_input = len(full_prompt) // 4
            est_output = len(output) // 4
            total_input_tokens += est_input
            total_output_tokens += est_output

        n = len(samples) or 1
        accuracy = correct / n
        avg_latency = total_latency / n

        # Cost estimate
        cost_per_call = 0.0
        if spec:
            cost_per_call = (
                (total_input_tokens / n / 1_000_000) * spec.input_cost_per_mtok
                + (total_output_tokens / n / 1_000_000) * spec.output_cost_per_mtok
            )

        results.append({
            "model": model_id,
            "tier": spec.tier if spec else "unknown",
            "accuracy": round(accuracy, 4),
            "avg_latency_s": round(avg_latency, 3),
            "avg_cost_per_call": round(cost_per_call, 6),
            "correct": correct,
            "total": n,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
        })

    # Generate recommendations
    recommendations = _generate_recommendations(results, task)

    # Summary
    best_accuracy = max(results, key=lambda r: r["accuracy"]) if results else {}
    cheapest = min(results, key=lambda r: r["avg_cost_per_call"]) if results else {}

    summary = {
        "task": task,
        "samples_count": len(samples),
        "models_tested": len(results),
        "best_accuracy_model": best_accuracy.get("model", ""),
        "best_accuracy": best_accuracy.get("accuracy", 0),
        "cheapest_model": cheapest.get("model", ""),
        "cheapest_cost": cheapest.get("avg_cost_per_call", 0),
    }

    return {
        "results": results,
        "recommendations": recommendations,
        "summary": summary,
    }


def _fuzzy_match(expected: str, output: str) -> bool:
    """Check if expected answer is contained in or closely matches output."""
    if not expected or not output:
        return False

    expected_lower = expected.strip().lower()
    output_lower = output.strip().lower()

    # Exact containment
    if expected_lower in output_lower:
        return True

    # Check if key tokens from expected appear in output
    expected_tokens = set(expected_lower.split())
    output_tokens = set(output_lower.split())
    if expected_tokens and expected_tokens.issubset(output_tokens):
        return True

    # Token overlap ratio
    if expected_tokens:
        overlap = len(expected_tokens & output_tokens) / len(expected_tokens)
        if overlap >= 0.8:
            return True

    return False


def _generate_recommendations(results: list[dict], task: str) -> list[str]:
    """Generate upgrade/downgrade recommendations from eval results."""
    if len(results) < 2:
        return ["Not enough models tested to generate recommendations."]

    recs = []
    sorted_by_accuracy = sorted(results, key=lambda r: r["accuracy"], reverse=True)
    sorted_by_cost = sorted(results, key=lambda r: r["avg_cost_per_call"])

    best = sorted_by_accuracy[0]
    cheapest = sorted_by_cost[0]

    # Current allocation for this task
    current_model, _ = get_model_for_task(task)

    current_result = next((r for r in results if r["model"] == current_model), None)

    if current_result:
        # Check if a cheaper model achieves same accuracy
        for r in sorted_by_cost:
            if (
                r["model"] != current_model
                and r["accuracy"] >= current_result["accuracy"]
                and r["avg_cost_per_call"] < current_result["avg_cost_per_call"]
            ):
                saving_pct = round(
                    (1 - r["avg_cost_per_call"] / max(current_result["avg_cost_per_call"], 1e-9)) * 100
                )
                recs.append(
                    f"DOWNGRADE: task '{task}' should use {r['model']} instead of "
                    f"{current_model} — same {r['accuracy']:.0%} accuracy at "
                    f"{saving_pct}% less cost"
                )
                break

        # Check if a better model is worth the cost
        if best["model"] != current_model and best["accuracy"] > current_result["accuracy"]:
            acc_gain = best["accuracy"] - current_result["accuracy"]
            recs.append(
                f"UPGRADE: task '{task}' could use {best['model']} for "
                f"+{acc_gain:.0%} accuracy (current: {current_result['accuracy']:.0%} "
                f"→ {best['accuracy']:.0%})"
            )
    else:
        recs.append(
            f"Task '{task}' currently uses {current_model} (not tested). "
            f"Best tested: {best['model']} at {best['accuracy']:.0%} accuracy."
        )

    # Always note the cheapest viable option
    if cheapest["accuracy"] >= 0.7:
        recs.append(
            f"BUDGET: {cheapest['model']} achieves {cheapest['accuracy']:.0%} accuracy "
            f"at ${cheapest['avg_cost_per_call']:.6f}/call — viable for non-critical uses"
        )

    return recs
