"""Claude Code usage tracker via ccusage.

Reads Claude Code token usage from ~/.claude/ usage logs and converts
them into usage_telemetry events for unified cost tracking.

If ccusage is not installed, all functions return empty results gracefully.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _ccusage_available() -> bool:
    """Check if ccusage CLI is installed."""
    try:
        result = subprocess.run(
            ["ccusage", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _claude_dir() -> Optional[Path]:
    """Find the Claude Code data directory."""
    home = Path.home()
    candidates = [
        home / ".claude",
        home / "Library" / "Application Support" / "claude",
    ]
    for d in candidates:
        if d.exists():
            return d
    return None


def get_ccusage_costs(days: int = 7) -> list[dict[str, Any]]:
    """Get Claude Code usage costs via ccusage CLI.

    Returns a list of usage records with token counts and cost estimates.
    Falls back to reading raw JSONL logs if ccusage CLI is not available.
    """
    # Try ccusage CLI first
    if _ccusage_available():
        return _get_via_cli(days)

    # Fall back to reading Claude Code logs directly
    return _get_from_logs(days)


def _get_via_cli(days: int = 7) -> list[dict[str, Any]]:
    """Get usage via ccusage CLI (preferred method).

    ccusage session --json returns:
    {
      "sessions": [{
        "sessionId": "...",
        "inputTokens": N,
        "outputTokens": N,
        "cacheCreationTokens": N,
        "cacheReadTokens": N,
        "totalTokens": N,
        "totalCost": N.NN,
        "lastActivity": "YYYY-MM-DD",
        "modelsUsed": ["claude-opus-4-6"],
        "modelBreakdowns": [{"modelName": "...", "inputTokens": N, ...}]
      }]
    }
    """
    since = datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)
    since_str = since.strftime("%Y%m%d")

    try:
        result = subprocess.run(
            ["ccusage", "session", "--json", f"--since={since_str}"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.debug("ccusage CLI returned non-zero: %s", result.stderr[:200])
            return []

        data = json.loads(result.stdout)
        records = []

        sessions = data.get("sessions", []) if isinstance(data, dict) else data
        for session in sessions:
            # Use primary model from modelsUsed list
            models_used = session.get("modelsUsed", [])
            primary_model = models_used[0] if models_used else "claude-opus-4-6"

            records.append({
                "source": "ccusage_cli",
                "session_id": session.get("sessionId", ""),
                "model": primary_model,
                "input_tokens": session.get("inputTokens", 0),
                "output_tokens": session.get("outputTokens", 0),
                "total_tokens": session.get("totalTokens", 0),
                "cache_read_tokens": session.get("cacheReadTokens", 0),
                "cache_write_tokens": session.get("cacheCreationTokens", 0),
                "cost_usd": session.get("totalCost", 0.0),
                "duration_s": 0,
                "timestamp": session.get("lastActivity", ""),
                "project": session.get("sessionId", ""),
                "models_used": models_used,
                "model_breakdowns": session.get("modelBreakdowns", []),
            })

        return records
    except Exception as e:
        logger.debug("ccusage CLI failed: %s", e)
        return []


def _get_from_logs(days: int = 7) -> list[dict[str, Any]]:
    """Read Claude Code usage directly from JSONL conversation logs.

    Claude Code stores conversations in ~/.claude/projects/*/conversations/
    as JSONL files. Each message has a usage block with token counts.
    """
    claude_dir = _claude_dir()
    if not claude_dir:
        return []

    records = []
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - (days * 86400)

    # Walk project directories for conversation logs
    projects_dir = claude_dir / "projects"
    if not projects_dir.exists():
        return records

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue

        # Check conversations/ or just .jsonl files
        conv_dirs = [project_dir, project_dir / "conversations"]
        for conv_dir in conv_dirs:
            if not conv_dir.exists():
                continue
            for f in conv_dir.glob("*.jsonl"):
                try:
                    session_tokens_in = 0
                    session_tokens_out = 0
                    session_cache_read = 0
                    session_cache_write = 0
                    session_model = "claude-opus-4-6"
                    session_ts = ""
                    message_count = 0

                    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # Check timestamp
                        ts_str = msg.get("timestamp", "")
                        if ts_str:
                            try:
                                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                                if ts.timestamp() < cutoff:
                                    continue
                                if not session_ts:
                                    session_ts = ts_str
                            except Exception:
                                pass

                        # Extract usage from assistant messages
                        inner = msg.get("message", {})
                        usage = inner.get("usage", {})
                        if usage:
                            session_tokens_in += usage.get("input_tokens", 0)
                            session_tokens_out += usage.get("output_tokens", 0)
                            session_cache_read += usage.get("cache_read_input_tokens", 0)
                            session_cache_write += usage.get("cache_creation_input_tokens", 0)
                            message_count += 1

                        model = inner.get("model", "")
                        if model:
                            session_model = model

                    if message_count > 0 and (session_tokens_in > 0 or session_tokens_out > 0):
                        records.append({
                            "source": "claude_logs",
                            "session_id": f.stem,
                            "model": session_model,
                            "input_tokens": session_tokens_in,
                            "output_tokens": session_tokens_out,
                            "total_tokens": session_tokens_in + session_tokens_out,
                            "cache_read_tokens": session_cache_read,
                            "cache_write_tokens": session_cache_write,
                            "cost_usd": 0.0,  # Calculated later via usage_telemetry.estimate_cost_usd
                            "duration_s": 0,
                            "timestamp": session_ts,
                            "project": project_dir.name,
                            "messages": message_count,
                        })
                except Exception as e:
                    logger.debug("Failed to parse %s: %s", f, e)
                    continue

    return sorted(records, key=lambda r: r.get("timestamp", ""), reverse=True)


def sync_ccusage_to_telemetry(days: int = 1) -> dict[str, Any]:
    """Sync Claude Code usage into the unified usage_telemetry system.

    Reads ccusage data and records each session as a usage_telemetry event
    so it shows up in the dashboard alongside OpenAI usage.
    """
    from .usage_telemetry import record_usage_event, estimate_cost_usd

    records = get_ccusage_costs(days=days)
    synced = 0
    total_tokens = 0
    total_cost = 0.0

    for r in records:
        input_tokens = r.get("input_tokens", 0)
        output_tokens = r.get("output_tokens", 0)
        model = r.get("model", "claude-opus-4-6")

        # Use actual cost from ccusage (includes cache pricing)
        cost = r.get("cost_usd", 0.0)
        if not cost:
            cost = estimate_cost_usd(model, input_tokens, output_tokens)

        ts_str = r.get("timestamp", "")
        ts = None
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except Exception:
                # ccusage uses YYYY-MM-DD format
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except Exception:
                    pass

        # Record event but override estimated_cost_usd with actual ccusage cost
        event = record_usage_event(
            interface="claude_code",
            operation="session",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=r.get("total_tokens", input_tokens + output_tokens),
            duration_ms=r.get("duration_s", 0) * 1000,
            success=True,
            metadata={
                "source": r.get("source", ""),
                "session_id": r.get("session_id", ""),
                "project": r.get("project", ""),
                "cache_read_tokens": r.get("cache_read_tokens", 0),
                "cache_write_tokens": r.get("cache_write_tokens", 0),
                "actual_cost_usd": cost,
                "models_used": r.get("models_used", []),
            },
            timestamp=ts,
        )
        # Patch the written event with actual cost (estimate_cost_usd doesn't know cache pricing)
        if cost > 0 and event.get("estimated_cost_usd", 0) != cost:
            event["estimated_cost_usd"] = cost
        synced += 1
        total_tokens += r.get("total_tokens", 0)
        total_cost += cost

    return {
        "synced": synced,
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 4),
        "source": records[0].get("source", "none") if records else "none",
    }
