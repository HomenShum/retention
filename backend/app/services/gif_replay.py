"""
GIF Replay Service — captures and stitches pipeline run screenshots into replay GIFs.

Every QA pipeline run captures screenshots at key steps:
- Page load / navigation
- Element interaction (click, type)
- Assertion check (pass/fail)
- Error state

After run completion, screenshots are stitched into an animated GIF.
"""

from __future__ import annotations
import io
import os
import json
import glob
import time
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
REPLAY_DIR = DATA_DIR / "replays"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"


def ensure_dirs():
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def capture_step_screenshot(run_id: str, step_index: int, screenshot_bytes: bytes, label: str = "", status: str = "running"):
    """Save a screenshot for a pipeline step. Called during pipeline execution."""
    ensure_dirs()
    run_dir = SCREENSHOTS_DIR / run_id
    run_dir.mkdir(exist_ok=True)

    filename = f"step-{step_index:03d}.png"
    filepath = run_dir / filename

    with open(filepath, "wb") as f:
        f.write(screenshot_bytes)

    # Save metadata
    meta_path = run_dir / f"step-{step_index:03d}.json"
    meta = {
        "step": step_index,
        "label": label,
        "status": status,
        "timestamp": datetime.now().isoformat(),
        "filename": filename,
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f)

    return str(filepath)


def generate_replay_gif(run_id: str, fps: float = 0.5, max_width: int = 1280) -> str:
    """Stitch all screenshots from a run into an animated GIF with overlays."""
    ensure_dirs()
    run_dir = SCREENSHOTS_DIR / run_id

    if not run_dir.exists():
        # Try to find screenshots from pipeline results
        return _generate_from_pipeline_results(run_id, fps, max_width)

    # Load screenshots in order
    png_files = sorted(glob.glob(str(run_dir / "step-*.png")))
    if not png_files:
        raise ValueError(f"No screenshots found for run {run_id}")

    # Load metadata
    meta_files = sorted(glob.glob(str(run_dir / "step-*.json")))
    metas = []
    for mf in meta_files:
        with open(mf) as f:
            metas.append(json.load(f))

    frames = []
    for i, png_path in enumerate(png_files):
        img = Image.open(png_path).convert("RGB")

        # Resize if too large
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)

        # Add overlay with step info
        img = _add_overlay(img, i, len(png_files), metas[i] if i < len(metas) else {}, run_id)
        frames.append(img)

    # Save GIF
    output_path = REPLAY_DIR / f"{run_id}.gif"
    duration_ms = int(1000 / fps)  # Convert fps to ms per frame

    frames[0].save(
        str(output_path),
        save_all=True,
        append_images=frames[1:],
        duration=[duration_ms] * len(frames),
        loop=0,
    )

    size_kb = os.path.getsize(output_path) // 1024

    # Save replay metadata
    replay_meta = {
        "run_id": run_id,
        "frames": len(frames),
        "fps": fps,
        "size_kb": size_kb,
        "width": frames[0].width,
        "height": frames[0].height,
        "created": datetime.now().isoformat(),
        "path": str(output_path),
    }
    with open(REPLAY_DIR / f"{run_id}.json", "w") as f:
        json.dump(replay_meta, f, indent=2)

    return str(output_path)


def _generate_from_pipeline_results(run_id: str, fps: float, max_width: int) -> str:
    """Generate a GIF from pipeline results when no screenshots exist.
    Creates frames from the test case data with status indicators."""
    ensure_dirs()

    # Load pipeline results
    results_path = DATA_DIR / "pipeline_results" / f"{run_id}.json"
    if not results_path.exists():
        # Try partial match
        results_dir = DATA_DIR / "pipeline_results"
        if results_dir.exists():
            matches = list(results_dir.glob(f"*{run_id[:8]}*.json"))
            if matches:
                results_path = matches[0]
            else:
                raise ValueError(f"No pipeline results found for {run_id}")
        else:
            raise ValueError(f"No pipeline results found for {run_id}")

    with open(results_path) as f:
        results = json.load(f)

    # Extract test cases and stages — handle nested result structure
    inner = results.get("result", results)
    test_cases = inner.get("test_cases", inner.get("tests", results.get("test_cases", results.get("tests", []))))
    stages = results.get("stage_timings", results.get("stages", inner.get("stages", {})))
    app_url = results.get("app_url", results.get("url", inner.get("app_url", "Unknown")))

    frames = []

    # Frame 1: Title card
    frames.append(_create_title_frame(run_id, app_url, len(test_cases), max_width))

    # Frame 2: Stages overview
    if stages:
        frames.append(_create_stages_frame(stages, run_id, max_width))

    # Frame per test case (max 15)
    for i, tc in enumerate(test_cases[:15]):
        frames.append(_create_test_frame(tc, i, len(test_cases), run_id, max_width))

    # Final frame: Summary
    passed = sum(1 for tc in test_cases if str(tc.get("status") or "").lower() in ("passed", "pass", "success") or str(tc.get("result") or "").lower() in ("passed", "pass", "success"))
    pending = sum(1 for tc in test_cases if (tc.get("status") is None and tc.get("result") is None))
    total = len(test_cases)
    frames.append(_create_summary_frame(run_id, passed, total, results, max_width))

    # Save GIF
    output_path = REPLAY_DIR / f"{run_id}.gif"
    duration_ms = int(1000 / fps)

    frames[0].save(
        str(output_path),
        save_all=True,
        append_images=frames[1:],
        duration=[duration_ms] * len(frames),
        loop=0,
    )

    size_kb = os.path.getsize(output_path) // 1024
    replay_meta = {
        "run_id": run_id,
        "frames": len(frames),
        "fps": fps,
        "size_kb": size_kb,
        "source": "pipeline_results",
        "created": datetime.now().isoformat(),
        "path": str(output_path),
    }
    with open(REPLAY_DIR / f"{run_id}.json", "w") as f:
        json.dump(replay_meta, f, indent=2)

    return str(output_path)


def _add_overlay(img: Image.Image, step: int, total: int, meta: dict, run_id: str) -> Image.Image:
    """Add status overlay to a screenshot frame."""
    draw = ImageDraw.Draw(img)

    # Top bar
    bar_height = 36
    draw.rectangle([(0, 0), (img.width, bar_height)], fill=(15, 15, 20, 220))

    # Step counter
    label = meta.get("label", f"Step {step + 1}")
    status = meta.get("status", "running")
    status_color = {"passed": (34, 197, 94), "failed": (239, 68, 68), "running": (139, 92, 246)}.get(status, (139, 92, 246))

    try:
        font = ImageFont.truetype("/System/Library/Fonts/SFCompact.ttf", 14)
    except Exception:
        font = ImageFont.load_default()

    # Draw step info
    draw.text((12, 10), f"Step {step + 1}/{total}", fill=(255, 255, 255), font=font)
    draw.text((140, 10), label[:60], fill=(200, 200, 200), font=font)

    # Status dot
    draw.ellipse([(img.width - 24, 12), (img.width - 12, 24)], fill=status_color)

    # Progress bar at bottom
    bar_y = img.height - 4
    progress = (step + 1) / total
    draw.rectangle([(0, bar_y), (int(img.width * progress), img.height)], fill=status_color)

    return img


def _create_title_frame(run_id: str, app_url: str, test_count: int, width: int) -> Image.Image:
    """Create a title card frame."""
    height = int(width * 0.5625)  # 16:9
    img = Image.new("RGB", (width, height), (15, 15, 20))
    draw = ImageDraw.Draw(img)

    try:
        font_lg = ImageFont.truetype("/System/Library/Fonts/SFCompact.ttf", 32)
        font_md = ImageFont.truetype("/System/Library/Fonts/SFCompact.ttf", 18)
        font_sm = ImageFont.truetype("/System/Library/Fonts/SFCompact.ttf", 14)
    except Exception:
        font_lg = font_md = font_sm = ImageFont.load_default()

    # Purple gradient bar at top
    for y in range(6):
        draw.rectangle([(0, y), (width, y + 1)], fill=(139, 92, 246))

    # Title
    draw.text((width // 2 - 180, height // 3), "retention.sh QA Replay", fill=(255, 255, 255), font=font_lg)
    draw.text((width // 2 - 120, height // 3 + 50), f"Run: {run_id[:16]}", fill=(139, 92, 246), font=font_md)
    draw.text((width // 2 - 120, height // 3 + 80), f"App: {app_url[:50]}", fill=(160, 160, 160), font=font_sm)
    draw.text((width // 2 - 120, height // 3 + 105), f"Tests: {test_count}", fill=(160, 160, 160), font=font_sm)

    return img


def _create_stages_frame(stages: dict, run_id: str, width: int) -> Image.Image:
    """Create a pipeline stages overview frame."""
    height = int(width * 0.5625)
    img = Image.new("RGB", (width, height), (15, 15, 20))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/SFCompact.ttf", 16)
        font_sm = ImageFont.truetype("/System/Library/Fonts/SFCompact.ttf", 13)
    except Exception:
        font = font_sm = ImageFont.load_default()

    draw.text((40, 30), "Pipeline Stages", fill=(255, 255, 255), font=font)

    y = 80
    for stage_name, stage_data in stages.items():
        status = stage_data.get("status", "unknown") if isinstance(stage_data, dict) else str(stage_data)
        color = (34, 197, 94) if "complete" in status.lower() or "done" in status.lower() else (139, 92, 246)
        draw.ellipse([(40, y + 2), (52, y + 14)], fill=color)
        draw.text((62, y), f"{stage_name}: {status}", fill=(200, 200, 200), font=font_sm)
        y += 30

    return img


def _create_test_frame(tc: dict, index: int, total: int, run_id: str, width: int) -> Image.Image:
    """Create a frame for a single test case."""
    height = int(width * 0.5625)
    img = Image.new("RGB", (width, height), (15, 15, 20))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/SFCompact.ttf", 16)
        font_sm = ImageFont.truetype("/System/Library/Fonts/SFCompact.ttf", 13)
        font_lg = ImageFont.truetype("/System/Library/Fonts/SFCompact.ttf", 20)
    except Exception:
        font = font_sm = font_lg = ImageFont.load_default()

    # Status — handle null/None (not yet executed)
    raw_status = tc.get("status") or tc.get("result") or tc.get("execution_status")
    if raw_status is None or str(raw_status).lower() in ("null", "none", "unknown", ""):
        is_pass = False
        is_pending = True
        status_color = (161, 161, 170)  # gray for pending/not-run
        status_text = "PENDING"
    else:
        is_pending = False
        is_pass = str(raw_status).lower() in ("passed", "pass", "success", "ok")
        is_fail = str(raw_status).lower() in ("failed", "fail", "error")
        status_color = (34, 197, 94) if is_pass else (239, 68, 68) if is_fail else (234, 179, 8)  # yellow for other
        status_text = "PASS" if is_pass else "FAIL" if is_fail else str(raw_status).upper()[:10]

    # Header
    draw.text((40, 30), f"Test {index + 1}/{total}", fill=(139, 92, 246), font=font)
    draw.text((width - 100, 30), status_text, fill=status_color, font=font_lg)

    # Test name
    name = tc.get("name", tc.get("title", tc.get("description", f"Test Case {index + 1}")))
    draw.text((40, 70), name[:80], fill=(255, 255, 255), font=font_lg)

    # Details
    y = 120
    if tc.get("description"):
        desc = tc["description"][:150]
        draw.text((40, y), desc, fill=(160, 160, 160), font=font_sm)
        y += 25

    if tc.get("steps"):
        for step in tc["steps"][:8]:
            step_text = step if isinstance(step, str) else step.get("action", step.get("description", str(step)))
            draw.text((60, y), f"* {str(step_text)[:90]}", fill=(140, 140, 140), font=font_sm)
            y += 22

    if tc.get("error") or tc.get("failure_reason"):
        error = tc.get("error", tc.get("failure_reason", ""))
        draw.text((40, y + 10), f"Error: {str(error)[:100]}", fill=(239, 68, 68), font=font_sm)

    # Progress bar
    progress = (index + 1) / total
    draw.rectangle([(0, height - 4), (int(width * progress), height)], fill=(139, 92, 246))

    return img


def _create_summary_frame(run_id: str, passed: int, total: int, results: dict, width: int) -> Image.Image:
    """Create a summary frame."""
    height = int(width * 0.5625)
    img = Image.new("RGB", (width, height), (15, 15, 20))
    draw = ImageDraw.Draw(img)

    try:
        font_lg = ImageFont.truetype("/System/Library/Fonts/SFCompact.ttf", 48)
        font_md = ImageFont.truetype("/System/Library/Fonts/SFCompact.ttf", 20)
        font_sm = ImageFont.truetype("/System/Library/Fonts/SFCompact.ttf", 14)
    except Exception:
        font_lg = font_md = font_sm = ImageFont.load_default()

    rate = (passed / total * 100) if total > 0 else 0
    verdict_color = (34, 197, 94) if rate >= 80 else (239, 68, 68)

    draw.text((width // 2 - 80, height // 4), f"{rate:.0f}%", fill=verdict_color, font=font_lg)
    draw.text((width // 2 - 60, height // 4 + 60), f"{passed}/{total} passed", fill=(200, 200, 200), font=font_md)

    # Details
    duration = results.get("duration_s", results.get("duration", "N/A"))
    tokens = results.get("total_tokens", results.get("tokens", "N/A"))
    cost = results.get("cost", results.get("estimated_cost", "N/A"))

    y = height // 2 + 20
    draw.text((width // 2 - 100, y), f"Duration: {duration}s", fill=(140, 140, 140), font=font_sm)
    draw.text((width // 2 - 100, y + 25), f"Tokens: {tokens}", fill=(140, 140, 140), font=font_sm)
    draw.text((width // 2 - 100, y + 50), f"Cost: ${cost}", fill=(140, 140, 140), font=font_sm)

    # Purple bar at bottom
    for y in range(height - 6, height):
        draw.rectangle([(0, y), (width, y + 1)], fill=(139, 92, 246))

    return img


def list_replays() -> list:
    """List all available replay GIFs."""
    ensure_dirs()
    replays = []
    for meta_path in sorted(REPLAY_DIR.glob("*.json")):
        with open(meta_path) as f:
            replays.append(json.load(f))
    return replays


def get_replay_path(run_id: str) -> str | None:
    """Get the path to a replay GIF if it exists."""
    path = REPLAY_DIR / f"{run_id}.gif"
    if path.exists():
        return str(path)
    return None
