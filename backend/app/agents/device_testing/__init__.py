"""
Device Testing Agent Module

Unified agent for all device testing operations:
- Device discovery
- Test execution
- Bug reproduction
- Autonomous exploration
- Manual device control
- Autonomous navigation (self-adaptive goal-driven navigation)

Note: Emulator launching is handled by the UI, not the agent.
"""

from .device_testing_agent import create_device_testing_agent

# Services
from .bug_reproduction_service import (
    UnifiedBugReproductionService,
    BugReproductionService,
    BugReportInput,
    BugEvidence,
    BugReproductionResult,
    TestScenarioInput,
    TestExecutionResult,
    ExecutionMode,
)
from .exploration_service import AutonomousExplorationService
from .mobile_mcp_client import MobileMCPClient
from .demo_walkthrough_service import (
    NarratedWalkthroughService,
    NarratedWalkthroughResult,
    NarrationSegment,
    TimedNarrationSegment,
)

# ActionSpan verification engine
from .action_span_models import (
    ActionSpan,
    ActionSpanStatus,
    ActionType,
    ActionSpanManifest,
    StartSpanRequest,
    StartSpanResponse,
    ScoreSpanRequest,
    ScoreSpanResponse,
    ListSpansResponse,
)
from .action_span_service import ActionSpanService, action_span_service

# Golden bug evaluation
from .golden_bug_service import GoldenBugService
from .golden_bug_models import (
    GoldenBugOutcome,
    GoldenBugAutoCheck,
    GoldenBugDefinition,
    GoldenBugAttemptResult,
    GoldenBugRunResult,
    GoldenBugSummary,
    GoldenBugEvaluationMetrics,
    GoldenBugEvaluationReport,
)


__all__ = [
    "create_device_testing_agent",
    "UnifiedBugReproductionService",
    "BugReproductionService",
    "BugReportInput",
    "BugEvidence",
    "BugReproductionResult",
    "TestScenarioInput",
    "TestExecutionResult",
    "ExecutionMode",
    "AutonomousExplorationService",
    "MobileMCPClient",
    "NarratedWalkthroughService",
    "NarratedWalkthroughResult",
    "NarrationSegment",
    "TimedNarrationSegment",
    # ActionSpan verification engine
    "ActionSpan",
    "ActionSpanStatus",
    "ActionType",
    "ActionSpanManifest",
    "StartSpanRequest",
    "StartSpanResponse",
    "ScoreSpanRequest",
    "ScoreSpanResponse",
    "ListSpansResponse",
    "ActionSpanService",
    "action_span_service",
    # Golden bug evaluation
    "GoldenBugService",
    "GoldenBugOutcome",
    "GoldenBugAutoCheck",
    "GoldenBugDefinition",
    "GoldenBugAttemptResult",
    "GoldenBugRunResult",
    "GoldenBugSummary",
    "GoldenBugEvaluationMetrics",
    "GoldenBugEvaluationReport",
]

