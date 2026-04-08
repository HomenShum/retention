"""Code Indexer — scans the repo and writes a durable symbol-level entity index.

Extracts:
  Backend (Python/ast):
    - FastAPI routes (@router.get/post/…)
    - Service / Agent classes
    - Top-level async handlers

  Frontend (TypeScript/TSX — regex):
    - Page/Component exports (pages/, components/)
    - Custom hooks (use*)
    - data-testid selectors
    - /api/... client-call strings

Output: backend/data/code_index/index.json

Usage (CLI):
    python -m app.services.code_indexer

Usage (library):
    from app.services.code_indexer import get_index, run_full_index
    entities = get_index()["entities"]
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[4]  # project root
_BACKEND_DIR = _REPO_ROOT / "backend"
_FRONTEND_DIR = _REPO_ROOT / "frontend" / "test-studio" / "src"
_INDEX_DIR = _BACKEND_DIR / "data" / "code_index"
_INDEX_PATH = _INDEX_DIR / "index.json"

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


def _make_entity(
    kind: str,
    file_path: str,
    symbol_name: str,
    start_line: int,
    end_line: int,
    language: str,
    route_path: str = "",
    http_method: str = "",
    selector: str = "",
    exports: Optional[List[str]] = None,
    feature_hints: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a code entity dict with a stable id."""
    slug = re.sub(r"[^a-z0-9_]", "_", f"{kind}_{file_path}_{symbol_name}".lower())[:80]
    entity_id = f"{slug}_{uuid.uuid5(uuid.NAMESPACE_URL, f'{file_path}:{symbol_name}').hex[:8]}"
    return {
        "entity_id": entity_id,
        "kind": kind,
        "file_path": file_path,
        "symbol_name": symbol_name,
        "start_line": start_line,
        "end_line": end_line,
        "language": language,
        "route_path": route_path,
        "http_method": http_method,
        "selector": selector,
        "exports": exports or [],
        "feature_hints": feature_hints or _derive_hints(file_path, symbol_name),
    }


def _derive_hints(file_path: str, symbol_name: str) -> List[str]:
    """Derive feature hint keywords from file path and symbol name."""
    raw = f"{file_path} {symbol_name}".lower()
    tokens = re.findall(r"[a-z]{3,}", raw)
    # Remove generic framework words
    skip = {
        "app", "api", "src", "test", "page", "view", "screen", "service",
        "agent", "backend", "frontend", "components", "routes", "hooks",
        "class", "def", "async", "function", "export", "default", "const",
        "import", "from", "type", "interface", "return", "with", "the",
    }
    return sorted(set(t for t in tokens if t not in skip and len(t) >= 4))[:8]


# ---------------------------------------------------------------------------
# Python AST scanner
# ---------------------------------------------------------------------------

_HTTP_DECORATORS = {"get", "post", "put", "patch", "delete", "options", "head"}


def _scan_python_file(abs_path: Path, rel_path: str) -> List[Dict[str, Any]]:
    """Extract entities from a Python source file via ast."""
    try:
        source = abs_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(abs_path))
    except SyntaxError:
        return []
    except Exception:
        return []

    entities: List[Dict[str, Any]] = []

    for node in ast.walk(tree):
        # ── FastAPI route decorators ──────────────────────────────────────
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for deco in node.decorator_list:
                method = ""
                route_path = ""

                # @router.get("/path") or @app.post("/path")
                if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute):
                    attr = deco.func.attr
                    if attr in _HTTP_DECORATORS:
                        method = attr.upper()
                        # First positional arg is the path
                        if deco.args and isinstance(deco.args[0], ast.Constant):
                            route_path = str(deco.args[0].value)

                if method:
                    entities.append(_make_entity(
                        kind="route",
                        file_path=rel_path,
                        symbol_name=node.name,
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        language="python",
                        http_method=method,
                        route_path=route_path,
                    ))
                    break  # one route entity per function

        # ── Service / Agent classes ───────────────────────────────────────
        if isinstance(node, ast.ClassDef):
            name_lower = node.name.lower()
            if any(w in name_lower for w in ("service", "agent", "client", "manager", "bridge")):
                kind = "service" if "service" in name_lower else "agent"
                entities.append(_make_entity(
                    kind=kind,
                    file_path=rel_path,
                    symbol_name=node.name,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    language="python",
                ))

    return entities


# ---------------------------------------------------------------------------
# TypeScript / TSX regex scanner
# ---------------------------------------------------------------------------

_COMPONENT_RE = re.compile(
    r"export\s+(?:default\s+)?(?:function|const)\s+([A-Z][A-Za-z0-9_]+)",
    re.MULTILINE,
)
_HOOK_RE = re.compile(
    r"export\s+(?:function|const)\s+(use[A-Z][A-Za-z0-9_]+)",
    re.MULTILINE,
)
_TESTID_RE = re.compile(r'data-testid=["\']([^"\']+)["\']', re.MULTILINE)
_API_CALL_RE = re.compile(r'["\`](\s*/api/[A-Za-z0-9_/:{}-]+)', re.MULTILINE)


def _scan_ts_file(abs_path: Path, rel_path: str) -> List[Dict[str, Any]]:
    """Extract entities from a TypeScript/TSX file via regex."""
    try:
        source = abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    entities: List[Dict[str, Any]] = []
    is_page = "pages/" in rel_path or "Pages/" in rel_path
    is_hook = "hooks/" in rel_path or "Hooks/" in rel_path

    lines = source.splitlines()
    line_starts = [0]
    for line in lines:
        line_starts.append(line_starts[-1] + len(line) + 1)

    def _line_of(pos: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if line_starts[mid] <= pos < line_starts[mid + 1]:
                return mid + 1
            elif pos < line_starts[mid]:
                hi = mid
            else:
                lo = mid + 1
        return lo + 1

    # Hooks (higher priority — match before components)
    for m in _HOOK_RE.finditer(source):
        ln = _line_of(m.start())
        entities.append(_make_entity(
            kind="hook",
            file_path=rel_path,
            symbol_name=m.group(1),
            start_line=ln,
            end_line=ln,
            language="typescript",
        ))

    # Components / Pages
    hook_symbols = {e["symbol_name"] for e in entities}
    for m in _COMPONENT_RE.finditer(source):
        sym = m.group(1)
        if sym in hook_symbols:
            continue
        ln = _line_of(m.start())
        kind = "page" if is_page else "component"
        entities.append(_make_entity(
            kind=kind,
            file_path=rel_path,
            symbol_name=sym,
            start_line=ln,
            end_line=ln,
            language="typescript",
        ))

    # Selectors (data-testid)
    for m in _TESTID_RE.finditer(source):
        ln = _line_of(m.start())
        sel = m.group(1)
        entities.append(_make_entity(
            kind="selector",
            file_path=rel_path,
            symbol_name=sel,
            start_line=ln,
            end_line=ln,
            language="typescript",
            selector=sel,
        ))

    # API client calls
    for m in _API_CALL_RE.finditer(source):
        route = m.group(1).strip()
        ln = _line_of(m.start())
        entities.append(_make_entity(
            kind="client_call",
            file_path=rel_path,
            symbol_name=route,
            start_line=ln,
            end_line=ln,
            language="typescript",
            route_path=route,
        ))

    return entities


# ---------------------------------------------------------------------------
# Full repo scan
# ---------------------------------------------------------------------------

_PY_SKIP_DIRS = {"venv311", "__pycache__", ".git", ".venv", "node_modules"}
_TS_SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".vite", "__pycache__"}


def run_full_index(
    backend_dir: Path = _BACKEND_DIR,
    frontend_dir: Path = _FRONTEND_DIR,
    output_path: Path = _INDEX_PATH,
) -> Dict[str, Any]:
    """Scan backend (Python) and frontend (TS/TSX) and write index.json.

    Returns the full index dict.
    """
    entities: List[Dict[str, Any]] = []
    seen_ids: set = set()

    # ── Backend Python scan ───────────────────────────────────────────────
    if backend_dir.exists():
        app_dir = backend_dir / "app"
        for root, dirs, files in os.walk(app_dir):
            dirs[:] = [d for d in dirs if d not in _PY_SKIP_DIRS]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                abs_path = Path(root) / fname
                try:
                    rel = abs_path.relative_to(backend_dir).as_posix()
                except ValueError:
                    rel = str(abs_path)
                for entity in _scan_python_file(abs_path, rel):
                    if entity["entity_id"] not in seen_ids:
                        seen_ids.add(entity["entity_id"])
                        entities.append(entity)

    # ── Frontend TypeScript scan ──────────────────────────────────────────
    if frontend_dir.exists():
        for root, dirs, files in os.walk(frontend_dir):
            dirs[:] = [d for d in dirs if d not in _TS_SKIP_DIRS]
            for fname in files:
                if not fname.endswith((".ts", ".tsx")):
                    continue
                abs_path = Path(root) / fname
                try:
                    rel = abs_path.relative_to(_REPO_ROOT / "frontend").as_posix()
                except ValueError:
                    rel = str(abs_path)
                for entity in _scan_ts_file(abs_path, rel):
                    if entity["entity_id"] not in seen_ids:
                        seen_ids.add(entity["entity_id"])
                        entities.append(entity)

    index = {
        "entities": entities,
        "entity_count": len(entities),
        "indexed_at": datetime.now(timezone.utc).isoformat(),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(index, indent=2, default=str))
    logger.info("Code index written: %d entities → %s", len(entities), output_path)
    return index


# ---------------------------------------------------------------------------
# Reader (cached per process)
# ---------------------------------------------------------------------------

_CACHED_INDEX: Optional[Dict[str, Any]] = None


def get_index(refresh: bool = False) -> Dict[str, Any]:
    """Return the code index, loading from disk if needed."""
    global _CACHED_INDEX
    if _CACHED_INDEX is None or refresh:
        if _INDEX_PATH.exists():
            try:
                _CACHED_INDEX = json.loads(_INDEX_PATH.read_text())
            except Exception:
                _CACHED_INDEX = {"entities": [], "entity_count": 0, "indexed_at": None}
        else:
            _CACHED_INDEX = {"entities": [], "entity_count": 0, "indexed_at": None}
    return _CACHED_INDEX


def get_entities_by_file(file_path: str) -> List[Dict[str, Any]]:
    """Return all entities whose file_path matches (exact or suffix)."""
    index = get_index()
    results = []
    for e in index.get("entities", []):
        ep = e.get("file_path", "")
        if ep == file_path or ep.endswith(file_path) or file_path.endswith(ep):
            results.append(e)
    return results


def get_entities_for_files(files: List[str]) -> List[Dict[str, Any]]:
    """Return distinct entities for a list of changed file paths."""
    seen = set()
    results = []
    for f in files:
        for e in get_entities_by_file(f):
            if e["entity_id"] not in seen:
                seen.add(e["entity_id"])
                results.append(e)
    return results


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    idx = run_full_index()
    print(f"Indexed {idx['entity_count']} entities → {_INDEX_PATH}")
