"""Frozen execution types — immutable proof artifacts for tool calls and runs.

Adopts claw-code's immutability pattern: every execution result is a frozen
dataclass that cannot be mutated after creation. This provides tamper-evident
audit trails and enables safe sharing across threads.

Existing mutable TrajectoryStep/TrajectoryLog stay unchanged for backward compat.
Frozen variants are created at run completion via factory methods.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class ToolExecution:
    """Immutable record of a single tool call."""

    tool_name: str
    input_keys: tuple[str, ...]  # privacy-safe: keys only, not values
    tokens_in: int
    tokens_out: int
    latency_ms: int
    success: bool
    timestamp: str
    stage: str  # CRAWL, WORKFLOW, TESTCASE, EXECUTION


@dataclass(frozen=True)
class StepProof:
    """Immutable record of a single pipeline step (may contain multiple tool calls)."""

    step_index: int
    tool_executions: tuple[ToolExecution, ...]
    state_fingerprint_before: str
    state_fingerprint_after: str


@dataclass(frozen=True)
class RunProof:
    """Immutable proof artifact for a complete pipeline run."""

    run_id: str
    app_url: str
    tool_executions: tuple[ToolExecution, ...]
    total_tokens_in: int
    total_tokens_out: int
    total_cost_usd: float
    verdict: str  # PASS, FAIL, BLOCKED
    pass_rate: float
    tests_total: int
    tests_passed: int
    started_at: str
    completed_at: str

    @classmethod
    def from_pipeline_result(cls, result: dict[str, Any]) -> "RunProof":
        """Create a RunProof from a pipeline result dict."""
        token_usage = result.get("token_usage", {})
        test_results = result.get("test_results", [])

        total_in = token_usage.get("input_tokens", 0)
        total_out = token_usage.get("output_tokens", 0)
        cost = token_usage.get("estimated_cost_usd", 0.0)

        passed = sum(1 for t in test_results if t.get("status") == "PASS")
        total = len(test_results)
        pass_rate = passed / total if total > 0 else 0.0

        # Determine verdict
        if total == 0:
            verdict = "BLOCKED"
        elif pass_rate >= 0.8:
            verdict = "PASS"
        else:
            verdict = "FAIL"

        # Extract tool executions from cost events if available
        tool_execs: list[ToolExecution] = []
        for event in result.get("cost_events", []):
            tool_execs.append(
                ToolExecution(
                    tool_name=event.get("tool_name", "unknown"),
                    input_keys=tuple(event.get("input_keys", [])),
                    tokens_in=event.get("tokens_in", 0),
                    tokens_out=event.get("tokens_out", 0),
                    latency_ms=event.get("latency_ms", 0),
                    success=event.get("success", True),
                    timestamp=event.get("timestamp", ""),
                    stage=event.get("stage", ""),
                )
            )

        now = datetime.now(timezone.utc).isoformat()

        return cls(
            run_id=result.get("run_id", "unknown"),
            app_url=result.get("app_url", ""),
            tool_executions=tuple(tool_execs),
            total_tokens_in=total_in,
            total_tokens_out=total_out,
            total_cost_usd=cost,
            verdict=verdict,
            pass_rate=pass_rate,
            tests_total=total,
            tests_passed=passed,
            started_at=result.get("started_at", now),
            completed_at=result.get("completed_at", now),
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON storage."""
        return {
            "run_id": self.run_id,
            "app_url": self.app_url,
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
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_cost_usd": self.total_cost_usd,
            "verdict": self.verdict,
            "pass_rate": self.pass_rate,
            "tests_total": self.tests_total,
            "tests_passed": self.tests_passed,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
