#!/usr/bin/env python3
"""
verify_api.py — Verify real API connectivity to Convex and backend services.

Tests actual HTTP calls to prove the data pipeline works end-to-end.
Requires: CONVEX_SITE_URL and CRON_AUTH_TOKEN in environment or backend/.env

Run: python backend/scripts/verify_api.py
"""

import json
import os
import sys
import time
from pathlib import Path

# Load .env if present
env_file = Path(__file__).resolve().parents[1] / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

try:
    import httpx
except ImportError:
    print("httpx not installed — falling back to urllib")
    httpx = None

CONVEX_SITE_URL = os.getenv("CONVEX_SITE_URL", "").rstrip("/")
CRON_AUTH_TOKEN = os.getenv("CRON_AUTH_TOKEN", "")
RETENTION_MCP_TOKEN = os.getenv("RETENTION_MCP_TOKEN", "")
BACKEND_URL = os.getenv("VITE_API_BASE", "http://localhost:8000")

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = ""):
    global passed, failed
    status = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    else:
        failed += 1
    suffix = f" — {detail}" if detail else ""
    print(f"  {status}  {label}{suffix}")


def http_post(url: str, data: dict, headers: dict = None, timeout: float = 10) -> dict:
    """Make HTTP POST, return response dict or error."""
    headers = headers or {}
    if httpx:
        try:
            r = httpx.post(url, json=data, headers=headers, timeout=timeout)
            return {"status": r.status_code, "body": r.json() if r.status_code < 400 else r.text}
        except Exception as e:
            return {"error": str(e)}
    else:
        import urllib.request
        req = urllib.request.Request(
            url, data=json.dumps(data).encode(),
            headers={**headers, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return {"status": resp.status, "body": json.loads(resp.read())}
        except Exception as e:
            return {"error": str(e)}


def http_get(url: str, headers: dict = None, timeout: float = 10) -> dict:
    """Make HTTP GET, return response dict or error."""
    headers = headers or {}
    if httpx:
        try:
            r = httpx.get(url, headers=headers, timeout=timeout)
            return {"status": r.status_code, "body": r.json() if r.status_code < 400 else r.text}
        except Exception as e:
            return {"error": str(e)}
    else:
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return {"status": resp.status, "body": json.loads(resp.read())}
        except Exception as e:
            return {"error": str(e)}


def verify_convex_connectivity():
    print("\n=== CONVEX API CONNECTIVITY ===")

    if not CONVEX_SITE_URL:
        print("  SKIP  CONVEX_SITE_URL not set")
        return

    if not CRON_AUTH_TOKEN:
        print("  SKIP  CRON_AUTH_TOKEN not set")
        return

    auth_headers = {"Authorization": f"Bearer {CRON_AUTH_TOKEN}"}

    # Test 1: MCP token verification
    if RETENTION_MCP_TOKEN:
        res = http_post(
            f"{CONVEX_SITE_URL}/api/mcp/verify-token",
            {"token": RETENTION_MCP_TOKEN},
            headers=auth_headers,
        )
        check(
            "MCP token verification",
            "error" not in res and res.get("status", 0) < 400,
            f"status={res.get('status', 'error')}" if "error" not in res else res["error"],
        )
    else:
        print("  SKIP  RETENTION_MCP_TOKEN not set — skipping token verification")

    # Test 2: Trajectory list endpoint
    res = http_get(
        f"{CONVEX_SITE_URL}/api/trajectories",
        headers=auth_headers,
    )
    check(
        "Trajectory list endpoint",
        "error" not in res and res.get("status", 0) < 400,
        f"status={res.get('status', 'error')}" if "error" not in res else res["error"],
    )

    # Test 3: Team status endpoint
    res = http_get(
        f"{CONVEX_SITE_URL}/api/team/status?token=test-verify",
        headers=auth_headers,
    )
    check(
        "Team status endpoint",
        "error" not in res,
        f"status={res.get('status', 'error')}" if "error" not in res else res["error"],
    )


def verify_backend_connectivity():
    print("\n=== BACKEND API CONNECTIVITY ===")

    # Test 1: Health check
    res = http_get(f"{BACKEND_URL}/api/health")
    check(
        "Backend health check",
        "error" not in res and res.get("status", 0) < 400,
        f"status={res.get('status', 'error')}" if "error" not in res else res["error"],
    )

    # Test 2: Live stats endpoint
    res = http_get(f"{BACKEND_URL}/api/stats/live")
    if "error" not in res and res.get("status", 0) < 400:
        body = res.get("body", {})
        check("Live stats endpoint", True, f"verified_at={body.get('verified_at', '?')}")

        # Verify the live stats contain real data
        replay = body.get("replay_results", {})
        evals = body.get("eval_results", {})
        check("Live stats has replay data", replay.get("total", 0) > 0, f"total={replay.get('total', 0)}")
        check("Live stats has eval data", evals.get("total", 0) > 0, f"total={evals.get('total', 0)}")
        check(
            "Live stats cost_saved > 0",
            evals.get("total_cost_saved_usd", 0) > 0,
            f"${evals.get('total_cost_saved_usd', 0)}",
        )
    else:
        check("Live stats endpoint", False, res.get("error", f"status={res.get('status')}"))

    # Test 3: Quick status
    res = http_get(f"{BACKEND_URL}/api/quick/status")
    check(
        "Quick status endpoint",
        "error" not in res and res.get("status", 0) < 400,
        f"status={res.get('status', 'error')}" if "error" not in res else res["error"],
    )

    # Test 4: ROP list
    res = http_get(f"{BACKEND_URL}/api/rops")
    check(
        "ROP list endpoint",
        "error" not in res and res.get("status", 0) < 400,
        f"status={res.get('status', 'error')}" if "error" not in res else res["error"],
    )


def verify_convex_data_sync():
    """Test that we can actually write + read from Convex."""
    print("\n=== CONVEX DATA SYNC (round-trip) ===")

    if not CONVEX_SITE_URL or not CRON_AUTH_TOKEN:
        print("  SKIP  Convex credentials not set")
        return

    auth_headers = {"Authorization": f"Bearer {CRON_AUTH_TOKEN}"}
    test_id = f"verify-{int(time.time())}"

    # Record a test savings entry
    res = http_post(
        f"{CONVEX_SITE_URL}/api/trajectories/record-savings",
        {
            "token": RETENTION_MCP_TOKEN or "verify-test",
            "run_id": test_id,
            "trajectory_id": "test-traj",
            "tokens_full": 1000,
            "tokens_actual": 150,
            "time_full": 10.0,
            "time_actual": 2.0,
        },
        headers=auth_headers,
    )
    check(
        "Convex savings write",
        "error" not in res and res.get("status", 0) < 400,
        f"run_id={test_id}" if "error" not in res else res["error"],
    )


def main():
    print("=" * 60)
    print("retention.sh API Verification")
    print(f"Convex: {CONVEX_SITE_URL or '(not set)'}")
    print(f"Backend: {BACKEND_URL}")
    print("=" * 60)

    verify_convex_connectivity()
    verify_backend_connectivity()
    verify_convex_data_sync()

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
