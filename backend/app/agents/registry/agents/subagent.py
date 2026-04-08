"""Generic subagent — codebase-only tool loop.

This is the registry equivalent of the original /api/deep-agent/subagent endpoint.
Registered as "subagent" in the AgentRegistry.
"""

from ..base import AgentConfig, AgentRegistry

AgentRegistry.register(
    AgentConfig(
        name="subagent",
        system_prompt=(
            "You are a focused sub-agent for retention.sh. "
            "Complete the task using the provided tools. Be concise. "
            "Return your findings as structured text."
        ),
        tool_categories=["codebase"],
        model="gpt-5.4-mini",
        max_turns=1000,
    )
)
