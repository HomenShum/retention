"""DRX Delta Refresh Benchmark API routes."""

import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/drx-benchmarks", tags=["drx-benchmark"])


class DRXBenchmarkRequest(BaseModel):
    research_topic: str
    frontier_model: str = "gpt-5.4"
    replay_model: str = "gpt-5.4-mini"
    cheap_model: str = "gpt-5.4-nano"
    max_tokens: int = 4000


class DRXOfflineRequest(BaseModel):
    research_topic: str
    baseline_output: str
    refresh_output: str
    cheap_refresh_output: str = ""
    frontier_model: str = "gpt-5.4"
    replay_model: str = "gpt-5.4-mini"
    cheap_model: str = "gpt-5.4-nano"
    use_llm_judge: bool = False


@router.get("")
async def list_drx_benchmarks():
    """List all DRX benchmark results."""
    from pathlib import Path
    drx_dir = Path(__file__).resolve().parents[2] / "data" / "drx_benchmarks"
    if not drx_dir.exists():
        return {"total": 0, "results": []}

    results = []
    for f in sorted(drx_dir.glob("drx-*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            results.append({
                "benchmark_id": data.get("benchmark_id"),
                "research_topic": data.get("research_topic"),
                "data_source": data.get("data_source"),
                "judge_method": data.get("judge_method"),
                "final_verdict": data.get("final_verdict"),
                "timestamp": data.get("timestamp"),
                "summary": data.get("summary", "")[:200],
            })
        except Exception:
            pass

    return {"total": len(results), "results": results}


@router.get("/{benchmark_id}")
async def get_drx_benchmark(benchmark_id: str):
    """Get a single DRX benchmark result."""
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "data" / "drx_benchmarks" / f"{benchmark_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"DRX benchmark {benchmark_id} not found")
    return json.loads(path.read_text())


@router.post("/run-live")
async def run_live_drx_benchmark(req: DRXBenchmarkRequest):
    """Run a LIVE DRX delta refresh benchmark with real API calls.

    Requires OPENAI_API_KEY. Returns data_source=live_api results.
    """
    from ..benchmarks.drx_delta_benchmark import run_drx_delta_benchmark_live

    try:
        result = await run_drx_delta_benchmark_live(
            research_topic=req.research_topic,
            frontier_model=req.frontier_model,
            replay_model=req.replay_model,
            cheap_model=req.cheap_model,
            max_tokens=req.max_tokens,
        )
        return result.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run-offline")
async def run_offline_drx_benchmark(req: DRXOfflineRequest):
    """Run an OFFLINE DRX benchmark from pre-existing research text.

    No API calls for research. data_source=simulated.
    """
    from ..benchmarks.drx_delta_benchmark import run_drx_delta_benchmark

    result = run_drx_delta_benchmark(
        research_topic=req.research_topic,
        baseline_output=req.baseline_output,
        refresh_output=req.refresh_output,
        cheap_refresh_output=req.cheap_refresh_output,
        frontier_model=req.frontier_model,
        replay_model=req.replay_model,
        cheap_model=req.cheap_model,
        use_llm_judge=req.use_llm_judge,
    )
    return result.model_dump()


@router.get("/cards/latest")
async def get_drx_benchmark_card():
    """Get the latest DRX benchmark card."""
    from pathlib import Path
    card_dir = Path(__file__).resolve().parents[2] / "data" / "benchmark_cards"
    drx_cards = sorted(card_dir.glob("drx_*.json"), reverse=True)
    if not drx_cards:
        raise HTTPException(status_code=404, detail="No DRX benchmark cards found")
    return json.loads(drx_cards[0].read_text())
