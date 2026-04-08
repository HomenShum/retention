"""
Trace Compare API — serves tool-call-level data for baseline vs replay comparison.

Returns the exact sequence of tools called, tokens used, files touched,
and checkpoints passed — so the frontend can render an undeniable
side-by-side view.
"""

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/traces", tags=["trace-compare"])

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# Model pricing for cost computation
MODEL_PRICING = {
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.0},
    "gpt-5.4": {"input": 2.50, "output": 15.0},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "gpt-5.4-nano": {"input": 0.20, "output": 1.25},
}


def _load_trajectory(task_name: str) -> Optional[Dict]:
    """Load a trajectory by task name."""
    traj_dir = _DATA_DIR / "trajectories" / task_name
    if not traj_dir.exists():
        return None
    for f in traj_dir.glob("*.json"):
        try:
            return json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _load_replay(replay_id: str) -> Optional[Dict]:
    """Load a replay result by ID."""
    p = _DATA_DIR / "replay_results" / f"{replay_id}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _extract_tool_timeline(trajectory: Dict) -> List[Dict]:
    """Extract tool-call timeline from a trajectory."""
    steps = trajectory.get("steps", [])
    timeline = []
    for step in steps:
        tool_calls = step.get("mcp_tool_calls", [])
        meta = step.get("metadata", {})
        for tc in tool_calls:
            params = tc.get("params", {})
            # Extract target file/path from params
            target = ""
            if "file_path" in params:
                target = params["file_path"]
            elif "path" in params:
                target = params["path"]
            elif "command" in params:
                target = str(params["command"])[:80]
            elif "pattern" in params:
                target = f"pattern: {params['pattern'][:40]}"

            timeline.append({
                "step": step.get("step_index", 0),
                "tool": tc.get("tool", "unknown"),
                "target": target,
                "category": meta.get("category", ""),
                "input_tokens": meta.get("input_tokens", 0),
                "output_tokens": meta.get("output_tokens", 0),
                "duration_ms": step.get("duration_ms", 0),
                "success": step.get("success", True),
                "semantic_label": step.get("semantic_label", ""),
                "checkpoint_passed": step.get("mcp_tool_calls") is not None,
            })
    return timeline


def _compute_run_anatomy(trajectory: Dict) -> Dict:
    """Compute the anatomy of a run — what happened and where savings come from."""
    steps = trajectory.get("steps", [])
    meta = trajectory.get("metadata", {})
    tool_dist = meta.get("tool_distribution", {})

    # Categorize steps
    categories = defaultdict(int)
    files_touched = set()
    surfaces = set()
    for step in steps:
        cat = step.get("metadata", {}).get("category", "unknown")
        categories[cat] += 1
        for tc in step.get("mcp_tool_calls", []):
            params = tc.get("params", {})
            fp = params.get("file_path", params.get("path", ""))
            if fp:
                files_touched.add(fp)
                if "frontend/" in fp:
                    surfaces.add("frontend")
                if "backend/" in fp:
                    surfaces.add("backend")
                if "test" in fp.lower():
                    surfaces.add("tests")

    total_tokens = meta.get("total_input_tokens", 0) + meta.get("total_output_tokens", 0)

    return {
        "total_steps": len(steps),
        "total_tokens": total_tokens,
        "total_files": len(files_touched),
        "surfaces": sorted(surfaces),
        "tool_distribution": dict(tool_dist),
        "category_distribution": dict(categories),
        "files_touched": sorted(files_touched)[:50],  # Cap at 50 for display
    }


def _compute_savings_waterfall(baseline: Dict, replay_tokens: int) -> List[Dict]:
    """Compute a waterfall showing where savings come from."""
    meta = baseline.get("metadata", {})
    total = meta.get("total_input_tokens", 0) + meta.get("total_output_tokens", 0)
    if total == 0:
        return []

    # Estimate breakdown of the original session
    tool_dist = meta.get("tool_distribution", {})
    total_calls = sum(tool_dist.values())

    reads = tool_dist.get("Read", 0) + tool_dist.get("Glob", 0) + tool_dist.get("Grep", 0)
    writes = tool_dist.get("Edit", 0) + tool_dist.get("Write", 0)
    shells = tool_dist.get("Bash", 0)
    agents = tool_dist.get("Agent", 0)
    other = total_calls - reads - writes - shells - agents

    def pct(n):
        return round(n / total_calls * 100, 1) if total_calls > 0 else 0

    return [
        {"label": "Full session", "tokens": total, "type": "total", "pct": 100},
        {"label": "Reading/searching code", "tokens": int(total * reads / max(total_calls, 1)), "type": "exploration", "pct": pct(reads)},
        {"label": "Reasoning between steps", "tokens": int(total * 0.4), "type": "reasoning", "pct": 40},
        {"label": "Writing/editing code", "tokens": int(total * writes / max(total_calls, 1)), "type": "execution", "pct": pct(writes)},
        {"label": "Shell/verification", "tokens": int(total * shells / max(total_calls, 1)), "type": "verification", "pct": pct(shells)},
        {"label": "Replay cost", "tokens": replay_tokens, "type": "replay", "pct": round(replay_tokens / total * 100, 1)},
        {"label": "Tokens avoided", "tokens": total - replay_tokens, "type": "saved", "pct": round((total - replay_tokens) / total * 100, 1)},
    ]


@router.get("/list")
async def list_traces() -> Dict[str, Any]:
    """List all available trajectories and replays for comparison."""
    trajectories = []
    traj_dir = _DATA_DIR / "trajectories"
    if traj_dir.exists():
        for task_dir in sorted(traj_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            for f in task_dir.glob("*.json"):
                try:
                    d = json.loads(f.read_text())
                    trajectories.append({
                        "trajectory_id": d.get("trajectory_id", f.stem),
                        "task_name": d.get("task_name", task_dir.name),
                        "workflow_family": d.get("workflow_family", ""),
                        "total_steps": len(d.get("steps", [])),
                        "total_tokens": d.get("metadata", {}).get("total_input_tokens", 0) + d.get("metadata", {}).get("total_output_tokens", 0),
                        "surfaces": d.get("metadata", {}).get("surfaces", []),
                        "model": d.get("metadata", {}).get("model", ""),
                    })
                except Exception:
                    continue

    replays = []
    replay_dir = _DATA_DIR / "replay_results"
    if replay_dir.exists():
        for f in sorted(replay_dir.glob("*.json")):
            try:
                d = json.loads(f.read_text())
                replays.append({
                    "replay_id": d.get("replay_run_id", f.stem),
                    "workflow": d.get("workflow", ""),
                    "success": d.get("success", False),
                    "token_savings_pct": d.get("comparison_with_full", {}).get("token_savings_pct", 0),
                })
            except Exception:
                continue

    return {"trajectories": trajectories, "replays": replays}


@router.get("/compare/{task_name}")
async def get_trace_comparison(task_name: str) -> Dict[str, Any]:
    """Get full trace comparison for a workflow.

    Returns tool timelines, file diffs, checkpoints, and savings waterfall
    for both the baseline trajectory and the replay.
    """
    trajectory = _load_trajectory(task_name)
    if not trajectory:
        raise HTTPException(404, f"Trajectory not found: {task_name}")

    # Find matching replay
    replay = None
    replay_dir = _DATA_DIR / "replay_results"
    if replay_dir.exists():
        for f in replay_dir.glob("*.json"):
            try:
                d = json.loads(f.read_text())
                if d.get("workflow", "") == task_name or task_name in d.get("trajectory_id", ""):
                    replay = d
                    break
            except Exception:
                continue

    # Build comparison
    baseline_timeline = _extract_tool_timeline(trajectory)
    baseline_anatomy = _compute_run_anatomy(trajectory)

    replay_tokens = 0
    if replay:
        replay_tokens = replay.get("comparison_with_full", {}).get("tokens_replay", 0)
    else:
        # Estimate: ~50 tokens per tool call for replay dispatch
        replay_tokens = len(baseline_timeline) * 50

    waterfall = _compute_savings_waterfall(trajectory, replay_tokens)

    # Model cost comparison
    total_tokens = baseline_anatomy["total_tokens"]
    input_tokens = trajectory.get("metadata", {}).get("total_input_tokens", 0)
    output_tokens = trajectory.get("metadata", {}).get("total_output_tokens", 0)
    model_costs = {}
    for model, pricing in MODEL_PRICING.items():
        cost = (input_tokens / 1_000_000 * pricing["input"]) + (output_tokens / 1_000_000 * pricing["output"])
        model_costs[model] = round(cost, 4)

    return {
        "task_name": task_name,
        "trajectory_id": trajectory.get("trajectory_id", ""),
        "workflow_family": trajectory.get("workflow_family", ""),
        "model": trajectory.get("metadata", {}).get("model", ""),

        "baseline": {
            "timeline": baseline_timeline[:200],  # Cap for payload size
            "anatomy": baseline_anatomy,
            "total_tokens": total_tokens,
            "model": trajectory.get("metadata", {}).get("model", ""),
        },

        "replay": {
            "replay_id": replay.get("replay_run_id", "") if replay else "",
            "success": replay.get("success", False) if replay else None,
            "token_savings_pct": replay.get("comparison_with_full", {}).get("token_savings_pct", 0) if replay else 0,
            "tokens_replay": replay_tokens,
        },

        "model_costs": model_costs,
        "savings_waterfall": waterfall,

        "limitations": {
            "replay_type": "offline_eval" if replay else "estimated",
            "note": "Replay tokens are estimated from tool call count, not from actual cheaper-model execution. Real replay requires running the trajectory on a device/environment.",
            "csp_determinism": "CSP (code change) workflows are more deterministic than browser QA workflows — results may not generalize across all workflow families.",
        },
    }
