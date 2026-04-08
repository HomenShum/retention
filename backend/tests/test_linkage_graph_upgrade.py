"""Tests for the upgraded Linkage Graph."""

import json
from pathlib import Path

from app.agents.qa_pipeline import linkage_graph


def test_link_symbol_to_feature_and_screen(monkeypatch, tmp_path: Path) -> None:
    graph_path = tmp_path / "linkage_graph.json"
    monkeypatch.setattr(linkage_graph, "_GRAPH_PATH", graph_path)

    # Bootstrap features and targets
    linkage_graph.register_feature("f1", "Auth", "auth stuff")
    linkage_graph.register_code_anchor("sym1", "route", "api/auth.py", "login", "/api/login")

    # Link sym1 to feat f1
    linkage_graph.link_symbol_to_feature("sym1", "f1")

    # Link sym1 to screen scr1
    linkage_graph.link_symbol_to_screen("sym1", "scr1", confidence="high")

    # Reload and verify
    data = json.loads(graph_path.read_text())
    assert "f1" in data["code_symbols"]["sym1"]["features"]
    assert "sym1" in data["features"]["f1"]["code_symbols"]

    screen_entries = data["code_symbols"]["sym1"]["screens"]
    assert any(e["screen_id"] == "scr1" and e["confidence"] == "high" for e in screen_entries)


def test_get_affected_features_via_symbols(monkeypatch, tmp_path: Path) -> None:
    graph_path = tmp_path / "linkage_graph.json"
    monkeypatch.setattr(linkage_graph, "_GRAPH_PATH", graph_path)

    linkage_graph.register_feature("f2", "Checkout", "")
    linkage_graph.register_code_anchor("sym2", "route", "backend/app/api/orders.py", "create_order")
    linkage_graph.link_symbol_to_feature("sym2", "f2")

    # The file path sent from the IDE might just be a suffix
    changed = ["app/api/orders.py"]
    affected = linkage_graph.get_affected_features(changed)
    
    assert len(affected) == 1
    assert affected[0]["feature_id"] == "f2"
    assert "Orders" in affected[0]["reason"] or "orders.py" in affected[0]["reason"]


def test_get_workflow_rerun_suggestions(monkeypatch, tmp_path: Path) -> None:
    graph_path = tmp_path / "linkage_graph.json"
    monkeypatch.setattr(linkage_graph, "_GRAPH_PATH", graph_path)

    linkage_graph.register_feature("f3", "Cart", "")
    linkage_graph.register_code_anchor("sym3", "component", "frontend/Cart.tsx", "CartComp")
    linkage_graph.link_symbol_to_feature("sym3", "f3")

    linkage_graph.link_workflow_to_feature("wf123", "f3")

    result = linkage_graph.get_workflow_rerun_suggestions(files_changed=["frontend/Cart.tsx"])
    assert "wf123" in result["workflow_ids"]
    assert any(feat["feature_id"] == "f3" for feat in result["affected_features"])
