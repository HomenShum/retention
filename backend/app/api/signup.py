"""Signup & Token Generation API.

Generates API keys for new users and provides setup instructions
for integrating retention.sh with their IDE agent (Claude Code, Cursor, etc.).

Endpoints:
  POST /api/signup              → generate an API key
  GET  /api/signup/verify/{token} → verify a token is valid
"""

import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/signup", tags=["signup"])

API_KEYS_PATH = Path(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
) / "data" / "api_keys.json"


# ── Models ───────────────────────────────────────────────────────


class SignupRequest(BaseModel):
    email: str
    name: str
    platform: Literal["claude-code", "cursor", "devin", "other"]


class SignupResponse(BaseModel):
    token: str
    setup_instructions: str


class VerifyResponse(BaseModel):
    valid: bool
    email: str = ""
    platform: str = ""


# ── Helpers ──────────────────────────────────────────────────────


def _load_keys() -> dict:
    """Load the api_keys.json store, creating it if absent."""
    if not API_KEYS_PATH.exists():
        API_KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
        API_KEYS_PATH.write_text("{}")
        return {}
    return json.loads(API_KEYS_PATH.read_text())


def _save_keys(keys: dict) -> None:
    """Persist the api_keys.json store."""
    API_KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    API_KEYS_PATH.write_text(json.dumps(keys, indent=2, default=str))


def _generate_token() -> str:
    """Generate a token in the format sk-ret-{8 hex chars}."""
    return f"sk-ret-{secrets.token_hex(4)}"


def _mcp_json_snippet(token: str) -> str:
    """Return the .mcp.json config snippet with the token filled in."""
    snippet = {
        "mcpServers": {
            "retention": {
                "type": "http",
                "url": "https://your-retention-host/mcp",
                "headers": {
                    "Authorization": f"Bearer {token}"
                }
            }
        }
    }
    instructions = (
        "Add this to your .mcp.json to connect your agent to retention.sh:\n\n"
        f"```json\n{json.dumps(snippet, indent=2)}\n```\n\n"
        "Replace 'your-retention-host' with your actual retention.sh URL.\n"
        "Then restart your IDE agent to pick up the new MCP server."
    )
    return instructions


# ── Routes ───────────────────────────────────────────────────────


@router.post("", response_model=SignupResponse, summary="Generate an API key")
async def signup(req: SignupRequest) -> SignupResponse:
    """Register a new user and generate an API key.

    Returns the token and setup instructions for integrating with
    the user's IDE agent platform.
    """
    keys = _load_keys()

    # Check if email already has a key
    for token, info in keys.items():
        if info.get("email") == req.email:
            return SignupResponse(
                token=token,
                setup_instructions=_mcp_json_snippet(token),
            )

    token = _generate_token()

    # Ensure uniqueness (extremely unlikely collision, but be safe)
    while token in keys:
        token = _generate_token()

    keys[token] = {
        "email": req.email,
        "name": req.name,
        "platform": req.platform,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_used": None,
    }
    _save_keys(keys)
    logger.info(f"[Signup] New key for {req.email} ({req.platform})")

    return SignupResponse(
        token=token,
        setup_instructions=_mcp_json_snippet(token),
    )


@router.get("/verify/{token}", response_model=VerifyResponse, summary="Verify an API token")
async def verify_token(token: str) -> VerifyResponse:
    """Check whether an API token is valid and return its metadata."""
    keys = _load_keys()
    info = keys.get(token)
    if not info:
        return VerifyResponse(valid=False)
    return VerifyResponse(
        valid=True,
        email=info.get("email", ""),
        platform=info.get("platform", ""),
    )
