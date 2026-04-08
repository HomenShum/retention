"""
Exploration Memory — durable cache for QA pipeline stages.

Converts expensive one-time exploration into reusable memory:
  Run 1: CRAWL ($) → WORKFLOW ($) → TESTCASE ($) → EXECUTION (device time)
  Run N: cache  → cache  → cache  → EXECUTION only

Memory is keyed by app fingerprint (URL hash + screen count) so:
- Same app, same version → full cache hit (skip crawl + workflow + testcase)
- Same app, UI changed → partial hit (reuse workflow templates, re-crawl changed screens)
- Different app → miss (full pipeline)

Storage: JSON files in backend/data/exploration_memory/
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .schemas import CrawlResult, ScreenNode, TestSuiteResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_MEMORY_DIR = Path(__file__).resolve().parents[3] / "data" / "exploration_memory"
_MEMORY_DIR.mkdir(parents=True, exist_ok=True)

_CRAWL_DIR = _MEMORY_DIR / "crawl"
_CRAWL_DIR.mkdir(parents=True, exist_ok=True)

_WORKFLOW_DIR = _MEMORY_DIR / "workflows"
_WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)

_TESTSUITE_DIR = _MEMORY_DIR / "test_suites"
_TESTSUITE_DIR.mkdir(parents=True, exist_ok=True)

_INDEX_PATH = _MEMORY_DIR / "memory_index.json"


# ---------------------------------------------------------------------------
# Fingerprinting — stable keys for cache lookup
# ---------------------------------------------------------------------------

def app_fingerprint(app_url: str = "", package_name: str = "", app_name: str = "") -> str:
    """Create a stable fingerprint for an app.

    Uses URL or package name as the primary key.
    Returns a short hex hash.
    """
    key = app_url or package_name or app_name
    # Normalize: strip trailing slashes, lowercase
    key = key.rstrip("/").lower()
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def screen_fingerprint(screen: ScreenNode) -> str:
    """Fingerprint a single screen based on name + component types + interactive count.

    Stable across runs if the screen hasn't changed.
    Changes when components are added/removed/retyped.
    """
    comp_sigs = sorted(
        f"{c.element_type}:{c.is_interactive}:{c.text[:20]}"
        for c in screen.components
    )
    sig = f"{screen.screen_name}|{'|'.join(comp_sigs)}"
    return hashlib.sha256(sig.encode()).hexdigest()[:12]


def crawl_fingerprint(crawl_result: CrawlResult) -> str:
    """Fingerprint a crawl result based on screen structure, not content.

    Uses screen names and component TYPE counts (not exact counts) so that
    the same app layout with different data (e.g. different search results)
    still matches. A real UI change (added/removed screen, changed layout)
    changes the fingerprint.
    """
    screen_sigs = sorted(
        # Use screen name + bucketed component count (rounds to nearest 5)
        # so minor content variations don't break the cache
        f"{s.screen_name}:{(len(s.components) // 5) * 5}"
        for s in crawl_result.screens
    )
    sig = "|".join(screen_sigs)
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def build_screen_graph(crawl_result: CrawlResult) -> Dict[str, str]:
    """Build a mapping of screen_id → screen_fingerprint for every screen in a crawl.

    Used for delta comparison between old and new crawls.
    """
    return {
        s.screen_id: screen_fingerprint(s)
        for s in crawl_result.screens
    }


def normalize_crawl_payload(crawl_data: Dict[str, Any], *, app_key: str = "") -> Dict[str, Any]:
    """Normalize crawl hierarchy into a canonical, acyclic crawl-order form.

    Rules:
    - parents must refer to an earlier screen in crawl order
    - self-parent and forward references are cleared
    - navigation_depth is recomputed from the normalized parent chain
    - transitions to unknown screens are dropped
    """
    screens_raw = list(crawl_data.get("screens", []) or [])
    transitions_raw = list(crawl_data.get("transitions", []) or [])
    log_label = app_key or crawl_data.get("app_name") or "unknown"

    normalized_screens: List[Dict[str, Any]] = []
    screen_map: Dict[str, Dict[str, Any]] = {}

    for index, raw_screen in enumerate(screens_raw):
        screen_id = (raw_screen.get("screen_id") or "").strip()
        if not screen_id:
            logger.warning(
                f"Skipping crawl screen with empty screen_id at index={index} for {log_label}"
            )
            continue
        if screen_id in screen_map:
            logger.warning(f"Skipping duplicate screen_id={screen_id} while normalizing {log_label}")
            continue

        normalized_screen = dict(raw_screen)
        declared_parent = raw_screen.get("parent_screen_id") or None
        parent_screen_id = declared_parent

        if parent_screen_id == screen_id:
            logger.warning(f"Clearing self-referential parent for {screen_id} in {log_label}")
            parent_screen_id = None
        elif parent_screen_id and parent_screen_id not in screen_map:
            logger.warning(
                f"Clearing invalid parent_screen_id={parent_screen_id} for {screen_id} in {log_label}"
            )
            parent_screen_id = None

        expected_depth = (
            int(screen_map[parent_screen_id].get("navigation_depth", 0)) + 1
            if parent_screen_id else 0
        )
        if raw_screen.get("navigation_depth", 0) != expected_depth:
            logger.info(
                f"Normalizing navigation_depth for {screen_id} in {log_label}: "
                f"{raw_screen.get('navigation_depth', 0)} -> {expected_depth}"
            )

        normalized_screen["parent_screen_id"] = parent_screen_id
        normalized_screen["navigation_depth"] = expected_depth
        normalized_screen["trigger_action"] = raw_screen.get("trigger_action") or None

        normalized_screens.append(normalized_screen)
        screen_map[screen_id] = normalized_screen

    valid_screen_ids = set(screen_map.keys())
    normalized_transitions: List[Dict[str, Any]] = []
    seen_transition_keys = set()
    for raw_transition in transitions_raw:
        from_screen = raw_transition.get("from_screen") or ""
        to_screen = raw_transition.get("to_screen") or ""
        if not from_screen or not to_screen:
            continue
        if from_screen not in valid_screen_ids or to_screen not in valid_screen_ids:
            logger.warning(
                f"Dropping transition {from_screen}->{to_screen} while normalizing {log_label}"
            )
            continue
        key = (
            from_screen,
            to_screen,
            raw_transition.get("action", ""),
            raw_transition.get("component_id"),
            raw_transition.get("edge_type"),
        )
        if key in seen_transition_keys:
            continue
        seen_transition_keys.add(key)
        normalized_transitions.append(dict(raw_transition))

    total_components = sum(len(screen.get("components", []) or []) for screen in normalized_screens)
    normalized = dict(crawl_data)
    normalized["screens"] = normalized_screens
    normalized["transitions"] = normalized_transitions
    normalized["total_screens"] = len(normalized_screens)
    normalized["total_components"] = total_components
    return normalized


def normalize_crawl_result(crawl_result: CrawlResult, *, app_key: str = "") -> CrawlResult:
    """Return a CrawlResult with normalized hierarchy and recomputed counts."""
    normalized_payload = normalize_crawl_payload(crawl_result.model_dump(), app_key=app_key)
    return CrawlResult(**normalized_payload)


# ---------------------------------------------------------------------------
# Memory Index — tracks what's cached and when
# ---------------------------------------------------------------------------

def _load_index() -> dict:
    if _INDEX_PATH.exists():
        try:
            return json.loads(_INDEX_PATH.read_text())
        except Exception:
            pass
    return {"apps": {}, "stats": {"total_hits": 0, "total_misses": 0, "tokens_saved": 0}}


def _save_index(index: dict) -> None:
    _INDEX_PATH.write_text(json.dumps(index, indent=2, default=str))


def _crawl_apps_from_disk() -> Dict[str, dict]:
    """Build app metadata directly from cached crawl files on disk.

    This keeps dashboard and memory tools honest even when a crawl file exists
    but the app was never inserted into memory_index.json (for example, manual
    demo fixtures or older cached files).
    """
    apps: Dict[str, dict] = {}
    for path in sorted(_CRAWL_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception as exc:
            logger.warning(f"Failed to read crawl metadata from {path.name}: {exc}")
            continue

        crawl = data.get("crawl_data", {})
        screens = crawl.get("screens", [])
        total_screens = data.get("total_screens", len(screens))
        if total_screens <= 0 or not screens:
            continue

        total_components = data.get("total_components")
        if total_components is None:
            total_components = sum(len(screen.get("components", [])) for screen in screens)

        app_key = data.get("app_key") or path.stem
        apps[app_key] = {
            "app_url": data.get("app_url", crawl.get("app_url", "")),
            "app_name": data.get("app_name", crawl.get("app_name", "")),
            "crawl_fingerprint": data.get("crawl_fingerprint", ""),
            "screens": total_screens,
            "components": total_components,
            "screen_graph_size": len(data.get("screen_graph", {})),
            "last_crawl": data.get("stored_at", ""),
            "crawl_count": 1,
        }

    return apps



def load_crawl(app_key: str) -> Optional[Tuple[CrawlResult, str]]:
    """Load a cached crawl result. Returns (CrawlResult, fingerprint) or None.

    Rejects cached crawls with 0 screens (crashed/incomplete crawls).
    """
    path = _CRAWL_DIR / f"{app_key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        crawl_data = data["crawl_data"]
        result = normalize_crawl_result(CrawlResult(**crawl_data), app_key=app_key)
        fp = crawl_fingerprint(result)

        # Reject empty crawls — these were cached from crashed runs
        if result.total_screens == 0 or len(result.screens) == 0:
            logger.warning(
                f"Cache REJECT: crawl for {app_key} has 0 screens "
                f"(likely from a crashed run). Treating as miss."
            )
            # Clean up the invalid cache entry
            path.unlink(missing_ok=True)
            return None

        logger.info(f"Cache HIT: crawl for {app_key} ({result.total_screens} screens)")
        return result, fp
    except Exception as e:
        logger.warning(f"Failed to load crawl cache for {app_key}: {e}")
        return None


# ---------------------------------------------------------------------------
# Workflow Memory — store and retrieve workflow analysis results
# ---------------------------------------------------------------------------

def store_workflows(app_key: str, crawl_fp: str, workflow_json: str, workflow_count: int) -> None:
    """Store workflow analysis results keyed by app + crawl fingerprint."""
    data = {
        "app_key": app_key,
        "crawl_fingerprint": crawl_fp,
        "workflow_count": workflow_count,
        "stored_at": datetime.now(timezone.utc).isoformat(),
        "workflow_data": workflow_json,
    }
    path = _WORKFLOW_DIR / f"{app_key}_{crawl_fp}.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    logger.info(f"Stored workflow memory: app={app_key}, crawl_fp={crawl_fp}, workflows={workflow_count}")


def load_workflows(app_key: str, crawl_fp: str) -> Optional[str]:
    """Load cached workflow analysis. Returns raw JSON string or None."""
    path = _WORKFLOW_DIR / f"{app_key}_{crawl_fp}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if data.get("crawl_fingerprint") != crawl_fp:
            logger.info(f"Workflow cache STALE for {app_key} (crawl changed)")
            return None
        logger.info(f"Cache HIT: workflows for {app_key} ({data.get('workflow_count')} workflows)")
        return data["workflow_data"]
    except Exception as e:
        logger.warning(f"Failed to load workflow cache for {app_key}: {e}")
        return None


# ---------------------------------------------------------------------------
# Test Suite Memory — store and retrieve generated test cases
# ---------------------------------------------------------------------------

def store_test_suite(app_key: str, crawl_fp: str, test_suite: TestSuiteResult) -> None:
    """Store a generated test suite keyed by app + crawl fingerprint."""
    data = {
        "app_key": app_key,
        "crawl_fingerprint": crawl_fp,
        "total_tests": test_suite.total_tests,
        "stored_at": datetime.now(timezone.utc).isoformat(),
        "test_suite_data": test_suite.model_dump(),
    }
    path = _TESTSUITE_DIR / f"{app_key}_{crawl_fp}.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    logger.info(f"Stored test suite memory: app={app_key}, tests={test_suite.total_tests}")


def load_test_suite(app_key: str, crawl_fp: str) -> Optional[TestSuiteResult]:
    """Load a cached test suite. Returns TestSuiteResult or None."""
    path = _TESTSUITE_DIR / f"{app_key}_{crawl_fp}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if data.get("crawl_fingerprint") != crawl_fp:
            return None
        suite = TestSuiteResult(**data["test_suite_data"])
        logger.info(f"Cache HIT: test suite for {app_key} ({suite.total_tests} tests)")
        return suite
    except Exception as e:
        logger.warning(f"Failed to load test suite cache for {app_key}: {e}")
        return None


# ---------------------------------------------------------------------------
# Pipeline Memory Check — decides what to skip
# ---------------------------------------------------------------------------

class MemoryCheckResult:
    """Result of checking exploration memory for a given app."""

    def __init__(self):
        self.crawl_hit = False
        self.crawl_result: Optional[CrawlResult] = None
        self.crawl_fingerprint = ""
        self.workflow_hit = False
        self.workflow_json: Optional[str] = None
        self.test_suite_hit = False
        self.test_suite: Optional[TestSuiteResult] = None
        self.stages_skipped: List[str] = []
        self.stages_needed: List[str] = []
        self.estimated_tokens_saved = 0
        self.estimated_cost_saved = 0.0

    @property
    def full_hit(self) -> bool:
        return self.crawl_hit and self.workflow_hit and self.test_suite_hit

    def summary(self) -> dict:
        return {
            "crawl_cached": self.crawl_hit,
            "workflow_cached": self.workflow_hit,
            "test_suite_cached": self.test_suite_hit,
            "stages_skipped": self.stages_skipped,
            "stages_needed": self.stages_needed,
            "estimated_tokens_saved": self.estimated_tokens_saved,
            "estimated_cost_saved_usd": round(self.estimated_cost_saved, 6),
        }


def check_memory(app_url: str = "", package_name: str = "", app_name: str = "") -> MemoryCheckResult:
    """Check what's cached for a given app.

    Returns a MemoryCheckResult telling the pipeline which stages can be skipped.
    """
    result = MemoryCheckResult()
    app_key = app_fingerprint(app_url, package_name, app_name)

    # Check crawl cache
    crawl_data = load_crawl(app_key)
    if crawl_data:
        result.crawl_hit = True
        result.crawl_result, result.crawl_fingerprint = crawl_data
        result.stages_skipped.append("CRAWL")
        # Measured: crawl uses ~8-11K tokens per run (from 8+ verified runs)
        # Using conservative upper bound of 11K
        result.estimated_tokens_saved += 11000
        result.estimated_cost_saved += 0.005  # ~$0.005 at gpt-5.4-mini pricing

        # Check workflow cache (requires matching crawl fingerprint)
        wf_json = load_workflows(app_key, result.crawl_fingerprint)
        if wf_json:
            result.workflow_hit = True
            result.workflow_json = wf_json
            result.stages_skipped.append("WORKFLOW")
            result.estimated_tokens_saved += 8000
            result.estimated_cost_saved += 0.003

            # Check test suite cache
            suite = load_test_suite(app_key, result.crawl_fingerprint)
            if suite:
                result.test_suite_hit = True
                result.test_suite = suite
                result.stages_skipped.append("TESTCASE")
                result.estimated_tokens_saved += 12000
                result.estimated_cost_saved += 0.005
    else:
        result.stages_needed.append("CRAWL")

    if not result.workflow_hit:
        result.stages_needed.append("WORKFLOW")
    if not result.test_suite_hit:
        result.stages_needed.append("TESTCASE")

    # EXECUTION always runs (that's the whole point — verify on device)
    result.stages_needed.append("EXECUTION")

    # Update stats
    index = _load_index()
    if result.crawl_hit:
        index["stats"]["total_hits"] = index["stats"].get("total_hits", 0) + 1
        index["stats"]["tokens_saved"] = index["stats"].get("tokens_saved", 0) + result.estimated_tokens_saved
    else:
        index["stats"]["total_misses"] = index["stats"].get("total_misses", 0) + 1
    _save_index(index)

    return result


# ---------------------------------------------------------------------------
# Memory stats — for MCP tools and dashboard
# ---------------------------------------------------------------------------

def get_memory_stats() -> dict:
    """Get exploration memory statistics."""
    index = _load_index()
    crawl_files = list(_CRAWL_DIR.glob("*.json"))
    workflow_files = list(_WORKFLOW_DIR.glob("*.json"))
    suite_files = list(_TESTSUITE_DIR.glob("*.json"))
    apps = dict(index.get("apps", {}))

    for app_key, crawl_app in _crawl_apps_from_disk().items():
        indexed = apps.get(app_key, {})
        apps[app_key] = {
            **crawl_app,
            **indexed,
            # Prefer live crawl-derived graph stats over stale index entries.
            "app_url": indexed.get("app_url") or crawl_app.get("app_url", ""),
            "app_name": indexed.get("app_name") or crawl_app.get("app_name", ""),
            "crawl_fingerprint": indexed.get("crawl_fingerprint") or crawl_app.get("crawl_fingerprint", ""),
            "screens": crawl_app.get("screens", indexed.get("screens", 0)),
            "components": crawl_app.get("components", indexed.get("components", 0)),
            "screen_graph_size": crawl_app.get("screen_graph_size", indexed.get("screen_graph_size", 0)),
            "last_crawl": indexed.get("last_crawl") or crawl_app.get("last_crawl", ""),
            "crawl_count": max(indexed.get("crawl_count", 0), crawl_app.get("crawl_count", 0)),
        }

    return {
        "apps_cached": len(apps),
        "crawl_results_cached": len(crawl_files),
        "workflow_results_cached": len(workflow_files),
        "test_suites_cached": len(suite_files),
        "total_cache_hits": index.get("stats", {}).get("total_hits", 0),
        "total_cache_misses": index.get("stats", {}).get("total_misses", 0),
        "estimated_tokens_saved": index.get("stats", {}).get("tokens_saved", 0),
        "hit_rate": (
            round(index["stats"]["total_hits"] / max(1, index["stats"]["total_hits"] + index["stats"]["total_misses"]), 4)
            if index.get("stats") else 0.0
        ),
        "apps": apps,
    }


# ---------------------------------------------------------------------------
# Delta Crawl — diff old vs new crawl, identify changed/added/removed screens
# ---------------------------------------------------------------------------

class DeltaCrawlResult:
    """Result of comparing a fresh crawl against cached memory."""

    def __init__(self):
        self.added_screens: List[str] = []        # screen_ids only in new crawl
        self.removed_screens: List[str] = []      # screen_ids only in old crawl
        self.changed_screens: List[str] = []      # screen_ids with different fingerprints
        self.unchanged_screens: List[str] = []    # screen_ids with matching fingerprints
        self.affected_workflows: List[str] = []   # workflow_ids touching changed/added/removed screens
        self.affected_tests: List[str] = []       # test case IDs touching affected screens

    @property
    def has_changes(self) -> bool:
        return bool(self.added_screens or self.removed_screens or self.changed_screens)

    @property
    def changed_screen_set(self) -> set:
        return set(self.added_screens + self.removed_screens + self.changed_screens)

    def summary(self) -> dict:
        return {
            "added": len(self.added_screens),
            "removed": len(self.removed_screens),
            "changed": len(self.changed_screens),
            "unchanged": len(self.unchanged_screens),
            "total_affected": len(self.changed_screen_set),
            "affected_workflows": len(self.affected_workflows),
            "affected_tests": len(self.affected_tests),
        }


def delta_crawl(
    old_crawl: CrawlResult,
    new_crawl: CrawlResult,
    old_workflows_json: Optional[str] = None,
    old_test_suite: Optional[TestSuiteResult] = None,
) -> DeltaCrawlResult:
    """Compare a fresh crawl against a cached crawl.

    Returns which screens changed, and which workflows/tests are affected.
    The pipeline can then:
    - Keep unchanged screens from cache
    - Re-process only changed/added screens
    - Only regenerate workflows/tests that touch affected screens
    """
    result = DeltaCrawlResult()

    old_graph = build_screen_graph(old_crawl)
    new_graph = build_screen_graph(new_crawl)

    old_ids = set(old_graph.keys())
    new_ids = set(new_graph.keys())

    result.added_screens = sorted(new_ids - old_ids)
    result.removed_screens = sorted(old_ids - new_ids)

    for sid in old_ids & new_ids:
        if old_graph[sid] != new_graph[sid]:
            result.changed_screens.append(sid)
        else:
            result.unchanged_screens.append(sid)

    result.changed_screens.sort()
    result.unchanged_screens.sort()

    affected = result.changed_screen_set

    # Find affected workflows
    if old_workflows_json and affected:
        try:
            wf_data = json.loads(old_workflows_json)
            workflows = wf_data if isinstance(wf_data, list) else wf_data.get("workflows", [])
            for wf in workflows:
                screens_involved = set(wf.get("screens_involved", []))
                if screens_involved & affected:
                    result.affected_workflows.append(wf.get("workflow_id", wf.get("name", "unknown")))
        except Exception as e:
            logger.warning(f"Failed to parse workflows for delta: {e}")

    # Find affected test cases
    if old_test_suite and affected:
        for tc in old_test_suite.test_cases:
            tc_screens = set()
            if hasattr(tc, "workflow") and tc.workflow:
                # Test cases reference workflows which reference screens
                tc_screens.add(tc.workflow)
            if hasattr(tc, "screens_involved"):
                tc_screens.update(tc.screens_involved)
            if tc_screens & affected:
                result.affected_tests.append(tc.test_id)

    logger.info(
        f"Delta crawl: +{len(result.added_screens)} added, "
        f"-{len(result.removed_screens)} removed, "
        f"~{len(result.changed_screens)} changed, "
        f"={len(result.unchanged_screens)} unchanged"
    )
    return result


def merge_crawl(old_crawl: CrawlResult, new_crawl: CrawlResult, delta: DeltaCrawlResult) -> CrawlResult:
    """Merge a partial re-crawl with cached data.

    Keeps unchanged screens from old_crawl, takes changed/added from new_crawl,
    drops removed screens. Updates transitions accordingly.
    """
    affected = delta.changed_screen_set

    # Screens: keep unchanged from old, take everything else from new
    merged_screens = []
    new_screen_map = {s.screen_id: s for s in new_crawl.screens}
    old_screen_map = {s.screen_id: s for s in old_crawl.screens}

    # Add unchanged screens from old crawl
    for sid in delta.unchanged_screens:
        if sid in old_screen_map:
            merged_screens.append(old_screen_map[sid])

    # Add changed + added screens from new crawl
    for sid in sorted(set(delta.changed_screens + delta.added_screens)):
        if sid in new_screen_map:
            merged_screens.append(new_screen_map[sid])

    # Transitions: keep ones between unchanged screens, take rest from new crawl
    merged_transitions = []
    new_transition_set = {(t.from_screen, t.to_screen) for t in new_crawl.transitions}
    for t in old_crawl.transitions:
        if t.from_screen not in affected and t.to_screen not in affected:
            merged_transitions.append(t)

    for t in new_crawl.transitions:
        if t.from_screen in affected or t.to_screen in affected:
            merged_transitions.append(t)

    return CrawlResult(
        app_name=new_crawl.app_name or old_crawl.app_name,
        package_name=new_crawl.package_name or old_crawl.package_name,
        screens=merged_screens,
        transitions=merged_transitions,
        total_screens=len(merged_screens),
        total_components=sum(len(s.components) for s in merged_screens),
    )


def invalidate_affected_only(
    app_key: str,
    crawl_fp: str,
    affected_workflows: List[str],
    affected_tests: List[str],
) -> dict:
    """Invalidate only workflows/tests that touch changed screens.

    Unlike invalidate_app() which nukes everything, this preserves
    workflows and tests for unchanged screens.
    """
    removed = {"workflows_invalidated": 0, "tests_invalidated": 0}

    # Load existing workflow data, remove affected entries, re-save
    wf_path = _WORKFLOW_DIR / f"{app_key}_{crawl_fp}.json"
    if wf_path.exists() and affected_workflows:
        try:
            data = json.loads(wf_path.read_text())
            wf_raw = data.get("workflow_data", "")
            wf_list = json.loads(wf_raw) if isinstance(wf_raw, str) else wf_raw
            workflows = wf_list if isinstance(wf_list, list) else wf_list.get("workflows", [])
            original_count = len(workflows)
            affected_set = set(affected_workflows)
            workflows = [
                wf for wf in workflows
                if wf.get("workflow_id", wf.get("name")) not in affected_set
            ]
            removed["workflows_invalidated"] = original_count - len(workflows)
            data["workflow_data"] = json.dumps(workflows)
            data["workflow_count"] = len(workflows)
            wf_path.write_text(json.dumps(data, indent=2, default=str))
        except Exception as e:
            logger.warning(f"Failed to selectively invalidate workflows: {e}")

    # Load existing test suite, remove affected tests, re-save
    ts_path = _TESTSUITE_DIR / f"{app_key}_{crawl_fp}.json"
    if ts_path.exists() and affected_tests:
        try:
            data = json.loads(ts_path.read_text())
            suite_data = data.get("test_suite_data", {})
            test_cases = suite_data.get("test_cases", [])
            original_count = len(test_cases)
            affected_set = set(affected_tests)
            test_cases = [
                tc for tc in test_cases
                if tc.get("test_id") not in affected_set
            ]
            removed["tests_invalidated"] = original_count - len(test_cases)
            suite_data["test_cases"] = test_cases
            suite_data["total_tests"] = len(test_cases)
            data["test_suite_data"] = suite_data
            data["total_tests"] = len(test_cases)
            ts_path.write_text(json.dumps(data, indent=2, default=str))
        except Exception as e:
            logger.warning(f"Failed to selectively invalidate tests: {e}")

    logger.info(f"Scoped invalidation for {app_key}: {removed}")
    return removed


# ---------------------------------------------------------------------------
# Store screen graph alongside crawl (for future delta comparisons)
# ---------------------------------------------------------------------------

def store_crawl(app_key: str, crawl_result: CrawlResult, app_url: str = "", app_name: str = "") -> str:
    """Store a crawl result in memory. Returns the crawl fingerprint.

    Also stores per-screen fingerprint graph for delta crawl comparisons.
    Refuses to cache crawls with 0 screens (crashed/empty crawls).
    """
    crawl_result = normalize_crawl_result(crawl_result, app_key=app_key)

    if crawl_result.total_screens == 0 or len(crawl_result.screens) == 0:
        logger.warning(
            f"Refusing to cache crawl for {app_key}: 0 screens detected "
            f"(likely crashed or empty crawl). Skipping storage."
        )
        return ""

    fp = crawl_fingerprint(crawl_result)
    sg = build_screen_graph(crawl_result)
    data = {
        "app_key": app_key,
        "crawl_fingerprint": fp,
        "screen_graph": sg,
        "app_url": app_url,
        "app_name": app_name or crawl_result.app_name,
        "total_screens": crawl_result.total_screens,
        "total_components": crawl_result.total_components,
        "stored_at": datetime.now(timezone.utc).isoformat(),
        "crawl_data": crawl_result.model_dump(),
    }
    path = _CRAWL_DIR / f"{app_key}.json"

    # ── History: archive previous crawl before overwriting ──
    if path.exists():
        try:
            history_dir = _CRAWL_DIR / "history"
            history_dir.mkdir(exist_ok=True)
            prev = json.loads(path.read_text())
            prev_ts = prev.get("stored_at", "unknown")[:19].replace(":", "").replace("-", "")
            history_path = history_dir / f"{app_key}_{prev_ts}.json"
            if not history_path.exists():
                history_path.write_text(json.dumps(prev, indent=2, default=str))
                logger.info(f"Archived previous crawl → {history_path.name}")
        except Exception as e:
            logger.warning(f"Failed to archive previous crawl: {e}")

    path.write_text(json.dumps(data, indent=2, default=str))

    # Update index
    index = _load_index()
    index["apps"][app_key] = {
        "app_url": app_url,
        "app_name": app_name or crawl_result.app_name,
        "crawl_fingerprint": fp,
        "screens": crawl_result.total_screens,
        "components": crawl_result.total_components,
        "screen_graph_size": len(sg),
        "last_crawl": datetime.now(timezone.utc).isoformat(),
        "crawl_count": index["apps"].get(app_key, {}).get("crawl_count", 0) + 1,
    }
    _save_index(index)

    logger.info(f"Stored crawl memory: app={app_key}, fp={fp}, screens={crawl_result.total_screens}, graph_size={len(sg)}")

    # ROP invalidation: retire any ROPs whose screens have changed
    try:
        from .rop_manager import ROPManager
        retired = ROPManager().check_all_rops_for_app(app_key, sg)
        if retired:
            logger.info(f"ROP invalidation: retired {len(retired)} patterns for app {app_key}")
    except Exception as e:
        logger.debug(f"ROP invalidation check skipped: {e}")

    return fp


def get_screen_fingerprints(app_key: str) -> Dict[str, str]:
    """Get the screen fingerprint graph from a cached crawl.

    Returns a dict mapping screen_id -> screen_fingerprint.
    Used by crawl_tools to detect unchanged screens and reduce BFS depth.
    Returns empty dict if no cache exists or cache is invalid.
    """
    path = _CRAWL_DIR / f"{app_key}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        sg = data.get("screen_graph", {})
        if not sg:
            return {}
        # Verify the crawl has actual screens (not a crashed empty crawl)
        if data.get("total_screens", 0) == 0:
            return {}
        logger.info(f"Loaded {len(sg)} screen fingerprints for {app_key}")
        return sg
    except Exception:
        return {}


def get_crawl_fingerprint_map(app_key: str) -> Dict[str, str]:
    """Get the BFS fingerprint -> screen_id map for a cached crawl.

    Used by crawl_tools to check if a newly registered screen was
    seen in a prior crawl. If the screen's BFS fingerprint matches,
    the crawl can reduce exploration depth for that screen.

    Returns dict mapping fingerprint_hash -> screen_id.
    """
    path = _CRAWL_DIR / f"{app_key}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        # screen_graph maps screen_id -> screen_fingerprint
        # We need the reverse: screen_fingerprint -> screen_id
        sg = data.get("screen_graph", {})
        if not sg or data.get("total_screens", 0) == 0:
            return {}
        reversed_map = {fp: sid for sid, fp in sg.items()}
        logger.info(f"Loaded {len(reversed_map)} BFS fingerprints for {app_key}")
        return reversed_map
    except Exception:
        return {}


def invalidate_app(app_url: str = "", package_name: str = "") -> dict:
    """Invalidate all cached memory for an app (force re-exploration)."""
    app_key = app_fingerprint(app_url, package_name)

    removed = {"crawl": False, "workflows": 0, "test_suites": 0}

    # Remove crawl
    crawl_path = _CRAWL_DIR / f"{app_key}.json"
    if crawl_path.exists():
        crawl_path.unlink()
        removed["crawl"] = True

    # Remove workflows
    for wf_path in _WORKFLOW_DIR.glob(f"{app_key}_*.json"):
        wf_path.unlink()
        removed["workflows"] += 1

    # Remove test suites
    for ts_path in _TESTSUITE_DIR.glob(f"{app_key}_*.json"):
        ts_path.unlink()
        removed["test_suites"] += 1

    # Update index
    index = _load_index()
    if app_key in index.get("apps", {}):
        del index["apps"][app_key]
        _save_index(index)

    logger.info(f"Invalidated memory for {app_key}: {removed}")
    return removed
