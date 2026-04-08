"""
Diff Analyzer — tracks workflow quality across sessions over days/weeks.

This is the layer that can't be distilled to a smaller model.
Large models generate content. Small models (this) analyze the diff
and signal when frontier model intervention is needed.

Key behaviors:
1. Compare session outcomes across time windows (day, week, month)
2. Detect drift in workflow quality (completion rate dropping)
3. Signal when a frontier model should be invoked (quality below threshold)
4. Track which workflows are stable vs degrading

This is NOT content generation. This is watchdog + signal.
Runs cheaply. Nudges expensively only when needed.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .session_reader import read_current_session, list_sessions, SessionSummary

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_DIFF_DIR = _DATA_DIR / "workflow_diffs"
_DIFF_DIR.mkdir(parents=True, exist_ok=True)
_VERDICT_LOG = _DATA_DIR / "verdict_log"
_VERDICT_LOG.mkdir(parents=True, exist_ok=True)


@dataclass
class SessionDiff:
    """Diff between two sessions or time windows."""
    window: str = ""  # "day", "week", "month"
    sessions_compared: int = 0

    # Capability drift
    search_usage_trend: str = ""  # "stable", "declining", "absent"
    preview_usage_trend: str = ""
    test_usage_trend: str = ""
    tool_call_avg_delta: float = 0.0

    # Workflow compliance drift
    avg_steps_done: float = 0.0
    avg_steps_missing: float = 0.0
    compliance_trend: str = ""  # "improving", "stable", "degrading"

    # Signals
    frontier_intervention_needed: bool = False
    intervention_reason: str = ""
    nudge_level: str = ""  # "none", "soft", "strong", "block"


@dataclass
class VerdictRecord:
    """Single verdict record for time-series tracking."""
    timestamp: str = ""
    session_id: str = ""
    workflow_id: str = ""
    verdict: str = ""
    steps_done: int = 0
    steps_missing: int = 0
    total_steps: int = 0
    missing_steps: List[str] = field(default_factory=list)
    has_search: bool = False
    has_preview: bool = False
    has_tests: bool = False
    tool_calls: int = 0


def record_verdict(
    session: SessionSummary,
    verdict_data: Dict[str, Any],
) -> None:
    """Record a verdict for time-series analysis."""
    record = VerdictRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        session_id=session.session_id,
        workflow_id=verdict_data.get("workflow_id", ""),
        verdict=verdict_data.get("verdict", ""),
        steps_done=verdict_data.get("steps_done", 0),
        steps_missing=verdict_data.get("steps_missing", 0),
        total_steps=verdict_data.get("steps_done", 0) + verdict_data.get("steps_missing", 0) + verdict_data.get("steps_partial", 0),
        missing_steps=verdict_data.get("missing_steps", []),
        has_search=session.has_web_search,
        has_preview=session.has_preview,
        has_tests=session.has_tests,
        tool_calls=session.total_tool_calls,
    )

    path = _VERDICT_LOG / f"verdicts-{datetime.now().strftime('%Y%m%d')}.jsonl"
    with open(path, "a") as f:
        f.write(json.dumps(asdict(record)) + "\n")


def analyze_diff(
    window: str = "week",
    project_path: str = "",
) -> SessionDiff:
    """Analyze workflow quality diff across a time window.

    This is the cheap watchdog operation. Reads verdict history,
    computes trends, and signals when frontier intervention is needed.
    """
    verdicts = _load_verdicts(window)
    sessions = list_sessions(project_path, limit=50)

    diff = SessionDiff(
        window=window,
        sessions_compared=len(verdicts),
    )

    if not verdicts:
        diff.frontier_intervention_needed = False
        diff.intervention_reason = "No verdict history yet — need more data"
        diff.nudge_level = "none"
        return diff

    # ── Capability trends ──
    recent = verdicts[-min(5, len(verdicts)):]
    older = verdicts[:max(1, len(verdicts) - 5)]

    recent_search = sum(1 for v in recent if v.get("has_search")) / max(len(recent), 1)
    older_search = sum(1 for v in older if v.get("has_search")) / max(len(older), 1)
    diff.search_usage_trend = _trend(older_search, recent_search)

    recent_preview = sum(1 for v in recent if v.get("has_preview")) / max(len(recent), 1)
    older_preview = sum(1 for v in older if v.get("has_preview")) / max(len(older), 1)
    diff.preview_usage_trend = _trend(older_preview, recent_preview)

    recent_tests = sum(1 for v in recent if v.get("has_tests")) / max(len(recent), 1)
    older_tests = sum(1 for v in older if v.get("has_tests")) / max(len(older), 1)
    diff.test_usage_trend = _trend(older_tests, recent_tests)

    # ── Compliance trends ──
    recent_done = [v.get("steps_done", 0) for v in recent]
    recent_missing = [v.get("steps_missing", 0) for v in recent]
    older_done = [v.get("steps_done", 0) for v in older]

    diff.avg_steps_done = sum(recent_done) / max(len(recent_done), 1)
    diff.avg_steps_missing = sum(recent_missing) / max(len(recent_missing), 1)

    avg_recent_done = sum(recent_done) / max(len(recent_done), 1)
    avg_older_done = sum(older_done) / max(len(older_done), 1)
    diff.compliance_trend = _trend(avg_older_done, avg_recent_done)

    # ── Frontier intervention signal ──
    # Signal frontier model if:
    # 1. Compliance is degrading
    # 2. Key capabilities are declining
    # 3. Too many failed verdicts recently
    failed_recent = sum(1 for v in recent if v.get("verdict") in ("failed_replay", "frontier_required"))
    failed_rate = failed_recent / max(len(recent), 1)

    reasons = []
    if diff.compliance_trend == "degrading":
        reasons.append("workflow compliance degrading over time")
    if diff.search_usage_trend == "declining":
        reasons.append("web search usage declining — may be missing context")
    if diff.preview_usage_trend == "declining":
        reasons.append("preview/QA usage declining — may be skipping visual verification")
    if failed_rate > 0.5:
        reasons.append(f"{failed_rate:.0%} of recent sessions failed the workflow judge")

    if reasons:
        diff.frontier_intervention_needed = True
        diff.intervention_reason = "; ".join(reasons)
        diff.nudge_level = "strong" if len(reasons) >= 2 else "soft"
    else:
        diff.frontier_intervention_needed = False
        diff.nudge_level = "none"

    # Persist
    path = _DIFF_DIR / f"diff-{window}-{datetime.now().strftime('%Y%m%d')}.json"
    path.write_text(json.dumps(asdict(diff), indent=2))

    return diff


def get_quality_timeline(
    days: int = 30,
) -> List[Dict[str, Any]]:
    """Get a timeline of workflow quality for the dashboard.

    Returns daily aggregates: date, sessions, avg_steps_done,
    avg_steps_missing, search_rate, preview_rate, test_rate.
    """
    all_verdicts = _load_all_verdicts(days)
    if not all_verdicts:
        return []

    # Group by date
    by_date: Dict[str, List[Dict]] = defaultdict(list)
    for v in all_verdicts:
        ts = v.get("timestamp", "")
        if ts:
            date = ts[:10]  # YYYY-MM-DD
            by_date[date].append(v)

    timeline = []
    for date in sorted(by_date.keys()):
        day_verdicts = by_date[date]
        n = len(day_verdicts)
        timeline.append({
            "date": date,
            "sessions": n,
            "avg_steps_done": round(sum(v.get("steps_done", 0) for v in day_verdicts) / n, 1),
            "avg_steps_missing": round(sum(v.get("steps_missing", 0) for v in day_verdicts) / n, 1),
            "search_rate": round(sum(1 for v in day_verdicts if v.get("has_search")) / n, 2),
            "preview_rate": round(sum(1 for v in day_verdicts if v.get("has_preview")) / n, 2),
            "test_rate": round(sum(1 for v in day_verdicts if v.get("has_tests")) / n, 2),
            "fail_rate": round(sum(1 for v in day_verdicts if v.get("verdict") in ("failed_replay", "frontier_required")) / n, 2),
        })

    return timeline


# ─── Helpers ────────────────────────────────────────────────────────────

def _trend(older: float, recent: float) -> str:
    """Determine trend direction."""
    if abs(recent - older) < 0.05:
        return "stable"
    if recent > older:
        return "improving"
    return "declining"


def _load_verdicts(window: str) -> List[Dict[str, Any]]:
    """Load verdicts for a time window."""
    days = {"day": 1, "week": 7, "month": 30}.get(window, 7)
    return _load_all_verdicts(days)


def _load_all_verdicts(days: int) -> List[Dict[str, Any]]:
    """Load all verdicts from the last N days."""
    if not _VERDICT_LOG.exists():
        return []

    cutoff = datetime.now() - timedelta(days=days)
    verdicts = []

    for f in sorted(_VERDICT_LOG.glob("*.jsonl")):
        # Parse date from filename
        try:
            file_date = f.stem.split("-", 1)[1]  # "verdicts-20260403" → "20260403"
            fd = datetime.strptime(file_date, "%Y%m%d")
            if fd < cutoff:
                continue
        except (ValueError, IndexError):
            continue

        try:
            for line in f.read_text().strip().split("\n"):
                if line.strip():
                    verdicts.append(json.loads(line))
        except Exception:
            continue

    return verdicts
