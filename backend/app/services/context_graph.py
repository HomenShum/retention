"""
Unified Contextual Graph for retention.sh.

Serves both the QA pipeline (device/emulator testing) and the OpenClaw Slack agent
(conversation/research). All node types share a common base so that precedent
matching, failure clustering, and lineage queries work across both domains.

Zero heavy dependencies — stdlib + json only.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Union


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class NodeKind(str, Enum):
    """Discriminator for every node stored in the graph."""
    TASK = "task"
    STATE = "state"
    OBSERVATION = "observation"
    ACTION = "action"
    HYPOTHESIS = "hypothesis"
    CONSTRAINT = "constraint"
    OUTCOME = "outcome"
    VERDICT = "verdict"
    PRECEDENT = "precedent"


class EdgeType(str, Enum):
    """All valid edge labels."""
    TASK_REQUIRES_STATE = "TASK_REQUIRES_STATE"
    STATE_OBSERVED_BY = "STATE_OBSERVED_BY"
    OBSERVATION_SUPPORTS_HYPOTHESIS = "OBSERVATION_SUPPORTS_HYPOTHESIS"
    ACTION_TAKEN_FROM_STATE = "ACTION_TAKEN_FROM_STATE"
    ACTION_EXPECTED_RESULT = "ACTION_EXPECTED_RESULT"
    ACTION_VIOLATED_CONSTRAINT = "ACTION_VIOLATED_CONSTRAINT"
    OUTCOME_JUDGED_AS = "OUTCOME_JUDGED_AS"
    RUN_SIMILAR_TO = "RUN_SIMILAR_TO"
    FAILURE_FIXED_BY = "FAILURE_FIXED_BY"
    STATE_CONTRADICTS_HYPOTHESIS = "STATE_CONTRADICTS_HYPOTHESIS"
    SUPERSEDES = "SUPERSEDES"
    RESOLVES = "RESOLVES"
    ESCALATED_TO = "ESCALATED_TO"


TaskSource = Literal["qa_pipeline", "slack_agent", "deep_sim"]

ObservationType = Literal[
    "screenshot", "ocr", "dom", "message_text",
    "file_attachment", "thread_context",
]

ActionType = Literal[
    "tap", "click", "type", "scroll",
    "tool_call", "agent_route", "slack_post", "api_call",
]

OutcomeStatus = Literal["success", "partial", "failure", "blocked", "flaky", "timeout"]

VerdictType = Literal["app_bug", "agent_bug", "environment", "correct", "inconclusive"]


# ---------------------------------------------------------------------------
# Node dataclasses
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class NodeBase:
    """Fields shared by every node."""
    id: str = field(default_factory=_uid)
    kind: NodeKind = field(default=NodeKind.TASK)
    run_id: str = ""
    created_at: str = field(default_factory=_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskNode(NodeBase):
    """A goal the system is trying to achieve."""
    kind: NodeKind = field(default=NodeKind.TASK, init=False)
    intent: str = ""
    goal_state: str = ""
    source: TaskSource = "qa_pipeline"


@dataclass
class StateNode(NodeBase):
    """A snapshot of app state or conversation state."""
    kind: NodeKind = field(default=NodeKind.STATE, init=False)
    app_state: str = ""
    conversation_state: str = ""
    observed_at: str = field(default_factory=_now_iso)
    screen_hash: str = ""
    components: List[str] = field(default_factory=list)


@dataclass
class ObservationNode(NodeBase):
    """Raw evidence collected from the environment."""
    kind: NodeKind = field(default=NodeKind.OBSERVATION, init=False)
    observation_type: ObservationType = "dom"
    content: str = ""
    artifact_path: str = ""


@dataclass
class ActionNode(NodeBase):
    """Something the agent or pipeline did."""
    kind: NodeKind = field(default=NodeKind.ACTION, init=False)
    action_type: ActionType = "click"
    target: str = ""
    result: str = ""
    expected_result: str = ""


@dataclass
class HypothesisNode(NodeBase):
    """A belief the agent holds, with confidence."""
    kind: NodeKind = field(default=NodeKind.HYPOTHESIS, init=False)
    agent_belief: str = ""
    confidence: float = 0.0
    supporting_evidence_ids: List[str] = field(default_factory=list)


@dataclass
class ConstraintNode(NodeBase):
    """A rule or limit that bounds behaviour."""
    kind: NodeKind = field(default=NodeKind.CONSTRAINT, init=False)
    constraint_type: Literal["policy", "timeout", "budget", "sandbox", "rate_limit"] = "policy"
    description: str = ""
    value: Any = None


@dataclass
class OutcomeNode(NodeBase):
    """The result of executing a task or action."""
    kind: NodeKind = field(default=NodeKind.OUTCOME, init=False)
    status: OutcomeStatus = "success"
    evidence: Dict[str, Any] = field(default_factory=dict)
    test_id: str = ""


@dataclass
class VerdictNode(NodeBase):
    """Final judgement about an outcome."""
    kind: NodeKind = field(default=NodeKind.VERDICT, init=False)
    verdict_type: VerdictType = "inconclusive"
    judge_model: str = ""
    confidence: float = 0.0
    reasoning: str = ""


@dataclass
class PrecedentNode(NodeBase):
    """Link to a similar past task for precedent matching."""
    kind: NodeKind = field(default=NodeKind.PRECEDENT, init=False)
    similar_task_id: str = ""
    similarity_score: float = 0.0
    outcome_of_precedent: OutcomeStatus = "success"


# Lookup: kind enum -> dataclass
_NODE_CLS = {
    NodeKind.TASK: TaskNode,
    NodeKind.STATE: StateNode,
    NodeKind.OBSERVATION: ObservationNode,
    NodeKind.ACTION: ActionNode,
    NodeKind.HYPOTHESIS: HypothesisNode,
    NodeKind.CONSTRAINT: ConstraintNode,
    NodeKind.OUTCOME: OutcomeNode,
    NodeKind.VERDICT: VerdictNode,
    NodeKind.PRECEDENT: PrecedentNode,
}

# Union type for convenience
Node = Union[
    TaskNode, StateNode, ObservationNode, ActionNode,
    HypothesisNode, ConstraintNode, OutcomeNode, VerdictNode,
    PrecedentNode,
]


# ---------------------------------------------------------------------------
# Edge dataclass
# ---------------------------------------------------------------------------

@dataclass
class Edge:
    """Directed, typed relationship between two nodes."""
    from_id: str
    to_id: str
    edge_type: EdgeType
    created_at: str = field(default_factory=_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Precedent match result
# ---------------------------------------------------------------------------

@dataclass
class PrecedentMatch:
    """Returned by find_precedents()."""
    precedent_node: PrecedentNode
    original_task: TaskNode
    similarity: float
    outcome: OutcomeStatus


# ---------------------------------------------------------------------------
# Graph metrics
# ---------------------------------------------------------------------------

@dataclass
class GraphMetrics:
    """Aggregate metrics computed from graph structure."""
    state_recognition_accuracy: float = 0.0
    action_appropriateness: float = 0.0
    hypothesis_validation_rate: float = 0.0
    precedent_reuse_lift: float = 0.0
    bug_attribution_accuracy: float = 0.0
    recovery_success_rate: float = 0.0


# ---------------------------------------------------------------------------
# ContextGraph
# ---------------------------------------------------------------------------

class ContextGraph:
    """
    In-memory directed graph of nodes and typed edges.

    Provides query helpers for lineage, clustering, and precedent matching,
    plus JSON persistence for replay and rerun scenarios.
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, Node] = {}
        self._edges: List[Edge] = []
        # Adjacency indexes for fast lookup
        self._out: Dict[str, List[int]] = {}  # node_id -> edge indexes
        self._in: Dict[str, List[int]] = {}   # node_id -> edge indexes

    # -- Core CRUD ---------------------------------------------------------

    def add_node(self, node: Node) -> str:
        """Insert a node and return its id."""
        self._nodes[node.id] = node
        self._out.setdefault(node.id, [])
        self._in.setdefault(node.id, [])
        return node.id

    def add_edge(
        self,
        from_id: str,
        to_id: str,
        edge_type: EdgeType,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Edge:
        """Create a directed edge between two existing nodes."""
        if from_id not in self._nodes:
            raise KeyError(f"Source node {from_id} not in graph")
        if to_id not in self._nodes:
            raise KeyError(f"Target node {to_id} not in graph")
        edge = Edge(from_id=from_id, to_id=to_id, edge_type=edge_type, metadata=metadata or {})
        idx = len(self._edges)
        self._edges.append(edge)
        self._out.setdefault(from_id, []).append(idx)
        self._in.setdefault(to_id, []).append(idx)
        return edge

    def get_node(self, node_id: str) -> Node:
        """Return a node by id, or raise KeyError."""
        return self._nodes[node_id]

    def get_edges(
        self,
        node_id: str,
        direction: Literal["out", "in", "both"] = "out",
        edge_type: Optional[EdgeType] = None,
    ) -> List[Edge]:
        """Return edges connected to *node_id*, optionally filtered by type."""
        indexes: List[int] = []
        if direction in ("out", "both"):
            indexes.extend(self._out.get(node_id, []))
        if direction in ("in", "both"):
            indexes.extend(self._in.get(node_id, []))
        edges = [self._edges[i] for i in indexes]
        if edge_type is not None:
            edges = [e for e in edges if e.edge_type == edge_type]
        return edges

    # -- Query helpers -----------------------------------------------------

    def nodes_by_kind(self, kind: NodeKind) -> List[Node]:
        """Return all nodes of a given kind."""
        return [n for n in self._nodes.values() if n.kind == kind]

    def nodes_by_run(self, run_id: str) -> List[Node]:
        """Return all nodes belonging to a specific run."""
        return [n for n in self._nodes.values() if n.run_id == run_id]

    def find_precedents(self, task_node: TaskNode, top_k: int = 5) -> List[PrecedentMatch]:
        """
        Find the most relevant precedent nodes for a given task.

        Uses simple keyword overlap between task intents as a zero-dependency
        similarity heuristic.  Returns up to *top_k* matches sorted by score.
        """
        task_tokens = set(task_node.intent.lower().split())
        if not task_tokens:
            return []

        scored: List[PrecedentMatch] = []
        for node in self.nodes_by_kind(NodeKind.PRECEDENT):
            assert isinstance(node, PrecedentNode)
            # Resolve the original task this precedent points to
            orig = self._nodes.get(node.similar_task_id)
            if orig is None or not isinstance(orig, TaskNode):
                continue
            orig_tokens = set(orig.intent.lower().split())
            if not orig_tokens:
                continue
            overlap = len(task_tokens & orig_tokens) / max(len(task_tokens | orig_tokens), 1)
            # Blend stored similarity_score with computed overlap
            blended = 0.5 * node.similarity_score + 0.5 * overlap
            scored.append(PrecedentMatch(
                precedent_node=node,
                original_task=orig,
                similarity=round(blended, 4),
                outcome=node.outcome_of_precedent,
            ))

        scored.sort(key=lambda m: m.similarity, reverse=True)
        return scored[:top_k]

    def get_failure_cluster(
        self,
        verdict_type: VerdictType,
        limit: int = 10,
    ) -> List[Node]:
        """Return verdict nodes matching a specific verdict type."""
        results: List[Node] = []
        for node in self.nodes_by_kind(NodeKind.VERDICT):
            assert isinstance(node, VerdictNode)
            if node.verdict_type == verdict_type:
                results.append(node)
                if len(results) >= limit:
                    break
        return results

    def get_task_lineage(self, task_id: str) -> List[Node]:
        """
        Walk the graph forward from *task_id* via outgoing edges (BFS)
        to collect every node in the causal chain from task to verdict.
        """
        if task_id not in self._nodes:
            return []
        visited: Dict[str, Node] = {}
        queue = [task_id]
        while queue:
            nid = queue.pop(0)
            if nid in visited:
                continue
            visited[nid] = self._nodes[nid]
            for edge in self.get_edges(nid, direction="out"):
                if edge.to_id not in visited:
                    queue.append(edge.to_id)
        return list(visited.values())

    # -- Metrics -----------------------------------------------------------

    def compute_metrics(self) -> GraphMetrics:
        """
        Derive aggregate metrics from graph structure.

        Each metric is a ratio in [0, 1].  Returns 0.0 when the denominator
        is zero (no relevant data).
        """
        metrics = GraphMetrics()

        # --- state_recognition_accuracy ---
        # Fraction of StateNodes that have at least one outgoing
        # STATE_OBSERVED_BY edge (i.e. they were actually used).
        states = self.nodes_by_kind(NodeKind.STATE)
        if states:
            observed = sum(
                1 for s in states
                if self.get_edges(s.id, "out", EdgeType.STATE_OBSERVED_BY)
            )
            metrics.state_recognition_accuracy = round(observed / len(states), 4)

        # --- action_appropriateness ---
        # Fraction of ActionNodes whose downstream OutcomeNode has status "success".
        actions = self.nodes_by_kind(NodeKind.ACTION)
        if actions:
            appropriate = 0
            for a in actions:
                for edge in self.get_edges(a.id, "out"):
                    target = self._nodes.get(edge.to_id)
                    if isinstance(target, OutcomeNode) and target.status == "success":
                        appropriate += 1
                        break
            metrics.action_appropriateness = round(appropriate / len(actions), 4)

        # --- hypothesis_validation_rate ---
        # Hypotheses confirmed (OBSERVATION_SUPPORTS_HYPOTHESIS edges exist
        # and no STATE_CONTRADICTS_HYPOTHESIS edges).
        hyps = self.nodes_by_kind(NodeKind.HYPOTHESIS)
        if hyps:
            confirmed = 0
            for h in hyps:
                supports = self.get_edges(h.id, "in", EdgeType.OBSERVATION_SUPPORTS_HYPOTHESIS)
                contradicts = self.get_edges(h.id, "in", EdgeType.STATE_CONTRADICTS_HYPOTHESIS)
                if supports and not contradicts:
                    confirmed += 1
            metrics.hypothesis_validation_rate = round(confirmed / len(hyps), 4)

        # --- precedent_reuse_lift ---
        # Fraction of tasks with a RUN_SIMILAR_TO edge that ended in success.
        tasks = self.nodes_by_kind(NodeKind.TASK)
        tasks_with_precedent = [
            t for t in tasks
            if self.get_edges(t.id, "out", EdgeType.RUN_SIMILAR_TO)
        ]
        if tasks_with_precedent:
            success_count = 0
            for t in tasks_with_precedent:
                lineage = self.get_task_lineage(t.id)
                if any(
                    isinstance(n, OutcomeNode) and n.status == "success"
                    for n in lineage
                ):
                    success_count += 1
            metrics.precedent_reuse_lift = round(success_count / len(tasks_with_precedent), 4)

        # --- bug_attribution_accuracy ---
        # Fraction of verdicts with confidence >= 0.8 (proxy for high-conviction judgements).
        verdicts = self.nodes_by_kind(NodeKind.VERDICT)
        if verdicts:
            high_conf = sum(
                1 for v in verdicts
                if isinstance(v, VerdictNode) and v.confidence >= 0.8
            )
            metrics.bug_attribution_accuracy = round(high_conf / len(verdicts), 4)

        # --- recovery_success_rate ---
        # Outcomes that follow a "failure" outcome via FAILURE_FIXED_BY and are "success".
        failure_edges = [e for e in self._edges if e.edge_type == EdgeType.FAILURE_FIXED_BY]
        if failure_edges:
            recovered = 0
            for e in failure_edges:
                target = self._nodes.get(e.to_id)
                if isinstance(target, OutcomeNode) and target.status == "success":
                    recovered += 1
            metrics.recovery_success_rate = round(recovered / len(failure_edges), 4)

        return metrics

    # -- Serialisation -----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialise entire graph to a plain dict (JSON-safe)."""
        return {
            "nodes": {nid: asdict(n) for nid, n in self._nodes.items()},
            "edges": [asdict(e) for e in self._edges],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContextGraph":
        """Reconstruct a graph from a dict produced by to_dict()."""
        g = cls()
        for nid, ndict in data.get("nodes", {}).items():
            kind = NodeKind(ndict["kind"])
            node_cls = _NODE_CLS[kind]
            # Filter keys to match the dataclass fields
            valid_fields = {f.name for f in node_cls.__dataclass_fields__.values()}
            filtered = {k: v for k, v in ndict.items() if k in valid_fields}
            # Restore enum for 'kind' — the init=False field is set by default
            filtered.pop("kind", None)
            node = node_cls(**filtered)
            g.add_node(node)
        for edict in data.get("edges", []):
            edge_type = EdgeType(edict["edge_type"])
            try:
                g.add_edge(
                    edict["from_id"],
                    edict["to_id"],
                    edge_type,
                    edict.get("metadata", {}),
                )
            except KeyError:
                # Dangling edge reference — skip gracefully on load
                pass
        return g

    def save(self, path: Union[str, Path]) -> None:
        """Persist the graph as JSON to *path*."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)

    @classmethod
    def load(cls, path: Union[str, Path]) -> "ContextGraph":
        """Load a graph from a JSON file."""
        with open(path, "r") as f:
            return cls.from_dict(json.load(f))

    # -- Summary -----------------------------------------------------------

    def __repr__(self) -> str:
        return f"<ContextGraph nodes={len(self._nodes)} edges={len(self._edges)}>"


# ---------------------------------------------------------------------------
# Default persistence directory
# ---------------------------------------------------------------------------

_DEFAULT_STORAGE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "context_graphs"


def _storage_path(run_id: str) -> Path:
    return _DEFAULT_STORAGE_DIR / f"{run_id}.json"


# ---------------------------------------------------------------------------
# Pipeline Integration Hooks (for qa_pipeline_service.py)
# ---------------------------------------------------------------------------

class PipelineHooks:
    """
    Convenience methods that create and wire nodes for common QA pipeline
    events.  Each method returns the created node so callers can chain.

    Usage::

        graph = ContextGraph()
        hooks = PipelineHooks(graph)
        task = hooks.on_crawl_start("run-001", "https://myapp.com")
    """

    def __init__(self, graph: ContextGraph) -> None:
        self.graph = graph

    def on_crawl_start(self, run_id: str, app_url: str) -> TaskNode:
        """Record the beginning of a crawl run."""
        node = TaskNode(
            run_id=run_id,
            intent=f"crawl {app_url}",
            goal_state="app fully explored",
            source="qa_pipeline",
            metadata={"app_url": app_url},
        )
        self.graph.add_node(node)
        return node

    def on_screen_discovered(
        self,
        run_id: str,
        screen_hash: str,
        components: List[str],
    ) -> StateNode:
        """Record a newly discovered screen."""
        node = StateNode(
            run_id=run_id,
            app_state=f"screen:{screen_hash}",
            screen_hash=screen_hash,
            components=components,
        )
        self.graph.add_node(node)
        # Link to the crawl task if one exists
        for t in self.graph.nodes_by_kind(NodeKind.TASK):
            if t.run_id == run_id and isinstance(t, TaskNode) and t.intent.startswith("crawl"):
                self.graph.add_edge(t.id, node.id, EdgeType.TASK_REQUIRES_STATE)
                break
        return node

    def on_action_taken(
        self,
        run_id: str,
        action_type: ActionType,
        target: str,
        result: str,
    ) -> ActionNode:
        """Record an action the agent performed on the device."""
        node = ActionNode(
            run_id=run_id,
            action_type=action_type,
            target=target,
            result=result,
        )
        self.graph.add_node(node)
        # Link to most recent state for this run
        states = [
            s for s in self.graph.nodes_by_kind(NodeKind.STATE)
            if s.run_id == run_id
        ]
        if states:
            latest = max(states, key=lambda s: s.created_at)
            self.graph.add_edge(node.id, latest.id, EdgeType.ACTION_TAKEN_FROM_STATE)
        return node

    def on_test_generated(self, run_id: str, test_case: Dict[str, Any]) -> TaskNode:
        """Record a generated test case as a sub-task."""
        node = TaskNode(
            run_id=run_id,
            intent=test_case.get("description", "test case"),
            goal_state=test_case.get("expected", "pass"),
            source="qa_pipeline",
            metadata={"test_case": test_case},
        )
        self.graph.add_node(node)
        return node

    def on_test_executed(
        self,
        run_id: str,
        test_id: str,
        passed: bool,
        evidence: Dict[str, Any],
    ) -> OutcomeNode:
        """Record the result of executing a test case."""
        node = OutcomeNode(
            run_id=run_id,
            status="success" if passed else "failure",
            test_id=test_id,
            evidence=evidence,
        )
        self.graph.add_node(node)
        # Link to the test task node if one matches
        for t in self.graph.nodes_by_kind(NodeKind.TASK):
            if (
                t.run_id == run_id
                and isinstance(t, TaskNode)
                and t.metadata.get("test_case", {}).get("id") == test_id
            ):
                self.graph.add_edge(t.id, node.id, EdgeType.ACTION_EXPECTED_RESULT)
                break
        return node

    def on_verdict(
        self,
        run_id: str,
        test_id: str,
        verdict_type: VerdictType,
        confidence: float,
        judge_model: str = "",
        reasoning: str = "",
    ) -> VerdictNode:
        """Record a verdict about a test outcome."""
        node = VerdictNode(
            run_id=run_id,
            verdict_type=verdict_type,
            judge_model=judge_model,
            confidence=confidence,
            reasoning=reasoning,
            metadata={"test_id": test_id},
        )
        self.graph.add_node(node)
        # Link from the outcome node
        for o in self.graph.nodes_by_kind(NodeKind.OUTCOME):
            if isinstance(o, OutcomeNode) and o.run_id == run_id and o.test_id == test_id:
                self.graph.add_edge(o.id, node.id, EdgeType.OUTCOME_JUDGED_AS)
                break
        return node

    def on_pipeline_complete(self, run_id: str) -> None:
        """
        Finalize a pipeline run: attempt to link precedents from prior runs
        and persist the graph.
        """
        # Look for prior saved graphs to source precedents
        if _DEFAULT_STORAGE_DIR.exists():
            for p in _DEFAULT_STORAGE_DIR.glob("*.json"):
                prior_run_id = p.stem
                if prior_run_id == run_id:
                    continue
                try:
                    prior = ContextGraph.load(p)
                except Exception:
                    continue
                # Compare task intents
                current_tasks = [
                    t for t in self.graph.nodes_by_kind(NodeKind.TASK)
                    if t.run_id == run_id and isinstance(t, TaskNode)
                ]
                prior_tasks = [
                    t for t in prior.nodes_by_kind(NodeKind.TASK)
                    if isinstance(t, TaskNode)
                ]
                for ct in current_tasks:
                    ct_tokens = set(ct.intent.lower().split())
                    for pt in prior_tasks:
                        pt_tokens = set(pt.intent.lower().split())
                        if not ct_tokens or not pt_tokens:
                            continue
                        sim = len(ct_tokens & pt_tokens) / max(len(ct_tokens | pt_tokens), 1)
                        if sim >= 0.4:
                            # Find the outcome for the prior task
                            prior_lineage = prior.get_task_lineage(pt.id)
                            prior_outcomes = [
                                n for n in prior_lineage
                                if isinstance(n, OutcomeNode)
                            ]
                            outcome_status: OutcomeStatus = "success"
                            if prior_outcomes:
                                outcome_status = prior_outcomes[-1].status
                            prec = PrecedentNode(
                                run_id=run_id,
                                similar_task_id=ct.id,
                                similarity_score=round(sim, 4),
                                outcome_of_precedent=outcome_status,
                                metadata={"prior_run_id": prior_run_id, "prior_task_id": pt.id},
                            )
                            self.graph.add_node(prec)
                            self.graph.add_edge(ct.id, prec.id, EdgeType.RUN_SIMILAR_TO)

        # Persist
        self.graph.save(_storage_path(run_id))


# ---------------------------------------------------------------------------
# Slack Agent Hooks (for slack-channel-observer / stream-agent-to-slack)
# ---------------------------------------------------------------------------

class SlackAgentHooks:
    """
    Convenience methods for wiring Slack/OpenClaw agent events into the
    same graph that the QA pipeline uses.

    Usage::

        graph = ContextGraph()
        hooks = SlackAgentHooks(graph)
        task = hooks.on_message_received("#general", "1234.5678", "U123", "search for X")
    """

    def __init__(self, graph: ContextGraph) -> None:
        self.graph = graph

    def on_message_received(
        self,
        channel: str,
        ts: str,
        user: str,
        text: str,
        files: Optional[List[Dict[str, Any]]] = None,
    ) -> TaskNode:
        """Record an incoming Slack message as a new task."""
        task_id = _uid()
        node = TaskNode(
            id=task_id,
            run_id=f"slack-{channel}-{ts}",
            intent=text[:256],
            goal_state="user request fulfilled",
            source="slack_agent",
            metadata={
                "channel": channel,
                "ts": ts,
                "user": user,
                "files": files or [],
            },
        )
        self.graph.add_node(node)

        # If files are attached, record them as observations
        for finfo in files or []:
            obs = ObservationNode(
                run_id=node.run_id,
                observation_type="file_attachment",
                content=finfo.get("name", ""),
                artifact_path=finfo.get("url_private", ""),
                metadata=finfo,
            )
            self.graph.add_node(obs)
            self.graph.add_edge(node.id, obs.id, EdgeType.STATE_OBSERVED_BY)

        return node

    def on_intent_classified(
        self,
        task_id: str,
        intent: str,
        confidence: float,
    ) -> HypothesisNode:
        """Record the agent's belief about what the user wants."""
        node = HypothesisNode(
            run_id=self.graph.get_node(task_id).run_id,
            agent_belief=intent,
            confidence=confidence,
            supporting_evidence_ids=[task_id],
        )
        self.graph.add_node(node)
        self.graph.add_edge(task_id, node.id, EdgeType.OBSERVATION_SUPPORTS_HYPOTHESIS)
        return node

    def on_agent_routed(
        self,
        task_id: str,
        agent_name: str,
        tools: List[str],
    ) -> ActionNode:
        """Record which specialist agent was chosen."""
        node = ActionNode(
            run_id=self.graph.get_node(task_id).run_id,
            action_type="agent_route",
            target=agent_name,
            result="",
            metadata={"tools": tools},
        )
        self.graph.add_node(node)
        self.graph.add_edge(task_id, node.id, EdgeType.ACTION_TAKEN_FROM_STATE)
        return node

    def on_response_posted(
        self,
        task_id: str,
        response_text: str,
        thread_ts: str,
    ) -> OutcomeNode:
        """Record that the agent posted a response."""
        node = OutcomeNode(
            run_id=self.graph.get_node(task_id).run_id,
            status="success",
            evidence={"response_text": response_text[:512], "thread_ts": thread_ts},
        )
        self.graph.add_node(node)
        self.graph.add_edge(task_id, node.id, EdgeType.ACTION_EXPECTED_RESULT)
        return node

    def on_deep_sim_started(
        self,
        task_id: str,
        topic: str,
        roles: List[str],
    ) -> TaskNode:
        """Record the start of a deep simulation (multi-role deliberation)."""
        node = TaskNode(
            run_id=self.graph.get_node(task_id).run_id,
            intent=f"deep_sim: {topic}",
            goal_state="consensus or structured divergence",
            source="deep_sim",
            metadata={"topic": topic, "roles": roles, "parent_task_id": task_id},
        )
        self.graph.add_node(node)
        self.graph.add_edge(task_id, node.id, EdgeType.TASK_REQUIRES_STATE)
        return node

    def on_deep_sim_role_response(
        self,
        task_id: str,
        role: str,
        content: str,
    ) -> ObservationNode:
        """Record a single role's contribution in a deep simulation."""
        node = ObservationNode(
            run_id=self.graph.get_node(task_id).run_id,
            observation_type="message_text",
            content=content[:1024],
            metadata={"role": role},
        )
        self.graph.add_node(node)
        self.graph.add_edge(task_id, node.id, EdgeType.STATE_OBSERVED_BY)
        return node

    def on_deep_sim_synthesis(
        self,
        task_id: str,
        consensus: str,
        divergences: List[str],
    ) -> VerdictNode:
        """Record the synthesis / outcome of a deep simulation."""
        node = VerdictNode(
            run_id=self.graph.get_node(task_id).run_id,
            verdict_type="correct" if consensus else "inconclusive",
            confidence=0.9 if consensus else 0.4,
            reasoning=consensus,
            metadata={"divergences": divergences},
        )
        self.graph.add_node(node)
        self.graph.add_edge(task_id, node.id, EdgeType.OUTCOME_JUDGED_AS)
        return node

    def on_user_feedback(
        self,
        task_id: str,
        reaction: str,
        reply_text: str,
    ) -> VerdictNode:
        """Record user feedback as a human verdict."""
        positive = reaction in ("+1", "thumbsup", "white_check_mark", "heavy_check_mark", "heart")
        node = VerdictNode(
            run_id=self.graph.get_node(task_id).run_id,
            verdict_type="correct" if positive else "agent_bug",
            judge_model="human",
            confidence=1.0,
            reasoning=reply_text[:512],
            metadata={"reaction": reaction},
        )
        self.graph.add_node(node)
        self.graph.add_edge(task_id, node.id, EdgeType.OUTCOME_JUDGED_AS)
        return node
