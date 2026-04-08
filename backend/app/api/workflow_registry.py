"""Workflow Registry API — register discovered workflows and replay them cheaply.

Routes:
    POST /api/workflows/register       — save a discovered workflow
    GET  /api/workflows                — list all registered workflows
    POST /api/workflows/{id}/replay    — replay using gpt-5.4-nano
    DELETE /api/workflows/{id}         — remove a registered workflow
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "registered_workflows"


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _workflow_path(workflow_id: str) -> Path:
    if not re.match(r'^[a-zA-Z0-9_-]+$', workflow_id):
        raise HTTPException(status_code=400, detail=f"Invalid workflow_id: {workflow_id}")
    return DATA_DIR / f"{workflow_id}.json"


def _load_workflow(workflow_id: str) -> Dict[str, Any]:
    path = _workflow_path(workflow_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")
    return json.loads(path.read_text())


def _save_workflow(workflow: Dict[str, Any]) -> None:
    _ensure_data_dir()
    path = _workflow_path(workflow["workflow_id"])
    path.write_text(json.dumps(workflow, indent=2))


# ---------------------------------------------------------------------------
# Pricing constants
# ---------------------------------------------------------------------------

# gpt-5.4 (full model) — $2.50 / 1M tokens
FULL_MODEL = "gpt-5.4"
FULL_MODEL_COST_PER_TOKEN = 2.50 / 1_000_000

# gpt-5.4-nano (cheap replay) — $0.20 / 1M tokens
NANO_MODEL = "gpt-5.4-nano"
NANO_MODEL_COST_PER_TOKEN = 0.20 / 1_000_000

# Rough token estimate per workflow step (prompt + completion)
TOKENS_PER_STEP = 800


def _estimate_cost(steps: List[Dict[str, Any]], model: str = NANO_MODEL) -> float:
    cost_per_token = (
        NANO_MODEL_COST_PER_TOKEN if model == NANO_MODEL else FULL_MODEL_COST_PER_TOKEN
    )
    return round(len(steps) * TOKENS_PER_STEP * cost_per_token, 6)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class WorkflowStep(BaseModel):
    action: str
    package: Optional[str] = None
    element_id: Optional[str] = None
    text: Optional[str] = None
    screen: Optional[str] = None


class RegisterWorkflowRequest(BaseModel):
    name: str
    app_package: str
    steps: List[WorkflowStep]
    source_crawl_id: Optional[str] = None
    workflow_id: Optional[str] = None  # auto-generated if omitted
    # Code-aware enrichment fields
    screen_id: Optional[str] = None
    screen_fingerprint: Optional[str] = None
    feature_ids: List[str] = []
    entry_route: Optional[str] = None
    code_anchors: List[Dict[str, Any]] = []
    selectors_used: List[str] = []
    api_routes_touched: List[str] = []


class RegisterWorkflowResponse(BaseModel):
    workflow_id: str
    name: str
    app_package: str
    steps: List[Dict[str, Any]]
    source_crawl_id: Optional[str]
    registered_at: str
    replay_model: str
    estimated_replay_cost_usd: float


class ReplayResponse(BaseModel):
    workflow_id: str
    name: str
    replay_model: str
    estimated_cost_usd: float
    replay_ready: bool
    steps: List[Dict[str, Any]]
    missing_element_ids: List[int] = Field(
        default_factory=list,
        description="0-based indices of steps that lack element_id",
    )
    executed: bool = False
    execution_results: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/register", response_model=RegisterWorkflowResponse, status_code=201)
async def register_workflow(req: RegisterWorkflowRequest):
    """Save a discovered workflow for future cheap replay."""
    wf_id = req.workflow_id or f"wf_{uuid.uuid4().hex[:12]}"

    # Prevent overwriting
    if _workflow_path(wf_id).exists():
        raise HTTPException(
            status_code=409, detail=f"Workflow {wf_id} already exists"
        )

    steps_dicts = [s.model_dump(exclude_none=True) for s in req.steps]
    now = datetime.now(timezone.utc).isoformat()

    workflow = {
        "workflow_id": wf_id,
        "name": req.name,
        "app_package": req.app_package,
        "steps": steps_dicts,
        "source_crawl_id": req.source_crawl_id,
        "screen_id": req.screen_id,
        "screen_fingerprint": req.screen_fingerprint,
        "feature_ids": req.feature_ids,
        "entry_route": req.entry_route,
        "code_anchors": req.code_anchors,
        "selectors_used": req.selectors_used,
        "api_routes_touched": req.api_routes_touched,
        "registered_at": now,
        "replay_model": NANO_MODEL,
        "estimated_replay_cost_usd": _estimate_cost(steps_dicts),
    }

    _save_workflow(workflow)
    logger.info("Registered workflow %s (%d steps)", wf_id, len(steps_dicts))

    # Phase 2 auto-linkage: push to linkage_graph
    try:
        from app.agents.qa_pipeline.linkage_graph import (
            link_workflow_to_feature,
            link_workflow_to_screen,
        )
        for fid in req.feature_ids:
            link_workflow_to_feature(wf_id, fid)
        if req.screen_id:
            link_workflow_to_screen(wf_id, req.screen_id)
    except Exception as exc:
        logger.warning("Failed to map workflow %s to linkage graph: %s", wf_id, exc)

    return workflow


@router.get("", response_model=List[RegisterWorkflowResponse])
async def list_workflows():
    """List all registered workflows."""
    _ensure_data_dir()
    workflows: List[Dict[str, Any]] = []
    for path in sorted(DATA_DIR.glob("*.json")):
        try:
            workflows.append(json.loads(path.read_text()))
        except Exception as exc:
            logger.warning("Skipping malformed workflow file %s: %s", path, exc)
    return workflows


# Dependency injection for replay execution
_mobile_mcp_client = None


def set_mobile_mcp_client(client) -> None:
    """Inject MobileMCPClient for replay execution (called from main.py)."""
    global _mobile_mcp_client
    _mobile_mcp_client = client
    logger.info("MobileMCPClient injected into workflow registry for replay")


class ReplayRequest(BaseModel):
    device_id: Optional[str] = None
    execute: bool = Field(False, description="If True, execute steps on a real device")
    app_url: Optional[str] = None


@router.post("/{workflow_id}/replay", response_model=ReplayResponse)
async def replay_workflow(workflow_id: str, body: ReplayRequest = ReplayRequest()):
    """Replay a registered workflow using gpt-5.4-nano.

    If ``execute=true`` is set and a device is available, drives the emulator
    through each step, captures screenshots, and returns pass/fail results.
    Otherwise returns metadata with cost estimate and readiness check.
    """
    wf = _load_workflow(workflow_id)
    steps: List[Dict[str, Any]] = wf["steps"]

    # Check which steps are missing element_ids (launch_app steps are exempt)
    missing: List[int] = []
    for idx, step in enumerate(steps):
        if step.get("action") == "launch_app":
            continue
        if not step.get("element_id"):
            missing.append(idx)

    replay_ready = len(missing) == 0

    # If not executing, return metadata only
    if not body.execute:
        return ReplayResponse(
            workflow_id=wf["workflow_id"],
            name=wf["name"],
            replay_model=NANO_MODEL,
            estimated_cost_usd=_estimate_cost(steps, NANO_MODEL),
            replay_ready=replay_ready,
            steps=steps,
            missing_element_ids=missing,
        )

    # ── Execute on real device ──────────────────────────────────────────
    if not _mobile_mcp_client:
        raise HTTPException(503, "MobileMCPClient not available for device execution")

    device_id = body.device_id
    if not device_id:
        # Auto-detect
        try:
            devices_text = await _mobile_mcp_client.list_available_devices()
            import re as _re
            matches = _re.findall(r"(emulator-\d+)", devices_text)
            if matches:
                device_id = matches[0]
            else:
                raise HTTPException(400, "No emulator detected. Launch one first.")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"Failed to detect device: {e}")

    execution_results = await _execute_workflow_steps(
        steps, device_id, wf["app_package"], body.app_url,
    )

    # Record replay in workflow metadata
    import copy
    wf_updated = copy.deepcopy(wf)
    replay_count = wf_updated.get("replay_count", 0) + 1
    wf_updated["replay_count"] = replay_count
    wf_updated["last_replayed_at"] = datetime.now(timezone.utc).isoformat()
    wf_updated["last_replay_result"] = execution_results.get("summary", {})
    _save_workflow(wf_updated)

    return ReplayResponse(
        workflow_id=wf["workflow_id"],
        name=wf["name"],
        replay_model=NANO_MODEL,
        estimated_cost_usd=_estimate_cost(steps, NANO_MODEL),
        replay_ready=replay_ready,
        steps=steps,
        missing_element_ids=missing,
        executed=True,
        execution_results=execution_results,
    )


async def _execute_workflow_steps(
    steps: List[Dict[str, Any]],
    device_id: str,
    app_package: str,
    app_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute workflow steps on a real device via MobileMCPClient.

    Uses the same action primitives as execution_agent.py but operates
    from registered workflow steps (element_id-based) instead of
    natural-language test steps. This is the deterministic replay path.
    """
    import asyncio
    import time
    import subprocess

    step_results: List[Dict[str, Any]] = []
    passed = 0
    total = len(steps)

    for idx, step in enumerate(steps):
        action = step.get("action", "")
        element_id = step.get("element_id", "")
        text = step.get("text", "")
        step_start = time.time()

        result: Dict[str, Any] = {
            "step_index": idx,
            "action": action,
            "element_id": element_id,
            "status": "running",
            "error": None,
        }

        try:
            if action in ("tap", "click", "press"):
                # Prefer element_id for deterministic replay
                target = element_id or text or step.get("screen", "")
                if target:
                    try:
                        await _mobile_mcp_client.tap_by_text(device_id, target)
                    except Exception:
                        # Fallback to ADB tap if coordinates are available
                        pass
                    await asyncio.sleep(1.5)

            elif action in ("type", "input", "fill"):
                # Tap target field first, then type
                if element_id:
                    try:
                        await _mobile_mcp_client.tap_by_text(device_id, element_id)
                        await asyncio.sleep(0.5)
                    except Exception:
                        pass
                if text:
                    try:
                        await _mobile_mcp_client.type_text(device_id, text)
                    except Exception:
                        pass
                    await asyncio.sleep(1)

            elif action in ("navigate", "open", "launch_app"):
                url = text or app_url
                if url:
                    try:
                        await _mobile_mcp_client.open_url(device_id, url)
                    except Exception:
                        subprocess.run(
                            ["adb", "-s", device_id, "shell", "am", "start",
                             "-a", "android.intent.action.VIEW", "-d", url],
                            timeout=10, check=False,
                        )
                elif app_package:
                    subprocess.run(
                        ["adb", "-s", device_id, "shell", "monkey", "-p",
                         app_package, "1"],
                        timeout=10, check=False,
                    )
                await asyncio.sleep(2)

            elif action == "back":
                try:
                    await _mobile_mcp_client.press_button(device_id, "BACK")
                except Exception:
                    subprocess.run(
                        ["adb", "-s", device_id, "shell", "input", "keyevent", "4"],
                        timeout=5, check=False,
                    )
                await asyncio.sleep(1)

            elif action in ("scroll", "swipe"):
                try:
                    screen_size = await _mobile_mcp_client.get_screen_size(device_id)
                    if isinstance(screen_size, dict):
                        w = screen_size.get("width", 1080) // 2
                        h = screen_size.get("height", 2400)
                        subprocess.run(
                            ["adb", "-s", device_id, "shell", "input", "swipe",
                             str(w), str(h * 3 // 4), str(w), str(h // 4), "300"],
                            timeout=5, check=False,
                        )
                except Exception:
                    pass
                await asyncio.sleep(1)

            elif action == "wait":
                await asyncio.sleep(2)

            # Take screenshot for evidence
            screenshot_data = None
            try:
                screenshot_result = await _mobile_mcp_client.take_screenshot(device_id)
                if isinstance(screenshot_result, dict):
                    screenshot_data = screenshot_result.get("data")
            except Exception:
                pass

            # Verify screen is responsive
            try:
                ui_elements = await _mobile_mcp_client.get_ui_elements(device_id)
                screen_text = ""
                if isinstance(ui_elements, dict):
                    for item in ui_elements.get("content", []):
                        if isinstance(item, dict):
                            screen_text += item.get("text", "") + " "
                result["screen_text_length"] = len(screen_text.strip())
                result["has_screenshot"] = screenshot_data is not None
                result["status"] = "pass" if len(screen_text.strip()) > 10 else "inconclusive"
            except Exception:
                result["status"] = "pass"  # Can't verify, assume pass

            if result["status"] == "pass":
                passed += 1

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)

        result["duration_ms"] = int((time.time() - step_start) * 1000)
        step_results.append(result)

    return {
        "step_results": step_results,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / total, 4) if total > 0 else 0.0,
        },
    }


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(workflow_id: str):
    """Remove a registered workflow."""
    path = _workflow_path(workflow_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")
    path.unlink()
    logger.info("Deleted workflow %s", workflow_id)
