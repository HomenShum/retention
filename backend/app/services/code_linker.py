"""Code Linker — infers which code entities likely produced a given screen.

Takes runtime signals (screen name, URL path, heading text, button labels,
data-testid attributes) and scores them against the indexed code entities
to produce structured CodeAnchor references.

Only `high`-confidence anchors are auto-written to the linkage graph.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CodeAnchor model
# ---------------------------------------------------------------------------


def _make_anchor(
    entity: Dict[str, Any],
    score: float,
    why_linked: str,
) -> Dict[str, Any]:
    return {
        "entity_id": entity.get("entity_id", ""),
        "kind": entity.get("kind", ""),
        "file_path": entity.get("file_path", ""),
        "symbol_name": entity.get("symbol_name", ""),
        "route_path": entity.get("route_path", ""),
        "start_line": entity.get("start_line", 0),
        "end_line": entity.get("end_line", 0),
        "confidence": "high" if score >= 0.6 else ("medium" if score >= 0.35 else "low"),
        "score": round(score, 3),
        "why_linked": why_linked,
    }


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    """Lowercase, strip punctuation, collapse spaces."""
    return re.sub(r"[^a-z0-9]", " ", s.lower()).strip()


def _token_overlap(a: str, b: str) -> float:
    """Jaccard token overlap between two strings."""
    ta = set(_normalize(a).split())
    tb = set(_normalize(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _path_score(url_path: str, route_path: str) -> float:
    """Score URL path against a code entity's route_path."""
    if not url_path or not route_path:
        return 0.0
    url_norm = _normalize(url_path)
    route_norm = _normalize(route_path)
    if url_norm == route_norm:
        return 1.0
    # Check each segment
    url_parts = [p for p in url_path.split("/") if p]
    route_parts = [p for p in route_path.split("/") if p and not p.startswith("{")]
    if not url_parts or not route_parts:
        return 0.0
    common = sum(1 for p in route_parts if p in url_parts)
    return common / max(len(url_parts), len(route_parts))


def _name_score(screen_name: str, entity: Dict[str, Any]) -> float:
    """Score screen name against entity's symbol_name and file_path."""
    symbol = entity.get("symbol_name", "")
    file_path = entity.get("file_path", "")
    file_stem = re.sub(r"\.(tsx?|py)$", "", file_path.split("/")[-1], flags=re.IGNORECASE)

    best = max(
        _token_overlap(screen_name, symbol),
        _token_overlap(screen_name, file_stem),
    )
    return best


def _testid_score(testids: List[str], entity: Dict[str, Any]) -> float:
    """Score data-testid signals against selector entities."""
    if entity.get("kind") != "selector":
        return 0.0
    sel = entity.get("selector", entity.get("symbol_name", ""))
    for tid in testids:
        if _normalize(tid) == _normalize(sel):
            return 1.0
        if _normalize(tid) in _normalize(sel) or _normalize(sel) in _normalize(tid):
            return 0.7
    return 0.0


def _feature_hint_score(screen_name: str, heading_text: str, entity: Dict[str, Any]) -> float:
    """Score feature hints from the entity against screen signals."""
    hints = entity.get("feature_hints", [])
    if not hints:
        return 0.0
    combined = _normalize(f"{screen_name} {heading_text}")
    combined_tokens = set(combined.split())
    matched = sum(1 for h in hints if h in combined_tokens)
    return min(matched / max(len(hints), 1), 1.0)


# ---------------------------------------------------------------------------
# Main inference function
# ---------------------------------------------------------------------------

def infer_screen_code_links(
    screen_id: str,
    screen_name: str,
    url_path: str = "",
    heading_text: str = "",
    button_labels: Optional[List[str]] = None,
    data_testids: Optional[List[str]] = None,
    max_anchors: int = 5,
    auto_persist: bool = True,
) -> List[Dict[str, Any]]:
    """Score code entities against screen signals. Return anchors sorted by score.

    Scoring weights:
      URL path match:      0.40
      Screen name/heading: 0.35
      data-testid:         0.15
      feature hints:       0.10

    Args:
        screen_id:       Unique screen identifier from exploration memory.
        screen_name:     Human-readable screen label.
        url_path:        The URL or route path observed for this screen.
        heading_text:    Primary visible heading on the screen.
        button_labels:   Visible button / link labels.
        data_testids:    Observed data-testid attribute values.
        max_anchors:     Maximum number of anchors to return.
        auto_persist:    If True, write high-confidence anchors to linkage_graph.

    Returns:
        List of CodeAnchor dicts sorted by score descending.
    """
    try:
        from app.services.code_indexer import get_index
        entities = get_index().get("entities", [])
    except Exception as exc:
        logger.warning("code_indexer unavailable: %s", exc)
        return []

    if not entities:
        return []

    button_labels = button_labels or []
    data_testids = data_testids or []
    scored: List[tuple] = []

    for entity in entities:
        route_score = _path_score(url_path, entity.get("route_path", ""))
        name_score = _name_score(screen_name, entity)
        tid_score = _testid_score(data_testids, entity)
        hint_score = _feature_hint_score(screen_name, heading_text, entity)

        total = (
            route_score * 0.40
            + name_score * 0.35
            + tid_score * 0.15
            + hint_score * 0.10
        )

        if total < 0.1:
            continue

        parts = []
        if route_score > 0.3:
            parts.append(f"route_match={route_score:.2f}")
        if name_score > 0.3:
            parts.append(f"name_match={name_score:.2f}")
        if tid_score > 0:
            parts.append(f"testid_match={tid_score:.2f}")
        if hint_score > 0:
            parts.append(f"hint_match={hint_score:.2f}")

        why = ", ".join(parts) or "weak_signal"
        scored.append((total, why, entity))

    scored.sort(key=lambda t: t[0], reverse=True)
    anchors = [_make_anchor(ent, sc, why) for sc, why, ent in scored[:max_anchors]]

    # Persist high-confidence anchors
    if auto_persist and anchors:
        _persist_high_confidence(screen_id, anchors)

    return anchors


def _persist_high_confidence(screen_id: str, anchors: List[Dict[str, Any]]) -> None:
    """Write high-confidence anchors to the linkage graph."""
    try:
        from app.agents.qa_pipeline.linkage_graph import (
            register_code_anchor,
            link_symbol_to_screen,
        )
        for anchor in anchors:
            if anchor.get("confidence") != "high":
                continue
            try:
                register_code_anchor(
                    entity_id=anchor["entity_id"],
                    kind=anchor["kind"],
                    file_path=anchor["file_path"],
                    symbol_name=anchor["symbol_name"],
                    route_path=anchor.get("route_path", ""),
                )
                link_symbol_to_screen(anchor["entity_id"], screen_id, confidence="high")
            except Exception as exc:
                logger.warning("Failed to persist anchor %s: %s", anchor["entity_id"], exc)
    except Exception as exc:
        logger.warning("linkage_graph unavailable for persistence: %s", exc)
