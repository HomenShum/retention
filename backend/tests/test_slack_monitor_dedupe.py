import pytest

from app.services.slack_monitor import Opportunity, OpportunityType, _has_recent_bot_reply


from typing import Optional


class FakeSlack:
    def __init__(self, replies=None, error: Optional[Exception] = None):
        self._replies = replies or []
        self._error = error

    async def get_thread(self, channel: str, thread_ts: str):
        if self._error:
            raise self._error
        return self._replies

    def is_bot_message(self, msg: dict) -> bool:
        return bool(msg.get("bot_id") or msg.get("user") == "U0ALSPANA1G")


@pytest.mark.asyncio
async def test_has_recent_bot_reply_detects_bot_response_after_source_message():
    import time

    source_ts = str(time.time())
    bot_ts = str(float(source_ts) + 1)
    opportunity = Opportunity(
        type=OpportunityType.META_FEEDBACK,
        channel="C123",
        message_ts=source_ts,
        message_preview="please stop double replying",
    )
    slack = FakeSlack(
        replies=[
            {"ts": source_ts, "user": "U_HUMAN", "text": "please stop double replying"},
            {"ts": bot_ts, "user": "U0ALSPANA1G", "text": "first bot response"},
        ]
    )

    assert await _has_recent_bot_reply(slack, opportunity, lookback_seconds=7200) is True


@pytest.mark.asyncio
async def test_has_recent_bot_reply_ignores_human_only_threads():
    import time

    source_ts = str(time.time())
    human_ts = str(float(source_ts) + 1)
    opportunity = Opportunity(
        type=OpportunityType.DIRECT_QUESTION,
        channel="C123",
        message_ts=source_ts,
        message_preview="what changed?",
    )
    slack = FakeSlack(
        replies=[
            {"ts": source_ts, "user": "U_HUMAN", "text": "what changed?"},
            {"ts": human_ts, "user": "U_OTHER", "text": "looking now"},
        ]
    )

    assert await _has_recent_bot_reply(slack, opportunity, lookback_seconds=7200) is False


@pytest.mark.asyncio
async def test_has_recent_bot_reply_fails_open_when_thread_lookup_errors():
    opportunity = Opportunity(
        type=OpportunityType.DIRECT_QUESTION,
        channel="C123",
        message_ts="300.0",
        message_preview="hello?",
    )
    slack = FakeSlack(error=RuntimeError("slack unavailable"))

    assert await _has_recent_bot_reply(slack, opportunity, lookback_seconds=7200) is False
