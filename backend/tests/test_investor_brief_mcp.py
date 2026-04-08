import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.api import mcp_server as mcp_router
from app.investor_brief.service import InvestorBriefService


FIXTURE_PATH = Path(__file__).resolve().parents[2] / "tmp" / "TA_Strategy_Brief_InHouseAgent.html"


def _service(tmp_path: Path) -> InvestorBriefService:
    brief_path = tmp_path / "brief.html"
    brief_path.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return InvestorBriefService(brief_path)


def test_mcp_tools_include_investor_brief_entries() -> None:
    app = FastAPI()
    app.include_router(mcp_router.router)

    with TestClient(app) as client:
        response = client.get("/mcp/tools")

    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()}
    assert "ta.investor_brief.get_state" in names
    assert "ta.investor_brief.update_section" in names


def test_mcp_dispatch_calls_investor_brief_service(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(mcp_router.router)
    mcp_router.set_investor_brief_service(_service(tmp_path))

    with TestClient(app) as client:
        response = client.post(
            "/mcp/tools/call",
            json={
                "tool": "ta.investor_brief.set_scenario",
                "arguments": {"scenario": "pessimistic"},
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["result"]["scenario"] == "pessimistic"