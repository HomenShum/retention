"""ActionSpan data models.

ActionSpan captures a bounded state transition with time, context, evidence,
verdict, and next-action metadata — the atomic unit of verified agent work.

Every valuable workflow is a sequence of state transitions over time.
ActionSpan makes each transition observable, judgeable, replayable, and
optimizable — whether the environment is a browser, emulator, API, or
physical device.

Architecture:
  - ActionSpan: one verified state transition per action
  - ActionSpanStatus: lifecycle states
  - ActionSpanManifest: session-level evidence roll-up
  - ActionSpanRequest/Response: API I/O shapes
"""

from enum import Enum
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ActionSpanStatus(str, Enum):
    """Lifecycle of a single ActionSpan."""
    CAPTURING  = "capturing"   # Recording in progress
    PROCESSING = "processing"  # Extracting frames / computing score
    SCORED     = "scored"      # Score + clip path available
    FAILED     = "failed"      # Could not capture or score


class ActionType(str, Enum):
    """Coarse category for the agent action being verified."""
    TAP         = "tap"
    SWIPE       = "swipe"
    TYPE        = "type"
    NAVIGATE    = "navigate"
    LAUNCH      = "launch"
    ASSERT      = "assert"
    SCREENSHOT  = "screenshot"
    API_CALL    = "api_call"
    CLI_COMMAND = "cli_command"
    OTHER       = "other"


class ActorType(str, Enum):
    """Who performed the action."""
    HUMAN = "human"
    AGENT = "agent"
    MIXED = "mixed"


class EnvironmentType(str, Enum):
    """Surface where the action was executed."""
    BROWSER    = "browser"
    EMULATOR   = "emulator"
    DESKTOP    = "desktop"
    BACKEND    = "backend"
    API        = "api"
    CLI        = "cli"
    CRM        = "crm"
    DEVICE     = "device"
    OTHER      = "other"


class EscalationStatus(str, Enum):
    """Whether this span requires human attention or model-tier escalation."""
    NONE              = "none"
    LOW_CONFIDENCE    = "low-confidence"
    SECURITY_ISSUE    = "security-issue"
    POLICY_VIOLATION  = "policy-violation"
    NEEDS_REVIEW      = "needs-review"
    # ROP tier escalation — replay model couldn't handle the step
    MISSING_TOOL      = "missing-tool"
    CHECKPOINT_FAILURE = "checkpoint-failure"
    UNEXPECTED_BRANCH = "unexpected-branch"
    CONTRACT_DRIFT    = "contract-drift"
    TIER_ESCALATION   = "tier-escalation"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class SpanJudgeResult(BaseModel):
    """Structured verdict from deterministic checks or LLM-as-judge."""
    passed: bool = False
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="Semantic confidence in verdict")
    reasoning: str = Field("", description="Why the judge reached this verdict")
    judge_type: str = Field("composite_score", description="deterministic | llm | composite_score")


class SpanCost(BaseModel):
    """Token and compute cost attributed to this single span."""
    input_tokens: int = 0
    output_tokens: int = 0
    token_cost_usd: float = 0.0
    compute_seconds: float = 0.0


class SpanToolCall(BaseModel):
    """Record of a tool invocation during this span."""
    tool_name: str
    input_params: Dict[str, Any] = Field(default_factory=dict)
    output_summary: str = ""
    status: str = "success"  # success | error | timeout
    duration_ms: Optional[int] = None


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------

class ActionSpan(BaseModel):
    """A single verified state transition — the atomic unit of agent work.

    Each span records: who acted, where, what changed, at what cost,
    with what confidence, and how to replay it.
    """

    model_config = {"extra": "allow"}

    span_id: str                              # UUID for this span
    session_id: str                           # Parent test session
    action_type: ActionType = ActionType.OTHER
    action_description: str = ""             # Human-readable label, e.g. "tap Sign In"

    # Identity & context (canonical spec fields)
    actor: ActorType = ActorType.AGENT
    environment: EnvironmentType = EnvironmentType.EMULATOR
    inputs: Dict[str, Any] = Field(default_factory=dict, description="Structured action inputs (coords, text, params)")

    # Timing
    started_at: str                           # ISO-8601 UTC
    ended_at: Optional[str] = None           # Set when scoring completes
    duration_ms: Optional[int] = None

    # Observed state (before / after)
    before_screenshot: Optional[str] = None  # Path to pre-action frame
    after_screenshot: Optional[str] = None   # Path to post-action frame
    before_state: Optional[Dict[str, Any]] = Field(default=None, description="Structured pre-state (UI tree, element dump)")
    after_state: Optional[Dict[str, Any]] = Field(default=None, description="Structured post-state (UI tree, element dump)")

    # Evidence artifacts
    clip_path: Optional[str] = None          # Relative path to .mp4 clip
    frame_count: int = 0
    logs: Optional[str] = Field(default=None, description="Per-span log output")
    trace_path: Optional[str] = Field(default=None, description="Per-span trace file")

    # Tool calls during this span
    tool_calls: List[SpanToolCall] = Field(default_factory=list)

    # Success criteria & judge
    success_criteria: Optional[str] = Field(default=None, description="What 'pass' means for this action")
    judge_result: SpanJudgeResult = Field(default_factory=SpanJudgeResult)

    # Scoring (visual verification layer)
    status: ActionSpanStatus = ActionSpanStatus.CAPTURING
    visual_change_score: Optional[float] = None   # 0.0 = no change, 1.0 = full change
    stability_score: Optional[float] = None       # 1.0 = perfectly stable, 0.0 = flickering
    composite_score: Optional[float] = None       # Weighted combination
    score_rationale: str = ""                     # Short human-readable explanation

    # Pass / fail verdict (legacy — prefer judge_result for new code)
    verified: Optional[bool] = None              # True if composite_score >= threshold
    error: Optional[str] = None                  # Set on FAILED status

    # Replay & evolution
    replay_path: Optional[str] = Field(default=None, description="Path/recipe to reproduce this action")

    # Cost attribution
    cost: SpanCost = Field(default_factory=SpanCost)

    # Escalation
    escalation: EscalationStatus = EscalationStatus.NONE

    # ROP tier-aware replay fields (populated during ROP replay)
    executing_model: Optional[str] = None
    executing_tier: Optional[str] = None       # "frontier" | "primary" | "replay"
    rop_id: Optional[str] = None
    escalated_from_model: Optional[str] = None
    escalated_from_tier: Optional[str] = None


class ActionSpanManifest(BaseModel):
    """Session-level evidence roll-up across all ActionSpans."""

    model_config = {"extra": "forbid"}

    session_id: str
    created_at: str
    updated_at: str

    total_spans: int = 0
    scored_spans: int = 0
    verified_spans: int = 0
    failed_spans: int = 0
    pass_rate: float = 0.0          # verified / scored, or 0 if none scored

    average_composite_score: float = 0.0
    spans: List[ActionSpan] = []

    # Aggregate metadata
    tags: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# API I/O shapes
# ---------------------------------------------------------------------------

class StartSpanRequest(BaseModel):
    """Request body for POST /action-spans/start."""

    session_id: str
    action_type: ActionType = ActionType.OTHER
    action_description: str = ""
    device_id: Optional[str] = None          # ADB device identifier
    score_threshold: float = 0.5             # Minimum composite_score to pass


class StartSpanResponse(BaseModel):
    """Response from POST /action-spans/start."""

    span_id: str
    session_id: str
    status: ActionSpanStatus
    started_at: str
    message: str = ""


class ScoreSpanRequest(BaseModel):
    """Request body for POST /action-spans/{span_id}/score."""

    span_id: str
    clip_path: Optional[str] = None           # Override clip path if pre-recorded
    score_threshold: float = 0.5


class ScoreSpanResponse(BaseModel):
    """Full ActionSpan returned after scoring."""

    span: ActionSpan
    manifest_updated: bool = False


class ListSpansResponse(BaseModel):
    """Response from GET /action-spans?session_id=..."""

    session_id: str
    spans: List[ActionSpan]
    manifest: Optional[ActionSpanManifest] = None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def now_iso_utc() -> str:
    """Consistent UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()

