"""
QA Emulation Service - Workflow State Machine with Parallel Extraction

Code-managed orchestration for the QA bug reproduction workflow.
Follows the same patterns as:
- prd_parser_service.py: asyncio.gather for parallel subagent execution
- coordinator_service.py: semaphore-based concurrency + simulation engine
- orchestration/run_session.py: step evaluation with retry

The workflow is DETERMINISTIC (code-managed), not LLM-directed:
  LEASE_DEVICE → LOGIN → LOAD_BUILD_OG → REPRO_ON_OG → ...
  → LOAD_BUILD_RB3 → REPRO_ON_RB3 → GATHER_EVIDENCE → ASSEMBLE_VERDICT

Within each phase, the LLM handles reasoning and tool calls.
Between phases, code controls the flow.

Resilience (addressing 74.6% failure rate feedback):
- Per-build retry with exponential backoff (max 3 attempts)
- Graceful degradation: failed builds produce INCONCLUSIVE, don't abort run
- Timeout protection per analysis call
- Batch test-case ingestion to reduce manual friction
"""

import asyncio
import logging
import json
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from agents import Runner

from .models.verdict_models import (
    QAReproVerdict,
    AnomalyResult,
    BuildEvidence,
    EvidenceItem,
    WorkflowPhase,
    QAEmulationConfig,
    RunTelemetry,
    MODEL_PRICING,
)
from .subagents import (
    create_bug_detection_agent,
    create_anomaly_detection_agent,
    create_verdict_assembly_agent,
)
from .qa_emulation_agent import create_qa_emulation_agent

logger = logging.getLogger(__name__)


# Default resilience settings
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_BASE = 2.0  # seconds
DEFAULT_ANALYSIS_TIMEOUT = 120.0  # seconds per analysis call


class QAEmulationService:
    """
    Orchestrates the QA bug reproduction workflow.

    Code-managed build sequence with parallel LLM extraction.
    Pattern: Anthropic's orchestrator-worker with deterministic flow.

    Resilience features (addressing 74.6% failure rate):
    - Per-build retry with exponential backoff
    - Graceful degradation on individual build failures
    - Analysis timeout protection
    - Batch test-case ingestion
    """

    def __init__(
        self,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_base: float = DEFAULT_RETRY_BACKOFF_BASE,
        analysis_timeout: float = DEFAULT_ANALYSIS_TIMEOUT,
    ):
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._max_retries = max_retries
        self._retry_backoff_base = retry_backoff_base
        self._analysis_timeout = analysis_timeout

    @staticmethod
    def _extract_usage(result, telemetry: RunTelemetry, model: str = "gpt-5.4") -> None:
        """Extract token usage from a Runner.run result and accumulate into telemetry.

        Follows the coordinator_service.py pattern:
          result.context_wrapper.usage → {requests, input_tokens, output_tokens, total_tokens}
        """
        if not hasattr(result, 'context_wrapper') or not hasattr(result.context_wrapper, 'usage'):
            return
        usage = result.context_wrapper.usage
        input_tokens = getattr(usage, 'input_tokens', 0) or 0
        output_tokens = getattr(usage, 'output_tokens', 0) or 0
        total = getattr(usage, 'total_tokens', 0) or (input_tokens + output_tokens)
        requests = getattr(usage, 'requests', 0) or 0
        reasoning = 0
        if hasattr(usage, 'output_tokens_details') and usage.output_tokens_details:
            reasoning = getattr(usage.output_tokens_details, 'reasoning_tokens', 0) or 0

        telemetry.total_requests += requests
        telemetry.total_input_tokens += input_tokens
        telemetry.total_output_tokens += output_tokens
        telemetry.total_tokens += total
        telemetry.reasoning_tokens += reasoning

        # Per-model cost
        pricing = MODEL_PRICING.get(model, MODEL_PRICING.get("gpt-5.4", {}))
        cost = (input_tokens * pricing.get("input", 0) + output_tokens * pricing.get("output", 0)) / 1_000_000
        telemetry.estimated_cost_usd += cost

        # Breakdown
        if model not in telemetry.model_breakdown:
            telemetry.model_breakdown[model] = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        telemetry.model_breakdown[model]["input_tokens"] += input_tokens
        telemetry.model_breakdown[model]["output_tokens"] += output_tokens
        telemetry.model_breakdown[model]["cost_usd"] += cost

    async def run_emulation(
        self,
        config: QAEmulationConfig,
        bug_description: str,
        repro_steps: List[str],
    ) -> tuple[QAReproVerdict, RunTelemetry]:
        """
        Execute the full QA emulation workflow.

        Args:
            config: Emulation configuration (prompt version, builds, etc.)
            bug_description: The bug report to reproduce
            repro_steps: Steps to reproduce the bug

        Returns:
            Tuple of (QAReproVerdict, RunTelemetry)
        """
        run_id = f"qa-{config.task_id}-{int(time.time())}"
        logger.info(f"[QA Emulation] Starting run {run_id} (version={config.prompt_version})")

        # Initialize run state
        run_state = {
            "run_id": run_id,
            "config": config.model_dump(),
            "status": "running",
            "current_phase": WorkflowPhase.LEASE_DEVICE.value,
            "build_results": {},
            "all_evidence": [],
            "all_anomalies": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self._runs[run_id] = run_state

        # Create specialist subagents with configured reasoning effort
        reasoning_effort = config.reasoning_effort
        bug_agent = create_bug_detection_agent(reasoning_effort=reasoning_effort)
        anomaly_agent = create_anomaly_detection_agent()  # vision tier, no reasoning param
        verdict_agent = create_verdict_assembly_agent(reasoning_effort=reasoning_effort)

        # Telemetry accumulator
        telemetry = RunTelemetry()

        # Collect results across builds
        build_results: List[BuildEvidence] = []

        try:
            # Phase: Test each build in sequence (code-managed)
            # Mirrors QA team process: OG → RB1 → RB2 → RB3
            for build_id in config.builds:
                logger.info(f"[QA Emulation] Testing build: {build_id}")
                run_state["current_phase"] = f"REPRO_ON_{build_id}"

                # Retry loop per build (addresses 74.6% failure rate)
                bug_result = None
                anomaly_result = None
                last_error = None

                for attempt in range(1, self._max_retries + 1):
                    try:
                        build_evidence = await self._test_build(
                            run_id=run_id,
                            build_id=build_id,
                            bug_description=bug_description,
                            repro_steps=repro_steps,
                            config=config,
                        )

                        # Parallel extraction with timeout protection
                        bug_result, anomaly_result = await asyncio.wait_for(
                            self._parallel_analysis(
                                bug_agent=bug_agent,
                                anomaly_agent=anomaly_agent,
                                build_id=build_id,
                                bug_description=bug_description,
                                build_evidence=build_evidence,
                                use_parallel=config.parallel_extraction,
                                telemetry=telemetry,
                            ),
                            timeout=self._analysis_timeout,
                        )
                        last_error = None
                        break  # Success — exit retry loop

                    except (asyncio.TimeoutError, Exception) as e:
                        last_error = e
                        if attempt < self._max_retries:
                            backoff = self._retry_backoff_base ** attempt
                            logger.warning(
                                f"[QA Emulation] Build {build_id} attempt {attempt}/{self._max_retries} "
                                f"failed: {e}. Retrying in {backoff:.1f}s..."
                            )
                            await asyncio.sleep(backoff)
                        else:
                            logger.error(
                                f"[QA Emulation] Build {build_id} failed after {self._max_retries} attempts: {e}"
                            )

                # Graceful degradation: failed builds produce INCONCLUSIVE
                if bug_result is None:
                    bug_result = {
                        "error": str(last_error),
                        "bug_detected": False,
                        "classification": "INCONCLUSIVE",
                        "rationale": f"Build analysis failed after {self._max_retries} retries: {last_error}",
                    }
                if anomaly_result is None:
                    anomaly_result = {
                        "error": str(last_error),
                        "category": "NO_ISSUE",
                        "blocks_reproduction": False,
                    }

                # Record results
                build_ev = BuildEvidence(
                    build_id=build_id,
                    reproduced=bug_result.get("bug_detected", False),
                    evidence_items=[],
                    repro_steps_taken=repro_steps,
                    anomalies_found=[],
                    notes=bug_result.get("rationale", ""),
                )
                build_results.append(build_ev)
                run_state["build_results"][build_id] = {
                    "bug_result": bug_result,
                    "anomaly_result": anomaly_result,
                    "retries_used": attempt if last_error is None else self._max_retries,
                }

                # Check for blocking anomaly
                if anomaly_result.get("blocks_reproduction", False):
                    logger.warning(f"[QA Emulation] Build {build_id}: reproduction blocked by new bug")
                    break

            # Phase: Assemble verdict
            run_state["current_phase"] = WorkflowPhase.ASSEMBLE_VERDICT.value
            verdict = await self._assemble_verdict(
                verdict_agent=verdict_agent,
                task_id=config.task_id,
                bug_description=bug_description,
                build_results=run_state["build_results"],
                telemetry=telemetry,
            )

            run_state["status"] = "completed"
            run_state["completed_at"] = datetime.now(timezone.utc).isoformat()
            run_state["telemetry"] = telemetry.model_dump()
            logger.info(
                f"[QA Emulation] Run {run_id} completed: {verdict.verdict.value} | "
                f"tokens={telemetry.total_tokens} cost=${telemetry.estimated_cost_usd:.4f}"
            )
            return verdict, telemetry

        except Exception as e:
            logger.error(f"[QA Emulation] Run {run_id} failed: {e}")
            run_state["status"] = "failed"
            run_state["error"] = str(e)
            # Return INSUFFICIENT_EVIDENCE on failure
            return QAReproVerdict(
                verdict="INSUFFICIENT_EVIDENCE",
                confidence=0.0,
                rationale=f"Emulation failed: {e}",
                build_results=build_results,
            ), telemetry

    async def _test_build(
        self,
        run_id: str,
        build_id: str,
        bug_description: str,
        repro_steps: List[str],
        config: QAEmulationConfig,
    ) -> Dict[str, Any]:
        """
        Test a single build. Returns raw evidence dict.

        In production, this would:
        1. Load the build onto the device
        2. Execute repro steps via device testing tools
        3. Capture screenshots and logs

        For now, returns a structured prompt for LLM analysis.
        """
        evidence = {
            "build_id": build_id,
            "bug_description": bug_description,
            "repro_steps": repro_steps,
            "device_id": config.device_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(f"[QA Emulation] Build {build_id} test evidence collected")
        return evidence

    async def _parallel_analysis(
        self,
        bug_agent,
        anomaly_agent,
        build_id: str,
        bug_description: str,
        build_evidence: Dict[str, Any],
        use_parallel: bool = True,
        telemetry: Optional[RunTelemetry] = None,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Run bug detection and anomaly detection in parallel.

        Follows prd_parser_service.py pattern: asyncio.gather over
        two Runner.run calls to separate specialist agents.

        Args:
            bug_agent: Bug Detection Specialist agent
            anomaly_agent: Anomaly Detection Specialist agent
            build_id: Current build being analyzed
            bug_description: Expected bug to find
            build_evidence: Evidence collected from this build
            use_parallel: Whether to run in parallel (True) or sequential
            telemetry: RunTelemetry accumulator for cost tracking

        Returns:
            Tuple of (bug_result_dict, anomaly_result_dict)
        """
        # Build prompts for each specialist
        bug_prompt = json.dumps({
            "bug_description": bug_description,
            "build_id": build_id,
            "evidence": build_evidence,
            "repro_steps": build_evidence.get("repro_steps", []),
            "expected_behavior": f"Bug should be present on build {build_id}",
        }, indent=2)

        anomaly_prompt = json.dumps({
            "expected_bug": bug_description,
            "build_id": build_id,
            "evidence": build_evidence,
            "current_phase": f"REPRO_ON_{build_id}",
            "previous_states": [],
        }, indent=2)

        async def run_bug_detection() -> Dict[str, Any]:
            try:
                result = await Runner.run(bug_agent, bug_prompt, max_turns=5)
                if telemetry:
                    self._extract_usage(result, telemetry, model="gpt-5.4")
                return json.loads(result.final_output) if isinstance(result.final_output, str) else {}
            except Exception as e:
                logger.error(f"[QA Emulation] Bug detection failed for {build_id}: {e}")
                return {"error": str(e), "bug_detected": False, "classification": "INCONCLUSIVE"}

        async def run_anomaly_detection() -> Dict[str, Any]:
            try:
                result = await Runner.run(anomaly_agent, anomaly_prompt, max_turns=5)
                if telemetry:
                    self._extract_usage(result, telemetry, model="gpt-5-mini")
                return json.loads(result.final_output) if isinstance(result.final_output, str) else {}
            except Exception as e:
                logger.error(f"[QA Emulation] Anomaly detection failed for {build_id}: {e}")
                return {"error": str(e), "category": "NO_ISSUE", "blocks_reproduction": False}

        if use_parallel:
            logger.info(f"[QA Emulation] Running parallel analysis for build {build_id}")
            bug_result, anomaly_result = await asyncio.gather(
                run_bug_detection(),
                run_anomaly_detection(),
            )
        else:
            logger.info(f"[QA Emulation] Running sequential analysis for build {build_id}")
            bug_result = await run_bug_detection()
            anomaly_result = await run_anomaly_detection()

        return bug_result, anomaly_result

    async def _assemble_verdict(
        self,
        verdict_agent,
        task_id: str,
        bug_description: str,
        build_results: Dict[str, Any],
        telemetry: Optional[RunTelemetry] = None,
    ) -> QAReproVerdict:
        """
        Use the Verdict Assembly Specialist to produce final verdict.

        The verdict agent has output_type=QAReproVerdict, so the SDK
        enforces the Pydantic schema automatically.
        """
        verdict_prompt = json.dumps({
            "task_id": task_id,
            "bug_description": bug_description,
            "build_results": build_results,
            "all_evidence_ids": [],
            "all_anomalies": [],
            "workflow_notes": "Code-managed workflow completed all phases.",
        }, indent=2)

        try:
            result = await Runner.run(verdict_agent, verdict_prompt, max_turns=5)
            if telemetry:
                self._extract_usage(result, telemetry, model="gpt-5.4")
            # output_type=QAReproVerdict means result.final_output is already typed
            if isinstance(result.final_output, QAReproVerdict):
                return result.final_output
            # Fallback: parse from string
            data = json.loads(result.final_output) if isinstance(result.final_output, str) else {}
            return QAReproVerdict(**data)
        except Exception as e:
            logger.error(f"[QA Emulation] Verdict assembly failed: {e}")
            return QAReproVerdict(
                verdict="INSUFFICIENT_EVIDENCE",
                confidence=0.0,
                rationale=f"Verdict assembly failed: {e}",
            )

    async def run_batch(
        self,
        test_cases: List[Dict[str, Any]],
        config: QAEmulationConfig,
    ) -> List[tuple[QAReproVerdict, RunTelemetry]]:
        """
        Run multiple test cases in batch (reduces manual friction).

        Each test case dict should have:
        - bug_description: str
        - repro_steps: List[str]
        - task_id: str (optional, overrides config.task_id)

        Args:
            test_cases: List of test case dicts
            config: Shared emulation configuration

        Returns:
            List of (QAReproVerdict, RunTelemetry) tuples, one per test case
        """
        logger.info(f"[QA Emulation] Batch run: {len(test_cases)} test cases")
        results: List[tuple[QAReproVerdict, RunTelemetry]] = []

        for i, tc in enumerate(test_cases):
            tc_config = config.model_copy()
            if "task_id" in tc:
                tc_config.task_id = tc["task_id"]
            else:
                tc_config.task_id = f"{config.task_id}-batch-{i}"

            logger.info(f"[QA Emulation] Batch [{i+1}/{len(test_cases)}]: {tc_config.task_id}")
            verdict, telemetry = await self.run_emulation(
                config=tc_config,
                bug_description=tc["bug_description"],
                repro_steps=tc.get("repro_steps", []),
            )
            results.append((verdict, telemetry))

        total_cost = sum(t.estimated_cost_usd for _, t in results)
        logger.info(
            f"[QA Emulation] Batch complete: "
            f"{sum(1 for v, _ in results if v.verdict.value == 'REPRODUCIBLE')}/{len(results)} reproducible | "
            f"total_cost=${total_cost:.4f}"
        )
        return results

    def get_run_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Get the current status of an emulation run."""
        return self._runs.get(run_id)

    def list_runs(self) -> List[Dict[str, Any]]:
        """List all emulation runs with summary info."""
        return [
            {
                "run_id": run["run_id"],
                "status": run["status"],
                "current_phase": run.get("current_phase"),
                "started_at": run.get("started_at"),
                "completed_at": run.get("completed_at"),
            }
            for run in self._runs.values()
        ]

