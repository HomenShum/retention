"""
Standard Evidence Schema for Benchmark Runs.

Every benchmark run (baseline or test-assurance) emits a single
BenchmarkRunEvidence JSON file. This is the product backbone:
without it, you cannot compare runs, build dashboards, or
onboard outside teams.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any, Literal

from pydantic import BaseModel, Field


class AgentMode(str, Enum):
    """Which execution mode produced this run."""
    CLAUDE_BASELINE = "claude-baseline"
    TEST_ASSURANCE = "test-assurance"


class RunStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"


class BenchmarkVerdictLabel(str, Enum):
    SUCCESS = "success"
    BUG_FOUND = "bug-found"
    BUG_FOUND_DETERMINISTIC = "bug-found-deterministic"
    BUG_FOUND_FLAKY = "bug-found-flaky"
    WRONG_OUTPUT = "wrong-output"
    TIMEOUT = "timeout"
    INFRA_FAILURE = "infra-failure"
    FLAKINESS_DETECTED = "flakiness-detected"
    NEEDS_HUMAN_REVIEW = "needs-human-review"


class BenchmarkVerdict(BaseModel):
    label: BenchmarkVerdictLabel = Field(
        default=BenchmarkVerdictLabel.NEEDS_HUMAN_REVIEW,
        description="Verdict classification",
    )
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="Verdict confidence")
    reason: str = Field("", description="Short explanation")
    blocking_issue: Optional[str] = Field(
        default=None, description="Root cause when status is blocked"
    )


class BenchmarkArtifacts(BaseModel):
    trace_path: Optional[str] = Field(default=None, description="Path to trace.zip")
    video_path: Optional[str] = Field(default=None, description="Path to video.webm")
    screenshots: List[str] = Field(default_factory=list, description="Screenshot paths")
    logs_path: Optional[str] = Field(default=None, description="Path to logs.txt")
    console_path: Optional[str] = Field(default=None, description="Path to console.json")
    network_path: Optional[str] = Field(default=None, description="Path to network.json")
    action_spans_path: Optional[str] = Field(
        default=None, description="Path to action_spans.json manifest"
    )
    tool_calls_path: Optional[str] = Field(
        default=None, description="Path to tool_calls.json with invocation log"
    )

    # Weighted importance for completeness scoring
    ARTIFACT_WEIGHTS: Dict[str, float] = {
        "trace_path": 0.25,
        "video_path": 0.20,
        "screenshots": 0.15,
        "action_spans_path": 0.15,
        "logs_path": 0.10,
        "console_path": 0.05,
        "network_path": 0.05,
        "tool_calls_path": 0.05,
    }

    model_config = {"arbitrary_types_allowed": True}

    def completeness_score(self) -> float:
        """Weighted completeness score — critical artifacts count more."""
        score = 0.0
        checks = {
            "trace_path": bool(self.trace_path),
            "video_path": bool(self.video_path),
            "screenshots": len(self.screenshots) > 0,
            "action_spans_path": bool(self.action_spans_path),
            "logs_path": bool(self.logs_path),
            "console_path": bool(self.console_path),
            "network_path": bool(self.network_path),
            "tool_calls_path": bool(self.tool_calls_path),
        }
        for key, present in checks.items():
            if present:
                score += self.ARTIFACT_WEIGHTS.get(key, 0.0)
        return round(score, 4)


class BenchmarkTaskMetrics(BaseModel):
    duration_seconds: float = Field(0.0, description="Wall-clock seconds")
    reruns: int = Field(0, description="Number of reruns attempted")
    manual_interventions: int = Field(0, description="Human interventions needed")
    artifact_completeness_score: float = Field(
        0.0, ge=0.0, le=1.0, description="Fraction of artifact slots filled"
    )


# March 2026 pricing per 1M tokens (extends verdict_models.MODEL_PRICING)
# Effort-level variants (e.g. "gpt-5.4:high") use same token price but
# produce more reasoning tokens — the effort suffix tracks quality tier.
BENCHMARK_MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # GPT-5.4 Family (March 2026) — base + effort variants
    "gpt-5.4": {"input": 2.50, "output": 15.00},
    "gpt-5.4:high": {"input": 2.50, "output": 15.00},
    "gpt-5.4:xhigh": {"input": 2.50, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "gpt-5.4-mini:high": {"input": 0.75, "output": 4.50},
    "gpt-5.4-mini:xhigh": {"input": 0.75, "output": 4.50},
    "gpt-5.4-nano": {"input": 0.20, "output": 1.25},
    # GPT-5 Family (legacy)
    "gpt-5": {"input": 2.00, "output": 8.00},
    "gpt-5-mini": {"input": 0.25, "output": 1.00},
    "gpt-5-nano": {"input": 0.10, "output": 0.40},
    # Anthropic
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    # Google
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
}

# Human-readable labels for UI dropdowns
MODEL_LABELS: Dict[str, str] = {
    "gpt-5.4": "GPT-5.4",
    "gpt-5.4:high": "GPT-5.4 (high)",
    "gpt-5.4:xhigh": "GPT-5.4 (xhigh)",
    "gpt-5.4-mini": "GPT-5.4 Mini",
    "gpt-5.4-mini:high": "GPT-5.4 Mini (high)",
    "gpt-5.4-mini:xhigh": "GPT-5.4 Mini (xhigh)",
    "gpt-5.4-nano": "GPT-5.4 Nano",
    "gpt-5-mini": "GPT-5 Mini",
    "claude-opus-4-6": "Claude Opus 4.6",
    "claude-sonnet-4-6": "Claude Sonnet 4.6",
    "claude-haiku-4-5": "Claude Haiku 4.5",
    "gemini-2.5-pro": "Gemini 2.5 Pro",
}


class BenchmarkCost(BaseModel):
    token_input: int = Field(0, description="Total input tokens")
    token_output: int = Field(0, description="Total output tokens")
    token_cost_usd: float = Field(0.0, description="LLM token cost in USD")
    compute_cost_usd: float = Field(0.0, description="Compute/infra cost in USD")
    ci_minutes: float = Field(0.0, description="CI pipeline minutes consumed")
    ci_cost_usd: float = Field(0.0, description="CI infrastructure cost in USD")
    storage_gb: float = Field(0.0, description="Artifact storage in GB")
    storage_cost_usd: float = Field(0.0, description="Storage cost in USD (S3 $0.023/GB-month)")
    total_cost_usd: float = Field(0.0, description="Total cost in USD")

    # Platform-specific costs (e.g., device_lease_usd for android)
    platform_costs: Dict[str, float] = Field(default_factory=dict)

    @classmethod
    def from_token_counts(
        cls,
        input_tokens: int,
        output_tokens: int,
        model: str = "gpt-5-mini",
        compute_cost: float = 0.0,
        ci_minutes: float = 0.0,
        ci_cost_per_minute: float = 0.006,  # GitHub Actions Linux 2-core
        storage_gb: float = 0.0,
        storage_cost_per_gb: float = 0.023,  # S3 Standard
    ) -> "BenchmarkCost":
        pricing = BENCHMARK_MODEL_PRICING.get(model, {"input": 0.0, "output": 0.0})
        token_cost = (
            input_tokens * pricing["input"] / 1_000_000
            + output_tokens * pricing["output"] / 1_000_000
        )
        ci_cost = ci_minutes * ci_cost_per_minute
        storage_cost = storage_gb * storage_cost_per_gb
        total = token_cost + compute_cost + ci_cost + storage_cost
        return cls(
            token_input=input_tokens,
            token_output=output_tokens,
            token_cost_usd=round(token_cost, 6),
            compute_cost_usd=round(compute_cost, 6),
            ci_minutes=round(ci_minutes, 2),
            ci_cost_usd=round(ci_cost, 6),
            storage_gb=round(storage_gb, 6),
            storage_cost_usd=round(storage_cost, 6),
            total_cost_usd=round(total, 6),
        )


class BenchmarkRunEvidence(BaseModel):
    """Top-level evidence schema emitted by every benchmark run."""
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str = Field(..., description="e.g. app-login-001")
    app_id: str = Field(..., description="e.g. khush-film-rating")
    platform: Literal["web", "android-emulator"] = Field("web")
    environment: Literal["local", "staging"] = Field("local")
    agent_mode: AgentMode = Field(...)
    start_time: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    end_time: Optional[str] = Field(default=None)
    status: RunStatus = Field(RunStatus.BLOCKED)
    verdict: BenchmarkVerdict = Field(default_factory=BenchmarkVerdict)
    artifacts: BenchmarkArtifacts = Field(default_factory=BenchmarkArtifacts)
    task_metrics: BenchmarkTaskMetrics = Field(default_factory=BenchmarkTaskMetrics)
    cost: BenchmarkCost = Field(default_factory=BenchmarkCost)

    def finalize(self) -> "BenchmarkRunEvidence":
        """Fill computed fields before persisting."""
        self.end_time = datetime.now(timezone.utc).isoformat()
        self.task_metrics.artifact_completeness_score = (
            self.artifacts.completeness_score()
        )
        elapsed = (
            datetime.fromisoformat(self.end_time)
            - datetime.fromisoformat(self.start_time)
        ).total_seconds()
        self.task_metrics.duration_seconds = round(elapsed, 2)
        return self


class CompactFailureBundle(BaseModel):
    """Lightweight failure summary extracted from a full BenchmarkRunEvidence.

    Designed to be small enough to embed in Slack messages, dashboards,
    and LLM judge prompts (target <200 tokens for the summary field).
    """
    task_id: str = Field(..., description="Which task failed")
    verdict: str = Field(..., description="pass/fail/blocked")
    failure_step: Optional[str] = Field(
        default=None, description="Which step in a multi-step task failed"
    )
    summary: str = Field(
        ..., description="Concise failure explanation (target <200 tokens)"
    )
    screenshot_paths: List[str] = Field(
        default_factory=list, description="Paths to relevant screenshots"
    )
    trace_path: Optional[str] = Field(
        default=None, description="Path to trace file"
    )
    log_excerpt: Optional[str] = Field(
        default=None, description="Truncated relevant log lines"
    )
    root_cause_candidates: List[str] = Field(
        default_factory=list, description="Potential root causes"
    )
    involved_files: List[str] = Field(
        default_factory=list, description="Source files likely related to failure"
    )
    duration_seconds: Optional[float] = Field(
        default=None, description="How long the run took"
    )
    confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Judge confidence 0-1"
    )

    @classmethod
    def from_evidence(cls, evidence: BenchmarkRunEvidence) -> "CompactFailureBundle":
        """Extract a compact failure bundle from a full evidence object."""
        # Build summary from verdict reason and blocking issue
        summary_parts = []
        if evidence.verdict.reason:
            summary_parts.append(evidence.verdict.reason)
        if evidence.verdict.blocking_issue:
            summary_parts.append(f"Blocking: {evidence.verdict.blocking_issue}")
        summary = " | ".join(summary_parts) if summary_parts else "No details available"

        # Pull root-cause candidates from blocking_issue if present
        root_causes: List[str] = []
        if evidence.verdict.blocking_issue:
            root_causes.append(evidence.verdict.blocking_issue)

        return cls(
            task_id=evidence.task_id,
            verdict=evidence.status.value,
            failure_step=None,
            summary=summary,
            screenshot_paths=evidence.artifacts.screenshots,
            trace_path=evidence.artifacts.trace_path,
            log_excerpt=None,
            root_cause_candidates=root_causes,
            involved_files=[],
            duration_seconds=evidence.task_metrics.duration_seconds or None,
            confidence=evidence.verdict.confidence or None,
        )
