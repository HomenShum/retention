"""
Dual-Mode Benchmark Runner.

Executes web benchmark tasks in two modes:
  Mode A (claude-baseline): Raw Playwright execution, native artifacts only
  Mode B (test-assurance): Full TA pipeline with MCP, evaluator, evidence capture

Mirrors the AndroidWorldExecutor.run_benchmark() pattern.
"""

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..evidence_schema import (
    AgentMode,
    BenchmarkArtifacts,
    BenchmarkCost,
    BenchmarkRunEvidence,
    BenchmarkTaskMetrics,
    BenchmarkVerdict,
    BenchmarkVerdictLabel,
    RunStatus,
)
from ..evidence_writer import EvidenceWriter
from ..scorecard import ScorecardAggregator, BenchmarkScorecard
from .task_registry import BenchmarkTask, WebTaskRegistry

# Self-healing & memory imports (Phase 1-3)
try:
    from .element_resolver import WebElementResolver
    _resolver_available = True
except ImportError:
    _resolver_available = False

try:
    from .action_span_web import WebActionSpanService
    _action_span_available = True
except ImportError:
    _action_span_available = False

try:
    from app.agents.device_testing.session_memory import (
        SessionMemory, LearningStore, SessionEvaluator, get_session_evaluator
    )
    _memory_available = True
except ImportError:
    _memory_available = False

logger = logging.getLogger(__name__)


def _sync_evidence_to_convex(evidence: BenchmarkRunEvidence, suite_id: str) -> None:
    """Best-effort sync of benchmark run evidence to Convex (fire-and-forget)."""
    convex_url = os.environ.get("CONVEX_URL") or os.environ.get("VITE_CONVEX_URL")
    if not convex_url:
        return
    try:
        import httpx

        def _iso_to_ms(iso_str: Optional[str]) -> Optional[int]:
            if not iso_str:
                return None
            dt = datetime.fromisoformat(iso_str)
            return int(dt.timestamp() * 1000)

        payload = {
            "path": "benchmarkRuns:upsertRun",
            "args": {
                "runId": evidence.run_id,
                "suiteId": suite_id,
                "taskId": evidence.task_id,
                "appId": evidence.app_id,
                "platform": evidence.platform,
                "agentMode": evidence.agent_mode.value,
                "status": evidence.status.value,
                "startedAt": _iso_to_ms(evidence.start_time) or int(time.time() * 1000),
                "endedAt": _iso_to_ms(evidence.end_time),
                "durationSeconds": evidence.task_metrics.duration_seconds,
                "verdictLabel": evidence.verdict.label.value,
                "verdictConfidence": evidence.verdict.confidence,
                "verdictReason": evidence.verdict.reason,
                "tokenCostUsd": evidence.cost.total_cost_usd,
                "artifactCount": len(evidence.artifacts.screenshots) + sum(
                    1 for p in [
                        evidence.artifacts.trace_path,
                        evidence.artifacts.video_path,
                        evidence.artifacts.console_path,
                        evidence.artifacts.network_path,
                        evidence.artifacts.action_spans_path,
                    ] if p
                ),
            },
        }
        payload["args"] = {k: v for k, v in payload["args"].items() if v is not None}

        httpx.post(f"{convex_url}/api/mutation", json=payload, timeout=5)
        logger.debug("Synced evidence %s to Convex", evidence.run_id)
    except Exception as exc:
        logger.debug("Convex evidence sync failed (best-effort): %s", exc)


async def _run_playwright_task(
    task: BenchmarkTask,
    mode: AgentMode,
    artifacts_dir: Path,
) -> BenchmarkRunEvidence:
    """
    Execute a single task with Playwright (Python async API).
    Captures trace, video, screenshots, console logs, and network logs.
    """
    evidence = BenchmarkRunEvidence(
        task_id=task.task_id,
        app_id=task.app_id,
        platform=task.platform,
        agent_mode=mode,
    )

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("playwright not installed — pip install playwright && playwright install chromium")
        evidence.status = RunStatus.BLOCKED
        evidence.verdict = BenchmarkVerdict(
            label=BenchmarkVerdictLabel.INFRA_FAILURE,
            reason="playwright Python package not installed",
        )
        return evidence.finalize()

    console_messages: List[Dict[str, str]] = []
    network_entries: List[Dict[str, Any]] = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--single-process"])
            context = await browser.new_context(
                record_video_dir=str(artifacts_dir),
                viewport={"width": 1280, "height": 800},
            )
            await context.tracing.start(screenshots=True, snapshots=True, sources=True)

            page = await context.new_page()

            # Capture console and network
            page.on("console", lambda msg: console_messages.append({
                "type": msg.type,
                "text": msg.text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))
            page.on("request", lambda req: network_entries.append({
                "method": req.method,
                "url": req.url,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))
            page.on("response", lambda res: _record_response(res, network_entries))

            # Navigate to base_url
            base = task.base_url or "http://localhost:5173"
            await page.goto(base, wait_until="domcontentloaded", timeout=task.timeout_seconds * 1000)

            # Take initial screenshot
            ss_before = str(artifacts_dir / "before.png")
            await page.screenshot(path=ss_before, full_page=True)

            # --- Phase 2: Session Memory ---
            session_memory = None
            learning_store = None
            if _memory_available and mode == AgentMode.TEST_ASSURANCE:
                learning_store = LearningStore(store_path="data/web_learnings.json")
                session_memory = SessionMemory(
                    task_goal=task.prompt,
                    device_id=task.app_id,
                )

            # --- Phase 1: Self-Healing Resolver ---
            resolver = WebElementResolver() if _resolver_available else None

            # --- Phase 3: ActionSpan Service ---
            span_service = WebActionSpanService() if _action_span_available else None
            action_spans = []

            # For baseline mode: just navigate and check pass_rule heuristically
            # For TA mode: enhanced pipeline with resolver, memory, action spans
            if mode == AgentMode.CLAUDE_BASELINE:
                # Simple: navigate to the expected page and screenshot
                # The prompt describes what to do — in baseline we just load the page
                await page.wait_for_timeout(2000)
            else:
                # TA mode: enhanced evidence capture with self-healing + action spans
                # Start action span for page load
                load_span = None
                if span_service:
                    load_span = await span_service.start_span(page, "page_load", artifacts_dir)

                await page.wait_for_timeout(2000)

                if load_span and span_service:
                    load_span = await span_service.end_span(page, load_span, artifacts_dir)
                    action_spans.append(load_span)

                # Try element interactions using self-healing resolver
                if resolver and task.element_intents:
                    for intent in task.element_intents[:5]:  # Cap at 5 interactions
                        interact_span = None
                        if span_service:
                            interact_span = await span_service.start_span(
                                page, f"interact:{intent}", artifacts_dir
                            )

                        try:
                            locator = await resolver.resolve(
                                page, intent,
                                app_id=task.app_id,
                                learning_store=learning_store,
                            )
                            if locator:
                                await locator.click(timeout=5000)
                                await page.wait_for_timeout(1000)

                                if session_memory:
                                    session_memory.record_action(
                                        action=f"click:{intent}",
                                        state_before={"url": page.url},
                                        success=True,
                                    )
                            else:
                                if session_memory:
                                    session_memory.record_failure(
                                        action=f"click:{intent}",
                                        state_before={"url": page.url},
                                        state_after=None,
                                        error="Element not found",
                                        failure_type="PERCEPTION_ERROR",
                                        root_cause=f"Could not resolve element for intent: {intent}",
                                        recovery_strategy="Try alternative selector or LLM vision",
                                    )
                        except Exception as interact_err:
                            logger.warning(f"Interaction failed for '{intent}': {interact_err}")
                            if session_memory:
                                session_memory.record_failure(
                                    action=f"click:{intent}",
                                    state_before={"url": page.url},
                                    state_after=None,
                                    error=str(interact_err),
                                    failure_type="EXECUTION_ERROR",
                                    root_cause=str(interact_err)[:200],
                                    recovery_strategy="Retry with different approach",
                                )

                        if interact_span and span_service:
                            interact_span = await span_service.end_span(
                                page, interact_span, artifacts_dir
                            )
                            action_spans.append(interact_span)

                else:
                    # No element intents — just wait longer for TA
                    await page.wait_for_timeout(1000)

                # Evaluate session with LLM-as-judge (best-effort)
                if session_memory and _memory_available:
                    try:
                        evaluator = get_session_evaluator()
                        evaluator.evaluate_session(session_memory)
                    except Exception:
                        pass  # Non-critical

            # Final screenshot
            ss_after = str(artifacts_dir / "after.png")
            await page.screenshot(path=ss_after, full_page=True)

            # Stop tracing
            trace_path = str(artifacts_dir / "trace.zip")
            await context.tracing.stop(path=trace_path)

            # Close to finalize video
            await page.close()
            await context.close()
            await browser.close()

            # Find video file
            video_path = None
            for f in artifacts_dir.iterdir():
                if f.suffix == ".webm":
                    video_path = str(f)
                    break

            # Write console logs
            console_path = str(artifacts_dir / "console.json")
            with open(console_path, "w") as f:
                json.dump(console_messages, f, indent=2)

            # Write network logs
            network_path = str(artifacts_dir / "network.json")
            with open(network_path, "w") as f:
                json.dump(network_entries, f, indent=2)

            # Save action spans manifest (Phase 3)
            action_spans_path = None
            if action_spans and span_service:
                action_spans_path = span_service.save_manifest(
                    action_spans, artifacts_dir
                )

            # Assemble artifacts
            evidence.artifacts = BenchmarkArtifacts(
                trace_path=trace_path if os.path.exists(trace_path) else None,
                video_path=video_path,
                screenshots=[ss_before, ss_after],
                console_path=console_path,
                network_path=network_path,
                action_spans_path=action_spans_path,
            )

            # Simple heuristic verdict — page loaded successfully
            has_errors = any(
                m["type"] == "error" for m in console_messages
                if "net::ERR" in m.get("text", "") or "Uncaught" in m.get("text", "")
            )
            if has_errors:
                evidence.status = RunStatus.FAIL
                evidence.verdict = BenchmarkVerdict(
                    label=BenchmarkVerdictLabel.BUG_FOUND,
                    confidence=0.6,
                    reason="Console errors detected during execution",
                )
            else:
                evidence.status = RunStatus.PASS
                evidence.verdict = BenchmarkVerdict(
                    label=BenchmarkVerdictLabel.SUCCESS,
                    confidence=0.7,
                    reason="Page loaded without critical console errors",
                )

    except Exception as e:
        logger.error(f"Task {task.task_id} failed: {e}")
        evidence.status = RunStatus.FAIL
        evidence.verdict = BenchmarkVerdict(
            label=BenchmarkVerdictLabel.INFRA_FAILURE,
            confidence=1.0,
            reason=str(e)[:200],
        )

    return evidence.finalize()


def _record_response(response, entries: List[Dict]):
    """Append response status to the matching network entry."""
    for entry in reversed(entries):
        if entry.get("url") == response.url and "status" not in entry:
            entry["status"] = response.status
            break


class BenchmarkRunner:
    """Orchestrate benchmark task execution across modes."""

    def __init__(
        self,
        evidence_writer: Optional[EvidenceWriter] = None,
        task_registry: Optional[WebTaskRegistry] = None,
    ):
        self.writer = evidence_writer or EvidenceWriter()
        self.registry = task_registry or WebTaskRegistry()

    async def run_task(
        self,
        task: BenchmarkTask,
        mode: AgentMode,
        suite_id: str,
    ) -> BenchmarkRunEvidence:
        """Run a single task in a single mode."""
        artifacts_dir = self.writer.artifacts_dir(suite_id, task.task_id, mode)

        reruns = 0
        evidence = None
        for attempt in range(task.max_reruns + 1):
            evidence = await _run_playwright_task(task, mode, artifacts_dir)
            if evidence.status == RunStatus.PASS:
                break
            reruns += 1
            logger.info(
                f"Task {task.task_id} (mode={mode.value}) attempt {attempt+1} failed, "
                f"retrying ({reruns}/{task.max_reruns})"
            )

        evidence.task_metrics.reruns = reruns
        self.writer.save_evidence(suite_id, evidence)

        # Sync to Convex real-time database (best-effort)
        try:
            _sync_evidence_to_convex(evidence, suite_id)
        except Exception:
            pass  # Non-critical

        return evidence

    async def run_suite(
        self,
        task_ids: Optional[List[str]] = None,
        modes: Optional[List[AgentMode]] = None,
        parallel: int = 2,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> BenchmarkScorecard:
        """
        Run a full benchmark suite.

        Returns a BenchmarkScorecard with per-task scores and mode comparison.
        """
        suite_id = str(uuid.uuid4())[:8]
        modes = modes or [AgentMode.CLAUDE_BASELINE, AgentMode.TEST_ASSURANCE]

        # Resolve tasks
        if task_ids:
            tasks = [self.registry.get(tid) for tid in task_ids]
            tasks = [t for t in tasks if t is not None]
        else:
            tasks = self.registry.list_tasks()

        if not tasks:
            logger.warning("No tasks to run")
            return BenchmarkScorecard(suite_id=suite_id)

        total = len(tasks) * len(modes)
        completed = 0

        # Save manifest
        manifest = {
            "suite_id": suite_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "task_count": len(tasks),
            "modes": [m.value for m in modes],
            "status": "running",
        }
        self.writer.save_suite_manifest(suite_id, manifest)

        # Run tasks
        all_evidences: List[BenchmarkRunEvidence] = []
        semaphore = asyncio.Semaphore(parallel)

        async def _run_one(task: BenchmarkTask, mode: AgentMode):
            nonlocal completed
            async with semaphore:
                ev = await self.run_task(task, mode, suite_id)
                all_evidences.append(ev)
                completed += 1
                if progress_callback:
                    progress_callback(
                        f"{task.task_id}:{mode.value}", completed, total
                    )

        coros = [_run_one(t, m) for t in tasks for m in modes]
        await asyncio.gather(*coros, return_exceptions=True)

        # Compute scorecard
        aggregator = ScorecardAggregator()
        scorecard = aggregator.compute_scorecard(suite_id, all_evidences)

        # Persist scorecard
        self.writer.save_scorecard(suite_id, scorecard.model_dump())

        # Update manifest
        manifest["status"] = "completed"
        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
        manifest["total_tasks_run"] = len(all_evidences)
        self.writer.save_suite_manifest(suite_id, manifest)

        return scorecard
