"""Regression tests for orchestration continuity metadata."""

from app.agents.orchestration.run_session import OrchestrationRunSession


def test_session_result_to_dict_omits_optional_metadata_when_unset():
    """Legacy payload shape should remain compact when no continuity metadata exists."""
    session = OrchestrationRunSession(session_id="session-1")

    payload = session.result.to_dict()

    assert "topic" not in payload
    assert "resource_context" not in payload
    assert "continuity" not in payload


def test_session_result_serializes_topic_resources_and_continuity():
    """Optional continuity metadata should serialize in a structured way."""
    session = OrchestrationRunSession(
        session_id="session-2",
        topic={"id": "topic-login", "title": "Login stability", "summary": "Investigate flaky sign-in"},
        resource_context={
            "mode": "attached",
            "attached": [
                {"type": "device", "id": "emulator-5554", "title": "Pixel 8"},
                {"type": "ticket", "id": "BUG-101", "title": "Login crash", "status": "open"},
            ],
        },
        continuity={
            "memory_summary": "Crash happens after relaunch.",
            "carry_forward": ["Reuse the signed-in account"],
            "open_loops": ["Confirm on Android 14"],
        },
    )

    session.attach_resource(
        resource_type="evidence_manifest",
        resource_id="manifest-1",
        title="Login crash evidence",
        metadata={"screenshots": 3},
    )
    session.update_continuity(
        carry_forward=["Capture post-login screenshot", "Reuse the signed-in account"],
        open_loops=["Check after cold start"],
    )

    payload = session.result.to_dict()

    assert payload["topic"] == {
        "id": "topic-login",
        "title": "Login stability",
        "summary": "Investigate flaky sign-in",
    }
    assert payload["resource_context"]["mode"] == "attached"
    assert payload["resource_context"]["attached"][0] == {
        "type": "device",
        "id": "emulator-5554",
        "title": "Pixel 8",
    }
    assert payload["resource_context"]["attached"][2] == {
        "type": "evidence_manifest",
        "id": "manifest-1",
        "title": "Login crash evidence",
        "metadata": {"screenshots": 3},
    }
    assert payload["continuity"] == {
        "memory_summary": "Crash happens after relaunch.",
        "carry_forward": [
            "Reuse the signed-in account",
            "Capture post-login screenshot",
        ],
        "open_loops": [
            "Confirm on Android 14",
            "Check after cold start",
        ],
    }


def test_session_helpers_can_create_metadata_after_init():
    """Helper APIs should allow incremental continuity enrichment."""
    session = OrchestrationRunSession(session_id="session-3")

    session.set_topic("topic-checkout", title="Checkout freeze")
    session.attach_resource("device", "emulator-5556", status="active")
    session.update_continuity(memory_summary="Freeze only happens on step 4.")

    payload = session.result.to_dict()

    assert payload["topic"] == {"id": "topic-checkout", "title": "Checkout freeze"}
    assert payload["resource_context"] == {
        "attached": [{"type": "device", "id": "emulator-5556", "status": "active"}],
        "mode": "attached",
    }
    assert payload["continuity"] == {"memory_summary": "Freeze only happens on step 4."}