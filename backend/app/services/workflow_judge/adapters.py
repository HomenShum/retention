"""
Runtime Adapters — normalize raw events into CanonicalEvent format.

Each adapter consumes a specific runtime's event payloads and produces
CanonicalEvents that the judge can evaluate without knowing the source.

Current adapters:
  - ClaudeCodeAdapter: Claude Code hook payloads (stdin JSON)
  - TrajectoryAdapter: Stored trajectory files (from session_reader)
  - GenericAdapter: Any runtime with {tool_name, input, output}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from .canonical_event import CanonicalEvent, EventType, Runtime


# ── Tool name → EventType mapping ────────────────────────────

_TOOL_TYPE_MAP = {
    # File operations
    "Read": EventType.FILE_READ,
    "Glob": EventType.FILE_READ,
    "Grep": EventType.FILE_READ,
    "Edit": EventType.FILE_WRITE,
    "Write": EventType.FILE_WRITE,
    "NotebookEdit": EventType.FILE_WRITE,
    # Web
    "WebSearch": EventType.WEB_SEARCH,
    "WebFetch": EventType.WEB_FETCH,
    # Preview
    "preview_start": EventType.PREVIEW,
    "preview_screenshot": EventType.PREVIEW,
    "preview_snapshot": EventType.PREVIEW,
    "preview_click": EventType.PREVIEW,
    "preview_fill": EventType.PREVIEW,
    "preview_console_logs": EventType.PREVIEW,
    "preview_eval": EventType.PREVIEW,
    # Shell
    "Bash": EventType.BASH,
    # Agents
    "Agent": EventType.AGENT_SPAWN,
}

# Tools that produce file paths from their input
_FILE_EXTRACTORS = {
    "Read": lambda inp: [inp.get("file_path", "")] if inp.get("file_path") else [],
    "Edit": lambda inp: [inp.get("file_path", "")] if inp.get("file_path") else [],
    "Write": lambda inp: [inp.get("file_path", "")] if inp.get("file_path") else [],
    "Glob": lambda inp: [],
    "Grep": lambda inp: [inp.get("path", "")] if inp.get("path") else [],
}

# Tools that produce URLs
_URL_EXTRACTORS = {
    "WebSearch": lambda inp: [],
    "WebFetch": lambda inp: [inp.get("url", "")] if inp.get("url") else [],
}


def _classify_tool(tool_name: str) -> EventType:
    """Map a tool name to its canonical event type."""
    # Direct match
    if tool_name in _TOOL_TYPE_MAP:
        return _TOOL_TYPE_MAP[tool_name]
    # MCP preview tools
    if "preview" in tool_name.lower():
        return EventType.PREVIEW
    # Bash commands that look like tests
    return EventType.TOOL_CALL


def _extract_files(tool_name: str, tool_input: Dict[str, Any]) -> List[str]:
    """Extract file paths from tool input."""
    extractor = _FILE_EXTRACTORS.get(tool_name)
    if extractor:
        return [f for f in extractor(tool_input) if f]
    return []


def _extract_urls(tool_name: str, tool_input: Dict[str, Any]) -> List[str]:
    """Extract URLs from tool input."""
    extractor = _URL_EXTRACTORS.get(tool_name)
    if extractor:
        return [u for u in extractor(tool_input) if u]
    return []


def _summarize_input(tool_name: str, tool_input: Dict[str, Any]) -> str:
    """Create a human-readable summary of the tool input."""
    if tool_name == "Read":
        return f"Read {tool_input.get('file_path', '?')}"
    if tool_name == "Edit":
        return f"Edit {tool_input.get('file_path', '?')}"
    if tool_name == "Write":
        return f"Write {tool_input.get('file_path', '?')}"
    if tool_name == "Grep":
        return f"Search for '{tool_input.get('pattern', '?')}'"
    if tool_name == "Glob":
        return f"Find files matching '{tool_input.get('pattern', '?')}'"
    if tool_name == "WebSearch":
        return f"Search: {tool_input.get('query', '?')}"
    if tool_name == "WebFetch":
        return f"Fetch {tool_input.get('url', '?')}"
    if tool_name == "Bash":
        cmd = tool_input.get("command", "?")
        return f"$ {cmd[:80]}{'...' if len(cmd) > 80 else ''}"
    if tool_name == "Agent":
        return f"Agent: {tool_input.get('description', '?')}"
    # Generic
    return f"{tool_name}({', '.join(f'{k}={str(v)[:30]}' for k, v in list(tool_input.items())[:3])})"


# ── Claude Code Adapter ─────────────────────────────────────

class ClaudeCodeAdapter:
    """Normalize Claude Code hook payloads into CanonicalEvents."""

    def __init__(self, run_id: Optional[str] = None):
        self.run_id = run_id or f"cc-{uuid.uuid4().hex[:8]}"
        self._step_counter = 0

    def normalize_prompt(self, payload: Dict[str, Any]) -> CanonicalEvent:
        """Normalize a UserPromptSubmit hook payload."""
        event = CanonicalEvent(
            run_id=self.run_id,
            runtime=Runtime.CLAUDE_CODE,
            session_id=payload.get("session_id", ""),
            step_index=self._step_counter,
            event_type=EventType.PROMPT,
            prompt=payload.get("prompt", ""),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._step_counter += 1
        return event

    def normalize_tool_use(self, payload: Dict[str, Any]) -> CanonicalEvent:
        """Normalize a PostToolUse hook payload."""
        tool_name = payload.get("tool_name", "")
        tool_input = payload.get("input", payload.get("tool_input", {}))

        event = CanonicalEvent(
            run_id=self.run_id,
            runtime=Runtime.CLAUDE_CODE,
            session_id=payload.get("session_id", ""),
            step_index=self._step_counter,
            event_type=_classify_tool(tool_name),
            tool_name=tool_name,
            tool_input_summary=_summarize_input(tool_name, tool_input),
            files_touched=_extract_files(tool_name, tool_input),
            urls_visited=_extract_urls(tool_name, tool_input),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._step_counter += 1
        return event

    def normalize_stop(self, payload: Dict[str, Any]) -> CanonicalEvent:
        """Normalize a Stop hook payload."""
        event = CanonicalEvent(
            run_id=self.run_id,
            runtime=Runtime.CLAUDE_CODE,
            session_id=payload.get("session_id", ""),
            step_index=self._step_counter,
            event_type=EventType.COMPLETION,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._step_counter += 1
        return event


# ── Trajectory Adapter ───────────────────────────────────────

class TrajectoryAdapter:
    """Convert stored trajectory files (from session JSONLs) into CanonicalEvents."""

    def normalize(self, trajectory: Dict[str, Any]) -> List[CanonicalEvent]:
        """Convert a full trajectory dict into a list of CanonicalEvents."""
        events: List[CanonicalEvent] = []
        run_id = trajectory.get("trajectory_id", f"traj-{uuid.uuid4().hex[:8]}")

        for step in trajectory.get("steps", []):
            tool_name = step.get("action", "")
            mcp_calls = step.get("mcp_tool_calls", [])
            tool_input = mcp_calls[0].get("params", {}) if mcp_calls else {}
            metadata = step.get("metadata", {})

            event = CanonicalEvent(
                run_id=run_id,
                runtime=Runtime.CLAUDE_CODE,
                step_index=step.get("step_index", len(events)),
                event_type=_classify_tool(tool_name),
                tool_name=tool_name,
                tool_input_summary=_summarize_input(tool_name, tool_input),
                state_before=step.get("state_before", {}),
                state_after=step.get("state_after", {}),
                token_cost=(
                    metadata.get("input_tokens", 0)
                    + metadata.get("output_tokens", 0)
                    + metadata.get("cache_read_tokens", 0)
                ),
                time_cost_ms=step.get("duration_ms", 0),
                timestamp=step.get("timestamp", ""),
            )
            events.append(event)

        return events


# ── Generic Adapter ──────────────────────────────────────────

class GenericAdapter:
    """Minimal adapter for any runtime that provides tool_name + input."""

    def __init__(self, runtime: Runtime = Runtime.UNKNOWN, run_id: Optional[str] = None):
        self.runtime = runtime
        self.run_id = run_id or f"gen-{uuid.uuid4().hex[:8]}"
        self._step_counter = 0

    def normalize(self, payload: Dict[str, Any]) -> CanonicalEvent:
        """Normalize a generic tool call."""
        tool_name = payload.get("tool_name", payload.get("tool", ""))
        tool_input = payload.get("input", payload.get("tool_input", {}))

        event = CanonicalEvent(
            run_id=self.run_id,
            runtime=self.runtime,
            step_index=self._step_counter,
            event_type=_classify_tool(tool_name),
            tool_name=tool_name,
            tool_input_summary=_summarize_input(tool_name, tool_input),
            files_touched=_extract_files(tool_name, tool_input),
            urls_visited=_extract_urls(tool_name, tool_input),
            token_cost=payload.get("token_cost", 0),
            time_cost_ms=payload.get("time_cost_ms", 0),
            timestamp=payload.get("timestamp", ""),
        )
        self._step_counter += 1
        return event
