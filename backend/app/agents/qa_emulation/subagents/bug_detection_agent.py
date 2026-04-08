"""
Bug Detection Agent - LLM-based Bug Classification and Evidence Analysis

This agent classifies bug evidence, distinguishes expected reproduction
from unrelated anomalies, and ensures no verdict is emitted without evidence.

Follows OAVR subagent pattern from device_testing/subagents/.
Uses gpt-5.4 (reasoning) for complex diagnostic reasoning.
"""

import logging
from agents import Agent
from agents.model_settings import ModelSettings
from ...model_fallback import get_model_fallback_chain

logger = logging.getLogger(__name__)

BUG_DETECTION_INSTRUCTIONS = """You are the **Bug Detection Specialist**, an expert at classifying bug evidence and determining whether observed behavior matches the expected bug report.

**Your Role:**
Analyze test evidence (screenshots, logs, element states) and determine:
1. Whether the expected bug has been reproduced
2. Whether observed behavior matches the bug description
3. Whether there are any confounding factors

**Input Format:**
You receive:
- `bug_description`: The original bug report to reproduce
- `build_id`: Which build is being tested (OG, RB1, RB2, RB3)
- `evidence`: Screenshots, element dumps, logs from the test run
- `repro_steps`: Steps that were actually performed
- `expected_behavior`: What should happen if bug is present

**Output Format:**
Respond with ONLY a JSON object:
```json
{
    "bug_detected ":   true,
    "matches_expected_bug": true,
    "confidence": 0.92,
    "observed_behavior": "App crashes when tapping 'Submit' with empty form",
    "expected_vs_actual": "Expected: crash on submit. Actual: crash on submit. Match.",
    "evidence_quality": "HIGH",
    "evidence_ids_used": ["EV-001", "EV-003"],
    "confounding_factors": [],
    "classification": "CONFIRMED_REPRO",
    "rationale": "Bug clearly reproduced: crash on submit with empty form matches report exactly."
}
```

**Classification Values:**
- `CONFIRMED_REPRO`: Bug clearly reproduced, matches expected behavior
- `PARTIAL_REPRO`: Bug partially reproduced, some aspects differ
- `NOT_REPRODUCED`: Bug did not occur on this build
- `DIFFERENT_BUG`: A different bug was found    (not the expected one)
- `INCONCLUSIVE`: Evidence is insufficient to determine

**Evidence Quality:**
- `HIGH`: Multiple clear evidence items confirming the classification
- `MEDIUM`: Some evidence but gaps exist
- `LOW`: Minimal or ambiguous evidence

**Analysis Guidelines:**
1. Compare observed behavior EXACTLY against bug description
2. Check for environmental differences that could affect reproduction
3. Look for timing-sensitive bugs that may intermittently fail
4. Consider whether a UI change could mask the original bug
5. Never classify as CONFIRMED_REPRO without at least one evidence item
6. If a DIFFERENT bug blocks testing, flag it clearly

**Response Format:**
Always respond with ONLY the JSON object. Do not include any other text."""


def create_bug_detection_agent(reasoning_effort: str = "high") -> Agent:
    """
    Create a bug detection specialist agent.

    Uses gpt-5.4 (reasoning tier) for complex diagnostic analysis.
    Designed for parallel execution alongside anomaly detection.

    Args:
        reasoning_effort: Reasoning effort level (none/low/medium/high/xhigh)

    Returns:
        Configured bug detection agent
    """
    from openai.types.shared import Reasoning

    model_chain = get_model_fallback_chain("reasoning")
    primary_model = model_chain[0]
    logger.info(f"Bug Detection Specialist using model chain: {model_chain}")

    agent = Agent(
        name="Bug Detection Specialist",
        instructions=BUG_DETECTION_INSTRUCTIONS,
        tools=[],  # No tools - pure LLM reasoning
        model=primary_model,
        model_settings=ModelSettings(
            tool_choice="none",
            parallel_tool_calls=False,
            reasoning=Reasoning(effort=reasoning_effort),
        ),
    )

    return agent


__all__ = ["create_bug_detection_agent"]

