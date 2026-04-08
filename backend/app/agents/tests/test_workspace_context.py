"""Regression tests for workspace-aware coordinator context."""

import json

import pytest

from app.agents.coordinator.context_tools import create_context_tools, get_workspace_context
from app.agents.coordinator.coordinator_service import _build_ui_context_info


def test_build_ui_context_info_includes_workspace_sections():
    info = _build_ui_context_info({
        "workspaceMode": True,
        "workspaceChannels": ["claw-communications", "general"],
        "workspaceIntent": "Track cross-team operating context",
    })

    assert "Workspace-aware mode" in info
    assert "#claw-communications" in info
    assert "Track cross-team operating context" in info


def test_create_context_tools_exposes_workspace_context():
    tools = create_context_tools()

    assert "get_workspace_context" in tools


@pytest.mark.asyncio
async def test_get_workspace_context_returns_usage_when_slack_unavailable(monkeypatch):
    import app.agents.coordinator.context_tools as ctx

    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setattr(ctx, "summarize_usage", lambda days=1: {
        "window_days": days,
        "generated_at": "2026-03-20T00:00:00+00:00",
        "totals": {
            "requests": 7,
            "estimated_cost_usd": 1.23,
            "total_tokens": 4567,
            "failed_requests": 1,
        },
        "by_interface": [{"name": "agent-api", "requests": 5}],
        "by_operation": [{"name": "strategy-agent", "requests": 3}],
        "by_model": [{"name": "gpt-5.4", "requests": 7}],
    })

    payload = json.loads(await get_workspace_context(scope="usage", days=2))

    assert payload["slack_available"] is False
    assert payload["usage"]["window_days"] == 2
    assert payload["usage"]["estimated_cost_usd"] == 1.23


@pytest.mark.asyncio
async def test_get_workspace_context_requires_explicit_enable(monkeypatch):
    import app.agents.coordinator.context_tools as ctx

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.delenv("TA_WORKSPACE_CONTEXT_ENABLED", raising=False)
    monkeypatch.setenv("TA_WORKSPACE_CHANNELS", "general")
    monkeypatch.setattr(ctx, "summarize_usage", lambda days=1: {
        "window_days": days,
        "generated_at": "2026-03-20T00:00:00+00:00",
        "totals": {
            "requests": 1,
            "estimated_cost_usd": 0.1,
            "total_tokens": 100,
            "failed_requests": 0,
        },
        "by_interface": [],
        "by_operation": [],
        "by_model": [],
    })

    payload = json.loads(await get_workspace_context(scope="overview", channels="general"))

    assert payload["workspace_context_enabled"] is False
    assert payload["slack_status"] == "disabled"
    assert payload["channel_summaries"] == []


@pytest.mark.asyncio
async def test_get_workspace_context_filters_channels_and_redacts_text(monkeypatch):
    import app.agents.coordinator.context_tools as ctx

    class FakeSlackClient:
        async def get_channel_history(self, channel, limit):
            return [
                {"ts": "1", "user": "U123", "text": "ship it", "reply_count": 1},
                {"ts": "2", "bot_id": "B456", "text": "bot message", "reply_count": 0},
            ]

        async def get_thread(self, channel, thread_ts):
            return [
                {"ts": thread_ts, "user": "U123", "text": "ship it"},
                {"ts": "3", "user": "U999", "text": "looks good"},
            ]

        async def close(self):
            return None

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("TA_WORKSPACE_CONTEXT_ENABLED", "true")
    monkeypatch.setenv("TA_WORKSPACE_CHANNELS", "general,claw-communications")
    monkeypatch.delenv("TA_WORKSPACE_ALLOW_MESSAGE_TEXT", raising=False)
    monkeypatch.setattr(ctx, "SlackClient", lambda: FakeSlackClient())
    monkeypatch.setattr(ctx, "summarize_usage", lambda days=1: {
        "window_days": days,
        "generated_at": "2026-03-20T00:00:00+00:00",
        "totals": {
            "requests": 2,
            "estimated_cost_usd": 0.2,
            "total_tokens": 200,
            "failed_requests": 0,
        },
        "by_interface": [],
        "by_operation": [],
        "by_model": [],
    })

    payload = json.loads(await get_workspace_context(scope="overview", channels="general,secret"))

    assert payload["channels_allowed"] == ["general"]
    assert payload["channels_blocked"] == ["secret"]
    assert payload["message_text_included"] is False
    assert len(payload["channel_summaries"]) == 1
    summary = payload["channel_summaries"][0]
    assert summary["channel"] == "general"
    assert summary["latest_human_messages"] == [{"ts": "1"}]
    assert summary["interesting_threads"] == [{"thread_ts": "1", "reply_count": 1}]
