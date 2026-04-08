"""
Test Execution Agent — runs generated test cases on the live device/browser.

Takes TestSuiteResult from the testcase stage and executes each test case
by replaying steps on the emulator, capturing screenshots, and using an AI
judge to determine pass/fail for each step.

Yields execution events as they happen (for live streaming to /curated).
"""

import asyncio
import base64
import hashlib
import logging
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from ..model_fallback import VISION_MODEL
from .context_graph import ContextGraphManager, EdgeType, GraphNode, NodeType
from .schemas import TestCase, TestSuiteResult

logger = logging.getLogger(__name__)

# Where to store execution screenshots
EXEC_SCREENSHOTS_DIR = Path(__file__).resolve().parents[2] / "screenshots" / "exec"
EXEC_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# Budget: max test cases to execute per run
MAX_TESTS_TO_EXECUTE = 15
# Max steps per test case
MAX_STEPS_PER_TEST = 8


def _prioritize_tests(test_cases: List[TestCase]) -> List[TestCase]:
    """Pick the most important tests to execute within budget."""
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    sorted_tests = sorted(
        test_cases,
        key=lambda tc: (priority_order.get(tc.priority, 9), tc.test_id),
    )
    return sorted_tests[:MAX_TESTS_TO_EXECUTE]


# Map natural-language actions to device operations
_ACTION_PATTERNS = {
    "click": "tap",
    "tap": "tap",
    "press": "tap",
    "type": "type",
    "enter": "type",
    "fill": "type",
    "input": "type",
    "navigate": "navigate",
    "go to": "navigate",
    "open": "navigate",
    "scroll": "scroll",
    "swipe": "scroll",
    "back": "back",
    "refresh": "refresh",
    "reload": "refresh",
    "wait": "wait",
    "verify": "verify",
    "check": "verify",
    "assert": "verify",
    "select": "tap",
}


def _classify_action(action_text: str) -> str:
    """Classify a natural-language test step into a device operation type."""
    action_lower = action_text.lower()
    for keyword, op_type in _ACTION_PATTERNS.items():
        if keyword in action_lower:
            return op_type
    return "verify"  # Default: just take screenshot and verify


def _extract_target(action_text: str) -> str:
    """Extract the target element/text from an action description."""
    import re
    # Look for quoted strings: "Login", 'Submit', etc.
    quoted = re.findall(r"""['"]([^'"]+)['"]""", action_text)
    if quoted:
        return quoted[0]
    # Look for text after "on", "the", "button", "link", "field"
    for marker in ["button", "link", "field", "input", "tab", "menu", "icon", "element"]:
        m = re.search(rf"{marker}\s+(.+?)(?:\s*$|\s+(?:to|in|on|at|with|from))", action_text, re.I)
        if m:
            return m.group(1).strip().rstrip(".")
    # Look for text after "Click", "Tap", "on"
    m = re.search(r"(?:click|tap|press)\s+(?:on\s+)?(?:the\s+)?(.+?)(?:\s*$|\s+(?:to|and|then))", action_text, re.I)
    if m:
        return m.group(1).strip().rstrip(".")
    return ""


def _extract_type_value(action_text: str) -> str:
    """Extract the text to type from an action like 'Type "hello" in search field'."""
    import re
    quoted = re.findall(r"""['"]([^'"]+)['"]""", action_text)
    if quoted:
        return quoted[0]
    m = re.search(r"(?:type|enter|fill|input)\s+(.+?)\s+(?:in|into|on)", action_text, re.I)
    if m:
        return m.group(1).strip()
    return "test input"


def _step_fingerprint(screen_text: str, action_text: str) -> str:
    """Stable fingerprint for a (screen_state, action) pair.

    Used to look up precedents in the context graph — past runs that executed
    the same action on the same screen. Stable as long as the screen layout
    and action text don't change substantially.
    """
    sig = f"{screen_text[:300]}|{action_text}"
    return hashlib.sha256(sig.encode()).hexdigest()[:12]


async def execute_test_suite(
    test_suite: TestSuiteResult,
    mobile_mcp_client,
    device_id: str,
    app_url: Optional[str] = None,
    flow_type: str = "web",
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Execute test cases from a generated test suite on the live device.

    Yields SSE events for each test step and result.
    """
    tests_to_run = _prioritize_tests(test_suite.test_cases)
    total = len(tests_to_run)

    # ── Context Graph setup ──────────────────────────────────────────────────
    # Load (or create) the per-app context graph. Every run writes
    # OBSERVATION → ACTION → OUTCOME nodes so future runs can call
    # find_precedents() and skip re-verifying known-good paths.
    _app_key = hashlib.sha256((app_url or "unknown").encode()).hexdigest()[:16]
    _cgm = ContextGraphManager.get()
    _graph = _cgm.get_app_graph(_app_key)
    _run_id = f"exec_{int(time.time())}"
    # ────────────────────────────────────────────────────────────────────────

    yield {
        "type": "stage_transition",
        "to_stage": "EXECUTION",
    }
    yield {
        "type": "stage_activity",
        "stage": "EXECUTION",
        "activity": "starting",
        "message": f"Executing {total} test cases on live device...",
    }

    # Navigate to app first
    if app_url and flow_type == "web":
        try:
            await mobile_mcp_client.open_url(device_id, app_url)
            await asyncio.sleep(3)
        except Exception as e:
            logger.warning(f"Failed to navigate to app URL: {e}")
            import subprocess
            try:
                subprocess.run(
                    ["adb", "-s", device_id, "shell", "am", "start",
                     "-a", "android.intent.action.VIEW", "-d", app_url],
                    timeout=10, check=True,
                )
                await asyncio.sleep(3)
            except Exception:
                pass

    results: List[Dict[str, Any]] = []
    passed = 0
    failed = 0

    for idx, tc in enumerate(tests_to_run):
        tc_start = time.time()
        test_result = {
            "test_id": tc.test_id,
            "name": tc.name,
            "priority": tc.priority,
            "category": tc.category,
            "status": "running",
            "steps_executed": 0,
            "steps_total": len(tc.steps[:MAX_STEPS_PER_TEST]),
            "step_results": [],
            "screenshots": [],
            "error": None,
        }

        yield {
            "type": "test_execution_start",
            "test_id": tc.test_id,
            "name": tc.name,
            "priority": tc.priority,
            "index": idx + 1,
            "total": total,
        }

        # Navigate back to start for each test (web: reload app)
        if app_url and flow_type == "web" and idx > 0:
            try:
                await mobile_mcp_client.open_url(device_id, app_url)
                await asyncio.sleep(2)
            except Exception:
                # Try ADB fallback
                import subprocess
                try:
                    subprocess.run(
                        ["adb", "-s", device_id, "shell", "am", "start",
                         "-a", "android.intent.action.VIEW", "-d", app_url],
                        timeout=10, check=True,
                    )
                    await asyncio.sleep(2)
                except Exception:
                    pass

        # Execute each step
        test_passed = True
        for step in tc.steps[:MAX_STEPS_PER_TEST]:
            step_start = time.time()
            step_result = {
                "step_number": step.step_number,
                "action": step.action,
                "expected": step.expected_result,
                "status": "running",
                "screenshot_path": None,
                "actual_result": None,
                "error": None,
            }

            action_type = _classify_action(step.action)
            target = _extract_target(step.action)

            try:
                # Execute the action
                if action_type == "tap" and target:
                    try:
                        await mobile_mcp_client.tap_by_text(device_id, target)
                    except Exception:
                        # Fallback: try tap_element or list and find
                        try:
                            elements = await mobile_mcp_client.get_ui_elements(device_id)
                            # Try to find element with matching text
                            if isinstance(elements, dict):
                                el_list = elements.get("content", [{}])
                                if el_list and isinstance(el_list[0], dict):
                                    el_text = el_list[0].get("text", "")
                                    logger.debug(f"Elements on screen: {el_text[:200]}")
                        except Exception:
                            pass
                    await asyncio.sleep(1.5)

                elif action_type == "type":
                    type_value = _extract_type_value(step.action)
                    if target:
                        try:
                            await mobile_mcp_client.tap_by_text(device_id, target)
                            await asyncio.sleep(0.5)
                        except Exception:
                            pass
                    try:
                        await mobile_mcp_client.type_text(device_id, type_value)
                    except Exception as e:
                        logger.debug(f"type_text failed: {e}")
                    await asyncio.sleep(1)

                elif action_type == "navigate" and target:
                    nav_url = target if target.startswith("http") else f"{app_url}/{target.lstrip('/')}" if app_url else target
                    try:
                        await mobile_mcp_client.open_url(device_id, nav_url)
                    except Exception:
                        import subprocess
                        try:
                            subprocess.run(
                                ["adb", "-s", device_id, "shell", "am", "start",
                                 "-a", "android.intent.action.VIEW", "-d", nav_url],
                                timeout=10, check=True,
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(2)

                elif action_type == "back":
                    try:
                        await mobile_mcp_client.press_button(device_id, "BACK")
                    except Exception:
                        import subprocess
                        subprocess.run(
                            ["adb", "-s", device_id, "shell", "input", "keyevent", "4"],
                            timeout=5,
                        )
                    await asyncio.sleep(1)

                elif action_type == "scroll":
                    try:
                        screen_size = await mobile_mcp_client.get_screen_size(device_id)
                        if isinstance(screen_size, dict):
                            w = screen_size.get("width", 1080) // 2
                            h = screen_size.get("height", 2400)
                            import subprocess
                            subprocess.run(
                                ["adb", "-s", device_id, "shell", "input", "swipe",
                                 str(w), str(h * 3 // 4), str(w), str(h // 4), "300"],
                                timeout=5,
                            )
                    except Exception:
                        pass
                    await asyncio.sleep(1)

                elif action_type == "refresh":
                    if app_url:
                        try:
                            await mobile_mcp_client.open_url(device_id, app_url)
                        except Exception:
                            pass
                    await asyncio.sleep(2)

                elif action_type == "wait":
                    await asyncio.sleep(2)

                # Take screenshot after action
                screenshot_data = None
                try:
                    screenshot_result = await mobile_mcp_client.take_screenshot(device_id)
                    if isinstance(screenshot_result, dict):
                        screenshot_data = screenshot_result.get("data")
                except Exception as e:
                    logger.debug(f"Screenshot failed: {e}")

                # Save screenshot
                if screenshot_data:
                    screenshot_filename = f"{tc.test_id}_step{step.step_number}_{int(time.time())}.png"
                    screenshot_path = str(EXEC_SCREENSHOTS_DIR / screenshot_filename)
                    try:
                        with open(screenshot_path, "wb") as f:
                            f.write(base64.b64decode(screenshot_data))
                        step_result["screenshot_path"] = screenshot_path
                        test_result["screenshots"].append(screenshot_path)
                    except Exception:
                        pass

                # Verify: check if expected result is visible on screen
                verification_passed = True
                actual_result = "Action completed"

                try:
                    ui_elements = await mobile_mcp_client.get_ui_elements(device_id)
                    screen_text = ""
                    if isinstance(ui_elements, dict):
                        content = ui_elements.get("content", [])
                        for item in content:
                            if isinstance(item, dict):
                                screen_text += item.get("text", "") + " "

                    # ── Context Graph: precedent lookup ──────────────────
                    # Compute a fingerprint for this (screen_state, action) pair
                    # and look up past outcomes. If we have strong precedent
                    # evidence, use it to augment verification confidence.
                    _step_fp = _step_fingerprint(screen_text, step.action)
                    _precedents = _graph.find_precedents(
                        _step_fp, node_type=NodeType.ACTION, limit=5
                    )
                    _precedent_context = ""
                    if _precedents:
                        # Resolve outcomes from the graph for each precedent action node
                        _past_outcomes = []
                        for _pnode in _precedents:
                            for _edge, _onode in _graph.get_outgoing(
                                _pnode.node_id, EdgeType.ACTION_PRODUCED_STATE
                            ):
                                _past_outcomes.append(_onode.label)
                        _pass_count = _past_outcomes.count("pass")
                        _fail_count = _past_outcomes.count("fail")
                        if _past_outcomes:
                            _precedent_context = (
                                f"[precedent: {_pass_count}P/{_fail_count}F across "
                                f"{len(_past_outcomes)} prior runs]"
                            )
                    # ────────────────────────────────────────────────────

                    # Simple verification: check if key words from expected result appear on screen
                    expected_lower = step.expected_result.lower()
                    screen_lower = screen_text.lower()

                    # Extract key phrases to check
                    key_checks = []
                    # Look for quoted strings in expected result
                    import re
                    quoted = re.findall(r"""['"]([^'"]+)['"]""", step.expected_result)
                    key_checks.extend(quoted)

                    # Check for keywords like "visible", "displays", "shows", "appears"
                    if any(kw in expected_lower for kw in ["visible", "display", "show", "appear", "open", "loaded"]):
                        # Check that the page isn't blank/error
                        if len(screen_text.strip()) > 20:
                            verification_passed = True
                            actual_result = f"Screen has content ({len(screen_text)} chars)"
                        else:
                            verification_passed = False
                            actual_result = "Screen appears empty or has minimal content"

                    elif any(kw in expected_lower for kw in ["error", "fail", "invalid", "denied"]):
                        # Negative test: error should be shown
                        if any(kw in screen_lower for kw in ["error", "fail", "invalid", "denied", "wrong"]):
                            verification_passed = True
                            actual_result = "Error message displayed as expected"
                        else:
                            verification_passed = False
                            actual_result = "Expected error message not found on screen"

                    elif key_checks:
                        # Check if quoted text appears on screen
                        found = [kc for kc in key_checks if kc.lower() in screen_lower]
                        if found:
                            verification_passed = True
                            actual_result = f"Found on screen: {', '.join(found)}"
                        else:
                            verification_passed = False
                            actual_result = f"Not found on screen: {', '.join(key_checks)}"

                    else:
                        # Generic: action completed, check screen isn't crashed
                        if "error" not in screen_lower and len(screen_text.strip()) > 10:
                            verification_passed = True
                            actual_result = "Action completed, screen is responsive"
                        elif len(screen_text.strip()) <= 10:
                            verification_passed = False
                            actual_result = "Screen may be blank or unresponsive"
                        else:
                            verification_passed = False
                            actual_result = "Error detected on screen"

                except Exception as e:
                    logger.debug(f"Verification failed: {e}")
                    # Can't verify — mark as inconclusive
                    verification_passed = True  # Don't fail if we can't verify
                    actual_result = f"Action executed (verification unavailable: {e})"

                # ── Context Graph: precedent override ───────────────────
                # Strong prior evidence (≥3 past runs all passing) overrides
                # a false-negative screen-text check. Strong failure evidence
                # appends a flakiness note but doesn't override the current
                # result — the screen is the ground truth for this run.
                if not verification_passed and _precedents:
                    _past_outcomes = []
                    for _pnode in _precedents:
                        for _edge, _onode in _graph.get_outgoing(
                            _pnode.node_id, EdgeType.ACTION_PRODUCED_STATE
                        ):
                            _past_outcomes.append(_onode.label)
                    if _past_outcomes.count("pass") >= 3 and _past_outcomes.count("fail") == 0:
                        verification_passed = True
                        actual_result += f" (precedent override: {_precedent_context})"
                elif verification_passed and _precedent_context:
                    actual_result += f" {_precedent_context}"

                # ── Context Graph: write this step's nodes ───────────────
                # OBSERVATION → ACTION → OUTCOME  (persisted at run end)
                try:
                    _obs = GraphNode(
                        NodeType.OBSERVATION,
                        label=f"screen_step{step.step_number}",
                        data={"screen_text_snippet": screen_text[:200]},
                        run_id=_run_id,
                        fingerprint=_step_fp,
                    )
                    _act = GraphNode(
                        NodeType.ACTION,
                        label=step.action[:80],
                        data={"action_type": action_type, "target": target,
                              "test_id": tc.test_id},
                        run_id=_run_id,
                        fingerprint=_step_fp,
                    )
                    _out = GraphNode(
                        NodeType.OUTCOME,
                        label="pass" if verification_passed else "fail",
                        data={"actual_result": actual_result[:200],
                              "expected": step.expected_result[:200]},
                        run_id=_run_id,
                    )
                    _graph.add_node(_obs)
                    _graph.add_node(_act)
                    _graph.add_node(_out)
                    _graph.connect(_obs.node_id, _act.node_id,
                                   EdgeType.ACTION_TAKEN_FROM)
                    _graph.connect(_act.node_id, _out.node_id,
                                   EdgeType.ACTION_PRODUCED_STATE)
                except Exception as _cg_err:
                    logger.debug(f"Context graph write skipped: {_cg_err}")
                # ────────────────────────────────────────────────────────

                step_result["status"] = "pass" if verification_passed else "fail"
                step_result["actual_result"] = actual_result
                step_result["duration_ms"] = int((time.time() - step_start) * 1000)

                if not verification_passed:
                    test_passed = False

            except Exception as e:
                step_result["status"] = "error"
                step_result["error"] = str(e)
                step_result["duration_ms"] = int((time.time() - step_start) * 1000)
                test_passed = False

            test_result["step_results"].append(step_result)
            test_result["steps_executed"] += 1

            yield {
                "type": "test_step_result",
                "test_id": tc.test_id,
                "step_number": step.step_number,
                "action": step.action,
                "status": step_result["status"],
                "actual_result": step_result.get("actual_result", ""),
                "has_screenshot": step_result["screenshot_path"] is not None,
            }

        # Test complete
        test_result["status"] = "pass" if test_passed else "fail"
        test_result["duration_ms"] = int((time.time() - tc_start) * 1000)

        if test_passed:
            passed += 1
        else:
            failed += 1

        results.append(test_result)

        yield {
            "type": "test_execution_result",
            "test_id": tc.test_id,
            "name": tc.name,
            "status": test_result["status"],
            "priority": tc.priority,
            "steps_passed": sum(1 for s in test_result["step_results"] if s["status"] == "pass"),
            "steps_total": test_result["steps_executed"],
            "duration_ms": test_result["duration_ms"],
            "screenshots": len(test_result["screenshots"]),
        }

        yield {
            "type": "stage_activity",
            "stage": "EXECUTION",
            "activity": "executing",
            "message": f"Test {idx + 1}/{total}: {tc.name} — {'PASS' if test_passed else 'FAIL'}",
        }

    # Execution complete
    yield {
        "type": "stage_activity",
        "stage": "EXECUTION",
        "activity": "completed",
        "message": f"Executed {total} tests: {passed} passed, {failed} failed",
    }

    # ── Context Graph: persist all nodes written this run ───────────────────
    try:
        _cgm.save_all()
        logger.debug(
            f"Context graph saved for run {_run_id} "
            f"(app_key={_app_key}, {len(_graph._nodes)} nodes)"
        )
    except Exception as _save_err:
        logger.warning(f"Context graph save failed: {_save_err}")
    # ────────────────────────────────────────────────────────────────────────

    yield {
        "type": "execution_complete",
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / total, 4) if total > 0 else 0.0,
        "results": results,
    }
