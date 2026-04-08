"""
End-to-End Test Runner

Comprehensive E2E testing with:
- Device lifecycle management (emulator setup/teardown)
- State reset between tests
- OrchestrationRunSession for inline LLM evaluation
- Progressive Disclosure for context loading
- Ground truth verification
- Result aggregation and reporting

Model Configuration (Industry Standard - January 2026):
- THINKING_MODEL (gpt-5.4): Orchestration, complex reasoning
- PRIMARY_MODEL (gpt-5-mini): Evaluation, verification
- DISTILL_MODEL (gpt-5-nano): ONLY for MCP tools, extraction
"""

from .runner import E2ETestRunner, E2ETestResult
from .config import E2EConfig, TestSuite, DeviceConfig
from .device_manager import DeviceManager
from .verifier import E2EVerifier

__all__ = [
    "E2ETestRunner",
    "E2ETestResult",
    "E2EConfig",
    "TestSuite",
    "DeviceConfig",
    "DeviceManager",
    "E2EVerifier",
]

