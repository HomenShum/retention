"""
Inline LLM Evaluators for Orchestration Run Session

Provides inline LLM evaluation during agent execution to:
1. Evaluate test case generation results against criteria
2. Verify device configuration matches bug reproduction requirements
3. Detect workarounds and misconfigurations
4. Suggest retries or changes when needed

Model Configuration (Industry Standard - January 2026):
- EVAL_MODEL: gpt-5.4 (flagship model for LLM-as-judge evaluation)
- THINKING_MODEL: gpt-5.4 (for high-thinking orchestration tasks)

Note: gpt-5-nano should ONLY be used for:
- MCP tool calls (Figma API, etc.)
- Distilling info from large files
- Search prompt enhancement

NOT for evaluation, routing, or any reasoning tasks.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime
import json

logger = logging.getLogger(__name__)

# Import model configuration from central location
try:
    from ..model_fallback import EVAL_MODEL, THINKING_MODEL, PRIMARY_MODEL
except ImportError:
    # Fallback if import fails
    EVAL_MODEL = "gpt-5.4"           # Flagship LLM-as-judge model
    THINKING_MODEL = "gpt-5.4"       # High thinking budget (Mar 2026)
    PRIMARY_MODEL = "gpt-5-mini"     # Primary for most tasks


@dataclass
class EvaluationResult:
    """Result of an inline LLM evaluation"""
    passed: bool
    confidence: float  # 0.0 to 1.0
    reasoning: str
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    needs_retry: bool = False
    retry_reason: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "passed": self.passed,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "issues": self.issues,
            "suggestions": self.suggestions,
            "needs_retry": self.needs_retry,
            "retry_reason": self.retry_reason
        }


class InlineLLMEvaluator:
    """Base class for inline LLM evaluation during agent execution"""

    def __init__(self, model: str = EVAL_MODEL):
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            from ...observability.tracing import get_traced_client
            self._client = get_traced_client(OpenAI())
        return self._client

    async def evaluate(self, prompt: str) -> EvaluationResult:
        """Run inline LLM evaluation with structured output"""
        try:
            client = self._get_client()
            response = client.responses.create(
                model=self.model,
                input=prompt + "\n\nRespond with JSON: {\"passed\": bool, \"confidence\": 0-1, \"reasoning\": str, \"issues\": [], \"suggestions\": [], \"needs_retry\": bool}",
                store=False,
            )
            return self._parse_response(response.output_text)
        except Exception as e:
            logger.error(f"[InlineLLMEvaluator] Error: {e}")
            return EvaluationResult(
                passed=False, confidence=0.0,
                reasoning=f"Evaluation failed: {e}",
                needs_retry=True, retry_reason=str(e)
            )

    def _parse_response(self, text: str) -> EvaluationResult:
        try:
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            data = json.loads(text.strip())
            return EvaluationResult(
                passed=data.get("passed", False),
                confidence=float(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", ""),
                issues=data.get("issues", []),
                suggestions=data.get("suggestions", []),
                needs_retry=data.get("needs_retry", False),
                retry_reason=data.get("retry_reason")
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            passed = "true" in text.lower() and "passed" in text.lower()
            return EvaluationResult(
                passed=passed, confidence=0.5 if passed else 0.3,
                reasoning=text[:200]
            )


class TestCaseEvaluator(InlineLLMEvaluator):
    """Evaluates test case generation against feature release criteria"""

    async def evaluate_test_case(
        self,
        generated_test: str,
        feature_criteria: Dict[str, Any],
        category: str,
        expected_coverage: List[str] = None
    ) -> EvaluationResult:
        """Evaluate generated test case against criteria."""
        coverage_str = ", ".join(expected_coverage) if expected_coverage else "Not specified"
        prompt = f"""Evaluate this generated test case against the feature release criteria.

GENERATED TEST:
{generated_test[:2000]}

FEATURE CRITERIA:
{json.dumps(feature_criteria, indent=2)[:1000]}

CATEGORY: {category}
EXPECTED COVERAGE: {coverage_str}

Evaluate:
1. Does the test cover all required scenarios?
2. Are assertions correct and complete?
3. Are edge cases handled?
4. Is the test deterministic (no flaky patterns)?
5. Does it match the category requirements?

CRITICAL: Detect any shortcuts, workarounds, or missing coverage."""
        return await self.evaluate(prompt)


class DeviceConfigVerifier(InlineLLMEvaluator):
    """Verifies device emulation configuration matches bug reproduction requirements"""

    async def verify_device_config(
        self,
        required_config: Dict[str, Any],
        actual_config: Dict[str, Any],
        bug_reproduction_steps: List[str]
    ) -> EvaluationResult:
        """Verify device configuration matches exactly what's needed for bug reproduction."""
        steps_str = chr(10).join(f'{i+1}. {step}' for i, step in enumerate(bug_reproduction_steps[:10]))
        prompt = f"""Verify the device configuration matches the bug reproduction requirements EXACTLY.

REQUIRED CONFIGURATION (from bug report):
{json.dumps(required_config, indent=2)}

ACTUAL CONFIGURATION (current device):
{json.dumps(actual_config, indent=2)}

BUG REPRODUCTION STEPS:
{steps_str}

CRITICAL CHECKS:
1. Device model matches EXACTLY (not a similar device)
2. OS version matches EXACTLY (bugs may be version-specific)
3. Screen resolution/density matches (UI bugs are resolution-dependent)
4. Locale/language matches (if specified)
5. Network conditions match (if specified)

DO NOT ACCEPT workarounds, "close enough" configurations, or missing values.
If ANY mismatch is found, mark as FAILED with specific issues."""
        return await self.evaluate(prompt)

    async def detect_workarounds(
        self,
        expected_steps: List[str],
        actual_steps: List[str],
        expected_outcome: str
    ) -> EvaluationResult:
        """Detect if the agent is using workarounds instead of proper reproduction."""
        expected_str = chr(10).join(f'{i+1}. {step}' for i, step in enumerate(expected_steps[:15]))
        actual_str = chr(10).join(f'{i+1}. {step}' for i, step in enumerate(actual_steps[:15]))
        prompt = f"""Detect if the executed steps are workarounds or deviations from expected reproduction.

EXPECTED STEPS (from bug report):
{expected_str}

ACTUAL STEPS (executed):
{actual_str}

EXPECTED OUTCOME: {expected_outcome}

DETECT WORKAROUNDS:
1. Did agent skip any required steps?
2. Did agent use alternative navigation paths?
3. Did agent make configuration changes not in the bug report?
4. Did agent retry with different parameters?
5. Did agent use app shortcuts instead of UI navigation?

WORKAROUNDS ARE NOT ACCEPTABLE - bugs may only occur with EXACT steps.
Mark as FAILED if ANY workarounds or deviations are detected."""
        return await self.evaluate(prompt)

