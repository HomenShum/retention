"""
Flywheel Cycle API — manages dev cycles through spec → plan → build → test → review → ship.
Wires into the workflow judge for step validation and retention crawls for verification.
"""

import time
import uuid
import logging
import asyncio
import json as json_mod
from typing import Any, Dict, List, Optional
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/flywheel", tags=["flywheel"])

_cycles: Dict[str, Dict[str, Any]] = {}

FLYWHEEL_STEPS = [
    {"id": "spec", "label": "Spec", "description": "Define requirements, acceptance criteria, and scope", "icon": "FileText", "judge_step": "understand_plan",
     "actions": [{"id": "detect_workflow", "label": "Auto-detect workflow", "endpoint": "/api/judge/detect", "method": "POST"}, {"id": "crawl_baseline", "label": "Crawl baseline", "endpoint": "/api/flywheel/crawl", "method": "POST"}]},
    {"id": "plan", "label": "Plan", "description": "Map affected files, surfaces, and dependencies", "icon": "Map", "judge_step": "inspect_surfaces",
     "actions": [{"id": "list_files", "label": "Scan affected files", "endpoint": "/api/flywheel/scan-files", "method": "POST"}]},
    {"id": "research", "label": "Research", "description": "Search for latest context, docs, or best practices", "icon": "Search", "judge_step": "latest_search", "actions": []},
    {"id": "build", "label": "Build", "description": "Implement the changes across frontend, backend, tests", "icon": "Hammer", "judge_step": "implement",
     "actions": [{"id": "git_diff", "label": "View diff", "endpoint": "/api/flywheel/diff", "method": "GET"}]},
    {"id": "test", "label": "Test", "description": "Run tests, typecheck, lint, crawl for regressions", "icon": "FlaskConical", "judge_step": "verify",
     "actions": [{"id": "run_tests", "label": "Run tests", "endpoint": "/api/flywheel/run-tests", "method": "POST"}, {"id": "crawl_verify", "label": "Re-crawl (retention)", "endpoint": "/api/flywheel/crawl", "method": "POST"}]},
    {"id": "review", "label": "Review", "description": "Audit interactive surfaces, check accessibility, review diff", "icon": "Eye", "judge_step": "interactive_audit",
     "actions": [{"id": "judge_check", "label": "Run judge", "endpoint": "/api/judge/check", "method": "POST"}]},
    {"id": "ship", "label": "Ship", "description": "Generate PR summary, commit, push", "icon": "Rocket", "judge_step": "pr_summary",
     "actions": [{"id": "savings", "label": "Show savings", "endpoint": "/api/flywheel/savings", "method": "GET"}]},
]


def _make_cycle(cycle_id, title, description, findings=None):
    return {
        "cycle_id": cycle_id, "title": title, "description": description, "status": "active",
        "current_step": "spec", "created_at": time.time(), "updated_at": time.time(),
        "findings": findings or [],
        "steps": {s["id"]: {"status": "pending", "evidence": [], "started_at": None, "completed_at": None, "verdict": None} for s in FLYWHEEL_STEPS},
        "crawl_baseline": None, "crawl_latest": None, "judge_verdict": None,
    }


class CreateCycleRequest(BaseModel):
    title: str
    description: str = ""
    findings: List[Dict[str, Any]] = []

class StepUpdateRequest(BaseModel):
    status: str
    evidence: Optional[Dict[str, Any]] = None
    verdict: Optional[str] = None

class CrawlRequest(BaseModel):
    url: str = "http://localhost:5173"
    cycle_id: str = ""

class RunTestsRequest(BaseModel):
    cycle_id: str = ""
    test_type: str = "all"


@router.get("/steps")
async def get_flywheel_steps():
    return {"steps": FLYWHEEL_STEPS}

@router.post("/cycles")
async def create_cycle(req: CreateCycleRequest):
    cycle_id = str(uuid.uuid4())[:8]
    cycle = _make_cycle(cycle_id, req.title, req.description, req.findings)
    _cycles[cycle_id] = cycle
    return cycle

@router.get("/cycles")
async def list_cycles():
    return {"cycles": sorted(_cycles.values(), key=lambda c: c["created_at"], reverse=True)}

@router.get("/cycles/{cycle_id}")
async def get_cycle(cycle_id: str):
    if cycle_id not in _cycles:
        raise HTTPException(404, f"Cycle {cycle_id} not found")
    return _cycles[cycle_id]

@router.patch("/cycles/{cycle_id}/steps/{step_id}")
async def update_step(cycle_id: str, step_id: str, req: StepUpdateRequest):
    if cycle_id not in _cycles:
        raise HTTPException(404)
    cycle = _cycles[cycle_id]
    if step_id not in cycle["steps"]:
        raise HTTPException(404)
    step = cycle["steps"][step_id]
    step["status"] = req.status
    if req.status == "in_progress" and not step["started_at"]:
        step["started_at"] = time.time()
    if req.status == "done":
        step["completed_at"] = time.time()
    if req.evidence:
        step["evidence"].append(req.evidence)
    if req.verdict:
        step["verdict"] = req.verdict
    step_ids = [s["id"] for s in FLYWHEEL_STEPS]
    if req.status == "done":
        idx = step_ids.index(step_id)
        if idx + 1 < len(step_ids):
            cycle["current_step"] = step_ids[idx + 1]
    cycle["updated_at"] = time.time()
    if all(s["status"] in ("done", "skipped") for s in cycle["steps"].values()):
        cycle["status"] = "complete"
    await broadcast_flywheel_event("step_updated", {"cycle_id": cycle_id, "step_id": step_id, "status": req.status})
    return cycle

@router.post("/crawl")
async def run_crawl(req: CrawlRequest):
    try:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get("http://localhost:8000/api/live-demo/crawl-sitemap", params={"url": req.url, "max_pages": 10})
            result = resp.json() if resp.status_code == 200 else {"error": resp.text}
    except Exception as e:
        result = {"error": str(e)}
    if req.cycle_id and req.cycle_id in _cycles:
        cycle = _cycles[req.cycle_id]
        if not cycle["crawl_baseline"]:
            cycle["crawl_baseline"] = result
        else:
            cycle["crawl_latest"] = result
    return result

@router.post("/run-tests")
async def run_tests(req: RunTestsRequest):
    import subprocess
    results = {}
    root = "/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"
    if req.test_type in ("all", "backend"):
        try:
            proc = subprocess.run(["python", "-m", "pytest", "--tb=short", "-q"], cwd=f"{root}/backend", capture_output=True, text=True, timeout=120)
            results["backend"] = {"passed": proc.returncode == 0, "output": proc.stdout[-2000:], "errors": proc.stderr[-1000:]}
        except Exception as e:
            results["backend"] = {"passed": False, "output": "", "errors": str(e)}
    if req.test_type in ("all", "frontend"):
        try:
            proc = subprocess.run(["npx", "tsc", "--noEmit"], cwd=f"{root}/frontend/test-studio", capture_output=True, text=True, timeout=120)
            results["frontend_typecheck"] = {"passed": proc.returncode == 0, "output": proc.stdout[-2000:], "errors": proc.stderr[-1000:]}
        except Exception as e:
            results["frontend_typecheck"] = {"passed": False, "output": "", "errors": str(e)}
    if req.cycle_id and req.cycle_id in _cycles:
        _cycles[req.cycle_id]["steps"]["test"]["evidence"].append({"type": "test_results", "results": results})
    return {"test_type": req.test_type, "results": results}

@router.get("/diff")
async def get_diff():
    import subprocess
    root = "/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"
    try:
        proc = subprocess.run(["git", "diff", "--stat"], cwd=root, capture_output=True, text=True, timeout=10)
        detail = subprocess.run(["git", "diff", "--no-color"], cwd=root, capture_output=True, text=True, timeout=10)
        return {"summary": proc.stdout, "diff": detail.stdout[-5000:], "files_changed": len([l for l in proc.stdout.split("\n") if "|" in l])}
    except Exception as e:
        return {"summary": "", "diff": "", "error": str(e)}

@router.get("/savings")
async def get_savings():
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get("http://localhost:8000/api/workflows/compression-stats")
            return resp.json() if resp.status_code == 200 else {"error": resp.text}
    except Exception as e:
        return {"error": str(e)}

@router.post("/scan-files")
async def scan_files():
    import subprocess
    root = "/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"
    try:
        proc = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=10)
        changed = [l.strip() for l in proc.stdout.split("\n") if l.strip()]
        return {"changed_files": changed, "count": len(changed)}
    except Exception as e:
        return {"changed_files": [], "error": str(e)}

@router.get("/diagnosis")
async def get_diagnosis():
    return {"findings": [
        {"id": "auth-bypass", "severity": "critical", "title": "Auth bypass when RETENTION_MCP_TOKEN unset", "file": "backend/app/main.py:224", "category": "security"},
        {"id": "cors-regex", "severity": "high", "title": "Broad CORS regex allows *.vercel.app", "file": "backend/app/main.py:155", "category": "security"},
        {"id": "env-inconsistency", "severity": "high", "title": "VITE_API_BASE vs VITE_API_URL inconsistency", "file": "16+ frontend files", "category": "config"},
        {"id": "no-api-tests", "severity": "medium", "title": "No API route test coverage", "file": "backend/app/api/", "category": "testing"},
        {"id": "no-rate-limit", "severity": "medium", "title": "No rate limiting on public endpoints", "file": "backend/app/main.py", "category": "security"},
        {"id": "require-import", "severity": "medium", "title": "require() instead of import in RequireAuth", "file": "frontend/.../RequireAuth.tsx:22", "category": "code-quality"},
        {"id": "bundle-size", "severity": "medium", "title": "Bundle not analyzed, heavy deps not lazy-loaded", "file": "frontend/.../vite.config.ts", "category": "performance"},
        {"id": "docker-root", "severity": "low", "title": "Dockerfile runs as root user", "file": "backend/Dockerfile", "category": "infra"},
        {"id": "a11y-gaps", "severity": "medium", "title": "Missing accessibility labels on icons/images", "file": "frontend components", "category": "accessibility"},
        {"id": "json-schemas", "severity": "low", "title": "No JSON schema validation on data files", "file": "backend/data/", "category": "data-integrity"},
    ]}


# ── SSE Stream ──────────────────────────────────────────────────

_flywheel_event_queues: List[asyncio.Queue] = []

async def broadcast_flywheel_event(event_type: str, data: Dict[str, Any]):
    for q in list(_flywheel_event_queues):
        try:
            await q.put({"type": event_type, **data})
        except Exception:
            pass

@router.get("/stream")
async def flywheel_stream():
    from starlette.responses import StreamingResponse
    queue: asyncio.Queue = asyncio.Queue()
    _flywheel_event_queues.append(queue)
    async def gen():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json_mod.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            if queue in _flywheel_event_queues:
                _flywheel_event_queues.remove(queue)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Distill ─────────────────────────────────────────────────────

@router.post("/cycles/{cycle_id}/distill")
async def distill_cycle(cycle_id: str):
    if cycle_id not in _cycles:
        raise HTTPException(404)
    cycle = _cycles[cycle_id]
    frontier_tokens = 0
    replay_tokens = 0
    step_recipes = []
    for sd in FLYWHEEL_STEPS:
        ss = cycle["steps"].get(sd["id"], {})
        ev = ss.get("evidence", [])
        status = ss.get("status", "pending")
        sf = len(ev) * 1500
        sr = len(ev) * 200 if status == "done" else 0
        frontier_tokens += sf
        replay_tokens += sr
        step_recipes.append({
            "step_id": sd["id"], "step_name": sd["label"], "judge_step": sd["judge_step"],
            "status": status, "evidence_count": len(ev),
            "frontier_tokens": sf, "replay_tokens": sr,
            "savings_pct": round((1 - sr / max(sf, 1)) * 100, 1),
            "can_replay": status == "done" and len(ev) > 0,
            "needs_frontier": status != "done",
        })
    savings_pct = round((1 - replay_tokens / max(frontier_tokens, 1)) * 100, 1)
    result = {
        "cycle_id": cycle_id, "title": cycle["title"],
        "distillation": {
            "frontier": {"tokens": frontier_tokens, "cost_usd": round(frontier_tokens * 0.000015, 4)},
            "replay": {"tokens": replay_tokens, "cost_usd": round(replay_tokens * 0.000003, 4)},
            "savings": {"tokens_saved": frontier_tokens - replay_tokens, "savings_pct": savings_pct, "cost_saved_usd": round((frontier_tokens * 0.000015) - (replay_tokens * 0.000003), 4)},
        },
        "step_recipes": step_recipes,
        "replay_ready": all(r["can_replay"] for r in step_recipes),
        "steps_needing_frontier": [r["step_name"] for r in step_recipes if r["needs_frontier"]],
    }
    await broadcast_flywheel_event("distill_complete", {"cycle_id": cycle_id, "savings_pct": savings_pct, "replay_ready": result["replay_ready"]})
    return result


# ── Savings Comparison ──────────────────────────────────────────

@router.get("/savings/comparison")
async def get_savings_comparison():
    comparisons = []
    for cid, cycle in _cycles.items():
        ft, rt = 0, 0
        for sd in FLYWHEEL_STEPS:
            ss = cycle["steps"].get(sd["id"], {})
            ev = ss.get("evidence", [])
            ft += len(ev) * 1500
            if ss.get("status") == "done":
                rt += len(ev) * 200
        comparisons.append({
            "cycle_id": cid, "title": cycle["title"], "status": cycle["status"],
            "frontier": {"tokens": ft, "cost_usd": round(ft * 0.000015, 4)},
            "replay": {"tokens": rt, "cost_usd": round(rt * 0.000003, 4)},
            "savings_pct": round((1 - rt / max(ft, 1)) * 100, 1) if ft > 0 else 0,
        })
    real_savings = []
    try:
        import json
        traj_dir = Path(__file__).parent.parent.parent / "data" / "trajectories"
        if traj_dir.exists():
            for fam_dir in traj_dir.iterdir():
                if not fam_dir.is_dir():
                    continue
                for tf in sorted(fam_dir.glob("*.json"))[:3]:
                    with open(tf) as f:
                        traj = json.load(f)
                    tokens = traj.get("total_tokens", 0) or traj.get("metadata", {}).get("total_tokens", 0)
                    if tokens > 0:
                        re = int(tokens * 0.13)
                        real_savings.append({"source": tf.stem, "type": "trajectory", "frontier": {"tokens": tokens, "cost_usd": round(tokens * 0.000015, 4)}, "replay": {"tokens": re, "cost_usd": round(re * 0.000003, 4)}, "savings_pct": 87.0})
    except Exception:
        pass
    return {"cycles": comparisons, "trajectories": real_savings, "aggregate": {
        "total_frontier_tokens": sum(c["frontier"]["tokens"] for c in comparisons),
        "total_replay_tokens": sum(c["replay"]["tokens"] for c in comparisons),
        "avg_savings_pct": round(sum(c["savings_pct"] for c in comparisons) / max(len(comparisons), 1), 1) if comparisons else 0,
    }}
