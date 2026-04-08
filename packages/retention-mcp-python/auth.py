"""
retention.sh — API key authentication for outbound WebSocket handshake.

The thin relay authenticates with the retention.sh server by sending an API key
during the WebSocket upgrade. The server validates the key and associates the
connection with the user's account.
"""

from __future__ import annotations

import os
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------

def get_api_key() -> str:
    """Return the retention.sh API key from environment or local config.

    Resolution order:
      1. TA_API_KEY environment variable
      2. RETENTION_MCP_TOKEN environment variable (legacy compat)
      3. ~/.retention/config.json → api_key field
    """
    key = os.getenv("TA_API_KEY") or os.getenv("RETENTION_MCP_TOKEN", "")
    if key:
        return key.strip()

    config_path = os.path.expanduser("~/.retention/config.json")
    try:
        with open(config_path) as f:
            data = json.load(f)
            return (data.get("api_key") or "").strip()
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass

    return ""


def build_auth_headers(api_key: Optional[str] = None) -> dict[str, str]:
    """Return headers dict for the WebSocket upgrade request."""
    key = api_key or get_api_key()
    if not key:
        raise ValueError(
            "No retention.sh API key found. Set TA_API_KEY env var or run the "
            "install script to configure ~/.retention/config.json"
        )
    return {"Authorization": f"Bearer {key}"}
