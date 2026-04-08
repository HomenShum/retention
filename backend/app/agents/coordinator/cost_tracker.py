"""CostTracker — drop-in replacement for PipelineTokenTracker with per-tool audit trail.

Adopts claw-code's dual-purpose pattern: tracks both aggregate totals AND a
chronological event list. The events list enables post-hoc analysis of which
tools cost the most, which stages are expensive, and where waste occurs.

Interface-compatible with PipelineTokenTracker:
  - set_stage(stage)
  - record(input_tokens, output_tokens)
  - totals() -> dict  (same shape)

New capabilities:
  - record(tool_name=...) — optional per-tool attribution
  - events: list[CostEvent] — full audit trail
  - by_tool() -> dict — per-tool cost breakdown
  - as_markdown() -> str — structured export
  - as_frozen() -> tuple — immutable snapshot
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict


@dataclass
class CostEvent:
    """Single cost event with tool attribution."""

    label: str
    tool_name: str
    stage: str
    tokens_in: int
    tokens_out: int
    timestamp: str

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out


class CostTracker:
    """Accumulates token usage with per-tool audit trail.

    Drop-in replacement for PipelineTokenTracker. All existing call sites
    that use .set_stage(), .record(), and .totals() work unchanged.
    """

    def __init__(self) -> None:
        self.stages: Dict[str, Dict[str, int]] = {}
        self.current_stage: str = "CRAWL"
        self.events: list[CostEvent] = []
        self._init_stage("CRAWL")

    def _init_stage(self, stage: str) -> None:
        if stage not in self.stages:
            self.stages[stage] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "api_calls": 0,
            }

    def set_stage(self, stage: str) -> None:
        """Set the current pipeline stage (CRAWL, WORKFLOW, TESTCASE, etc.)."""
        self.current_stage = stage
        self._init_stage(stage)

    def record(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        tool_name: str = "",
    ) -> None:
        """Record a token usage event.

        Backward-compatible: tool_name is optional. Existing call sites
        that pass only input_tokens/output_tokens work unchanged.
        """
        self._init_stage(self.current_stage)
        s = self.stages[self.current_stage]
        s["input_tokens"] += input_tokens
        s["output_tokens"] += output_tokens
        s["total_tokens"] += input_tokens + output_tokens
        s["api_calls"] += 1

        # Audit trail (the key addition over PipelineTokenTracker)
        self.events.append(
            CostEvent(
                label=f"{self.current_stage}:{tool_name or 'llm_call'}",
                tool_name=tool_name or "llm_call",
                stage=self.current_stage,
                tokens_in=input_tokens,
                tokens_out=output_tokens,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )

    def totals(self) -> Dict[str, Any]:
        """Return totals in the same shape as PipelineTokenTracker.totals()."""
        total_in = sum(s["input_tokens"] for s in self.stages.values())
        total_out = sum(s["output_tokens"] for s in self.stages.values())
        total_calls = sum(s["api_calls"] for s in self.stages.values())

        # Cost estimate at OpenAI gpt-5.4-mini pricing ($0.40/M in, $1.60/M out)
        cost_usd = (total_in * 0.40 + total_out * 1.60) / 1_000_000

        return {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "api_calls": total_calls,
            "estimated_cost_usd": round(cost_usd, 6),
            "by_stage": dict(self.stages),
        }

    def by_tool(self) -> Dict[str, Dict[str, Any]]:
        """Per-tool cost breakdown — the key new capability."""
        tools: Dict[str, Dict[str, Any]] = {}
        for ev in self.events:
            if ev.tool_name not in tools:
                tools[ev.tool_name] = {
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "total_tokens": 0,
                    "calls": 0,
                }
            t = tools[ev.tool_name]
            t["tokens_in"] += ev.tokens_in
            t["tokens_out"] += ev.tokens_out
            t["total_tokens"] += ev.total_tokens
            t["calls"] += 1
        return tools

    def as_markdown(self) -> str:
        """Structured export for history log and reports."""
        totals = self.totals()
        lines = [
            "## Cost Summary",
            f"- Total tokens: {totals['total_tokens']:,}",
            f"- Input: {totals['input_tokens']:,} | Output: {totals['output_tokens']:,}",
            f"- API calls: {totals['api_calls']}",
            f"- Estimated cost: ${totals['estimated_cost_usd']:.4f}",
            "",
            "### By Stage",
        ]
        for stage, data in self.stages.items():
            if data["api_calls"] > 0:
                lines.append(
                    f"- **{stage}**: {data['total_tokens']:,} tokens, "
                    f"{data['api_calls']} calls"
                )

        tool_data = self.by_tool()
        if tool_data:
            lines.append("")
            lines.append("### By Tool")
            sorted_tools = sorted(
                tool_data.items(), key=lambda x: x[1]["total_tokens"], reverse=True
            )
            for name, data in sorted_tools[:10]:
                lines.append(
                    f"- **{name}**: {data['total_tokens']:,} tokens, "
                    f"{data['calls']} calls"
                )

        return "\n".join(lines)

    def as_frozen(self) -> tuple[CostEvent, ...]:
        """Return immutable snapshot of all events."""
        return tuple(self.events)

    def cost_events_as_dicts(self) -> list[dict[str, Any]]:
        """Serialize events for JSON storage (feeds into RunProof)."""
        return [
            {
                "tool_name": ev.tool_name,
                "stage": ev.stage,
                "tokens_in": ev.tokens_in,
                "tokens_out": ev.tokens_out,
                "timestamp": ev.timestamp,
            }
            for ev in self.events
        ]
