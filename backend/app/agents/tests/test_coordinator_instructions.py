"""Regression tests for coordinator instructions continuity guidance."""

from app.agents.coordinator.coordinator_instructions import create_coordinator_instructions


def test_coordinator_instructions_include_topic_and_canvas_memory_guidance():
    """Coordinator prompt should include the additive Epiral-inspired stance."""
    instructions = create_coordinator_instructions(
        scenarios=[{"name": "login", "description": "Test login flow"}],
        ui_context_info="",
    )

    assert "Treat each active request as an ongoing topic" in instructions
    assert "Use canvas memory when delegating or resuming work" in instructions
    assert "Let attached resources shape delegation" in instructions


def test_coordinator_instructions_preserve_existing_routing_rules():
    """Epiral guidance should remain additive to the current coordinator behavior."""
    instructions = create_coordinator_instructions(scenarios=[], ui_context_info="")

    assert "search for bugs" in instructions
    assert "Device Testing Specialist" in instructions
    assert "launch_emulators" in instructions

def test_coordinator_instructions_include_workspace_context_guidance():
    """Workspace-aware orchestration should be called out explicitly."""
    instructions = create_coordinator_instructions(scenarios=[], ui_context_info="")

    assert "get_workspace_context(scope=\"overview\")" in instructions
    assert "operator surface" in instructions
