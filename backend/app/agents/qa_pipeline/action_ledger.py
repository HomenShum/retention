"""ActionLedger — per-action telemetry for every pipeline run.

Captures every tool call, state transition, memory hit, and verdict
into the canonical dogfood telemetry schema. Persisted per run.

Usage:
    ledger = ActionLedger(run_id="web-abc123", workflow_family="kyb_aml")
    ledger.record_action(tool_name="browser.click", input_summary="...", ...)
    ledger.record_stage_transition("CRAWL", "WORKFLOW")
    ledger.record_memory_hit("structural_memory", tokens_saved=31000)
    ledger.save()  # Persists to data/action_ledger/{run_id}.json
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LEDGER_DIR = Path(__file__).resolve().parents[3] / "data" / "action_ledger"
_LEDGER_DIR.mkdir(parents=True, exist_ok=True)


class ActionLedger:
    """Collects per-action telemetry for a pipeline run."""

    def __init__(
        self,
        run_id: str,
        workflow_family: str = "mobile_app",
        scenario_id: str = "",
        setup_variant: str = "B_ta_harness",
        runtime: str = "internal_deep_agent",
        model: str = "gpt-5.4-mini",
        surface: str = "browser",
        app_name: str = "",
    ):
        self.run_id = run_id
        self.workflow_family = workflow_family
        self.scenario_id = scenario_id
        self.setup_variant = setup_variant
        self.runtime = runtime
        self.model = model
        self.surface = surface
        self.app_name = app_name

        self.actions: List[Dict[str, Any]] = []
        self.action_index = 0
        self.current_stage = ""
        self.stage_transitions: List[Dict[str, Any]] = []
        self.memory_hits: List[Dict[str, Any]] = []
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self._pending_calls: Dict[str, Dict[str, Any]] = {}  # call_id → partial action
        self._start_time = time.time()

    def record_tool_call_start(
        self,
        tool_name: str,
        tool_input: str = "",
        call_id: str = "",
        page_url: str = "",
    ):
        """Record the start of a tool call."""
        action = {
            "run_id": self.run_id,
            "workflow_family": self.workflow_family,
            "scenario_id": self.scenario_id,
            "setup_variant": self.setup_variant,
            "runtime": self.runtime,
            "model": self.model,
            "surface": self.surface,
            "action_index": self.action_index,
            "tool_name": tool_name,
            "tool_call_id": call_id,
            "input_summary": str(tool_input)[:200],
            "output_summary": "",
            "timestamp_start": datetime.now(timezone.utc).isoformat(),
            "timestamp_end": "",
            "latency_ms": 0,
            "page_or_screen_id": "",
            "page_url_or_app_screen": page_url,
            "state_before_id": "",
            "state_after_id": "",
            "screenshot_id": "",
            "artifact_ids": [],
            "memory_hit": False,
            "memory_layer": "none",
            "expected_outcome": "",
            "observed_outcome": "",
            "verdict": "",
            "tokens_in": 0,
            "tokens_out": 0,
            "estimated_cost_usd": 0.0,
            "rerun_of_run_id": None,
            "human_judge_score": None,
            "llm_judge_score": None,
            "stage": self.current_stage,
        }

        if call_id:
            self._pending_calls[call_id] = action
        else:
            self.actions.append(action)
            self.action_index += 1

    def record_tool_call_end(
        self,
        call_id: str = "",
        tool_name: str = "",
        output_summary: str = "",
        duration_ms: int = 0,
        verdict: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
    ):
        """Complete a pending tool call with its output."""
        if call_id and call_id in self._pending_calls:
            action = self._pending_calls.pop(call_id)
            action["output_summary"] = str(output_summary)[:200]
            action["timestamp_end"] = datetime.now(timezone.utc).isoformat()
            action["latency_ms"] = duration_ms
            action["verdict"] = verdict
            action["tokens_in"] = tokens_in
            action["tokens_out"] = tokens_out
            self.total_tokens_in += tokens_in
            self.total_tokens_out += tokens_out
            self.actions.append(action)
            self.action_index += 1
        else:
            # No pending call — record as standalone
            self.actions.append({
                "run_id": self.run_id,
                "action_index": self.action_index,
                "tool_name": tool_name,
                "output_summary": str(output_summary)[:200],
                "timestamp_end": datetime.now(timezone.utc).isoformat(),
                "latency_ms": duration_ms,
                "stage": self.current_stage,
            })
            self.action_index += 1

    def record_stage_transition(self, from_stage: str, to_stage: str):
        """Record a stage transition."""
        self.current_stage = to_stage
        self.stage_transitions.append({
            "from": from_stage,
            "to": to_stage,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action_index": self.action_index,
        })

    def record_memory_hit(
        self,
        memory_layer: str,
        tokens_saved: int = 0,
        stages_skipped: List[str] = None,
        detail: str = "",
    ):
        """Record an exploration memory cache hit."""
        hit = {
            "memory_layer": memory_layer,
            "tokens_saved": tokens_saved,
            "stages_skipped": stages_skipped or [],
            "detail": detail,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action_index": self.action_index,
        }
        self.memory_hits.append(hit)

        # Mark recent actions as memory-backed
        for action in self.actions[-3:]:
            action["memory_hit"] = True
            action["memory_layer"] = memory_layer

    def record_test_result(
        self,
        test_name: str,
        verdict: str,
        duration_ms: int = 0,
        failure_reason: str = "",
    ):
        """Record a test execution result as an action."""
        self.actions.append({
            "run_id": self.run_id,
            "action_index": self.action_index,
            "tool_name": "ta.test_execute",
            "input_summary": test_name[:200],
            "output_summary": failure_reason[:200] if failure_reason else "passed",
            "timestamp_end": datetime.now(timezone.utc).isoformat(),
            "latency_ms": duration_ms,
            "verdict": verdict,
            "stage": "EXECUTION",
        })
        self.action_index += 1

    def get_rollup(self) -> Dict[str, Any]:
        """Compute run-level rollup from per-action data."""
        total_duration = time.time() - self._start_time
        tool_calls = [a for a in self.actions if a.get("tool_name")]
        unique_tools = set(a.get("tool_name", "") for a in tool_calls)
        verdicts = [a.get("verdict", "") for a in self.actions if a.get("verdict")]
        passes = sum(1 for v in verdicts if v in ("pass", "PASS", "passed"))
        fails = sum(1 for v in verdicts if v in ("fail", "FAIL", "failed"))
        memory_tokens_saved = sum(h.get("tokens_saved", 0) for h in self.memory_hits)

        return {
            "run_id": self.run_id,
            "day": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "scenario_id": self.scenario_id,
            "workflow_family": self.workflow_family,
            "setup_variant": self.setup_variant,
            "runtime": self.runtime,
            "model": self.model,
            "surface": self.surface,
            "app_name": self.app_name,
            "actions_total": len(self.actions),
            "tool_calls_total": len(tool_calls),
            "unique_tools": list(unique_tools),
            "stages": [s["to"] for s in self.stage_transitions],
            "tests_passed": passes,
            "tests_failed": fails,
            "tests_total": passes + fails,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_tokens": self.total_tokens_in + self.total_tokens_out,
            "memory_hits": len(self.memory_hits),
            "memory_tokens_saved": memory_tokens_saved,
            "memory_hit_rate": round(
                sum(1 for a in self.actions if a.get("memory_hit")) / max(len(self.actions), 1), 3
            ),
            "total_duration_s": round(total_duration, 2),
            "avg_action_latency_ms": round(
                sum(a.get("latency_ms", 0) for a in self.actions) / max(len(self.actions), 1)
            ),
        }

    def save(self) -> str:
        """Persist the full action ledger + rollup to disk."""
        path = _LEDGER_DIR / f"{self.run_id}.json"
        data = {
            "run_id": self.run_id,
            "app_name": self.app_name,
            "workflow_family": self.workflow_family,
            "scenario_id": self.scenario_id,
            "setup_variant": self.setup_variant,
            "model": self.model,
            "surface": self.surface,
            "actions": self.actions,
            "stage_transitions": self.stage_transitions,
            "memory_hits": self.memory_hits,
            "rollup": self.get_rollup(),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2, default=str)
            logger.info(
                f"Action ledger saved: {self.run_id} — "
                f"{len(self.actions)} actions, {len(self.memory_hits)} memory hits"
            )
            return str(path)
        except Exception as e:
            logger.warning(f"Failed to save action ledger: {e}")
            return ""

    def from_pipeline_event(self, event: Dict[str, Any]):
        """Ingest a pipeline event and record the appropriate action."""
        etype = event.get("type", "")

        if etype == "stage_transition":
            self.record_stage_transition(self.current_stage, event.get("to_stage", ""))

        elif etype == "tool_call":
            self.record_tool_call_start(
                tool_name=event.get("tool_name", ""),
                tool_input=event.get("tool_input", ""),
                call_id=event.get("call_id", ""),
            )

        elif etype == "tool_call_output":
            self.record_tool_call_end(
                call_id=event.get("call_id", ""),
                tool_name=event.get("tool_name", ""),
                output_summary=event.get("tool_output", ""),
                duration_ms=event.get("duration_ms", 0),
            )

        elif etype == "test_execution_result":
            self.record_test_result(
                test_name=event.get("test_name", ""),
                verdict=event.get("status", event.get("result", "")),
                failure_reason=event.get("failure_reason", ""),
            )

        elif etype == "memory_cache_hit":
            self.record_memory_hit(
                memory_layer=event.get("memory_layer", "structural_memory"),
                tokens_saved=event.get("tokens_saved", 0),
                stages_skipped=event.get("stages_skipped", []),
                detail=event.get("detail", ""),
            )
