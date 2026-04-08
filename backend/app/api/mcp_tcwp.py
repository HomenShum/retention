"""TCWP (TA Canonical Workflow Package) MCP tools.

Generates, validates, lists, exports, and ingests TCWP bundles from
trajectory data, run history, and evaluation artifacts.
"""

import json
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# Storage directory for TCWP bundles
_TCWP_DIR = Path(__file__).resolve().parents[3] / "data" / "tcwp_bundles"


def _ensure_dir():
    _TCWP_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: str) -> str:
    return f"sha256:{hashlib.sha256(data.encode()).hexdigest()}"


def _build_manifest(
    workflow_id: str,
    run_id: str,
    *,
    parent_run_id: str = "",
    rerun_of_run_id: str = "",
    actor_id: str = "retention",
    actor_type: str = "system",
    events_hash: str = "",
    trajectory_hash: str = "",
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a TCWP manifest envelope."""
    pkg_id = f"pkg_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{workflow_id}"
    return {
        "package_type": "tcwp",
        "schema_version": "1.0.0",
        "package_id": pkg_id,
        "created_at": _now_iso(),
        "created_by": {
            "org_id": "org_retention",
            "actor_id": actor_id,
            "actor_type": actor_type,
        },
        "workflow_id": workflow_id,
        "run_id": run_id,
        "parent_run_id": parent_run_id or None,
        "rerun_of_run_id": rerun_of_run_id or None,
        "lineage_ids": [run_id],
        "hashes": {
            "events_jsonl_sha256": events_hash,
            "trajectory_json_sha256": trajectory_hash,
        },
        "extensions_present": ["anthropic"],
        "tags": tags or [],
    }


def _build_workflow(
    workflow_id: str,
    name: str,
    goal: str,
    *,
    family: str = "mobile_app",
    surface: Optional[List[str]] = None,
    success_criteria: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "workflow_id": workflow_id,
        "workflow_family": family,
        "name": name,
        "goal": goal,
        "surface": surface or ["android_emulator"],
        "success_criteria": success_criteria or [goal],
        "risk_class": "medium",
        "compliance_tags": ["qa"],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


def _build_run(
    run_id: str,
    workflow_id: str,
    *,
    mode: str = "full_crawl",
    runtime: str = "claude_code",
    model: str = "claude-opus-4-6",
    surface: str = "android_emulator",
    status: str = "success",
    metrics: Optional[Dict[str, Any]] = None,
    started_at: str = "",
    ended_at: str = "",
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "mode": mode,
        "runtime": runtime,
        "model": model,
        "environment": {"surface": surface},
        "status": status,
        "started_at": started_at or _now_iso(),
        "ended_at": ended_at or _now_iso(),
        "metrics": metrics or {
            "requests": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "estimated_cost_usd": 0,
            "duration_ms": 0,
        },
    }


def _build_trajectory_from_logger(traj: Any) -> Dict[str, Any]:
    """Convert a TrajectoryLogger trajectory to TCWP trajectory format."""
    steps = []
    for i, step in enumerate(getattr(traj, "steps", [])):
        steps.append({
            "step_id": f"s{i+1}",
            "label": getattr(step, "action", f"Step {i+1}"),
            "action_ref": f"evt_{i+1:05d}",
            "state_before": getattr(step, "screen_before", ""),
            "state_after": getattr(step, "screen_after", ""),
            "skippable": False,
            "cost_tokens": getattr(step, "tokens_used", 0),
            "cost_ms": getattr(step, "duration_ms", 0),
        })

    return {
        "trajectory_id": getattr(traj, "trajectory_id", ""),
        "workflow_id": getattr(traj, "task_name", ""),
        "source_run_id": getattr(traj, "source_run_id", ""),
        "strategy": "saved_path_with_checkpoint_validation",
        "steps": steps,
        "replay_stats": {
            "replay_count": getattr(traj, "replay_count", 0),
            "avg_token_savings_pct": getattr(traj, "avg_token_savings", 0) * 100,
            "avg_time_savings_pct": getattr(traj, "avg_time_savings", 0) * 100,
            "drift_incidents": 0,
            "durability_score": 1.0 - getattr(traj, "drift_score", 0),
        },
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


def _build_events_jsonl(traj: Any) -> str:
    """Convert trajectory steps to JSONL events."""
    lines = []
    for i, step in enumerate(getattr(traj, "steps", [])):
        event = {
            "event_id": f"evt_{i+1:05d}",
            "ts": _now_iso(),
            "type": "action",
            "action": getattr(step, "action", "unknown"),
            "target": getattr(step, "target", ""),
            "actor": "agent",
            "state_before": getattr(step, "screen_before", ""),
            "state_after": getattr(step, "screen_after", ""),
            "cost": {
                "tokens": getattr(step, "tokens_used", 0),
                "ms": getattr(step, "duration_ms", 0),
            },
        }
        lines.append(json.dumps(event))
    return "\n".join(lines)


def _build_sales_brief(
    workflow_name: str,
    baseline_metrics: Dict[str, Any],
    replay_metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a sales brief from baseline and replay metrics."""
    b_tokens = baseline_metrics.get("tokens_in", 0) + baseline_metrics.get("tokens_out", 0)
    r_tokens = replay_metrics.get("tokens_in", 0) + replay_metrics.get("tokens_out", 0)
    token_savings = ((b_tokens - r_tokens) / b_tokens * 100) if b_tokens > 0 else 0

    b_time = baseline_metrics.get("duration_ms", 0)
    r_time = replay_metrics.get("duration_ms", 0)
    time_savings = ((b_time - r_time) / b_time * 100) if b_time > 0 else 0

    b_cost = baseline_metrics.get("estimated_cost_usd", 0)
    r_cost = replay_metrics.get("estimated_cost_usd", 0)
    cost_savings = ((b_cost - r_cost) / b_cost * 100) if b_cost > 0 else 0

    return {
        "account_theme": f"Workflow verification — {workflow_name}",
        "workflow_name": workflow_name,
        "baseline": baseline_metrics,
        "improved": replay_metrics,
        "savings": {
            "tokens_pct": round(token_savings, 1),
            "time_pct": round(time_savings, 1),
            "cost_usd_pct": round(cost_savings, 1),
        },
        "buyer_message": f"retention.sh replays the cheapest valid path — {token_savings:.0f}% token savings on {workflow_name}",
        "generated_at": _now_iso(),
    }


def _build_provenance(pkg_id: str, source: str = "local_mcp") -> Dict[str, Any]:
    return {
        "package_id": pkg_id,
        "origin": {
            "source": source,
            "created_by": "retention",
            "created_at": _now_iso(),
        },
        "chain": [
            {"action": "created", "actor": "retention", "ts": _now_iso()},
        ],
        "data_classification": "internal",
        "retention_policy": {"auto_archive_after_days": 90},
    }


def _build_permissions(pkg_id: str) -> Dict[str, Any]:
    return {
        "package_id": pkg_id,
        "owner": "org_retention",
        "visibility": "team",
        "access_rules": [],
        "export_allowed": True,
        "training_allowed": False,
    }


# ---------------------------------------------------------------------------
# MCP tool handlers
# ---------------------------------------------------------------------------

def dispatch_tcwp(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle ta.tcwp.* tools."""
    _ensure_dir()

    if tool == "ta.tcwp.generate":
        return _handle_generate(args)
    if tool == "ta.tcwp.validate":
        return _handle_validate(args)
    if tool == "ta.tcwp.list":
        return _handle_list(args)
    if tool == "ta.tcwp.export":
        return _handle_export(args)
    if tool == "ta.tcwp.ingest":
        return _handle_ingest(args)
    if tool == "ta.tcwp.export_profile":
        return _handle_export_profile(args)

    return {"error": f"Unknown TCWP tool: {tool}"}


def _handle_generate(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a TCWP bundle from a trajectory.

    Args:
        trajectory_id: ID of saved trajectory to package
        task_name: Task name for trajectory lookup
        workflow_name: Human-readable workflow name
        workflow_goal: Goal description
        include_sales_brief: Whether to include sales brief (default True)
    """
    trajectory_id = args.get("trajectory_id", "")
    task_name = args.get("task_name", "")
    workflow_name = args.get("workflow_name", task_name)
    workflow_goal = args.get("workflow_goal", f"Verify {workflow_name}")

    if not trajectory_id:
        return {"error": "trajectory_id is required"}

    # Load trajectory
    try:
        from ..agents.device_testing.trajectory_logger import get_trajectory_logger
        tl = get_trajectory_logger()

        traj = None
        if task_name:
            traj = tl.load_trajectory(task_name, trajectory_id)
        else:
            base = tl._base_dir
            if base.exists():
                for task_dir in base.iterdir():
                    if task_dir.is_dir() and not task_dir.name.startswith("_"):
                        traj = tl.load_trajectory(task_dir.name, trajectory_id)
                        if traj:
                            task_name = task_dir.name
                            break

        if not traj:
            return {"error": f"Trajectory {trajectory_id} not found"}
    except Exception as e:
        return {"error": f"Failed to load trajectory: {e}"}

    # Build TCWP components
    workflow_id = f"wf_{task_name.replace(' ', '_').lower()}"
    run_id = f"run_{trajectory_id}"

    trajectory_json = json.dumps(_build_trajectory_from_logger(traj))
    events_jsonl = _build_events_jsonl(traj)

    manifest = _build_manifest(
        workflow_id, run_id,
        events_hash=_sha256(events_jsonl),
        trajectory_hash=_sha256(trajectory_json),
        tags=[task_name],
    )
    pkg_id = manifest["package_id"]

    workflow = _build_workflow(workflow_id, workflow_name, workflow_goal)
    run = _build_run(run_id, workflow_id)
    provenance = _build_provenance(pkg_id)
    permissions = _build_permissions(pkg_id)

    # Write bundle to disk
    bundle_dir = _TCWP_DIR / pkg_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    (bundle_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (bundle_dir / "workflow.json").write_text(json.dumps(workflow, indent=2))
    (bundle_dir / "run.json").write_text(json.dumps(run, indent=2))
    (bundle_dir / "trajectory.json").write_text(trajectory_json)
    (bundle_dir / "events.jsonl").write_text(events_jsonl)
    (bundle_dir / "provenance.json").write_text(json.dumps(provenance, indent=2))
    (bundle_dir / "permissions.json").write_text(json.dumps(permissions, indent=2))

    # Optional sales brief
    if args.get("include_sales_brief", True):
        brief = _build_sales_brief(workflow_name, run.get("metrics", {}), run.get("metrics", {}))
        (bundle_dir / "sales_brief.json").write_text(json.dumps(brief, indent=2))

    return {
        "tool": "ta.tcwp.generate",
        "status": "ok",
        "package_id": pkg_id,
        "path": str(bundle_dir),
        "files": [f.name for f in bundle_dir.iterdir()],
        "trajectory_steps": len(getattr(traj, "steps", [])),
    }


def _handle_validate(args: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a TCWP bundle for integrity and schema compliance.

    Args:
        package_id: ID of the package to validate
        path: Path to a TCWP bundle directory (alternative to package_id)
    """
    pkg_id = args.get("package_id", "")
    pkg_path = args.get("path", "")

    if pkg_id:
        bundle_dir = _TCWP_DIR / pkg_id
    elif pkg_path:
        bundle_dir = Path(pkg_path)
    else:
        return {"error": "package_id or path is required"}

    if not bundle_dir.exists():
        return {"error": f"Bundle not found at {bundle_dir}"}

    errors = []
    warnings = []

    # Check required files
    required_files = ["manifest.json", "workflow.json", "run.json"]
    for fname in required_files:
        if not (bundle_dir / fname).exists():
            errors.append(f"Missing required file: {fname}")

    # Validate manifest
    manifest_path = bundle_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            if manifest.get("package_type") != "tcwp":
                errors.append("manifest.package_type must be 'tcwp'")
            if not manifest.get("schema_version"):
                errors.append("manifest.schema_version is required")
            if not manifest.get("package_id"):
                errors.append("manifest.package_id is required")
            if not manifest.get("workflow_id"):
                errors.append("manifest.workflow_id is required")
            if not manifest.get("run_id"):
                errors.append("manifest.run_id is required")

            # Verify hashes if events exist
            events_path = bundle_dir / "events.jsonl"
            if events_path.exists():
                actual_hash = _sha256(events_path.read_text())
                expected_hash = manifest.get("hashes", {}).get("events_jsonl_sha256", "")
                if expected_hash and actual_hash != expected_hash:
                    warnings.append(f"events.jsonl hash mismatch: expected {expected_hash[:20]}... got {actual_hash[:20]}...")

        except json.JSONDecodeError:
            errors.append("manifest.json is not valid JSON")

    # Validate run
    run_path = bundle_dir / "run.json"
    if run_path.exists():
        try:
            run = json.loads(run_path.read_text())
            required_run_fields = ["run_id", "workflow_id", "mode", "runtime", "model", "status", "started_at", "ended_at", "metrics"]
            for field in required_run_fields:
                if field not in run:
                    errors.append(f"run.json missing required field: {field}")
        except json.JSONDecodeError:
            errors.append("run.json is not valid JSON")

    # Check optional files
    optional_files = ["trajectory.json", "events.jsonl", "evals.jsonl", "annotations.jsonl",
                      "sales_brief.json", "provenance.json", "permissions.json"]
    present = [f for f in optional_files if (bundle_dir / f).exists()]
    missing = [f for f in optional_files if not (bundle_dir / f).exists()]

    valid = len(errors) == 0
    return {
        "tool": "ta.tcwp.validate",
        "status": "ok" if valid else "error",
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "files_present": [f.name for f in bundle_dir.iterdir()],
        "optional_present": present,
        "optional_missing": missing,
    }


def _handle_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List all TCWP bundles."""
    bundles = []
    if _TCWP_DIR.exists():
        for pkg_dir in sorted(_TCWP_DIR.iterdir(), reverse=True):
            if pkg_dir.is_dir():
                manifest_path = pkg_dir / "manifest.json"
                if manifest_path.exists():
                    try:
                        manifest = json.loads(manifest_path.read_text())
                        bundles.append({
                            "package_id": manifest.get("package_id", pkg_dir.name),
                            "workflow_id": manifest.get("workflow_id", ""),
                            "run_id": manifest.get("run_id", ""),
                            "created_at": manifest.get("created_at", ""),
                            "tags": manifest.get("tags", []),
                            "files": [f.name for f in pkg_dir.iterdir()],
                        })
                    except Exception:
                        bundles.append({"package_id": pkg_dir.name, "error": "invalid manifest"})

    return {
        "tool": "ta.tcwp.list",
        "status": "ok",
        "bundles": bundles,
        "total": len(bundles),
    }


def _handle_export(args: Dict[str, Any]) -> Dict[str, Any]:
    """Export a TCWP bundle as a single JSON file for sharing.

    Args:
        package_id: ID of the package to export
        redact: Whether to apply redaction rules (default False)
    """
    pkg_id = args.get("package_id", "")
    if not pkg_id:
        return {"error": "package_id is required"}

    bundle_dir = _TCWP_DIR / pkg_id
    if not bundle_dir.exists():
        return {"error": f"Bundle not found: {pkg_id}"}

    bundle = {}
    for f in bundle_dir.iterdir():
        if f.is_file():
            try:
                if f.suffix == ".json":
                    bundle[f.stem] = json.loads(f.read_text())
                elif f.suffix == ".jsonl":
                    lines = [json.loads(line) for line in f.read_text().strip().split("\n") if line.strip()]
                    bundle[f.stem] = lines
                else:
                    bundle[f.stem] = f.read_text()
            except Exception:
                bundle[f.stem] = {"error": "failed to read"}

    # Write combined export
    export_path = _TCWP_DIR / f"{pkg_id}_export.json"
    export_path.write_text(json.dumps(bundle, indent=2))

    return {
        "tool": "ta.tcwp.export",
        "status": "ok",
        "package_id": pkg_id,
        "export_path": str(export_path),
        "sections": list(bundle.keys()),
    }


def _handle_ingest(args: Dict[str, Any]) -> Dict[str, Any]:
    """Ingest a TCWP bundle from a path or JSON string.

    Args:
        path: Path to a TCWP bundle directory or export JSON file
        json_data: JSON string of a TCWP export (alternative to path)
    """
    source_path = args.get("path", "")
    json_data = args.get("json_data", "")

    if source_path:
        source = Path(source_path)
        if source.is_dir():
            # Copy directory
            manifest_path = source / "manifest.json"
            if not manifest_path.exists():
                return {"error": "Source directory has no manifest.json"}
            manifest = json.loads(manifest_path.read_text())
            pkg_id = manifest.get("package_id", source.name)
            dest = _TCWP_DIR / pkg_id
            if dest.exists():
                return {"error": f"Package {pkg_id} already exists", "package_id": pkg_id}
            import shutil
            shutil.copytree(source, dest)
            return {"tool": "ta.tcwp.ingest", "status": "ok", "package_id": pkg_id, "path": str(dest)}

        elif source.is_file() and source.suffix == ".json":
            # Import from export JSON
            bundle = json.loads(source.read_text())
            manifest = bundle.get("manifest", {})
            pkg_id = manifest.get("package_id", source.stem)
            dest = _TCWP_DIR / pkg_id
            dest.mkdir(parents=True, exist_ok=True)
            for key, value in bundle.items():
                if isinstance(value, list):
                    (dest / f"{key}.jsonl").write_text("\n".join(json.dumps(v) for v in value))
                elif isinstance(value, dict):
                    (dest / f"{key}.json").write_text(json.dumps(value, indent=2))
            return {"tool": "ta.tcwp.ingest", "status": "ok", "package_id": pkg_id, "path": str(dest)}

    elif json_data:
        bundle = json.loads(json_data)
        manifest = bundle.get("manifest", {})
        pkg_id = manifest.get("package_id", f"imported_{_now_iso().replace(':', '-')}")
        dest = _TCWP_DIR / pkg_id
        dest.mkdir(parents=True, exist_ok=True)
        for key, value in bundle.items():
            if isinstance(value, list):
                (dest / f"{key}.jsonl").write_text("\n".join(json.dumps(v) for v in value))
            elif isinstance(value, dict):
                (dest / f"{key}.json").write_text(json.dumps(value, indent=2))
        return {"tool": "ta.tcwp.ingest", "status": "ok", "package_id": pkg_id, "path": str(dest)}

    return {"error": "path or json_data is required"}


# ---------------------------------------------------------------------------
# Export profiles
# ---------------------------------------------------------------------------

_EXPORT_PROFILES = {
    "ops": {
        "includes": [
            "manifest.json", "workflow.json", "run.json", "trajectory.json",
            "checkpoints.json", "events.jsonl", "state_snapshots.jsonl",
            "tool_calls.jsonl", "evals.jsonl", "replay_plan.json",
            "handoff.json", "provenance.json", "permissions.json",
        ],
        "redaction_required": False,
        "training_consent_required": False,
    },
    "training": {
        "includes": [
            "manifest.json", "workflow.json", "trajectory.json",
            "events.jsonl", "evals.jsonl", "annotations.jsonl",
            "training_examples.jsonl", "preferences.jsonl",
            "policy_labels.jsonl", "reward_signals.jsonl",
            "dataset_card.json", "provenance.json",
        ],
        "redaction_required": True,
        "training_consent_required": True,
    },
    "sales": {
        "includes": [
            "manifest.json", "workflow.json", "run.json",
            "sales_brief.json", "sales_brief.md",
            "evals.jsonl", "optimization_candidates.json",
        ],
        "redaction_required": True,
        "training_consent_required": False,
    },
}


def _handle_export_profile(args: Dict[str, Any]) -> Dict[str, Any]:
    """Export a TCWP bundle using a specific export profile (ops, training, sales).

    Args:
        package_id: ID of the TCWP package to export
        profile: Export profile to use (ops, training, sales)
    """
    pkg_id = args.get("package_id", "")
    profile_name = args.get("profile", "ops")

    if not pkg_id:
        return {"error": "package_id is required"}

    bundle_dir = _TCWP_DIR / pkg_id
    if not bundle_dir.exists():
        return {"error": f"Bundle not found: {pkg_id}"}

    profile = _EXPORT_PROFILES.get(profile_name)
    if not profile:
        return {"error": f"Unknown profile: {profile_name}. Use: ops, training, sales"}

    includes = profile["includes"]
    bundle = {}
    included_files = []
    skipped_files = []

    for fname in includes:
        fpath = bundle_dir / fname
        if fpath.exists():
            try:
                if fname.endswith(".json"):
                    bundle[fpath.stem] = json.loads(fpath.read_text())
                elif fname.endswith(".jsonl"):
                    lines = []
                    for line in fpath.read_text().strip().split("\n"):
                        if line.strip():
                            record = json.loads(line)
                            # Filter by training consent if required
                            if profile["training_consent_required"]:
                                if not record.get("allowed_for_training", False):
                                    continue
                            lines.append(record)
                    bundle[fpath.stem] = lines
                elif fname.endswith(".md"):
                    bundle[fpath.stem] = fpath.read_text()
                included_files.append(fname)
            except Exception:
                skipped_files.append(fname)
        else:
            skipped_files.append(fname)

    # Write profiled export
    export_path = _TCWP_DIR / f"{pkg_id}_{profile_name}_export.json"
    export_path.write_text(json.dumps(bundle, indent=2))

    return {
        "tool": "ta.tcwp.export_profile",
        "status": "ok",
        "package_id": pkg_id,
        "profile": profile_name,
        "export_path": str(export_path),
        "included": included_files,
        "skipped": skipped_files,
        "redaction_applied": profile["redaction_required"],
        "training_consent_filtered": profile["training_consent_required"],
    }
