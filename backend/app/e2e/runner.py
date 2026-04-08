"""
E2E Test Runner

Main orchestrator for end-to-end testing with:
- Device lifecycle management
- OrchestrationRunSession for inline LLM evaluation
- Progressive Disclosure for context loading
- Ground truth verification
- Result aggregation and reporting
- Parallel execution across multiple devices

Model Configuration (Industry Standard - January 2026):
- THINKING_MODEL (gpt-5.4): Orchestration, complex reasoning
- PRIMARY_MODEL (gpt-5-mini): Evaluation, verification (quality!)
- DISTILL_MODEL (gpt-5-nano): ONLY for MCP tools, extraction
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import E2EConfig, TestSuite, SMOKE_SUITE
from .device_manager import DeviceManager, DeviceStatus
from .verifier import E2EVerifier, VerificationResult

logger = logging.getLogger(__name__)

# Maximum parallel workers (one per device)
MAX_PARALLEL_WORKERS = 4


@dataclass
class E2ETestResult:
    """Result of a single E2E test"""
    task_name: str
    passed: bool
    duration_seconds: float
    actions: List[Dict] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    verification: Optional[VerificationResult] = None
    error: Optional[str] = None
    retries: int = 0
    steps_taken: int = 0
    token_usage: Optional[Dict[str, int]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_name": self.task_name,
            "passed": self.passed,
            "duration_seconds": self.duration_seconds,
            "actions_count": len(self.actions),
            "steps_taken": self.steps_taken,
            "token_usage": self.token_usage,
            "verification": self.verification.to_dict() if self.verification else None,
            "error": self.error,
            "retries": self.retries,
        }


@dataclass
class E2ESuiteResult:
    """Result of a full E2E test suite run"""
    suite_name: str
    total_tests: int
    passed_tests: int
    failed_tests: int
    success_rate: float
    duration_seconds: float
    test_results: List[E2ETestResult] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "suite_name": self.suite_name,
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "success_rate": f"{self.success_rate:.1%}",
            "duration_seconds": self.duration_seconds,
            "test_results": [r.to_dict() for r in self.test_results],
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class DeviceWorker:
    """Worker that runs tests on a single device."""

    def __init__(self, device_id: str, config: E2EConfig):
        self.device_id = device_id
        self.config = config
        self._mcp_client = None
        self._executor = None
        self._lock = asyncio.Lock()

    async def setup(self) -> bool:
        """Initialize MCP client and executor for this device."""
        try:
            from app.agents.device_testing.mobile_mcp_client import MobileMCPClient
            self._mcp_client = MobileMCPClient()
            await self._mcp_client.start()

            if self.config.use_agent_executor:
                from app.benchmarks.android_world.agent_executor import AgentExecutor
                self._executor = AgentExecutor(self._mcp_client, model=self.config.eval_model)
            else:
                from app.benchmarks.android_world.executor import AndroidWorldExecutor
                self._executor = AndroidWorldExecutor(self._mcp_client)

            logger.info(f"[Worker-{self.device_id}] Initialized")
            return True
        except Exception as e:
            logger.error(f"[Worker-{self.device_id}] Setup failed: {e}")
            return False

    async def teardown(self):
        """Cleanup resources."""
        if self._mcp_client:
            await self._mcp_client.stop()

    async def run_task(self, task_name: str) -> E2ETestResult:
        """Run a single task on this device."""
        async with self._lock:  # Ensure one task at a time per device
            return await self._execute_task(task_name)

    async def _execute_task(self, task_name: str) -> E2ETestResult:
        """Internal task execution."""
        start_time = time.time()
        result = E2ETestResult(task_name=task_name, passed=False, duration_seconds=0.0)

        from app.benchmarks.android_world.task_registry import AndroidWorldTaskRegistry
        registry = AndroidWorldTaskRegistry()
        task = registry.get_instantiated(task_name)

        if not task:
            result.error = f"Task not found: {task_name}"
            result.duration_seconds = time.time() - start_time
            return result

        try:
            exec_result = await self._executor.execute_task(
                task=task,
                device_id=self.device_id,
                take_screenshots=self.config.screenshots,
            )

            result.actions = exec_result.actions
            result.screenshots = exec_result.screenshots
            result.steps_taken = exec_result.steps_taken
            if exec_result.token_usage:
                result.token_usage = exec_result.token_usage.to_dict()

            # Verify with LLM judge
            if self.config.suite.use_llm_judge:
                verifier = E2EVerifier(device_id=self.device_id, model=self.config.eval_model)
                final_screenshot = result.screenshots[-1] if result.screenshots else None

                verification = await verifier.verify(
                    task_name=task_name,
                    expected_outcome={"description": task.description},
                    actual_actions=result.actions,
                    screenshot_b64=final_screenshot,
                    agent_output=getattr(exec_result, 'agent_output', None),
                )
                result.verification = verification
                result.passed = verification.passed
            else:
                result.passed = exec_result.status.value == "success"

        except Exception as e:
            logger.error(f"[Worker-{self.device_id}] Task {task_name} failed: {e}")
            result.error = str(e)

        result.duration_seconds = time.time() - start_time
        status = "✅ PASS" if result.passed else "❌ FAIL"
        logger.info(f"[Worker-{self.device_id}] {task_name}: {status} ({result.duration_seconds:.1f}s)")
        return result


class E2ETestRunner:
    """
    End-to-end test runner with full lifecycle management.

    Usage:
        runner = E2ETestRunner(E2EConfig.for_suite("smoke"))
        result = await runner.run()
        print(f"Success rate: {result.success_rate:.0%}")
    """

    def __init__(self, config: E2EConfig = None):
        self.config = config or E2EConfig()
        self.device_manager = DeviceManager(self.config.device)
        self._mcp_client = None
        self._executor = None
        self._disclosure_loader = None
    
    async def run(self) -> E2ESuiteResult:
        """Run the full E2E test suite"""
        suite = self.config.suite
        result = E2ESuiteResult(
            suite_name=suite.name,
            total_tests=len(suite.tasks),
            passed_tests=0,
            failed_tests=0,
            success_rate=0.0,
            duration_seconds=0.0,
            started_at=datetime.now().isoformat(),
        )
        
        start_time = time.time()
        logger.info(f"[E2E] Starting suite: {suite.name} ({len(suite.tasks)} tests)")
        
        try:
            # Phase 1: Setup
            device_status = await self._setup()
            if not device_status.ready:
                raise RuntimeError(f"Device not ready: {device_status.error}")
            
            # Phase 2: Execute tests
            for task_name in suite.tasks:
                test_result = await self._run_single_test(task_name)
                result.test_results.append(test_result)
                if test_result.passed:
                    result.passed_tests += 1
                else:
                    result.failed_tests += 1
                
                # Reset state between tests
                if suite.reset_between_tests:
                    await self._reset_state_for_task(task_name)
            
            # Phase 3: Finalize
            result.duration_seconds = time.time() - start_time
            result.success_rate = result.passed_tests / result.total_tests
            result.completed_at = datetime.now().isoformat()
            
        except Exception as e:
            logger.error(f"[E2E] Suite failed: {e}")
            result.failed_tests = result.total_tests
        finally:
            await self._teardown()
        
        self._print_summary(result)
        return result
    
    async def _setup(self) -> DeviceStatus:
        """Setup device and initialize clients"""
        logger.info("[E2E] Phase 1: Setup")
        
        # Setup device
        status = await self.device_manager.setup()
        
        # Initialize MCP client
        if status.ready:
            from app.agents.device_testing.mobile_mcp_client import MobileMCPClient
            self._mcp_client = MobileMCPClient()
            await self._mcp_client.start()

            # Initialize executor - choose between agent-based and scripted
            if self.config.use_agent_executor:
                from app.benchmarks.android_world.agent_executor import AgentExecutor
                self._executor = AgentExecutor(self._mcp_client, model=self.config.eval_model)
                logger.info("[E2E] Using AGENT-based executor (LLM-driven)")
            else:
                from app.benchmarks.android_world.executor import AndroidWorldExecutor
                self._executor = AndroidWorldExecutor(self._mcp_client)
                logger.info("[E2E] Using scripted executor")

            # Initialize Progressive Disclosure
            if self.config.progressive_disclosure:
                from app.agents.orchestration import ProgressiveDisclosureLoader
                self._disclosure_loader = ProgressiveDisclosureLoader()
                self._disclosure_loader.load_all_metadata()

        return status

    async def _run_single_test(self, task_name: str) -> E2ETestResult:
        """Run a single test with orchestration and verification"""
        logger.info(f"[E2E] Running test: {task_name}")
        start_time = time.time()

        result = E2ETestResult(
            task_name=task_name,
            passed=False,
            duration_seconds=0.0,
        )

        # Get task from registry (instantiated to fill in template parameters)
        from app.benchmarks.android_world.task_registry import AndroidWorldTaskRegistry
        registry = AndroidWorldTaskRegistry()
        task = registry.get_instantiated(task_name)

        if not task:
            result.error = f"Task not found: {task_name}"
            result.duration_seconds = time.time() - start_time
            return result

        # Load Progressive Disclosure context
        if self._disclosure_loader:
            context = self._disclosure_loader.get_context_for_task(
                task.description, level=2
            )
            if context.get("matched"):
                logger.debug(f"[E2E] Loaded skill context: {context.get('skill_name')}")

        # Execute with retry
        max_retries = self.config.suite.max_retries
        for attempt in range(max_retries):
            try:
                # Execute task
                exec_result = await self._executor.execute_task(
                    task=task,
                    device_id=self.config.device.device_id,
                    take_screenshots=self.config.screenshots,
                )

                result.actions = exec_result.actions
                result.screenshots = exec_result.screenshots
                result.retries = attempt
                result.steps_taken = exec_result.steps_taken
                if exec_result.token_usage:
                    result.token_usage = exec_result.token_usage.to_dict()

                # Verify result
                if self.config.suite.verify_state or self.config.suite.use_llm_judge:
                    verifier = E2EVerifier(
                        device_id=self.config.device.device_id,
                        model=self.config.eval_model
                    )

                    # Get final screenshot for verification
                    final_screenshot = None
                    if result.screenshots:
                        # Use last screenshot for verification
                        final_screenshot = result.screenshots[-1]
                    else:
                        # Take a fresh screenshot for verification
                        try:
                            screenshot_result = await self._mcp_client.take_screenshot(
                                self.config.device.device_id
                            )
                            if screenshot_result and "data" in screenshot_result:
                                final_screenshot = screenshot_result["data"]
                        except Exception as e:
                            logger.warning(f"[E2E] Failed to take verification screenshot: {e}")

                    verification = await verifier.verify(
                        task_name=task_name,
                        expected_outcome={
                            "description": task.description,
                            "state_checks": task.expected_state if hasattr(task, 'expected_state') else [],
                        },
                        actual_actions=result.actions,
                        screenshot_b64=final_screenshot,
                        agent_output=getattr(exec_result, 'agent_output', None),
                    )

                    result.verification = verification
                    result.passed = verification.passed
                else:
                    # No verification - check execution status
                    result.passed = exec_result.status.value == "success"

                if result.passed:
                    break

            except Exception as e:
                logger.warning(f"[E2E] Attempt {attempt + 1} failed: {e}")
                result.error = str(e)
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)

        result.duration_seconds = time.time() - start_time
        status = "✅ PASS" if result.passed else "❌ FAIL"
        logger.info(f"[E2E] {task_name}: {status} ({result.duration_seconds:.1f}s)")

        return result

    async def _reset_state_for_task(self, task_name: str):
        """Reset app state for next test"""
        # Map tasks to packages
        task_packages = {
            "ClockStopWatchRunning": "com.android.deskclock",
            "ContactsAddContact": "com.android.contacts",
            "CameraTakePhoto": "com.android.camera2",
            "MarkorCreateNote": "net.gsantner.markor",
        }
        package = task_packages.get(task_name)
        if package:
            await self.device_manager.reset_app_state(package)

    async def _teardown(self):
        """Cleanup resources"""
        logger.info("[E2E] Teardown")
        if self._mcp_client:
            await self._mcp_client.stop()
        await self.device_manager.teardown()

    def _print_summary(self, result: E2ESuiteResult):
        """Print test summary"""
        print("\n" + "=" * 60)
        print(f"E2E Test Suite: {result.suite_name}")
        print("=" * 60)
        print(f"Total Tests:  {result.total_tests}")
        print(f"Passed:       {result.passed_tests} ✅")
        print(f"Failed:       {result.failed_tests} ❌")
        print(f"Success Rate: {result.success_rate:.0%}")
        print(f"Duration:     {result.duration_seconds:.1f}s")
        print("-" * 60)
        for tr in result.test_results:
            status = "✅" if tr.passed else "❌"
            print(f"  {status} {tr.task_name} ({tr.duration_seconds:.1f}s)")
        print("=" * 60)


async def run_parallel_tests(
    suite: TestSuite,
    device_ids: List[str],
    config: E2EConfig,
) -> E2ESuiteResult:
    """
    Run tests in parallel across multiple devices.

    Args:
        suite: Test suite to run
        device_ids: List of device IDs to use (e.g., ["emulator-5556", "emulator-5560"])
        config: E2E configuration

    Returns:
        Aggregated suite result
    """
    start_time = time.time()
    tasks = list(suite.tasks)
    num_devices = min(len(device_ids), len(tasks), MAX_PARALLEL_WORKERS)

    logger.info(f"[PARALLEL] Running {len(tasks)} tests across {num_devices} devices")
    print(f"\n🚀 Parallel Execution: {len(tasks)} tests × {num_devices} devices")

    # Initialize workers
    workers = []
    for device_id in device_ids[:num_devices]:
        worker = DeviceWorker(device_id, config)
        if await worker.setup():
            workers.append(worker)

    if not workers:
        raise RuntimeError("No devices available for parallel execution")

    # Create task queue
    task_queue: asyncio.Queue[str] = asyncio.Queue()
    for task_name in tasks:
        await task_queue.put(task_name)

    # Results collection
    results: List[E2ETestResult] = []
    results_lock = asyncio.Lock()

    async def worker_loop(worker: DeviceWorker):
        """Worker coroutine that pulls tasks from queue."""
        while True:
            try:
                task_name = task_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            result = await worker.run_task(task_name)
            async with results_lock:
                results.append(result)
            task_queue.task_done()

    # Run workers in parallel
    await asyncio.gather(*[worker_loop(w) for w in workers])

    # Cleanup workers
    for worker in workers:
        await worker.teardown()

    # Aggregate results
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    duration = time.time() - start_time

    suite_result = E2ESuiteResult(
        suite_name=suite.name,
        total_tests=len(results),
        passed_tests=passed,
        failed_tests=failed,
        success_rate=passed / len(results) if results else 0.0,
        duration_seconds=duration,
        test_results=results,
        started_at=datetime.now().isoformat(),
        completed_at=datetime.now().isoformat(),
    )

    # Print summary
    print("\n" + "=" * 60)
    print(f"PARALLEL E2E Results: {suite.name}")
    print("=" * 60)
    print(f"Devices Used:   {num_devices}")
    print(f"Total Tests:    {len(results)}")
    print(f"Passed:         {passed} ✅")
    print(f"Failed:         {failed} ❌")
    print(f"Success Rate:   {suite_result.success_rate:.0%}")
    print(f"Total Duration: {duration:.1f}s")
    print(f"Speedup:        ~{len(workers)}x vs sequential")
    print("-" * 60)
    for tr in sorted(results, key=lambda x: x.task_name):
        status = "✅" if tr.passed else "❌"
        print(f"  {status} {tr.task_name} ({tr.duration_seconds:.1f}s)")
    print("=" * 60)

    return suite_result
