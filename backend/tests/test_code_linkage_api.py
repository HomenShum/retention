"""Tests for the Code Linkage impact API endpoints."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.agents.qa_pipeline import linkage_graph


@pytest.fixture
def api_client(monkeypatch, tmp_path: Path) -> TestClient:
    graph_path = tmp_path / "linkage_graph.json"
    monkeypatch.setattr(linkage_graph, "_GRAPH_PATH", graph_path)
    
    # Pre-populate graph
    graph = linkage_graph._load_graph()
    graph["screens"]["scr_auth"] = {}
    linkage_graph._save_graph(graph)
    linkage_graph.register_feature("f_api", "API Feature", "")
    linkage_graph.register_code_anchor("sym_api", "hook", "hooks/useAuth.ts", "useAuth")
    linkage_graph.link_symbol_to_feature("sym_api", "f_api")
    linkage_graph.link_workflow_to_feature("wf_auth", "f_api")
    linkage_graph.link_symbol_to_screen("sym_api", "scr_auth")

    # Mock code indexer so it doesn't really scan
    from app.services import code_indexer
    monkeypatch.setattr(code_indexer, "_CACHED_INDEX", {"entity_count": 99})

    return TestClient(app)


def test_impact_endpoint(api_client: TestClient) -> None:
    payload = {"files_changed": ["useAuth.ts"]}
    response = api_client.post("/api/code-linkage/impact", json=payload)
    
    assert response.status_code == 200
    data = response.json()
    assert "useAuth.ts" in data["files_changed"]
    assert "wf_auth" in data["workflow_ids"]
    assert len(data["affected_features"]) == 1
    assert data["affected_features"][0]["feature_id"] == "f_api"


def test_screen_linkage_endpoint(api_client: TestClient) -> None:
    response = api_client.get("/api/code-linkage/screen/scr_auth")
    
    assert response.status_code == 200
    data = response.json()
    assert data["screen_id"] == "scr_auth"
    assert len(data["anchors"]) == 1
    assert data["anchors"][0]["entity_id"] == "sym_api"
    assert "f_api" in data["linked_features"]


def test_workflow_linkage_endpoint(api_client: TestClient) -> None:
    response = api_client.get("/api/code-linkage/workflow/wf_auth")
    
    assert response.status_code == 200
    data = response.json()
    assert data["workflow_id"] == "wf_auth"
    assert "f_api" in data["feature_ids"]
