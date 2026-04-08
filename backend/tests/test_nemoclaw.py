import pytest

from app.integrations.nemoclaw import (
    DeepAgentBridge,
    NemotronClient,
    OpenShellPolicy,
    TAToolSpec,
    dispatch_nemoclaw_run,
)


def test_openshell_policy_includes_expected_routes():
    policy = OpenShellPolicy(
        ta_endpoint="ta.internal:8000",
        emulator_host="emulator.internal:5554",
    )

    yaml_text = policy.to_yaml()

    assert 'destination: "ta.internal:8000"' in yaml_text
    assert 'destination: "emulator.internal:5554"' in yaml_text
    assert 'default_deny: true' in yaml_text


def test_deep_agent_bridge_maps_openai_safe_tool_names(monkeypatch):
    bridge = DeepAgentBridge(ta_endpoint="http://example.com/mcp")
    monkeypatch.setattr(
        bridge,
        "fetch_tools",
        lambda: [TAToolSpec(name="ta.pipeline.run", description="", parameters=[])],
    )

    assert bridge._tool_name_map() == {"ta_pipeline_run": "ta.pipeline.run"}


@pytest.mark.asyncio
async def test_dispatch_nemoclaw_run_requires_prompt():
    assert await dispatch_nemoclaw_run({}) == {"error": "prompt is required"}


def test_nemotron_client_allows_local_endpoint_without_api_key():
    client = NemotronClient(base_url="http://localhost:1234/v1", api_key="")

    assert client.is_configured() is True


def test_nemotron_client_requires_key_for_default_nim_endpoint():
    client = NemotronClient(base_url="https://integrate.api.nvidia.com/v1", api_key="")

    assert client.is_configured() is False
