"""Golden bug definitions and evaluation models.

These models capture:
- Golden bug definitions (device profile, steps, expected outcome)
- Automatic check configuration (simple yes/no evaluation rules)
- Per-bug run results and overall evaluation metrics

They are intentionally lightweight and JSON-friendly so we can:
- Store golden bugs in backend/data/golden_bugs.json
- Return summaries and reports to the AI agent and DevMate inspector.
"""

from enum import Enum
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from pydantic import BaseModel

from .bug_reproduction_service import BugReportInput, BugReproductionResult


class GoldenBugOutcome(str, Enum):
    """Expected outcome for a golden bug in the target environment.

    REPRODUCED   -> We expect the bug to reproduce on this device/app build.
    NOT_REPRODUCED -> We expect the bug NOT to reproduce (fixed / not applicable).
    """

    REPRODUCED = "reproduced"
    NOT_REPRODUCED = "not_reproduced"


class GoldenBugAutoCheck(BaseModel):
    """Configuration for simple automatic yes/no checks.

    We avoid arbitrary scores and only use boolean checks driven by:
    - The BugReproductionResult.reproduction_successful flag.
    - Presence of key phrases in the AI analysis string.
    """

    model_config = {"extra": "forbid"}

    expectation: GoldenBugOutcome
    require_text_in_analysis: List[str] = []


class GoldenBugDefinition(BaseModel):
    """Golden bug definition used by the evaluation harness.

    Fields are chosen so a single JSON file (golden_bugs.json) can fully describe
    each golden bug without extra DB tables.
    """

    model_config = {"extra": "forbid"}

    bug_id: str
    name: str
    description: str
    bug_report: BugReportInput
    auto_check: GoldenBugAutoCheck


class GoldenBugAttemptResult(BaseModel):
    """Result of one attempt (run) for a golden bug.

    We allow up to 3 attempts per bug to reduce flakiness.
    """

    model_config = {"extra": "forbid"}

    attempt_index: int
    reproduction_successful: bool
    auto_check_passed: bool
    auto_check_reason: str
    bug_reproduction_result: BugReproductionResult
    screenshot_url: Optional[str] = None


class GoldenBugRunResult(BaseModel):
    """Aggregated results for a single golden bug across attempts.

    Includes both the **pre-device planning stage** result and the
    **on-device execution stage** result so we can debug failures.
    """

    model_config = {"extra": "forbid"}

    bug_id: str
    name: str
    expected_outcome: GoldenBugOutcome

    # Pre-device planning + boolean judge stage
    planning_passed: bool
    planning_reason: str

    # On-device execution attempts
    attempts: List[GoldenBugAttemptResult]
    passed: bool
    classification: str  # TP, FP, TN, FN
    created_at: str


class GoldenBugSummary(BaseModel):
    """Lightweight summary for listing golden bugs in the agent UI."""

    model_config = {"extra": "forbid"}

    bug_id: str
    name: str
    description: str
    expected_outcome: GoldenBugOutcome
    device_id: str
    severity: str
    tags: List[str] = []


class GoldenBugEvaluationMetrics(BaseModel):
    """Simple precision/recall/F1 metrics over all golden bugs."""

    model_config = {"extra": "forbid"}

    total_bugs: int
    bugs_passed: int
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float


class GoldenBugEvaluationReport(BaseModel):
    """Full evaluation report for a batch run of golden bugs."""

    model_config = {"extra": "forbid"}

    run_id: str
    created_at: str
    metrics: GoldenBugEvaluationMetrics
    runs: List[GoldenBugRunResult]


def now_iso_utc() -> str:
    """Helper for consistent timestamp formatting (UTC ISO-8601)."""

    return datetime.now(timezone.utc).isoformat()
