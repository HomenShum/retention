"""Canonical event schema for retention telemetry.

Every tracked action (tool call, LLM think, file edit, search) is normalized
into a CanonicalEvent before storage.
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class CanonicalEvent:
    """A single canonical telemetry event."""

    event_type: str  # "tool_call", "think", "file_edit", "search", etc.
    tool_name: Optional[str] = None
    input_keys: list = field(default_factory=list)
    scrubbed_input: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    runtime: str = "generic"  # "openai", "anthropic", "langchain", "crewai", etc.
    duration_ms: Optional[int] = None

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON storage."""
        return {
            "event_type": self.event_type,
            "tool_name": self.tool_name,
            "input_keys": self.input_keys,
            "scrubbed_input": self.scrubbed_input,
            "timestamp": self.timestamp,
            "runtime": self.runtime,
            "duration_ms": self.duration_ms,
        }
