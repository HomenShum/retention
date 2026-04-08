"""
Unified Bug Reproduction & Test Execution Module

Provides unified capabilities for:
1. Test scenario execution (predefined tests)
2. Bug reproduction (manual bug reports)

Note: The agent has been merged into device_testing agent.
"""

from .bug_reproduction_service import (
    UnifiedBugReproductionService,
    BugReproductionService,  # Backward compatibility alias
    BugReportInput,
    BugEvidence,
    BugReproductionResult,
    TestScenarioInput,
    TestExecutionResult,
    ExecutionMode,
)

__all__ = [
    "UnifiedBugReproductionService",
    "BugReproductionService",
    "BugReportInput",
    "BugEvidence",
    "BugReproductionResult",
    "TestScenarioInput",
    "TestExecutionResult",
    "ExecutionMode",
]

