import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.api import mcp_server as mcp_router


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(mcp_router.router)
    return TestClient(app)


def test_setup_for_agent_normalizes_convex_cloud_to_site(monkeypatch) -> None:
    monkeypatch.setenv("CONVEX_SITE_URL", "https://exuberant-ferret-263.convex.cloud")

    with _build_client() as client:
        response = client.get("/mcp/setup/for-agent")

    assert response.status_code == 200
    body = response.text
    assert "https://exuberant-ferret-263.convex.site/api/mcp/generate-token" in body
    assert "https://exuberant-ferret-263.convex.cloud/api/mcp/generate-token" not in body
    assert "TOKEN_FROM_STEP_1" in body


def test_setup_for_agent_includes_app_url_when_provided(monkeypatch) -> None:
    monkeypatch.delenv("CONVEX_SITE_URL", raising=False)

    with _build_client() as client:
        response = client.get("/mcp/setup/for-agent", params={"app_url": "http://localhost:3000"})

    assert response.status_code == 200
    body = response.text
    assert 'ACTION: Call ta.run_web_flow with url="http://localhost:3000" to start QA testing.' in body
    assert 'RUN: curl -s "http://testserver/mcp/setup/init.sh" | bash' in body


def test_setup_for_agent_cursor_uses_cursor_mcp_path(monkeypatch) -> None:
    monkeypatch.delenv("CONVEX_SITE_URL", raising=False)

    with _build_client() as client:
        response = client.get("/mcp/setup/for-agent", params={"platform": "cursor"})

    assert response.status_code == 200
    body = response.text
    assert ".cursor/mcp.json" in body
    assert "Cursor" in body
    # Should NOT reference Claude Code's .mcp.json as the primary config
    assert "PLATFORM: cursor" in body


def test_setup_for_agent_openclaw_uses_openclaw_mcp_path(monkeypatch) -> None:
    monkeypatch.delenv("CONVEX_SITE_URL", raising=False)

    with _build_client() as client:
        response = client.get("/mcp/setup/for-agent", params={"platform": "openclaw"})

    assert response.status_code == 200
    body = response.text
    assert ".openclaw/mcp.json" in body
    assert "OpenClaw" in body
    assert "PLATFORM: openclaw" in body


def test_setup_for_agent_claude_code_uses_dot_mcp_json(monkeypatch) -> None:
    monkeypatch.delenv("CONVEX_SITE_URL", raising=False)

    with _build_client() as client:
        response = client.get("/mcp/setup/for-agent", params={"platform": "claude-code"})

    assert response.status_code == 200
    body = response.text
    assert ".mcp.json" in body
    assert "Claude Code" in body
    assert "PLATFORM: claude-code" in body


# ── init.sh tests ──────────────────────────────────────────────────────────────

def test_init_sh_default_platform_writes_dot_mcp_json(monkeypatch) -> None:
    monkeypatch.delenv("CONVEX_SITE_URL", raising=False)

    with _build_client() as client:
        response = client.get("/mcp/setup/init.sh")

    assert response.status_code == 200
    body = response.text
    assert 'MCP_FILE=".mcp.json"' in body
    assert "Claude Code" in body


def test_init_sh_cursor_platform_writes_cursor_mcp_json(monkeypatch) -> None:
    monkeypatch.delenv("CONVEX_SITE_URL", raising=False)

    with _build_client() as client:
        response = client.get("/mcp/setup/init.sh", params={"platform": "cursor"})

    assert response.status_code == 200
    body = response.text
    assert 'MCP_FILE=".cursor/mcp.json"' in body
    assert "Cursor" in body


def test_init_sh_openclaw_platform_writes_openclaw_mcp_json(monkeypatch) -> None:
    monkeypatch.delenv("CONVEX_SITE_URL", raising=False)

    with _build_client() as client:
        response = client.get("/mcp/setup/init.sh", params={"platform": "openclaw"})

    assert response.status_code == 200
    body = response.text
    assert 'MCP_FILE=".openclaw/mcp.json"' in body
    assert "OpenClaw" in body


def test_init_sh_normalizes_convex_cloud_to_site(monkeypatch) -> None:
    monkeypatch.setenv("CONVEX_SITE_URL", "https://exuberant-ferret-263.convex.cloud")

    with _build_client() as client:
        response = client.get("/mcp/setup/init.sh")

    assert response.status_code == 200
    body = response.text
    assert "exuberant-ferret-263.convex.site" in body
    assert "exuberant-ferret-263.convex.cloud" not in body


# ── init.ps1 tests ─────────────────────────────────────────────────────────────

def test_init_ps1_default_platform_returns_powershell(monkeypatch) -> None:
    monkeypatch.delenv("CONVEX_SITE_URL", raising=False)

    with _build_client() as client:
        response = client.get("/mcp/setup/init.ps1")

    assert response.status_code == 200
    body = response.text
    # PowerShell idioms
    assert "Invoke-RestMethod" in body
    assert "Invoke-WebRequest" in body
    assert 'mcp.json"' in body
    assert "Claude Code" in body


def test_init_ps1_cursor_platform(monkeypatch) -> None:
    monkeypatch.delenv("CONVEX_SITE_URL", raising=False)

    with _build_client() as client:
        response = client.get("/mcp/setup/init.ps1", params={"platform": "cursor"})

    assert response.status_code == 200
    body = response.text
    assert ".cursor\\mcp.json" in body
    assert "Cursor" in body


def test_init_ps1_openclaw_platform(monkeypatch) -> None:
    monkeypatch.delenv("CONVEX_SITE_URL", raising=False)

    with _build_client() as client:
        response = client.get("/mcp/setup/init.ps1", params={"platform": "openclaw"})

    assert response.status_code == 200
    body = response.text
    assert ".openclaw\\mcp.json" in body
    assert "OpenClaw" in body


def test_init_ps1_normalizes_convex_cloud_to_site(monkeypatch) -> None:
    monkeypatch.setenv("CONVEX_SITE_URL", "https://exuberant-ferret-263.convex.cloud")

    with _build_client() as client:
        response = client.get("/mcp/setup/init.ps1")

    assert response.status_code == 200
    body = response.text
    assert "exuberant-ferret-263.convex.site" in body
    assert "exuberant-ferret-263.convex.cloud" not in body
