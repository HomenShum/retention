"""Convex HTTP client for persisting decision logs and institutional memory.

Calls Convex HTTP actions via httpx. No Convex SDK dependency needed.
The backend writes state to Convex so it persists across restarts and
is accessible even when the developer's laptop is off.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Module-level shared HTTP client (singleton, lazily initialised)
_shared_http_client: Optional[httpx.AsyncClient] = None


def _get_shared_http_client() -> httpx.AsyncClient:
    """Return the shared httpx.AsyncClient, creating it on first call."""
    global _shared_http_client
    if _shared_http_client is None or _shared_http_client.is_closed:
        _shared_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=10),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _shared_http_client


class ConvexClient:
    """HTTP client for Convex state persistence."""

    def __init__(self):
        raw_url = os.getenv("CONVEX_SITE_URL", "").rstrip("/")
        # Convex HTTP actions live on .convex.site, not .convex.cloud
        if ".convex.cloud" in raw_url:
            raw_url = raw_url.replace(".convex.cloud", ".convex.site")
            logger.info("Auto-fixed CONVEX_SITE_URL: .convex.cloud → .convex.site")
        self.site_url = raw_url
        self.auth_token = os.getenv("CRON_AUTH_TOKEN", "")
        if not self.site_url:
            logger.warning("CONVEX_SITE_URL not set — state persistence disabled")
        self._client = _get_shared_http_client()

    @property
    def enabled(self) -> bool:
        return bool(self.site_url)

    async def close(self):
        """No-op: the shared HTTP client is not closed here.
        Use ConvexClient.close_shared() to tear down the shared pool."""
        pass

    @classmethod
    async def close_shared(cls):
        """Explicitly close the module-level shared HTTP client."""
        global _shared_http_client
        if _shared_http_client is not None and not _shared_http_client.is_closed:
            await _shared_http_client.aclose()
            _shared_http_client = None

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    async def _post(self, path: str, payload: dict) -> dict:
        """POST to a Convex HTTP action endpoint."""
        if not self.enabled:
            logger.debug("Convex disabled — skipping POST %s", path)
            return {"ok": True, "skipped": True}
        url = f"{self.site_url}{path}"
        resp = await self._client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {self.auth_token}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def _get(self, path: str, params: Optional[dict] = None) -> Any:
        """GET from a Convex HTTP action endpoint."""
        if not self.enabled:
            return []
        url = f"{self.site_url}{path}"
        resp = await self._client.get(
            url,
            params=params or {},
            headers={"Authorization": f"Bearer {self.auth_token}"},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Decision log writes
    # ------------------------------------------------------------------

    async def log_monitor_decision(self, decision: dict) -> dict:
        """Write a monitor decision to the slackMonitorDecisions table."""
        return await self._post("/api/slack/log-monitor-decision", decision)

    async def log_digest_decision(self, decision: dict) -> dict:
        """Write a digest decision to the slackDigestDecisions table."""
        return await self._post("/api/slack/log-digest-decision", decision)

    async def log_evolve_review(self, review: dict) -> dict:
        """Write an evolution review to the slackEvolveReviews table."""
        return await self._post("/api/slack/log-evolve-review", review)

    # ------------------------------------------------------------------
    # Decision log reads
    # ------------------------------------------------------------------

    async def get_recent_decisions(
        self, task: str, limit: int = 48
    ) -> list[dict]:
        """Query recent decisions for a specific task (monitor or digest)."""
        return await self._get(
            "/api/slack/decisions",
            params={"task": task, "limit": str(limit)},
        )

    # ------------------------------------------------------------------
    # Institutional memory
    # ------------------------------------------------------------------

    async def store_memory(self, entry: dict) -> dict:
        """Store a memory entry (topic, decision, context)."""
        return await self._post("/api/slack/store-memory", entry)

    async def search_memory(
        self, topic: str, limit: int = 5
    ) -> list[dict]:
        """Search institutional memory by topic."""
        return await self._get(
            "/api/slack/memory",
            params={"topic": topic, "limit": str(limit)},
        )

    # ------------------------------------------------------------------
    # Task state
    # ------------------------------------------------------------------

    async def update_task_state(self, task_name: str, state: dict) -> dict:
        """Update the running state of a scheduled task."""
        return await self._post(
            "/api/slack/task-state",
            {"taskName": task_name, **state},
        )

    async def get_task_state(self, task_name: str) -> Optional[dict]:
        """Get the current state of a scheduled task."""
        result = await self._get(
            "/api/slack/task-state",
            params={"taskName": task_name},
        )
        return result if result else None

    # ------------------------------------------------------------------
    # Trajectory Replay sync
    # ------------------------------------------------------------------

    async def sync_trajectory(self, trajectory: dict,
                               team_id: Optional[str] = None,
                               member_email: Optional[str] = None) -> dict:
        """Push trajectory metadata to Convex for real-time dashboards."""
        return await self._post("/api/trajectories/sync", {
            "trajectoryId": trajectory.get("trajectory_id", ""),
            "taskName": trajectory.get("task_name", ""),
            "taskGoal": trajectory.get("task_goal"),
            "workflowFamily": trajectory.get("workflow_family"),
            "surface": trajectory.get("surface", "web"),
            "success": trajectory.get("success", False),
            "totalActions": trajectory.get("total_actions", 0),
            "replayCount": trajectory.get("replay_count", 0),
            "driftScore": trajectory.get("drift_score", 0.0),
            "avgTokenSavings": trajectory.get("avg_token_savings", 0.0),
            "avgTimeSavings": trajectory.get("avg_time_savings", 0.0),
            "sourceRunId": trajectory.get("source_run_id"),
            "createdBy": member_email or trajectory.get("created_by"),
            "isShared": trajectory.get("is_shared", False),
            "teamId": team_id or trajectory.get("team_id"),
        })

    async def record_savings(self, run_id: str, trajectory_id: str,
                              tokens_full: int, tokens_actual: int,
                              time_full: float, time_actual: float,
                              requests_full: int = 0, requests_actual: int = 0,
                              app_name: str = "", app_url: str = "",
                              team_id: Optional[str] = None,
                              member_email: Optional[str] = None) -> dict:
        """Record a replay savings measurement in Convex."""
        token_saved_pct = round(
            max(0, (tokens_full - tokens_actual) / max(tokens_full, 1)) * 100, 1
        )
        time_saved_pct = round(
            max(0, (time_full - time_actual) / max(time_full, 0.001)) * 100, 1
        )
        return await self._post("/api/trajectories/record-savings", {
            "runId": run_id,
            "trajectoryId": trajectory_id,
            "runType": "replay",
            "tokensFull": tokens_full,
            "tokensActual": tokens_actual,
            "tokensSavedPct": token_saved_pct,
            "timeFull": time_full,
            "timeActual": time_actual,
            "timeSavedPct": time_saved_pct,
            "requestsFull": requests_full,
            "requestsActual": requests_actual,
            "appName": app_name or None,
            "appUrl": app_url or None,
            "teamId": team_id,
            "memberEmail": member_email,
        })

    # ------------------------------------------------------------------
    # MCP token verification
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Team management
    # ------------------------------------------------------------------

    async def create_team(self, email: str, name: str) -> dict:
        """Create a new team and return { teamId, inviteCode, dashboardUrl }."""
        return await self._post("/api/team/create", {"email": email, "name": name})

    async def join_team(self, token: str, invite_code: str) -> dict:
        """Join a team via invite code. Returns { ok, teamId, name, dashboardUrl }."""
        return await self._post("/api/team/join", {"token": token, "inviteCode": invite_code})

    async def get_team_status(self, token: str) -> dict:
        """Get team status for the token owner."""
        if not self.enabled:
            return {"inTeam": False}
        try:
            url = f"{self.site_url}/api/team/status"
            resp = await self._client.get(
                url,
                params={"token": token},
                headers={"Authorization": f"Bearer {self.auth_token}"},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.debug("get_team_status failed: %s", e)
            return {"inTeam": False}

    async def verify_mcp_token(self, token: str) -> dict:
        """Verify an MCP token against Convex. Returns {valid, email, ...} or {valid: false, reason}."""
        if not self.enabled:
            return {"valid": False, "reason": "convex_disabled"}
        try:
            result = await self._post(
                "/api/mcp/verify-token",
                {"token": token},
            )
            return result
        except Exception as e:
            logger.warning("Convex token verification failed: %s", e)
            # Fail closed — deny access if Convex is unreachable
            return {"valid": False, "reason": "convex_unreachable"}

    async def record_mcp_usage(self, token: str) -> None:
        """Record a token usage event (fire-and-forget)."""
        if not self.enabled:
            return
        try:
            await self._post("/api/mcp/record-usage", {"token": token})
        except Exception as e:
            logger.debug("Failed to record MCP usage: %s", e)

    # ------------------------------------------------------------------
    # Fallback: local JSONL logging
    # ------------------------------------------------------------------

    @staticmethod
    def log_local_fallback(log_file: str, entry: dict):
        """Append a JSON line to a local log file as fallback when Convex is unavailable."""
        import json
        import os

        log_dir = os.path.dirname(log_file)
        os.makedirs(log_dir, exist_ok=True)
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
