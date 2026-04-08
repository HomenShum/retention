from pathlib import Path
import importlib.util

_MODULE_PATH = Path(__file__).parent.parent.parent / "scripts/stream-agent-to-slack.py"
_spec = importlib.util.spec_from_file_location("stream_agent_to_slack", _MODULE_PATH)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[assignment]


def test_claim_stream_request_blocks_duplicate_inbound_message(tmp_path):
    claim = _mod.claim_stream_request(
        channel="C123",
        thread_ts="1774484735.887299",
        agent="strategy-brief",
        source_key="ts:1774484735.887299",
        guard_dir=str(tmp_path),
        now=1_000,
    )

    assert claim["claimed"] is True

    duplicate = _mod.claim_stream_request(
        channel="C123",
        thread_ts="1774484735.887299",
        agent="strategy-brief",
        source_key="ts:1774484735.887299",
        guard_dir=str(tmp_path),
        now=1_001,
    )

    assert duplicate["claimed"] is False
    assert duplicate["reason"] == "in_progress"

    _mod.mark_stream_request_done(claim, now=1_002)

    completed = _mod.claim_stream_request(
        channel="C123",
        thread_ts="1774484735.887299",
        agent="strategy-brief",
        source_key="ts:1774484735.887299",
        guard_dir=str(tmp_path),
        now=1_003,
    )

    assert completed["claimed"] is False
    assert completed["reason"] == "completed"


def test_claim_stream_request_reclaims_stale_lock(tmp_path):
    import os
    import time

    claim = _mod.claim_stream_request(
        channel="C123",
        thread_ts="1774482914.785059",
        agent="strategy-brief",
        source_key="ts:1774482914.785059",
        guard_dir=str(tmp_path),
    )

    assert claim["claimed"] is True

    stale_at = time.time() - _mod.STREAM_IN_PROGRESS_TTL_S - 5
    os.utime(claim["lock_path"], (stale_at, stale_at))

    reclaimed = _mod.claim_stream_request(
        channel="C123",
        thread_ts="1774482914.785059",
        agent="strategy-brief",
        source_key="ts:1774482914.785059",
        guard_dir=str(tmp_path),
    )

    assert reclaimed["claimed"] is True
    assert reclaimed["reason"] == "claimed"
