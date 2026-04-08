"""Route-level tests for the Validation Stop Hook API (validation_hooks.py).

Uses monkeypatch to replace the module-level _hooks with a plain dict so tests
are fully isolated from disk I/O and from each other.
"""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.api.validation_hooks as _hooks_mod
from app.api import validation_hooks as _vh_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQ = {
    "agent_id": "cursor",
    "task_description": "Implement login flow",
    "pr_url": "https://github.com/org/repo/pull/42",
    "repo": "org/repo",
    "branch": "feature/login",
}


def _build_client() -> TestClient:
    application = FastAPI()
    application.include_router(_vh_router.router)
    return TestClient(application)


@pytest.fixture(autouse=True)
def isolate_hooks(monkeypatch):
    """Replace _hooks with a fresh plain dict for each test — no file I/O."""
    monkeypatch.setattr(_hooks_mod, "_hooks", {})
    yield


# ---------------------------------------------------------------------------
# POST /hooks/request
# ---------------------------------------------------------------------------

class TestRequestHook:
    def test_creates_hook_with_pending_status(self):
        with _build_client() as client:
            resp = client.post("/hooks/request", json=_REQ)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending"
        assert body["agent_id"] == "cursor"
        assert "hook_id" in body
        assert body["pr_url"] == _REQ["pr_url"]

    def test_hook_stored_in_module_dict(self):
        with _build_client() as client:
            resp = client.post("/hooks/request", json=_REQ)
        hook_id = resp.json()["hook_id"]
        assert hook_id in _hooks_mod._hooks

    def test_missing_required_fields_returns_422(self):
        with _build_client() as client:
            resp = client.post("/hooks/request", json={"agent_id": "cursor"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /hooks/{hook_id}/release
# ---------------------------------------------------------------------------

class TestReleaseHook:
    def test_release_sets_status_released(self):
        with _build_client() as client:
            hook_id = client.post("/hooks/request", json=_REQ).json()["hook_id"]
            resp = client.post(
                f"/hooks/{hook_id}/release",
                json={"release_notes": "All checks passed"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "released"
        assert body["release_notes"] == "All checks passed"
        assert body["released_at"] is not None

    def test_release_nonexistent_hook_returns_404(self):
        with _build_client() as client:
            resp = client.post("/hooks/bad-id/release", json={})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /hooks/{hook_id}/fail
# ---------------------------------------------------------------------------

class TestFailHook:
    def test_fail_sets_status_blocked(self):
        with _build_client() as client:
            hook_id = client.post("/hooks/request", json=_REQ).json()["hook_id"]
            resp = client.post(
                f"/hooks/{hook_id}/fail",
                json={"failure_reason": "Visual regression detected"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "blocked"
        assert body["failure_reason"] == "Visual regression detected"

    def test_fail_nonexistent_hook_returns_404(self):
        with _build_client() as client:
            resp = client.post("/hooks/bad-id/fail", json={"failure_reason": "x"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /hooks/{hook_id}
# ---------------------------------------------------------------------------

class TestGetHook:
    def test_poll_returns_current_status(self):
        with _build_client() as client:
            hook_id = client.post("/hooks/request", json=_REQ).json()["hook_id"]
            resp = client.get(f"/hooks/{hook_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    def test_poll_after_release_shows_released(self):
        with _build_client() as client:
            hook_id = client.post("/hooks/request", json=_REQ).json()["hook_id"]
            client.post(f"/hooks/{hook_id}/release", json={})
            resp = client.get(f"/hooks/{hook_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "released"

    def test_poll_nonexistent_hook_returns_404(self):
        with _build_client() as client:
            resp = client.get("/hooks/not-there")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /hooks
# ---------------------------------------------------------------------------

class TestListHooks:
    def test_empty_store_returns_empty_list(self):
        with _build_client() as client:
            resp = client.get("/hooks")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_all_hooks(self):
        with _build_client() as client:
            client.post("/hooks/request", json={**_REQ, "agent_id": "cursor"})
            client.post("/hooks/request", json={**_REQ, "agent_id": "devin"})
            resp = client.get("/hooks")
        assert len(resp.json()) == 2

    def test_filter_by_agent_id(self):
        with _build_client() as client:
            client.post("/hooks/request", json={**_REQ, "agent_id": "cursor"})
            client.post("/hooks/request", json={**_REQ, "agent_id": "devin"})
            resp = client.get("/hooks?agent_id=cursor")
        assert all(h["agent_id"] == "cursor" for h in resp.json())
        assert len(resp.json()) == 1

    def test_filter_by_status_released(self):
        with _build_client() as client:
            id1 = client.post("/hooks/request", json=_REQ).json()["hook_id"]
            client.post("/hooks/request", json=_REQ)  # stays pending
            client.post(f"/hooks/{id1}/release", json={})
            resp = client.get("/hooks?status=released")
        assert len(resp.json()) == 1
        assert resp.json()[0]["hook_id"] == id1

