"""retention.sh SDK — drop-in agent observability for any Python AI app.

3 lines to integrate:

    from retention_sh import track
    track()  # auto-patches OpenAI, Anthropic, LangChain, etc.

Or wrap individual calls:

    from retention_sh import observe
    result = observe(my_tool_function, name="search")(query="AI startups")
"""

from .core import track, observe, log_tool_call, configure, RetentionConfig
from .wrappers import (
    patch_openai,
    patch_anthropic,
    patch_langchain,
    patch_crewai,
    patch_openai_agents,
    patch_claude_agent_sdk,
    patch_pydantic_ai,
    LangChainRetentionHandler,
    OpenAIRetentionProcessor,
)

__version__ = "0.1.0"
__all__ = [
    "track",
    "observe",
    "log_tool_call",
    "configure",
    "RetentionConfig",
    "patch_openai",
    "patch_anthropic",
    "patch_langchain",
    "patch_crewai",
    "patch_openai_agents",
    "patch_claude_agent_sdk",
    "patch_pydantic_ai",
    "LangChainRetentionHandler",
    "OpenAIRetentionProcessor",
]
