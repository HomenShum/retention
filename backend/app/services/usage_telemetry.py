"""Unified usage telemetry + spend tracking.

This module centralizes token/cost attribution so every interface can report
usage in one place. Events are appended to JSONL files under data/usage_telemetry/
and can be summarized into a Slack-friendly daily spend report.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# March 2026 pricing per 1M tokens.
MODEL_PRICING: dict[str, dict[str, float]] = {
    # GPT-5.4 family
    "gpt-5.4": {"input": 2.50, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "gpt-5.4-nano": {"input": 0.20, "output": 1.25},
    # GPT-5 family
    "gpt-5": {"input": 2.00, "output": 8.00},
    "gpt-5-mini": {"input": 0.25, "output": 1.00},
    "gpt-5-nano": {"input": 0.10, "output": 0.40},
    # Anthropic family (best-effort for shared reporting)
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
}


_TELEMETRY_DIR = Path(
    os.getenv(
        "USAGE_TELEMETRY_DIR",
        str(Path(__file__).resolve().parents[3] / "data" / "usage_telemetry"),
    )
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _event_path(ts: datetime | None = None) -> Path:
    ts = ts or _utc_now()
    day = ts.date().isoformat()
    path = _TELEMETRY_DIR / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_model(model: str | None) -> str:
    if not model:
        return "unknown"
    clean = str(model).strip()
    if clean in MODEL_PRICING:
        return clean
    # Handle provider prefixes/suffixes like openai/gpt-5.4 or gpt-5.4-2026-03-17
    for candidate in sorted(MODEL_PRICING.keys(), key=len, reverse=True):
        if clean == candidate or clean.startswith(candidate) or clean.endswith(candidate):
            return candidate
    return clean


def estimate_cost_usd(model: str | None, input_tokens: int = 0, output_tokens: int = 0) -> float:
    """Estimate token cost from model pricing."""
    normalized = _normalize_model(model)
    pricing = MODEL_PRICING.get(normalized)
    if not pricing:
        return 0.0
    cost = (
        (max(input_tokens, 0) * pricing["input"]) +
        (max(output_tokens, 0) * pricing["output"])
    ) / 1_000_000
    return round(cost, 6)


def record_usage_event(
    *,
    interface: str,
    operation: str,
    model: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int | None = None,
    reasoning_tokens: int = 0,
    duration_ms: int = 0,
    success: bool = True,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Append a normalized usage event to disk.

    Each event represents one billable model interaction or agent run.
    """
    ts = timestamp or _utc_now()
    normalized_model = _normalize_model(model)
    total = total_tokens if total_tokens is not None else max(input_tokens, 0) + max(output_tokens, 0)
    event = {
        "timestamp": ts.isoformat(),
        "interface": interface or "unknown-interface",
        "operation": operation or "unknown-operation",
        "model": normalized_model,
        "raw_model": model or "",
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "total_tokens": int(total or 0),
        "reasoning_tokens": int(reasoning_tokens or 0),
        "duration_ms": int(duration_ms or 0),
        "estimated_cost_usd": estimate_cost_usd(normalized_model, int(input_tokens or 0), int(output_tokens or 0)),
        "success": bool(success),
        "error": (error or "")[:300],
        "metadata": metadata or {},
    }

    try:
        with _event_path(ts).open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except Exception as exc:
        logger.warning("Failed to record usage telemetry: %s", exc)
    return event


def _iter_events(days: int = 1) -> list[dict[str, Any]]:
    now = _utc_now().date()
    events: list[dict[str, Any]] = []
    for offset in range(max(days, 1)):
        day = (now - timedelta(days=offset)).isoformat()
        path = _TELEMETRY_DIR / f"{day}.jsonl"
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            continue
    return events


def summarize_usage(days: int = 1) -> dict[str, Any]:
    """Aggregate usage by interface, operation, and model for the last N days."""
    events = _iter_events(days=days)

    totals = {
        "requests": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "reasoning_tokens": 0,
        "duration_ms": 0,
        "estimated_cost_usd": 0.0,
        "unique_interfaces": 0,
        "unique_operations": 0,
        "unique_models": 0,
    }
    by_interface: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "reasoning_tokens": 0,
        "duration_ms": 0,
        "estimated_cost_usd": 0.0,
        "successful_requests": 0,
        "failed_requests": 0,
    })
    by_operation: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "reasoning_tokens": 0,
        "duration_ms": 0,
        "estimated_cost_usd": 0.0,
        "successful_requests": 0,
        "failed_requests": 0,
    })
    by_model: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "reasoning_tokens": 0,
        "estimated_cost_usd": 0.0,
    })

    for event in events:
        interface = event.get("interface", "unknown-interface")
        operation = event.get("operation", "unknown-operation")
        model = event.get("model", "unknown")
        input_tokens = int(event.get("input_tokens", 0) or 0)
        output_tokens = int(event.get("output_tokens", 0) or 0)
        total_tokens = int(event.get("total_tokens", 0) or (input_tokens + output_tokens))
        reasoning_tokens = int(event.get("reasoning_tokens", 0) or 0)
        duration_ms = int(event.get("duration_ms", 0) or 0)
        cost = float(event.get("estimated_cost_usd", 0.0) or 0.0)
        success = bool(event.get("success", True))

        totals["requests"] += 1
        totals["successful_requests"] += 1 if success else 0
        totals["failed_requests"] += 0 if success else 1
        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["total_tokens"] += total_tokens
        totals["reasoning_tokens"] += reasoning_tokens
        totals["duration_ms"] += duration_ms
        totals["estimated_cost_usd"] += cost

        iface = by_interface[interface]
        iface["requests"] += 1
        iface["input_tokens"] += input_tokens
        iface["output_tokens"] += output_tokens
        iface["total_tokens"] += total_tokens
        iface["reasoning_tokens"] += reasoning_tokens
        iface["duration_ms"] += duration_ms
        iface["estimated_cost_usd"] += cost
        iface["successful_requests"] += 1 if success else 0
        iface["failed_requests"] += 0 if success else 1

        op = by_operation[operation]
        op["requests"] += 1
        op["input_tokens"] += input_tokens
        op["output_tokens"] += output_tokens
        op["total_tokens"] += total_tokens
        op["reasoning_tokens"] += reasoning_tokens
        op["duration_ms"] += duration_ms
        op["estimated_cost_usd"] += cost
        op["successful_requests"] += 1 if success else 0
        op["failed_requests"] += 0 if success else 1

        mdl = by_model[model]
        mdl["requests"] += 1
        mdl["input_tokens"] += input_tokens
        mdl["output_tokens"] += output_tokens
        mdl["total_tokens"] += total_tokens
        mdl["reasoning_tokens"] += reasoning_tokens
        mdl["estimated_cost_usd"] += cost

    totals["estimated_cost_usd"] = round(totals["estimated_cost_usd"], 6)
    totals["unique_interfaces"] = len(by_interface)
    totals["unique_operations"] = len(by_operation)
    totals["unique_models"] = len(by_model)

    def _finalize(entries: dict[str, dict[str, Any]], *, include_duration: bool = True) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for name, payload in entries.items():
            row = {"name": name, **payload}
            row["estimated_cost_usd"] = round(float(row.get("estimated_cost_usd", 0.0)), 6)
            requests = max(int(row.get("requests", 0)), 1)
            row["avg_cost_usd"] = round(row["estimated_cost_usd"] / requests, 6)
            if include_duration:
                row["avg_duration_ms"] = round(float(row.get("duration_ms", 0)) / requests, 1)
            rows.append(row)
        rows.sort(key=lambda r: (-r["estimated_cost_usd"], -r["requests"], r["name"]))
        return rows

    return {
        "window_days": max(days, 1),
        "generated_at": _utc_now().isoformat(),
        "totals": totals,
        "by_interface": _finalize(by_interface, include_duration=True),
        "by_operation": _finalize(by_operation, include_duration=True),
        "by_model": _finalize(by_model, include_duration=False),
        "events": events,
    }


def build_slack_usage_report(summary: dict[str, Any], top_n: int = 5) -> str:
    """Render a concise Slack summary of spend + usage volume."""
    totals = summary.get("totals", {})
    interfaces = summary.get("by_interface", [])[:top_n]
    operations = summary.get("by_operation", [])[:top_n]
    models = summary.get("by_model", [])[:top_n]
    window_days = int(summary.get("window_days", 1) or 1)
    label = "today" if window_days == 1 else f"last {window_days} days"

    lines = [
        ":money_with_wings: *Usage & Spend Snapshot*",
        "Think of this like the fuel gauge for the whole machine: it shows where requests, tokens, and dollars are actually going.",
        "",
        f"*{label.title()} so far*",
        f"• {totals.get('requests', 0)} tracked calls across {totals.get('unique_interfaces', 0)} interfaces and {totals.get('unique_operations', 0)} operations",
        f"• ${totals.get('estimated_cost_usd', 0.0):,.4f} estimated model spend",
        f"• {totals.get('total_tokens', 0):,} total tokens ({totals.get('input_tokens', 0):,} in / {totals.get('output_tokens', 0):,} out)",
        f"• {totals.get('failed_requests', 0)} failed calls",
    ]

    if interfaces:
        lines.append("")
        lines.append("*By interface*")
        for item in interfaces:
            lines.append(
                f"• `{item['name']}` — {item['requests']} calls, ${item['estimated_cost_usd']:,.4f}, {item['total_tokens']:,} tokens"
            )

    if operations:
        lines.append("")
        lines.append("*Top operations*")
        for item in operations:
            lines.append(
                f"• `{item['name']}` — {item['requests']} calls, ${item['estimated_cost_usd']:,.4f}, {item['total_tokens']:,} tokens"
            )

    if models:
        lines.append("")
        lines.append("*By model*")
        for item in models:
            lines.append(
                f"• `{item['name']}` — {item['requests']} calls, ${item['estimated_cost_usd']:,.4f}, {item['total_tokens']:,} tokens"
            )

    generated_at = summary.get("generated_at", "")
    if generated_at:
        lines.append("")
        lines.append(f"_Updated: {generated_at[:19].replace('T', ' ')} UTC_")

    return "\n".join(lines)


async def upsert_daily_usage_message(
    *,
    slack=None,
    convex=None,
    channel: str = "C0AM2J4G6S0",
    thread_ts: str | None = None,
    days: int = 1,
) -> dict[str, Any]:
    """Post or update the daily usage snapshot in Slack."""
    from .convex_client import ConvexClient
    from .slack_client import SlackClient

    own_slack = slack is None
    own_convex = convex is None
    slack = slack or SlackClient()
    convex = convex or ConvexClient()

    try:
        summary = summarize_usage(days=days)
        text = build_slack_usage_report(summary)
        today = datetime.now(timezone.utc).date().isoformat()
        state = await convex.get_task_state("usage_summary") if hasattr(convex, "get_task_state") else None
        message_ts = state.get("usageSummaryTs") if state and state.get("usageSummaryDate") == today else None

        if not thread_ts:
            thread_ts = await slack.get_or_create_daily_thread(channel)

        if message_ts:
            await slack.update_message(channel, message_ts, text)
            final_ts = message_ts
            action = "updated"
        else:
            posted = await slack.post_message(channel, text, thread_ts=thread_ts)
            final_ts = posted.get("ts") or posted.get("message", {}).get("ts") or ""
            action = "posted"

        if final_ts and hasattr(convex, "update_task_state"):
            await convex.update_task_state("usage_summary", {
                "lastRunAt": time.time(),
                "iterationCount": (state or {}).get("iterationCount", 0) + 1,
                "status": "active",
                "usageSummaryDate": today,
                "usageSummaryTs": final_ts,
                "usageSummaryThreadTs": thread_ts or "",
                "lastEstimatedCostUsd": summary.get("totals", {}).get("estimated_cost_usd", 0.0),
                "lastRequests": summary.get("totals", {}).get("requests", 0),
            })

        return {"action": action, "ts": final_ts, "summary": summary, "text": text}
    finally:
        if own_slack:
            await slack.close()
        if own_convex:
            await convex.close()
