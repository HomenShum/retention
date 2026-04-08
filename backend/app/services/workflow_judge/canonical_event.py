"""
Canonical Event Schema — runtime-agnostic event format for TA workflow judge.

Every runtime (Claude Code, OpenAI Agents SDK, LangGraph, generic CLI)
normalizes its traces into this schema. The judge evaluates events
regardless of where they came from.

Usage:
    from app.services.workflow_judge.canonical_event import CanonicalEvent, EventType
    from app.services.workflow_judge.adapters import ClaudeCodeAdapter

    # Normalize raw Claude Code hook payload
    adapter = ClaudeCodeAdapter()
    event = adapter.normalize(raw_hook_payload)

    # Feed to judge
    judge.ingest(event)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class EventType(str, Enum):
    """What happened in this event."""
    PROMPT = "prompt"                # User/system prompt
    TOOL_CALL = "tool_call"          # Tool invocation + result
    FILE_READ = "file_read"          # File read (subset of tool_call)
    FILE_WRITE = "file_write"        # File write/edit
    WEB_SEARCH = "web_search"        # Web search
    WEB_FETCH = "web_fetch"          # URL fetch / deep read
    PREVIEW = "preview"              # Browser preview action
    BASH = "bash"                    # Shell command
    TEST_RUN = "test_run"            # Test execution
    AGENT_SPAWN = "agent_spawn"      # Sub-agent launched
    COMPLETION = "completion"        # Session/run ended
    CORRECTION = "correction"        # User correction ("you forgot X")
    NUDGE = "nudge"                  # Judge nudge emitted


class Runtime(str, Enum):
    """Source runtime that produced this event."""
    CLAUDE_CODE = "claude_code"
    OPENAI_AGENTS_SDK = "openai_agents_sdk"
    LANGGRAPH = "langgraph"
    LANGSMITH = "langsmith"
    OPENCLAW = "openclaw"
    GENERIC_CLI = "generic_cli"
    UNKNOWN = "unknown"


@dataclass
class CanonicalEvent:
    """One atomic event in a workflow trace.

    Every runtime adapter must produce these.
    The judge consumes these — it never sees raw runtime payloads.
    """

    # ── Identity ─────────────────────────────────────────────
    run_id: str                          # Unique run / session ID
    runtime: Runtime                     # Source runtime
    session_id: str = ""                 # Optional sub-session ID
    step_index: int = 0                  # Monotonic step counter

    # ── What happened ────────────────────────────────────────
    event_type: EventType = EventType.TOOL_CALL
    tool_name: str = ""                  # Normalized tool name (Read, Edit, WebSearch, etc.)
    tool_input_summary: str = ""         # Human-readable input summary
    tool_output_summary: str = ""        # Human-readable output summary (truncated)
    prompt: str = ""                     # User prompt (for PROMPT events)

    # ── Context ──────────────────────────────────────────────
    workflow_detected: str = ""          # Workflow ID if detected
    files_touched: List[str] = field(default_factory=list)
    urls_visited: List[str] = field(default_factory=list)
    artifact_refs: List[str] = field(default_factory=list)  # Screenshot paths, diff hashes, etc.

    # ── State ────────────────────────────────────────────────
    state_before: Dict[str, Any] = field(default_factory=dict)
    state_after: Dict[str, Any] = field(default_factory=dict)

    # ── Cost ─────────────────────────────────────────────────
    token_cost: int = 0                  # Input + output tokens
    time_cost_ms: int = 0               # Wall-clock milliseconds
    model: str = ""                     # Model used (if applicable)
    finish_reason: str = ""             # stop, max_tokens, tool_use, etc.

    # ── Judge annotation (filled by judge, not adapter) ──────
    judge_annotation: Optional[str] = None   # Step ID this maps to
    judge_status: Optional[str] = None       # done / partial / missing

    # ── Timestamp ────────────────────────────────────────────
    timestamp: str = ""                  # ISO 8601

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for storage / API response."""
        return {
            "run_id": self.run_id,
            "runtime": self.runtime.value,
            "session_id": self.session_id,
            "step_index": self.step_index,
            "event_type": self.event_type.value,
            "tool_name": self.tool_name,
            "tool_input_summary": self.tool_input_summary,
            "tool_output_summary": self.tool_output_summary,
            "prompt": self.prompt,
            "workflow_detected": self.workflow_detected,
            "files_touched": self.files_touched,
            "urls_visited": self.urls_visited,
            "artifact_refs": self.artifact_refs,
            "state_before": self.state_before,
            "state_after": self.state_after,
            "token_cost": self.token_cost,
            "time_cost_ms": self.time_cost_ms,
            "model": self.model,
            "finish_reason": self.finish_reason,
            "judge_annotation": self.judge_annotation,
            "judge_status": self.judge_status,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> CanonicalEvent:
        """Deserialize from stored JSON."""
        return cls(
            run_id=d.get("run_id", ""),
            runtime=Runtime(d.get("runtime", "unknown")),
            session_id=d.get("session_id", ""),
            step_index=d.get("step_index", 0),
            event_type=EventType(d.get("event_type", "tool_call")),
            tool_name=d.get("tool_name", ""),
            tool_input_summary=d.get("tool_input_summary", ""),
            tool_output_summary=d.get("tool_output_summary", ""),
            prompt=d.get("prompt", ""),
            workflow_detected=d.get("workflow_detected", ""),
            files_touched=d.get("files_touched", []),
            urls_visited=d.get("urls_visited", []),
            artifact_refs=d.get("artifact_refs", []),
            state_before=d.get("state_before", {}),
            state_after=d.get("state_after", {}),
            token_cost=d.get("token_cost", 0),
            time_cost_ms=d.get("time_cost_ms", 0),
            model=d.get("model", ""),
            finish_reason=d.get("finish_reason", ""),
            judge_annotation=d.get("judge_annotation"),
            judge_status=d.get("judge_status"),
            timestamp=d.get("timestamp", ""),
        )
