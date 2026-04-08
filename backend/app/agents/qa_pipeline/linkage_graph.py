"""Linkage Graph — connects QA runs to features, commits, code, and design.

Maps test cases and screens to product context so that when code changes,
we can infer which QA paths need re-verification.

Storage: data/linkage/graph.json

Graph structure:
  feature → [screens, test_cases, code_files, commits, prd_sections]
  screen  → [features, test_cases, code_files]
  commit  → [features, affected_screens, run_ids]
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_LINKAGE_DIR = _DATA_DIR / "linkage"
_LINKAGE_DIR.mkdir(parents=True, exist_ok=True)
_GRAPH_PATH = _LINKAGE_DIR / "graph.json"


def _load_graph() -> dict:
    """Load the linkage graph from disk."""
    if _GRAPH_PATH.exists():
        try:
            return json.loads(_GRAPH_PATH.read_text())
        except Exception:
            pass
    return {
        "features": {},
        "screens": {},
        "commits": {},
        "code_files": {},
        "code_symbols": {},   # entity_id → {kind, file_path, symbol_name, features, screens}
        "workflows": {},      # workflow_id → {feature_ids, screen_ids, code_anchors}
        "runs": {},
        "updated_at": None,
    }


def _save_graph(graph: dict) -> None:
    """Persist the linkage graph."""
    graph["updated_at"] = datetime.now(timezone.utc).isoformat()
    _GRAPH_PATH.write_text(json.dumps(graph, indent=2, default=str))



def register_feature(
    feature_id: str,
    name: str,
    description: str = "",
    prd_section: str = "",
    design_ref: str = "",
) -> Dict[str, Any]:
    """Register a product feature in the linkage graph."""
    graph = _load_graph()

    if feature_id not in graph["features"]:
        graph["features"][feature_id] = {
            "name": name,
            "description": description,
            "prd_section": prd_section,
            "design_ref": design_ref,
            "screens": [],
            "test_cases": [],
            "code_files": [],
            "commits": [],
            "runs": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    else:
        f = graph["features"][feature_id]
        f["name"] = name
        if description:
            f["description"] = description
        if prd_section:
            f["prd_section"] = prd_section
        if design_ref:
            f["design_ref"] = design_ref

    _save_graph(graph)
    logger.info(f"Feature registered: {feature_id} ({name})")
    return graph["features"][feature_id]


def link_screen_to_feature(screen_id: str, feature_id: str, screen_name: str = "") -> None:
    """Link a discovered screen to a product feature."""
    graph = _load_graph()

    # Ensure feature exists
    if feature_id not in graph["features"]:
        graph["features"][feature_id] = {
            "name": feature_id, "screens": [], "test_cases": [],
            "code_files": [], "commits": [], "runs": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    # Ensure screen exists
    if screen_id not in graph["screens"]:
        graph["screens"][screen_id] = {
            "name": screen_name or screen_id,
            "features": [],
            "test_cases": [],
            "code_files": [],
        }

    # Bidirectional link
    if screen_id not in graph["features"][feature_id]["screens"]:
        graph["features"][feature_id]["screens"].append(screen_id)
    if feature_id not in graph["screens"][screen_id]["features"]:
        graph["screens"][screen_id]["features"].append(feature_id)

    _save_graph(graph)


def link_code_to_feature(file_path: str, feature_id: str) -> None:
    """Link a code file to a product feature."""
    graph = _load_graph()

    if feature_id not in graph["features"]:
        graph["features"][feature_id] = {
            "name": feature_id, "screens": [], "test_cases": [],
            "code_files": [], "commits": [], "runs": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    if file_path not in graph["code_files"]:
        graph["code_files"][file_path] = {
            "features": [],
            "screens": [],
            "commits": [],
        }

    if file_path not in graph["features"][feature_id]["code_files"]:
        graph["features"][feature_id]["code_files"].append(file_path)
    if feature_id not in graph["code_files"][file_path]["features"]:
        graph["code_files"][file_path]["features"].append(feature_id)

    _save_graph(graph)


def link_commit_to_feature(
    commit_hash: str,
    feature_id: str,
    message: str = "",
    files_changed: Optional[List[str]] = None,
) -> None:
    """Link a git commit to a product feature."""
    graph = _load_graph()

    if commit_hash not in graph["commits"]:
        graph["commits"][commit_hash] = {
            "message": message,
            "feature_ids": [],
            "files_changed": files_changed or [],
            "affected_screens": [],
            "run_ids": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    if feature_id not in graph["commits"][commit_hash]["feature_ids"]:
        graph["commits"][commit_hash]["feature_ids"].append(feature_id)

    if feature_id in graph["features"]:
        if commit_hash not in graph["features"][feature_id]["commits"]:
            graph["features"][feature_id]["commits"].append(commit_hash)

    _save_graph(graph)


def link_run_to_feature(run_id: str, feature_id: str) -> None:
    """Link a QA pipeline run to a product feature."""
    graph = _load_graph()

    if run_id not in graph["runs"]:
        graph["runs"][run_id] = {
            "feature_ids": [],
            "screens_tested": [],
            "test_cases": [],
        }

    if feature_id not in graph["runs"][run_id]["feature_ids"]:
        graph["runs"][run_id]["feature_ids"].append(feature_id)

    if feature_id in graph["features"]:
        if run_id not in graph["features"][feature_id]["runs"]:
            graph["features"][feature_id]["runs"].append(run_id)

    _save_graph(graph)


def get_affected_features(files_changed: List[str]) -> List[Dict[str, Any]]:
    """Given a list of changed code files, return affected features.

    This is the core "affected-flow inference" — when code changes,
    which product features and QA paths need re-verification?
    """
    graph = _load_graph()
    affected = {}

    for file_path in files_changed:
        # Direct file→feature links
        if file_path in graph["code_files"]:
            for fid in graph["code_files"][file_path]["features"]:
                if fid not in affected and fid in graph["features"]:
                    feat = graph["features"][fid]
                    affected[fid] = {
                        "feature_id": fid,
                        "name": feat.get("name", ""),
                        "reason": f"Code file changed: {file_path}",
                        "affected_screens": feat.get("screens", []),
                        "test_cases": feat.get("test_cases", []),
                        "last_run": feat.get("runs", [])[-1] if feat.get("runs") else None,
                    }

        # Fuzzy match: check if file path substring matches any registered code file
        for registered_path, code_data in graph["code_files"].items():
            if file_path.endswith(registered_path) or registered_path.endswith(file_path):
                for fid in code_data["features"]:
                    if fid not in affected and fid in graph["features"]:
                        feat = graph["features"][fid]
                        affected[fid] = {
                            "feature_id": fid,
                            "name": feat.get("name", ""),
                            "reason": f"Code file changed: {file_path} (fuzzy match to {registered_path})",
                            "affected_screens": feat.get("screens", []),
                            "test_cases": feat.get("test_cases", []),
                            "last_run": feat.get("runs", [])[-1] if feat.get("runs") else None,
                        }

    return list(affected.values())


def get_rerun_suggestions(commit_hash: str = "", files_changed: Optional[List[str]] = None) -> Dict[str, Any]:
    """Suggest which QA runs should be re-executed based on a commit or file changes.

    Returns:
        {
            "affected_features": [...],
            "screens_to_retest": [...],
            "suggested_reruns": [...],
            "confidence": "high"|"medium"|"low",
        }
    """
    graph = _load_graph()

    changed_files = files_changed or []
    if commit_hash and commit_hash in graph["commits"]:
        changed_files = graph["commits"][commit_hash].get("files_changed", [])

    affected = get_affected_features(changed_files)

    # Collect all affected screens
    screens_to_retest = set()
    suggested_reruns = set()
    for feat in affected:
        screens_to_retest.update(feat.get("affected_screens", []))
        if feat.get("last_run"):
            suggested_reruns.add(feat["last_run"])

    # Confidence based on linkage completeness
    total_files = len(graph.get("code_files", {}))
    if total_files == 0:
        confidence = "low"
    elif len(affected) > 0:
        confidence = "high"
    else:
        confidence = "medium"

    return {
        "commit": commit_hash,
        "files_changed": changed_files,
        "affected_features": affected,
        "screens_to_retest": list(screens_to_retest),
        "suggested_reruns": list(suggested_reruns),
        "confidence": confidence,
    }


def get_graph_stats() -> Dict[str, Any]:
    """Return linkage graph statistics."""
    graph = _load_graph()
    return {
        "features": len(graph.get("features", {})),
        "screens": len(graph.get("screens", {})),
        "commits": len(graph.get("commits", {})),
        "code_files": len(graph.get("code_files", {})),
        "code_symbols": len(graph.get("code_symbols", {})),
        "workflows": len(graph.get("workflows", {})),
        "runs": len(graph.get("runs", {})),
        "updated_at": graph.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# Symbol-level linking (Phase 2 upgrade)
# ---------------------------------------------------------------------------

def register_code_anchor(
    entity_id: str,
    kind: str,
    file_path: str,
    symbol_name: str,
    route_path: str = "",
    http_method: str = "",
    feature_hints: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Register or update a code entity (symbol/route/component) in the graph."""
    graph = _load_graph()
    if "code_symbols" not in graph:
        graph["code_symbols"] = {}

    if entity_id not in graph["code_symbols"]:
        graph["code_symbols"][entity_id] = {
            "kind": kind,
            "file_path": file_path,
            "symbol_name": symbol_name,
            "route_path": route_path,
            "http_method": http_method,
            "feature_hints": feature_hints or [],
            "features": [],
            "screens": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    else:
        sym = graph["code_symbols"][entity_id]
        sym.update({
            "kind": kind,
            "file_path": file_path,
            "symbol_name": symbol_name,
        })

    _save_graph(graph)
    return graph["code_symbols"][entity_id]


def link_symbol_to_feature(entity_id: str, feature_id: str) -> None:
    """Bidirectional edge: code symbol ↔ feature."""
    graph = _load_graph()

    if "code_symbols" not in graph:
        graph["code_symbols"] = {}

    if entity_id not in graph["code_symbols"]:
        graph["code_symbols"][entity_id] = {
            "kind": "unknown", "file_path": "", "symbol_name": entity_id,
            "features": [], "screens": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    sym = graph["code_symbols"][entity_id]
    if feature_id not in sym.get("features", []):
        sym.setdefault("features", []).append(feature_id)

    if feature_id in graph.get("features", {}):
        feat = graph["features"][feature_id]
        if entity_id not in feat.get("code_symbols", []):
            feat.setdefault("code_symbols", []).append(entity_id)

    _save_graph(graph)


def link_symbol_to_screen(
    entity_id: str,
    screen_id: str,
    confidence: str = "medium",
) -> None:
    """Bidirectional edge: code symbol ↔ screen, with confidence label."""
    graph = _load_graph()

    if "code_symbols" not in graph:
        graph["code_symbols"] = {}

    if entity_id in graph["code_symbols"]:
        sym = graph["code_symbols"][entity_id]
        entry = {"screen_id": screen_id, "confidence": confidence}
        existing = [e["screen_id"] for e in sym.get("screens", []) if isinstance(e, dict)]
        if screen_id not in existing:
            sym.setdefault("screens", []).append(entry)

    if screen_id in graph.get("screens", {}):
        scr = graph["screens"][screen_id]
        code_syms = scr.setdefault("code_symbols", [])
        if entity_id not in code_syms:
            code_syms.append(entity_id)

    _save_graph(graph)


def link_workflow_to_feature(workflow_id: str, feature_id: str) -> None:
    """Register a workflow→feature relationship."""
    graph = _load_graph()

    if "workflows" not in graph:
        graph["workflows"] = {}

    if workflow_id not in graph["workflows"]:
        graph["workflows"][workflow_id] = {
            "feature_ids": [],
            "screen_ids": [],
            "code_anchors": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    wf = graph["workflows"][workflow_id]
    if feature_id not in wf.get("feature_ids", []):
        wf.setdefault("feature_ids", []).append(feature_id)

    if feature_id in graph.get("features", {}):
        feat = graph["features"][feature_id]
        feat.setdefault("workflow_ids", [])
        if workflow_id not in feat["workflow_ids"]:
            feat["workflow_ids"].append(workflow_id)

    _save_graph(graph)


def link_workflow_to_screen(workflow_id: str, screen_id: str) -> None:
    """Register a workflow→screen relationship."""
    graph = _load_graph()

    if "workflows" not in graph:
        graph["workflows"] = {}

    if workflow_id not in graph.get("workflows", {}):
        graph["workflows"][workflow_id] = {
            "feature_ids": [], "screen_ids": [], "code_anchors": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    wf = graph["workflows"][workflow_id]
    if screen_id not in wf.get("screen_ids", []):
        wf.setdefault("screen_ids", []).append(screen_id)

    _save_graph(graph)


def get_affected_features(files_changed: List[str]) -> List[Dict[str, Any]]:
    """Given a list of changed code files, return affected features.

    Upgraded to match via:
      1. Direct file→feature links (code_files map)
      2. Symbol-level links (code_symbols map — file_path match)
      3. Fuzzy suffix match on registered code_files
    """
    graph = _load_graph()
    affected: Dict[str, Dict[str, Any]] = {}

    code_symbols = graph.get("code_symbols", {})
    features = graph.get("features", {})

    def _add_affected(fid: str, reason: str) -> None:
        if fid in affected or fid not in features:
            return
        feat = features[fid]
        affected[fid] = {
            "feature_id": fid,
            "name": feat.get("name", ""),
            "reason": reason,
            "affected_screens": feat.get("screens", []),
            "test_cases": feat.get("test_cases", []),
            "workflow_ids": feat.get("workflow_ids", []),
            "last_run": feat.get("runs", [])[-1] if feat.get("runs") else None,
        }

    for file_path in files_changed:
        # 1. Direct file→feature links
        if file_path in graph.get("code_files", {}):
            for fid in graph["code_files"][file_path].get("features", []):
                _add_affected(fid, f"Code file changed: {file_path}")

        # 2. Symbol-level: find any symbols whose file_path matches
        for entity_id, sym in code_symbols.items():
            sym_path = sym.get("file_path", "")
            if sym_path and (sym_path == file_path or sym_path.endswith(file_path) or file_path.endswith(sym_path)):
                for fid in sym.get("features", []):
                    _add_affected(
                        fid,
                        f"Symbol '{sym.get('symbol_name', entity_id)}' in {file_path} changed",
                    )

        # 3. Fuzzy suffix match on code_files
        for registered_path, code_data in graph.get("code_files", {}).items():
            if file_path.endswith(registered_path) or registered_path.endswith(file_path):
                for fid in code_data.get("features", []):
                    _add_affected(fid, f"Code file changed: {file_path} (fuzzy match to {registered_path})")

    return list(affected.values())


def get_workflow_rerun_suggestions(
    files_changed: Optional[List[str]] = None,
    commit_hash: str = "",
) -> Dict[str, Any]:
    """Return which workflows should be re-run after a set of file changes.

    Extends get_rerun_suggestions with workflow-level recommendations.
    """
    graph = _load_graph()

    changed_files = files_changed or []
    if commit_hash and commit_hash in graph.get("commits", {}):
        changed_files = graph["commits"][commit_hash].get("files_changed", [])

    affected = get_affected_features(changed_files)

    screens_to_retest: Set[str] = set()
    suggested_reruns: Set[str] = set()
    workflow_ids: Set[str] = set()

    for feat in affected:
        screens_to_retest.update(feat.get("affected_screens", []))
        if feat.get("last_run"):
            suggested_reruns.add(feat["last_run"])
        workflow_ids.update(feat.get("workflow_ids", []))

    # Also look up any workflows directly mapped to affected screens
    for wf_id, wf_data in graph.get("workflows", {}).items():
        for sid in wf_data.get("screen_ids", []):
            if sid in screens_to_retest:
                workflow_ids.add(wf_id)

    total_files = len(graph.get("code_files", {})) + len(graph.get("code_symbols", {}))
    if total_files == 0:
        confidence = "low"
    elif len(affected) > 0:
        confidence = "high"
    else:
        confidence = "medium"

    return {
        "commit": commit_hash,
        "files_changed": changed_files,
        "affected_features": affected,
        "screens_to_retest": list(screens_to_retest),
        "suggested_reruns": list(suggested_reruns),
        "workflow_ids": list(workflow_ids),
        "confidence": confidence,
    }

