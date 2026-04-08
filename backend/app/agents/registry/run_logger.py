"""Simple JSON logger for agent runs.

Saves a compact summary after each agent run to data/agent_runs/{date}/{timestamp}.json.
Used for:
  - Daily summary aggregation
  - Agent performance tracking
  - Historical context for future queries
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_RUNS_DIR = Path(os.getenv(
    "AGENT_RUNS_DIR",
    str(Path(__file__).resolve().parents[4] / "data" / "agent_runs"),
))


def log_agent_run(result: Dict[str, Any], question: str, agent_name: str) -> Optional[str]:
    """Save a compact run summary to disk. Returns the file path, or None on failure."""
    try:
        now = time.time()
        date_str = time.strftime("%Y-%m-%d", time.localtime(now))
        ts_str = time.strftime("%H%M%S", time.localtime(now))

        day_dir = _RUNS_DIR / date_str
        day_dir.mkdir(parents=True, exist_ok=True)

        summary = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
            "agent": agent_name,
            "question": question[:500],
            "strategy": result.get("strategy", {}),
            "tool_calls": result.get("tool_calls", []),
            "num_tools": len(result.get("tool_calls", [])),
            "turns": result.get("turns", 0),
            "tokens": result.get("tokens", {}),
            "duration_ms": result.get("duration_ms", 0),
            "confidence": result.get("confidence", ""),
            "model": result.get("model", ""),
            "estimated_cost_usd": result.get("estimated_cost_usd", 0.0),
            "telemetry_interface": result.get("telemetry_interface", ""),
            "error": result.get("error"),
            # Compact answer — first 300 chars
            "answer_preview": (result.get("text", "") or "")[:300],
        }

        file_path = day_dir / f"{ts_str}_{agent_name}.json"
        file_path.write_text(json.dumps(summary, indent=2, default=str))
        logger.debug("Logged agent run to %s", file_path)
        return str(file_path)

    except Exception as e:
        logger.warning("Failed to log agent run: %s", e)
        return None


def get_today_runs(agent_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get all runs from today, optionally filtered by agent name."""
    date_str = time.strftime("%Y-%m-%d")
    day_dir = _RUNS_DIR / date_str
    if not day_dir.exists():
        return []

    runs = []
    for f in sorted(day_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            if agent_name and data.get("agent") != agent_name:
                continue
            runs.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return runs


def get_recent_runs(days: int = 7, agent_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get runs from the last N days."""
    runs = []
    for i in range(days):
        date_str = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
        day_dir = _RUNS_DIR / date_str
        if not day_dir.exists():
            continue
        for f in sorted(day_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                if agent_name and data.get("agent") != agent_name:
                    continue
                runs.append(data)
            except (json.JSONDecodeError, OSError):
                continue
    return runs
