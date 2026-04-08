"""End-to-end integration tests for the MCP server surface.

Tests the full tool lifecycle without requiring a connected device:
  - Tool discovery (list tools)
  - Validation gate lifecycle (request → poll → release)
  - Codebase tools (recent_commits, search, git_status)
  - Investor brief tools (get_state, list_sections)
  - Smoke test graceful fallback (no device)
"""

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import app.api.mcp_server as mcpmod
    import app.api.validation_hooks as vhmod
    from app.main import app

    previous_dev_mode = os.getenv("TA_DEV_MODE")
    original_vh_hooks = vhmod._hooks
    original_mcp_hooks = mcpmod._hooks

    os.environ["TA_DEV_MODE"] = "1"
    isolated_hooks = vhmod._JsonBackedDict(path=tmp_path_factory.mktemp("mcp_e2e") / "hooks.json")
    vhmod._hooks = isolated_hooks
    mcpmod._hooks = isolated_hooks

    test_client = TestClient(app)
    token = os.getenv("RETENTION_MCP_TOKEN", "").strip()
    if token:
        test_client.headers.update({"Authorization": f"Bearer {token}"})

    try:
        yield test_client
    finally:
        vhmod._hooks = original_vh_hooks
        mcpmod._hooks = original_mcp_hooks
        if previous_dev_mode is None:
            os.environ.pop("TA_DEV_MODE", None)
        else:
            os.environ["TA_DEV_MODE"] = previous_dev_mode


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_mcp_health(client):
    resp = client.get("/mcp/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["tools"] > 0


def test_list_tools(client):
    resp = client.get("/mcp/tools")
    assert resp.status_code == 200
    tools = resp.json()
    assert isinstance(tools, list)
    names = {t["name"] for t in tools}
    # Core tools must be present
    assert "ta.request_validation_gate" in names
    assert "ta.get_hook_status" in names
    assert "ta.smoke_test" in names
    assert "ta.codebase.recent_commits" in names


# ---------------------------------------------------------------------------
# Validation gate lifecycle
# ---------------------------------------------------------------------------

def test_validation_gate_lifecycle(client):
    """Request → poll (pending) → release → poll (released)."""
    # 1. Request a gate
    resp = client.post("/mcp/tools/call", json={
        "tool": "ta.request_validation_gate",
        "arguments": {
            "agent_id": "test-agent",
            "task_description": "Integration test gate",
        },
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    hook_id = body["result"]["hook_id"]
    assert hook_id

    # 2. Poll — should be pending
    resp = client.post("/mcp/tools/call", json={
        "tool": "ta.get_hook_status",
        "arguments": {"hook_id": hook_id},
    })
    assert resp.status_code == 200
    assert resp.json()["result"]["status"] == "pending"

    # 3. Release the gate (via REST endpoint — release is internal TA action, not MCP-exposed)
    resp = client.post(f"/api/hooks/{hook_id}/release", json={
        "release_notes": "test pass",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "released"

    # 4. Poll again — should be released
    resp = client.post("/mcp/tools/call", json={
        "tool": "ta.get_hook_status",
        "arguments": {"hook_id": hook_id},
    })
    assert resp.status_code == 200
    assert resp.json()["result"]["status"] == "released"


# ---------------------------------------------------------------------------
# Codebase tools (no device needed — runs against local git)
# ---------------------------------------------------------------------------

def test_codebase_recent_commits(client):
    resp = client.post("/mcp/tools/call", json={
        "tool": "ta.codebase.recent_commits",
        "arguments": {"limit": 5},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    commits = body["result"]
    assert isinstance(commits, list)
    assert len(commits) > 0
    assert "sha" in commits[0]
    assert "message" in commits[0]


def test_codebase_git_status(client):
    resp = client.post("/mcp/tools/call", json={
        "tool": "ta.codebase.git_status",
        "arguments": {},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert "modified" in result or "total" in result


def test_codebase_search(client):
    resp = client.post("/mcp/tools/call", json={
        "tool": "ta.codebase.search",
        "arguments": {"query": "ActionSpan"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # Should find at least one match in the codebase
    assert body["result"]


# ---------------------------------------------------------------------------
# Smoke test — graceful without device
# ---------------------------------------------------------------------------

def test_smoke_test_no_device(client):
    """smoke_test should return a clean JSON error, not crash, when no device is connected."""
    resp = client.post("/mcp/tools/call", json={
        "tool": "ta.smoke_test",
        "arguments": {},
    })
    assert resp.status_code == 200
    body = resp.json()
    # Even without a device, the response should be structured
    assert body["status"] == "ok"  # tool dispatch succeeded (the tool itself reports pass/fail in result)
    result = body["result"]
    assert "passed" in result
    assert "verdict" in result
    # We don't assert passed=False since adb might be available in CI


# ---------------------------------------------------------------------------
# Unknown tool
# ---------------------------------------------------------------------------

def test_unknown_tool(client):
    resp = client.post("/mcp/tools/call", json={
        "tool": "ta.nonexistent_tool",
        "arguments": {},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
