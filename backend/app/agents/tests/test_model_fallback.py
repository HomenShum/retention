"""
Tests for Model Fallback Configuration (March 2026 - GPT-5.4)

Verifies:
1. Model constants are correct (gpt-5.4 flagship, gpt-5.3-codex)
2. Task-to-model mapping follows March 2026 standard
3. Fallback chains are properly configured
4. gpt-5-nano is ONLY used for MCP/distillation tasks
"""

import pytest
from app.agents.model_fallback import (
    THINKING_MODEL,
    PRIMARY_MODEL,
    VISION_MODEL,
    REASONING_MODEL,
    CODING_MODEL,
    DISTILL_MODEL,
    EVAL_MODEL,
    FALLBACK_MODEL,
    ROUTING_MODEL,
    get_model_for_task,
    get_model_fallback_chain,
    MODEL_FALLBACK_CHAINS,
)


class TestModelConstants:
    """Test that model constants match industry standard"""

    def test_thinking_model_is_gpt_5_4(self):
        """gpt-5.4 should be used for high-thinking tasks (Mar 2026)"""
        assert THINKING_MODEL == "gpt-5.4"

    def test_primary_model_is_gpt_5_mini(self):
        """gpt-5-mini should be the primary model (NOT nano)"""
        assert PRIMARY_MODEL == "gpt-5-mini"

    def test_vision_model_is_gpt_5_mini(self):
        """gpt-5-mini should be used for vision tasks"""
        assert VISION_MODEL == "gpt-5-mini"

    def test_eval_model_is_gpt_5_4(self):
        """gpt-5.4 should be used for evaluation (flagship model for verdict quality)"""
        assert EVAL_MODEL == "gpt-5.4"

    def test_distill_model_is_gpt_5_mini(self):
        """DISTILL_MODEL is now gpt-5-mini (gpt-5-nano as fallback)"""
        assert DISTILL_MODEL == "gpt-5-mini"

    def test_fallback_model_is_gpt_5(self):
        """gpt-5 should be the flagship fallback"""
        assert FALLBACK_MODEL == "gpt-5"

    def test_coding_model_is_gpt_5_3_codex(self):
        """gpt-5.3-codex should be used for code generation (Feb 2026)"""
        assert CODING_MODEL == "gpt-5.3-codex"

    def test_routing_model_is_primary(self):
        """ROUTING_MODEL should be an alias for PRIMARY_MODEL"""
        assert ROUTING_MODEL == PRIMARY_MODEL


class TestTaskToModelMapping:
    """Test that tasks are mapped to correct models"""

    # HIGH THINKING tasks should use gpt-5.4
    @pytest.mark.parametrize("task", [
        "orchestration",
        "reasoning",
        "planning",
        "agent",
        "multi_step",
    ])
    def test_high_thinking_tasks_use_gpt_5_4(self, task):
        """High-thinking tasks should use gpt-5.4 (Mar 2026)"""
        model = get_model_for_task(task)
        assert model == "gpt-5.4", f"Task '{task}' should use gpt-5.4, got {model}"

    # EVAL tasks now use gpt-5.4 (flagship model for verdict quality)
    @pytest.mark.parametrize("task", [
        "evaluation",
    ])
    def test_eval_tasks_use_gpt_5_4(self, task):
        """Evaluation tasks should use gpt-5.4 (LLM-as-judge needs flagship quality)"""
        model = get_model_for_task(task)
        assert model == "gpt-5.4", f"Task '{task}' should use gpt-5.4, got {model}"

    # PRIMARY tasks should use gpt-5-mini
    @pytest.mark.parametrize("task", [
        "routing",
        "vision",
        "classification",
        "general",
        "default",
    ])
    def test_primary_tasks_use_gpt_5_mini(self, task):
        """Primary tasks should use gpt-5-mini"""
        model = get_model_for_task(task)
        assert model == "gpt-5-mini", f"Task '{task}' should use gpt-5-mini, got {model}"

    # DISTILLATION tasks now use gpt-5-mini (with nano fallback)
    @pytest.mark.parametrize("task", [
        "mcp_tool",
        "figma",
        "distillation",
        "search_enhancement",
        "extraction",
    ])
    def test_distillation_tasks_use_gpt_5_mini(self, task):
        """Distillation tasks now use gpt-5-mini (gpt-5-nano as fallback)"""
        model = get_model_for_task(task)
        assert model == "gpt-5-mini", f"Task '{task}' should use gpt-5-mini, got {model}"

    # CODING tasks should use gpt-5.3-codex
    @pytest.mark.parametrize("task", [
        "coding",
        "code_generation",
    ])
    def test_coding_tasks_use_gpt_5_3_codex(self, task):
        """Coding tasks should use gpt-5.3-codex (Feb 2026)"""
        model = get_model_for_task(task)
        assert model == "gpt-5.3-codex", f"Task '{task}' should use gpt-5.3-codex, got {model}"

    def test_unknown_task_uses_primary(self):
        """Unknown tasks should default to PRIMARY_MODEL"""
        model = get_model_for_task("unknown_task_xyz")
        assert model == PRIMARY_MODEL


class TestFallbackChains:
    """Test that fallback chains are properly configured"""

    def test_orchestration_chain_starts_with_gpt_5_4(self):
        """Orchestration chain should start with gpt-5.4 (Mar 2026)"""
        chain = get_model_fallback_chain("orchestration")
        assert chain[0] == "gpt-5.4"
        assert "gpt-5" in chain
        assert "gpt-5-mini" in chain

    def test_routing_chain_starts_with_gpt_5_mini(self):
        """Routing chain should start with gpt-5-mini (NOT nano)"""
        chain = get_model_fallback_chain("routing")
        assert chain[0] == "gpt-5-mini"
        assert "gpt-5-nano" not in chain  # nano should NOT be in routing chain

    def test_evaluation_chain_starts_with_gpt_5_4(self):
        """Evaluation chain should start with gpt-5.4 (flagship for LLM-as-judge)"""
        chain = get_model_fallback_chain("evaluation")
        assert chain[0] == "gpt-5.4"

    def test_distillation_chain_starts_with_gpt_5_mini(self):
        """Distillation chain now starts with gpt-5-mini (nano as fallback)"""
        chain = get_model_fallback_chain("distillation")
        assert chain[0] == "gpt-5-mini"
        assert "gpt-5-nano" in chain  # nano should be in the fallback chain

    def test_coding_chain_starts_with_codex(self):
        """Coding chain should start with gpt-5.3-codex"""
        chain = get_model_fallback_chain("coding")
        assert chain[0] == "gpt-5.3-codex"


class TestNanoUsageRestrictions:
    """Test that gpt-5-nano is NOT used for inappropriate tasks"""

    TASKS_THAT_SHOULD_NOT_USE_NANO = [
        "orchestration",
        "reasoning",
        "planning",
        "routing",
        "vision",
        "evaluation",
        "classification",
        "general",
    ]

    @pytest.mark.parametrize("task", TASKS_THAT_SHOULD_NOT_USE_NANO)
    def test_nano_not_used_for_reasoning_tasks(self, task):
        """gpt-5-nano should NOT be used for reasoning/routing/eval tasks"""
        model = get_model_for_task(task)
        assert model != "gpt-5-nano", \
            f"Task '{task}' should NOT use gpt-5-nano (got {model})"

    def test_nano_only_in_distillation_fallback_chains(self):
        """gpt-5-nano should only appear as fallback in distillation-related chains"""
        for chain_name, chain in MODEL_FALLBACK_CHAINS.items():
            if "gpt-5-nano" in chain:
                # Nano is now only a fallback, not primary
                assert chain_name in ["distillation", "search_enhancement", "gpt-5-nano"], \
                    f"Chain '{chain_name}' should not contain gpt-5-nano"

