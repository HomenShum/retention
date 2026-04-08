from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import figma as figma_router
from app.figma.models import DisclosureLevel


class _FakeFigmaService:
    async def get_snapshot(self, request):
        # Minimal shape matching SnapshotResponse
        return {
            "file_key": request.file_key or "KEY",
            "level": request.level,
            "dimensions": {},
        }


def test_snapshot_endpoint_requires_service_then_succeeds() -> None:
    app = FastAPI()
    app.include_router(figma_router.router)

    # No service: should 503
    figma_router.set_figma_service(None)
    with TestClient(app) as client:
        r = client.post("/api/figma/snapshot", json={"file_key": "KEY", "level": "metadata"})
        assert r.status_code == 503

    # With service: should 200
    figma_router.set_figma_service(_FakeFigmaService())
    with TestClient(app) as client:
        r = client.post("/api/figma/snapshot", json={"file_key": "KEY", "level": "metadata"})
        assert r.status_code == 200
        assert r.json()["file_key"] == "KEY"
