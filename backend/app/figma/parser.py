from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class ParsedFigmaUrl:
    file_key: str
    node_id: Optional[str] = None
    file_type: Optional[str] = None


def parse_figma_url(url: str) -> ParsedFigmaUrl:
    """Parse a Figma URL and extract file_key and optional node-id.

    Supports common URL formats documented by Figma:
    - https://www.figma.com/:file_type/:file_key/:file_name
    - https://www.figma.com/:file_type/:file_key/:file_name?node-id=:id
    """
    if not url or not isinstance(url, str):
        raise ValueError("url must be a non-empty string")

    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if "figma.com" not in host:
        raise ValueError("not a figma.com url")

    # Path: /{file_type}/{file_key}/{file_name...}
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise ValueError("could not parse file_key from url path")

    file_type = parts[0]
    file_key = parts[1]
    if not file_key:
        raise ValueError("missing file_key")

    qs = parse_qs(parsed.query or "")
    node_id = None
    # Figma uses both node-id and node_id in the wild; docs show node-id.
    for key in ("node-id", "node_id"):
        if key in qs and qs[key]:
            node_id = qs[key][0]
            break

    return ParsedFigmaUrl(file_key=file_key, node_id=node_id, file_type=file_type)
