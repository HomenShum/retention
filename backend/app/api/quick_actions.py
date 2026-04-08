"""
Quick Actions API — one-click convenience endpoints for common operations.

Powers the Quick Actions dashboard on the frontend.
"""

import logging
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/quick", tags=["quick-actions"])

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _count_files(subdir: str, ext: str = "*.json") -> int:
    p = _DATA_DIR / subdir
    return len(list(p.glob(ext))) if p.exists() else 0


@router.get("/status")
async def system_status() -> Dict[str, Any]:
    """System health overview — counts of all major data artifacts."""
    return {
        "rops": _count_files("rop_patterns"),
        "manifests": _count_files("rop_manifests"),
        "evals": _count_files("rerun_eval"),
        "benchmarks": _count_files("three_lane_benchmarks"),
        "datasets": _count_files("distillation_datasets"),
        "trajectories": _count_files("trajectories"),
        "replay_results": _count_files("replay_results"),
        "dreams": _count_files("rop_dreams"),
        "savings_records": _count_files("rop_savings", "*.jsonl"),
        "timestamp": time.time(),
    }


@router.post("/run-benchmark")
async def run_benchmark(background_tasks: BackgroundTasks):
    """Trigger three-lane benchmark (offline mode from existing replay results)."""
    task_id = f"bench-{int(time.time())}"

    def _run():
        try:
            from ..benchmarks.three_lane_benchmark import run_three_lane_eval_offline
            run_three_lane_eval_offline()
            logger.info(f"Quick action: benchmark {task_id} complete")
        except Exception as e:
            logger.error(f"Quick action benchmark failed: {e}")

    background_tasks.add_task(_run)
    return {"task_id": task_id, "status": "started", "action": "run-benchmark"}


@router.post("/trigger-dream")
async def trigger_dream(background_tasks: BackgroundTasks):
    """Fire KAIROS dream engine consolidation."""
    def _run():
        try:
            from ..services.rop_dream_engine import run_dream
            result = run_dream()
            logger.info(
                f"Quick action: dream complete — "
                f"promoted={result.trajectories_promoted}, "
                f"pruned={result.trajectories_pruned}"
            )
        except Exception as e:
            logger.error(f"Quick action dream failed: {e}")

    background_tasks.add_task(_run)
    return {"status": "dream_triggered", "action": "trigger-dream"}


@router.post("/generate-dataset")
async def generate_dataset_action(background_tasks: BackgroundTasks):
    """Trigger distillation dataset generation from latest eval results."""
    def _run():
        try:
            from ..benchmarks.distillation_dataset import generate_dataset
            result = generate_dataset(task_name="latest", min_composite_score=0.75)
            logger.info(f"Quick action: dataset generated — {result}")
        except Exception as e:
            logger.error(f"Quick action dataset generation failed: {e}")

    background_tasks.add_task(_run)
    return {"status": "generation_started", "action": "generate-dataset"}


@router.post("/compare-runs")
async def compare_runs():
    """Compare latest cold vs replay runs (last 7 days)."""
    try:
        from ..services.rop_savings_tracker import get_rop_savings_tracker
        tracker = get_rop_savings_tracker()
        stats = tracker.portfolio_stats(days=7)
        return {"status": "ok", "stats": stats}
    except Exception as e:
        logger.error(f"Quick action compare failed: {e}")
        return {"status": "error", "error": str(e), "stats": {}}
