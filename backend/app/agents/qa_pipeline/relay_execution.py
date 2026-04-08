"""
Relay-based test execution — sends generated test cases through the agent relay
to the user's local Claude Code, which drives the emulator and returns results.

Architecture:
  Server (this code)
    → agent relay WebSocket
    → user's laptop (Claude Code + emulator)
    → results stream back

Each test case is sent as a run_web_flow or run_android_flow command.
The user's agent executes steps, captures screenshots, and returns pass/fail.
"""

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from .schemas import TestCase, TestSuiteResult

logger = logging.getLogger(__name__)

# Budget limits
MAX_TESTS_TO_EXECUTE = 15
RELAY_COMMAND_TIMEOUT = 120.0  # seconds per test case


def _prioritize_tests(test_cases: List[TestCase]) -> List[TestCase]:
    """Pick the most important tests to execute within budget."""
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    sorted_tests = sorted(
        test_cases,
        key=lambda tc: (priority_order.get(tc.priority, 9), tc.test_id),
    )
    return sorted_tests[:MAX_TESTS_TO_EXECUTE]


def _format_test_as_flow(tc: TestCase, app_url: Optional[str] = None) -> Dict[str, Any]:
    """Convert a TestCase into a flow description for the relay agent."""
    steps = []
    for step in tc.steps:
        steps.append({
            "step_number": step.step_number,
            "action": step.action,
            "expected_result": step.expected_result,
            "screen": getattr(step, "screen", ""),
        })

    return {
        "test_id": tc.test_id,
        "test_name": tc.name,
        "description": tc.description if hasattr(tc, "description") else "",
        "app_url": app_url or "",
        "steps": steps,
        "verify_each_step": True,
        "capture_screenshots": True,
    }


async def execute_via_relay(
    test_suite: TestSuiteResult,
    relay_session,
    app_url: Optional[str] = None,
    flow_type: str = "web",
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Execute test cases by sending them through the agent relay to the user's
    local Claude Code, which drives the emulator.

    Args:
        test_suite: Generated test cases to execute
        relay_session: A RelaySession connected to the user's machine
        app_url: URL of the app under test (for web flows)
        flow_type: "web" or "android"
    """
    tests_to_run = _prioritize_tests(test_suite.test_cases)
    total = len(tests_to_run)

    yield {
        "type": "stage_transition",
        "to_stage": "EXECUTION",
    }
    yield {
        "type": "stage_activity",
        "stage": "EXECUTION",
        "activity": "starting",
        "message": f"Executing {total} test cases via relay on user's device...",
    }

    results: List[Dict[str, Any]] = []
    passed = 0
    failed = 0

    command_name = "run_web_flow" if flow_type == "web" else "run_android_flow"

    for idx, tc in enumerate(tests_to_run):
        tc_start = time.time()

        yield {
            "type": "test_execution_start",
            "test_id": tc.test_id,
            "name": tc.name,
            "priority": tc.priority,
            "index": idx + 1,
            "total": total,
        }

        # Format test case as a flow and send through relay
        flow_payload = _format_test_as_flow(tc, app_url)

        try:
            response = await relay_session.send_command(
                command_name,
                timeout=RELAY_COMMAND_TIMEOUT,
                flow=flow_payload,
            )

            # Parse relay response
            if response.get("error"):
                test_result = _build_error_result(tc, response["error"], tc_start)
                failed += 1
            else:
                test_result = _parse_relay_response(tc, response, tc_start)
                if test_result["status"] == "pass":
                    passed += 1
                else:
                    failed += 1

        except Exception as e:
            logger.error(f"Relay execution failed for {tc.test_id}: {e}")
            test_result = _build_error_result(tc, str(e), tc_start)
            failed += 1

        results.append(test_result)

        # Yield per-step results if available
        for step_result in test_result.get("step_results", []):
            yield {
                "type": "test_step_result",
                "test_id": tc.test_id,
                "step_number": step_result.get("step_number", 0),
                "action": step_result.get("action", ""),
                "status": step_result.get("status", "unknown"),
                "actual_result": step_result.get("actual_result", ""),
                "has_screenshot": bool(step_result.get("screenshot")),
            }

        yield {
            "type": "test_execution_result",
            "test_id": tc.test_id,
            "name": tc.name,
            "status": test_result["status"],
            "priority": tc.priority,
            "steps_passed": sum(
                1 for s in test_result.get("step_results", []) if s.get("status") == "pass"
            ),
            "steps_total": test_result.get("steps_total", len(tc.steps)),
            "duration_ms": test_result.get("duration_ms", 0),
            "screenshots": len(test_result.get("screenshots", [])),
        }

        yield {
            "type": "stage_activity",
            "stage": "EXECUTION",
            "activity": "executing",
            "message": f"Test {idx + 1}/{total}: {tc.name} — {'PASS' if test_result['status'] == 'pass' else 'FAIL'}",
        }

    # Execution complete
    yield {
        "type": "stage_activity",
        "stage": "EXECUTION",
        "activity": "completed",
        "message": f"Executed {total} tests: {passed} passed, {failed} failed",
    }

    yield {
        "type": "execution_complete",
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / total, 4) if total > 0 else 0.0,
        "results": results,
    }


def _parse_relay_response(
    tc: TestCase, response: Dict[str, Any], start_time: float
) -> Dict[str, Any]:
    """Parse the relay agent's response into a normalized test result."""
    duration_ms = int((time.time() - start_time) * 1000)

    # The relay response can come in different shapes depending on
    # how the user's Claude Code formats results.
    result_data = response.get("result", response)

    # Try to extract structured step results
    step_results = []
    raw_steps = result_data.get("steps", result_data.get("step_results", []))
    for step in raw_steps:
        step_results.append({
            "step_number": step.get("step_number", step.get("step", 0)),
            "action": step.get("action", ""),
            "expected": step.get("expected_result", step.get("expected", "")),
            "status": step.get("status", "unknown"),
            "actual_result": step.get("actual_result", step.get("result", "")),
            "screenshot": step.get("screenshot", step.get("screenshot_data", None)),
            "error": step.get("error"),
        })

    # Determine overall status
    status = result_data.get("status", "unknown")
    if status not in ("pass", "fail", "error"):
        # Infer from step results
        if step_results:
            all_pass = all(s["status"] == "pass" for s in step_results)
            status = "pass" if all_pass else "fail"
        else:
            # No step detail — check for success indicators
            if result_data.get("passed") or result_data.get("success"):
                status = "pass"
            else:
                status = "fail"

    # Collect screenshots
    screenshots = [
        s["screenshot"] for s in step_results if s.get("screenshot")
    ]

    return {
        "test_id": tc.test_id,
        "name": tc.name,
        "priority": tc.priority,
        "category": tc.category,
        "status": status,
        "steps_executed": len(step_results),
        "steps_total": len(tc.steps),
        "step_results": step_results,
        "screenshots": screenshots,
        "duration_ms": duration_ms,
        "error": None,
        "relay_raw": result_data,
    }


def _build_error_result(
    tc: TestCase, error_msg: str, start_time: float
) -> Dict[str, Any]:
    """Build an error test result."""
    return {
        "test_id": tc.test_id,
        "name": tc.name,
        "priority": tc.priority,
        "category": tc.category,
        "status": "error",
        "steps_executed": 0,
        "steps_total": len(tc.steps),
        "step_results": [],
        "screenshots": [],
        "duration_ms": int((time.time() - start_time) * 1000),
        "error": error_msg,
    }
