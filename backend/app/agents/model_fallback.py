"""
Model Fallback Utility

Provides fallback model chains for resilient agent execution.
If a model is unavailable or returns an error, automatically tries the next model in the chain.

OpenAI Model Hierarchy (as of March 17, 2026):
- GPT-5.4 (Mar 5 2026): Latest flagship — native computer-use, preambles, xhigh reasoning
- GPT-5.4-mini (Mar 17 2026): Most capable small model — 2x faster than gpt-5-mini
  $0.75/1M input, $4.50/1M output
- GPT-5.4-nano (Mar 17 2026): Smallest/cheapest — outperforms old gpt-5-mini
  $0.20/1M input, $1.25/1M output
- GPT-5.3-Codex (Feb 5 2026): Latest agentic coding model

DEPRECATED (DO NOT USE):
- gpt-5-mini (legacy — use gpt-5.4-mini instead)
- gpt-5-nano (legacy — use gpt-5.4-nano instead)
- gpt-5.4 (legacy — use gpt-5.4 instead)
- gpt-5.4-codex (legacy — use gpt-5.3-codex instead)
- gpt-4o, gpt-4o-mini (deprecated Aug 2025)
- gpt-4.1, gpt-4.1-mini (deprecated with GPT-5 launch)
- o3, o3-pro (deprecated)

Model Tiering Strategy (March 17, 2026) - GPT-5.4 Family Complete:
===================================================================

HIGH THINKING BUDGET (Agent Orchestration, Complex Reasoning):
- THINKING_MODEL: gpt-5.4 - For agent orchestration, multi-step planning, complex reasoning
  Supports reasoning effort: none (default), low, medium, high, xhigh
  Native computer-use, preambles for tool-call transparency

PRIMARY TASKS (Standard Reasoning, Vision):
- PRIMARY_MODEL: gpt-5.4-mini - Default for routing, classification, general tasks
- VISION_MODEL: gpt-5.4-mini - Default for screenshot analysis, image understanding

FALLBACK (Flagship):
- FALLBACK_MODEL: gpt-5 - Flagship model for fallback scenarios

LOW BUDGET / DISTILLATION (MCP Tools, Info Extraction):
- DISTILL_MODEL: gpt-5.4-mini - For extraction/distillation tool calls
- DISTILL_FALLBACK: gpt-5.4-nano - Fast/cheap fallback ($0.20/1M input)

SPECIALIZED:
- CODING_MODEL: gpt-5.3-codex - For agentic coding (Feb 2026 release)
"""

import logging
from typing import List

logger = logging.getLogger(__name__)


# Model constants for tiering (March 17, 2026 - GPT-5.4 Family Complete)
# PRIORITY ORDER: gpt-5.4 (flagship) → gpt-5.4-mini → gpt-5.4-nano
THINKING_MODEL = "gpt-5.4"             # High thinking budget - agent orchestration (Mar 5 2026)
PRIMARY_MODEL = "gpt-5.4-mini"         # Default for most tasks — 2x faster than old mini (Mar 17 2026)
VISION_MODEL = "gpt-5.4-mini"          # Default for vision tasks (Mar 17 2026)
REASONING_MODEL = "gpt-5.4"            # Complex reasoning (same as thinking)
CODING_MODEL = "gpt-5.3-codex"         # Agentic coding model (Feb 2026)
DISTILL_MODEL = "gpt-5.4-mini"         # MCP tools, info extraction (Mar 17 2026)
DISTILL_FALLBACK = "gpt-5.4-nano"      # Fast/cheap fallback — $0.20/1M input (Mar 17 2026)
EVAL_MODEL = "gpt-5.4"                 # LLM-as-judge — flagship model for verdict quality
FALLBACK_MODEL = "gpt-5"               # Flagship fallback (stable)

# NVIDIA — open model for cost-efficient QA and parallel fan-out
NEMOTRON_MODEL = "nvidia/nemotron-3-super-120b-a12b"  # MoE 120B→12B, 1M ctx, OpenAI-compat

# Legacy aliases (backward compatibility)
ROUTING_MODEL = PRIMARY_MODEL          # Route to PRIMARY_MODEL (gpt-5.4-mini)
LEGACY_THINKING_MODEL = "gpt-5.4"     # Previous flagship (deprecated, use gpt-5.4)

# ---------------------------------------------------------------------------
# Model Tiers — Frontier Discovery → Cheap Replay
# ---------------------------------------------------------------------------
# Anthropic models for cross-provider tier support
FRONTIER_ANTHROPIC = "claude-opus-4"       # Frontier discovery, deep research
PRIMARY_ANTHROPIC = "claude-sonnet-4"      # Standard operations
REPLAY_ANTHROPIC = "claude-haiku-4.5"      # Constrained replay ONLY

# Tier → ordered model lists (cheapest replay tier at bottom)
MODEL_TIERS = {
    "frontier": [THINKING_MODEL, FRONTIER_ANTHROPIC, FALLBACK_MODEL],
    "primary": [PRIMARY_MODEL, PRIMARY_ANTHROPIC, FALLBACK_MODEL],
    "replay": [DISTILL_FALLBACK, REPLAY_ANTHROPIC, PRIMARY_MODEL],
}

# Cost per 1M input tokens (USD) — for savings calculations
MODEL_INPUT_COSTS = {
    # OpenAI GPT-5.4 family
    "gpt-5.4": 2.50,
    "gpt-5.4-mini": 0.75,
    "gpt-5.4-nano": 0.20,
    "gpt-5": 5.00,
    "gpt-5.3-codex": 2.50,
    # Anthropic
    "claude-opus-4": 15.00,
    "claude-sonnet-4": 3.00,
    "claude-haiku-4.5": 0.80,
    # NVIDIA
    "nvidia/nemotron-3-super-120b-a12b": 0.08,
}


# Define fallback chains for different agent types (March 17, 2026 - GPT-5.4 Family)
MODEL_FALLBACK_CHAINS = {
    # Agent Orchestration - HIGH THINKING BUDGET (gpt-5.4 → gpt-5 → gpt-5.4-mini)
    "orchestration": [
        THINKING_MODEL,    # Primary: gpt-5.4 (high thinking budget, xhigh reasoning)
        FALLBACK_MODEL,    # Fallback 1: gpt-5 (flagship)
        PRIMARY_MODEL,     # Fallback 2: gpt-5.4-mini
    ],
    # Routing/Coordinator - PRIMARY MODEL (gpt-5.4-mini → gpt-5 → gpt-5.4)
    "routing": [
        PRIMARY_MODEL,     # Primary: gpt-5.4-mini (NOT nano for quality)
        FALLBACK_MODEL,    # Fallback 1: gpt-5
        THINKING_MODEL,    # Fallback 2: gpt-5.4
    ],
    # Vision tasks (screenshot analysis)
    "vision": [
        VISION_MODEL,      # Primary: gpt-5.4-mini
        FALLBACK_MODEL,    # Fallback 1: gpt-5 (flagship)
        PRIMARY_MODEL,     # Fallback 2: gpt-5.4-mini
    ],
    # Complex reasoning (test generation, analysis, diagnosis)
    "reasoning": [
        THINKING_MODEL,    # Primary: gpt-5.4 (high thinking, native computer-use)
        FALLBACK_MODEL,    # Fallback 1: gpt-5 (flagship)
        PRIMARY_MODEL,     # Fallback 2: gpt-5.4-mini
    ],
    # Coding tasks (code generation, analysis)
    "coding": [
        CODING_MODEL,      # Primary: gpt-5.3-codex (agentic coding)
        THINKING_MODEL,    # Fallback 1: gpt-5.4
        FALLBACK_MODEL,    # Fallback 2: gpt-5
    ],
    # Evaluation tasks (inline LLM evaluation - needs quality!)
    "evaluation": [
        EVAL_MODEL,        # Primary: gpt-5.4 (quality matters)
        FALLBACK_MODEL,    # Fallback 1: gpt-5
        PRIMARY_MODEL,     # Fallback 2: gpt-5.4-mini
    ],
    # MCP Tool Calls / Distillation - gpt-5.4-mini with nano fallback
    "distillation": [
        DISTILL_MODEL,     # Primary: gpt-5.4-mini
        DISTILL_FALLBACK,  # Fallback 1: gpt-5.4-nano (fast/cheap)
        FALLBACK_MODEL,    # Fallback 2: gpt-5
    ],
    # Search Enhancement - gpt-5.4-mini with nano fallback
    "search_enhancement": [
        DISTILL_MODEL,     # Primary: gpt-5.4-mini
        DISTILL_FALLBACK,  # Fallback 1: gpt-5.4-nano
        FALLBACK_MODEL,    # Fallback 2: gpt-5
    ],
    # Direct model name mappings
    "gpt-5.4": [
        THINKING_MODEL,
        FALLBACK_MODEL,
        PRIMARY_MODEL,
    ],
    "gpt-5.4-mini": [
        PRIMARY_MODEL,
        FALLBACK_MODEL,
        THINKING_MODEL,
    ],
    "gpt-5.4-nano": [
        DISTILL_FALLBACK,
        PRIMARY_MODEL,
        FALLBACK_MODEL,
    ],
    "gpt-5": [
        FALLBACK_MODEL,
        PRIMARY_MODEL,
        THINKING_MODEL,
    ],
    # Legacy mappings (redirect to new models)
    "gpt-5.4": [
        THINKING_MODEL,    # Redirect gpt-5.4 → gpt-5.4
        FALLBACK_MODEL,
        PRIMARY_MODEL,
    ],
    "gpt-5-mini": [
        PRIMARY_MODEL,     # Redirect gpt-5-mini → gpt-5.4-mini
        FALLBACK_MODEL,
        THINKING_MODEL,
    ],
    "gpt-5-nano": [
        DISTILL_FALLBACK,  # Redirect gpt-5-nano → gpt-5.4-nano
        PRIMARY_MODEL,
        FALLBACK_MODEL,
    ],
    # NVIDIA Nemotron — open model for cost-efficient QA, parallel fan-out
    # 120B MoE activating 12B, 1M context, $0.08/M input — 30x cheaper than gpt-5.4
    # Use for: bulk test analysis, parallel pipeline runs, competitor comparisons
    "nemotron": [
        NEMOTRON_MODEL,    # Primary: nemotron-3-super (fast, cheap, 1M context)
        PRIMARY_MODEL,     # Fallback 1: gpt-5.4-mini
        FALLBACK_MODEL,    # Fallback 2: gpt-5
    ],
    "nvidia/nemotron-3-super-120b-a12b": [
        NEMOTRON_MODEL,
        PRIMARY_MODEL,
        FALLBACK_MODEL,
    ],
    # ROP Replay — full escalation ladder from cheapest to most expensive
    "rop_replay": [
        DISTILL_FALLBACK,      # Primary: gpt-5.4-nano (cheapest)
        REPLAY_ANTHROPIC,      # Fallback 1: claude-haiku-4.5
        PRIMARY_MODEL,         # Fallback 2: gpt-5.4-mini
        THINKING_MODEL,        # Fallback 3: gpt-5.4 (frontier)
    ],
    # PRD Parser tier mappings (for subagent usage)
    "balanced": [
        FALLBACK_MODEL,    # Primary: gpt-5 (balanced performance)
        PRIMARY_MODEL,     # Fallback 1: gpt-5.4-mini
        THINKING_MODEL,    # Fallback 2: gpt-5.4
    ],
    "extraction": [
        FALLBACK_MODEL,    # Primary: gpt-5 (structured extraction)
        PRIMARY_MODEL,     # Fallback 1: gpt-5.4-mini
        THINKING_MODEL,    # Fallback 2: gpt-5.4
    ],
}


def get_model_for_task(task_type: str) -> str:
    """
    Get the appropriate model for a specific task type.

    Model Selection (March 2026 - GPT-5.4):

    HIGH THINKING (gpt-5.4):
    - "orchestration": Agent orchestration, multi-step planning
    - "reasoning": Complex analysis, test generation, diagnosis
    - "planning": Task decomposition, strategy

    PRIMARY (gpt-5-mini):
    - "routing": Request classification, coordinator routing
    - "vision": Screenshot analysis, image understanding
    - "evaluation": Inline LLM evaluation (quality matters!)
    - "classification": Intent detection, categorization
    - "general": Standard tasks

    DISTILLATION (gpt-5-nano) - ONLY for:
    - "mcp_tool": Figma API, MCP tool calls (info extraction)
    - "distillation": Summarizing large file content
    - "search_enhancement": Hybrid search prompt generation

    SPECIALIZED:
    - "coding": Code generation (gpt-5.3-codex)

    Args:
        task_type: The type of task to perform

    Returns:
        The recommended model for the task
    """
    task_model_map = {
        # HIGH THINKING (gpt-5.4)
        "orchestration": THINKING_MODEL,
        "reasoning": THINKING_MODEL,
        "planning": THINKING_MODEL,
        "agent": THINKING_MODEL,
        "multi_step": THINKING_MODEL,
        # PRIMARY (gpt-5-mini)
        "routing": PRIMARY_MODEL,
        "vision": VISION_MODEL,
        "evaluation": EVAL_MODEL,
        "classification": PRIMARY_MODEL,
        "general": PRIMARY_MODEL,
        "default": PRIMARY_MODEL,
        # DISTILLATION (gpt-5-nano) - only for extraction
        "mcp_tool": DISTILL_MODEL,
        "figma": DISTILL_MODEL,
        "distillation": DISTILL_MODEL,
        "search_enhancement": DISTILL_MODEL,
        "extraction": DISTILL_MODEL,
        # SPECIALIZED
        "coding": CODING_MODEL,
        "code_generation": CODING_MODEL,
        # NON-QA WORKFLOW TYPES (for benchmark families)
        "deep_research": THINKING_MODEL,       # DRX: multi-source research
        "code_analysis": THINKING_MODEL,       # CSP: cross-stack codebase analysis
        "document_generation": PRIMARY_MODEL,  # report/memo generation
        "cross_stack_change": THINKING_MODEL,  # CSP: propagate changes across layers
    }

    model = task_model_map.get(task_type, PRIMARY_MODEL)
    logger.debug(f"Task '{task_type}' → Model '{model}'")
    return model


def get_model_fallback_chain(primary_model: str) -> List[str]:
    """
    Get the fallback chain for a given primary model.

    Model Tiering (March 2026 - GPT-5.4):

    HIGH THINKING (for agent orchestration, complex reasoning):
    - "orchestration": gpt-5.4 → gpt-5 → gpt-5-mini
    - "reasoning": gpt-5.4 → gpt-5 → gpt-5-mini

    PRIMARY (for most tasks - NOT nano!):
    - "routing": gpt-5-mini → gpt-5 → gpt-5.4
    - "vision": gpt-5-mini → gpt-5 → gpt-5.4
    - "evaluation": gpt-5-mini → gpt-5 → gpt-5.4

    DISTILLATION (only for MCP/extraction):
    - "distillation": gpt-5-mini → gpt-5-nano → gpt-5
    - "search_enhancement": gpt-5-mini → gpt-5-nano → gpt-5

    SPECIALIZED:
    - "coding": gpt-5.3-codex → gpt-5.4 → gpt-5

    Args:
        primary_model: Model type or model name

    Returns:
        List of models to try in order, starting with primary

    Example:
        >>> get_model_fallback_chain("orchestration")
        ["gpt-5.4", "gpt-5", "gpt-5-mini"]
    """
    if primary_model in MODEL_FALLBACK_CHAINS:
        chain = MODEL_FALLBACK_CHAINS[primary_model]
        logger.info(f"Model fallback chain for {primary_model}: {' → '.join(chain)}")
        return chain
    else:
        # If model not in predefined chains, return it as single-item list
        logger.warning(f"No fallback chain defined for {primary_model}, using as-is")
        return [primary_model]


def log_model_attempt(model: str, attempt_number: int, total_attempts: int):
    """Log a model attempt."""
    if attempt_number == 1:
        logger.info(f"Attempting model: {model}")
    else:
        logger.warning(f"Model attempt {attempt_number}/{total_attempts}: {model}")


def log_model_success(model: str, attempt_number: int):
    """Log successful model usage."""
    if attempt_number == 1:
        logger.info(f"✅ Using model: {model}")
    else:
        logger.info(f"✅ Fallback successful with model: {model} (attempt {attempt_number})")


def log_model_failure(model: str, error: str):
    """Log model failure."""
    logger.warning(f"Model {model} failed: {error}")


# ---------------------------------------------------------------------------
# Tier utilities — for ROP distillation / cheap replay
# ---------------------------------------------------------------------------

def get_tier_for_model(model: str) -> str:
    """Reverse lookup: given a model name, return its tier."""
    for tier_name, models in MODEL_TIERS.items():
        if model in models:
            return tier_name
    # Heuristic: check by substring
    model_lower = model.lower()
    if any(k in model_lower for k in ("opus", "5.4-xhigh", "mythos")):
        return "frontier"
    if any(k in model_lower for k in ("haiku", "nano")):
        return "replay"
    return "primary"


def get_models_for_tier(tier: str) -> List[str]:
    """Return ordered model list for a tier."""
    return MODEL_TIERS.get(tier, [PRIMARY_MODEL])


def get_escalation_chain(current_model: str) -> List[str]:
    """Return escalation path: current tier → primary → frontier."""
    current_tier = get_tier_for_model(current_model)
    chain = []
    # Start from current tier, walk up
    tier_order = ["replay", "primary", "frontier"]
    started = False
    for tier in tier_order:
        if tier == current_tier:
            started = True
        if started:
            for m in MODEL_TIERS.get(tier, []):
                if m not in chain and m != current_model:
                    chain.append(m)
    if not chain:
        chain = [THINKING_MODEL]
    return chain


def estimate_cost(tokens: int, model: str) -> float:
    """Estimate USD cost for a given token count and model."""
    cost_per_m = MODEL_INPUT_COSTS.get(model, 1.0)
    return (tokens / 1_000_000) * cost_per_m


__all__ = [
    # Functions
    "get_model_for_task",
    "get_model_fallback_chain",
    "log_model_attempt",
    "log_model_success",
    "log_model_failure",
    "MODEL_FALLBACK_CHAINS",
    # Model constants for tiering (March 17, 2026 - GPT-5.4 Family Complete)
    "THINKING_MODEL",          # gpt-5.4 - High thinking budget (Mar 5 2026)
    "PRIMARY_MODEL",           # gpt-5.4-mini - Primary for most tasks (Mar 17 2026)
    "VISION_MODEL",            # gpt-5.4-mini - Vision tasks (Mar 17 2026)
    "REASONING_MODEL",         # gpt-5.4 - Complex reasoning
    "CODING_MODEL",            # gpt-5.3-codex - Agentic coding (Feb 2026)
    "DISTILL_MODEL",           # gpt-5.4-mini - MCP/extraction (Mar 17 2026)
    "DISTILL_FALLBACK",        # gpt-5.4-nano - Fast/cheap fallback (Mar 17 2026)
    "EVAL_MODEL",              # gpt-5.4 - Evaluation (quality)
    "FALLBACK_MODEL",          # gpt-5 - Flagship fallback
    "ROUTING_MODEL",           # gpt-5.4-mini - Legacy alias for PRIMARY_MODEL
    "LEGACY_THINKING_MODEL",   # gpt-5.4 - Previous flagship (deprecated)
    # Tier system (Frontier Discovery → Cheap Replay)
    "FRONTIER_ANTHROPIC",
    "PRIMARY_ANTHROPIC",
    "REPLAY_ANTHROPIC",
    "MODEL_TIERS",
    "MODEL_INPUT_COSTS",
    "get_tier_for_model",
    "get_models_for_tier",
    "get_escalation_chain",
    "estimate_cost",
]

