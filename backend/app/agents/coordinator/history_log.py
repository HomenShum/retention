"""Structured history log — claw-code pattern for auditable agent sessions.

Records routing decisions, tool executions, and cost data as structured data
(not just text logs). Supports export to markdown and JSON, and persistence
to disk for session resumption.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .cost_tracker import CostTracker
from .execution_types import ToolExecution

logger = logging.getLogger(__name__)

_SESSIONS_DIR = Path(__file__).resolve().parents[3] / "data" / "sessions"


@dataclass
class RoutingDecision:
    """Record of a single agent routing decision."""

    user_input_preview: str  # first 100 chars
    selected_agent: str
    scores: dict[str, float]
    timestamp: str


@dataclass
class HistoryLog:
    """Structured audit trail for an agent session."""

    session_id: str
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    routing_decisions: list[RoutingDecision] = field(default_factory=list)
    tool_executions: list[ToolExecution] = field(default_factory=list)
    cost_tracker: CostTracker = field(default_factory=CostTracker)
    total_turns: int = 0
    agent_handoff_counts: dict[str, int] = field(default_factory=dict)

    def log_routing(
        self,
        user_input: str,
        selected_agent: str,
        scores: dict[str, float],
    ) -> None:
        """Record a routing decision."""
        self.routing_decisions.append(
            RoutingDecision(
                user_input_preview=user_input[:100],
                selected_agent=selected_agent,
                scores=scores,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )
        self.agent_handoff_counts[selected_agent] = (
            self.agent_handoff_counts.get(selected_agent, 0) + 1
        )
        self.total_turns += 1

    def log_tool_execution(self, tool_exec: ToolExecution) -> None:
        """Record a tool execution."""
        self.tool_executions.append(tool_exec)

    def as_markdown(self) -> str:
        """Export as structured markdown report."""
        lines = [
            f"# Session {self.session_id}",
            f"Started: {self.started_at}",
            f"Total turns: {self.total_turns}",
            "",
        ]

        # Routing decisions
        if self.routing_decisions:
            lines.append("## Routing Decisions")
            for rd in self.routing_decisions:
                score_str = ", ".join(
                    f"{k}: {v:.2f}" for k, v in rd.scores.items()
                )
                lines.append(
                    f"- **{rd.selected_agent}** ({score_str}) "
                    f'— "{rd.user_input_preview}"'
                )
            lines.append("")

        # Agent usage
        if self.agent_handoff_counts:
            lines.append("## Agent Usage")
            for agent, count in sorted(
                self.agent_handoff_counts.items(),
                key=lambda x: x[1],
                reverse=True,
            ):
                lines.append(f"- {agent}: {count} handoffs")
            lines.append("")

        # Cost summary
        lines.append(self.cost_tracker.as_markdown())

        return "\n".join(lines)

    def as_json(self) -> dict[str, Any]:
        """Serialize to dict for JSON storage."""
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "total_turns": self.total_turns,
            "agent_handoff_counts": self.agent_handoff_counts,
            "routing_decisions": [
                {
                    "user_input_preview": rd.user_input_preview,
                    "selected_agent": rd.selected_agent,
                    "scores": rd.scores,
                    "timestamp": rd.timestamp,
                }
                for rd in self.routing_decisions
            ],
            "tool_executions": [
                {
                    "tool_name": te.tool_name,
                    "input_keys": list(te.input_keys),
                    "tokens_in": te.tokens_in,
                    "tokens_out": te.tokens_out,
                    "latency_ms": te.latency_ms,
                    "success": te.success,
                    "timestamp": te.timestamp,
                    "stage": te.stage,
                }
                for te in self.tool_executions
            ],
            "cost_totals": self.cost_tracker.totals(),
            "cost_by_tool": self.cost_tracker.by_tool(),
        }

    def save(self, path: Optional[Path] = None) -> Path:
        """Persist session to disk."""
        if path is None:
            _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            path = _SESSIONS_DIR / f"{self.session_id}.json"

        path.write_text(json.dumps(self.as_json(), indent=2))
        logger.info(f"Session saved: {path}")
        return path

    @classmethod
    def load(cls, path: Path) -> "HistoryLog":
        """Restore session from disk."""
        data = json.loads(path.read_text())

        log = cls(
            session_id=data["session_id"],
            started_at=data.get("started_at", ""),
            total_turns=data.get("total_turns", 0),
            agent_handoff_counts=data.get("agent_handoff_counts", {}),
        )

        for rd in data.get("routing_decisions", []):
            log.routing_decisions.append(
                RoutingDecision(
                    user_input_preview=rd["user_input_preview"],
                    selected_agent=rd["selected_agent"],
                    scores=rd.get("scores", {}),
                    timestamp=rd.get("timestamp", ""),
                )
            )

        for te_data in data.get("tool_executions", []):
            log.tool_executions.append(
                ToolExecution(
                    tool_name=te_data["tool_name"],
                    input_keys=tuple(te_data.get("input_keys", [])),
                    tokens_in=te_data.get("tokens_in", 0),
                    tokens_out=te_data.get("tokens_out", 0),
                    latency_ms=te_data.get("latency_ms", 0),
                    success=te_data.get("success", True),
                    timestamp=te_data.get("timestamp", ""),
                    stage=te_data.get("stage", ""),
                )
            )

        logger.info(f"Session loaded: {path}")
        return log
