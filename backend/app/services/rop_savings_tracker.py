"""
ROP Savings Tracker — records, compares, and aggregates savings from
Retained Operation Pattern usage (RET-14).

Tracks three run types:
  - cold: full pipeline, no retention assistance
  - assisted: retention suggest_next() active, may skip reasoning
  - replay: deterministic trajectory replay

Provides portfolio-level stats over time for the dashboard.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "rop_savings"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

_RUNS_FILE = _DATA_DIR / "runs.jsonl"


# ─── Data types ──────────────────────────────────────────────────────────

@dataclass
class ROPRunRecord:
    """A single run recorded for savings tracking."""
    run_id: str
    rop_id: str  # e.g. "rop.drx.v1" or "" for unclassified
    rop_family: str  # "DRX", "CSP", etc.
    run_type: str  # "cold", "assisted", "replay"
    timestamp: str
    # Token metrics
    total_tokens: int = 0
    reasoning_tokens: int = 0
    reasoning_tokens_avoided: int = 0  # by suggest_next
    # Time metrics
    total_time_s: float = 0.0
    time_saved_s: float = 0.0
    # Coverage
    files_searched: int = 0
    files_modified: int = 0
    urls_visited: int = 0
    layers_covered: int = 0
    # Quality
    suggestions_offered: int = 0
    suggestions_followed: int = 0
    divergences_detected: int = 0
    checkpoints_passed: int = 0
    checkpoints_failed: int = 0
    # Outcome
    success: bool = True
    health_grade: str = ""


# ─── Core tracker ────────────────────────────────────────────────────────

class ROPSavingsTracker:
    """Records and aggregates ROP savings data."""

    def __init__(self, data_dir: Optional[Path] = None):
        self._dir = data_dir or _DATA_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._runs_file = self._dir / "runs.jsonl"

    def record_run(self, record: ROPRunRecord) -> None:
        """Append a run record to the log."""
        with open(self._runs_file, "a") as f:
            f.write(json.dumps(asdict(record)) + "\n")
        logger.info(
            f"ROP savings: recorded {record.run_type} run {record.run_id} "
            f"(rop={record.rop_id}, tokens_avoided={record.reasoning_tokens_avoided})"
        )

    def _load_runs(self, days: int = 0) -> list[dict[str, Any]]:
        """Load run records from disk, optionally filtered by recency."""
        if not self._runs_file.exists():
            return []

        runs = []
        cutoff = None
        if days > 0:
            cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)

        for line in self._runs_file.read_text().strip().split("\n"):
            if not line:
                continue
            try:
                r = json.loads(line)
                if cutoff:
                    ts = datetime.fromisoformat(r.get("timestamp", "")).timestamp()
                    if ts < cutoff:
                        continue
                runs.append(r)
            except (json.JSONDecodeError, ValueError):
                continue
        return runs

    def compare_runs(
        self, cold_run_id: str, assisted_run_id: str
    ) -> dict[str, Any]:
        """Compare a cold run against a retention-assisted run."""
        runs = self._load_runs()
        cold = next((r for r in runs if r["run_id"] == cold_run_id), None)
        assisted = next((r for r in runs if r["run_id"] == assisted_run_id), None)

        if not cold or not assisted:
            return {"error": "One or both runs not found"}

        token_savings = cold["total_tokens"] - assisted["total_tokens"]
        reasoning_savings = cold["reasoning_tokens"] - assisted["reasoning_tokens"]
        time_savings = cold["total_time_s"] - assisted["total_time_s"]

        return {
            "cold_run": cold,
            "assisted_run": assisted,
            "delta": {
                "token_savings": token_savings,
                "token_savings_pct": round(
                    token_savings / max(cold["total_tokens"], 1) * 100, 1
                ),
                "reasoning_savings": reasoning_savings,
                "reasoning_savings_pct": round(
                    reasoning_savings / max(cold["reasoning_tokens"], 1) * 100, 1
                ),
                "time_savings_s": round(time_savings, 1),
                "time_savings_pct": round(
                    time_savings / max(cold["total_time_s"], 0.1) * 100, 1
                ),
                "suggestions_followed": assisted.get("suggestions_followed", 0),
                "divergences_detected": assisted.get("divergences_detected", 0),
            },
        }

    def pattern_stats(self) -> list[dict[str, Any]]:
        """Per-ROP pattern stats: invocations, success rate, savings."""
        runs = self._load_runs()
        by_rop: dict[str, list[dict]] = defaultdict(list)
        for r in runs:
            rop_id = r.get("rop_id") or "unclassified"
            by_rop[rop_id].append(r)

        stats = []
        for rop_id, rop_runs in by_rop.items():
            total = len(rop_runs)
            successes = sum(1 for r in rop_runs if r.get("success"))
            total_tokens_avoided = sum(r.get("reasoning_tokens_avoided", 0) for r in rop_runs)
            total_time_saved = sum(r.get("time_saved_s", 0) for r in rop_runs)
            total_suggestions = sum(r.get("suggestions_offered", 0) for r in rop_runs)
            total_followed = sum(r.get("suggestions_followed", 0) for r in rop_runs)
            total_divergences = sum(r.get("divergences_detected", 0) for r in rop_runs)

            avg_confidence = 0.0
            assisted = [r for r in rop_runs if r.get("run_type") == "assisted"]
            cold = [r for r in rop_runs if r.get("run_type") == "cold"]
            replay = [r for r in rop_runs if r.get("run_type") == "replay"]

            stats.append({
                "rop_id": rop_id,
                "rop_family": rop_runs[0].get("rop_family", ""),
                "total_invocations": total,
                "cold_runs": len(cold),
                "assisted_runs": len(assisted),
                "replay_runs": len(replay),
                "success_rate": round(successes / max(total, 1), 3),
                "total_tokens_avoided": total_tokens_avoided,
                "avg_tokens_avoided": round(total_tokens_avoided / max(total, 1)),
                "total_time_saved_s": round(total_time_saved, 1),
                "avg_time_saved_s": round(total_time_saved / max(total, 1), 1),
                "suggestion_follow_rate": round(
                    total_followed / max(total_suggestions, 1), 3
                ),
                "divergence_rate": round(
                    total_divergences / max(total_suggestions, 1), 3
                ),
            })

        stats.sort(key=lambda s: s["total_tokens_avoided"], reverse=True)
        return stats

    def portfolio_stats(self, days: int = 30) -> dict[str, Any]:
        """Aggregate portfolio stats over a time window."""
        runs = self._load_runs(days=days)
        if not runs:
            return {
                "timeframe_days": days,
                "total_runs": 0,
                "total_tokens_saved": 0,
                "total_time_saved_s": 0,
                "most_used_rop": None,
                "most_savings_rop": None,
                "daily_breakdown": [],
            }

        total_tokens_saved = sum(r.get("reasoning_tokens_avoided", 0) for r in runs)
        total_time_saved = sum(r.get("time_saved_s", 0) for r in runs)

        # Most used ROP
        rop_counts: dict[str, int] = defaultdict(int)
        rop_savings: dict[str, int] = defaultdict(int)
        for r in runs:
            rop_id = r.get("rop_id") or "unclassified"
            rop_counts[rop_id] += 1
            rop_savings[rop_id] += r.get("reasoning_tokens_avoided", 0)

        most_used = max(rop_counts, key=rop_counts.get) if rop_counts else None
        most_savings = max(rop_savings, key=rop_savings.get) if rop_savings else None

        # Daily breakdown
        daily: dict[str, dict] = defaultdict(
            lambda: {"runs": 0, "tokens_saved": 0, "time_saved_s": 0.0}
        )
        for r in runs:
            day = r.get("timestamp", "")[:10]
            daily[day]["runs"] += 1
            daily[day]["tokens_saved"] += r.get("reasoning_tokens_avoided", 0)
            daily[day]["time_saved_s"] += r.get("time_saved_s", 0)

        daily_list = [
            {"date": k, **v} for k, v in sorted(daily.items())
        ]

        return {
            "timeframe_days": days,
            "total_runs": len(runs),
            "total_tokens_saved": total_tokens_saved,
            "total_time_saved_s": round(total_time_saved, 1),
            "total_cost_saved_usd": round(total_tokens_saved / 1_000_000 * 15, 4),
            "most_used_rop": most_used,
            "most_savings_rop": most_savings,
            "daily_breakdown": daily_list,
        }


# ─── Module singleton ────────────────────────────────────────────────────

_tracker: Optional[ROPSavingsTracker] = None


def get_rop_savings_tracker() -> ROPSavingsTracker:
    global _tracker
    if _tracker is None:
        _tracker = ROPSavingsTracker()
    return _tracker
