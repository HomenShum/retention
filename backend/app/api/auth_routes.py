"""Authentication API — login, session, logout.

Complements signup.py (which handles registration + API key generation).
Uses a file-based user/session store for MVP. Upgrade to Postgres later.

Endpoints:
  POST /api/auth/signup  -> create user with password, generate API key + session token
  POST /api/auth/login   -> verify credentials, return session token
  GET  /api/auth/me      -> return current user from session token
  POST /api/auth/logout  -> invalidate session token
"""

import hashlib
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── Storage paths ───────────────────────────────────────────────

_DATA_DIR = Path(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))) / "data"
USERS_FILE = _DATA_DIR / "users.json"
SESSIONS_FILE = _DATA_DIR / "sessions.json"

# Session TTL: 30 days
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60

# Max sessions per user (evict oldest beyond this)
MAX_SESSIONS_PER_USER = 10

# Max total users (file-based store safety bound)
MAX_USERS = 10_000


# ── Models ──────────────────────────────────────────────────────


class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, description="Minimum 8 characters")
    name: str = ""
    plan: str = "free"


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    token: str
    api_key: str
    email: str
    plan: str
    name: str = ""


class MeResponse(BaseModel):
    email: str
    plan: str
    name: str
    created_at: str
    api_key: str


class LogoutResponse(BaseModel):
    ok: bool
    message: str = "Logged out"


# ── Helpers ─────────────────────────────────────────────────────


def _hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Hash password with PBKDF2-SHA256. Returns (hash_hex, salt_hex)."""
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations=100_000)
    return h.hex(), salt


def _verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Verify a password against stored hash + salt."""
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations=100_000)
    return secrets.compare_digest(h.hex(), stored_hash)


def _load_json(path: Path) -> dict:
    """Load a JSON file, creating it if absent."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_json(path: Path, data: dict) -> None:
    """Persist a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def _generate_api_key() -> str:
    """Generate API key in sk-ret-{hex} format (matches signup.py)."""
    return f"sk-ret-{secrets.token_hex(16)}"


def _generate_session_token() -> str:
    """Generate a session token."""
    return f"sess-{secrets.token_hex(24)}"


def _evict_expired_sessions(sessions: dict) -> dict:
    """Remove expired sessions. Returns cleaned dict."""
    now = time.time()
    return {
        tok: s for tok, s in sessions.items()
        if now - s.get("created_at_unix", 0) < SESSION_TTL_SECONDS
    }


def _extract_token(authorization: Optional[str] = Header(None)) -> str:
    """Extract Bearer token from Authorization header."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization format. Use: Bearer <token>")
    return authorization[7:]


# ── Routes ──────────────────────────────────────────────────────


@router.post("/signup", response_model=AuthResponse, summary="Create account")
async def signup(req: SignupRequest):
    """Register a new user with email + password. Returns session token and API key."""
    users = _load_json(USERS_FILE)

    # Bound check
    if len(users) >= MAX_USERS:
        raise HTTPException(status_code=503, detail="User registration temporarily unavailable")

    # Check duplicate
    if req.email in users:
        raise HTTPException(status_code=409, detail="Email already registered. Use /api/auth/login instead.")

    # Hash password
    pw_hash, salt = _hash_password(req.password)
    api_key = _generate_api_key()

    users[req.email] = {
        "password_hash": pw_hash,
        "salt": salt,
        "name": req.name,
        "plan": req.plan,
        "api_key": api_key,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_json(USERS_FILE, users)

    # Create session
    session_token = _generate_session_token()
    sessions = _load_json(SESSIONS_FILE)
    sessions = _evict_expired_sessions(sessions)
    sessions[session_token] = {
        "email": req.email,
        "created_at_unix": time.time(),
    }
    _save_json(SESSIONS_FILE, sessions)

    logger.info(f"[Auth] Signup: {req.email}, plan={req.plan}")

    return AuthResponse(
        token=session_token,
        api_key=api_key,
        email=req.email,
        plan=req.plan,
        name=req.name,
    )


@router.post("/login", response_model=AuthResponse, summary="Login")
async def login(req: LoginRequest):
    """Verify credentials and return a session token."""
    users = _load_json(USERS_FILE)
    user = users.get(req.email)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not _verify_password(req.password, user["password_hash"], user["salt"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Create session
    session_token = _generate_session_token()
    sessions = _load_json(SESSIONS_FILE)
    sessions = _evict_expired_sessions(sessions)

    # Evict oldest sessions for this user if over limit
    user_sessions = sorted(
        [(tok, s) for tok, s in sessions.items() if s.get("email") == req.email],
        key=lambda x: x[1].get("created_at_unix", 0),
    )
    if len(user_sessions) >= MAX_SESSIONS_PER_USER:
        for tok, _ in user_sessions[: len(user_sessions) - MAX_SESSIONS_PER_USER + 1]:
            sessions.pop(tok, None)

    sessions[session_token] = {
        "email": req.email,
        "created_at_unix": time.time(),
    }
    _save_json(SESSIONS_FILE, sessions)

    logger.info(f"[Auth] Login: {req.email}")

    return AuthResponse(
        token=session_token,
        api_key=user.get("api_key", ""),
        email=req.email,
        plan=user.get("plan", "free"),
        name=user.get("name", ""),
    )


@router.get("/me", response_model=MeResponse, summary="Current user")
async def me(token: str = Depends(_extract_token)):
    """Return the current user from a valid session token."""
    sessions = _load_json(SESSIONS_FILE)
    session = sessions.get(token)

    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session token")

    # Check expiry
    if time.time() - session.get("created_at_unix", 0) > SESSION_TTL_SECONDS:
        sessions.pop(token, None)
        _save_json(SESSIONS_FILE, sessions)
        raise HTTPException(status_code=401, detail="Session expired. Please login again.")

    users = _load_json(USERS_FILE)
    user = users.get(session["email"])

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return MeResponse(
        email=session["email"],
        plan=user.get("plan", "free"),
        name=user.get("name", ""),
        created_at=user.get("created_at", ""),
        api_key=user.get("api_key", ""),
    )


@router.post("/logout", response_model=LogoutResponse, summary="Logout")
async def logout(token: str = Depends(_extract_token)):
    """Invalidate a session token."""
    sessions = _load_json(SESSIONS_FILE)

    if token in sessions:
        sessions.pop(token)
        _save_json(SESSIONS_FILE, sessions)
        logger.info(f"[Auth] Logout: token invalidated")

    return LogoutResponse(ok=True, message="Logged out")
