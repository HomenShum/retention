"""Path Memory — persistent exploration memory across crawl runs.

Stores the screen graph, fingerprints, and transitions from each crawl
so the next crawl on the same app can skip known-unchanged screens.

Storage: JSON files in backend/data/path_memory/{app_key}.json

Usage:
    from app.agents.qa_pipeline.path_memory import PathMemoryStore

    store = PathMemoryStore()

    # Save after crawl
    store.save(app_key="quickbook", crawl_result=result, fingerprints=fp_dict)

    # Load before next crawl
    prior = store.load("quickbook")
    if prior:
        known_fingerprints = prior["fingerprints"]  # Skip these screens
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schemas import CrawlResult, ScreenNode, ScreenTransition, ComponentInfo

logger = logging.getLogger(__name__)

_MEMORY_DIR = Path(__file__).resolve().parents[3] / "data" / "path_memory"
_MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _app_key(app_name: str, app_url: str = "", package_name: str = "") -> str:
    """Generate a stable key for an app from its identifiers."""
    import hashlib
    raw = f"{app_name.lower().strip()}|{app_url.strip()}|{package_name.strip()}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


class PathMemoryStore:
    """Persistent store for exploration path memory."""

    def save(
        self,
        app_name: str,
        crawl_result: CrawlResult,
        fingerprints: Dict[str, str],
        app_url: str = "",
        package_name: str = "",
        run_id: str = "",
    ) -> str:
        """Save crawl results as path memory for future reuse.

        Returns the app_key used for storage.
        """
        key = _app_key(app_name, app_url, package_name)

        memory = {
            "app_key": key,
            "app_name": app_name,
            "app_url": app_url,
            "package_name": package_name,
            "run_id": run_id,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "screens": [s.model_dump() for s in crawl_result.screens],
            "transitions": [t.model_dump() for t in crawl_result.transitions],
            "fingerprints": fingerprints,  # hash -> screen_id
            "total_screens": crawl_result.total_screens,
            "total_components": crawl_result.total_components,
        }

        path = _MEMORY_DIR / f"{key}.json"
        try:
            with open(path, "w") as f:
                json.dump(memory, f, indent=2, default=str)
            logger.info(
                f"Saved path memory for '{app_name}' ({key}): "
                f"{crawl_result.total_screens} screens, "
                f"{len(fingerprints)} fingerprints"
            )
        except Exception as e:
            logger.warning(f"Failed to save path memory for {key}: {e}")

        return key

    def load(self, app_name: str, app_url: str = "", package_name: str = "") -> Optional[Dict[str, Any]]:
        """Load prior path memory for an app. Returns None if no memory exists."""
        key = _app_key(app_name, app_url, package_name)
        path = _MEMORY_DIR / f"{key}.json"

        if not path.exists():
            logger.info(f"No path memory for '{app_name}' ({key})")
            return None

        try:
            with open(path) as f:
                memory = json.load(f)
            logger.info(
                f"Loaded path memory for '{app_name}' ({key}): "
                f"{memory.get('total_screens', 0)} screens, "
                f"{len(memory.get('fingerprints', {}))} fingerprints, "
                f"from run {memory.get('run_id', '?')}"
            )
            return memory
        except Exception as e:
            logger.warning(f"Failed to load path memory for {key}: {e}")
            return None

    def get_known_fingerprints(self, app_name: str, app_url: str = "", package_name: str = "") -> Dict[str, str]:
        """Get fingerprints from prior crawl. Returns empty dict if no memory."""
        memory = self.load(app_name, app_url, package_name)
        if memory:
            return memory.get("fingerprints", {})
        return {}

    def get_prior_screens(self, app_name: str, app_url: str = "", package_name: str = "") -> List[Dict]:
        """Get screen data from prior crawl for comparison."""
        memory = self.load(app_name, app_url, package_name)
        if memory:
            return memory.get("screens", [])
        return []

    def compare(self, app_name: str, current_fingerprints: Dict[str, str],
                app_url: str = "", package_name: str = "") -> Dict[str, Any]:
        """Compare current crawl fingerprints against stored memory.

        Returns:
            {
                "has_prior": bool,
                "prior_screens": int,
                "current_screens": int,
                "unchanged": [screen_ids],   # Same fingerprint
                "changed": [screen_ids],     # Fingerprint changed
                "new": [fingerprints],       # Not in prior memory
                "removed": [screen_ids],     # In prior but not current
                "change_ratio": float,       # 0.0 = identical, 1.0 = completely different
            }
        """
        prior_fp = self.get_known_fingerprints(app_name, app_url, package_name)
        if not prior_fp:
            return {
                "has_prior": False,
                "prior_screens": 0,
                "current_screens": len(current_fingerprints),
                "unchanged": [],
                "changed": [],
                "new": list(current_fingerprints.keys()),
                "removed": [],
                "change_ratio": 1.0,
            }

        # Invert: screen_id -> fingerprint for prior
        prior_sid_to_fp = {sid: fp for fp, sid in prior_fp.items()}
        current_sid_to_fp = {sid: fp for fp, sid in current_fingerprints.items()}

        prior_fps_set = set(prior_fp.keys())
        current_fps_set = set(current_fingerprints.keys())

        unchanged_fps = prior_fps_set & current_fps_set
        new_fps = current_fps_set - prior_fps_set
        removed_fps = prior_fps_set - current_fps_set

        unchanged_sids = [prior_fp[fp] for fp in unchanged_fps]
        removed_sids = [prior_fp[fp] for fp in removed_fps]

        total = max(len(prior_fps_set | current_fps_set), 1)
        change_ratio = round(len(new_fps | removed_fps) / total, 3)

        return {
            "has_prior": True,
            "prior_screens": len(prior_fp),
            "current_screens": len(current_fingerprints),
            "unchanged": unchanged_sids,
            "changed": [],  # Would need content comparison, not just fingerprint
            "new": list(new_fps),
            "removed": removed_sids,
            "change_ratio": change_ratio,
        }

    def list_apps(self) -> List[Dict[str, Any]]:
        """List all apps with stored path memory."""
        apps = []
        for p in sorted(_MEMORY_DIR.glob("*.json")):
            try:
                with open(p) as f:
                    d = json.load(f)
                apps.append({
                    "app_key": d.get("app_key", p.stem),
                    "app_name": d.get("app_name", ""),
                    "screens": d.get("total_screens", 0),
                    "fingerprints": len(d.get("fingerprints", {})),
                    "saved_at": d.get("saved_at", ""),
                    "run_id": d.get("run_id", ""),
                })
            except Exception:
                pass
        return apps
