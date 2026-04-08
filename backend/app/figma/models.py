from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DisclosureLevel(str, Enum):
    metadata = "metadata"  # file JSON (depth=1) + lightweight summary
    components = "components"  # + published components/styles
    full = "full"  # + variables/comments/dev-resources/images


class SnapshotRequest(BaseModel):
    # Input sources
    figma_url: Optional[str] = Field(
        default=None,
        description="A Figma file or node URL, e.g. https://www.figma.com/design/<file_key>/...",
    )
    file_key: Optional[str] = Field(default=None, description="Figma file key")

    # Progressive disclosure
    level: DisclosureLevel = Field(default=DisclosureLevel.metadata)
    dimensions: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional explicit dimension selection. If omitted, dimensions are implied by level. "
            "Known: file, file_meta, file_nodes, images, image_fills, comments, variables_local, "
            "variables_published, dev_resources, file_components, file_component_sets, file_styles."
        ),
    )

    # Filters / options
    node_ids: Optional[List[str]] = Field(
        default=None,
        description="Optional node IDs for file_nodes/images/dev_resources filtering.",
    )
    depth: Optional[int] = Field(
        default=None,
        description="Optional depth for file/file_nodes endpoints (see Figma docs).",
        ge=1,
    )
    as_markdown_comments: bool = Field(
        default=True,
        description="If true, request comments as markdown when available (as_md=true).",
    )

    # Image render options (for /v1/images/:key)
    image_format: Optional[str] = Field(default=None, description="png|jpg|svg|pdf")
    image_scale: Optional[float] = Field(default=None, ge=0.01, le=4.0)


class DimensionResult(BaseModel):
    ref_id: str
    summary: Dict[str, Any] = Field(default_factory=dict)
    size_chars: Optional[int] = None


class SnapshotResponse(BaseModel):
    file_key: str
    level: DisclosureLevel
    dimensions: Dict[str, DimensionResult]
