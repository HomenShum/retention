"""Autoresearch-style evaluation for recent orchestration updates."""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from ..qa_emulation import qa_emulation_agent
from ...observability.tracing import get_traced_client
from .progressive_disclosure import ProgressiveDisclosureLoader

logger = logging.getLogger(__name__)


class AgenticUpdateEvaluator:
    """Evaluate the QA emulation ↔ progressive disclosure integration."""

    TARGET_UPDATE = "qa_emulation_progressive_disclosure"

    def __init__(
        self,
        runs_dir: Optional[Path] = None,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        client: Any = None,
    ):
        backend_dir = Path(__file__).resolve().parents[3]
        self.agent_runs_dir = runs_dir or backend_dir / "data" / "agent_runs" / "agentic_updates"
        self.agent_runs_dir.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name or os.getenv("OPENAI_MODEL", "gpt-5.4")
        self.api_key = os.getenv("OPENAI_API_KEY") if api_key is None else api_key
        self._client = client

    def _build_check(self, name: str, passed: bool, details: str) -> Dict[str, Any]:
        return {"name": name, "passed": passed, "details": details}

    def _run_alias_activation_check(self) -> Dict[str, Any]:
        original_active_skills = list(qa_emulation_agent._active_skills)
        original_loader = qa_emulation_agent._loader

        try:
            qa_emulation_agent._active_skills.clear()
            qa_emulation_agent._loader = None
            activation_result = qa_emulation_agent._activate_skill("device_setup")
            passed = (
                "Requested alias 'device_setup'" in activation_result
                and "Loaded skill 'device_testing'." in activation_result
            )
            return self._build_check(
                name="alias_activation_contract",
                passed=passed,
                details=(
                    "Alias activation should resolve device_setup → device_testing and "
                    f"return repo-native load output. observed={activation_result[:180]!r}"
                ),
            )
        finally:
            qa_emulation_agent._active_skills[:] = original_active_skills
            qa_emulation_agent._loader = original_loader

    def run_static_checks(self) -> Dict[str, Any]:
        """Run deterministic checks for the latest agentic update."""
        loader = ProgressiveDisclosureLoader()
        metadata = loader.load_all_metadata()
        qa_context = loader.get_context_for_skill("qa_emulation", level=2)
        qa_doc = qa_context.get("skill_doc", "")
        matched_skill = loader.match_skill("Reproduce bug across regression builds and assemble verdict")

        checks = [
            self._build_check(
                name="qa_emulation_metadata_present",
                passed="qa_emulation" in metadata,
                details=f"Loaded {len(metadata)} skill metadata files.",
            ),
            self._build_check(
                name="qa_emulation_direct_context_loads",
                passed=(
                    qa_context.get("matched") is True
                    and qa_context.get("skill_name") == "qa_emulation"
                    and "skill_doc" in qa_context
                ),
                details="Expected direct Level-2 context load for repo-native skill qa_emulation.",
            ),
            self._build_check(
                name="qa_emulation_doc_documents_repo_native_loading",
                passed=(
                    "device_testing" in qa_doc
                    and "qa_emulation" in qa_doc
                    and "Legacy aliases" in qa_doc
                ),
                details="SKILL.md should prefer repo-native names while keeping legacy alias guidance.",
            ),
            self._build_check(
                name="qa_emulation_task_matching",
                passed=bool(matched_skill and matched_skill.name == "qa_emulation"),
                details="Regression-build reproduction query should route to qa_emulation.",
            ),
            self._build_check(
                name="alias_resolution_contract",
                passed=(
                    qa_emulation_agent._resolve_skill_name("device_setup") == "device_testing"
                    and qa_emulation_agent._resolve_skill_name("bug_detection") == "qa_emulation"
                    and qa_emulation_agent._resolve_skill_name("qa_emulation") == "qa_emulation"
                ),
                details="Expected legacy aliases to resolve without changing repo-native names.",
            ),
            self._run_alias_activation_check(),
        ]
        passed = all(check["passed"] for check in checks)
        return {
            "target": self.TARGET_UPDATE,
            "passed": passed,
            "checks": checks,
            "summary": "All deterministic QA emulation/progressive disclosure checks passed."
            if passed
            else "One or more deterministic checks failed.",
        }

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            client = OpenAI(api_key=self.api_key) if self.api_key else OpenAI()
            self._client = get_traced_client(client)
        return self._client

    def run_live_judge_if_configured(self, static_report: Dict[str, Any]) -> Dict[str, Any]:
        """Run an optional LLM judge using strict JSON schema."""
        if not self.api_key:
            return {"skipped": True, "reason": "OPENAI_API_KEY not configured."}

        schema = {
            "type": "object",
            "properties": {
                "passed": {"type": "boolean"},
                "confidence": {"type": "number"},
                "reasoning": {"type": "string"},
                "issues": {"type": "array", "items": {"type": "string"}},
                "suggestions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["passed", "confidence", "reasoning", "issues", "suggestions"],
            "additionalProperties": False,
        }
        prompt_payload = {
            "target_update": self.TARGET_UPDATE,
            "evaluation_style": "autoresearch-inspired bounded judge",
            "static_evaluation": static_report,
            "criteria": [
                "Prefer repo-native orchestration contracts over parallel foreign abstractions.",
                "Keep docs, runtime alias behavior, and loader wiring aligned.",
                "Flag regressions only when supported by the deterministic evidence.",
            ],
        }

        try:
            response = self._get_client().responses.create(
                model=self.model_name,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "You are a strict reviewer for a small agentic update. Judge whether the "
                            "update should be kept based on the provided deterministic evidence only."
                        ),
                    },
                    {"role": "user", "content": json.dumps(prompt_payload, indent=2)},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "agentic_update_judgment",
                        "schema": schema,
                        "strict": True,
                    }
                },
                store=False,
            )
            judgment = json.loads(response.output_text)
            return {
                "skipped": False,
                "model": self.model_name,
                "judgment": judgment,
            }
        except Exception as exc:
            logger.warning("Live judge failed: %s", exc)
            return {
                "skipped": False,
                "model": self.model_name,
                "error": str(exc),
                "judgment": {
                    "passed": False,
                    "confidence": 0.0,
                    "reasoning": f"Live judge failed: {exc}",
                    "issues": ["live_judge_failed"],
                    "suggestions": ["Re-run with a valid OpenAI configuration if live judging is required."],
                },
            }

    def build_report(self) -> Dict[str, Any]:
        """Build a structured evaluation report."""
        static_report = self.run_static_checks()
        live_judge = self.run_live_judge_if_configured(static_report)
        live_passed = live_judge.get("skipped") or live_judge.get("judgment", {}).get("passed", False)
        return {
            "run_id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "target_update": self.TARGET_UPDATE,
            "static_evaluation": static_report,
            "live_judge": live_judge,
            "overall_passed": bool(static_report.get("passed") and live_passed),
        }

    def persist_report(self, report: Dict[str, Any]) -> Optional[Path]:
        """Persist a report artifact under backend/data/agent_runs/."""
        path = self.agent_runs_dir / f"agentic_update_eval_{report['run_id']}.json"
        try:
            path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            return path
        except Exception as exc:
            logger.warning("Failed to persist agentic update evaluation %s: %s", report["run_id"], exc)
            return None

    def run_and_persist(self) -> tuple[Dict[str, Any], Optional[Path]]:
        """Run the evaluation and persist the resulting artifact."""
        report = self.build_report()
        path = self.persist_report(report)
        return report, path