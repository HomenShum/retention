"""
Trajectory API — serve real trajectory data for the compliance trace UI.

GET /api/trajectories               — list all available trajectories
GET /api/trajectories/{traj_id}     — get full trajectory with normalized events
GET /api/trajectories/{traj_id}/judge — run judge against trajectory events
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/trace", tags=["trace-ui"])

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "trajectories"


# ── Helpers ──────────────────────────────────────────────────

def _classify_category(action: str, semantic_label: str) -> str:
    """Map action/semantic_label to UI category."""
    label = semantic_label.lower()
    act = action.lower()

    if "read" in act or "glob" in act or "grep" in act or "codebase_read" in label:
        return "read"
    if "edit" in act or "write" in act or "file_write" in label:
        return "write"
    if "websearch" in act or "web_search" in label:
        return "search"
    if "webfetch" in act or "web_fetch" in label:
        return "search"
    if "preview" in act or "screenshot" in act:
        return "preview"
    if "bash" in act or "shell" in label:
        # Detect test commands
        if any(kw in act for kw in ["test", "pytest", "jest", "typecheck", "lint", "check"]):
            return "test"
        return "other"
    if "agent" in act:
        return "other"
    return "other"


def _summarize_action(action: str, mcp_tool_calls: List[Dict]) -> str:
    """Create a human-readable summary of the step."""
    # Try to extract from mcp_tool_calls params
    if mcp_tool_calls:
        params = mcp_tool_calls[0].get("params", {})
        tool = mcp_tool_calls[0].get("tool", action)

        if "file_path" in params:
            verb = "Edit" if "edit" in action.lower() or "write" in action.lower() else "Read"
            return f"{verb} {params['file_path']}"
        if "pattern" in params and "path" in params:
            return f"Search for '{params['pattern']}' in {params['path']}"
        if "pattern" in params:
            return f"Search for '{params['pattern']}'"
        if "query" in params:
            return f"Search: {str(params['query'])[:80]}"
        if "url" in params:
            return f"Fetch {str(params['url'])[:80]}"
        if "command" in params:
            cmd = str(params["command"])[:80]
            return f"$ {cmd}"
        if "prompt" in params:
            return f"Agent: {str(params['prompt'])[:60]}"
        if "description" in params:
            return f"Agent: {str(params['description'])[:60]}"
        if "keywords" in params:
            return f"Search MCP: {params['keywords']}"

    # Fallback: clean up the action string
    clean = action
    # Remove long bash commands, keep first part
    if clean.startswith("Bash("):
        cmd = clean[5:].rstrip(")")
        return f"$ {cmd[:80]}"
    return clean[:100]


def _extract_files(action: str, mcp_tool_calls: List[Dict]) -> List[str]:
    """Extract file paths from step data."""
    files = []
    for call in mcp_tool_calls:
        params = call.get("params", {})
        if "file_path" in params:
            files.append(str(params["file_path"]))
        if "path" in params and "/" in str(params.get("path", "")):
            files.append(str(params["path"]))
    return files


def _extract_diff_context(action: str, mcp_tool_calls: List[Dict]) -> Optional[Dict[str, Any]]:
    """Extract code diff context from write/edit steps."""
    for call in mcp_tool_calls:
        params = call.get("params", {})
        tool = call.get("tool", "")

        if "edit" in tool.lower() or "Edit" in action:
            return {
                "type": "edit",
                "file_path": params.get("file_path", ""),
                "old_string": str(params.get("old_string", ""))[:200],
                "new_string": str(params.get("new_string", ""))[:200],
            }
        if "write" in tool.lower() or "Write" in action:
            content = str(params.get("content", ""))
            return {
                "type": "write",
                "file_path": params.get("file_path", ""),
                "content_preview": content[:300],
                "content_lines": content.count("\n") + 1,
            }
    return None


def _normalize_step(step: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a raw trajectory step into the format the frontend expects."""
    action = step.get("action", "")
    semantic_label = step.get("semantic_label", "")
    mcp_calls = step.get("mcp_tool_calls", [])
    metadata = step.get("metadata", {})

    # Extract the clean tool name
    tool_name = action
    if mcp_calls:
        tool_name = mcp_calls[0].get("tool", action)
    # Clean up: "Bash(some command)" → "Bash"
    if "(" in tool_name:
        tool_name = tool_name.split("(")[0]
    # Clean up MCP prefixes: "mcp__Claude_Preview__preview_screenshot" → "preview_screenshot"
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        tool_name = parts[-1] if len(parts) > 2 else tool_name

    tokens = (
        metadata.get("input_tokens", 0)
        + metadata.get("output_tokens", 0)
        + metadata.get("cache_read_tokens", 0)
    )

    diff_context = _extract_diff_context(action, mcp_calls)

    return {
        "step_index": step.get("step_index", 0),
        "timestamp": step.get("timestamp", ""),
        "tool": tool_name,
        "action_summary": _summarize_action(action, mcp_calls),
        "files_touched": _extract_files(action, mcp_calls),
        "tokens": tokens,
        "duration_ms": step.get("duration_ms", 0),
        "category": _classify_category(action, semantic_label),
        "success": step.get("success", True),
        "semantic_label": semantic_label,
        "screen_fingerprint": step.get("screen_fingerprint_after", ""),
        "diff_context": diff_context,
        "maps_to_step": None,  # Filled by judge
    }


# ── Endpoints ────────────────────────────────────────────────

@router.get("")
async def list_trajectories() -> Dict[str, Any]:
    """List all available trajectory families and files."""
    if not DATA_DIR.exists():
        return {"trajectories": [], "count": 0}

    result = []
    for family_dir in sorted(DATA_DIR.iterdir()):
        if not family_dir.is_dir():
            continue
        files = []
        for f in sorted(family_dir.glob("*.json")):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                files.append({
                    "id": f.stem,
                    "task_name": data.get("task_name", family_dir.name),
                    "task_goal": data.get("task_goal", ""),
                    "steps": len(data.get("steps", [])),
                    "started_at": data.get("started_at", ""),
                    "completed_at": data.get("completed_at", ""),
                    "workflow_family": data.get("workflow_family", ""),
                    "surface": data.get("surface", ""),
                    "avg_token_savings": data.get("avg_token_savings", 0),
                })
            except Exception:
                continue
        if files:
            result.append({
                "family": family_dir.name,
                "trajectories": files,
                "count": len(files),
            })

    return {"families": result, "total": sum(f["count"] for f in result)}


@router.get("/{traj_id}")
async def get_trajectory(traj_id: str, max_steps: int = Query(200, ge=1, le=5000)) -> Dict[str, Any]:
    """Get a full trajectory with normalized events for the trace UI.

    Returns the trajectory metadata plus normalized step events
    in the format the ComplianceDashboardPage expects.
    """
    # Find the file across all family directories
    traj_path = _find_trajectory(traj_id)
    if not traj_path:
        raise HTTPException(404, f"Trajectory {traj_id} not found")

    with open(traj_path) as f:
        raw = json.load(f)

    steps = raw.get("steps", [])[:max_steps]
    events = [_normalize_step(s) for s in steps]

    # Compute summary stats
    total_tokens = sum(e["tokens"] for e in events)
    total_time_ms = sum(e["duration_ms"] for e in events)
    categories = {}
    for e in events:
        categories[e["category"]] = categories.get(e["category"], 0) + 1

    return {
        "trajectory_id": raw.get("trajectory_id", traj_id),
        "task_name": raw.get("task_name", ""),
        "task_goal": raw.get("task_goal", ""),
        "device_id": raw.get("device_id", ""),
        "started_at": raw.get("started_at", ""),
        "completed_at": raw.get("completed_at", ""),
        "workflow_family": raw.get("workflow_family", ""),
        "surface": raw.get("surface", ""),
        "success": raw.get("success", True),
        "total_steps": len(steps),
        "total_tokens": total_tokens,
        "total_time_ms": total_time_ms,
        "avg_token_savings": raw.get("avg_token_savings", 0),
        "categories": categories,
        "events": events,
        # Git context
        "git_commit": raw.get("source_git_commit", ""),
        "git_branch": raw.get("source_git_branch", ""),
    }


@router.get("/{traj_id}/judge")
async def judge_trajectory(traj_id: str, workflow_id: str = Query("dev.flywheel.v3")) -> Dict[str, Any]:
    """Run the workflow judge against a trajectory's events.

    Maps each event to workflow steps and returns the verdict.
    """
    import copy
    from ..services.workflow_judge.models import seed_builtin_workflows
    from ..services.workflow_judge.judge import score_step, evaluate_hard_gates, decide_verdict

    traj_path = _find_trajectory(traj_id)
    if not traj_path:
        raise HTTPException(404, f"Trajectory {traj_id} not found")

    with open(traj_path) as f:
        raw = json.load(f)

    # Get the workflow (deep copy to avoid mutating shared state)
    _wfs = {wf.workflow_id: wf for wf in seed_builtin_workflows()}
    workflow = _wfs.get(workflow_id)
    if not workflow:
        raise HTTPException(400, f"Unknown workflow: {workflow_id}")
    workflow = copy.deepcopy(workflow)

    # Collect tool calls in the format the judge expects
    steps = raw.get("steps", [])
    tool_calls = []
    for s in steps:
        action = s.get("action", "")
        tool_name = action.split("(")[0] if "(" in action else action
        if s.get("mcp_tool_calls"):
            tool_name = s["mcp_tool_calls"][0].get("tool", tool_name)
        tool_input = s.get("mcp_tool_calls", [{}])[0].get("params", {}) if s.get("mcp_tool_calls") else {}
        tool_calls.append({
            "tool": tool_name,
            "input": tool_input,
            "params": tool_input,
            "timestamp": s.get("timestamp", ""),
        })

    # Score each step (score_step calls collect_evidence internally)
    scored_steps = []
    for ws in workflow.required_steps:
        scored = score_step(ws, tool_calls)
        scored_steps.append(scored)

    # Evaluate hard gates and verdict
    hard_gates = evaluate_hard_gates(workflow, scored_steps, tool_calls)
    verdict_class, nudge_level, summary = decide_verdict(scored_steps, hard_gates)

    # Build response
    step_details = []
    for ws in scored_steps:
        step_details.append({
            "step_id": ws.step_id,
            "name": ws.name,
            "status": ws.status.value if hasattr(ws.status, 'value') else str(ws.status),
            "confidence": ws.confidence,
            "evidence_count": len(ws.evidence) if ws.evidence else 0,
            "notes": ws.notes or "",
        })

    done = sum(1 for s in step_details if s["status"] == "done")
    partial = sum(1 for s in step_details if s["status"] == "partial")
    missing = sum(1 for s in step_details if s["status"] == "missing")
    total = len(step_details)
    missing_names = [s["name"] for s in step_details if s["status"] == "missing"]

    return {
        "workflow_id": workflow_id,
        "workflow_name": workflow.name,
        "verdict": verdict_class,
        "nudge_level": nudge_level,
        "steps_done": done,
        "steps_partial": partial,
        "steps_missing": missing,
        "total_steps": total,
        "missing_steps": missing_names,
        "summary": summary,
        "nudge_message": f"Cannot mark complete — {missing} mandatory steps have no evidence." if missing > 0 else "",
        "tool_calls_analyzed": len(tool_calls),
        "step_details": step_details,
        "hard_gates": hard_gates,
    }


@router.post("/{traj_id}/share")
async def share_trajectory(traj_id: str, team_id: str = "", member_email: str = "") -> Dict[str, Any]:
    """Share a trajectory with a team by syncing it to Convex."""
    traj_path = _find_trajectory(traj_id)
    if not traj_path:
        raise HTTPException(404, f"Trajectory {traj_id} not found")

    with open(traj_path) as f:
        raw = json.load(f)

    # Try to sync to Convex
    try:
        from ..services.convex_client import get_convex_client
        client = get_convex_client()
        if client:
            await client.sync_trajectory(raw, team_id=team_id, member_email=member_email)
            return {
                "shared": True,
                "trajectory_id": traj_id,
                "team_id": team_id,
                "url": f"https://test-studio-xi.vercel.app/run-inspector?traj={traj_id}",
            }
    except Exception as e:
        return {"shared": False, "error": str(e), "trajectory_id": traj_id}

    return {"shared": False, "error": "Convex client not available", "trajectory_id": traj_id}


def _find_trajectory(traj_id: str) -> Optional[Path]:
    """Find a trajectory file by ID across all family directories."""
    if not DATA_DIR.exists():
        return None

    # Check with and without traj_ prefix
    candidates = [traj_id, f"traj_{traj_id}"]

    for family_dir in DATA_DIR.iterdir():
        if not family_dir.is_dir():
            continue
        for candidate in candidates:
            path = family_dir / f"{candidate}.json"
            if path.exists():
                return path
            # Also try without extension in case they passed it
            if candidate.endswith(".json"):
                path = family_dir / candidate
                if path.exists():
                    return path

    return None
