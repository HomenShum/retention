"""Route-level tests for the ActionSpan API (action_spans.py).

Isolates module-level stores so tests don't share state.
ADB / ffmpeg calls are avoided by using no device_id and no real clip file.
"""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the router first so the service module is loaded into sys.modules.
from app.api import action_spans as _spans_mod  # noqa: E402

# Retrieve the real module (not the singleton re-exported by __init__.py).
_svc_module = sys.modules["app.agents.device_testing.action_span_service"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_client() -> TestClient:
    application = FastAPI()
    application.include_router(_spans_mod.router)
    return TestClient(application)


@pytest.fixture(autouse=True)
def _clear_stores():
    """Reset module-level stores and point clip_dir to /tmp before each test."""
    _svc_module._span_store.clear()
    _svc_module._manifest_store.clear()
    _svc_module._recording_procs.clear()
    # Redirect clip writes to /tmp so tests don't pollute the backend directory.
    _svc_module.action_span_service.clip_dir = Path("/tmp/ta_test_spans")
    yield
    _svc_module._span_store.clear()
    _svc_module._manifest_store.clear()
    _svc_module._recording_procs.clear()


# ---------------------------------------------------------------------------
# POST /action-spans/start
# ---------------------------------------------------------------------------

class TestStartSpan:
    def test_start_returns_span_id_and_capturing_status(self):
        with _build_client() as client:
            resp = client.post(
                "/action-spans/start",
                json={"session_id": "sess-001", "action_type": "tap", "action_description": "Tap login"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "capturing"
        assert body["session_id"] == "sess-001"
        assert "span_id" in body
        assert "started_at" in body

    def test_start_without_device_id_still_succeeds(self):
        """No ADB should not prevent span creation."""
        with _build_client() as client:
            resp = client.post(
                "/action-spans/start",
                json={"session_id": "sess-002"},
            )
        assert resp.status_code == 200

    def test_start_adds_span_to_store(self):
        with _build_client() as client:
            resp = client.post(
                "/action-spans/start",
                json={"session_id": "sess-003"},
            )
        span_id = resp.json()["span_id"]
        assert span_id in _svc_module._span_store


# ---------------------------------------------------------------------------
# POST /action-spans/{span_id}/score
# ---------------------------------------------------------------------------

class TestScoreSpan:
    def test_score_missing_span_returns_404(self):
        with _build_client() as client:
            resp = client.post(
                "/action-spans/nonexistent-id/score",
                json={"span_id": "nonexistent-id", "score_threshold": 0.5},
            )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_score_existing_span_returns_scored_status(self):
        """Score a span that has no clip — falls back to neutral scoring."""
        with _build_client() as client:
            start_resp = client.post(
                "/action-spans/start",
                json={"session_id": "sess-score", "action_type": "tap"},
            )
            span_id = start_resp.json()["span_id"]

            score_resp = client.post(
                f"/action-spans/{span_id}/score",
                json={"span_id": span_id, "score_threshold": 0.5},
            )
        assert score_resp.status_code == 200
        body = score_resp.json()
        assert body["span"]["status"] == "scored"
        assert body["manifest_updated"] is True
        assert "composite_score" in body["span"]
        assert "verified" in body["span"]


# ---------------------------------------------------------------------------
# GET /action-spans/{span_id}
# ---------------------------------------------------------------------------

class TestGetSpan:
    def test_get_existing_span(self):
        with _build_client() as client:
            start_resp = client.post(
                "/action-spans/start",
                json={"session_id": "sess-get"},
            )
            span_id = start_resp.json()["span_id"]
            get_resp = client.get(f"/action-spans/{span_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["span_id"] == span_id

    def test_get_nonexistent_span_returns_404(self):
        with _build_client() as client:
            resp = client.get("/action-spans/does-not-exist")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /action-spans?session_id=...
# ---------------------------------------------------------------------------

class TestListSpans:
    def test_list_requires_session_id(self):
        with _build_client() as client:
            resp = client.get("/action-spans")
        assert resp.status_code == 422  # missing required query param

    def test_list_returns_only_matching_session(self):
        with _build_client() as client:
            client.post("/action-spans/start", json={"session_id": "sess-A"})
            client.post("/action-spans/start", json={"session_id": "sess-A"})
            client.post("/action-spans/start", json={"session_id": "sess-B"})
            resp = client.get("/action-spans?session_id=sess-A")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == "sess-A"
        assert len(body["spans"]) == 2

    def test_list_empty_session_returns_zero_spans(self):
        with _build_client() as client:
            resp = client.get("/action-spans?session_id=no-such-session")
        assert resp.status_code == 200
        assert resp.json()["spans"] == []


# ---------------------------------------------------------------------------
# GET /action-spans/manifest/{session_id}
# ---------------------------------------------------------------------------

class TestManifest:
    def test_manifest_for_empty_session_returns_zeroes(self):
        with _build_client() as client:
            resp = client.get("/action-spans/manifest/empty-sess")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == "empty-sess"
        assert body["total_spans"] == 0
        assert body["pass_rate"] == 0.0

    def test_manifest_updates_after_scoring(self):
        with _build_client() as client:
            start_resp = client.post(
                "/action-spans/start",
                json={"session_id": "sess-manifest"},
            )
            span_id = start_resp.json()["span_id"]
            client.post(
                f"/action-spans/{span_id}/score",
                json={"span_id": span_id, "score_threshold": 0.0},
            )
            manifest_resp = client.get("/action-spans/manifest/sess-manifest")
        assert manifest_resp.status_code == 200
        body = manifest_resp.json()
        assert body["total_spans"] == 1
        assert body["scored_spans"] == 1

