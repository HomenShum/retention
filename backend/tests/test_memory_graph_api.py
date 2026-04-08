import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main as main_module


def test_memory_graph_endpoint_returns_normalized_graph(monkeypatch, tmp_path: Path) -> None:
    fake_main = tmp_path / "backend" / "app" / "main.py"
    crawl_dir = tmp_path / "backend" / "data" / "exploration_memory" / "crawl"
    crawl_dir.mkdir(parents=True)
    monkeypatch.setattr(main_module, "__file__", str(fake_main))

    payload = {
        "app_name": "Demo Graph App",
        "app_url": "https://example.com/demo",
        "crawl_fingerprint": "fp-demo-123",
        "screen_graph": {"screen_001": "fp1", "screen_002": "fp2", "screen_003": "fp3"},
        "crawl_data": {
            "screens": [
                {
                    "screen_id": "screen_001",
                    "screen_name": "Home",
                    "screenshot_path": "/tmp/shots/home.png",
                    "navigation_depth": 0,
                    "components": [
                        {"element_type": "button", "text": "Search", "action": "tap_search", "is_interactive": True},
                        {"element_type": "text", "text": "Welcome", "is_interactive": False},
                    ],
                },
                {
                    "screen_id": "screen_002",
                    "screen_name": "Results",
                    "screenshot_path": "/tmp/shots/results.png",
                    "navigation_depth": 1,
                    "parent_screen_id": "screen_001",
                    "trigger_action": "Tapped Search",
                    "components": [
                        {"element_type": "button", "text": "Open Item", "action": "tap_item", "is_interactive": True},
                    ],
                },
                {
                    "screen_id": "screen_003",
                    "screen_name": "Detail",
                    "navigation_depth": 2,
                    "parent_screen_id": "screen_002",
                    "trigger_action": "Tapped Item",
                    "components": [
                        {"element_type": "button", "text": "Download", "action": "tap_download", "is_interactive": True},
                        {"element_type": "button", "text": "Share", "action": "tap_share", "is_interactive": True},
                    ],
                },
            ],
            "transitions": [
                {"from_screen": "screen_001", "to_screen": "screen_002", "action": "Tapped Search", "edge_type": "action"},
                {"from_screen": "screen_002", "to_screen": "screen_003", "action": "Tapped Item", "edge_type": "navigation"},
                {"from_screen": "screen_003", "to_screen": "screen_001", "action": "Back Home", "edge_type": "recovery"},
            ],
        },
    }
    (crawl_dir / "demo_graph.json").write_text(json.dumps(payload))

    client = TestClient(main_module.app)
    response = client.get("/api/memory/app/demo_graph/graph")
    assert response.status_code == 200

    body = response.json()
    assert body["app_name"] == "Demo Graph App"
    assert body["app_url"] == "https://example.com/demo"
    assert body["crawl_fingerprint"] == "fp-demo-123"
    assert body["total_screens"] == 3
    assert body["total_components"] == 5
    assert body["max_depth"] == 2
    assert len(body["hierarchy_edges"]) == 2
    assert len(body["transitions"]) == 1
    assert body["transitions"][0]["from_screen"] == "screen_003"
    assert body["transitions"][0]["edge_type"] == "recovery"
    assert body["screens"][0]["screenshot_url"] == "/static/screenshots/home.png"
    assert [screen["crawl_index"] for screen in body["screens"]] == [0, 1, 2]
    assert body["screens"][1]["interactive_count"] == 1
    assert body["screens"][2]["interactive_elements"] == [
        {"type": "button", "text": "Download", "action": "tap_download"},
        {"type": "button", "text": "Share", "action": "tap_share"},
    ]


def test_memory_graph_endpoint_normalizes_invalid_hierarchy(monkeypatch, tmp_path: Path) -> None:
    fake_main = tmp_path / "backend" / "app" / "main.py"
    crawl_dir = tmp_path / "backend" / "data" / "exploration_memory" / "crawl"
    crawl_dir.mkdir(parents=True)
    monkeypatch.setattr(main_module, "__file__", str(fake_main))

    payload = {
        "app_name": "Broken Graph App",
        "app_url": "https://example.com/broken",
        "crawl_fingerprint": "broken-graph",
        "screen_graph": {"screen_001": "fp1", "screen_002": "fp2", "screen_003": "fp3"},
        "crawl_data": {
            "screens": [
                {
                    "screen_id": "screen_001",
                    "screen_name": "Home",
                    "navigation_depth": 7,
                    "parent_screen_id": "missing_parent",
                    "components": [],
                },
                {
                    "screen_id": "screen_002",
                    "screen_name": "Search",
                    "navigation_depth": 9,
                    "parent_screen_id": "screen_001",
                    "trigger_action": "Tap Search",
                    "components": [],
                },
                {
                    "screen_id": "screen_003",
                    "screen_name": "Receipt",
                    "navigation_depth": 12,
                    "parent_screen_id": "screen_999",
                    "components": [],
                },
            ],
            "transitions": [
                {"from_screen": "screen_001", "to_screen": "screen_002", "action": "Tap Search", "edge_type": "action"},
                {"from_screen": "screen_002", "to_screen": "screen_003", "action": "View Receipt", "edge_type": "navigation"},
                {"from_screen": "ghost", "to_screen": "screen_003", "action": "Invalid", "edge_type": "recovery"},
            ],
        },
    }
    (crawl_dir / "broken_graph.json").write_text(json.dumps(payload))

    client = TestClient(main_module.app)
    response = client.get("/api/memory/app/broken_graph/graph")
    assert response.status_code == 200

    body = response.json()
    assert [(screen["screen_id"], screen["crawl_index"]) for screen in body["screens"]] == [
        ("screen_001", 0),
        ("screen_002", 1),
        ("screen_003", 2),
    ]
    assert body["screens"][0]["parent_screen_id"] is None
    assert body["screens"][0]["navigation_depth"] == 0
    assert body["screens"][1]["parent_screen_id"] == "screen_001"
    assert body["screens"][1]["navigation_depth"] == 1
    assert body["screens"][2]["parent_screen_id"] is None
    assert body["screens"][2]["navigation_depth"] == 0
    assert body["hierarchy_edges"] == [
        {
            "from_screen": "screen_001",
            "to_screen": "screen_002",
            "action": "Tap Search",
            "edge_type": "hierarchy",
        }
    ]
    assert body["transitions"] == [
        {
            "from_screen": "screen_002",
            "to_screen": "screen_003",
            "action": "View Receipt",
            "edge_type": "navigation",
        }
    ]


def test_memory_graph_endpoint_returns_not_found_for_missing_app() -> None:
    client = TestClient(main_module.app)
    response = client.get("/api/memory/app/does_not_exist/graph")
    assert response.status_code == 200
    assert response.json() == {"error": "not_found", "app_key": "does_not_exist"}