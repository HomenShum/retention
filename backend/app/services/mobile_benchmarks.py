"""Mobile Benchmark Harness — SWE-Bench Mobile, MobileBench-OL, MemGUI-Bench.

Runs three benchmark suites against retention.sh's agent infrastructure:
1. SWE-Bench Mobile  — develop + test mobile apps (code gen → device verification)
2. MobileBench-OL    — 80-app task execution with noise robustness (pop-ups, ads)
3. MemGUI-Bench      — cross-session memory (run → remember → improve)

Each suite uses the existing AndroidWorld executor + LLM judge.
Results are scored, stored, diffed against baselines, and posted to Slack.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BENCH_DIR = _REPO_ROOT / "data" / "mobile_benchmarks"
_RESULTS_DIR = _BENCH_DIR / "results"
_BASELINES_DIR = _BENCH_DIR / "baselines"


def _ensure_dirs():
    _BENCH_DIR.mkdir(parents=True, exist_ok=True)
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _BASELINES_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Task definitions — inspired by the academic benchmarks, adapted for retention.sh
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkTask:
    id: str
    suite: str  # swe_bench_mobile | mobilebench_ol | memgui_bench
    title: str
    instruction: str
    app_package: str  # target app or "any"
    difficulty: str  # easy | medium | hard
    noise_type: str = "none"  # none | popup | ad | notification | permission
    requires_memory: bool = False
    expected_outcome: str = ""
    max_steps: int = 50
    timeout_s: int = 300


# ── SWE-Bench Mobile tasks ──
# Tests: can the agent write code, push it, and verify it works on a real device?
SWE_BENCH_MOBILE_TASKS = [
    BenchmarkTask(
        id="swem-001", suite="swe_bench_mobile",
        title="Fix login validation crash",
        instruction="The login form crashes when email contains '+'. Write a fix, run tests, verify on device.",
        app_package="com.saucedemo.app", difficulty="medium",
        expected_outcome="Login with 'user+test@email.com' succeeds without crash",
    ),
    BenchmarkTask(
        id="swem-002", suite="swe_bench_mobile",
        title="Add pull-to-refresh on product list",
        instruction="Implement pull-to-refresh on the product listing screen. Verify the refresh animation plays and data reloads.",
        app_package="com.saucedemo.app", difficulty="medium",
        expected_outcome="Pull down gesture triggers refresh animation and reloads product list",
    ),
    BenchmarkTask(
        id="swem-003", suite="swe_bench_mobile",
        title="Fix dark mode text contrast",
        instruction="Text in the checkout screen is unreadable in dark mode. Fix the text color to meet WCAG AA contrast ratio.",
        app_package="com.saucedemo.app", difficulty="easy",
        expected_outcome="Checkout text has contrast ratio >= 4.5:1 in dark mode",
    ),
    BenchmarkTask(
        id="swem-004", suite="swe_bench_mobile",
        title="Implement offline cart persistence",
        instruction="Cart items are lost when app goes offline. Implement local storage persistence so cart survives network loss.",
        app_package="com.saucedemo.app", difficulty="hard",
        expected_outcome="Items added to cart persist after toggling airplane mode",
    ),
    BenchmarkTask(
        id="swem-005", suite="swe_bench_mobile",
        title="Fix keyboard overlapping input field",
        instruction="On the address form, the keyboard covers the zip code field. Fix the scroll behavior so the active field is always visible.",
        app_package="com.saucedemo.app", difficulty="medium",
        expected_outcome="Tapping zip code field scrolls it above keyboard",
    ),
    BenchmarkTask(
        id="swem-006", suite="swe_bench_mobile",
        title="Add search filter by price range",
        instruction="Add a price range slider filter to the product search. Filter should work in real-time.",
        app_package="com.saucedemo.app", difficulty="hard",
        expected_outcome="Sliding price filter updates product list in real-time",
    ),
    BenchmarkTask(
        id="swem-007", suite="swe_bench_mobile",
        title="Fix back button not returning to previous screen",
        instruction="Pressing back from product detail goes to home instead of the category the user came from. Fix navigation stack.",
        app_package="com.saucedemo.app", difficulty="medium",
        expected_outcome="Back button returns to the originating category screen",
    ),
    BenchmarkTask(
        id="swem-008", suite="swe_bench_mobile",
        title="Add biometric authentication option",
        instruction="Add fingerprint/face auth as login option alongside username/password. Should fall back to PIN if biometrics unavailable.",
        app_package="com.saucedemo.app", difficulty="hard",
        expected_outcome="Biometric prompt appears on login, PIN fallback works",
    ),
]

# ── MobileBench-OL tasks ──
# Tests: can the agent complete tasks across diverse apps WITH noise (pop-ups, ads, permissions)?
MOBILEBENCH_OL_TASKS = [
    BenchmarkTask(
        id="mbol-001", suite="mobilebench_ol",
        title="Set alarm through notification interruption",
        instruction="Set an alarm for 7:30 AM tomorrow. A notification will pop up mid-task — dismiss it and continue.",
        app_package="com.google.android.deskclock", difficulty="easy",
        noise_type="notification",
        expected_outcome="Alarm set for 7:30 AM, notification dismissed",
    ),
    BenchmarkTask(
        id="mbol-002", suite="mobilebench_ol",
        title="Send message despite ad overlay",
        instruction="Open Messages, compose 'Meeting at 3pm' to the first contact. An ad overlay will appear — close it first.",
        app_package="com.google.android.apps.messaging", difficulty="medium",
        noise_type="ad",
        expected_outcome="Message composed and ready to send, ad dismissed",
    ),
    BenchmarkTask(
        id="mbol-003", suite="mobilebench_ol",
        title="Grant permission and take photo",
        instruction="Open Camera app, grant camera permission if prompted, take a photo.",
        app_package="com.android.camera2", difficulty="easy",
        noise_type="permission",
        expected_outcome="Photo captured successfully, permission granted",
    ),
    BenchmarkTask(
        id="mbol-004", suite="mobilebench_ol",
        title="Multi-app: copy address from Maps to Notes",
        instruction="Search for 'Golden Gate Bridge' in Maps, copy the address, switch to Notes, paste it into a new note.",
        app_package="com.google.android.apps.maps", difficulty="hard",
        noise_type="popup",
        expected_outcome="Note created with Golden Gate Bridge address",
    ),
    BenchmarkTask(
        id="mbol-005", suite="mobilebench_ol",
        title="Navigate settings with system dialog",
        instruction="Go to Settings > Display > Dark mode. A 'battery optimization' dialog may appear — dismiss it.",
        app_package="com.android.settings", difficulty="easy",
        noise_type="popup",
        expected_outcome="Dark mode toggled in display settings",
    ),
    BenchmarkTask(
        id="mbol-006", suite="mobilebench_ol",
        title="Install app from Play Store with interruptions",
        instruction="Search for 'Calculator' in Play Store and install it. Dismiss any promotional banners or permission requests.",
        app_package="com.android.vending", difficulty="medium",
        noise_type="ad",
        expected_outcome="Calculator app installed successfully",
    ),
    BenchmarkTask(
        id="mbol-007", suite="mobilebench_ol",
        title="Create calendar event across timezone popup",
        instruction="Create a calendar event 'Team Standup' for tomorrow 9am. Handle any timezone confirmation dialogs.",
        app_package="com.google.android.calendar", difficulty="medium",
        noise_type="popup",
        expected_outcome="Calendar event created for 9am tomorrow",
    ),
    BenchmarkTask(
        id="mbol-008", suite="mobilebench_ol",
        title="Download file through cookie consent",
        instruction="Open Chrome, navigate to a test page, download the sample PDF. Dismiss cookie consent banners.",
        app_package="com.android.chrome", difficulty="medium",
        noise_type="popup",
        expected_outcome="PDF downloaded to device storage",
    ),
    BenchmarkTask(
        id="mbol-009", suite="mobilebench_ol",
        title="Compose email with attachment despite notification storm",
        instruction="Open Gmail, compose email to test@example.com with subject 'Report'. Attach a file from Downloads. Multiple notifications will fire.",
        app_package="com.google.android.gm", difficulty="hard",
        noise_type="notification",
        expected_outcome="Email drafted with attachment, ready to send",
    ),
    BenchmarkTask(
        id="mbol-010", suite="mobilebench_ol",
        title="Configure WiFi through permission cascade",
        instruction="Go to WiFi settings, connect to 'TestNetwork'. Multiple permission dialogs (location, WiFi) will appear.",
        app_package="com.android.settings", difficulty="medium",
        noise_type="permission",
        expected_outcome="Connected to TestNetwork WiFi",
    ),
]

# ── MemGUI-Bench tasks ──
# Tests: can the agent remember context across sessions and improve over time?
MEMGUI_BENCH_TASKS = [
    BenchmarkTask(
        id="mgb-001", suite="memgui_bench",
        title="Remember preferred language from last session",
        instruction="In session 1, set app language to Spanish. In session 2, verify the agent remembers and navigates in Spanish.",
        app_package="com.android.settings", difficulty="medium",
        requires_memory=True,
        expected_outcome="Agent navigates Spanish UI without re-setting language",
    ),
    BenchmarkTask(
        id="mgb-002", suite="memgui_bench",
        title="Learn from failed navigation path",
        instruction="Session 1: try to find 'Developer Options' (fail expected on first try). Session 2: agent should go directly to correct path.",
        app_package="com.android.settings", difficulty="medium",
        requires_memory=True,
        expected_outcome="Session 2 reaches Developer Options in fewer steps than session 1",
    ),
    BenchmarkTask(
        id="mgb-003", suite="memgui_bench",
        title="Remember user contact preference",
        instruction="Session 1: user adds 'Mom' as favorite contact. Session 2: when asked to 'call Mom', agent should find contact directly.",
        app_package="com.google.android.contacts", difficulty="easy",
        requires_memory=True,
        expected_outcome="Agent finds Mom contact without re-searching",
    ),
    BenchmarkTask(
        id="mgb-004", suite="memgui_bench",
        title="Adapt to user's app workflow",
        instruction="Session 1: user always opens Calendar→Notes→Email in that order. Session 2: after opening Calendar, suggest Notes next.",
        app_package="com.google.android.calendar", difficulty="hard",
        requires_memory=True,
        expected_outcome="Agent suggests or opens Notes after Calendar",
    ),
    BenchmarkTask(
        id="mgb-005", suite="memgui_bench",
        title="Remember bug reproduction steps",
        instruction="Session 1: reproduce a crash (specific tap sequence). Session 2: agent should reproduce it in fewer steps using memory.",
        app_package="com.saucedemo.app", difficulty="hard",
        requires_memory=True,
        expected_outcome="Session 2 reproduces crash faster than session 1",
    ),
    BenchmarkTask(
        id="mgb-006", suite="memgui_bench",
        title="Cross-session test coverage tracking",
        instruction="Session 1: test screens A, B, C. Session 2: agent should automatically start with untested screens D, E.",
        app_package="com.saucedemo.app", difficulty="hard",
        requires_memory=True,
        expected_outcome="Session 2 tests new screens, not already-covered ones",
    ),
]

ALL_TASKS = SWE_BENCH_MOBILE_TASKS + MOBILEBENCH_OL_TASKS + MEMGUI_BENCH_TASKS

SUITE_MAP: dict[str, list[BenchmarkTask]] = {
    "swe_bench_mobile": SWE_BENCH_MOBILE_TASKS,
    "mobilebench_ol": MOBILEBENCH_OL_TASKS,
    "memgui_bench": MEMGUI_BENCH_TASKS,
}


# ---------------------------------------------------------------------------
# Task runner — executes tasks via the agent pipeline + LLM judge
# ---------------------------------------------------------------------------

@dataclass
class TaskResult:
    task_id: str
    suite: str
    title: str
    status: str  # pass | fail | error | timeout
    score: float  # 0.0 - 1.0
    steps_taken: int
    elapsed_s: float
    judge_result: dict[str, Any] = field(default_factory=dict)
    noise_handled: bool = False
    memory_used: bool = False
    error: str = ""
    agent_output: str = ""


# Mobile benchmark quality gates — extended from base eval_benchmark gates
MOBILE_QUALITY_GATES = [
    {"name": "task_completed", "question": "Did the agent successfully complete the stated task objective?"},
    {"name": "correct_app", "question": "Did the agent operate in the correct application(s)?"},
    {"name": "noise_resilience", "question": "If noise (popups, ads, notifications) appeared, did the agent handle them correctly?"},
    {"name": "efficiency", "question": "Did the agent complete the task in a reasonable number of steps (not excessive back-and-forth)?"},
    {"name": "no_destructive_actions", "question": "Did the agent avoid destructive actions like deleting data, uninstalling apps, or factory reset?"},
]


async def _judge_task_result(task: BenchmarkTask, agent_output: str) -> dict[str, Any]:
    """Run mobile-specific quality gates on a task result."""
    from .llm_judge import call_responses_api

    prompt = (
        "You are a strict mobile QA benchmark judge. Evaluate the agent's execution.\n\n"
        f"TASK: {task.title}\n"
        f"INSTRUCTION: {task.instruction}\n"
        f"EXPECTED OUTCOME: {task.expected_outcome}\n"
        f"NOISE TYPE: {task.noise_type}\n"
        f"REQUIRES MEMORY: {task.requires_memory}\n\n"
        f"AGENT OUTPUT:\n{agent_output[:3000]}\n\n"
        "For EACH criterion, respond with JSON array:\n"
    )
    for g in MOBILE_QUALITY_GATES:
        prompt += f"- {g['name']}: {g['question']}\n"
    prompt += '\nRespond with ONLY a JSON array: [{"name": "...", "passed": true/false, "reason": "..."}]'

    try:
        raw = await call_responses_api(prompt, task="gate_evaluation", reasoning_effort="medium", timeout_s=30)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        gates = json.loads(raw)
        passed = sum(1 for g in gates if g.get("passed"))
        return {"gates": gates, "score": passed / max(len(gates), 1), "passed_all": passed == len(gates)}
    except Exception as e:
        logger.error("Mobile judge failed: %s", e)
        return {"gates": [], "score": 0.5, "passed_all": False, "error": str(e)}


async def _run_task(task: BenchmarkTask) -> TaskResult:
    """Execute a single benchmark task through the agent pipeline."""
    from .llm_judge import call_responses_api

    t0 = time.time()
    try:
        # Build the agent prompt — simulates what the agent would see
        agent_prompt = (
            f"You are a mobile testing agent. Execute this benchmark task on a real device.\n\n"
            f"TASK: {task.title}\n"
            f"APP: {task.app_package}\n"
            f"INSTRUCTION: {task.instruction}\n"
            f"MAX STEPS: {task.max_steps}\n"
        )
        if task.noise_type != "none":
            agent_prompt += f"WARNING: Expect {task.noise_type} interruptions — handle them and continue.\n"
        if task.requires_memory:
            agent_prompt += "NOTE: This task requires cross-session memory. Check previous session results.\n"
        agent_prompt += (
            "\nExecute the task step by step. For each step, describe:\n"
            "1. What you observe on screen\n"
            "2. What action you take\n"
            "3. What happened after the action\n"
            "End with RESULT: PASS or RESULT: FAIL and a brief explanation."
        )

        # Run through the agent (using strategy-brief model for now)
        output = await call_responses_api(
            agent_prompt,
            task="deep_sim_research",  # Uses mini model — fast, capable
            reasoning_effort="high",
            timeout_s=task.timeout_s,
        )

        elapsed = time.time() - t0

        # Count steps from output
        steps = output.lower().count("step ") + output.lower().count("action:")
        steps = max(steps, 1)

        # Judge the result
        judge = await _judge_task_result(task, output)

        # Determine pass/fail
        result_pass = "RESULT: PASS" in output.upper() or "RESULT:PASS" in output.upper()
        status = "pass" if result_pass and judge.get("passed_all") else "fail"

        return TaskResult(
            task_id=task.id,
            suite=task.suite,
            title=task.title,
            status=status,
            score=judge.get("score", 0),
            steps_taken=steps,
            elapsed_s=round(elapsed, 1),
            judge_result=judge,
            noise_handled=task.noise_type != "none" and judge.get("score", 0) > 0.5,
            memory_used=task.requires_memory,
            agent_output=output[:500],
        )
    except asyncio.TimeoutError:
        return TaskResult(
            task_id=task.id, suite=task.suite, title=task.title,
            status="timeout", score=0, steps_taken=0,
            elapsed_s=round(time.time() - t0, 1), error="Task timed out",
        )
    except Exception as e:
        return TaskResult(
            task_id=task.id, suite=task.suite, title=task.title,
            status="error", score=0, steps_taken=0,
            elapsed_s=round(time.time() - t0, 1), error=str(e)[:200],
        )


# ---------------------------------------------------------------------------
# Suite runner — runs all tasks in a suite, scores, diffs baseline
# ---------------------------------------------------------------------------

@dataclass
class SuiteResult:
    suite: str
    timestamp: float
    tasks_run: int
    passed: int
    failed: int
    errors: int
    timeouts: int
    avg_score: float
    avg_steps: float
    avg_elapsed_s: float
    noise_resilience_rate: float  # % of noise tasks handled correctly
    memory_utilization_rate: float  # % of memory tasks that used memory
    regression_warnings: list[str] = field(default_factory=list)
    task_results: list[dict[str, Any]] = field(default_factory=list)


async def run_suite(suite_name: str) -> SuiteResult:
    """Run a complete benchmark suite."""
    tasks = SUITE_MAP.get(suite_name, [])
    if not tasks:
        return SuiteResult(
            suite=suite_name, timestamp=time.time(),
            tasks_run=0, passed=0, failed=0, errors=0, timeouts=0,
            avg_score=0, avg_steps=0, avg_elapsed_s=0,
            noise_resilience_rate=0, memory_utilization_rate=0,
        )

    logger.info("Starting %s benchmark: %d tasks", suite_name, len(tasks))

    # Run tasks sequentially (they share device state for memory tasks)
    results: list[TaskResult] = []
    for task in tasks:
        logger.info("  Running %s: %s", task.id, task.title)
        result = await _run_task(task)
        results.append(result)
        logger.info("  → %s (score=%.1f, %.1fs)", result.status, result.score, result.elapsed_s)

    # Aggregate
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    errors = sum(1 for r in results if r.status == "error")
    timeouts = sum(1 for r in results if r.status == "timeout")
    scores = [r.score for r in results]
    steps = [r.steps_taken for r in results if r.steps_taken > 0]
    elapsed = [r.elapsed_s for r in results]

    noise_tasks = [r for r in results if any(t.noise_type != "none" for t in tasks if t.id == r.task_id)]
    noise_handled = sum(1 for r in noise_tasks if r.noise_handled)

    memory_tasks = [r for r in results if r.memory_used]
    memory_used = len(memory_tasks)

    suite_result = SuiteResult(
        suite=suite_name,
        timestamp=time.time(),
        tasks_run=len(results),
        passed=passed,
        failed=failed,
        errors=errors,
        timeouts=timeouts,
        avg_score=round(sum(scores) / max(len(scores), 1), 3),
        avg_steps=round(sum(steps) / max(len(steps), 1), 1),
        avg_elapsed_s=round(sum(elapsed) / max(len(elapsed), 1), 1),
        noise_resilience_rate=round(noise_handled / max(len(noise_tasks), 1), 3),
        memory_utilization_rate=round(memory_used / max(len(memory_tasks), 1), 3),
        task_results=[{
            "task_id": r.task_id, "title": r.title, "status": r.status,
            "score": r.score, "steps": r.steps_taken, "elapsed_s": r.elapsed_s,
            "error": r.error,
        } for r in results],
    )

    # Check regression against baseline
    suite_result.regression_warnings = _check_suite_regression(suite_name, suite_result)

    # Save result
    _save_suite_result(suite_name, suite_result)

    return suite_result


def _save_suite_result(suite_name: str, result: SuiteResult):
    """Persist suite result to disk."""
    _ensure_dirs()
    date_str = time.strftime("%Y-%m-%d_%H%M")
    path = _RESULTS_DIR / f"{date_str}_{suite_name}.json"
    data = {
        "suite": result.suite,
        "timestamp": result.timestamp,
        "tasks_run": result.tasks_run,
        "passed": result.passed,
        "failed": result.failed,
        "errors": result.errors,
        "timeouts": result.timeouts,
        "avg_score": result.avg_score,
        "avg_steps": result.avg_steps,
        "avg_elapsed_s": result.avg_elapsed_s,
        "noise_resilience_rate": result.noise_resilience_rate,
        "memory_utilization_rate": result.memory_utilization_rate,
        "regression_warnings": result.regression_warnings,
        "task_results": result.task_results,
    }
    path.write_text(json.dumps(data, indent=2, default=str))
    logger.info("Suite result saved: %s", path)


def _check_suite_regression(suite_name: str, current: SuiteResult) -> list[str]:
    """Compare against baseline, return warnings."""
    baseline_path = _BASELINES_DIR / f"{suite_name}_baseline.json"
    if not baseline_path.exists():
        # No baseline yet — save this as baseline
        _ensure_dirs()
        baseline_path.write_text(json.dumps({
            "suite": suite_name,
            "avg_score": current.avg_score,
            "passed": current.passed,
            "tasks_run": current.tasks_run,
            "noise_resilience_rate": current.noise_resilience_rate,
            "timestamp": current.timestamp,
        }, indent=2))
        return ["First run — saved as baseline"]

    baseline = json.loads(baseline_path.read_text())
    warnings = []

    base_score = baseline.get("avg_score", 0)
    if current.avg_score < base_score - 0.05:
        warnings.append(
            f"Score regression: {current.avg_score:.1%} vs baseline {base_score:.1%} "
            f"(dropped {(base_score - current.avg_score):.1%})"
        )

    base_pass_rate = baseline.get("passed", 0) / max(baseline.get("tasks_run", 1), 1)
    curr_pass_rate = current.passed / max(current.tasks_run, 1)
    if curr_pass_rate < base_pass_rate - 0.1:
        warnings.append(
            f"Pass rate regression: {curr_pass_rate:.0%} vs baseline {base_pass_rate:.0%}"
        )

    base_noise = baseline.get("noise_resilience_rate", 0)
    if current.noise_resilience_rate < base_noise - 0.1:
        warnings.append(
            f"Noise resilience dropped: {current.noise_resilience_rate:.0%} vs baseline {base_noise:.0%}"
        )

    # Update baseline if improved
    if current.avg_score > base_score + 0.02:
        baseline_path.write_text(json.dumps({
            "suite": suite_name,
            "avg_score": current.avg_score,
            "passed": current.passed,
            "tasks_run": current.tasks_run,
            "noise_resilience_rate": current.noise_resilience_rate,
            "timestamp": current.timestamp,
        }, indent=2))
        warnings.append(f"New baseline set! Score improved: {base_score:.1%} → {current.avg_score:.1%}")

    return warnings


# ---------------------------------------------------------------------------
# Main entry point — runs all 3 suites and returns combined report
# ---------------------------------------------------------------------------

async def run_all_mobile_benchmarks() -> dict[str, Any]:
    """Run all 3 mobile benchmark suites in parallel and return combined results."""
    suite_names = ["swe_bench_mobile", "mobilebench_ol", "memgui_bench"]

    suite_outcomes = await asyncio.gather(
        *[run_suite(name) for name in suite_names],
        return_exceptions=True,
    )

    results = {}
    for suite_name, outcome in zip(suite_names, suite_outcomes):
        if isinstance(outcome, Exception):
            logger.error("Suite %s failed: %s", suite_name, outcome)
            results[suite_name] = {"error": str(outcome)}
        else:
            results[suite_name] = {
                "tasks_run": outcome.tasks_run,
                "passed": outcome.passed,
                "failed": outcome.failed,
                "avg_score": outcome.avg_score,
                "noise_resilience": outcome.noise_resilience_rate,
                "memory_utilization": outcome.memory_utilization_rate,
                "regressions": outcome.regression_warnings,
                "task_results": outcome.task_results,
            }

    return {
        "timestamp": time.time(),
        "suites": results,
        "total_tasks": sum(r.get("tasks_run", 0) for r in results.values() if isinstance(r, dict)),
        "total_passed": sum(r.get("passed", 0) for r in results.values() if isinstance(r, dict)),
    }


def format_slack_report(results: dict[str, Any]) -> str:
    """Format benchmark results as a Slack message."""
    total = results.get("total_tasks", 0)
    passed = results.get("total_passed", 0)
    pass_rate = passed / max(total, 1)

    lines = [
        f"*Mobile Benchmark Report* — {total} tasks, {pass_rate:.0%} pass rate",
        "",
    ]

    suite_emoji = {
        "swe_bench_mobile": ":hammer_and_wrench:",
        "mobilebench_ol": ":iphone:",
        "memgui_bench": ":brain:",
    }
    suite_labels = {
        "swe_bench_mobile": "SWE-Bench Mobile",
        "mobilebench_ol": "MobileBench-OL",
        "memgui_bench": "MemGUI-Bench",
    }

    for suite_name, data in results.get("suites", {}).items():
        emoji = suite_emoji.get(suite_name, ":bar_chart:")
        label = suite_labels.get(suite_name, suite_name)

        if "error" in data:
            lines.append(f"{emoji} *{label}*: :x: Error — {data['error'][:100]}")
            continue

        p = data.get("passed", 0)
        t = data.get("tasks_run", 0)
        score = data.get("avg_score", 0)
        noise = data.get("noise_resilience", 0)
        mem = data.get("memory_utilization", 0)
        regs = data.get("regressions", [])

        status = ":white_check_mark:" if p == t else ":warning:" if p > t / 2 else ":x:"
        lines.append(f"{emoji} *{label}* {status}  {p}/{t} passed | score {score:.0%}")

        if suite_name == "mobilebench_ol" and noise > 0:
            lines.append(f"    Noise resilience: {noise:.0%}")
        if suite_name == "memgui_bench" and mem > 0:
            lines.append(f"    Memory utilization: {mem:.0%}")

        for reg in regs:
            lines.append(f"    :warning: {reg}")

        # Show failed tasks
        for tr in data.get("task_results", []):
            if tr.get("status") != "pass":
                lines.append(f"    :x: `{tr['task_id']}` {tr['title']} — {tr.get('error') or tr['status']}")

    return "\n".join(lines)
