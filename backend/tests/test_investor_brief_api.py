import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.api import investor_brief as investor_brief_router
from app.investor_brief.service import InvestorBriefService


FIXTURE_PATH = Path(__file__).resolve().parents[2] / "tmp" / "TA_Strategy_Brief_InHouseAgent.html"


def _service(tmp_path: Path) -> InvestorBriefService:
    brief_path = tmp_path / "brief.html"
    brief_path.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return InvestorBriefService(brief_path)


def test_investor_brief_router_requires_service_then_succeeds(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(investor_brief_router.router)

    investor_brief_router.set_investor_brief_service(None)
    with TestClient(app) as client:
        response = client.get("/api/investor-brief/state")
        assert response.status_code == 503

    investor_brief_router.set_investor_brief_service(_service(tmp_path))
    with TestClient(app) as client:
        response = client.get("/api/investor-brief/state")
        assert response.status_code == 200
        assert response.json()["scenario"] == "base"


def test_investor_brief_action_endpoint_updates_variables(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(investor_brief_router.router)
    investor_brief_router.set_investor_brief_service(_service(tmp_path))

    with TestClient(app) as client:
        response = client.post(
            "/api/investor-brief/actions",
            json={
                "action": "set_variables",
                "arguments": {"variables": {"team_size": 7}},
            },
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["scenario"] == "custom"
    assert result["variables"]["team_size"] == 7