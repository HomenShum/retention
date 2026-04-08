"""
E2E Test Configuration

Defines test suites, device configurations, and runtime options.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from pathlib import Path


class TestSuiteType(str, Enum):
    SMOKE = "smoke"
    REGRESSION = "regression"
    DEVICE_TESTING = "device_testing"
    TEST_GENERATION = "test_generation"
    CUSTOM = "custom"


@dataclass
class DeviceConfig:
    """Device configuration for E2E tests"""
    device_id: str = "emulator-5554"
    platform: str = "android"
    os_version: str = "14"
    model: str = "Pixel 6"
    resolution: str = "1080x2400"
    auto_launch: bool = True
    avd_name: Optional[str] = None
    headless: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "platform": self.platform,
            "os_version": self.os_version,
            "model": self.model,
            "resolution": self.resolution,
        }


@dataclass
class TestSuite:
    """Test suite configuration"""
    name: str
    type: TestSuiteType
    tasks: List[str]
    description: str = ""
    timeout_per_task: int = 120
    max_retries: int = 3
    verify_state: bool = True
    use_llm_judge: bool = True
    reset_between_tests: bool = True


# Pre-defined test suites
SMOKE_SUITE = TestSuite(
    name="smoke",
    type=TestSuiteType.SMOKE,
    description="Quick smoke tests for basic functionality",
    tasks=[
        "ClockStopWatchRunning",
        "OpenAppTaskEval",
        "SystemBluetoothTurnOn",
    ],
    timeout_per_task=60,
    max_retries=2,
)

REGRESSION_SUITE = TestSuite(
    name="regression",
    type=TestSuiteType.REGRESSION,
    description="Full regression test suite",
    tasks=[
        "ClockStopWatchRunning",
        "CameraTakePhoto",
        "ContactsAddContact",
        "MarkorCreateNote",
        "SystemBluetoothTurnOn",
        "SystemWifiTurnOff",
    ],
    timeout_per_task=120,
    max_retries=3,
)

DEVICE_TESTING_SUITE = TestSuite(
    name="device_testing",
    type=TestSuiteType.DEVICE_TESTING,
    description="Device interaction tests",
    tasks=[
        "CameraTakePhoto",
        "ContactsAddContact",
        "MarkorCreateNote",
    ],
    timeout_per_task=180,
)


@dataclass
class E2EConfig:
    """Main E2E test configuration"""
    suite: TestSuite = field(default_factory=lambda: SMOKE_SUITE)
    device: DeviceConfig = field(default_factory=DeviceConfig)
    parallel_devices: int = 1
    output_dir: Path = field(default_factory=lambda: Path("e2e_results"))
    screenshots: bool = True
    trace_actions: bool = True
    progressive_disclosure: bool = True
    inline_llm_eval: bool = True
    verbose: bool = False

    # Agent-based execution (uses LLM to drive device instead of scripted actions)
    use_agent_executor: bool = True  # Default to agent-based execution

    # Model configuration (Industry Standard)
    thinking_model: str = "gpt-5.4"
    eval_model: str = "gpt-5.4-mini"  # Gates/eval — mini is near-flagship on SWE-Bench
    distill_model: str = "gpt-5.4-mini"  # MCP tools (gpt-5.4-nano fallback)
    
    @classmethod
    def for_suite(cls, suite_name: str) -> "E2EConfig":
        """Create config for a predefined suite"""
        suites = {
            "smoke": SMOKE_SUITE,
            "regression": REGRESSION_SUITE,
            "device_testing": DEVICE_TESTING_SUITE,
        }
        suite = suites.get(suite_name, SMOKE_SUITE)
        return cls(suite=suite)
    
    @classmethod
    def from_yaml(cls, path: Path) -> "E2EConfig":
        """Load config from YAML file"""
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f)
        # Parse and return config
        return cls()  # Simplified - extend as needed

