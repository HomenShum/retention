import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.investor_brief.service import InvestorBriefService


FIXTURE_PATH = Path(__file__).resolve().parents[2] / "tmp" / "TA_Strategy_Brief_InHouseAgent.html"


def _build_service(tmp_path: Path) -> InvestorBriefService:
    brief_path = tmp_path / "brief.html"
    brief_path.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return InvestorBriefService(brief_path)


def test_get_state_returns_sections_and_breakdown(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    state = service.get_state()

    assert state["scenario"] == "base"
    assert state["variables"]["team_size"] == 4
    assert state["breakdown"]["totalBurn"] > 0
    assert any(section["sectionId"] == "sprint-cost-model" for section in state["sections"])
    assert any(section["sectionId"] == "onepager-economics" for section in state["sections"])


def test_set_variables_persists_custom_state(tmp_path: Path) -> None:
    service = _build_service(tmp_path)

    state = service.set_variables({"team_size": 6, "benchmark_replays": 33})

    assert state["scenario"] == "custom"
    assert state["variables"]["team_size"] == 6
    assert state["variables"]["benchmark_replays"] == 33
    html_doc = service.brief_path.read_text(encoding="utf-8")
    assert 'data-current-scenario="custom"' in html_doc
    assert 'value="6" data-cost-input="team_size"' in html_doc


def test_update_section_replaces_body_not_heading(tmp_path: Path) -> None:
    service = _build_service(tmp_path)

    section = service.update_section(
        "executive-summary",
        "Updated investor summary.\n\nSecond paragraph.",
        content_format="text",
    )

    assert section["title"] == "Executive Summary"
    assert "Updated investor summary." in section["bodyText"]
    html_doc = service.brief_path.read_text(encoding="utf-8")
    assert "<h2>Executive Summary</h2>" in html_doc
    assert "Updated investor summary." in html_doc