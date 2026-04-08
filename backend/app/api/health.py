"""
Health Check API Router

Provides health check endpoints for various services:
- Backend health
- Test service health
- MCP Appium health
"""

from fastapi import APIRouter
from datetime import datetime, timezone
from typing import Dict, Any

# Create router
router = APIRouter(prefix="/api", tags=["health"])


# ============================================================================
# Dependency Injection for State
# ============================================================================

# These will be set by main.py during startup
_sessions_store: Dict = {}
_results_store: Dict = {}


def set_stores(sessions_store: Dict, results_store: Dict):
    """Set the session and results stores"""
    global _sessions_store, _results_store
    _sessions_store = sessions_store
    _results_store = results_store


# ============================================================================
# Health Check Endpoints
# ============================================================================

@router.get("/health")
async def health() -> Dict[str, Any]:
    """Backend health check."""
    return {
        "status": "ok",
        "service": "backend",
        "time": datetime.now(timezone.utc).isoformat()
    }


@router.get("/test/health")
async def test_health() -> Dict[str, Any]:
    """Test service health check."""
    return {
        "status": "ok",
        "service": "test-service",
        "sessions_active": len(_sessions_store),
        "tests_completed": len(_results_store),
        "time": datetime.now(timezone.utc).isoformat()
    }


@router.get("/mcp/health")
async def mcp_health() -> Dict[str, Any]:
    """MCP Appium health check (mock)."""
    return {
        "status": "ok",
        "service": "mcp-appium",
        "version": "1.0.0",
        "tools_available": 10,
        "time": datetime.now(timezone.utc).isoformat()
    }

