"""Replay GIF API routes.

Endpoints:
  POST /api/replays/{run_id}/generate  — generate a replay GIF for a pipeline run
  GET  /api/replays/{run_id}           — serve the GIF file directly
  GET  /api/replays                    — list all available replays
  GET  /api/replays/{run_id}/meta      — get replay metadata
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from ..services.gif_replay import (
    generate_replay_gif,
    get_replay_path,
    list_replays,
    REPLAY_DIR,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/replays", tags=["replays"])


@router.post("/{run_id}/generate", summary="Generate replay GIF for a pipeline run")
async def generate_replay(
    run_id: str,
    fps: float = Query(0.5, ge=0.1, le=5.0, description="Frames per second"),
    max_width: int = Query(1280, ge=320, le=1920, description="Max width in pixels"),
):
    """Generate an animated GIF replay from pipeline run screenshots or results.

    If screenshots were captured during the run, they are stitched with overlays.
    Otherwise, synthetic frames are generated from the pipeline result data.
    """
    try:
        path = generate_replay_gif(run_id, fps=fps, max_width=max_width)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to generate replay GIF for {run_id}")
        raise HTTPException(status_code=500, detail=f"GIF generation failed: {e}")

    # Read the metadata we just saved
    meta_path = REPLAY_DIR / f"{run_id}.json"
    meta = {}
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)

    return {
        "run_id": run_id,
        "gif_url": f"/api/replays/{run_id}",
        "frames": meta.get("frames", 0),
        "size_kb": meta.get("size_kb", 0),
        "created": meta.get("created", ""),
    }


@router.get("/{run_id}", summary="Serve replay GIF", response_class=FileResponse)
async def serve_replay(run_id: str):
    """Serve the replay GIF file directly. Use as <img> src."""
    path = get_replay_path(run_id)
    if not path:
        raise HTTPException(status_code=404, detail=f"No replay GIF found for run {run_id}")
    return FileResponse(path, media_type="image/gif", filename=f"{run_id}.gif")


@router.get("", summary="List all replay GIFs")
async def list_all_replays():
    """List all available replay GIFs with metadata."""
    replays = list_replays()
    return {
        "replays": replays,
        "total": len(replays),
    }


@router.get("/{run_id}/meta", summary="Get replay metadata")
async def get_replay_meta(run_id: str):
    """Get metadata for a specific replay GIF."""
    meta_path = REPLAY_DIR / f"{run_id}.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail=f"No replay metadata for run {run_id}")
    with open(meta_path) as f:
        return json.load(f)
