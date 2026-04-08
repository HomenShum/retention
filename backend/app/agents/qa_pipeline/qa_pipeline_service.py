"""
QA Pipeline Service — orchestrates 3 agents sequentially.

Crawl (streamed) -> Workflow (non-streamed) -> Test Cases (non-streamed)

Yields SSE-compatible event dicts for each stage.
"""

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

# Lazy imports: agents SDK can be slow on cold start (network-dependent)
# Imported at first use instead of module load time
Runner = None
MaxTurnsExceeded = None

def _ensure_agents_sdk():
    """Lazy-load the OpenAI Agents SDK on first use."""
    global Runner, MaxTurnsExceeded
    if Runner is None:
        from agents import Runner as _Runner
        from agents.exceptions import MaxTurnsExceeded as _MaxTurns
        Runner = _Runner
        MaxTurnsExceeded = _MaxTurns

# Lazy imports for agent creators (they import agents SDK which is slow on cold start)
# These are only needed when a pipeline actually runs, not at server startup
def _get_crawl_agent():
    from .crawl_agent import create_crawl_agent
    return create_crawl_agent

def _get_workflow_agent():
    from .workflow_agent import create_workflow_agent
    return create_workflow_agent

def _get_testcase_agent():
    from .testcase_agent import create_testcase_agent
    return create_testcase_agent

def _get_crawl_tools():
    from .tools.crawl_tools import create_crawl_tools
    return create_crawl_tools

from .schemas import CrawlResult, WorkflowResult, TestSuiteResult

logger = logging.getLogger(__name__)


# CostTracker replaces PipelineTokenTracker — same interface, adds per-tool audit trail.
# See backend/app/agents/coordinator/cost_tracker.py for implementation.
from app.agents.coordinator.cost_tracker import CostTracker as PipelineTokenTracker  # noqa: E402


def _extract_json(text: str) -> str:
    """Extract JSON from agent response (strip markdown fences if present)."""
    # Strip ```json ... ``` fences
    match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text)
    if match:
        return match.group(1).strip()
    # Try to find raw JSON object
    brace_start = text.find("{")
    if brace_start >= 0:
        # Find matching closing brace
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[brace_start : i + 1]
    return text


class QAPipelineService:
    """Orchestrates the 3-stage QA pipeline."""

    def __init__(self, mobile_mcp_client):
        self.mobile_mcp_client = mobile_mcp_client
        logger.info("QAPipelineService initialized")

    async def run_pipeline(
        self,
        app_name: str,
        package_name: str,
        device_id: str,
        target_workflows: Optional[List] = None,
        crawl_hints: Optional[str] = None,
        max_crawl_turns: Optional[int] = None,
        relay_session=None,
        model_override: str = "",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Run the full QA pipeline: Crawl -> Workflow -> TestCase.

        Args:
            target_workflows: List of dicts with {name, goal, entry_hint} for focused crawl.
                              If None, falls back to open-ended BFS (not recommended).

        Yields SSE event dicts with type, data, and optional metadata.
        """
        _ensure_agents_sdk()

        # Lazy-load context graph hooks (non-critical — failures silenced)
        try:
            from ...services.context_graph import ContextGraph, PipelineHooks
            _ctx_graph = ContextGraph()
            _pipeline_hooks = PipelineHooks(_ctx_graph)
        except Exception:
            _ctx_graph = None
            _pipeline_hooks = None

        logger.info(f"Starting QA pipeline for {app_name} ({package_name}) on {device_id}")
        if target_workflows:
            logger.info(f"Target workflows: {[w['name'] for w in target_workflows]}")

        # Token tracker — accumulates usage across all stages
        token_tracker = PipelineTokenTracker()

        # ── Stage 1: CRAWL ───────────────────────────────────────────────────
        yield {"type": "stage_transition", "to_stage": "CRAWL"}
        token_tracker.set_stage("CRAWL")

        # Generate a run_id for context graph tracking
        import uuid as _uuid
        _run_id = f"pipeline-{_uuid.uuid4().hex[:12]}"
        if _pipeline_hooks:
            _pipeline_hooks.on_crawl_start(_run_id, package_name)

        # Create navigation tools (reuse existing infrastructure)
        from ..device_testing.tools.autonomous_navigation_tools import (
            create_autonomous_navigation_tools,
        )

        nav_tools = create_autonomous_navigation_tools(self.mobile_mcp_client, device_id)

        # Demo mode: keep turns tight to avoid rate limits and ensure completion.
        # With MAX_ELEMENTS_PER_SCREEN=3, MAX_DEPTH=2, budget=(turns-6)//6:
        #   30 turns → budget=4 explorations (one clean trajectory for simple apps)
        #   50 turns → budget=7 explorations (slightly richer for open-ended)
        max_crawl_turns = max_crawl_turns or (30 if target_workflows else 50)
        crawl_tool_dict, get_crawl_result, get_fingerprints = _get_crawl_tools()(
            device_id, max_turns=max_crawl_turns,
            app_name=app_name, package_name=package_name,
        )

        crawl_agent = _get_crawl_agent()(nav_tools, crawl_tool_dict, model_override=model_override)

        # Build crawl prompt — open discovery with optional hints
        hints_line = f"Areas of interest: {', '.join(w['name'] for w in target_workflows)}\n" if target_workflows else ""
        hints_note = "But don't limit yourself — discover whatever screens you can reach.\n\n" if target_workflows else ""
        app_hints_line = f"{crawl_hints}\n\n" if crawl_hints else ""

        crawl_prompt = (
            f"Device ID: '{device_id}'\n"
            f"App: '{app_name}' (package: {package_name})\n\n"
            f"{app_hints_line}"
            f"Explore this app and discover its screens. No typing — tap only.\n"
            f"{hints_line}"
            f"{hints_note}"
            f"1. launch_app → get_ui_elements → list_elements_on_screen → register_screen (home screen)\n"
            f"2. get_next_target → tap_by_text(element_text) → get_ui_elements → list_elements_on_screen → register_screen\n"
            f"3. Press BACK to return and explore other paths.\n"
            f"4. complete_crawl when get_next_target says QUEUE_EMPTY or BUDGET_EXHAUSTED.\n\n"
            f"Use tap_by_text for all taps. Only fall back to click_at_coordinates if tap_by_text fails."
        )

        try:
            # Run crawl agent with streaming to capture tool calls
            result = Runner.run_streamed(
                crawl_agent,
                input=crawl_prompt,
                max_turns=max_crawl_turns,
            )

            # Track tool calls for output matching
            tool_call_names: Dict[str, str] = {}       # call_id -> tool_name
            tool_call_start_times: Dict[str, float] = {}  # call_id -> timestamp

            last_crawl_progress = None
            async for event in result.stream_events():
                event_type = getattr(event, "type", "")

                # Capture token usage from raw API responses
                if event_type == "raw_response_event":
                    raw_data = getattr(event, "data", None)
                    if raw_data:
                        # For ResponseCompletedEvent: data.response.usage
                        evt_type = getattr(raw_data, "type", "")
                        if evt_type == "response.completed":
                            resp = getattr(raw_data, "response", None)
                            if resp:
                                usage = getattr(resp, "usage", None)
                                if usage:
                                    inp = getattr(usage, "input_tokens", 0) or 0
                                    out = getattr(usage, "output_tokens", 0) or 0
                                    if inp or out:
                                        token_tracker.record(input_tokens=inp, output_tokens=out)
                        else:
                            # Fallback: check data.usage directly
                            usage = getattr(raw_data, "usage", None)
                            if usage:
                                inp = getattr(usage, "input_tokens", 0) or 0
                                out = getattr(usage, "output_tokens", 0) or 0
                                if inp or out:
                                    token_tracker.record(input_tokens=inp, output_tokens=out)

                # Forward tool call events
                if event_type == "run_item_stream_event":
                    item = getattr(event, "item", None)
                    if not item:
                        continue
                    item_type = getattr(item, "type", "")

                    if item_type == "tool_call_item":
                        tool_name = ""
                        tool_input = "{}"
                        call_id = None

                        # Extract from raw_item (Pydantic model or dict)
                        if hasattr(item, "raw_item"):
                            raw = item.raw_item
                            if hasattr(raw, "name"):
                                tool_name = raw.name
                                if hasattr(raw, "arguments"):
                                    tool_input = raw.arguments
                                if hasattr(raw, "call_id"):
                                    call_id = raw.call_id
                            elif isinstance(raw, dict):
                                if "function" in raw:
                                    func = raw["function"]
                                    if isinstance(func, dict):
                                        tool_name = func.get("name", "")
                                        tool_input = func.get("arguments", "{}")
                                elif "name" in raw:
                                    tool_name = raw["name"]
                                    tool_input = raw.get("arguments", "{}")
                                if "call_id" in raw:
                                    call_id = raw["call_id"]

                        # Fallback to item-level attributes
                        if not tool_name and hasattr(item, "name"):
                            tool_name = item.name
                        if hasattr(item, "arguments") and item.arguments:
                            tool_input = item.arguments
                        if not call_id and hasattr(item, "call_id"):
                            call_id = item.call_id

                        # Track for output matching
                        if call_id:
                            tool_call_names[call_id] = tool_name
                            tool_call_start_times[call_id] = time.time()

                        # Track action for suggest_next advisory (RET-12)
                        if '_track_action' in dir():
                            _track_action(f"{tool_name}({tool_input[:100]})")

                        yield {
                            "type": "tool_call",
                            "tool_name": tool_name,
                            "tool_input": tool_input,
                            "agent_name": "QA Crawl Agent",
                            "status": "started",
                            "call_id": call_id or "",
                        }

                    elif item_type == "tool_call_output_item":
                        output_text = getattr(item, "output", "")
                        call_id = None
                        tool_name = "unknown"
                        duration_ms = None

                        # Get call_id from raw_item
                        if hasattr(item, "raw_item"):
                            raw = item.raw_item
                            if isinstance(raw, dict) and "call_id" in raw:
                                call_id = raw["call_id"]
                            elif hasattr(raw, "call_id"):
                                call_id = raw.call_id

                        # Resolve tool name and compute duration
                        if call_id:
                            tool_name = tool_call_names.get(call_id, "unknown")
                            start = tool_call_start_times.pop(call_id, None)
                            if start:
                                duration_ms = int((time.time() - start) * 1000)

                        # Truncate large outputs for SSE
                        truncated_output = str(output_text)[:500] if output_text else ""

                        yield {
                            "type": "tool_call_output",
                            "tool_name": tool_name,
                            "tool_output": truncated_output,
                            "agent_name": "QA Crawl Agent",
                            "status": "completed",
                            "call_id": call_id or "",
                            "duration_ms": duration_ms,
                        }

                        out_str = str(output_text or "")

                        # Crawl progress on register_screen
                        if tool_name == "register_screen":
                            crawl_state = get_crawl_result()
                            current_screen = ""
                            if "Registered screen_" in out_str:
                                try:
                                    current_screen = out_str.split("'")[1] if "'" in out_str else ""
                                except Exception:
                                    current_screen = ""
                            elif "DUPLICATE" in out_str:
                                current_screen = "(duplicate — skipped)"
                            yield {
                                "type": "crawl_progress",
                                "screens_found": crawl_state.total_screens,
                                "components_found": crawl_state.total_components,
                                "current_screen": current_screen,
                            }

                            # Context graph: record discovered screen
                            if _pipeline_hooks and current_screen and "duplicate" not in current_screen.lower():
                                try:
                                    components = [c.text for c in crawl_state.screens[-1].components if c.text] if crawl_state.screens else []
                                    _pipeline_hooks.on_screen_discovered(_run_id, current_screen, components)
                                except Exception:
                                    pass

                        # Trajectory plan saved — broadcast the full plan
                        elif tool_name == "save_trajectory_plan" and "Plan saved" in out_str:
                            try:
                                # Output format: "Plan saved — N trajectories:\n[1] name → entry | ...\n→ Begin PHASE 2"
                                # The summary entries are on the second line
                                plan_lines = out_str.strip().splitlines()
                                summary_line = plan_lines[1] if len(plan_lines) > 1 else plan_lines[0] if plan_lines else ""
                                yield {
                                    "type": "trajectory_plan",
                                    "summary": summary_line.strip(),
                                }
                            except Exception:
                                pass

                        # Trajectory started — broadcast which one is active
                        elif tool_name == "get_next_trajectory" and "TRAJECTORY" in out_str:
                            try:
                                lines = out_str.strip().splitlines()
                                name_line = lines[0] if lines else ""
                                goal_line = next((l for l in lines if l.startswith("Goal:")), "")
                                yield {
                                    "type": "trajectory_progress",
                                    "trajectory": name_line.strip(),
                                    "goal": goal_line.replace("Goal:", "").strip(),
                                }
                            except Exception:
                                pass

                    elif item_type == "message_item":
                        # Agent reasoning / thinking text
                        if hasattr(item, "content"):
                            content = item.content
                            text_parts = []
                            if isinstance(content, list):
                                for part in content:
                                    if hasattr(part, "text") and part.text:
                                        text_parts.append(part.text)
                            elif isinstance(content, str) and content:
                                text_parts.append(content)

                            for text in text_parts:
                                yield {
                                    "type": "agent_reasoning",
                                    "agent_name": "QA Crawl Agent",
                                    "content": text[:300],
                                }

            # ── Post-stream token capture fallback ──────────────────────
            # If streaming didn't capture tokens (0 recorded), extract from
            # the RunResult's raw_responses as a fallback.
            crawl_tokens = token_tracker.stages.get("CRAWL", {})
            if crawl_tokens.get("total_tokens", 0) == 0:
                try:
                    final_result = await result.get()
                    for raw_resp in getattr(final_result, "raw_responses", []):
                        usage = getattr(raw_resp, "usage", None)
                        if usage:
                            inp = getattr(usage, "input_tokens", 0) or 0
                            out = getattr(usage, "output_tokens", 0) or 0
                            if inp or out:
                                token_tracker.record(input_tokens=inp, output_tokens=out)
                    fallback_total = token_tracker.stages.get("CRAWL", {}).get("total_tokens", 0)
                    if fallback_total > 0:
                        logger.info(f"CRAWL tokens captured via fallback: {fallback_total:,}")
                    else:
                        logger.warning("CRAWL tokens still 0 after fallback — SDK may not report per-response usage in streaming mode")
                except Exception as e:
                    logger.warning(f"Token fallback extraction failed: {e}")

            # Get final crawl result
            crawl_result = get_crawl_result()
            crawl_result.app_name = app_name
            crawl_result.package_name = package_name

            logger.info(
                f"Crawl complete: {crawl_result.total_screens} screens, "
                f"{crawl_result.total_components} components, "
                f"tokens={token_tracker.stages.get('CRAWL', {}).get('total_tokens', 0):,}"
            )

            # ── Save exploration memory (crawl results) ─────────────────
            # The crawl_tools' complete_crawl() already saves incrementally,
            # but we also save here as a final checkpoint with the complete result.
            try:
                from .exploration_memory import store_crawl, app_fingerprint
                _app_key = app_fingerprint(app_url="", package_name=package_name, app_name=app_name)
                if crawl_result.total_screens > 0:
                    store_crawl(_app_key, crawl_result, app_name=app_name)
                    logger.info(f"Exploration memory saved for {_app_key}")
                else:
                    logger.warning("Skipping exploration memory save: 0 screens")
            except Exception as mem_err:
                logger.warning(f"Failed to save exploration memory: {mem_err}")

            # Always emit a final crawl_progress so the UI reflects the completed state
            yield {
                "type": "crawl_progress",
                "screens_found": crawl_result.total_screens,
                "components_found": crawl_result.total_components,
                "current_screen": "Crawl complete",
            }

            # Emit full screen map data for mindmap visualization
            yield {
                "type": "crawl_complete",
                "screens": [
                    {
                        "id": s.screen_id,
                        "name": s.screen_name,
                        "depth": s.navigation_depth,
                        "parent": s.parent_screen_id,
                        "elements": [
                            {
                                "id": str(c.element_id),
                                "type": c.element_type.lower(),
                                "label": c.text,
                                "navigatesTo": c.leads_to,
                            }
                            for c in s.components
                            if c.is_interactive
                        ],
                    }
                    for s in crawl_result.screens
                ],
                "transitions": [
                    {
                        "from": t.from_screen,
                        "to": t.to_screen,
                        "action": t.action,
                    }
                    for t in crawl_result.transitions
                ],
            }

        except MaxTurnsExceeded:
            logger.warning(f"Crawl reached max_turns ({max_crawl_turns}) — using partial results")
            crawl_result = get_crawl_result()
            crawl_result.app_name = app_name
            crawl_result.package_name = package_name
            yield {
                "type": "crawl_progress",
                "screens_found": crawl_result.total_screens,
                "components_found": crawl_result.total_components,
                "current_screen": "Budget reached — handing off",
            }
            # Emit partial screen map even on budget exceeded
            yield {
                "type": "crawl_complete",
                "screens": [
                    {
                        "id": s.screen_id,
                        "name": s.screen_name,
                        "depth": s.navigation_depth,
                        "parent": s.parent_screen_id,
                        "elements": [
                            {
                                "id": str(c.element_id),
                                "type": c.element_type.lower(),
                                "label": c.text,
                                "navigatesTo": c.leads_to,
                            }
                            for c in s.components
                            if c.is_interactive
                        ],
                    }
                    for s in crawl_result.screens
                ],
                "transitions": [
                    {
                        "from": t.from_screen,
                        "to": t.to_screen,
                        "action": t.action,
                    }
                    for t in crawl_result.transitions
                ],
            }
        except Exception as e:
            logger.error(f"Crawl stage failed: {e}")
            yield {"type": "error", "content": f"Crawl failed: {str(e)}"}
            return

        # ── Stages 2 & 3: WORKFLOW + TESTCASE ────────────────────────────────
        if not crawl_result.screens:
            yield {"type": "error", "content": "Crawl found no screens — cannot generate test cases."}
            return

        crawl_json = crawl_result.model_dump_json(indent=2)
        async for event in self._run_analysis_stages(
            crawl_result, crawl_json, app_name,
            flow_type="android", device_id=device_id,
            relay_session=relay_session,
            token_tracker=token_tracker,
            model_override=model_override,
            _pipeline_hooks=_pipeline_hooks,
            _ctx_graph=_ctx_graph,
            _run_id=_run_id,
        ):
            yield event

    async def run_pipeline_for_url(
        self,
        app_url: str,
        app_name: str,
        device_id: str,
        max_crawl_turns: int = 80,
        entry_url: Optional[str] = None,
        scope_hint: Optional[str] = None,
        workflow_ids: Optional[List] = None,
        relay_session=None,
        model_override: str = "",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Run the QA pipeline for a web app URL: open in Chrome -> crawl -> workflow -> testcase.

        Args:
            entry_url: Start crawl at this URL instead of app_url (scoped crawl).
            scope_hint: Natural language hint to focus exploration.
            workflow_ids: Re-test these registered workflows instead of discovering new ones.
        """
        _ensure_agents_sdk()

        # Lazy-load context graph hooks (non-critical — failures silenced)
        try:
            from ...services.context_graph import ContextGraph, PipelineHooks
            _ctx_graph = ContextGraph()
            _pipeline_hooks = PipelineHooks(_ctx_graph)
        except Exception:
            _ctx_graph = None
            _pipeline_hooks = None

        import uuid as _uuid
        _run_id = f"pipeline-{_uuid.uuid4().hex[:12]}"

        # Resolve entry_url: relative paths get joined to app_url
        crawl_url = app_url
        if entry_url:
            if entry_url.startswith("http"):
                crawl_url = entry_url
            else:
                # Relative path like "/settings" — join to base
                from urllib.parse import urljoin
                crawl_url = urljoin(app_url, entry_url)
            logger.info(f"Scoped crawl: starting at {crawl_url} (base: {app_url})")

        logger.info(f"Starting URL pipeline for {app_name} ({crawl_url}) on {device_id}")

        # ── Workflow replay shortcut ──────────────────────────────────────────
        # If workflow_ids provided, skip crawl+discovery and replay registered workflows
        if workflow_ids:
            logger.info(f"Workflow replay mode: re-testing {workflow_ids}")
            yield {"type": "stage_transition", "to_stage": "WORKFLOW_REPLAY"}

            from pathlib import Path as _Path
            import json as _json
            _wf_dir = _Path(__file__).resolve().parent.parent.parent.parent / "data" / "registered_workflows"

            replay_workflows = []
            for wf_id in workflow_ids:
                wf_path = _wf_dir / f"{wf_id}.json"
                if wf_path.exists():
                    wf_data = _json.loads(wf_path.read_text())
                    replay_workflows.append(wf_data)
                    yield {
                        "type": "workflow_loaded",
                        "workflow_id": wf_id,
                        "name": wf_data.get("name", wf_id),
                        "steps": len(wf_data.get("steps", [])),
                    }
                else:
                    yield {"type": "warning", "content": f"Workflow {wf_id} not found in registry, skipping"}

            if not replay_workflows:
                yield {"type": "error", "content": "No valid workflow_ids found in registry"}
                return

            # Build a synthetic test suite from the registered workflows
            yield {"type": "stage_transition", "to_stage": "TEST_GENERATION"}
            from .schemas import TestCase, TestStep, TestSuiteResult, WorkflowSummary

            test_cases = []
            workflow_summaries = []
            for wf in replay_workflows:
                wf_id = wf["workflow_id"]
                wf_name = wf.get("name", wf_id)
                steps = wf.get("steps", [])

                workflow_summaries.append(WorkflowSummary(
                    workflow_id=wf_id,
                    name=wf_name,
                    test_count=1,
                ))

                test_cases.append(TestCase(
                    test_id=f"replay-{wf_id}",
                    name=f"Replay: {wf_name}",
                    workflow_id=wf_id,
                    workflow_name=wf_name,
                    description=f"Re-test registered workflow: {wf_name}",
                    steps=[
                        TestStep(
                            step_number=i + 1,
                            action=s.get("action", ""),
                            expected_result=s.get("text", "Action completes successfully"),
                        )
                        for i, s in enumerate(steps)
                    ],
                    expected_result="All workflow steps complete successfully",
                    priority="P0",
                    category="regression",
                ))

                yield {
                    "type": "test_case_generated",
                    "test_id": f"replay-{wf_id}",
                    "name": f"Replay: {wf_name}",
                    "workflow": wf_name,
                }

            suite = TestSuiteResult(
                app_name=app_name,
                test_cases=test_cases,
                workflows=workflow_summaries,
                total_tests=len(test_cases),
            )
            yield {
                "type": "pipeline_complete",
                "result": suite.model_dump(),
                "mode": "workflow_replay",
            }
            return

        # ── SUGGEST_NEXT ADVISORY (RET-12) ─────────────────────────────────
        # Initialize prefix tracker for this run. As the pipeline executes
        # tool calls, we accumulate them as a prefix and periodically call
        # suggest_next() to log what retention would have recommended.
        # This is passive/advisory in v1 — it logs but doesn't override the agent.
        _action_prefix: List[str] = []
        _last_suggestion = None
        _suggestions_offered = 0
        _suggestions_followed = 0
        _divergences_detected = 0

        def _track_action(action: str) -> None:
            """Track an action for suggest_next prefix matching."""
            nonlocal _last_suggestion, _suggestions_offered, _suggestions_followed, _divergences_detected
            _action_prefix.append(action)
            try:
                from .suggest_next import ActionPrefix, suggest_next as _suggest, check_divergence
                # Check divergence from last suggestion
                if _last_suggestion:
                    div = check_divergence(
                        ActionPrefix(actions=_action_prefix),
                        _last_suggestion,
                    )
                    if div.get("diverged"):
                        _divergences_detected += 1
                        if div.get("severity") == "critical":
                            _last_suggestion = None  # stop suggesting

                # Get next suggestion (advisory only)
                prefix = ActionPrefix(
                    actions=_action_prefix,
                    context={"surface": "web", "app_url": app_url},
                    current_url=app_url,
                )
                suggestion = _suggest(prefix, min_confidence=0.65)
                if suggestion:
                    _suggestions_offered += 1
                    _last_suggestion = suggestion
                    # Check if the agent is about to do what we suggested
                    if _last_suggestion and action.lower()[:30] in _last_suggestion.action.lower():
                        _suggestions_followed += 1
                        from .suggest_next import mark_suggestion_followed
                        mark_suggestion_followed(_last_suggestion.pattern_id, True)
            except Exception:
                pass  # suggest_next is advisory — never block the pipeline

        # ── EXPLORATION MEMORY CHECK ──────────────────────────────────────
        from .exploration_memory import (
            check_memory, store_crawl, load_crawl, store_workflows, store_test_suite,
            app_fingerprint, crawl_fingerprint,
        )

        app_key = app_fingerprint(app_url=app_url, app_name=app_name)
        mem = check_memory(app_url=app_url, app_name=app_name)

        # ── ROP-FIRST ROUTING ────────────────────────────────────────────────
        # If a promoted/operating ROP exists for this workflow, skip the full
        # pipeline and replay it with the cheapest validated model.
        try:
            from .rop_manager import ROPManager
            from .tier_replay_engine import TierReplayEngine

            _rop_mgr = ROPManager()
            _rop = _rop_mgr.find_rop_for_workflow(app_key, workflow_ids[0] if workflow_ids else app_key)
            if _rop:
                logger.info(
                    f"ROP HIT: {_rop.rop_id} for app={app_key}, "
                    f"replay_model={_rop.replay_model}, status={_rop.status.value}"
                )
                yield {
                    "type": "rop_cache_hit",
                    "rop_id": _rop.rop_id,
                    "replay_model": _rop.replay_model,
                    "replay_tier": _rop.replay_tier.value,
                    "discovery_cost_usd": _rop.cost_metrics.discovery_cost_usd,
                    "replay_count": _rop.replay_count,
                    "replay_success_rate": round(
                        _rop.replay_success_count / max(_rop.replay_count, 1), 3
                    ),
                }
                yield {"type": "stage_transition", "to_stage": "ROP_REPLAY"}

                engine = TierReplayEngine(_rop_mgr)
                async for event in engine.replay_rop(
                    _rop, self.mobile_mcp_client, device_id, app_url=app_url,
                ):
                    yield event

                # If replay completed successfully, we're done
                if event.get("type") == "rop_replay_complete" and event.get("success"):
                    yield {"type": "pipeline_complete", "result": {"rop_replay": True, **event}}
                    return
                # If escalation occurred, fall through to full pipeline
                logger.info(f"ROP replay did not fully succeed, falling through to full pipeline")
        except Exception as e:
            logger.debug(f"ROP routing check skipped: {e}")

        if mem.crawl_hit:
            yield {
                "type": "stage_activity",
                "stage": "MEMORY",
                "activity": "cache_hit",
                "message": (
                    f"Exploration memory HIT — reusing cached crawl "
                    f"({mem.crawl_result.total_screens} screens, "
                    f"{mem.crawl_result.total_components} components). "
                    f"Skipping: {', '.join(mem.stages_skipped)}. "
                    f"Est. tokens saved: {mem.estimated_tokens_saved:,}"
                ),
            }

        if mem.full_hit:
            # Full cache hit — skip crawl + workflow + testcase, go straight to execution
            logger.info(
                f"FULL MEMORY HIT for {app_name}: skipping CRAWL+WORKFLOW+TESTCASE "
                f"(~{mem.estimated_tokens_saved:,} tokens saved)"
            )
            yield {
                "type": "memory_cache_hit",
                "memory_layer": "structural_memory",
                "tokens_saved": mem.estimated_tokens_saved,
                "stages_skipped": ["CRAWL", "WORKFLOW", "TESTCASE"],
                "detail": f"Full hit: {mem.test_suite.total_tests} cached tests, {mem.estimated_cost_saved:.4f} saved",
            }
            yield {"type": "stage_transition", "to_stage": "EXECUTION"}
            yield {
                "type": "stage_activity",
                "stage": "EXECUTION",
                "activity": "replaying",
                "message": (
                    f"Full exploration memory hit — {mem.test_suite.total_tests} cached test cases. "
                    f"Running execution only (est. ${mem.estimated_cost_saved:.4f} saved)."
                ),
            }

            # Run execution with cached test suite
            from .execution_agent import execute_test_suite
            execution_events = []
            async for event in execute_test_suite(
                mem.test_suite, self.mobile_mcp_client, device_id,
                app_url=app_url, flow_type="web",
            ):
                execution_events.append(event)
                yield event

            # Build final result
            exec_summary = next((e for e in reversed(execution_events) if e.get("type") == "execution_complete"), {})
            # Emit memory_cache_hit event for ActionLedger tracking
            yield {
                "type": "memory_cache_hit",
                "memory_layer": "exploration_memory",
                "tokens_saved": mem.estimated_tokens_saved,
                "stages_skipped": mem.stages_skipped,
                "detail": f"Full memory hit: skipped {', '.join(mem.stages_skipped)}, saved ~{mem.estimated_tokens_saved} tokens",
            }
            yield {
                "type": "pipeline_complete",
                "result": {
                    **mem.test_suite.model_dump(),
                    "execution": exec_summary,
                    "memory_hit": True,
                    "stages_skipped": mem.stages_skipped,
                    "tokens_saved": mem.estimated_tokens_saved,
                    "cost_saved_usd": mem.estimated_cost_saved,
                },
            }
            return

        # ── Stage 1: CRAWL ───────────────────────────────────────────────────
        if mem.crawl_hit:
            # Partial hit — reuse cached crawl, skip to workflow/testcase
            crawl_result = mem.crawl_result
            crawl_result.app_name = app_name
            yield {
                "type": "memory_cache_hit",
                "memory_layer": "run_memory",
                "tokens_saved": 5000,  # Estimated crawl token savings
                "stages_skipped": ["CRAWL"],
                "detail": f"Crawl cache hit: {crawl_result.total_screens} screens reused",
            }
            yield {"type": "stage_transition", "to_stage": "CRAWL"}
            yield {
                "type": "stage_activity",
                "stage": "CRAWL",
                "activity": "cache_hit",
                "message": f"Reusing cached crawl ({crawl_result.total_screens} screens). Skipping device crawl.",
            }
            yield {
                "type": "crawl_progress",
                "screens_found": crawl_result.total_screens,
                "components_found": crawl_result.total_components,
                "current_screen": "Loaded from exploration memory",
            }
        else:
            yield {"type": "stage_transition", "to_stage": "CRAWL"}
            if _pipeline_hooks:
                _pipeline_hooks.on_crawl_start(_run_id, crawl_url)

            # Open URL in Chrome on the emulator
            yield {
                "type": "stage_activity",
                "stage": "CRAWL",
                "activity": "analyzing",
                "message": f"Opening {app_name} in Chrome on device...",
            }

        try:
            await self.mobile_mcp_client.open_url(device_id, crawl_url)
            await asyncio.sleep(3)  # Wait for page load
        except Exception as e:
            logger.warning(f"open_url failed, trying ADB fallback: {e}")
            try:
                import subprocess
                subprocess.run(
                    ["adb", "-s", device_id, "shell", "am", "start",
                     "-a", "android.intent.action.VIEW", "-d", crawl_url],
                    timeout=10, check=True,
                )
                await asyncio.sleep(3)
            except Exception as e2:
                logger.error(f"Failed to open URL: {e2}")
                yield {"type": "error", "content": f"Failed to open URL in Chrome: {str(e2)}"}
                return

        # Create navigation tools
        from ..device_testing.tools.autonomous_navigation_tools import (
            create_autonomous_navigation_tools,
        )

        nav_tools = create_autonomous_navigation_tools(self.mobile_mcp_client, device_id)
        url_max_turns = max_crawl_turns
        crawl_tool_dict, get_crawl_result, get_fingerprints = _get_crawl_tools()(
            device_id, max_turns=url_max_turns, is_web_crawl=True,
            app_name=app_name, app_url=app_url,
        )

        crawl_agent = _get_crawl_agent()(nav_tools, crawl_tool_dict, model_override=model_override)

        # Build scope-aware crawl prompt
        scope_section = ""
        if scope_hint:
            scope_section += f"\nSCOPE: {scope_hint}\n"
            scope_section += "Focus your exploration on this area. Do NOT wander to unrelated sections.\n\n"
        if entry_url and entry_url != app_url:
            scope_section += f"You are starting at a SPECIFIC SECTION ({crawl_url}), not the app root. "
            scope_section += "Only explore pages reachable from this section.\n\n"

        exploration_goal = (
            "discover pages and features in this section."
            if (scope_hint or entry_url)
            else "discover ALL pages, sections, and features."
        )

        crawl_prompt = (
            f"Device ID: '{device_id}'\n"
            f"A web app called '{app_name}' is already open in Chrome at {crawl_url}. "
            f"Your goal is DEEP exploration — {exploration_goal}\n\n"
            f"{scope_section}"
            f"STRATEGY:\n"
            f"1. First: get_ui_elements to see what's on the current page, then list_elements_on_screen → register_screen.\n"
            f"2. PRIORITIZE navigation elements: sidebar links, nav menus, tabs, buttons that lead to new pages.\n"
            f"3. get_next_target to get the next element to explore.\n"
            f"4. tap_by_text(element_text) to tap it — precise, no coordinate guessing.\n"
            f"5. At each new page: get_ui_elements → list_elements_on_screen → register_screen.\n"
            f"6. Press BACK to return and explore other navigation targets.\n"
            f"7. complete_crawl when get_next_target returns QUEUE_EMPTY or BUDGET_EXHAUSTED.\n\n"
            f"SPA TIPS:\n"
            f"- This is a single-page app. Navigation links change the view without full page reload.\n"
            f"- Look for sidebar menus, top nav bars, dropdown menus, tabs, and card links.\n"
            f"- If a page seems empty or loading, wait a moment and re-check with get_ui_elements.\n"
            f"- Scroll down if the page has content below the fold.\n"
            f"- Explore EVERY visible navigation link before moving deeper.\n\n"
            f"Do NOT type anything. Use tap_by_text for all taps. Only fall back to click_at_coordinates if tap_by_text fails."
        )

        try:
            result = Runner.run_streamed(
                crawl_agent,
                input=crawl_prompt,
                max_turns=url_max_turns,
            )

            tool_call_names: Dict[str, str] = {}
            tool_call_start_times: Dict[str, float] = {}
            last_crawl_progress = None

            async for event in result.stream_events():
                event_type = getattr(event, "type", "")

                if event_type == "run_item_stream_event":
                    item = getattr(event, "item", None)
                    if not item:
                        continue
                    item_type = getattr(item, "type", "")

                    if item_type == "tool_call_item":
                        tool_name = ""
                        tool_input = "{}"
                        call_id = None

                        if hasattr(item, "raw_item"):
                            raw = item.raw_item
                            if hasattr(raw, "name"):
                                tool_name = raw.name
                                if hasattr(raw, "arguments"):
                                    tool_input = raw.arguments
                                if hasattr(raw, "call_id"):
                                    call_id = raw.call_id
                            elif isinstance(raw, dict):
                                if "function" in raw:
                                    func = raw["function"]
                                    if isinstance(func, dict):
                                        tool_name = func.get("name", "")
                                        tool_input = func.get("arguments", "{}")
                                elif "name" in raw:
                                    tool_name = raw["name"]
                                    tool_input = raw.get("arguments", "{}")
                                if "call_id" in raw:
                                    call_id = raw["call_id"]

                        if not tool_name and hasattr(item, "name"):
                            tool_name = item.name
                        if hasattr(item, "arguments") and item.arguments:
                            tool_input = item.arguments
                        if not call_id and hasattr(item, "call_id"):
                            call_id = item.call_id

                        if call_id:
                            tool_call_names[call_id] = tool_name
                            tool_call_start_times[call_id] = time.time()

                        # Track action for suggest_next advisory (RET-12)
                        _track_action(f"{tool_name}({tool_input[:100]})")

                        yield {
                            "type": "tool_call",
                            "tool_name": tool_name,
                            "tool_input": tool_input,
                            "agent_name": "QA Crawl Agent",
                            "status": "started",
                            "call_id": call_id or "",
                        }

                    elif item_type == "tool_call_output_item":
                        output_text = getattr(item, "output", "")
                        call_id = None
                        tool_name = "unknown"
                        duration_ms = None

                        if hasattr(item, "raw_item"):
                            raw = item.raw_item
                            if isinstance(raw, dict) and "call_id" in raw:
                                call_id = raw["call_id"]
                            elif hasattr(raw, "call_id"):
                                call_id = raw.call_id

                        if call_id:
                            tool_name = tool_call_names.get(call_id, "unknown")
                            start = tool_call_start_times.pop(call_id, None)
                            if start:
                                duration_ms = int((time.time() - start) * 1000)

                        truncated_output = str(output_text)[:500] if output_text else ""

                        yield {
                            "type": "tool_call_output",
                            "tool_name": tool_name,
                            "tool_output": truncated_output,
                            "agent_name": "QA Crawl Agent",
                            "status": "completed",
                            "call_id": call_id or "",
                            "duration_ms": duration_ms,
                        }

                        if tool_name == "register_screen":
                            crawl_state = get_crawl_result()
                            current_screen = ""
                            out_str = str(output_text or "")
                            if "Registered screen_" in out_str:
                                try:
                                    current_screen = out_str.split("'")[1] if "'" in out_str else ""
                                except Exception:
                                    current_screen = ""
                            elif "DUPLICATE" in out_str:
                                current_screen = "(duplicate — skipped)"
                            yield {
                                "type": "crawl_progress",
                                "screens_found": crawl_state.total_screens,
                                "components_found": crawl_state.total_components,
                                "current_screen": current_screen,
                            }

                            # Context graph: record discovered screen
                            if _pipeline_hooks and current_screen and "duplicate" not in current_screen.lower():
                                try:
                                    components = [c.text for c in crawl_state.screens[-1].components if c.text] if crawl_state.screens else []
                                    _pipeline_hooks.on_screen_discovered(_run_id, current_screen, components)
                                except Exception:
                                    pass

                    elif item_type == "message_item":
                        if hasattr(item, "content"):
                            content = item.content
                            text_parts = []
                            if isinstance(content, list):
                                for part in content:
                                    if hasattr(part, "text") and part.text:
                                        text_parts.append(part.text)
                            elif isinstance(content, str) and content:
                                text_parts.append(content)

                            for text in text_parts:
                                yield {
                                    "type": "agent_reasoning",
                                    "agent_name": "QA Crawl Agent",
                                    "content": text[:300],
                                }

            crawl_result = get_crawl_result()
            crawl_result.app_name = app_name

            logger.info(
                f"URL crawl complete: {crawl_result.total_screens} screens, "
                f"{crawl_result.total_components} components"
            )

            # ── Delta crawl: compare fresh crawl vs cached crawl ──────────
            delta_info = None
            old_crawl_data = load_crawl(app_key) if not mem.crawl_hit else None
            if old_crawl_data:
                old_crawl, old_fp = old_crawl_data
                new_fp = crawl_fingerprint(crawl_result)
                if old_fp != new_fp:
                    # UI changed — run delta comparison
                    from .exploration_memory import (
                        delta_crawl as _delta_crawl,
                        merge_crawl as _merge_crawl,
                        invalidate_affected_only,
                        load_workflows as _load_wf,
                        load_test_suite as _load_ts,
                    )
                    old_wf_json = _load_wf(app_key, old_fp)
                    old_suite = _load_ts(app_key, old_fp)
                    delta_info = _delta_crawl(old_crawl, crawl_result, old_wf_json, old_suite)

                    if delta_info.has_changes:
                        yield {
                            "type": "stage_activity",
                            "stage": "CRAWL",
                            "activity": "delta_crawl",
                            "message": (
                                f"Delta crawl: +{len(delta_info.added_screens)} added, "
                                f"~{len(delta_info.changed_screens)} changed, "
                                f"-{len(delta_info.removed_screens)} removed, "
                                f"={len(delta_info.unchanged_screens)} unchanged. "
                                f"Only re-processing affected screens."
                            ),
                            "delta": delta_info.summary(),
                        }
                        # Merge: keep unchanged from old, take changed/added from new
                        crawl_result = _merge_crawl(old_crawl, crawl_result, delta_info)

                        # Scoped invalidation: only nuke workflows/tests for changed screens
                        if delta_info.affected_workflows or delta_info.affected_tests:
                            invalidate_affected_only(
                                app_key, old_fp,
                                delta_info.affected_workflows,
                                delta_info.affected_tests,
                            )
                    else:
                        yield {
                            "type": "stage_activity",
                            "stage": "CRAWL",
                            "activity": "delta_crawl",
                            "message": "Fresh crawl matches cached version — no UI changes detected.",
                        }

            # Store crawl in exploration memory
            try:
                store_crawl(app_key, crawl_result, app_url=app_url, app_name=app_name)
                msg = "Crawl stored in exploration memory — next run will skip crawl stage."
                if delta_info and delta_info.has_changes:
                    msg = (
                        f"Delta crawl stored — {len(delta_info.unchanged_screens)} screens preserved from cache, "
                        f"{len(delta_info.changed_screen_set)} re-processed."
                    )
                yield {
                    "type": "stage_activity",
                    "stage": "CRAWL",
                    "activity": "memory_stored",
                    "message": msg,
                }
            except Exception as mem_err:
                logger.warning(f"Failed to store crawl in memory: {mem_err}")

            yield {
                "type": "crawl_progress",
                "screens_found": crawl_result.total_screens,
                "components_found": crawl_result.total_components,
                "current_screen": "Crawl complete",
            }

        except Exception as e:
            logger.error(f"URL crawl stage failed: {e}")
            yield {"type": "error", "content": f"Crawl failed: {str(e)}"}
            return

        # ── Stages 2, 3, 4: WORKFLOW + TESTCASE + EXECUTION ──────────────────
        crawl_json = crawl_result.model_dump_json(indent=2)
        token_tracker = PipelineTokenTracker()
        async for event in self._run_analysis_stages(
            crawl_result, crawl_json, app_name,
            flow_type="web", device_id=device_id, app_url=app_url,
            relay_session=relay_session,
            token_tracker=token_tracker,
            model_override=model_override,
            _pipeline_hooks=_pipeline_hooks,
            _ctx_graph=_ctx_graph,
            _run_id=_run_id,
        ):
            yield event

    async def _run_analysis_stages(
        self,
        crawl_result: CrawlResult,
        crawl_json: str,
        app_name: str,
        flow_type: str = "android",
        device_id: Optional[str] = None,
        app_url: Optional[str] = None,
        relay_session=None,
        token_tracker: Optional["PipelineTokenTracker"] = None,
        model_override: str = "",
        _pipeline_hooks=None,
        _ctx_graph=None,
        _run_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Shared stages 2 (Workflow), 3 (TestCase), and 4 (Execution) for both pipeline modes.

        Args:
            relay_session: Optional RelaySession for remote execution via user's device.
                           When provided, test execution routes through the agent relay
                           instead of calling the local MobileMCPClient directly.
        """

        # ── Stage 2: WORKFLOW ────────────────────────────────────────────────
        yield {"type": "stage_transition", "to_stage": "WORKFLOW"}
        if not token_tracker:
            token_tracker = PipelineTokenTracker()
        token_tracker.set_stage("WORKFLOW")
        yield {
            "type": "stage_activity",
            "stage": "WORKFLOW",
            "activity": "analyzing",
            "message": f"Analyzing crawl data ({crawl_result.total_screens} screens, {crawl_result.total_components} components) to identify user workflows...",
        }

        try:
            workflow_agent = _get_workflow_agent()(model_override=model_override)

            wf_prompt = (
                f"Analyze this CrawlResult and identify 5-10 user workflows:\n\n{crawl_json}"
            )

            wf_run_result = await Runner.run(
                workflow_agent,
                input=wf_prompt,
                max_turns=5,
            )

            # Capture token usage from workflow run
            try:
                for ri in getattr(wf_run_result, "raw_responses", []) or []:
                    usage = getattr(ri, "usage", None)
                    if usage:
                        token_tracker.record(
                            input_tokens=getattr(usage, "input_tokens", 0) or 0,
                            output_tokens=getattr(usage, "output_tokens", 0) or 0,
                        )
            except Exception:
                pass

            # Parse workflow result from agent output
            wf_text = wf_run_result.final_output or ""
            wf_json_str = _extract_json(wf_text)
            workflow_result = WorkflowResult.model_validate_json(wf_json_str)

            logger.info(f"Workflow analysis complete: {len(workflow_result.workflows)} workflows")

            yield {
                "type": "stage_activity",
                "stage": "WORKFLOW",
                "activity": "completed",
                "message": f"Identified {len(workflow_result.workflows)} workflows",
            }

            for wf in workflow_result.workflows:
                yield {
                    "type": "workflow_identified",
                    "workflow_id": wf.workflow_id,
                    "name": wf.name,
                    "steps_count": len(wf.steps),
                    "complexity": wf.complexity,
                }

            # Store workflows in exploration memory
            try:
                from .exploration_memory import store_workflows, app_fingerprint as _app_fp, crawl_fingerprint as _cr_fp
                _ak = _app_fp(app_url=app_url or "", app_name=app_name)
                _cf = _cr_fp(crawl_result)
                store_workflows(_ak, _cf, wf_json_str, len(workflow_result.workflows))
            except Exception as mem_err:
                logger.warning(f"Failed to store workflows in memory: {mem_err}")

        except Exception as e:
            logger.error(f"Workflow stage failed: {e}")
            yield {"type": "error", "content": f"Workflow analysis failed: {str(e)}"}
            return

        # ── Stage 3: TEST CASES ──────────────────────────────────────────────
        yield {"type": "stage_transition", "to_stage": "TESTCASE"}
        token_tracker.set_stage("TESTCASE")
        yield {
            "type": "stage_activity",
            "stage": "TESTCASE",
            "activity": "generating",
            "message": f"Generating QA test cases from {len(workflow_result.workflows)} workflows...",
        }

        try:
            testcase_agent = _get_testcase_agent()(model_override=model_override)
            wf_json = workflow_result.model_dump_json(indent=2)

            platform = "web" if flow_type == "web" else "android"

            # Build a domain-context summary from crawl data so the test
            # generator has an explicit list of screens, their content, and
            # the vocabulary it MUST use in assertions.
            domain_context_lines: list[str] = []
            try:
                for scr in crawl_result.screens:
                    comp_texts = [
                        c.text for c in scr.components
                        if c.text and c.text.strip()
                    ]
                    comp_summary = ", ".join(comp_texts[:30])  # cap for token budget
                    domain_context_lines.append(
                        f"- Screen '{scr.screen_name}' (id={scr.screen_id}): "
                        f"{scr.screenshot_description or 'no description'}. "
                        f"Key content: [{comp_summary}]"
                    )
            except Exception:
                pass  # graceful fallback — crawl_json still included below

            domain_context = "\n".join(domain_context_lines) if domain_context_lines else "(see CRAWL DATA below)"

            # Build workflow summary so the model sees names + descriptions
            # without needing to parse full JSON first.
            wf_summary_lines: list[str] = []
            try:
                for wf in workflow_result.workflows:
                    step_actions = " -> ".join(s.action for s in wf.steps[:6])
                    wf_summary_lines.append(
                        f"- {wf.workflow_id} '{wf.name}': {wf.description}. "
                        f"Steps: {step_actions}"
                    )
            except Exception:
                pass

            wf_summary = "\n".join(wf_summary_lines) if wf_summary_lines else "(see WORKFLOWS JSON below)"

            tc_prompt = (
                f"Generate 20+ domain-specific QA test cases for this {platform} app.\n\n"
                f"PLATFORM: {platform}\n\n"
                f"## SCREEN INVENTORY (use these names and content in your assertions)\n"
                f"{domain_context}\n\n"
                f"## WORKFLOW SUMMARY (each test must map to one of these)\n"
                f"{wf_summary}\n\n"
                f"## FULL CRAWL DATA (reference for component IDs, coordinates, transitions)\n"
                f"{crawl_json}\n\n"
                f"## FULL WORKFLOW DATA\n"
                f"{wf_json}\n\n"
                f"IMPORTANT: Your test assertions MUST reference the actual content visible "
                f"on the screens above — real labels, headings, data values, entity names, "
                f"field values. Do NOT write generic assertions like 'page loads' or 'click works'. "
                f"Every expected_result must describe domain-specific outcomes using vocabulary "
                f"from the screen inventory."
            )

            tc_run_result = await Runner.run(
                testcase_agent,
                input=tc_prompt,
                max_turns=10,
            )

            # Capture token usage from testcase run
            try:
                for ri in getattr(tc_run_result, "raw_responses", []) or []:
                    usage = getattr(ri, "usage", None)
                    if usage:
                        token_tracker.record(
                            input_tokens=getattr(usage, "input_tokens", 0) or 0,
                            output_tokens=getattr(usage, "output_tokens", 0) or 0,
                        )
            except Exception:
                pass

            # Parse test suite result
            tc_text = tc_run_result.final_output or ""
            tc_json_str = _extract_json(tc_text)
            test_suite = TestSuiteResult.model_validate_json(tc_json_str)

            logger.info(f"Test case generation complete: {test_suite.total_tests} test cases")

            yield {
                "type": "stage_activity",
                "stage": "TESTCASE",
                "activity": "completed",
                "message": f"Generated {test_suite.total_tests} test cases",
            }

            for tc in test_suite.test_cases:
                yield {
                    "type": "test_case_generated",
                    "test_id": tc.test_id,
                    "name": tc.name,
                    "workflow_name": tc.workflow_name,
                    "priority": tc.priority,
                    "category": tc.category,
                }

            # Store test suite in exploration memory
            try:
                from .exploration_memory import store_test_suite, app_fingerprint as _app_fp2, crawl_fingerprint as _cr_fp2
                _ak2 = _app_fp2(app_url=app_url or "", app_name=app_name)
                _cf2 = _cr_fp2(crawl_result)
                store_test_suite(_ak2, _cf2, test_suite)
            except Exception as mem_err:
                logger.warning(f"Failed to store test suite in memory: {mem_err}")

        except Exception as e:
            logger.error(f"Test case stage failed: {e}")
            yield {"type": "error", "content": f"Test case generation failed: {str(e)}"}
            return

        # ── Stage 4: EXECUTION ────────────────────────────────────────────────
        # Run generated test cases on the live device/browser.
        # Priority: relay (user's remote device) > local MobileMCPClient > skip
        execution_results = None

        if relay_session:
            # Remote execution via agent relay → user's laptop emulator
            try:
                from .relay_execution import execute_via_relay

                logger.info("Executing tests via agent relay (user's device)")
                async for exec_event in execute_via_relay(
                    test_suite=test_suite,
                    relay_session=relay_session,
                    app_url=app_url,
                    flow_type=flow_type,
                ):
                    yield exec_event
                    if exec_event.get("type") == "test_execution_result" and _pipeline_hooks and _run_id:
                        try:
                            _pipeline_hooks.on_test_executed(
                                _run_id,
                                exec_event.get("test_id", ""),
                                exec_event.get("passed", False),
                                exec_event,
                            )
                        except Exception:
                            pass
                    if exec_event.get("type") == "execution_complete":
                        execution_results = exec_event
            except Exception as e:
                logger.error(f"Relay execution failed: {e}")
                yield {
                    "type": "stage_activity",
                    "stage": "EXECUTION",
                    "activity": "error",
                    "message": f"Relay execution failed: {str(e)}",
                }

        elif device_id and self.mobile_mcp_client:
            # Local execution — emulator on same machine as server (dev mode)
            try:
                from .execution_agent import execute_test_suite

                logger.info("Executing tests via local MobileMCPClient")
                async for exec_event in execute_test_suite(
                    test_suite=test_suite,
                    mobile_mcp_client=self.mobile_mcp_client,
                    device_id=device_id,
                    app_url=app_url,
                    flow_type=flow_type,
                ):
                    yield exec_event
                    if exec_event.get("type") == "test_execution_result" and _pipeline_hooks and _run_id:
                        try:
                            _pipeline_hooks.on_test_executed(
                                _run_id,
                                exec_event.get("test_id", ""),
                                exec_event.get("passed", False),
                                exec_event,
                            )
                        except Exception:
                            pass
                    if exec_event.get("type") == "execution_complete":
                        execution_results = exec_event
            except Exception as e:
                logger.error(f"Local execution failed: {e}")
                yield {
                    "type": "stage_activity",
                    "stage": "EXECUTION",
                    "activity": "error",
                    "message": f"Execution failed: {str(e)}",
                }
        else:
            logger.info("Skipping execution stage (no relay session or local device)")

        # ── Pipeline Complete ────────────────────────────────────────────────
        pipeline_result = test_suite.model_dump()
        if execution_results:
            pipeline_result["execution"] = {
                "total": execution_results["total"],
                "passed": execution_results["passed"],
                "failed": execution_results["failed"],
                "pass_rate": execution_results["pass_rate"],
                "results": execution_results["results"],
            }
        # Attach token usage to pipeline result
        pipeline_result["token_usage"] = token_tracker.totals()

        # Save context graph to disk
        if _ctx_graph and _run_id:
            try:
                import os as _os
                _graph_dir = _os.path.join(_os.path.dirname(__file__), "..", "..", "..", "data", "context_graphs")
                _os.makedirs(_graph_dir, exist_ok=True)
                _ctx_graph.save(_os.path.join(_graph_dir, f"{_run_id}.json"))
            except Exception:
                pass

        yield {
            "type": "pipeline_complete",
            "result": pipeline_result,
        }

        token_totals = token_tracker.totals()
        logger.info(
            f"QA pipeline complete for {app_name} — "
            f"tokens: {token_totals['total_tokens']:,} "
            f"(in={token_totals['input_tokens']:,} out={token_totals['output_tokens']:,}) "
            f"cost=${token_totals['estimated_cost_usd']:.4f} "
            f"calls={token_totals['api_calls']}"
        )

        # ── Increment dream engine run count (KAIROS dual-gate) ────────────
        try:
            from ...services.rop_dream_engine import increment_run_count, should_dream, run_dream
            increment_run_count()
            # Check if dream should fire (advisory — runs in-process for now)
            dream_check = should_dream()
            if dream_check["should_run"]:
                logger.info("[KAIROS] Dream gates passed — running consolidation")
                dream_result = run_dream()
                yield {
                    "type": "dream_consolidation",
                    "promoted": dream_result.trajectories_promoted,
                    "pruned": dream_result.trajectories_pruned,
                    "archived": dream_result.trajectories_archived,
                    "contradictions": dream_result.contradictions_resolved,
                    "rops_created": dream_result.rops_created,
                    "duration_s": dream_result.duration_s,
                }
        except Exception as e:
            logger.debug(f"Dream engine skipped: {e}")

        # ── Record ROP savings (RET-14) ──────────────────────────────────
        try:
            from ...services.rop_savings_tracker import get_rop_savings_tracker, ROPRunRecord
            _tracker = get_rop_savings_tracker()
            _tracker.record_run(ROPRunRecord(
                run_id=_run_id,
                rop_id="",  # filled by auto-ROP creation below if applicable
                rop_family="",
                run_type="assisted" if _suggestions_offered > 0 else "cold",
                timestamp=datetime.now(timezone.utc).isoformat(),
                total_tokens=token_totals.get("total_tokens", 0),
                reasoning_tokens=token_totals.get("output_tokens", 0),
                reasoning_tokens_avoided=_suggestions_followed * 500,
                total_time_s=pipeline_result.get("elapsed_s", 0),
                suggestions_offered=_suggestions_offered,
                suggestions_followed=_suggestions_followed,
                divergences_detected=_divergences_detected,
                success=pipeline_result.get("pass_rate", 0) > 0.5,
            ))
        except Exception as e:
            logger.debug(f"ROP savings recording skipped: {e}")

        # ── Auto-create DRAFT ROP from successful pipeline run ────────────
        try:
            from .rop_manager import ROPManager
            _rop_mgr = ROPManager()
            # Check if execution had trajectory data
            _traj_id = pipeline_result.get("trajectory_id", "")
            if _traj_id and pipeline_result.get("pass_rate", 0) > 0.5:
                _rop = _rop_mgr.create_rop(
                    trajectory_id=_traj_id,
                    task_name=app_name,
                    app_key=app_key,
                    app_url=app_url,
                    origin_model=model_override or "gpt-5.4",
                    discovery_tokens=token_totals.get("total_tokens", 0),
                    discovery_cost_usd=token_totals.get("estimated_cost_usd", 0),
                    discovery_time_s=pipeline_result.get("elapsed_s", 0),
                    step_count=pipeline_result.get("total_steps", 0),
                )
                logger.info(f"Auto-created DRAFT ROP {_rop.rop_id} from pipeline run")
        except Exception as e:
            logger.debug(f"Auto-ROP creation skipped: {e}")

        # ── Auto-score and update run history ─────────────────────────────
        try:
            from .run_history import build_index
            build_index(force=True)
            logger.info("Run history index updated after pipeline completion")
        except Exception as e:
            logger.warning(f"Failed to update run history: {e}")
