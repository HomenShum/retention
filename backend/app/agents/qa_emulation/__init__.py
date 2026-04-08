"""
QA Emulation Module - Parallel Subagent Bug Reproduction System

Provides:
- QAEmulationService: Code-managed workflow orchestrator
- create_qa_emulation_agent: Agent factory with prompt variants (v11/v12)
- Subagents: Bug Detection, Anomaly Detection, Verdict Assembly
- Structured models: QAReproVerdict, AnomalyResult, BuildEvidence, RunTelemetry

Architecture:
- Parallel extraction: asyncio.gather (prd_parser pattern)
- Deterministic workflow: code-managed build sequence (orchestration pattern)
- Structured output: output_type=QAReproVerdict (SDK enforcement)
- Model tiering: gpt-5.4 (reasoning) + gpt-5-mini (vision)
- Configurable reasoning: QAEmulationConfig.reasoning_effort (none/low/medium/high/xhigh)
- Cost telemetry: run_emulation() returns tuple[QAReproVerdict, RunTelemetry]
"""

from .qa_emulation_service import QAEmulationService
from .qa_emulation_agent import create_qa_emulation_agent
from .models.verdict_models import (
    QAReproVerdict,
    AnomalyResult,
    BuildEvidence,
    EvidenceItem,
    QAEmulationConfig,
    VerdictType,
    AnomalyCategory,
    WorkflowPhase,
    RunTelemetry,
    MODEL_PRICING,
)
from .subagents import (
    create_bug_detection_agent,
    create_anomaly_detection_agent,
    create_verdict_assembly_agent,
)

__all__ = [
    "QAEmulationService",
    "create_qa_emulation_agent",
    "QAReproVerdict",
    "AnomalyResult",
    "BuildEvidence",
    "EvidenceItem",
    "QAEmulationConfig",
    "VerdictType",
    "AnomalyCategory",
    "WorkflowPhase",
    "RunTelemetry",
    "MODEL_PRICING",
    "create_bug_detection_agent",
    "create_anomaly_detection_agent",
    "create_verdict_assembly_agent",
]

