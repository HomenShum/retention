"""Regression tests for coordinator model selection.

We require the coordinator to run on the orchestration model chain:
  gpt-5.4 -> gpt-5 -> gpt-5-mini

This ensures complex multi-agent orchestration uses the flagship thinking model,
while still having a robust fallback.
"""

def test_coordinator_uses_orchestration_model_chain(monkeypatch):
    """Coordinator primary model should be gpt-5.4 via orchestration chain."""
    from agents import Agent

    # Avoid importing/constructing the full device tool surface in this unit test.
    from app.agents.coordinator import coordinator_agent as coordinator_module

    monkeypatch.setattr(coordinator_module, "create_device_testing_tools", lambda service_ref=None: {})

    from app.agents.coordinator.coordinator_agent import create_coordinator_agent
    from app.agents.model_fallback import get_model_fallback_chain

    search_agent = Agent(name="Search", instructions="noop", model="gpt-5-mini")
    test_generation_agent = Agent(name="TG", instructions="noop", model="gpt-5-mini")
    device_testing_agent = Agent(name="DT", instructions="noop", model="gpt-5-mini")

    coordinator = create_coordinator_agent(
        search_agent=search_agent,
        test_generation_agent=test_generation_agent,
        device_testing_agent=device_testing_agent,
        scenarios=[],
        ui_context_info="",
        execute_simulation_func=None,
    )

    expected_chain = get_model_fallback_chain("orchestration")
    assert coordinator.model == expected_chain[0]
    assert coordinator.model == "gpt-5.4"
