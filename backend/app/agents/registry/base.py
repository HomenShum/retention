"""AgentConfig dataclass and AgentRegistry singleton.

Each agent type declares its own config — system prompt, tool categories,
model, turn budget, and optional pre/post hooks. The registry keeps them
isolated so adding a new agent never touches existing ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type, Union

from pydantic import BaseModel


@dataclass
class AgentConfig:
    """Declarative configuration for a registered agent type."""

    # Identity
    name: str  # URL-safe slug, e.g. "strategy-brief"

    # Prompt — either a static string or an async/sync callable(**kwargs) -> str
    system_prompt: Union[str, Callable[..., Any]]

    # Which tool categories this agent can access (keys in tool_schemas.TOOL_CATEGORIES)
    tool_categories: List[str] = field(default_factory=lambda: ["codebase"])

    # Model defaults
    model: str = "gpt-5.4-mini"
    reasoning_effort: str = "high"  # none | low | medium | high | xhigh
    max_turns: int = 1000
    max_tool_result_chars: int = 8000

    # Optional lifecycle hooks
    # pre_run(question, api_key, model, **kwargs) -> dict  (e.g. strategy selection)
    pre_run: Optional[Callable[..., Any]] = None
    # post_run(text, tool_results, **kwargs) -> dict  (e.g. evidence extraction)
    post_run: Optional[Callable[..., Any]] = None

    # Optional Pydantic response model for the endpoint
    response_model: Optional[Type[BaseModel]] = None


class AgentRegistry:
    """Singleton registry of agent configs, keyed by name."""

    _agents: Dict[str, AgentConfig] = {}

    @classmethod
    def register(cls, config: AgentConfig) -> None:
        cls._agents[config.name] = config

    @classmethod
    def get(cls, name: str) -> AgentConfig:
        if name not in cls._agents:
            raise KeyError(f"Unknown agent type: {name!r}. Available: {list(cls._agents.keys())}")
        return cls._agents[name]

    @classmethod
    def list_agents(cls) -> List[str]:
        return list(cls._agents.keys())

    @classmethod
    def has(cls, name: str) -> bool:
        return name in cls._agents
