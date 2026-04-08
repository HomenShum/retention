"""
QA Emulation Sub-agents - Parallel Specialist Pattern

Three specialist agents that run in parallel during bug reproduction:
- Bug Detection: Classifies whether the expected bug was reproduced
- Anomaly Detection: Monitors for unexpected issues (BLOCKED_NEW_BUG)
- Verdict Assembly: Synthesizes final structured verdict

Architecture follows:
- OAVR pattern from device_testing/subagents/
- Parallel execution pattern from prd_parser/subagents/
"""

from .bug_detection_agent import create_bug_detection_agent
from .anomaly_detection_agent import create_anomaly_detection_agent
from .verdict_assembly_agent import create_verdict_assembly_agent

__all__ = [
    "create_bug_detection_agent",
    "create_anomaly_detection_agent",
    "create_verdict_assembly_agent",
]

