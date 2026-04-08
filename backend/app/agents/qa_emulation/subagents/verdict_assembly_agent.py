"""
Verdict Assembly Agent - Structured Final Verdict Generation

This agent assembles the final QA reproduction verdict from bug detection
and anomaly detection results, producing a machine-checkable QAReproVerdict.

Follows OAVR subagent pattern from device_testing/subagents/.
Uses gpt-5.4 (reasoning) with output_type=QAReproVerdict for structured output.
"""

import logging
from agents import Agent
from agents.model_settings import ModelSettings
from ...model_fallback import get_model_fallback_chain
from ..models.verdict_models import QAReproVerdict

logger = logging.getLogger(__name__)

VERDICT_ASSEMBLY_INSTRUCTIONS = """You are the **Verdict Assembly Specialist**, the final arbiter of bug reproduction verdicts.

**Your Role:**
Synthesize bug detection and anomaly detection results across all builds
into a single, evidence-backed QAReproVerdict.

**Input Format:**
You receive a JSON summary containing:
- `task_id`: The task/bug being reproduced
- `bug_description`: Original bug report
- `build_results`: Per-build bug detection and anomaly results
- `all_evidence_ids`: All collected evidence IDs
- `all_anomalies`: All anomalies found across builds
- `workflow_notes`: Any notes from the workflow phases

**Verdict Decision Tree:**
1. If bug reproduced on OG build AND at least one RB build → `REPRODUCIBLE`
2. If bug NOT reproduced on ANY build → `NOT_REPRODUCIBLE`
3. If a DIFFERENT new bug blocks reproduction → `BLOCKED_NEW_BUG`
4. If evidence is insufficient (missing builds, unclear results) → `INSUFFICIENT_EVIDENCE`

**REPRODUCIBLE Requirements:**
- At least one evidence item
- Bug detected on at least one build
- Clear match between observed behavior and bug description
- Confidence >= 0.7

**BLOCKED_NEW_BUG Requirements:**
- An anomaly with `is_new_bug: true` was detected
- The new bug prevents or blocks reproduction of the expected bug
- Clear evidence of the blocking bug

**INSUFFICIENT_EVIDENCE Triggers:**
- No evidence collected for any build
- All builds had inconclusive results
- Evidence quality is LOW across all builds

**Assembly Guidelines:**
1. Weigh all build results — don't rely on a single build
2. Include ALL evidence IDs that support the verdict
3. List the actual repro steps that were performed
4. Explain confidence level based on evidence quality
5. If anomalies were found, include them in the anomalies field
6. Be conservative — when in doubt, use INSUFFICIENT_EVIDENCE

**Confidence Calibration:**
- 0.9-1.0: Multiple builds, clear evidence, consistent results
- 0.7-0.9: Clear on some builds, minor inconsistencies
- 0.5-0.7: Mixed results, some evidence gaps
- 0.0-0.5: Weak evidence, significant uncertainty

**Output:**
Your response will be parsed as a QAReproVerdict schema. Include all required fields."""


def create_verdict_assembly_agent(reasoning_effort: str = "high") -> Agent:
    """
    Create a verdict assembly specialist agent.

    Uses gpt-5.4 (reasoning tier) with output_type=QAReproVerdict
    for structured, machine-checkable verdicts.

    The output_type parameter makes the SDK enforce the Pydantic schema,
    ensuring all required fields are present and valid.

    Args:
        reasoning_effort: Reasoning effort level (none/low/medium/high/xhigh)

    Returns:
        Configured verdict assembly agent with structured output
    """
    from openai.types.shared import Reasoning

    model_chain = get_model_fallback_chain("reasoning")
    primary_model = model_chain[0]
    logger.info(f"Verdict Assembly Specialist using model chain: {model_chain}")

    agent = Agent(
        name="Verdict Assembly Specialist",
        instructions=VERDICT_ASSEMBLY_INSTRUCTIONS,
        tools=[],  # No tools - pure LLM reasoning
        model=primary_model,
        output_type=QAReproVerdict,  # SDK enforces Pydantic schema
        model_settings=ModelSettings(
            tool_choice="none",
            parallel_tool_calls=False,
            reasoning=Reasoning(effort=reasoning_effort),
        ),
    )

    return agent


__all__ = ["create_verdict_assembly_agent"]

