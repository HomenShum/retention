from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set

from ..agents.coordinator.context_compactor import get_storage_info, store_full_output
from .client import FigmaClient
from .models import DisclosureLevel, DimensionResult, SnapshotRequest, SnapshotResponse
from .parser import parse_figma_url


DEFAULT_DIMENSIONS_BY_LEVEL: Dict[DisclosureLevel, List[str]] = {
    DisclosureLevel.metadata: ["file"],
    DisclosureLevel.components: ["file", "file_components", "file_component_sets", "file_styles"],
    DisclosureLevel.full: [
        "file",
        "file_components",
        "file_component_sets",
        "file_styles",
        "image_fills",
        "comments",
        "dev_resources",
        "variables_local",
        "variables_published",
    ],
}


@dataclass
class FigmaService:
    client: FigmaClient

    async def get_snapshot(self, request: SnapshotRequest) -> SnapshotResponse:
        file_key = request.file_key
        if request.figma_url:
            parsed = parse_figma_url(request.figma_url)
            file_key = file_key or parsed.file_key
            if request.node_ids is None and parsed.node_id:
                request.node_ids = [parsed.node_id]

        if not file_key:
            raise ValueError("file_key or figma_url is required")

        dims = request.dimensions or DEFAULT_DIMENSIONS_BY_LEVEL[request.level]
        dimensions: Set[str] = set(dims)

        # Convenience: if caller asks for images but does not provide node_ids, skip rather than error.
        node_ids_csv = ",".join(request.node_ids) if request.node_ids else None

        results: Dict[str, DimensionResult] = {}

        async def store(name: str, payload: Any, summary: Dict[str, Any]) -> None:
            ref_id = store_full_output(payload, tool_name=f"figma:{name}")
            info = get_storage_info(ref_id)
            results[name] = DimensionResult(
                ref_id=ref_id,
                summary=summary,
                size_chars=(info or {}).get("size_chars"),
            )

        # Tier 1: file JSON (depth=1 by default for metadata/page list)
        if "file" in dimensions:
            depth = request.depth if request.depth is not None else 1
            file_json = await self.client.get_file(file_key, depth=depth)
            doc = (file_json or {}).get("document") or {}
            children = doc.get("children") or []
            page_names = [c.get("name") for c in children if isinstance(c, dict)]
            await store(
                "file",
                file_json,
                summary={
                    "name": file_json.get("name"),
                    "lastModified": file_json.get("lastModified"),
                    "page_count": len(children),
                    "pages": page_names[:20],
                    "depth": depth,
                },
            )

        if "file_nodes" in dimensions and node_ids_csv:
            nodes_json = await self.client.get_file_nodes(
                file_key,
                ids=node_ids_csv,
                depth=request.depth,
            )
            nodes_map = (nodes_json or {}).get("nodes") or {}
            await store(
                "file_nodes",
                nodes_json,
                summary={"node_ids": list(nodes_map.keys()), "count": len(nodes_map)},
            )

        if "images" in dimensions and node_ids_csv:
            images_json = await self.client.get_images(
                file_key,
                ids=node_ids_csv,
                image_format=request.image_format,
                scale=request.image_scale,
            )
            images_map = (images_json or {}).get("images") or {}
            await store(
                "images",
                images_json,
                summary={"count": len(images_map), "node_ids": list(images_map.keys())[:20]},
            )

        if "image_fills" in dimensions:
            fills_json = await self.client.get_image_fills(file_key)
            images_map = (fills_json or {}).get("images") or {}
            await store(
                "image_fills",
                fills_json,
                summary={"image_ref_count": len(images_map)},
            )

        if "comments" in dimensions:
            comments_json = await self.client.get_comments(file_key, as_md=request.as_markdown_comments)
            comments = (comments_json or {}).get("comments") or []
            await store(
                "comments",
                comments_json,
                summary={"count": len(comments)},
            )

        if "dev_resources" in dimensions:
            dev_json = await self.client.get_dev_resources(file_key, node_ids=node_ids_csv)
            devs = (dev_json or {}).get("dev_resources") or []
            await store(
                "dev_resources",
                dev_json,
                summary={"count": len(devs)},
            )

        if "variables_local" in dimensions:
            vars_local = await self.client.get_variables_local(file_key)
            meta = (vars_local or {}).get("meta") or {}
            await store(
                "variables_local",
                vars_local,
                summary={
                    "variables": len((meta.get("variables") or {}).keys()),
                    "collections": len((meta.get("variableCollections") or {}).keys()),
                },
            )

        if "variables_published" in dimensions:
            vars_pub = await self.client.get_variables_published(file_key)
            meta = (vars_pub or {}).get("meta") or {}
            await store(
                "variables_published",
                vars_pub,
                summary={
                    "variables": len((meta.get("variables") or {}).keys()),
                    "collections": len((meta.get("variableCollections") or {}).keys()),
                },
            )

        if "file_meta" in dimensions:
            meta_json = await self.client.get_file_meta(file_key)
            await store("file_meta", meta_json, summary={"has_file": "file" in meta_json})

        # Tier 3 library endpoints
        if "file_components" in dimensions:
            comps = await self.client.get_file_components(file_key)
            meta = (comps or {}).get("meta") or {}
            await store(
                "file_components",
                comps,
                summary={"count": len(meta.get("components") or [])},
            )

        if "file_component_sets" in dimensions:
            sets = await self.client.get_file_component_sets(file_key)
            meta = (sets or {}).get("meta") or {}
            await store(
                "file_component_sets",
                sets,
                summary={"count": len(meta.get("component_sets") or [])},
            )

        if "file_styles" in dimensions:
            styles = await self.client.get_file_styles(file_key)
            meta = (styles or {}).get("meta") or {}
            await store(
                "file_styles",
                styles,
                summary={"count": len(meta.get("styles") or [])},
            )

        return SnapshotResponse(file_key=file_key, level=request.level, dimensions=results)
