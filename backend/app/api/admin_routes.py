"""Admin API — user management, usage stats, system health.

Protected by admin token (ADMIN_TOKEN env var or first registered user).
File-based storage for MVP; upgrade to Postgres later.

Endpoints:
  GET /api/admin/users   -> list all users (admin only)
  GET /api/admin/usage   -> usage statistics
  GET /api/admin/health  -> system health check
"""

import json
import logging
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# ── Storage paths (shared with auth_routes.py) ──────────────────

_DATA_DIR = Path(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))) / "data"
USERS_FILE = _DATA_DIR / "users.json"
SESSIONS_FILE = _DATA_DIR / "sessions.json"
API_KEYS_FILE = _DATA_DIR / "api_keys.json"

# Admin token from env — if not set, only the first registered user is admin
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

# Max users returned in list (pagination safety)
MAX_LIST_USERS = 200

_start_time = time.time()


# ── Models ──────────────────────────────────────────────────────


class UserSummary(BaseModel):
    email: str
    name: str
    plan: str
    created_at: str
    has_api_key: bool


class UsersListResponse(BaseModel):
    total: int
    users: list[UserSummary]


class UsageResponse(BaseModel):
    total_users: int
    total_api_keys: int
    active_sessions: int
    plans: dict[str, int]


class HealthResponse(BaseModel):
    status: str
    uptime_seconds: float
    python_version: str
    data_dir_exists: bool
    users_count: int
    timestamp: str


# ── Helpers ─────────────────────────────────────────────────────


def _load_json(path: Path) -> dict:
    """Load a JSON file, returning empty dict if absent or corrupt."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _verify_admin(authorization: Optional[str] = Header(None)) -> str:
    """Verify the request carries a valid admin token.

    Accepts:
      - ADMIN_TOKEN env var as Bearer token
      - Any session token belonging to the first registered user (auto-admin)
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization format")

    token = authorization[7:]

    # Check env-based admin token
    if ADMIN_TOKEN and token == ADMIN_TOKEN:
        return "admin-env"

    # Check session-based admin (first registered user)
    sessions = _load_json(SESSIONS_FILE)
    session = sessions.get(token)
    if not session:
        raise HTTPException(status_code=403, detail="Invalid admin token")

    users = _load_json(USERS_FILE)
    if not users:
        raise HTTPException(status_code=403, detail="No users registered")

    # First registered user by created_at is auto-admin
    first_email = min(users.keys(), key=lambda e: users[e].get("created_at", ""))
    if session.get("email") != first_email:
        raise HTTPException(status_code=403, detail="Admin access required")

    return session["email"]


# ── Routes ──────────────────────────────────────────────────────


@router.get("/users", response_model=UsersListResponse, summary="List all users")
async def list_users(admin: str = Depends(_verify_admin)):
    """List all registered users. Admin only."""
    users = _load_json(USERS_FILE)

    user_list = []
    for email, info in list(users.items())[:MAX_LIST_USERS]:
        user_list.append(UserSummary(
            email=email,
            name=info.get("name", ""),
            plan=info.get("plan", "free"),
            created_at=info.get("created_at", ""),
            has_api_key=bool(info.get("api_key")),
        ))

    # Sort by created_at descending (newest first)
    user_list.sort(key=lambda u: u.created_at, reverse=True)

    return UsersListResponse(total=len(users), users=user_list)


@router.get("/usage", response_model=UsageResponse, summary="Usage statistics")
async def usage_stats(admin: str = Depends(_verify_admin)):
    """Aggregate usage statistics. Admin only."""
    users = _load_json(USERS_FILE)
    api_keys = _load_json(API_KEYS_FILE)
    sessions = _load_json(SESSIONS_FILE)

    # Count active sessions (not expired, 30-day TTL)
    now = time.time()
    ttl = 30 * 24 * 60 * 60
    active = sum(1 for s in sessions.values() if now - s.get("created_at_unix", 0) < ttl)

    # Plan distribution
    plans: dict[str, int] = {}
    for info in users.values():
        plan = info.get("plan", "free")
        plans[plan] = plans.get(plan, 0) + 1

    return UsageResponse(
        total_users=len(users),
        total_api_keys=len(api_keys),
        active_sessions=active,
        plans=plans,
    )


@router.get("/health", response_model=HealthResponse, summary="System health")
async def system_health():
    """System health check. No auth required (for monitoring)."""
    users = _load_json(USERS_FILE)

    return HealthResponse(
        status="ok",
        uptime_seconds=round(time.time() - _start_time, 1),
        python_version=platform.python_version(),
        data_dir_exists=_DATA_DIR.exists(),
        users_count=len(users),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
