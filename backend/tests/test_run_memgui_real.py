import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.run_memgui_real import _parse_action, update_memgui_registry  # noqa: E402


def _memgui_bench(registry: dict) -> dict:
    for bench in registry["benchmarks"]:
        if bench["id"] == "memgui_bench":
            return bench
    raise AssertionError("memgui_bench not found")


def test_parse_action_accepts_direct_json():
    assert _parse_action('{"action": "tap", "x": 12, "y": 34}') == {
        "action": "tap",
        "x": 12,
        "y": 34,
    }


def test_parse_action_accepts_fenced_json():
    parsed = _parse_action('```json\n{"action": "done", "success": true, "reason": "complete"}\n```')
    assert parsed == {
        "action": "done",
        "success": True,
        "reason": "complete",
    }


def test_parse_action_falls_back_to_unknown_for_non_json_text():
    parsed = _parse_action("tap the save button")
    assert parsed["action"] == "unknown"
    assert "tap the save button" in parsed["raw"]


def test_update_memgui_registry_marks_dry_run_as_simulated(tmp_path):
    registry_path = tmp_path / "benchmark_registry.json"
    registry_path.write_text(json.dumps({
        "benchmarks": [
            {
                "id": "memgui_bench",
                "status": "ready",
                "submission_process": "PR",
            }
        ]
    }))

    update_memgui_registry(registry_path, pass_at_1=0.25, is_dry=True, run_date="2026-03-20")

    updated = json.loads(registry_path.read_text())
    bench = _memgui_bench(updated)
    assert bench["our_score"] == 0.25
    assert bench["status"] == "simulated"
    assert bench["last_run"] == "2026-03-20"
    assert "submission_status" not in bench


def test_update_memgui_registry_marks_real_run_pending_submission(tmp_path):
    registry_path = tmp_path / "benchmark_registry.json"
    registry_path.write_text(json.dumps({
        "benchmarks": [
            {
                "id": "memgui_bench",
                "status": "ready",
                "submission_process": "PR",
            }
        ]
    }))

    update_memgui_registry(registry_path, pass_at_1=0.5, is_dry=False, run_date="2026-03-20")

    updated = json.loads(registry_path.read_text())
    bench = _memgui_bench(updated)
    assert bench["our_score"] == 0.5
    assert bench["status"] == "verified_self_reported"
    assert bench["submission_status"] == "pending_formal"
    assert bench["last_run"] == "2026-03-20"


def test_update_memgui_registry_clears_submission_status_on_dry_run(tmp_path):
    registry_path = tmp_path / "benchmark_registry.json"
    registry_path.write_text(json.dumps({
        "benchmarks": [
            {
                "id": "memgui_bench",
                "status": "verified_self_reported",
                "submission_process": "PR",
                "submission_status": "pending_formal",
            }
        ]
    }))

    update_memgui_registry(registry_path, pass_at_1=0.1, is_dry=True, run_date="2026-03-21")

    updated = json.loads(registry_path.read_text())
    bench = _memgui_bench(updated)
    assert bench["status"] == "simulated"
    assert bench["last_run"] == "2026-03-21"
    assert "submission_status" not in bench
