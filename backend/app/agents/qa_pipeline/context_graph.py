"""Context Graph — unified execution judgment infrastructure for retention.sh + OpenClaw.

Connects task intent, UI state, observed evidence, chosen action, and judged
outcome so the system can debug, explain, and improve execution over time.

Four layers:
  Layer 1: Perception  — screenshots, OCR, DOM, accessibility, visual diffs
  Layer 2: State Graph — what screen/state the app is in, constraints active
  Layer 3: Action Graph — what was attempted, alternatives, precedent
  Layer 4: Verdict Graph — what succeeded/failed, why, what to patch

Works for both:
  retention.sh:  task → UI state → observation → action → outcome → verdict
  OpenClaw:   user request → context gathered → intent → agent routed → tools → response → feedback

Storage: backend/data/context_graph/{graph_id}.json
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_GRAPH_DIR = Path(__file__).resolve().parents[3] / "data" / "context_graph"
_GRAPH_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Node Types
# ---------------------------------------------------------------------------

class NodeType(str, Enum):
    """Primary node types in the context graph."""

    # ── retention.sh execution nodes ──
    TASK = "task"                    # log_in, create_account, checkout
    UI_STATE = "ui_state"            # login_screen, modal_open, spinner_loading
    OBSERVATION = "observation"      # screenshot, OCR text, accessibility tree
    ACTION = "action"                # tap, type, scroll, wait, call_tool
    INTENT = "intent"                # "I believe this is the login form"
    CONSTRAINT = "constraint"        # test_env_only, cannot_send_real_message
    OUTCOME = "outcome"              # success, failure, blocked, flaky
    VERDICT = "verdict"              # grounded, app_bug, agent_misread, env_issue
    PRECEDENT = "precedent"          # link to similar past run/path

    # ── OpenClaw Slack agent nodes ──
    REQUEST = "request"              # user message / command
    CONTEXT = "context"              # gathered context (threads, files, history)
    CLASSIFICATION = "classification"  # intent classified (qa, search, deep_sim)
    ROUTING = "routing"              # agent routed to specialist
    TOOL_CALL = "tool_call"          # MCP tool invocation
    RESPONSE = "response"            # agent response posted
    FEEDBACK = "feedback"            # user reaction / follow-up

    # ── Shared nodes ──
    RUN = "run"                      # pipeline run or conversation session
    SCREEN = "screen"                # reusable screen identity (from path_memory)
    WORKFLOW = "workflow"            # reusable workflow identity
    TEST_CASE = "test_case"          # reusable test case identity
    FEATURE = "feature"              # product feature (from linkage_graph)

    # ── Code-aware nodes ──
    CODE_FILE = "code_file"          # a source file in the repo
    CODE_SYMBOL = "code_symbol"      # function, class, component, hook, route handler
    ROUTE = "route"                  # HTTP endpoint (FastAPI route or React page route)
    SELECTOR = "selector"            # data-testid or accessibility selector
    COMMIT = "commit"                # git commit (sha + message)


# ---------------------------------------------------------------------------
# Edge Types
# ---------------------------------------------------------------------------

class EdgeType(str, Enum):
    """Typed relationships between nodes."""

    # ── Structural ──
    TASK_REQUIRES_STATE = "task_requires_state"
    RUN_CONTAINS = "run_contains"
    WORKFLOW_CONTAINS_STEP = "workflow_contains_step"

    # ── Perception ──
    STATE_OBSERVED_BY = "state_observed_by"
    OBSERVATION_SUPPORTS = "observation_supports"
    STATE_CONTRADICTS = "state_contradicts"

    # ── Action ──
    ACTION_TAKEN_FROM = "action_taken_from"
    ACTION_PRODUCED_STATE = "action_produced_state"
    ACTION_VIOLATED_CONSTRAINT = "action_violated_constraint"
    ACTION_REJECTED_ALTERNATIVE = "action_rejected_alternative"

    # ── Judgment ──
    OUTCOME_JUDGED_AS = "outcome_judged_as"
    VERDICT_ATTRIBUTED_TO = "verdict_attributed_to"
    FAILURE_FIXED_BY = "failure_fixed_by"

    # ── Precedent ──
    RUN_SIMILAR_TO = "run_similar_to"
    SCREEN_SAME_FAMILY = "screen_same_family"
    FAILURE_SAME_SIGNATURE = "failure_same_signature"

    # ── OpenClaw Slack ──
    REQUEST_GATHERED_CONTEXT = "request_gathered_context"
    REQUEST_CLASSIFIED_AS = "request_classified_as"
    CLASSIFICATION_ROUTED_TO = "classification_routed_to"
    ROUTING_CALLED_TOOL = "routing_called_tool"
    TOOL_PRODUCED_RESPONSE = "tool_produced_response"
    RESPONSE_GOT_FEEDBACK = "response_got_feedback"
    REQUEST_SUPERSEDES = "request_supersedes"
    CONTEXT_RESOLVED_BY = "context_resolved_by"

    # ── Feature linkage ──
    TESTS_FEATURE = "tests_feature"
    SCREEN_IMPLEMENTS = "screen_implements"
    COMMIT_AFFECTS = "commit_affects"

    # ── Code linkage ──
    SCREEN_BACKED_BY = "screen_backed_by"           # screen → code_symbol (component/page)
    ROUTE_HANDLED_BY = "route_handled_by"           # route → code_symbol (handler function)
    COMMIT_CHANGES_SYMBOL = "commit_changes_symbol" # commit → code_symbol
    RUN_VALIDATES_SYMBOL = "run_validates_symbol"   # run → code_symbol
    FAILURE_POINTS_TO_SYMBOL = "failure_points_to_symbol"  # outcome → code_symbol
    STEP_MATCHES_SELECTOR = "step_matches_selector" # action → selector


# ---------------------------------------------------------------------------
# Verdict Attribution — was it app bug, agent bug, or environment?
# ---------------------------------------------------------------------------

class VerdictAttribution(str, Enum):
    """Root cause category for a failure."""
    APP_BUG = "app_bug"                  # Real product defect
    AGENT_MISREAD = "agent_misread"      # Agent misinterpreted UI
    SELECTOR_MISMATCH = "selector_mismatch"  # Element not found / wrong element
    ENVIRONMENT_ISSUE = "environment_issue"  # Emulator, network, timing
    TASK_AMBIGUITY = "task_ambiguity"    # Task description unclear
    UI_AMBIGUITY = "ui_ambiguity"        # Multiple valid interpretations
    FLAKY = "flaky"                      # Non-deterministic failure
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Core Data Structures
# ---------------------------------------------------------------------------

class GraphNode:
    """A node in the context graph."""

    __slots__ = ("node_id", "node_type", "label", "data", "created_at",
                 "run_id", "fingerprint")

    def __init__(
        self,
        node_type: NodeType,
        label: str,
        data: Optional[Dict[str, Any]] = None,
        node_id: Optional[str] = None,
        run_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
    ):
        self.node_id = node_id or f"{node_type.value}_{uuid.uuid4().hex[:8]}"
        self.node_type = node_type
        self.label = label
        self.data = data or {}
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.run_id = run_id
        self.fingerprint = fingerprint  # For dedup / precedent matching

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "label": self.label,
            "data": self.data,
            "created_at": self.created_at,
            "run_id": self.run_id,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GraphNode":
        node = cls(
            node_type=NodeType(d["node_type"]),
            label=d["label"],
            data=d.get("data", {}),
            node_id=d["node_id"],
            run_id=d.get("run_id"),
            fingerprint=d.get("fingerprint"),
        )
        node.created_at = d.get("created_at", node.created_at)
        return node


class GraphEdge:
    """A directed edge between two nodes."""

    __slots__ = ("edge_type", "source_id", "target_id", "data", "created_at",
                 "weight")

    def __init__(
        self,
        edge_type: EdgeType,
        source_id: str,
        target_id: str,
        data: Optional[Dict[str, Any]] = None,
        weight: float = 1.0,
    ):
        self.edge_type = edge_type
        self.source_id = source_id
        self.target_id = target_id
        self.data = data or {}
        self.weight = weight
        self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "edge_type": self.edge_type.value,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "data": self.data,
            "weight": self.weight,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GraphEdge":
        edge = cls(
            edge_type=EdgeType(d["edge_type"]),
            source_id=d["source_id"],
            target_id=d["target_id"],
            data=d.get("data", {}),
            weight=d.get("weight", 1.0),
        )
        edge.created_at = d.get("created_at", edge.created_at)
        return edge


# ---------------------------------------------------------------------------
# Context Graph — in-memory graph with JSON persistence
# ---------------------------------------------------------------------------

class ContextGraph:
    """Unified context graph for retention.sh + OpenClaw.

    In-memory adjacency list with JSON persistence.
    Not Neo4j — lightweight, fast, and sufficient for our scale.
    """

    def __init__(self, graph_id: Optional[str] = None):
        self.graph_id = graph_id or f"cg_{uuid.uuid4().hex[:10]}"
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.created_at

        # Core storage
        self._nodes: Dict[str, GraphNode] = {}
        self._edges: List[GraphEdge] = []

        # Indexes for fast lookup
        self._outgoing: Dict[str, List[int]] = {}  # node_id → edge indices
        self._incoming: Dict[str, List[int]] = {}   # node_id → edge indices
        self._by_type: Dict[NodeType, Set[str]] = {}  # type → node_ids
        self._by_run: Dict[str, Set[str]] = {}       # run_id → node_ids
        self._by_fingerprint: Dict[str, str] = {}     # fingerprint → node_id

    # ── Node operations ──────────────────────────────────────────────────

    def add_node(self, node: GraphNode) -> str:
        """Add a node to the graph. Returns node_id."""
        self._nodes[node.node_id] = node

        # Update indexes
        self._by_type.setdefault(node.node_type, set()).add(node.node_id)
        if node.run_id:
            self._by_run.setdefault(node.run_id, set()).add(node.node_id)
        if node.fingerprint:
            self._by_fingerprint[node.fingerprint] = node.node_id

        self.updated_at = datetime.now(timezone.utc).isoformat()
        return node.node_id

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        return self._nodes.get(node_id)

    def get_nodes_by_type(self, node_type: NodeType) -> List[GraphNode]:
        ids = self._by_type.get(node_type, set())
        return [self._nodes[nid] for nid in ids if nid in self._nodes]

    def get_nodes_by_run(self, run_id: str) -> List[GraphNode]:
        ids = self._by_run.get(run_id, set())
        return [self._nodes[nid] for nid in ids if nid in self._nodes]

    def find_by_fingerprint(self, fingerprint: str) -> Optional[GraphNode]:
        nid = self._by_fingerprint.get(fingerprint)
        return self._nodes.get(nid) if nid else None

    # ── Edge operations ──────────────────────────────────────────────────

    def add_edge(self, edge: GraphEdge) -> int:
        """Add an edge. Returns edge index."""
        idx = len(self._edges)
        self._edges.append(edge)

        self._outgoing.setdefault(edge.source_id, []).append(idx)
        self._incoming.setdefault(edge.target_id, []).append(idx)

        self.updated_at = datetime.now(timezone.utc).isoformat()
        return idx

    def connect(
        self,
        source_id: str,
        target_id: str,
        edge_type: EdgeType,
        data: Optional[Dict[str, Any]] = None,
        weight: float = 1.0,
    ) -> int:
        """Convenience: create and add an edge in one call."""
        edge = GraphEdge(edge_type, source_id, target_id, data, weight)
        return self.add_edge(edge)

    def get_outgoing(self, node_id: str, edge_type: Optional[EdgeType] = None) -> List[Tuple[GraphEdge, GraphNode]]:
        """Get all outgoing edges + target nodes from a node."""
        results = []
        for idx in self._outgoing.get(node_id, []):
            edge = self._edges[idx]
            if edge_type and edge.edge_type != edge_type:
                continue
            target = self._nodes.get(edge.target_id)
            if target:
                results.append((edge, target))
        return results

    def get_incoming(self, node_id: str, edge_type: Optional[EdgeType] = None) -> List[Tuple[GraphEdge, GraphNode]]:
        """Get all incoming edges + source nodes to a node."""
        results = []
        for idx in self._incoming.get(node_id, []):
            edge = self._edges[idx]
            if edge_type and edge.edge_type != edge_type:
                continue
            source = self._nodes.get(edge.source_id)
            if source:
                results.append((edge, source))
        return results

    # ── Traversal / Query ────────────────────────────────────────────────

    def get_subgraph(self, run_id: str) -> "ContextGraph":
        """Extract a subgraph for a specific run."""
        sub = ContextGraph(graph_id=f"{self.graph_id}_run_{run_id}")
        node_ids = self._by_run.get(run_id, set())

        for nid in node_ids:
            node = self._nodes.get(nid)
            if node:
                sub.add_node(node)

        for edge in self._edges:
            if edge.source_id in node_ids and edge.target_id in node_ids:
                sub.add_edge(edge)

        return sub

    def get_failure_chain(self, outcome_node_id: str) -> List[GraphNode]:
        """Walk backwards from an outcome node to reconstruct the failure chain.

        Returns: [task, ui_state, observation, action, intent, outcome, verdict]
        """
        chain = []
        visited = set()

        def _walk_back(nid: str, depth: int = 0):
            if nid in visited or depth > 20:
                return
            visited.add(nid)
            node = self._nodes.get(nid)
            if not node:
                return
            chain.append(node)
            for edge, source in self.get_incoming(nid):
                _walk_back(source.node_id, depth + 1)

        _walk_back(outcome_node_id)
        # Reverse so chain reads task → ... → outcome
        chain.reverse()
        return chain

    def find_precedents(
        self,
        fingerprint: str,
        node_type: Optional[NodeType] = None,
        limit: int = 5,
    ) -> List[GraphNode]:
        """Find nodes with similar fingerprints (precedent matching).

        Uses prefix matching on fingerprint hashes for fuzzy similarity.
        """
        results = []
        prefix = fingerprint[:8]  # 8-char prefix for fuzzy match

        for fp, nid in self._by_fingerprint.items():
            if fp.startswith(prefix):
                node = self._nodes.get(nid)
                if node and (not node_type or node.node_type == node_type):
                    results.append(node)
                    if len(results) >= limit:
                        break

        return results

    def get_verdict_stats(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        """Aggregate verdict attribution stats."""
        verdict_nodes = self.get_nodes_by_type(NodeType.VERDICT)
        if run_id:
            verdict_nodes = [v for v in verdict_nodes if v.run_id == run_id]

        stats: Dict[str, int] = {}
        for v in verdict_nodes:
            attribution = v.data.get("attribution", VerdictAttribution.UNKNOWN.value)
            stats[attribution] = stats.get(attribution, 0) + 1

        total = sum(stats.values())
        return {
            "total_verdicts": total,
            "by_attribution": stats,
            "app_bug_rate": stats.get("app_bug", 0) / max(total, 1),
            "agent_bug_rate": (
                stats.get("agent_misread", 0) + stats.get("selector_mismatch", 0)
            ) / max(total, 1),
            "env_issue_rate": stats.get("environment_issue", 0) / max(total, 1),
        }

    # ── Precedent Linking ────────────────────────────────────────────────

    def link_precedents(self, current_run_id: str) -> int:
        """Auto-link the current run to similar past runs via fingerprints.

        Compares screen fingerprints, failure signatures, and workflow paths
        to find runs with overlapping context.

        Returns number of precedent edges created.
        """
        current_nodes = self.get_nodes_by_run(current_run_id)
        current_fps = {
            n.fingerprint for n in current_nodes if n.fingerprint
        }

        linked = 0
        seen_runs: Set[str] = set()

        for fp in current_fps:
            for past_fp, past_nid in self._by_fingerprint.items():
                if past_fp == fp:
                    continue
                # Check prefix similarity
                if past_fp[:8] != fp[:8]:
                    continue
                past_node = self._nodes.get(past_nid)
                if not past_node or not past_node.run_id:
                    continue
                if past_node.run_id == current_run_id:
                    continue
                if past_node.run_id in seen_runs:
                    continue

                seen_runs.add(past_node.run_id)

                # Create precedent link at run level
                current_run_nodes = [
                    n for n in current_nodes
                    if n.node_type == NodeType.RUN
                ]
                if current_run_nodes:
                    self.connect(
                        current_run_nodes[0].node_id,
                        past_nid,
                        EdgeType.RUN_SIMILAR_TO,
                        data={"shared_fingerprint_prefix": fp[:8]},
                    )
                    linked += 1

        logger.info(f"Linked {linked} precedent runs for {current_run_id}")
        return linked

    # ── Persistence ──────────────────────────────────────────────────────

    def save(self, path: Optional[Path] = None) -> Path:
        """Persist graph to JSON."""
        path = path or (_GRAPH_DIR / f"{self.graph_id}.json")
        data = {
            "graph_id": self.graph_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "node_count": len(self._nodes),
            "edge_count": len(self._edges),
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges],
        }
        path.write_text(json.dumps(data, indent=2, default=str))
        logger.info(f"Saved context graph {self.graph_id}: {len(self._nodes)} nodes, {len(self._edges)} edges")
        return path

    @classmethod
    def load(cls, path: Path) -> "ContextGraph":
        """Load graph from JSON."""
        data = json.loads(path.read_text())
        graph = cls(graph_id=data["graph_id"])
        graph.created_at = data.get("created_at", graph.created_at)
        graph.updated_at = data.get("updated_at", graph.updated_at)

        for nd in data.get("nodes", []):
            graph.add_node(GraphNode.from_dict(nd))
        for ed in data.get("edges", []):
            graph.add_edge(GraphEdge.from_dict(ed))

        logger.info(f"Loaded context graph {graph.graph_id}: {len(graph._nodes)} nodes, {len(graph._edges)} edges")
        return graph

    # ── Summary / Stats ──────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        type_counts = {}
        for nt, ids in self._by_type.items():
            type_counts[nt.value] = len(ids)

        edge_type_counts: Dict[str, int] = {}
        for e in self._edges:
            edge_type_counts[e.edge_type.value] = edge_type_counts.get(e.edge_type.value, 0) + 1

        return {
            "graph_id": self.graph_id,
            "total_nodes": len(self._nodes),
            "total_edges": len(self._edges),
            "total_runs": len(self._by_run),
            "nodes_by_type": type_counts,
            "edges_by_type": edge_type_counts,
            "verdict_stats": self.get_verdict_stats(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_mermaid(self, run_id: Optional[str] = None, max_nodes: int = 50) -> str:
        """Export graph as Mermaid diagram for visualization."""
        lines = ["graph TD"]
        nodes = self.get_nodes_by_run(run_id) if run_id else list(self._nodes.values())
        nodes = nodes[:max_nodes]
        node_ids = {n.node_id for n in nodes}

        # Shape by type
        shapes = {
            NodeType.TASK: ("[{label}]", "fill:#4CAF50"),
            NodeType.UI_STATE: ("({label})", "fill:#2196F3"),
            NodeType.OBSERVATION: ("[/{label}/]", "fill:#FF9800"),
            NodeType.ACTION: (">{label}]", "fill:#9C27B0"),
            NodeType.INTENT: ("{{{{label}}}}", "fill:#E91E63"),
            NodeType.OUTCOME: ("[[{label}]]", "fill:#F44336"),
            NodeType.VERDICT: ("(({label}))", "fill:#795548"),
            NodeType.REQUEST: ("[{label}]", "fill:#00BCD4"),
            NodeType.RESPONSE: (">{label}]", "fill:#8BC34A"),
        }

        for n in nodes:
            shape_tpl, _ = shapes.get(n.node_type, ("[{label}]", "fill:#999"))
            safe_label = n.label.replace('"', "'")[:40]
            shape = shape_tpl.replace("{label}", safe_label)
            lines.append(f"    {n.node_id}{shape}")

        for e in self._edges:
            if e.source_id in node_ids and e.target_id in node_ids:
                label = e.edge_type.value.replace("_", " ")[:20]
                lines.append(f"    {e.source_id} -->|{label}| {e.target_id}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Global Graph Manager — singleton that holds the active graph
# ---------------------------------------------------------------------------

class ContextGraphManager:
    """Manages the global context graph instance.

    Loads from disk on first access, saves periodically.
    Supports multiple app graphs via app_key scoping.
    """

    _instance: Optional["ContextGraphManager"] = None

    def __init__(self):
        self._graphs: Dict[str, ContextGraph] = {}
        self._global_graph: Optional[ContextGraph] = None

    @classmethod
    def get(cls) -> "ContextGraphManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def global_graph(self) -> ContextGraph:
        """The global cross-run context graph."""
        if self._global_graph is None:
            path = _GRAPH_DIR / "global.json"
            if path.exists():
                self._global_graph = ContextGraph.load(path)
            else:
                self._global_graph = ContextGraph(graph_id="global")
        return self._global_graph

    def get_app_graph(self, app_key: str) -> ContextGraph:
        """Get or create a per-app context graph."""
        if app_key not in self._graphs:
            path = _GRAPH_DIR / f"app_{app_key}.json"
            if path.exists():
                self._graphs[app_key] = ContextGraph.load(path)
            else:
                self._graphs[app_key] = ContextGraph(graph_id=f"app_{app_key}")
        return self._graphs[app_key]

    def save_all(self) -> int:
        """Persist all loaded graphs. Returns number saved."""
        count = 0
        if self._global_graph:
            self._global_graph.save()
            count += 1
        for app_key, graph in self._graphs.items():
            graph.save(_GRAPH_DIR / f"app_{app_key}.json")
            count += 1
        return count

    def list_graphs(self) -> List[Dict[str, Any]]:
        """List all persisted graphs with basic stats."""
        results = []
        for path in sorted(_GRAPH_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                results.append({
                    "graph_id": data.get("graph_id", path.stem),
                    "node_count": data.get("node_count", 0),
                    "edge_count": data.get("edge_count", 0),
                    "updated_at": data.get("updated_at"),
                    "path": str(path),
                })
            except Exception:
                pass
        return results


# ---------------------------------------------------------------------------
# Fingerprint Helpers — create stable hashes for precedent matching
# ---------------------------------------------------------------------------

def failure_fingerprint(
    test_name: str,
    failure_reason: str,
    screen_name: str = "",
) -> str:
    """Create a stable fingerprint for a failure signature.

    Used to detect when the same bug appears across runs.
    """
    sig = f"{test_name}|{failure_reason[:100]}|{screen_name}".lower()
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def action_path_fingerprint(actions: List[str]) -> str:
    """Fingerprint an action sequence for trajectory matching."""
    sig = "|".join(a.lower().strip()[:50] for a in actions)
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def conversation_fingerprint(
    user_message: str,
    intent: str = "",
) -> str:
    """Fingerprint a Slack conversation for precedent matching."""
    sig = f"{intent}|{user_message[:200]}".lower()
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Convenience constructors — build common node patterns
# ---------------------------------------------------------------------------

def make_task_node(
    task_name: str,
    task_goal: str,
    run_id: str,
    **extra: Any,
) -> GraphNode:
    return GraphNode(
        node_type=NodeType.TASK,
        label=task_name,
        data={"goal": task_goal, **extra},
        run_id=run_id,
    )


def make_ui_state_node(
    screen_name: str,
    screen_id: str,
    run_id: str,
    screen_fingerprint: str = "",
    **extra: Any,
) -> GraphNode:
    return GraphNode(
        node_type=NodeType.UI_STATE,
        label=screen_name,
        data={"screen_id": screen_id, **extra},
        run_id=run_id,
        fingerprint=screen_fingerprint,
    )


def make_observation_node(
    description: str,
    observation_type: str,
    run_id: str,
    evidence_path: Optional[str] = None,
    **extra: Any,
) -> GraphNode:
    return GraphNode(
        node_type=NodeType.OBSERVATION,
        label=description[:80],
        data={
            "observation_type": observation_type,
            "evidence_path": evidence_path,
            **extra,
        },
        run_id=run_id,
    )


def make_action_node(
    action_description: str,
    action_type: str,
    run_id: str,
    span_id: Optional[str] = None,
    **extra: Any,
) -> GraphNode:
    return GraphNode(
        node_type=NodeType.ACTION,
        label=action_description[:80],
        data={"action_type": action_type, "span_id": span_id, **extra},
        run_id=run_id,
    )


def make_intent_node(
    hypothesis: str,
    confidence: float,
    run_id: str,
    **extra: Any,
) -> GraphNode:
    return GraphNode(
        node_type=NodeType.INTENT,
        label=hypothesis[:80],
        data={"confidence": confidence, **extra},
        run_id=run_id,
    )


def make_outcome_node(
    status: str,
    test_id: str,
    run_id: str,
    failure_reason: Optional[str] = None,
    duration_ms: Optional[int] = None,
    **extra: Any,
) -> GraphNode:
    fp = failure_fingerprint(test_id, failure_reason or "", "")
    return GraphNode(
        node_type=NodeType.OUTCOME,
        label=f"{test_id}: {status}",
        data={
            "status": status,
            "test_id": test_id,
            "failure_reason": failure_reason,
            "duration_ms": duration_ms,
            **extra,
        },
        run_id=run_id,
        fingerprint=fp if failure_reason else None,
    )


def make_verdict_node(
    attribution: VerdictAttribution,
    reasoning: str,
    run_id: str,
    confidence: float = 0.0,
    test_id: str = "",
    **extra: Any,
) -> GraphNode:
    return GraphNode(
        node_type=NodeType.VERDICT,
        label=f"{attribution.value}: {reasoning[:60]}",
        data={
            "attribution": attribution.value,
            "reasoning": reasoning,
            "confidence": confidence,
            "test_id": test_id,
            **extra,
        },
        run_id=run_id,
    )


# ── OpenClaw Slack node constructors ──

def make_request_node(
    user_message: str,
    user_id: str,
    channel: str,
    thread_ts: str = "",
    session_id: str = "",
    **extra: Any,
) -> GraphNode:
    fp = conversation_fingerprint(user_message)
    return GraphNode(
        node_type=NodeType.REQUEST,
        label=user_message[:80],
        data={
            "user_id": user_id,
            "channel": channel,
            "thread_ts": thread_ts,
            "full_message": user_message,
            **extra,
        },
        run_id=session_id,
        fingerprint=fp,
    )


def make_context_node(
    context_type: str,
    summary: str,
    session_id: str,
    sources: Optional[List[str]] = None,
    **extra: Any,
) -> GraphNode:
    return GraphNode(
        node_type=NodeType.CONTEXT,
        label=f"{context_type}: {summary[:60]}",
        data={
            "context_type": context_type,
            "summary": summary,
            "sources": sources or [],
            **extra,
        },
        run_id=session_id,
    )


def make_classification_node(
    intent: str,
    confidence: float,
    session_id: str,
    **extra: Any,
) -> GraphNode:
    return GraphNode(
        node_type=NodeType.CLASSIFICATION,
        label=f"intent: {intent}",
        data={"intent": intent, "confidence": confidence, **extra},
        run_id=session_id,
    )


def make_tool_call_node(
    tool_name: str,
    params: Dict[str, Any],
    result_summary: str,
    session_id: str,
    status: str = "success",
    duration_ms: Optional[int] = None,
    **extra: Any,
) -> GraphNode:
    return GraphNode(
        node_type=NodeType.TOOL_CALL,
        label=f"{tool_name}: {status}",
        data={
            "tool_name": tool_name,
            "params": params,
            "result_summary": result_summary[:200],
            "status": status,
            "duration_ms": duration_ms,
            **extra,
        },
        run_id=session_id,
    )


def make_response_node(
    response_text: str,
    session_id: str,
    channel: str = "",
    thread_ts: str = "",
    **extra: Any,
) -> GraphNode:
    return GraphNode(
        node_type=NodeType.RESPONSE,
        label=response_text[:80],
        data={
            "full_response": response_text,
            "channel": channel,
            "thread_ts": thread_ts,
            **extra,
        },
        run_id=session_id,
    )


def make_feedback_node(
    feedback_type: str,
    content: str,
    session_id: str,
    user_id: str = "",
    **extra: Any,
) -> GraphNode:
    return GraphNode(
        node_type=NodeType.FEEDBACK,
        label=f"{feedback_type}: {content[:60]}",
        data={
            "feedback_type": feedback_type,
            "content": content,
            "user_id": user_id,
            **extra,
        },
        run_id=session_id,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enums
    "NodeType", "EdgeType", "VerdictAttribution",
    # Core
    "GraphNode", "GraphEdge", "ContextGraph", "ContextGraphManager",
    # Fingerprints
    "failure_fingerprint", "action_path_fingerprint", "conversation_fingerprint",
    # retention.sh constructors
    "make_task_node", "make_ui_state_node", "make_observation_node",
    "make_action_node", "make_intent_node", "make_outcome_node",
    "make_verdict_node",
    # OpenClaw constructors
    "make_request_node", "make_context_node", "make_classification_node",
    "make_tool_call_node", "make_response_node", "make_feedback_node",
]
