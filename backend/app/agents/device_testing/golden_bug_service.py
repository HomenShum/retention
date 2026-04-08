import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from .bug_reproduction_service import BugReproductionResult
from .golden_bug_models import (
    GoldenBugDefinition,
    GoldenBugSummary,
    GoldenBugRunResult,
    GoldenBugAttemptResult,
    GoldenBugAutoCheck,
    GoldenBugOutcome,
    GoldenBugEvaluationMetrics,
    GoldenBugEvaluationReport,
    now_iso_utc,
)
from .infrastructure import MCPAppiumClient, Platform

logger = logging.getLogger(__name__)


class GoldenBugService:
    """Service for loading, running, and evaluating golden bugs."""

    def __init__(self, bug_repro_service, capabilities: Dict[str, object]):
        self.bug_repro_service = bug_repro_service
        self.capabilities = capabilities or {}

        backend_dir = Path(__file__).resolve().parents[3]
        self.golden_path = backend_dir / "data" / "golden_bugs.json"
        self.screenshots_dir = backend_dir / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.agent_runs_dir = backend_dir / "data" / "agent_runs" / "golden"
        self.agent_runs_dir.mkdir(parents=True, exist_ok=True)
        self.capabilities_path = backend_dir / "capabilities.json"
        self.android_home = os.getenv("ANDROID_HOME")

        # LLM planning configuration (aligned with coordinator defaults)
        # January 2026: Use gpt-5-mini for golden bug verification (gpt-5-nano fallback)
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model_name = os.getenv("OPENAI_MODEL", "gpt-5-mini")

        self._bugs: Dict[str, GoldenBugDefinition] = self._load_golden_bugs()
        logger.info(f"Loaded {len(self._bugs)} golden bugs from {self.golden_path}")

    def _load_golden_bugs(self) -> Dict[str, GoldenBugDefinition]:
        if not self.golden_path.exists():
            logger.warning(f"Golden bugs file not found: {self.golden_path}")
            return {}
        try:
            raw = json.loads(self.golden_path.read_text(encoding="utf-8"))
            bugs: Dict[str, GoldenBugDefinition] = {}
            for item in raw:
                bug = GoldenBugDefinition.model_validate(item)
                bugs[bug.bug_id] = bug
            return bugs
        except Exception as e:
            logger.error(f"Failed to load golden bugs: {e}")
            return {}

    def list_golden_bug_summaries(self) -> List[GoldenBugSummary]:
        summaries: List[GoldenBugSummary] = []
        for bug in self._bugs.values():
            br = bug.bug_report
            summaries.append(
                GoldenBugSummary(
                    bug_id=bug.bug_id,
                    name=bug.name,
                    description=bug.description,
                    expected_outcome=bug.auto_check.expectation,
                    device_id=br.device_id,
                    severity=br.severity,
                    tags=br.tags,
                )
            )
        return summaries

    def get_golden_bug(self, bug_id: str) -> GoldenBugDefinition:
        bug = self._bugs.get(bug_id)
        if not bug:
            raise KeyError(f"Golden bug '{bug_id}' not found")
        return bug

    def _copy_latest_screenshot(self, result: BugReproductionResult, bug_id: str, attempt_index: int) -> Optional[str]:
        latest_path: Optional[str] = None
        for ev in reversed(result.evidence):
            if ev.screenshot_path:
                latest_path = ev.screenshot_path
                break
        if not latest_path or not os.path.exists(latest_path):
            return None
        src = Path(latest_path)
        dest_name = f"{bug_id.lower()}_attempt_{attempt_index}.png"
        dest = self.screenshots_dir / dest_name
        try:
            shutil.copy2(src, dest)
            logger.info(f"Copied golden screenshot to {dest}")
            return f"/static/screenshots/{dest.name}"
        except Exception as e:
            logger.warning(f"Failed to copy screenshot {src} -> {dest}: {e}")
            return None

    def _evaluate_auto_check(self, auto: GoldenBugAutoCheck, result: BugReproductionResult) -> tuple[bool, str]:
        expected = auto.expectation
        success = result.reproduction_successful
        analysis = (result.ai_analysis or "").lower()
        analysis_ok = all(token.lower() in analysis for token in auto.require_text_in_analysis)
        if expected == GoldenBugOutcome.REPRODUCED:
            base_ok = success
            expected_str = "bug reproduces"
        else:
            base_ok = not success
            expected_str = "bug does NOT reproduce"
        passed = bool(base_ok and analysis_ok)
        reason = (
            f"Expected {expected_str}; "
            f"reproduction_successful={success}; analysis_ok={analysis_ok}."
        )
        return passed, reason

    def _pre_device_plan_and_judge(self, bug: GoldenBugDefinition) -> tuple[bool, str]:
        """Pre-device planning + boolean judge stage for a golden bug.

        This stage runs **without touching any real device**. It uses a
        combination of simple static checks and an LLM "planning judge" to
        decide whether the golden bug definition is ready for execution.

        Returns:
            (planning_passed, reason)
        """

        report = bug.bug_report

        # --- Static sanity checks (cheap, deterministic) ---
        static_issues: list[str] = []

        if not report.device_id:
            static_issues.append("Missing device_id on bug_report")
        if not report.reproduction_steps:
            static_issues.append("No reproduction_steps defined on bug_report")
        if not report.app_package:
            static_issues.append("Missing app_package on bug_report")

        caps_android = self.capabilities.get("android", {}) if hasattr(self, "capabilities") else {}
        capability_notes: list[str] = []
        default_udid = caps_android.get("appium:udid")
        if default_udid and report.device_id and report.device_id != default_udid:
            capability_notes.append(
                f"device_id '{report.device_id}' differs from default capability udid '{default_udid}'"
            )

        static_ok = not static_issues

        # If we have no API key, fall back to static checks only
        if not getattr(self, "api_key", None):
            if static_ok:
                return True, "Planning OK (LLM disabled; static checks only)."
            return False, "Planning failed (LLM disabled): " + "; ".join(static_issues)

        # --- LLM-based planning judge using OpenAI Responses API ---
        try:
            import json as _json
            import openai
            from ...observability.tracing import get_traced_client

            payload = {
                "bug_id": bug.bug_id,
                "name": bug.name,
                "description": bug.description,
                "bug_report": report.model_dump(),
                "static_issues": static_issues,
                "capability_notes": capability_notes,
            }

            prompt = (
                "You are the **Pre-Device Planning Judge** for deterministic golden bugs.\n"
                "You receive a JSON object describing a golden bug definition and must decide if it is\n"
                "ready to run on a real device.\n\n"
                "RULES:\n"
                "- Use ONLY boolean reasoning, no scores.\n"
                "- Consider whether the device/app information and steps are sufficient and consistent.\n"
                "- If there are minor capability notes but everything is still runnable, planning can be OK.\n"
                "- If required fields are missing (device_id, steps, app_package), planning must FAIL.\n\n"
                "Return ONLY a compact JSON object with this exact shape (no extra fields, no prose):\n"
                "{\n"
                "  \"planning_ok\": true | false,\n"
                "  \"environment_ok\": true | false,\n"
                "  \"steps_ok\": true | false,\n"
                "  \"reason\": \"short explanation string\"\n"
                "}\n\n"
                "BUG_CONTEXT:\n" + _json.dumps(payload, indent=2)
            )

            client = get_traced_client(openai.OpenAI(api_key=self.api_key))
            model_name = getattr(self, "model_name", None) or "gpt-5-mini"

            # Using the Responses API per OpenAI migration guidance
            resp = client.responses.create(
                model=model_name,
                input=prompt,
                store=False,
                reasoning={"effort": "minimal"},
            )

            text = getattr(resp, "output_text", None)
            if not text and getattr(resp, "output", None):  # defensive parsing
                try:
                    first_item = resp.output[0]
                    if getattr(first_item, "content", None):
                        text = first_item.content[0].text
                except Exception:
                    text = None

            if not text:
                raise RuntimeError("LLM planning judge returned empty output")

            try:
                parsed = _json.loads(text)
            except _json.JSONDecodeError:
                # Try to recover JSON substring
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end != -1 and end > start:
                    parsed = _json.loads(text[start : end + 1])
                else:
                    raise

            planning_ok = bool(parsed.get("planning_ok"))
            reason = parsed.get("reason") or "LLM planning judge did not provide a reason."
            return planning_ok, reason

        except Exception as e:  # pragma: no cover - best-effort safety
            logger.warning(f"LLM planning judge failed: {e}", exc_info=True)
            if static_ok:
                return True, "Planning OK (LLM judge failed; static checks only)."
            return False, "Planning failed (LLM judge failed; static issues: " + "; ".join(static_issues) + ")"

    async def run_golden_bug(
        self,
        bug_id: str,
        device_id_override: Optional[str] = None,
        max_attempts: int = 3,
    ) -> GoldenBugRunResult:
        bug = self.get_golden_bug(bug_id)

        # --- Pre-device planning + boolean judge stage (no device access) ---
        planning_passed, planning_reason = self._pre_device_plan_and_judge(bug)

        bug_report = bug.bug_report.model_copy(deep=True)
        if device_id_override:
            bug_report.device_id = device_id_override

        android_caps = dict(self.capabilities.get("android", {}))
        if bug_report.device_id:
            android_caps["appium:udid"] = bug_report.device_id

        attempts: List[GoldenBugAttemptResult] = []

        # If planning fails, we skip on-device execution and record a failed run
        if planning_passed:
            client = MCPAppiumClient(
                capabilities_config=str(self.capabilities_path),
                android_home=self.android_home,
            )

            try:
                started = await client.start()
                if not started:
                    raise RuntimeError("Failed to start MCP Appium client for golden run")
                await client.select_platform(Platform.ANDROID)

                for attempt_index in range(1, max_attempts + 1):
                    session_id = await client.create_session(android_caps)
                    if not session_id:
                        logger.error("Failed to create Appium session for golden bug")
                        break

                    result = await self.bug_repro_service.reproduce_bug(
                        bug_report=bug_report,
                        mcp_client=client,
                        session_id=session_id,
                    )

                    # Close session between attempts to keep runs isolated
                    try:
                        await client.close_session()
                    except Exception:
                        logger.debug("close_session failed; continuing")

                    auto_passed, reason = self._evaluate_auto_check(bug.auto_check, result)
                    screenshot_url = self._copy_latest_screenshot(result, bug.bug_id, attempt_index)

                    attempts.append(
                        GoldenBugAttemptResult(
                            attempt_index=attempt_index,
                            reproduction_successful=result.reproduction_successful,
                            auto_check_passed=auto_passed,
                            auto_check_reason=reason,
                            bug_reproduction_result=result,
                            screenshot_url=screenshot_url,
                        )
                    )

                    if auto_passed:
                        break

            finally:
                try:
                    await client.close()
                except Exception:
                    logger.debug("Error closing MCP Appium client after golden run", exc_info=True)

        any_passed = any(a.auto_check_passed for a in attempts)
        expected = bug.auto_check.expectation
        if expected == GoldenBugOutcome.REPRODUCED:
            classification = "TP" if any_passed else "FN"
        else:
            classification = "TN" if any_passed else "FP"

        run = GoldenBugRunResult(
            bug_id=bug.bug_id,
            name=bug.name,
            expected_outcome=bug.auto_check.expectation,
            planning_passed=planning_passed,
            planning_reason=planning_reason,
            attempts=attempts,
            passed=any_passed,
            classification=classification,
            created_at=now_iso_utc(),
        )
        self._persist_run(run)
        return run

    async def run_all_golden_bugs(
        self,
        device_id_override: Optional[str] = None,
        max_attempts: int = 3,
    ) -> GoldenBugEvaluationReport:
        runs: List[GoldenBugRunResult] = []
        for bug_id in sorted(self._bugs.keys()):
            run = await self.run_golden_bug(bug_id, device_id_override=device_id_override, max_attempts=max_attempts)
            runs.append(run)

        total = len(runs)
        bugs_passed = sum(1 for r in runs if r.passed)
        tp = sum(1 for r in runs if r.classification == "TP")
        fp = sum(1 for r in runs if r.classification == "FP")
        tn = sum(1 for r in runs if r.classification == "TN")
        fn = sum(1 for r in runs if r.classification == "FN")

        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

        metrics = GoldenBugEvaluationMetrics(
            total_bugs=total,
            bugs_passed=bugs_passed,
            true_positives=tp,
            false_positives=fp,
            true_negatives=tn,
            false_negatives=fn,
            precision=precision,
            recall=recall,
            f1=f1,
        )

        report = GoldenBugEvaluationReport(
            run_id=str(uuid.uuid4()),
            created_at=now_iso_utc(),
            metrics=metrics,
            runs=runs,
        )
        self._persist_eval(report)
        return report

    def _persist_run(self, run: GoldenBugRunResult) -> None:
        run_name = f"{run.bug_id}_{int(uuid.uuid4().int % 1_000_000)}.json"
        path = self.agent_runs_dir / run_name
        try:
            path.write_text(json.dumps(run.model_dump(), indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to persist golden bug run {run.bug_id}: {e}")

    def _persist_eval(self, report: GoldenBugEvaluationReport) -> None:
        path = self.agent_runs_dir / f"golden_eval_{report.run_id}.json"
        try:
            path.write_text(json.dumps(report.model_dump(), indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to persist golden evaluation report {report.run_id}: {e}")

