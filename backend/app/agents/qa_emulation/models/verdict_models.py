"""
Pydantic Models for QA Emulation System

Defines structured schemas for verdicts, anomalies, build evidence,
and workflow configuration. Used as output_type on verdict agents
for machine-checkable results.

Reference: tmp/Untitled.txt architecture mapping
"""

from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime


class VerdictType(str, Enum):
    """Final verdict categories for bug reproduction"""
    REPRODUCIBLE = "REPRODUCIBLE"
    NOT_REPRODUCIBLE = "NOT_REPRODUCIBLE"
    BLOCKED_NEW_BUG = "BLOCKED_NEW_BUG"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class AnomalyCategory(str, Enum):
    """5-class taxonomy for anomaly detection + BLOCKED_NEW_BUG"""
    CRASH = "CRASH"
    VISUAL_REGRESSION = "VISUAL_REGRESSION"
    UI_REGRESSION = "UI_REGRESSION"
    STATE_ANOMALY = "STATE_ANOMALY"
    PERFORMANCE = "PERFORMANCE"
    NO_ISSUE = "NO_ISSUE"


class WorkflowPhase(str, Enum):
    """Deterministic workflow phases for build-sequence testing"""
    LEASE_DEVICE = "LEASE_DEVICE"
    LOGIN = "LOGIN"
    LOAD_BUILD_OG = "LOAD_BUILD_OG"
    REPRO_ON_OG = "REPRO_ON_OG"
    LOAD_BUILD_RB1 = "LOAD_BUILD_RB1"
    REPRO_ON_RB1 = "REPRO_ON_RB1"
    LOAD_BUILD_RB2 = "LOAD_BUILD_RB2"
    REPRO_ON_RB2 = "REPRO_ON_RB2"
    LOAD_BUILD_RB3 = "LOAD_BUILD_RB3"
    REPRO_ON_RB3 = "REPRO_ON_RB3"
    GATHER_EVIDENCE = "GATHER_EVIDENCE"
    ASSEMBLE_VERDICT = "ASSEMBLE_VERDICT"


class BuildId(str, Enum):
    """Build identifiers for the reproduction sequence"""
    OG = "OG"
    RB1 = "RB1"
    RB2 = "RB2"
    RB3 = "RB3"


class EvidenceItem(BaseModel):
    """A single piece of evidence collected during testing"""
    id: str = Field(..., description="Unique evidence identifier (EV-XXX)")
    build_id: str = Field(..., description="Which build this evidence is from")
    evidence_type: Literal["screenshot", "log", "video", "element_dump", "network_trace"] = Field(
        ..., description="Type of evidence"
    )
    description: str = Field(..., description="What this evidence shows")
    file_path: Optional[str] = Field(default=None, description="Path to evidence file")
    timestamp: Optional[str] = Field(default=None, description="When evidence was captured")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class BuildEvidence(BaseModel):
    """Evidence collected for a single build during reproduction"""
    build_id: str = Field(..., description="Build identifier (OG, RB1, RB2, RB3)")
    reproduced: Optional[bool] = Field(default=None, description="Whether bug was reproduced on this build")
    evidence_items: List[EvidenceItem] = Field(default_factory=list)
    repro_steps_taken: List[str] = Field(default_factory=list, description="Steps actually performed")
    anomalies_found: List[str] = Field(default_factory=list, description="Anomaly IDs found during this build")
    notes: str = Field(default="", description="Agent notes about this build's test run")


class AnomalyResult(BaseModel):
    """Structured anomaly detection result"""
    category: AnomalyCategory = Field(..., description="Anomaly classification")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Detection confidence")
    rationale: str = Field(..., description="Why this was classified as such")
    evidence_ids: List[str] = Field(default_factory=list, description="Supporting evidence IDs")
    is_expected_bug: bool = Field(default=False, description="Is this the bug we're looking for?")
    is_new_bug: bool = Field(default=False, description="Is this an unexpected new bug?")
    next_action: str = Field(default="continue", description="Suggested next action")


class QAReproVerdict(BaseModel):
    """
    Structured final verdict for bug reproduction.

    Used as output_type on the Verdict Assembly agent for
    machine-checkable, guardrail-validated results.
    """
    verdict: VerdictType = Field(..., description="Final reproduction verdict")
    bug_type: Optional[str] = Field(default=None, description="Bug category if reproduced")
    repro_steps: List[str] = Field(default_factory=list, description="Actual reproduction steps")
    evidence_ids: List[str] = Field(default_factory=list, description="All evidence IDs supporting verdict")
    compared_builds: List[str] = Field(default_factory=list, description="Builds compared (OG, RB1, etc.)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Verdict confidence")
    rationale: str = Field(..., description="Detailed reasoning for verdict")
    anomalies: List[AnomalyResult] = Field(default_factory=list, description="All anomalies detected")
    build_results: List[BuildEvidence] = Field(default_factory=list, description="Per-build evidence")


class RunTelemetry(BaseModel):
    """Token usage and cost telemetry for a QA emulation run.

    Aggregates usage from all Runner.run calls (subagents + verdict).
    Pricing based on March 2026 official rates:
      GPT-5.4: $2.50/1M input, $15.00/1M output
      GPT-5-mini: $0.25/1M input, $1.00/1M output
    """
    total_requests: int = Field(default=0, description="Total LLM API requests")
    total_input_tokens: int = Field(default=0, description="Total input tokens")
    total_output_tokens: int = Field(default=0, description="Total output tokens")
    total_tokens: int = Field(default=0, description="Sum of input + output tokens")
    reasoning_tokens: int = Field(default=0, description="Reasoning tokens (GPT-5.4)")
    estimated_cost_usd: float = Field(default=0.0, description="Estimated cost in USD")
    model_breakdown: Dict[str, Any] = Field(
        default_factory=dict,
        description="Per-model token breakdown {model: {input, output, cost}}"
    )


# March 2026 pricing per 1M tokens
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "gpt-5.4": {"input": 2.50, "output": 15.00},
    "gpt-5": {"input": 2.00, "output": 8.00},
    "gpt-5-mini": {"input": 0.25, "output": 1.00},
}


class QAEmulationConfig(BaseModel):
    """Configuration for a QA emulation run"""
    prompt_version: Literal["v11_compact", "v12", "v12_compaction"] = Field(
        default="v12", description="Prompt/agent variant to use"
    )
    task_id: str = Field(..., description="Task identifier to reproduce")
    seed_bug: Optional[str] = Field(default=None, description="Expected bug to find (for eval)")
    builds: List[str] = Field(default=["OG", "RB1", "RB2", "RB3"], description="Builds to test")
    device_id: Optional[str] = Field(default=None, description="Target device ID")
    max_retries: int = Field(default=3, description="Max retries per phase")
    parallel_extraction: bool = Field(default=True, description="Run bug/anomaly detection in parallel")
    enable_compaction: bool = Field(default=False, description="Enable session compaction")
    enable_hitl: bool = Field(default=False, description="Enable human-in-the-loop for risky actions")
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh"] = Field(
        default="high",
        description="Reasoning effort for GPT-5.4 agents (none/low/medium/high/xhigh). Default: high"
    )

