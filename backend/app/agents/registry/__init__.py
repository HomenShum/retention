"""Agent Registry — declarative agent configs with isolated prompts, tools, and hooks."""

from .base import AgentConfig, AgentRegistry
from .runner import AgentRunner

__all__ = ["AgentConfig", "AgentRegistry", "AgentRunner"]
