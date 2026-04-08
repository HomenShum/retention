import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.qa_pipeline import exploration_memory
from app.agents.qa_pipeline.schemas import CrawlResult, ScreenNode, ScreenTransition


def test_get_memory_stats_includes_valid_crawl_apps_from_disk(monkeypatch, tmp_path: Path) -> None:
    memory_dir = tmp_path / "exploration_memory"
    crawl_dir = memory_dir / "crawl"
    workflow_dir = memory_dir / "workflows"
    suite_dir = memory_dir / "test_suites"
    crawl_dir.mkdir(parents=True)
    workflow_dir.mkdir()
    suite_dir.mkdir()

    monkeypatch.setattr(exploration_memory, "_MEMORY_DIR", memory_dir)
    monkeypatch.setattr(exploration_memory, "_CRAWL_DIR", crawl_dir)
    monkeypatch.setattr(exploration_memory, "_WORKFLOW_DIR", workflow_dir)
    monkeypatch.setattr(exploration_memory, "_TESTSUITE_DIR", suite_dir)
    monkeypatch.setattr(exploration_memory, "_INDEX_PATH", memory_dir / "memory_index.json")

    index_payload = {
        "apps": {
            "indexed_only": {
                "app_url": "https://example.com/indexed",
                "app_name": "Indexed Only",
                "crawl_fingerprint": "idx123",
                "screens": 1,
                "components": 3,
                "screen_graph_size": 1,
                "last_crawl": "2026-03-26T00:00:00+00:00",
                "crawl_count": 2,
            }
        },
        "stats": {"total_hits": 2, "total_misses": 1, "tokens_saved": 1234},
    }
    exploration_memory._INDEX_PATH.write_text(json.dumps(index_payload))

    crawl_payload = {
        "app_key": "demo_edgar_kyb_v2",
        "app_name": "SEC EDGAR KYB Intelligence",
        "app_url": "https://www.sec.gov/cgi-bin/browse-edgar",
        "crawl_fingerprint": "demo123",
        "total_screens": 2,
        "total_components": 7,
        "screen_graph": {"screen_001": "abc", "screen_002": "def"},
        "stored_at": "2026-03-26T01:00:00+00:00",
        "crawl_data": {
            "screens": [
                {"screen_id": "screen_001", "components": [{"id": 1}]},
                {"screen_id": "screen_002", "components": [{"id": 2}, {"id": 3}]},
            ]
        },
    }
    (crawl_dir / "demo_edgar_kyb_v2.json").write_text(json.dumps(crawl_payload))

    invalid_crawl_payload = {
        "app_key": "empty_app",
        "app_name": "Broken Crawl",
        "total_screens": 0,
        "crawl_data": {"screens": []},
    }
    (crawl_dir / "empty_app.json").write_text(json.dumps(invalid_crawl_payload))

    stats = exploration_memory.get_memory_stats()

    assert stats["apps_cached"] == 2
    assert "indexed_only" in stats["apps"]
    assert "demo_edgar_kyb_v2" in stats["apps"]
    assert "empty_app" not in stats["apps"]
    assert stats["apps"]["demo_edgar_kyb_v2"]["screens"] == 2
    assert stats["apps"]["demo_edgar_kyb_v2"]["screen_graph_size"] == 2
    assert stats["total_cache_hits"] == 2
    assert stats["estimated_tokens_saved"] == 1234


def test_store_crawl_normalizes_hierarchy_for_live_runs(monkeypatch, tmp_path: Path) -> None:
    memory_dir = tmp_path / "exploration_memory"
    crawl_dir = memory_dir / "crawl"
    workflow_dir = memory_dir / "workflows"
    suite_dir = memory_dir / "test_suites"
    crawl_dir.mkdir(parents=True)
    workflow_dir.mkdir()
    suite_dir.mkdir()

    monkeypatch.setattr(exploration_memory, "_MEMORY_DIR", memory_dir)
    monkeypatch.setattr(exploration_memory, "_CRAWL_DIR", crawl_dir)
    monkeypatch.setattr(exploration_memory, "_WORKFLOW_DIR", workflow_dir)
    monkeypatch.setattr(exploration_memory, "_TESTSUITE_DIR", suite_dir)
    monkeypatch.setattr(exploration_memory, "_INDEX_PATH", memory_dir / "memory_index.json")

    crawl_result = CrawlResult(
        app_name="Live Demo",
        package_name="",
        screens=[
            ScreenNode(
                screen_id="screen_001",
                screen_name="Home",
                screenshot_path="/tmp/home.png",
                screenshot_description="Home screen",
                navigation_depth=8,
                parent_screen_id="missing_parent",
                components=[],
            ),
            ScreenNode(
                screen_id="screen_002",
                screen_name="Results",
                screenshot_path="/tmp/results.png",
                screenshot_description="Results screen",
                navigation_depth=99,
                parent_screen_id="screen_001",
                trigger_action="Tap Search",
                components=[],
            ),
            ScreenNode(
                screen_id="screen_003",
                screen_name="Detail",
                screenshot_path="/tmp/detail.png",
                screenshot_description="Detail screen",
                navigation_depth=4,
                parent_screen_id="screen_004",
                trigger_action="Tap phantom",
                components=[],
            ),
            ScreenNode(
                screen_id="screen_004",
                screen_name="Receipt",
                screenshot_path="/tmp/receipt.png",
                screenshot_description="Receipt screen",
                navigation_depth=5,
                parent_screen_id="screen_003",
                trigger_action="Tap receipt",
                components=[],
            ),
        ],
        transitions=[
            ScreenTransition(from_screen="screen_001", to_screen="screen_002", action="Tap Search"),
            ScreenTransition(from_screen="screen_004", to_screen="ghost", action="Invalid jump"),
        ],
        total_components=0,
        total_screens=4,
    )

    fingerprint = exploration_memory.store_crawl("live_demo", crawl_result, app_url="https://example.com/live")
    assert fingerprint

    stored = json.loads((crawl_dir / "live_demo.json").read_text())
    stored_screens = stored["crawl_data"]["screens"]
    assert [(screen["screen_id"], screen["parent_screen_id"], screen["navigation_depth"]) for screen in stored_screens] == [
        ("screen_001", None, 0),
        ("screen_002", "screen_001", 1),
        ("screen_003", None, 0),
        ("screen_004", "screen_003", 1),
    ]
    assert stored["crawl_data"]["transitions"] == [
        {"from_screen": "screen_001", "to_screen": "screen_002", "action": "Tap Search", "component_id": None}
    ]

    loaded = exploration_memory.load_crawl("live_demo")
    assert loaded is not None
    loaded_result, loaded_fp = loaded
    assert loaded_fp == fingerprint
    assert [(screen.screen_id, screen.parent_screen_id, screen.navigation_depth) for screen in loaded_result.screens] == [
        ("screen_001", None, 0),
        ("screen_002", "screen_001", 1),
        ("screen_003", None, 0),
        ("screen_004", "screen_003", 1),
    ]