import httpx
import pytest

from app.figma.client import FigmaClient
from app.figma.models import DisclosureLevel, SnapshotRequest
from app.figma.service import FigmaService


@pytest.mark.asyncio
async def test_service_metadata_level_stores_file_dimension() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/files/KEY":
            return httpx.Response(
                200,
                json={
                    "name": "File",
                    "lastModified": "2026-01-01",
                    "document": {"children": [{"name": "Page 1"}]},
                },
            )
        return httpx.Response(404, json={"err": "not found"})

    transport = httpx.MockTransport(handler)
    client = FigmaClient(access_token="token", transport=transport)
    service = FigmaService(client=client)
    try:
        resp = await service.get_snapshot(SnapshotRequest(file_key="KEY", level=DisclosureLevel.metadata))
        assert resp.file_key == "KEY"
        assert "file" in resp.dimensions
        assert resp.dimensions["file"].ref_id
    finally:
        await client.aclose()
