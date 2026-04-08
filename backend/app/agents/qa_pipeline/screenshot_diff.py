"""Screenshot Diff Engine — before/after visual comparison across runs.

Captures baseline screenshots per screen during crawl, then compares
against subsequent runs to detect visual regressions.

Storage: data/screenshots/{app_key}/{run_id}/{screen_id}.png
         data/screenshots/{app_key}/baseline/{screen_id}.png
"""

import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_SCREENSHOTS_DIR = _DATA_DIR / "screenshots"
_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
_DIFF_DIR = _DATA_DIR / "screenshot_diffs"
_DIFF_DIR.mkdir(parents=True, exist_ok=True)


def _app_key(app_name: str, app_url: str = "") -> str:
    raw = f"{app_name.lower().strip()}|{app_url.strip()}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def store_screenshot(
    app_name: str,
    run_id: str,
    screen_id: str,
    screenshot_path: str,
    app_url: str = "",
) -> str:
    """Store a screenshot from a crawl run. Returns the stored path."""
    key = _app_key(app_name, app_url)
    run_dir = _SCREENSHOTS_DIR / key / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    dest = run_dir / f"{screen_id}.png"
    src = Path(screenshot_path)

    if src.exists():
        shutil.copy2(src, dest)
        logger.debug(f"Screenshot stored: {dest}")
    else:
        # Store path reference if file doesn't exist locally
        meta = run_dir / f"{screen_id}.meta.json"
        meta.write_text(json.dumps({
            "original_path": screenshot_path,
            "screen_id": screen_id,
            "stored_at": datetime.now(timezone.utc).isoformat(),
        }))
        logger.debug(f"Screenshot reference stored: {meta}")

    return str(dest)


def set_baseline(app_name: str, run_id: str, app_url: str = "") -> Dict[str, Any]:
    """Promote a run's screenshots to be the baseline for future comparison."""
    key = _app_key(app_name, app_url)
    run_dir = _SCREENSHOTS_DIR / key / run_id
    baseline_dir = _SCREENSHOTS_DIR / key / "baseline"

    if not run_dir.exists():
        return {"error": f"No screenshots for run {run_id}", "screens": 0}

    baseline_dir.mkdir(parents=True, exist_ok=True)

    # Archive old baseline
    if baseline_dir.exists() and any(baseline_dir.iterdir()):
        archive_dir = _SCREENSHOTS_DIR / key / f"baseline_archive_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        shutil.copytree(baseline_dir, archive_dir)
        logger.info(f"Archived old baseline to {archive_dir}")

    # Copy new baseline
    count = 0
    for f in run_dir.glob("*.png"):
        shutil.copy2(f, baseline_dir / f.name)
        count += 1

    logger.info(f"Baseline set from run {run_id}: {count} screenshots")
    return {"run_id": run_id, "screens": count, "baseline_dir": str(baseline_dir)}


def compare_screenshots(
    app_name: str,
    run_id: str,
    app_url: str = "",
) -> Dict[str, Any]:
    """Compare a run's screenshots against baseline using pixel hash.

    Returns diff report: which screens changed, which are new, which removed.
    For actual pixel diffing, we compare file hashes (fast, deterministic).
    """
    key = _app_key(app_name, app_url)
    run_dir = _SCREENSHOTS_DIR / key / run_id
    baseline_dir = _SCREENSHOTS_DIR / key / "baseline"

    if not baseline_dir.exists():
        return {
            "has_baseline": False,
            "message": "No baseline set. Call set_baseline() with a reference run.",
        }

    if not run_dir.exists():
        return {
            "has_baseline": True,
            "error": f"No screenshots for run {run_id}",
        }

    # Hash all baseline screenshots
    baseline_hashes = {}
    for f in baseline_dir.glob("*.png"):
        baseline_hashes[f.stem] = hashlib.md5(f.read_bytes()).hexdigest()

    # Hash all run screenshots
    run_hashes = {}
    for f in run_dir.glob("*.png"):
        run_hashes[f.stem] = hashlib.md5(f.read_bytes()).hexdigest()

    # Compare
    unchanged = []
    changed = []
    new_screens = []
    removed_screens = []

    for screen_id, run_hash in run_hashes.items():
        if screen_id in baseline_hashes:
            if run_hash == baseline_hashes[screen_id]:
                unchanged.append(screen_id)
            else:
                changed.append(screen_id)
        else:
            new_screens.append(screen_id)

    for screen_id in baseline_hashes:
        if screen_id not in run_hashes:
            removed_screens.append(screen_id)

    total = max(len(baseline_hashes | run_hashes), 1)
    change_ratio = round((len(changed) + len(new_screens) + len(removed_screens)) / total, 3)

    diff_result = {
        "has_baseline": True,
        "run_id": run_id,
        "baseline_screens": len(baseline_hashes),
        "run_screens": len(run_hashes),
        "unchanged": unchanged,
        "changed": changed,
        "new_screens": new_screens,
        "removed_screens": removed_screens,
        "change_ratio": change_ratio,
        "visual_regression": len(changed) > 0,
        "compared_at": datetime.now(timezone.utc).isoformat(),
    }

    # Persist diff result
    diff_path = _DIFF_DIR / f"{key}_{run_id}.json"
    diff_path.write_text(json.dumps(diff_result, indent=2))
    logger.info(
        f"Screenshot diff: {len(unchanged)} unchanged, {len(changed)} changed, "
        f"{len(new_screens)} new, {len(removed_screens)} removed "
        f"(change_ratio={change_ratio})"
    )

    return diff_result


def get_diff_history(app_name: str, app_url: str = "") -> List[Dict[str, Any]]:
    """Get all screenshot diff results for an app, sorted by time."""
    key = _app_key(app_name, app_url)
    diffs = []
    for f in sorted(_DIFF_DIR.glob(f"{key}_*.json")):
        try:
            diffs.append(json.loads(f.read_text()))
        except Exception:
            pass
    return diffs
