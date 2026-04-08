import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import usage_telemetry as usage_telemetry


def _sample_now() -> datetime:
    return datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)


def test_estimate_cost_usd_uses_model_pricing() -> None:
    assert usage_telemetry.estimate_cost_usd("gpt-5.4", 1_000_000, 1_000_000) == 17.5
    assert usage_telemetry.estimate_cost_usd("gpt-5.4-mini", 2_000_000, 0) == 1.5
    assert usage_telemetry.estimate_cost_usd("unknown-model", 1_000_000, 1_000_000) == 0.0


def test_record_and_summarize_usage_events(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("USAGE_TELEMETRY_DIR", str(tmp_path / "usage"))
    module = importlib.reload(usage_telemetry)

    now = _sample_now()
    module.record_usage_event(
        interface="agent-api",
        operation="strategy-brief",
        model="gpt-5.4",
        input_tokens=1_000,
        output_tokens=500,
        total_tokens=1_500,
        duration_ms=1200,
        timestamp=now,
    )
    module.record_usage_event(
        interface="slack_digest",
        operation="digest_composition",
        model="gpt-5.4-mini",
        input_tokens=2_000,
        output_tokens=1_000,
        total_tokens=3_000,
        reasoning_tokens=200,
        duration_ms=800,
        success=False,
        error="timeout",
        timestamp=now,
    )

    summary = module.summarize_usage(days=1)

    assert summary["totals"]["requests"] == 2
    assert summary["totals"]["failed_requests"] == 1
    assert summary["totals"]["total_tokens"] == 4_500
    assert summary["totals"]["reasoning_tokens"] == 200
    assert summary["totals"]["unique_operations"] == 2
    assert {row["name"] for row in summary["by_interface"]} == {"agent-api", "slack_digest"}
    assert {row["name"] for row in summary["by_operation"]} == {"strategy-brief", "digest_composition"}
    assert {row["name"] for row in summary["by_model"]} == {"gpt-5.4", "gpt-5.4-mini"}


def test_build_slack_usage_report_includes_cost_interfaces_and_operations(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("USAGE_TELEMETRY_DIR", str(tmp_path / "usage"))
    module = importlib.reload(usage_telemetry)

    now = _sample_now()
    module.record_usage_event(
        interface="agent-api",
        operation="strategy-brief",
        model="gpt-5.4",
        input_tokens=1_000,
        output_tokens=500,
        total_tokens=1_500,
        duration_ms=1200,
        timestamp=now,
    )

    report = module.build_slack_usage_report(module.summarize_usage(days=1))

    assert "Usage & Spend Snapshot" in report
    assert "agent-api" in report
    assert "strategy-brief" in report
    assert "Top operations" in report
    assert "gpt-5.4" in report
    assert "$" in report
