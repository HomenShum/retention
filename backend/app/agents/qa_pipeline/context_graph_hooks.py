"""Context Graph Pipeline Hooks — populate the graph during QA pipeline execution.

These hooks integrate with the existing pipeline stages (CRAWL → WORKFLOW →
TESTCASE → EXECUTION) and the ActionSpan evidence system to build the
contextual graph automatically.

Usage:
    from .context_graph_hooks import PipelineGraphHooks

    hooks = PipelineGraphHooks(run_id="run_abc123", app_key="myapp")

    # During CRAWL
    hooks.on_crawl_complete(crawl_result, screen_fingerprints)

    # During WORKFLOW
    hooks.on_workflows_identified(workflows)

    # During EXECUTION
    hooks.on_test_start(test_case)
    hooks.on_action_taken(action_desc, action_type, screen_before, screen_after, span_id)
    hooks.on_test_complete(test_case, status, failure_reason, duration_ms)

    # After EXECUTION
    hooks.on_run_complete(results)
    hooks.attribute_verdicts(results)  # auto-classify app_bug vs agent_bug

    # Persist
    hooks.save()
"""

import logging
from typing import Any, Dict, List, Optional

from .context_graph import (
    ContextGraph,
    ContextGraphManager,
    EdgeType,
    GraphNode,
    NodeType,
    VerdictAttribution,
    action_path_fingerprint,
    failure_fingerprint,
    make_action_node,
    make_intent_node,
    make_observation_node,
    make_outcome_node,
    make_task_node,
    make_ui_state_node,
    make_verdict_node,
)

logger = logging.getLogger(__name__)


class PipelineGraphHooks:
    """Hooks that fire during QA pipeline execution to build the context graph.

    Wires into existing pipeline stages without modifying their core logic.
    Each hook creates nodes + edges in the graph, building up the
    task → state → observation → action → outcome → verdict chain.
    """

    def __init__(self, run_id: str, app_key: str, app_name: str = ""):
        self.run_id = run_id
        self.app_key = app_key
        self.app_name = app_name

        mgr = ContextGraphManager.get()
        self.graph = mgr.get_app_graph(app_key)

        # Create the run node
        self._run_node = GraphNode(
            node_type=NodeType.RUN,
            label=f"Run {run_id[:8]}",
            data={"app_key": app_key, "app_name": app_name},
            run_id=run_id,
        )
        self.graph.add_node(self._run_node)

        # Track current context for edge wiring
        self._current_test_node: Optional[str] = None
        self._current_state_node: Optional[str] = None
        self._last_action_node: Optional[str] = None
        self._action_sequence: List[str] = []

        # Track screen nodes to avoid duplicates
        self._screen_node_map: Dict[str, str] = {}  # screen_id → node_id

    # ── CRAWL Stage ──────────────────────────────────────────────────────

    def on_crawl_complete(
        self,
        screens: List[Dict[str, Any]],
        transitions: List[Dict[str, Any]],
        screen_fingerprints: Dict[str, str],
    ) -> None:
        """Called when crawl stage completes.

        Creates UI_STATE nodes for each screen and edges for transitions.
        """
        for screen in screens:
            sid = screen.get("screen_id", "")
            name = screen.get("screen_name", sid)
            fp = screen_fingerprints.get(sid, "")

            node = make_ui_state_node(
                screen_name=name,
                screen_id=sid,
                run_id=self.run_id,
                screen_fingerprint=fp,
                navigation_depth=screen.get("navigation_depth", 0),
                component_count=len(screen.get("components", [])),
            )
            self.graph.add_node(node)
            self._screen_node_map[sid] = node.node_id

            # Link screen to run
            self.graph.connect(
                self._run_node.node_id,
                node.node_id,
                EdgeType.RUN_CONTAINS,
            )

        # Create transition edges between screens
        for trans in transitions:
            from_sid = trans.get("from_screen", "")
            to_sid = trans.get("to_screen", "")
            from_nid = self._screen_node_map.get(from_sid)
            to_nid = self._screen_node_map.get(to_sid)

            if from_nid and to_nid:
                self.graph.connect(
                    from_nid,
                    to_nid,
                    EdgeType.ACTION_PRODUCED_STATE,
                    data={"action": trans.get("action", "navigate")},
                )

        logger.info(
            f"Graph: crawl complete — {len(screens)} screens, "
            f"{len(transitions)} transitions"
        )

    # ── WORKFLOW Stage ───────────────────────────────────────────────────

    def on_workflows_identified(self, workflows: List[Dict[str, Any]]) -> None:
        """Called when workflow identification completes."""
        for wf in workflows:
            wf_node = GraphNode(
                node_type=NodeType.WORKFLOW,
                label=wf.get("name", wf.get("workflow_id", "unknown")),
                data={
                    "workflow_id": wf.get("workflow_id"),
                    "description": wf.get("description", ""),
                    "complexity": wf.get("complexity", "unknown"),
                    "screens_involved": wf.get("screens_involved", []),
                },
                run_id=self.run_id,
            )
            self.graph.add_node(wf_node)
            self.graph.connect(
                self._run_node.node_id,
                wf_node.node_id,
                EdgeType.RUN_CONTAINS,
            )

            # Link workflow to its screens
            for sid in wf.get("screens_involved", []):
                screen_nid = self._screen_node_map.get(sid)
                if screen_nid:
                    self.graph.connect(
                        wf_node.node_id,
                        screen_nid,
                        EdgeType.WORKFLOW_CONTAINS_STEP,
                    )

    # ── TEST CASE Stage ──────────────────────────────────────────────────

    def on_test_cases_generated(self, test_cases: List[Dict[str, Any]]) -> None:
        """Called when test case generation completes."""
        for tc in test_cases:
            tc_node = GraphNode(
                node_type=NodeType.TEST_CASE,
                label=tc.get("name", tc.get("test_id", "unknown")),
                data={
                    "test_id": tc.get("test_id"),
                    "workflow_id": tc.get("workflow_id"),
                    "priority": tc.get("priority"),
                    "category": tc.get("category"),
                    "pressure_point": tc.get("pressure_point"),
                    "step_count": len(tc.get("steps", [])),
                },
                run_id=self.run_id,
            )
            self.graph.add_node(tc_node)
            self.graph.connect(
                self._run_node.node_id,
                tc_node.node_id,
                EdgeType.RUN_CONTAINS,
            )

    # ── EXECUTION Stage ──────────────────────────────────────────────────

    def on_test_start(self, test_id: str, test_name: str, task_goal: str = "") -> str:
        """Called when a test case begins execution.

        Creates a TASK node and sets it as current context.
        Returns the task node ID.
        """
        task_node = make_task_node(
            task_name=test_name,
            task_goal=task_goal or test_name,
            run_id=self.run_id,
            test_id=test_id,
        )
        self.graph.add_node(task_node)
        self.graph.connect(
            self._run_node.node_id,
            task_node.node_id,
            EdgeType.RUN_CONTAINS,
        )

        self._current_test_node = task_node.node_id
        self._current_state_node = None
        self._last_action_node = None
        self._action_sequence = []

        return task_node.node_id

    def on_state_observed(
        self,
        screen_name: str,
        screen_id: str = "",
        screenshot_path: Optional[str] = None,
        elements_detected: int = 0,
        screen_fingerprint: str = "",
    ) -> str:
        """Called when the agent observes a new UI state.

        Creates a UI_STATE + OBSERVATION node pair.
        Returns the state node ID.
        """
        # Reuse existing screen node if same fingerprint
        state_node = make_ui_state_node(
            screen_name=screen_name,
            screen_id=screen_id,
            run_id=self.run_id,
            screen_fingerprint=screen_fingerprint,
            elements_detected=elements_detected,
        )
        self.graph.add_node(state_node)

        # Create observation node
        obs_node = make_observation_node(
            description=f"Observed {screen_name} ({elements_detected} elements)",
            observation_type="screenshot" if screenshot_path else "ui_dump",
            run_id=self.run_id,
            evidence_path=screenshot_path,
            elements_detected=elements_detected,
        )
        self.graph.add_node(obs_node)

        # Wire: state ← observed_by → observation
        self.graph.connect(
            state_node.node_id,
            obs_node.node_id,
            EdgeType.STATE_OBSERVED_BY,
        )

        # Wire: task requires state
        if self._current_test_node:
            self.graph.connect(
                self._current_test_node,
                state_node.node_id,
                EdgeType.TASK_REQUIRES_STATE,
            )

        # Wire: previous action → produced this state
        if self._last_action_node:
            self.graph.connect(
                self._last_action_node,
                state_node.node_id,
                EdgeType.ACTION_PRODUCED_STATE,
            )

        self._current_state_node = state_node.node_id
        return state_node.node_id

    def on_action_taken(
        self,
        action_description: str,
        action_type: str = "other",
        span_id: Optional[str] = None,
        hypothesis: Optional[str] = None,
        confidence: float = 0.0,
    ) -> str:
        """Called when the agent takes an action.

        Creates an ACTION node, optionally an INTENT node.
        Returns the action node ID.
        """
        action_node = make_action_node(
            action_description=action_description,
            action_type=action_type,
            run_id=self.run_id,
            span_id=span_id,
        )
        self.graph.add_node(action_node)

        # Wire: action taken from current state
        if self._current_state_node:
            self.graph.connect(
                action_node.node_id,
                self._current_state_node,
                EdgeType.ACTION_TAKEN_FROM,
            )

        # Optional: intent/hypothesis node
        if hypothesis:
            intent_node = make_intent_node(
                hypothesis=hypothesis,
                confidence=confidence,
                run_id=self.run_id,
            )
            self.graph.add_node(intent_node)
            self.graph.connect(
                intent_node.node_id,
                action_node.node_id,
                EdgeType.OBSERVATION_SUPPORTS,
            )

        self._last_action_node = action_node.node_id
        self._action_sequence.append(action_description)

        return action_node.node_id

    def on_test_complete(
        self,
        test_id: str,
        status: str,
        failure_reason: Optional[str] = None,
        duration_ms: Optional[int] = None,
        severity: Optional[str] = None,
    ) -> str:
        """Called when a test case completes execution.

        Creates an OUTCOME node and wires it to the action chain.
        Returns the outcome node ID.
        """
        outcome_node = make_outcome_node(
            status=status,
            test_id=test_id,
            run_id=self.run_id,
            failure_reason=failure_reason,
            duration_ms=duration_ms,
            severity=severity,
        )
        self.graph.add_node(outcome_node)

        # Wire: last action → outcome
        if self._last_action_node:
            self.graph.connect(
                self._last_action_node,
                outcome_node.node_id,
                EdgeType.ACTION_PRODUCED_STATE,
            )

        # Wire: task → outcome
        if self._current_test_node:
            self.graph.connect(
                self._current_test_node,
                outcome_node.node_id,
                EdgeType.RUN_CONTAINS,
            )

        # Fingerprint the action path for precedent matching
        if self._action_sequence:
            path_fp = action_path_fingerprint(self._action_sequence)
            outcome_node.data["action_path_fingerprint"] = path_fp

        # Reset test context
        self._current_test_node = None
        self._current_state_node = None
        self._last_action_node = None
        self._action_sequence = []

        return outcome_node.node_id

    # ── Post-Execution Analysis ──────────────────────────────────────────

    def on_run_complete(self, results: Dict[str, Any]) -> None:
        """Called when the full pipeline run completes.

        Updates run node with aggregate stats and triggers precedent linking.
        """
        run_node = self.graph.get_node(self._run_node.node_id)
        if run_node:
            run_node.data.update({
                "total_tests": results.get("total_tests", 0),
                "passed": results.get("passed", 0),
                "failed": results.get("failed", 0),
                "blocked": results.get("blocked", 0),
                "pass_rate": results.get("pass_rate", 0),
                "duration_s": results.get("duration_s", 0),
            })

        # Auto-link to similar past runs
        self.graph.link_precedents(self.run_id)

    def attribute_verdicts(
        self,
        test_results: List[Dict[str, Any]],
        fix_suggestions: Optional[List[Dict[str, Any]]] = None,
    ) -> List[str]:
        """Auto-classify failures as app_bug vs agent_bug vs environment.

        Uses heuristics from failure reason, fix suggestions, and
        precedent matching. Returns list of verdict node IDs created.

        Heuristics:
        - "timeout" / "connection" / "emulator" → environment_issue
        - "not found" / "selector" / "element" → selector_mismatch
        - "assertion failed" / "expected X got Y" → app_bug
        - "could not determine" / "ambiguous" → ui_ambiguity
        - Matches precedent with same fingerprint → inherit attribution
        """
        verdict_ids = []
        fix_map = {}
        if fix_suggestions:
            for fs in fix_suggestions:
                tid = fs.get("test_id", "")
                if tid:
                    fix_map[tid] = fs

        for result in test_results:
            status = result.get("status", "")
            if status in ("passed", "pass", "PASS"):
                continue  # No verdict needed for passing tests

            test_id = result.get("test_id", "")
            reason = (result.get("failure_reason") or "").lower()

            # Heuristic classification
            attribution = VerdictAttribution.UNKNOWN
            confidence = 0.5

            if any(kw in reason for kw in ("timeout", "connection", "emulator", "adb", "device")):
                attribution = VerdictAttribution.ENVIRONMENT_ISSUE
                confidence = 0.8
            elif any(kw in reason for kw in ("not found", "selector", "element", "no such", "stale")):
                attribution = VerdictAttribution.SELECTOR_MISMATCH
                confidence = 0.7
            elif any(kw in reason for kw in ("assertion", "expected", "mismatch", "wrong value")):
                attribution = VerdictAttribution.APP_BUG
                confidence = 0.7
            elif any(kw in reason for kw in ("ambiguous", "multiple", "unclear", "could not determine")):
                attribution = VerdictAttribution.UI_AMBIGUITY
                confidence = 0.6
            elif any(kw in reason for kw in ("misread", "misinterpret", "wrong screen")):
                attribution = VerdictAttribution.AGENT_MISREAD
                confidence = 0.7

            # Check fix suggestions for stronger signals
            fix = fix_map.get(test_id, {})
            if fix.get("likely_product_bug"):
                attribution = VerdictAttribution.APP_BUG
                confidence = max(confidence, 0.8)
            elif fix.get("likely_automation_issue"):
                attribution = VerdictAttribution.AGENT_MISREAD
                confidence = max(confidence, 0.75)

            # Check precedents
            fp = failure_fingerprint(test_id, reason)
            precedent = self.graph.find_by_fingerprint(fp)
            if precedent and precedent.node_type == NodeType.OUTCOME:
                # Look for existing verdict on precedent
                for edge, verdict_node in self.graph.get_outgoing(
                    precedent.node_id, EdgeType.OUTCOME_JUDGED_AS
                ):
                    prev_attr = verdict_node.data.get("attribution")
                    if prev_attr:
                        attribution = VerdictAttribution(prev_attr)
                        confidence = max(confidence, 0.85)
                        break

            # Create verdict node
            verdict_node = make_verdict_node(
                attribution=attribution,
                reasoning=result.get("failure_reason", "No reason provided"),
                run_id=self.run_id,
                confidence=confidence,
                test_id=test_id,
            )
            self.graph.add_node(verdict_node)

            # Find the outcome node for this test and wire it
            for outcome in self.graph.get_nodes_by_type(NodeType.OUTCOME):
                if (outcome.run_id == self.run_id
                        and outcome.data.get("test_id") == test_id):
                    self.graph.connect(
                        outcome.node_id,
                        verdict_node.node_id,
                        EdgeType.OUTCOME_JUDGED_AS,
                    )
                    break

            verdict_ids.append(verdict_node.node_id)

        logger.info(f"Attributed {len(verdict_ids)} verdicts for run {self.run_id}")
        return verdict_ids

    # ── Persistence ──────────────────────────────────────────────────────

    def save(self) -> None:
        """Persist the graph to disk."""
        ContextGraphManager.get().save_all()

    def get_stats(self) -> Dict[str, Any]:
        """Get graph stats for this run."""
        return {
            "run_id": self.run_id,
            "app_key": self.app_key,
            "graph_stats": self.graph.stats(),
            "verdict_stats": self.graph.get_verdict_stats(self.run_id),
        }


# ---------------------------------------------------------------------------
# Integration with existing pipeline — import these into qa_pipeline_service
# ---------------------------------------------------------------------------

def create_hooks_for_run(
    run_id: str,
    app_key: str,
    app_name: str = "",
) -> PipelineGraphHooks:
    """Factory: create graph hooks for a new pipeline run."""
    return PipelineGraphHooks(run_id=run_id, app_key=app_key, app_name=app_name)


__all__ = ["PipelineGraphHooks", "create_hooks_for_run"]
