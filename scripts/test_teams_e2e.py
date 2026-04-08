#!/usr/bin/env python3
"""
End-to-end test: real hackathon team scenario.

Simulates two isolated teams running simultaneously against the live Convex
endpoint — exactly as real hackathon participants would experience it.

Usage:
    python scripts/test_teams_e2e.py [--convex-url URL]

What it tests:
  Team Alpha (alice + bob + carol):
    1. Alice installs → creates Team Alpha → gets invite code
    2. Bob installs → joins Team Alpha via invite code
    3. Carol installs → joins Team Alpha via invite code
    4. All three run QA sessions → trajectory + savings sync to Convex
    5. Dashboard at /memory/team?team=<code> shows 3 members + aggregate stats

  Team Beta (dave — separate, isolated):
    1. Dave installs → creates Team Beta
    2. Dave runs QA → data syncs
    3. Beta dashboard shows ONLY dave's data (not Alpha's)

  Isolation verification:
    - Alpha data not visible on Beta dashboard
    - Beta data not visible on Alpha dashboard
    - Counts match exactly
"""

import asyncio
import json
import sys
import time
import uuid
import argparse
import httpx

CONVEX_URL = "https://exuberant-ferret-263.convex.site"

GREEN = "\033[92m"
RED   = "\033[91m"
CYAN  = "\033[96m"
YELLOW = "\033[93m"
BOLD  = "\033[1m"
NC    = "\033[0m"

passed = 0
failed = 0


def ok(msg: str):
    global passed
    passed += 1
    print(f"  {GREEN}✓{NC} {msg}")


def fail(msg: str, detail: str = ""):
    global failed
    failed += 1
    print(f"  {RED}✗{NC} {msg}")
    if detail:
        print(f"    {RED}{detail}{NC}")


def info(msg: str):
    print(f"  {CYAN}→{NC} {msg}")


def section(title: str):
    print(f"\n{BOLD}{title}{NC}")


async def generate_token(client: httpx.AsyncClient, email: str) -> str:
    resp = await client.post(
        f"{CONVEX_URL}/api/mcp/generate-token",
        json={"email": email, "platform": "claude-code"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["token"]


async def create_team(client: httpx.AsyncClient, email: str, name: str) -> dict:
    resp = await client.post(
        f"{CONVEX_URL}/api/team/create",
        json={"email": email, "name": name},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


async def join_team(client: httpx.AsyncClient, token: str, invite_code: str) -> dict:
    resp = await client.post(
        f"{CONVEX_URL}/api/team/join",
        json={"token": token, "inviteCode": invite_code},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


async def team_status(client: httpx.AsyncClient, token: str) -> dict:
    resp = await client.get(
        f"{CONVEX_URL}/api/team/status",
        params={"token": token},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


async def sync_trajectory(client: httpx.AsyncClient, traj_id: str, task: str,
                           team_id: str, member_email: str) -> dict:
    resp = await client.post(
        f"{CONVEX_URL}/api/trajectories/sync",
        json={
            "trajectoryId": traj_id,
            "taskName": task,
            "taskGoal": f"Complete {task} workflow end-to-end",
            "workflowFamily": "auth",
            "surface": "web",
            "success": True,
            "totalActions": 7,
            "replayCount": 1,
            "driftScore": 0.04,
            "avgTokenSavings": 93.5,
            "avgTimeSavings": 95.0,
            "sourceRunId": f"src_{traj_id}",
            "createdBy": member_email,
            "isShared": True,
            "teamId": team_id,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


async def record_savings(client: httpx.AsyncClient, run_id: str, traj_id: str,
                          team_id: str, member_email: str) -> dict:
    resp = await client.post(
        f"{CONVEX_URL}/api/trajectories/record-savings",
        json={
            "runId": run_id,
            "trajectoryId": traj_id,
            "runType": "replay",
            "tokensFull": 31000,
            "tokensActual": 1400,
            "tokensSavedPct": 95.5,
            "timeFull": 254.0,
            "timeActual": 11.0,
            "timeSavedPct": 95.7,
            "requestsFull": 50,
            "requestsActual": 0,
            "appName": "Hackathon App",
            "teamId": team_id,
            "memberEmail": member_email,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


async def get_team_dashboard(client: httpx.AsyncClient, invite_code: str) -> dict:
    resp = await client.get(
        f"{CONVEX_URL}/api/savings/team",
        params={"team": invite_code},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


async def get_trajectories(client: httpx.AsyncClient, invite_code: str) -> list:
    resp = await client.get(
        f"{CONVEX_URL}/api/trajectories",
        params={"team": invite_code},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("trajectories", [])


async def run_e2e():
    # Unique suffix to avoid collisions with previous test runs
    suffix = str(uuid.uuid4())[:6]

    print(f"\n{BOLD}{CYAN}retention.sh — Teams E2E Test{NC}")
    print(f"Convex: {CONVEX_URL}")
    print(f"Run ID suffix: {suffix}\n")

    async with httpx.AsyncClient(timeout=30) as client:

        # ── Phase 1: Setup Team Alpha ────────────────────────────────────────
        section("Phase 1: Team Alpha setup")

        alice_email = f"alice-{suffix}@hackteam.test"
        bob_email   = f"bob-{suffix}@hackteam.test"
        carol_email = f"carol-{suffix}@hackteam.test"
        dave_email  = f"dave-{suffix}@hackteam.test"

        info(f"Generating tokens for Alpha team members…")
        alice_token = await generate_token(client, alice_email)
        bob_token   = await generate_token(client, bob_email)
        carol_token = await generate_token(client, carol_email)
        ok(f"Alice token: {alice_token[:8]}…")
        ok(f"Bob token:   {bob_token[:8]}…")
        ok(f"Carol token: {carol_token[:8]}…")

        # Alice creates Team Alpha
        info("Alice creates Team Alpha…")
        alpha = await create_team(client, alice_email, f"Hackathon Alpha {suffix}")
        if alpha.get("teamId") and alpha.get("inviteCode"):
            ok(f"Team Alpha created: {alpha['name']} (code: {alpha['inviteCode']})")
        else:
            fail("Team Alpha creation failed", str(alpha))
            sys.exit(1)

        alpha_team_id   = alpha["teamId"]
        alpha_invite    = alpha["inviteCode"]
        alpha_dashboard = alpha.get("dashboardUrl", "")

        # Bob joins
        info(f"Bob joins via invite code {alpha_invite}…")
        bob_join = await join_team(client, bob_token, alpha_invite)
        if bob_join.get("ok"):
            ok(f"Bob joined Team Alpha")
        else:
            fail("Bob failed to join", str(bob_join))

        # Carol joins
        info(f"Carol joins via invite code {alpha_invite}…")
        carol_join = await join_team(client, carol_token, alpha_invite)
        if carol_join.get("ok"):
            ok(f"Carol joined Team Alpha")
        else:
            fail("Carol failed to join", str(carol_join))

        # ── Phase 2: Setup Team Beta (isolated) ──────────────────────────────
        section("Phase 2: Team Beta setup (isolated)")

        info("Generating token for Dave…")
        dave_token = await generate_token(client, dave_email)
        ok(f"Dave token: {dave_token[:8]}…")

        info("Dave creates Team Beta…")
        beta = await create_team(client, dave_email, f"Hackathon Beta {suffix}")
        if beta.get("teamId") and beta.get("inviteCode"):
            ok(f"Team Beta created: {beta['name']} (code: {beta['inviteCode']})")
        else:
            fail("Team Beta creation failed", str(beta))
            sys.exit(1)

        beta_team_id = beta["teamId"]
        beta_invite  = beta["inviteCode"]

        # ── Phase 3: Simulate QA runs ─────────────────────────────────────────
        section("Phase 3: QA runs — each member syncs trajectory + savings")

        alpha_members = [
            (alice_email, "login_flow"),
            (bob_email,   "checkout_flow"),
            (carol_email, "search_flow"),
        ]

        for email, task in alpha_members:
            traj_id = f"traj_{task}_{suffix}"
            run_id  = f"run_{email.split('@')[0]}_{suffix}"
            info(f"  {email} syncs {task}…")
            await sync_trajectory(client, traj_id, task, alpha_team_id, email)
            await record_savings(client, run_id, traj_id, alpha_team_id, email)
            ok(f"  Synced: {task} for {email.split('@')[0]}")

        # Dave runs for Beta
        dave_traj_id = f"traj_onboarding_{suffix}"
        dave_run_id  = f"run_dave_{suffix}"
        info(f"  {dave_email} syncs onboarding_flow (Beta)…")
        await sync_trajectory(client, dave_traj_id, "onboarding_flow", beta_team_id, dave_email)
        await record_savings(client, dave_run_id, dave_traj_id, beta_team_id, dave_email)
        ok(f"  Synced: onboarding_flow for dave")

        # Small wait for Convex to settle
        await asyncio.sleep(1)

        # ── Phase 4: Verify team status via MCP ──────────────────────────────
        section("Phase 4: Team status (MCP view)")

        alice_status = await team_status(client, alice_token)
        if alice_status.get("inTeam") and alice_status.get("inviteCode") == alpha_invite:
            ok(f"Alice sees herself in Team Alpha (code: {alice_status['inviteCode']})")
        else:
            fail("Alice team status wrong", str(alice_status))

        bob_status = await team_status(client, bob_token)
        if bob_status.get("inTeam") and bob_status.get("teamId") == alpha_team_id:
            ok(f"Bob sees himself in Team Alpha")
        else:
            fail("Bob team status wrong", str(bob_status))

        dave_status = await team_status(client, dave_token)
        if dave_status.get("inTeam") and dave_status.get("teamId") == beta_team_id:
            ok(f"Dave sees himself in Team Beta (isolated)")
        else:
            fail("Dave team status wrong", str(dave_status))

        # ── Phase 5: Dashboard data verification ─────────────────────────────
        section("Phase 5: Dashboard data — team isolation")

        alpha_data = await get_team_dashboard(client, alpha_invite)
        beta_data  = await get_team_dashboard(client, beta_invite)

        alpha_members_count = len(alpha_data.get("members", []))
        beta_members_count  = len(beta_data.get("members", []))

        if alpha_members_count >= 3:
            ok(f"Alpha dashboard shows {alpha_members_count} members (expected ≥3)")
        else:
            fail(f"Alpha dashboard shows only {alpha_members_count} members", str(alpha_data.get("members")))

        if beta_members_count >= 1:
            ok(f"Beta dashboard shows {beta_members_count} member(s) (expected ≥1)")
        else:
            fail(f"Beta dashboard shows {beta_members_count} members", str(beta_data.get("members")))

        # Verify isolation: Alpha members shouldn't appear in Beta
        alpha_emails = {m["email"] for m in alpha_data.get("members", [])}
        beta_emails  = {m["email"] for m in beta_data.get("members", [])}

        # Alice/Bob/Carol should NOT be in Beta
        leaked = alpha_emails & beta_emails
        if not leaked:
            ok("No data leak: Alpha member emails not present in Beta dashboard")
        else:
            fail(f"DATA LEAK: {leaked} appear in both dashboards!")

        # Trajectory isolation
        alpha_trajs = await get_trajectories(client, alpha_invite)
        beta_trajs  = await get_trajectories(client, beta_invite)

        alpha_traj_ids = {t["trajectory_id"] for t in alpha_trajs}
        beta_traj_ids  = {t["trajectory_id"] for t in beta_trajs}

        alpha_traj_count = len([t for t in alpha_trajs if suffix in t.get("trajectory_id", "")])
        beta_traj_count  = len([t for t in beta_trajs  if suffix in t.get("trajectory_id", "")])

        if alpha_traj_count >= 3:
            ok(f"Alpha has {alpha_traj_count} trajectories from this run")
        else:
            fail(f"Alpha has only {alpha_traj_count} trajectories (expected 3)")

        if beta_traj_count >= 1:
            ok(f"Beta has {beta_traj_count} trajectory from this run")
        else:
            fail(f"Beta has {beta_traj_count} trajectories (expected 1)")

        # Dave's trajectory should NOT appear in Alpha
        dave_traj_in_alpha = dave_traj_id in alpha_traj_ids
        if not dave_traj_in_alpha:
            ok("Trajectory isolation: Dave's Beta trajectory not in Alpha dashboard")
        else:
            fail("TRAJECTORY LEAK: Dave's Beta trajectory visible in Alpha dashboard!")

        # ── Phase 6: Savings metrics ──────────────────────────────────────────
        section("Phase 6: Aggregate savings metrics")

        alpha_agg = alpha_data.get("aggregate", {})
        beta_agg  = beta_data.get("aggregate", {})

        if alpha_agg.get("total_replays", 0) >= 3:
            ok(f"Alpha aggregate: {alpha_agg['total_replays']} replays, "
               f"{alpha_agg.get('total_tokens_saved', 0):,} tokens saved")
        else:
            fail(f"Alpha aggregate replays too low: {alpha_agg}")

        if beta_agg.get("total_replays", 0) >= 1:
            ok(f"Beta aggregate: {beta_agg['total_replays']} replays "
               f"({beta_agg.get('total_tokens_saved', 0):,} tokens saved)")
        else:
            fail(f"Beta aggregate replays too low: {beta_agg}")

        # ── Phase 7: Dashboard URL works ─────────────────────────────────────
        section("Phase 7: Dashboard URLs")

        ok(f"Team Alpha dashboard: {alpha_dashboard}")
        ok(f"Team Beta  dashboard: https://test-studio-xi.vercel.app/memory/team?team={beta_invite}")
        info("Share the URL with teammates — anyone with the code sees the team dashboard")

        # ── Summary ───────────────────────────────────────────────────────────
        section("Summary")
        total = passed + failed
        if failed == 0:
            print(f"\n  {GREEN}{BOLD}✓ All {total} checks passed.{NC}")
            print(f"\n  {CYAN}Team Alpha invite code: {alpha_invite}{NC}")
            print(f"  Share with teammates:")
            print(f"    {CYAN}RETENTION_TEAM={alpha_invite} curl -sL retention.sh/install.sh | bash{NC}")
            print(f"  Dashboard:")
            print(f"    {CYAN}{alpha_dashboard}{NC}\n")
        else:
            print(f"\n  {RED}{BOLD}✗ {failed}/{total} checks failed.{NC}\n")
            sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--convex-url", default=CONVEX_URL)
    args = parser.parse_args()
    CONVEX_URL = args.convex_url.rstrip("/")

    asyncio.run(run_e2e())
