import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.investor_brief.agent import create_investor_brief_agent
from app.investor_brief.service import InvestorBriefService


FIXTURE_PATH = Path(__file__).resolve().parents[2] / "tmp" / "TA_Strategy_Brief_InHouseAgent.html"


def test_create_investor_brief_agent_exposes_all_actions(tmp_path: Path) -> None:
    brief_path = tmp_path / "brief.html"
    brief_path.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    agent = create_investor_brief_agent(InvestorBriefService(brief_path))

    assert agent.name == "InvestorBriefController"
    assert len(agent.tools) == 7