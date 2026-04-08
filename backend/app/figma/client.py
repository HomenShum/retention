from __future__ import annotations

from typing import Any, Dict, Optional

import httpx


class FigmaClientError(RuntimeError):
    pass


class FigmaAuthError(FigmaClientError):
    pass


class FigmaClient:
    """Minimal Figma REST API client.

    Uses X-Figma-Token authentication (personal access token) per Figma docs.
    """

    def __init__(
        self,
        access_token: str,
        base_url: str = "https://api.figma.com",
        timeout: Optional[httpx.Timeout] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        if not access_token:
            raise ValueError("access_token is required")
        self._access_token = access_token
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-Figma-Token": access_token},
            timeout=timeout or httpx.Timeout(30.0),
            transport=transport,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            resp = await self._client.get(path, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status in (401, 403):
                raise FigmaAuthError(f"Figma auth error ({status})") from e
            raise FigmaClientError(f"Figma API error ({status})") from e
        except httpx.TimeoutException as e:
            raise FigmaClientError("Figma API timeout") from e
        except httpx.RequestError as e:
            raise FigmaClientError("Figma API request failed") from e

    # Tier 1 (file content)
    async def get_file(self, key: str, *, depth: Optional[int] = None, ids: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if depth is not None:
            params["depth"] = depth
        if ids:
            params["ids"] = ids
        return await self._get(f"/v1/files/{key}", params=params or None)

    async def get_file_nodes(
        self,
        key: str,
        *,
        ids: str,
        depth: Optional[int] = None,
        geometry: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"ids": ids}
        if depth is not None:
            params["depth"] = depth
        if geometry is not None:
            params["geometry"] = geometry
        return await self._get(f"/v1/files/{key}/nodes", params=params)

    async def get_images(
        self,
        key: str,
        *,
        ids: str,
        image_format: Optional[str] = None,
        scale: Optional[float] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"ids": ids}
        if image_format:
            params["format"] = image_format
        if scale is not None:
            params["scale"] = scale
        return await self._get(f"/v1/images/{key}", params=params)

    async def get_image_fills(self, key: str) -> Dict[str, Any]:
        return await self._get(f"/v1/files/{key}/images")

    # Tier 2
    async def get_comments(self, key: str, *, as_md: bool = True) -> Dict[str, Any]:
        params = {"as_md": str(as_md).lower()}
        return await self._get(f"/v1/files/{key}/comments", params=params)

    async def get_dev_resources(self, file_key: str, *, node_ids: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if node_ids:
            params["node_ids"] = node_ids
        return await self._get(f"/v1/files/{file_key}/dev_resources", params=params or None)

    async def get_variables_local(self, file_key: str) -> Dict[str, Any]:
        return await self._get(f"/v1/files/{file_key}/variables/local")

    async def get_variables_published(self, file_key: str) -> Dict[str, Any]:
        return await self._get(f"/v1/files/{file_key}/variables/published")

    # Tier 3 (library + file metadata)
    async def get_file_meta(self, key: str) -> Dict[str, Any]:
        return await self._get(f"/v1/files/{key}/meta")

    async def get_file_components(self, file_key: str) -> Dict[str, Any]:
        return await self._get(f"/v1/files/{file_key}/components")

    async def get_file_component_sets(self, file_key: str) -> Dict[str, Any]:
        return await self._get(f"/v1/files/{file_key}/component_sets")

    async def get_file_styles(self, file_key: str) -> Dict[str, Any]:
        return await self._get(f"/v1/files/{file_key}/styles")
