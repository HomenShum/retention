"""
Retained Operation Pattern (ROP) data models.

Model-to-workflow distillation: expensive frontier models explore and solve hard
workflows once, TA captures those as ROPs, then cheaper models (Haiku, nano)
replay them safely with checkpoint validation and automatic escalation.

One-line framing:
  retention.sh distills expensive exploratory workflows into retained, audited
  operation patterns that cheaper models can replay safely.

Lifecycle:
  DRAFT → VALIDATING → PROMOTED → OPERATING → ESCALATED → RETIRED
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ModelTier(str, Enum):
    """Model capability/cost bands for ROP distillation."""
    FRONTIER = "frontier"   # Opus, GPT-5.4 — discovery, deep research, novel exploration
    PRIMARY = "primary"     # Sonnet, GPT-5.4-mini — standard operations
    REPLAY = "replay"       # Haiku, GPT-5.4-nano — constrained replay ONLY


class ROPStatus(str, Enum):
    """Lifecycle states for a Retained Operation Pattern."""
    DRAFT = "draft"             # Captured from frontier, not yet validated
    VALIDATING = "validating"   # Replay validation in progress
    PROMOTED = "promoted"       # Validated; cheap model can replay faithfully
    OPERATING = "operating"     # In active production use
    ESCALATED = "escalated"     # Drift detected, needs frontier re-solve
    RETIRED = "retired"         # App changes invalidated the pattern


class EscalationReason(str, Enum):
    """Why a replay had to escalate to a stronger model."""
    MISSING_TOOL = "missing_tool"
    CHECKPOINT_FAILURE = "checkpoint_failure"
    UNEXPECTED_BRANCH = "unexpected_branch"
    CONTRACT_DRIFT = "contract_drift"
    CONFIDENCE_BELOW_THRESHOLD = "confidence_below_threshold"
    APP_FINGERPRINT_CHANGED = "app_fingerprint_changed"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class ROPCheckpoint(BaseModel):
    """Contract point the replay model must satisfy at a trajectory step."""
    step_index: int
    screen_fingerprint: str = ""
    required_elements: List[str] = Field(default_factory=list)
    expected_action_type: str = ""
    min_confidence: float = 0.7
    timeout_ms: int = 5000


class ReplayPolicy(BaseModel):
    """How the replay engine should behave for this ROP."""
    mode: str = "prefix_match_with_checkpoints"
    min_confidence: float = 0.6
    max_drift_score: float = 0.4       # matches MAX_DRIFT_SCORE_BEFORE_FALLBACK
    max_consecutive_drifts: int = 3    # matches MAX_CONSECUTIVE_DRIFTS
    escalation_triggers: List[EscalationReason] = Field(
        default_factory=lambda: list(EscalationReason)
    )
    checkpoint_interval: int = 1       # validate every N steps


class ROPCostMetrics(BaseModel):
    """Discovery vs replay cost tracking."""
    # Discovery (frontier model)
    discovery_tokens: int = 0
    discovery_cost_usd: float = 0.0
    discovery_time_s: float = 0.0
    # Replay (cheap model)
    replay_tokens: int = 0
    replay_cost_usd: float = 0.0
    replay_time_s: float = 0.0
    # Computed
    savings_pct: float = 0.0           # 1 - (replay_cost / discovery_cost)
    cumulative_savings_usd: float = 0.0


class ROPValidationAttempt(BaseModel):
    """Record of one replay validation attempt."""
    model: str
    tier: str
    success: bool
    drift_score: float = 0.0
    checkpoints_passed: int = 0
    checkpoints_total: int = 0
    timestamp: str = ""


# ---------------------------------------------------------------------------
# Core model
# ---------------------------------------------------------------------------

class RetainedOperationPattern(BaseModel):
    """A retained, audited operation pattern that cheaper models can replay safely.

    Captures:
    - What the frontier model discovered (origin)
    - How a cheap model should replay it (policy + checkpoints)
    - How well replays have been performing (metrics)
    """

    model_config = {"extra": "allow"}

    # Identity
    rop_id: str
    app_key: str                    # from app_fingerprint()
    app_url: str = ""
    workflow_id: str = ""
    workflow_name: str = ""

    # Provenance — which model discovered this pattern
    origin_model: str
    origin_tier: ModelTier
    origin_trajectory_id: str       # links to TrajectoryLog
    origin_run_id: str = ""

    # Replay assignment — cheapest model verified to replay
    replay_model: str = ""
    replay_tier: ModelTier = ModelTier.REPLAY
    replay_policy: ReplayPolicy = Field(default_factory=ReplayPolicy)

    # Checkpoints — contract points for replay validation
    checkpoints: List[ROPCheckpoint] = Field(default_factory=list)

    # Fingerprinting — for invalidation via delta crawl
    crawl_fingerprint: str = ""
    screen_fingerprints: Dict[str, str] = Field(default_factory=dict)

    # Step summary
    step_count: int = 0
    steps_summary: List[str] = Field(default_factory=list)

    # Lifecycle
    status: ROPStatus = ROPStatus.DRAFT
    created_at: str = ""
    validated_at: str = ""
    promoted_at: str = ""
    last_replayed_at: str = ""
    retired_at: str = ""

    # Metrics
    cost_metrics: ROPCostMetrics = Field(default_factory=ROPCostMetrics)
    replay_count: int = 0
    replay_success_count: int = 0
    replay_failure_count: int = 0
    escalation_count: int = 0
    last_escalation_reason: Optional[EscalationReason] = None

    # Validation history
    validation_attempts: List[ROPValidationAttempt] = Field(default_factory=list)
