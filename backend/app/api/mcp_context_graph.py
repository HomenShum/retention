"""MCP Context Graph tools — expose graph queries to external agents.

Called from mcp_server.py for:
  ta.graph.stats      — get graph statistics
  ta.graph.verdicts   — verdict attribution breakdown
  ta.graph.failure_chain — walk failure chain from outcome node
  ta.graph.precedents — find similar past runs
  ta.graph.list       — list all graphs
  ta.graph.mermaid    — export as Mermaid diagram
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_GRAPH_DIR = Path(__file__).resolve().parents[2] / "data" / "context_graph"


def _load_graph(graph_id: str):
    """Load a graph from disk. Returns ContextGraph or None."""
    from ..agents.qa_pipeline.context_graph import ContextGraph
    path = _GRAPH_DIR / f"{graph_id}.json"
    if not path.exists():
        return None
    return ContextGraph.load(path)


async def graph_list() -> Dict[str, Any]:
    """List all persisted context graphs."""
    from ..agents.qa_pipeline.context_graph import ContextGraphManager
    mgr = ContextGraphManager.get()
    graphs = mgr.list_graphs()
    return {"graphs": graphs, "count": len(graphs)}


async def graph_stats(graph_id: str = "global") -> Dict[str, Any]:
    """Get graph statistics including verdict attribution."""
    graph = _load_graph(graph_id)
    if not graph:
        return {"error": f"Graph '{graph_id}' not found"}
    return graph.stats()


async def graph_verdicts(graph_id: str = "global", run_id: str = "") -> Dict[str, Any]:
    """Get verdict attribution breakdown.

    Shows: app_bug_rate, agent_bug_rate, env_issue_rate, by_attribution counts.
    """
    graph = _load_graph(graph_id)
    if not graph:
        return {"error": f"Graph '{graph_id}' not found"}
    return graph.get_verdict_stats(run_id=run_id or None)


async def graph_failure_chain(graph_id: str, node_id: str) -> Dict[str, Any]:
    """Walk backwards from an outcome/verdict node to reconstruct the full failure chain.

    Returns: task → state → observation → action → outcome → verdict
    """
    graph = _load_graph(graph_id)
    if not graph:
        return {"error": f"Graph '{graph_id}' not found"}

    chain = graph.get_failure_chain(node_id)
    return {
        "node_id": node_id,
        "chain_length": len(chain),
        "chain": [
            {
                "node_id": n.node_id,
                "type": n.node_type.value,
                "label": n.label,
                "data": n.data,
            }
            for n in chain
        ],
    }


async def graph_precedents(
    graph_id: str,
    fingerprint: str,
    node_type: str = "",
    limit: int = 5,
) -> Dict[str, Any]:
    """Find similar past nodes by fingerprint prefix matching."""
    from ..agents.qa_pipeline.context_graph import NodeType

    graph = _load_graph(graph_id)
    if not graph:
        return {"error": f"Graph '{graph_id}' not found"}

    nt = NodeType(node_type) if node_type else None
    results = graph.find_precedents(fingerprint, nt, limit)

    return {
        "fingerprint": fingerprint,
        "matches": [
            {
                "node_id": n.node_id,
                "type": n.node_type.value,
                "label": n.label,
                "run_id": n.run_id,
                "fingerprint": n.fingerprint,
            }
            for n in results
        ],
    }


async def graph_mermaid(
    graph_id: str = "global",
    run_id: str = "",
    max_nodes: int = 50,
) -> Dict[str, Any]:
    """Export graph as Mermaid diagram for visualization."""
    graph = _load_graph(graph_id)
    if not graph:
        return {"error": f"Graph '{graph_id}' not found"}

    mermaid = graph.to_mermaid(run_id=run_id or None, max_nodes=max_nodes)
    return {"mermaid": mermaid}


# ---------------------------------------------------------------------------
# Slack Graph Queries (for OpenClaw agent)
# ---------------------------------------------------------------------------

async def slack_topic_history(
    keywords: str,
    limit: int = 10,
) -> Dict[str, Any]:
    """Search past Slack conversations by topic keywords."""
    from ..services.slack_graph_hooks import create_slack_hooks

    hooks = create_slack_hooks()
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    results = hooks.get_topic_history(kw_list, limit)
    return {"keywords": kw_list, "results": results, "count": len(results)}


async def slack_user_history(
    user_id: str,
    limit: int = 20,
) -> Dict[str, Any]:
    """Get all requests from a specific Slack user."""
    from ..services.slack_graph_hooks import create_slack_hooks

    hooks = create_slack_hooks()
    results = hooks.get_user_history(user_id, limit)
    return {"user_id": user_id, "results": results, "count": len(results)}


async def slack_open_items() -> Dict[str, Any]:
    """Find unresolved action items from deep sims and other interactions."""
    from ..services.slack_graph_hooks import create_slack_hooks

    hooks = create_slack_hooks()
    items = hooks.get_open_action_items()
    return {"open_items": items, "count": len(items)}


async def slack_similar_request(message: str, limit: int = 5) -> Dict[str, Any]:
    """Find past requests similar to a new message."""
    from ..services.slack_graph_hooks import create_slack_hooks

    hooks = create_slack_hooks()
    results = hooks.find_similar_request(message, limit)
    return {"message": message[:100], "similar": results, "count": len(results)}
