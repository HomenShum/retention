import httpx
import pytest

from app.figma.client import FigmaClient, FigmaAuthError


@pytest.mark.asyncio
async def test_client_adds_token_and_parses_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("X-Figma-Token") == "token"
        if request.url.path == "/v1/files/KEY":
            return httpx.Response(200, json={"name": "My file", "document": {"children": []}})
        return httpx.Response(404, json={"err": "not found"})

    transport = httpx.MockTransport(handler)
    client = FigmaClient(access_token="token", transport=transport)
    try:
        data = await client.get_file("KEY", depth=1)
        assert data["name"] == "My file"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_client_auth_error_maps_to_figma_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"err": "forbidden"})

    transport = httpx.MockTransport(handler)
    client = FigmaClient(access_token="token", transport=transport)
    try:
        with pytest.raises(FigmaAuthError):
            await client.get_file("KEY")
    finally:
        await client.aclose()
