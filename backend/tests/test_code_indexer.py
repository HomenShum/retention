"""Tests for the code indexer."""

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from app.services import code_indexer


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Setup a mock repo with Python and TypeScript files."""
    repo = tmp_path / "mock_repo"
    backend = repo / "backend" / "app" / "api"
    frontend = repo / "frontend" / "test-studio" / "src" / "pages"
    frontend_components = repo / "frontend" / "test-studio" / "src" / "components"
    backend.mkdir(parents=True)
    frontend.mkdir(parents=True)
    frontend_components.mkdir(parents=True)

    # Python backend file with routes and service
    py_code = '''
from fastapi import APIRouter
router = APIRouter()

@router.get("/api/users")
async def list_users():
    return []

@router.post("/api/orders/{id}")
def create_order(id: int):
    pass

class PaymentService:
    def process(self):
        pass
'''
    (backend / "test_api.py").write_text(py_code)

    # TSX frontend file with component, selectors, and API calls
    tsx_code = '''
export default function CheckoutPage() {
    const handleSave = async () => {
        await fetch("/api/orders/abc");
    };
    return (
        <div data-testid="checkout-container">
            <button data-testid="save-btn" onClick={handleSave}>Save</button>
        </div>
    );
}
'''
    (frontend / "CheckoutPage.tsx").write_text(tsx_code)

    # Hook component
    hook_code = '''
export function useAuth() {
    return { user: null };
}
'''
    (frontend_components / "useAuth.ts").write_text(hook_code)

    return repo


def test_code_indexer_extracts_entities(temp_repo: Path, tmp_path: Path) -> None:
    backend_dir = temp_repo / "backend"
    frontend_dir = temp_repo / "frontend" / "test-studio" / "src"
    out_path = tmp_path / "data" / "index.json"

    idx = code_indexer.run_full_index(
        backend_dir=backend_dir,
        frontend_dir=frontend_dir,
        output_path=out_path,
    )

    assert out_path.exists()
    assert idx["entity_count"] > 0
    entities: list[Dict[str, Any]] = idx["entities"]

    # Python entities
    routes = [e for e in entities if e["kind"] == "route" and e["language"] == "python"]
    assert len(routes) == 2
    assert any(r["symbol_name"] == "list_users" and r["route_path"] == "/api/users" for r in routes)
    assert any(r["symbol_name"] == "create_order" and r["http_method"] == "POST" for r in routes)

    services = [e for e in entities if e["kind"] == "service"]
    assert len(services) == 1
    assert services[0]["symbol_name"] == "PaymentService"

    # TypeScript entities
    pages = [e for e in entities if e["kind"] == "page"]
    assert len(pages) == 1
    assert pages[0]["symbol_name"] == "CheckoutPage"

    hooks = [e for e in entities if e["kind"] == "hook"]
    assert len(hooks) == 1
    assert hooks[0]["symbol_name"] == "useAuth"

    selectors = [e for e in entities if e["kind"] == "selector"]
    assert len(selectors) == 2
    assert any(s["symbol_name"] == "checkout-container" for s in selectors)
    assert any(s["symbol_name"] == "save-btn" for s in selectors)

    api_calls = [e for e in entities if e["kind"] == "client_call"]
    assert len(api_calls) == 1
    assert api_calls[0]["route_path"] == "/api/orders/abc"


def test_get_entities_by_file(monkeypatch, tmp_path: Path) -> None:
    out_path = tmp_path / "index.json"
    dummy_index = {
        "entities": [
            {"entity_id": "1", "file_path": "backend/app/main.py", "symbol_name": "app"},
            {"entity_id": "2", "file_path": "frontend/src/App.tsx", "symbol_name": "App"},
        ],
        "entity_count": 2,
    }
    out_path.write_text(json.dumps(dummy_index))

    monkeypatch.setattr(code_indexer, "_INDEX_PATH", out_path)
    monkeypatch.setattr(code_indexer, "_CACHED_INDEX", None)  # force reload

    results = code_indexer.get_entities_by_file("app/main.py")
    assert len(results) == 1
    assert results[0]["entity_id"] == "1"

    results2 = code_indexer.get_entities_by_file("App.tsx")
    assert len(results2) == 1
    assert results2[0]["entity_id"] == "2"
