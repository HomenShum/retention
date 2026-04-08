"""
Orchestration Module - Agent Run Session with Inline LLM Evaluation

Industry Standard Model Configuration (January 2026):
- THINKING_MODEL (gpt-5.4): Agent orchestration, complex reasoning, planning
- PRIMARY_MODEL (gpt-5-mini): Evaluation, vision, routing (quality matters!)
- DISTILL_MODEL (gpt-5-nano): ONLY for MCP tools, distillation, extraction
- FALLBACK_MODEL (gpt-5): Flagship fallback

This module provides orchestration capabilities for agent runs that:
1. Invoke LLM tool calls during execution for self-evaluation
2. Evaluate test case generation against criteria
3. Verify device emulation configuration matches bug reproduction requirements
4. Prevent workarounds and misconfigurations
5. Retry or make changes when needed
6. Progressive Disclosure - load context incrementally as needed

Key Components:
- OrchestrationRunSession: Main orchestration class (uses gpt-5.4 for thinking)
- TestCaseEvaluator: Evaluates generated test cases against criteria (gpt-5-mini)
- DeviceConfigVerifier: Verifies device configuration matches requirements
- InlineLLMEvaluator: LLM-based inline evaluation during execution (gpt-5-mini)
- ProgressiveDisclosureLoader: Skills-based context loading (minimal → full)
"""

from .run_session import OrchestrationRunSession
from .agentic_update_eval import AgenticUpdateEvaluator
from .evaluators import TestCaseEvaluator, DeviceConfigVerifier, InlineLLMEvaluator
from .progressive_disclosure import ProgressiveDisclosureLoader, SkillMetadata, SkillContext

__all__ = [
    "OrchestrationRunSession",
    "AgenticUpdateEvaluator",
    "TestCaseEvaluator",
    "DeviceConfigVerifier",
    "InlineLLMEvaluator",
    "ProgressiveDisclosureLoader",
    "SkillMetadata",
    "SkillContext",
]

