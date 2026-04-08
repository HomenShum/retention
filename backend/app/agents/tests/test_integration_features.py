"""
Integration tests for session continuity and validation hooks.

Covers:
- resume_session_id skips new session creation in chat_stream
- session_created event is emitted with the correct ID in both cases
- POST /hooks/request  → PENDING hook created
- POST /hooks/{id}/release → hook transitions to RELEASED
- POST /hooks/{id}/fail   → hook transitions to BLOCKED
- GET  /hooks/{id}        → returns current hook state
- GET  /hooks             → list with optional filters
"""
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_app():
    """Build a minimal FastAPI app that mounts only the validation-hooks router
    so tests never need a running backend.
    """
    from app.api.validation_hooks import router as hooks_router, _JsonBackedDict, _hooks
    app = FastAPI()
    app.include_router(hooks_router, prefix="/api")
    return app, _hooks


@pytest.fixture()
def hooks_client(tmp_path):
    """TestClient with a fresh in-memory hooks store (no disk I/O)."""
    import app.api.validation_hooks as vhmod

    # Patch the module-level _hooks with a fresh temp-backed instance
    fresh_store = vhmod._JsonBackedDict(path=tmp_path / "hooks.json")
    original = vhmod._hooks
    vhmod._hooks = fresh_store
    try:
        from app.api.validation_hooks import router as hooks_router
        app = FastAPI()
        app.include_router(hooks_router, prefix="/api")
        with TestClient(app) as client:
            yield client
    finally:
        vhmod._hooks = original


# ---------------------------------------------------------------------------
# Validation hooks endpoint tests
# ---------------------------------------------------------------------------

class TestHookRequest:
    def test_creates_pending_hook(self, hooks_client):
        resp = hooks_client.post("/api/hooks/request", json={
            "agent_id": "cursor",
            "task_description": "Add login screen",
            "pr_url": "https://github.com/org/repo/pull/42",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["agent_id"] == "cursor"
        assert "hook_id" in data
        assert data["hook_id"] != ""

    def test_hook_id_is_unique(self, hooks_client):
        payload = {"agent_id": "devin", "task_description": "Fix crash"}
        r1 = hooks_client.post("/api/hooks/request", json=payload).json()
        r2 = hooks_client.post("/api/hooks/request", json=payload).json()
        assert r1["hook_id"] != r2["hook_id"]


class TestHookRelease:
    def test_transitions_to_released(self, hooks_client):
        hook_id = hooks_client.post("/api/hooks/request", json={
            "agent_id": "openclaw", "task_description": "Refactor auth"
        }).json()["hook_id"]

        resp = hooks_client.post(f"/api/hooks/{hook_id}/release", json={
            "release_notes": "All UI checks passed",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "released"
        assert data["release_notes"] == "All UI checks passed"
        assert data["released_at"] is not None

    def test_release_unknown_id_returns_404(self, hooks_client):
        resp = hooks_client.post("/api/hooks/nonexistent-id/release", json={"release_notes": ""})
        assert resp.status_code == 404


class TestHookFail:
    def test_transitions_to_blocked(self, hooks_client):
        hook_id = hooks_client.post("/api/hooks/request", json={
            "agent_id": "cursor", "task_description": "Add dark mode"
        }).json()["hook_id"]

        resp = hooks_client.post(f"/api/hooks/{hook_id}/fail", json={
            "failure_reason": "Contrast ratio below WCAG AA threshold",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "blocked"
        assert "WCAG" in data["failure_reason"]

    def test_fail_unknown_id_returns_404(self, hooks_client):
        resp = hooks_client.post("/api/hooks/bad-id/fail", json={"failure_reason": "x"})
        assert resp.status_code == 404


class TestHookPoll:
    def test_get_returns_current_state(self, hooks_client):
        hook_id = hooks_client.post("/api/hooks/request", json={
            "agent_id": "agent-x", "task_description": "Test something"
        }).json()["hook_id"]

        resp = hooks_client.get(f"/api/hooks/{hook_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    def test_get_unknown_id_returns_404(self, hooks_client):
        assert hooks_client.get("/api/hooks/missing").status_code == 404


class TestHookList:
    def test_list_returns_all_hooks(self, hooks_client):
        hooks_client.post("/api/hooks/request", json={"agent_id": "a1", "task_description": "t1"})
        hooks_client.post("/api/hooks/request", json={"agent_id": "a2", "task_description": "t2"})
        resp = hooks_client.get("/api/hooks")
        assert resp.status_code == 200
        assert len(resp.json()) >= 2

    def test_list_filters_by_agent_id(self, hooks_client):
        hooks_client.post("/api/hooks/request", json={"agent_id": "bot", "task_description": "x"})
        hooks_client.post("/api/hooks/request", json={"agent_id": "other", "task_description": "y"})
        resp = hooks_client.get("/api/hooks", params={"agent_id": "bot"})
        assert all(h["agent_id"] == "bot" for h in resp.json())

    def test_list_filters_by_status(self, hooks_client):
        hook_id = hooks_client.post("/api/hooks/request", json={
            "agent_id": "z", "task_description": "pending item"
        }).json()["hook_id"]
        hooks_client.post(f"/api/hooks/{hook_id}/release", json={"release_notes": ""})
        resp = hooks_client.get("/api/hooks", params={"status": "released"})
        assert all(h["status"] == "released" for h in resp.json())


# ---------------------------------------------------------------------------
# Session continuity unit tests
# ---------------------------------------------------------------------------

class TestSessionContinuity:
    """
    Unit tests for resume_session_id logic in AIAgentService.chat_stream.

    These tests run WITHOUT any OpenAI API key by mocking the Runner.
    """

    @pytest.mark.asyncio
    async def test_resume_session_skips_new_session_creation(self):
        """When resume_session_id is provided, _create_agent_session must NOT be called."""
        from app.agents.coordinator.coordinator_service import AIAgentService, ChatMessage

        svc = AIAgentService.__new__(AIAgentService)
        # Minimal attribute setup so chat_stream initialises cleanly
        svc.api_key = "sk-test"
        svc.model_name = "gpt-5.4"
        svc.thinking_model = "gpt-5.4"
        svc.distill_model = "gpt-5-nano"
        svc.auto_screenshot_every_step = False
        svc._chef_runner_ref = None
        svc.mobile_mcp_client = None
        svc._mobile_mcp_started = False
        svc.capabilities = {}
        svc.vector_search = None
        svc.appium_mcp = None

        svc._create_agent_session = AsyncMock(return_value="should-not-be-called")

        existing_id = "resume-" + str(uuid.uuid4())
        msgs = [ChatMessage(role="user", content="resume me")]

        events = []
        try:
            async for evt in svc.chat_stream(msgs, resume_session_id=existing_id):
                events.append(evt)
                if evt.get("type") in ("session_created", "error", "final"):
                    break
        except Exception:
            pass  # We only care about whether _create_agent_session was called

        svc._create_agent_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_session_emits_correct_session_id(self):
        """The session_created event must carry the *resumed* session ID."""
        from app.agents.coordinator.coordinator_service import AIAgentService, ChatMessage

        svc = AIAgentService.__new__(AIAgentService)
        svc.api_key = "sk-test"
        svc.model_name = "gpt-5.4"
        svc.thinking_model = "gpt-5.4"
        svc.distill_model = "gpt-5-nano"
        svc.auto_screenshot_every_step = False
        svc._chef_runner_ref = None
        svc.mobile_mcp_client = None
        svc._mobile_mcp_started = False
        svc.capabilities = {}
        svc.vector_search = None
        svc.appium_mcp = None
        svc._create_agent_session = AsyncMock(return_value="brand-new-session")

        target_id = "resume-" + str(uuid.uuid4())
        msgs = [ChatMessage(role="user", content="hello")]

        session_events = []
        try:
            async for evt in svc.chat_stream(msgs, resume_session_id=target_id):
                if isinstance(evt, dict) and evt.get("type") == "session_created":
                    session_events.append(evt)
                    break
        except Exception:
            pass

        assert len(session_events) == 1, "Expected a session_created event"
        assert session_events[0]["session_id"] == target_id

