"""
External Tool Benchmark Comparison API.

Records and compares benchmark results across external tools
(Claude Code vanilla, Cursor, Windsurf, OpenClaw, Google Antigravity)
against retention.sh (Claude Code + TA MCP tools).
"""

import json
import logging
import math
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/benchmarks/external", tags=["benchmark-external"])

# ── Data directories ────────────────────────────────────────────

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data",
)
_RECORDS_PATH = os.path.join(_DATA_DIR, "benchmark_external.json")
_GROUND_TRUTH_PATH = os.path.join(_DATA_DIR, "benchmark_ground_truth.json")


# ── External tool definitions ───────────────────────────────────

EXTERNAL_TOOLS: Dict[str, Dict[str, str]] = {
    "claude-code-vanilla": {
        "name": "Claude Code (vanilla)",
        "description": "No MCP tools, raw agent",
    },
    "cursor-vanilla": {
        "name": "Cursor (vanilla)",
        "description": "No MCP tools, raw agent",
    },
    "windsurf-vanilla": {
        "name": "Windsurf (vanilla)",
        "description": "No MCP tools, raw agent",
    },
    "openclaw-vanilla": {
        "name": "OpenClaw (vanilla)",
        "description": "No TA harnesses, raw agent",
    },
    "google-antigravity": {
        "name": "Google Antigravity (vanilla)",
        "description": "No MCP tools, raw agent",
    },
    "retention": {
        "name": "Claude Code + TA Agent",
        "description": "Flow registry, deep-wide search, ActionSpan, curated harnesses",
    },
}


# ── Pydantic models ─────────────────────────────────────────────

class BugRecord(BaseModel):
    title: str
    severity: str
    is_true_positive: bool


class ExternalBenchmarkRecord(BaseModel):
    tool_id: str = Field(..., description="Key from EXTERNAL_TOOLS")
    app_name: str
    task_description: str
    duration_seconds: float
    token_cost_usd: float
    bugs_found: List[BugRecord] = Field(default_factory=list)
    bugs_missed: List[str] = Field(
        default_factory=list, description="Known bugs that were NOT found"
    )
    false_positives: int = 0
    consistency_score: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="0-1, similarity to previous runs"
    )
    has_evidence: bool = False
    report_quality: Literal["chat-only", "structured", "shareable-with-clips"] = (
        "chat-only"
    )
    notes: str = ""


class ExternalBenchmarkStored(ExternalBenchmarkRecord):
    """Record as persisted on disk, with generated fields."""
    record_id: str
    recorded_at: str


class GroundTruthBug(BaseModel):
    bug_id: str
    title: str
    severity: str
    description: str = ""


class GroundTruthRequest(BaseModel):
    app_name: str
    known_bugs: List[GroundTruthBug]


class ToolMetrics(BaseModel):
    tool_id: str
    tool_name: str
    run_count: int = 0
    avg_duration: float = 0.0
    avg_cost: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    consistency: float = 0.0
    report_quality_distribution: Dict[str, int] = Field(default_factory=dict)
    evidence_rate: float = 0.0


class ComparisonResponse(BaseModel):
    app_filter: Optional[str] = None
    tools: List[ToolMetrics] = Field(default_factory=list)
    deltas_vs_retention: Dict[str, Dict[str, float]] = Field(default_factory=dict)
    ground_truth_count: int = 0


# ── File I/O helpers ────────────────────────────────────────────

def _load_records() -> List[dict]:
    if not os.path.exists(_RECORDS_PATH):
        return []
    try:
        with open(_RECORDS_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_records(records: List[dict]) -> None:
    os.makedirs(os.path.dirname(_RECORDS_PATH), exist_ok=True)
    with open(_RECORDS_PATH, "w") as f:
        json.dump(records, f, indent=2, default=str)


def _load_ground_truth() -> Dict[str, List[dict]]:
    """Returns {app_name: [bug_dicts]}."""
    if not os.path.exists(_GROUND_TRUTH_PATH):
        return {}
    try:
        with open(_GROUND_TRUTH_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_ground_truth(gt: Dict[str, List[dict]]) -> None:
    os.makedirs(os.path.dirname(_GROUND_TRUTH_PATH), exist_ok=True)
    with open(_GROUND_TRUTH_PATH, "w") as f:
        json.dump(gt, f, indent=2, default=str)


# ── Metric calculation ──────────────────────────────────────────

def calculate_metrics(
    records: List[dict],
    ground_truth_bugs: List[dict],
) -> Dict[str, Any]:
    """Calculate precision, recall, F1, and aggregate stats for a set of records."""
    if not records:
        return {
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "avg_duration": 0.0,
            "avg_cost": 0.0,
            "consistency": 0.0,
            "run_count": 0,
            "report_quality_distribution": {},
            "evidence_rate": 0.0,
        }

    gt_titles = {b["title"].lower().strip() for b in ground_truth_bugs}

    # Aggregate across all runs for this tool
    total_tp = 0
    total_fp = 0
    total_fn = 0
    durations = []
    costs = []
    consistency_scores = []
    quality_dist: Dict[str, int] = defaultdict(int)
    evidence_count = 0

    for rec in records:
        # Count true positives: bugs found that match ground truth
        tp = 0
        fp = 0
        for bug in rec.get("bugs_found", []):
            bug_title = bug.get("title", "").lower().strip()
            if bug.get("is_true_positive", False) or bug_title in gt_titles:
                tp += 1
            else:
                fp += 1

        # Also count explicit false positives from the record
        fp += rec.get("false_positives", 0)

        # False negatives: ground truth bugs not found
        found_titles = {
            b.get("title", "").lower().strip() for b in rec.get("bugs_found", [])
        }
        fn = len(gt_titles - found_titles) if gt_titles else len(rec.get("bugs_missed", []))

        total_tp += tp
        total_fp += fp
        total_fn += fn

        durations.append(rec.get("duration_seconds", 0.0))
        costs.append(rec.get("token_cost_usd", 0.0))

        cs = rec.get("consistency_score")
        if cs is not None:
            consistency_scores.append(cs)

        quality_dist[rec.get("report_quality", "chat-only")] += 1

        if rec.get("has_evidence", False):
            evidence_count += 1

    n = len(records)
    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    f1 = (
        2 * (precision * recall) / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    # Consistency: average of per-run consistency scores, or std-dev of durations
    if consistency_scores:
        consistency = sum(consistency_scores) / len(consistency_scores)
    elif len(durations) > 1:
        mean_d = sum(durations) / len(durations)
        variance = sum((d - mean_d) ** 2 for d in durations) / len(durations)
        std_dev = math.sqrt(variance)
        # Normalize: lower std_dev relative to mean = higher consistency
        consistency = max(0.0, 1.0 - (std_dev / mean_d)) if mean_d > 0 else 0.0
    else:
        consistency = 1.0  # single run is perfectly consistent with itself

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "avg_duration": round(sum(durations) / n, 2),
        "avg_cost": round(sum(costs) / n, 4),
        "consistency": round(consistency, 4),
        "run_count": n,
        "report_quality_distribution": dict(quality_dist),
        "evidence_rate": round(evidence_count / n, 4),
    }


# ── Endpoints ───────────────────────────────────────────────────

@router.post("/record")
async def record_benchmark(body: ExternalBenchmarkRecord):
    """Record a benchmark run result for any external tool."""
    if body.tool_id not in EXTERNAL_TOOLS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tool_id '{body.tool_id}'. Valid: {list(EXTERNAL_TOOLS.keys())}",
        )

    record_id = f"ext-{uuid.uuid4().hex[:8]}"
    stored = ExternalBenchmarkStored(
        record_id=record_id,
        recorded_at=datetime.now(timezone.utc).isoformat(),
        **body.model_dump(),
    )

    records = _load_records()
    records.append(stored.model_dump())
    _save_records(records)

    # Build a quick metrics summary for the response
    tool_records = [r for r in records if r["tool_id"] == body.tool_id]
    gt = _load_ground_truth()
    gt_bugs = gt.get(body.app_name, [])
    metrics = calculate_metrics(tool_records, gt_bugs)

    logger.info(
        f"[benchmark-external] Recorded {record_id} for {body.tool_id} on {body.app_name}"
    )

    return {
        "record_id": record_id,
        "tool_id": body.tool_id,
        "metrics_summary": {
            "runs": metrics["run_count"],
            "precision": metrics["precision"],
            "recall": metrics["recall"],
            "f1": metrics["f1"],
            "avg_duration": metrics["avg_duration"],
            "avg_cost": metrics["avg_cost"],
        },
    }


@router.get("/compare", response_model=ComparisonResponse)
async def compare_tools(
    app_name: Optional[str] = Query(None, description="Filter by app name"),
):
    """Generate comparison table across all external tools."""
    all_records = _load_records()
    gt_data = _load_ground_truth()

    # Filter by app if requested
    if app_name:
        all_records = [r for r in all_records if r.get("app_name") == app_name]

    # Determine ground truth bugs for the filter context
    if app_name:
        gt_bugs = gt_data.get(app_name, [])
    else:
        # Merge all ground truth bugs across apps (deduplicated by bug_id)
        seen_ids = set()
        gt_bugs = []
        for bugs in gt_data.values():
            for b in bugs:
                bid = b.get("bug_id", b.get("title", ""))
                if bid not in seen_ids:
                    seen_ids.add(bid)
                    gt_bugs.append(b)

    # Group records by tool_id
    by_tool: Dict[str, List[dict]] = defaultdict(list)
    for rec in all_records:
        by_tool[rec["tool_id"]].append(rec)

    # Calculate metrics per tool
    tool_metrics_list: List[ToolMetrics] = []
    ta_metrics: Optional[Dict[str, Any]] = None

    for tool_id, tool_info in EXTERNAL_TOOLS.items():
        recs = by_tool.get(tool_id, [])
        metrics = calculate_metrics(recs, gt_bugs)

        tm = ToolMetrics(
            tool_id=tool_id,
            tool_name=tool_info["name"],
            run_count=metrics["run_count"],
            avg_duration=metrics["avg_duration"],
            avg_cost=metrics["avg_cost"],
            precision=metrics["precision"],
            recall=metrics["recall"],
            f1=metrics["f1"],
            consistency=metrics["consistency"],
            report_quality_distribution=metrics["report_quality_distribution"],
            evidence_rate=metrics["evidence_rate"],
        )
        tool_metrics_list.append(tm)

        if tool_id == "retention":
            ta_metrics = metrics

    # Calculate deltas vs retention baseline
    deltas: Dict[str, Dict[str, float]] = {}
    if ta_metrics and ta_metrics["run_count"] > 0:
        for tm in tool_metrics_list:
            if tm.tool_id == "retention":
                continue
            deltas[tm.tool_id] = {
                "precision_delta": round(tm.precision - ta_metrics["precision"], 4),
                "recall_delta": round(tm.recall - ta_metrics["recall"], 4),
                "f1_delta": round(tm.f1 - ta_metrics["f1"], 4),
                "duration_delta": round(
                    tm.avg_duration - ta_metrics["avg_duration"], 2
                ),
                "cost_delta": round(tm.avg_cost - ta_metrics["avg_cost"], 4),
                "consistency_delta": round(
                    tm.consistency - ta_metrics["consistency"], 4
                ),
            }

    return ComparisonResponse(
        app_filter=app_name,
        tools=tool_metrics_list,
        deltas_vs_retention=deltas,
        ground_truth_count=len(gt_bugs),
    )


@router.get("/records")
async def list_records(
    tool_id: Optional[str] = Query(None, description="Filter by tool ID"),
    app_name: Optional[str] = Query(None, description="Filter by app name"),
):
    """List all recorded benchmark data, optionally filtered."""
    records = _load_records()

    if tool_id:
        records = [r for r in records if r.get("tool_id") == tool_id]
    if app_name:
        records = [r for r in records if r.get("app_name") == app_name]

    return {
        "records": records,
        "total": len(records),
        "available_tools": list(EXTERNAL_TOOLS.keys()),
    }


@router.post("/ground-truth")
async def set_ground_truth(body: GroundTruthRequest):
    """Define ground truth bugs for an app (used to calculate precision/recall)."""
    gt = _load_ground_truth()
    gt[body.app_name] = [b.model_dump() for b in body.known_bugs]
    _save_ground_truth(gt)

    logger.info(
        f"[benchmark-external] Set {len(body.known_bugs)} ground truth bugs "
        f"for '{body.app_name}'"
    )

    return {
        "app_name": body.app_name,
        "bug_count": len(body.known_bugs),
        "bug_ids": [b.bug_id for b in body.known_bugs],
    }
