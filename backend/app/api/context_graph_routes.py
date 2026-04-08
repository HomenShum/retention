"""Context Graph API routes — expose graph data for the frontend UI and MCP tools.

Endpoints:
  GET  /api/context-graph/list                    — list all persisted graphs
  GET  /api/context-graph/{graph_id}              — get full graph data
  GET  /api/context-graph/{graph_id}/stats        — get graph statistics
  GET  /api/context-graph/{graph_id}/run/{run_id} — get subgraph for a run
  GET  /api/context-graph/{graph_id}/mermaid      — export as Mermaid diagram
  GET  /api/context-graph/{graph_id}/verdicts     — verdict attribution breakdown
  GET  /api/context-graph/{graph_id}/failure-chain/{node_id} — walk failure chain
  POST /api/context-graph/save-all                — persist all loaded graphs
"""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..agents.qa_pipeline.context_graph import (
    ContextGraph,
    ContextGraphManager,
    NodeType,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/context-graph", tags=["context-graph"])

_GRAPH_DIR = Path(__file__).resolve().parents[2] / "data" / "context_graph"


@router.get("/demo-report")
async def demo_report():
    """Mock pipeline report with mixed pass/fail for dogfooding verdict attribution UI."""
    return {
        "runs": [{
            "run_id": "demo-verdict-test",
            "app_name": "QuickCart Planted Bugs",
            "status": "complete",
            "started_at": "2026-03-25T02:42:56Z",
            "duration_s": 142,
            "result": {
                "test_cases": [
                    {"test_id": f"tc_{i:03d}", "name": n, "category": c, "priority": p,
                     "steps": s, "expected_result": e}
                    for i, (n, c, p, s, e) in enumerate([
                        ("Login with valid credentials", "smoke", "P0",
                         ["Navigate to login", "Enter username", "Enter password", "Click login"],
                         "User logged in"),
                        ("Search product by name", "functional", "P0",
                         ["Click search bar", "Type 'laptop'", "Press enter"],
                         "Search results shown"),
                        ("Add item to cart", "smoke", "P0",
                         ["Browse products", "Click Add to Cart"],
                         "Cart count increases"),
                        ("Checkout with empty cart", "edge_case", "P1",
                         ["Navigate to cart", "Click checkout"],
                         "Error message shown"),
                        ("Remove item from cart", "functional", "P1",
                         ["Add item", "Go to cart", "Click remove"],
                         "Item removed"),
                        ("Sort products by price", "functional", "P2",
                         ["Go to products", "Click sort dropdown", "Select price low-high"],
                         "Products sorted"),
                        ("Apply discount code", "regression", "P1",
                         ["Add item to cart", "Enter discount code", "Click apply"],
                         "Discount applied"),
                        ("Navigate to product detail", "smoke", "P0",
                         ["Browse products", "Click product image"],
                         "Product detail page shown"),
                        ("Filter by category", "functional", "P1",
                         ["Click category filter", "Select Electronics"],
                         "Filtered results"),
                        ("Mobile responsive layout", "accessibility", "P2",
                         ["Resize viewport to 375px", "Check layout"],
                         "Layout adapts"),
                    ])
                ],
                "execution": {"results": [
                    {"test_id": "tc_000", "name": "Login with valid credentials",
                     "status": "pass", "duration_ms": 3200},
                    {"test_id": "tc_001", "name": "Search product by name",
                     "status": "fail", "duration_ms": 8500,
                     "failure_reason": "Element not found: search input selector '.search-bar' does not match any element on the page"},
                    {"test_id": "tc_002", "name": "Add item to cart",
                     "status": "pass", "duration_ms": 4100},
                    {"test_id": "tc_003", "name": "Checkout with empty cart",
                     "status": "fail", "duration_ms": 5200,
                     "failure_reason": "Assertion failed: expected error message 'Cart is empty' but got no visible error text"},
                    {"test_id": "tc_004", "name": "Remove item from cart",
                     "status": "pass", "duration_ms": 6300},
                    {"test_id": "tc_005", "name": "Sort products by price",
                     "status": "fail", "duration_ms": 12000,
                     "failure_reason": "Timeout waiting for sort dropdown after 10000ms — emulator rendering delay suspected"},
                    {"test_id": "tc_006", "name": "Apply discount code",
                     "status": "fail", "duration_ms": 7800,
                     "failure_reason": "Assertion failed: expected price $45.00 after discount but got $50.00 — discount calculation wrong"},
                    {"test_id": "tc_007", "name": "Navigate to product detail",
                     "status": "pass", "duration_ms": 2900},
                    {"test_id": "tc_008", "name": "Filter by category",
                     "status": "fail", "duration_ms": 9100,
                     "failure_reason": "Element not found: could not locate category filter button with selector '[data-filter]'"},
                    {"test_id": "tc_009", "name": "Mobile responsive layout",
                     "status": "pass", "duration_ms": 4500},
                ]},
                "summary": {"total": 10, "passed": 5, "failed": 5, "pass_rate": 0.5},
            },
        }],
    }


@router.get("/list")
async def list_graphs():
    """List all persisted context graphs."""
    mgr = ContextGraphManager.get()
    return {"graphs": mgr.list_graphs()}


@router.get("/{graph_id}")
async def get_graph(graph_id: str):
    """Get full graph data for the frontend."""
    path = _GRAPH_DIR / f"{graph_id}.json"
    if not path.exists():
        raise HTTPException(404, f"Graph {graph_id} not found")

    graph = ContextGraph.load(path)
    return {
        "graph_id": graph.graph_id,
        "node_count": len(graph._nodes),
        "edge_count": len(graph._edges),
        "nodes": [n.to_dict() for n in graph._nodes.values()],
        "edges": [e.to_dict() for e in graph._edges],
    }


@router.get("/{graph_id}/stats")
async def get_graph_stats(graph_id: str):
    """Get graph statistics."""
    path = _GRAPH_DIR / f"{graph_id}.json"
    if not path.exists():
        raise HTTPException(404, f"Graph {graph_id} not found")

    graph = ContextGraph.load(path)
    return graph.stats()


@router.get("/{graph_id}/run/{run_id}")
async def get_run_subgraph(graph_id: str, run_id: str):
    """Get subgraph for a specific run."""
    path = _GRAPH_DIR / f"{graph_id}.json"
    if not path.exists():
        raise HTTPException(404, f"Graph {graph_id} not found")

    graph = ContextGraph.load(path)
    sub = graph.get_subgraph(run_id)

    return {
        "graph_id": sub.graph_id,
        "run_id": run_id,
        "node_count": len(sub._nodes),
        "edge_count": len(sub._edges),
        "nodes": [n.to_dict() for n in sub._nodes.values()],
        "edges": [e.to_dict() for e in sub._edges],
    }


@router.get("/{graph_id}/mermaid")
async def get_mermaid(graph_id: str, run_id: str = "", max_nodes: int = 50):
    """Export graph as Mermaid diagram."""
    path = _GRAPH_DIR / f"{graph_id}.json"
    if not path.exists():
        raise HTTPException(404, f"Graph {graph_id} not found")

    graph = ContextGraph.load(path)
    mermaid = graph.to_mermaid(run_id=run_id or None, max_nodes=max_nodes)
    return {"mermaid": mermaid}


@router.get("/{graph_id}/verdicts")
async def get_verdicts(graph_id: str, run_id: str = ""):
    """Get verdict attribution breakdown."""
    path = _GRAPH_DIR / f"{graph_id}.json"
    if not path.exists():
        raise HTTPException(404, f"Graph {graph_id} not found")

    graph = ContextGraph.load(path)
    return graph.get_verdict_stats(run_id=run_id or None)


@router.get("/{graph_id}/failure-chain/{node_id}")
async def get_failure_chain(graph_id: str, node_id: str):
    """Walk backwards from an outcome/verdict node to reconstruct the failure chain."""
    path = _GRAPH_DIR / f"{graph_id}.json"
    if not path.exists():
        raise HTTPException(404, f"Graph {graph_id} not found")

    graph = ContextGraph.load(path)
    chain = graph.get_failure_chain(node_id)

    return {
        "node_id": node_id,
        "chain_length": len(chain),
        "chain": [n.to_dict() for n in chain],
    }


@router.post("/save-all")
async def save_all():
    """Persist all loaded graphs to disk."""
    mgr = ContextGraphManager.get()
    count = mgr.save_all()
    return {"saved": count}


