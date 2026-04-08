#!/usr/bin/env python3
"""Seed realistic team trajectory + replay data for the Team Dashboard demo.

Creates:
  - 2 trajectories by Peer A (alice) — YouTube search (emulator) + Gov data (browser)
  - 20 replay results — 2 exploration runs (alice) + 8 replays (bob) × 2 workflows
  - Realistic savings curve: exploration is full cost, replays converge toward ~90% savings

Run: python backend/scripts/seed_team_demo.py
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Resolve paths relative to repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
TRAJ_DIR = REPO_ROOT / "backend" / "data" / "trajectories"
# Replay results must go to backend/app/data/replay_results/ (where trajectory_replay.py reads from)
REPLAY_DIR = REPO_ROOT / "backend" / "app" / "data" / "replay_results"

BASE_TIME = datetime(2026, 3, 25, 9, 0, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.isoformat()


def make_step(idx, action, label, fingerprint_before, fingerprint_after,
              duration_ms=800, tool=None, tool_params=None, success=True):
    step = {
        "step_index": idx,
        "timestamp": iso(BASE_TIME + timedelta(seconds=idx * 15)),
        "action": action,
        "state_before": {"screen": fingerprint_before},
        "state_after": {"screen": fingerprint_after},
        "success": success,
        "error": None,
        "failure_type": None,
        "recovery_strategy": None,
        "recovery_successful": None,
        "notes": None,
        "semantic_label": label,
        "screen_fingerprint_before": fingerprint_before,
        "screen_fingerprint_after": fingerprint_after,
        "coordinates": None,
        "duration_ms": duration_ms,
        "mcp_tool_calls": [{"tool": tool, "params": tool_params or {}}] if tool else None,
    }
    return step


# ── Trajectory 1: YouTube Search on Android Emulator ─────────────────────

def build_youtube_trajectory():
    traj_id = "traj_yt_alice_001"
    steps = [
        make_step(0, "Launch YouTube app", "open_app",
                  "home_screen", "yt_splash",
                  duration_ms=2400, tool="mobile.launch_app",
                  tool_params={"package": "com.google.android.youtube"}),
        make_step(1, "Wait for feed to load", "wait_feed",
                  "yt_splash", "yt_home_feed",
                  duration_ms=3200),
        make_step(2, "Tap search icon", "tap_search",
                  "yt_home_feed", "yt_search_bar",
                  duration_ms=600, tool="mobile.tap",
                  tool_params={"x": 980, "y": 120}),
        make_step(3, "Type search query", "type_query",
                  "yt_search_bar", "yt_search_typing",
                  duration_ms=1800, tool="mobile.type",
                  tool_params={"text": "latest claude code updates"}),
        make_step(4, "Submit search", "submit_search",
                  "yt_search_typing", "yt_search_results",
                  duration_ms=2800, tool="mobile.key_press",
                  tool_params={"key": "enter"}),
        make_step(5, "Scroll results to find relevant video", "scroll_results",
                  "yt_search_results", "yt_results_scrolled",
                  duration_ms=1500, tool="mobile.swipe",
                  tool_params={"startY": 1800, "endY": 600}),
        make_step(6, "Tap first relevant result", "tap_result",
                  "yt_results_scrolled", "yt_video_playing",
                  duration_ms=900, tool="mobile.tap",
                  tool_params={"x": 540, "y": 450}),
        make_step(7, "Verify video playback started", "verify_playback",
                  "yt_video_playing", "yt_video_confirmed",
                  duration_ms=2000, tool="ta.verify.checkpoint",
                  tool_params={"check": "video_playing", "expected": True}),
    ]

    return {
        "trajectory_id": traj_id,
        "task_name": "youtube_search_claude_updates",
        "task_goal": "Navigate to YouTube and search 'latest claude code updates', play first relevant result",
        "device_id": "emulator-5554",
        "started_at": iso(BASE_TIME),
        "completed_at": iso(BASE_TIME + timedelta(minutes=4, seconds=15)),
        "steps": steps,
        "success": True,
        "total_actions": len(steps),
        "total_failures": 0,
        "recovery_success_rate": 1.0,
        "evaluation_score": 0.95,
        "metadata": {
            "created_by": "alice@tastudios.com",
            "peer": "A",
            "device_model": "Pixel 8",
            "android_api": 35,
        },
        "workflow_family": "media_search",
        "surface": "android",
        "drift_score": 0.08,
        "replay_count": 10,
        "last_validated_at": iso(BASE_TIME + timedelta(days=4)),
        "avg_token_savings": 0.845,
        "avg_time_savings": 0.72,
        "source_run_id": "run_yt_alice_001",
        "success_conditions": ["video_playing"],
        "failure_conditions": ["search_no_results", "app_crash"],
        "source_tokens_actual": 34200,
        "source_time_actual_s": 255.0,
        "source_git_commit": "58ca774",
        "source_git_branch": "main",
        "source_git_dirty": False,
    }


# ── Trajectory 2: Government Data Retrieval (Browser) ────────────────────

def build_gov_trajectory():
    traj_id = "traj_gov_alice_001"
    steps = [
        make_step(0, "Navigate to data.gov portal", "navigate_portal",
                  "browser_blank", "gov_homepage",
                  duration_ms=3500, tool="playwright.navigate",
                  tool_params={"url": "https://data.gov"}),
        make_step(1, "Accept cookie banner", "accept_cookies",
                  "gov_homepage", "gov_homepage_clean",
                  duration_ms=800, tool="playwright.click",
                  tool_params={"selector": "#accept-cookies"}),
        make_step(2, "Navigate to datasets section", "nav_datasets",
                  "gov_homepage_clean", "gov_datasets_page",
                  duration_ms=2200, tool="playwright.click",
                  tool_params={"selector": "a[href='/datasets']"}),
        make_step(3, "Search for population statistics", "search_dataset",
                  "gov_datasets_page", "gov_search_results",
                  duration_ms=2800, tool="playwright.fill",
                  tool_params={"selector": "#search-input", "value": "population statistics 2025"}),
        make_step(4, "Filter by CSV format", "filter_csv",
                  "gov_search_results", "gov_filtered_results",
                  duration_ms=1200, tool="playwright.click",
                  tool_params={"selector": "[data-format='csv']"}),
        make_step(5, "Select first matching dataset", "select_dataset",
                  "gov_filtered_results", "gov_dataset_detail",
                  duration_ms=900, tool="playwright.click",
                  tool_params={"selector": ".dataset-card:first-child a"}),
        make_step(6, "Click download CSV link", "download_csv",
                  "gov_dataset_detail", "gov_download_started",
                  duration_ms=1500, tool="playwright.click",
                  tool_params={"selector": ".download-btn[data-format='csv']"}),
        make_step(7, "Wait for download to complete", "wait_download",
                  "gov_download_started", "gov_download_complete",
                  duration_ms=4000),
        make_step(8, "Verify CSV has expected columns", "verify_columns",
                  "gov_download_complete", "gov_csv_verified",
                  duration_ms=1200, tool="ta.verify.checkpoint",
                  tool_params={"check": "csv_columns", "expected": ["state", "population", "year"]}),
        make_step(9, "Extract summary row and log result", "extract_summary",
                  "gov_csv_verified", "gov_task_complete",
                  duration_ms=800, tool="ta.verify.checkpoint",
                  tool_params={"check": "summary_extracted", "expected": True}),
    ]

    return {
        "trajectory_id": traj_id,
        "task_name": "gov_data_retrieval",
        "task_goal": "Navigate to legacy government data portal and retrieve population statistics CSV dataset",
        "device_id": "browser-chrome-01",
        "started_at": iso(BASE_TIME + timedelta(hours=2)),
        "completed_at": iso(BASE_TIME + timedelta(hours=2, minutes=5, seconds=12)),
        "steps": steps,
        "success": True,
        "total_actions": len(steps),
        "total_failures": 0,
        "recovery_success_rate": 1.0,
        "evaluation_score": 0.92,
        "metadata": {
            "created_by": "alice@tastudios.com",
            "peer": "A",
            "browser": "Chrome 130",
            "viewport": {"width": 1920, "height": 1080},
        },
        "workflow_family": "data_retrieval",
        "surface": "web",
        "drift_score": 0.12,
        "replay_count": 10,
        "last_validated_at": iso(BASE_TIME + timedelta(days=4)),
        "avg_token_savings": 0.81,
        "avg_time_savings": 0.68,
        "source_run_id": "run_gov_alice_001",
        "success_conditions": ["csv_columns_match", "summary_extracted"],
        "failure_conditions": ["portal_down", "dataset_not_found"],
        "source_tokens_actual": 28500,
        "source_time_actual_s": 312.0,
        "source_git_commit": "58ca774",
        "source_git_branch": "main",
        "source_git_dirty": False,
    }


# ── Replay Results ───────────────────────────────────────────────────────

# Savings curves: exploration (runs 1-2) then replays (runs 3-10)
YT_CURVE = [
    # run, tokens, time_s, peer, is_replay
    (1, 34200, 255.0, "alice@tastudios.com", False),
    (2, 31800, 242.0, "alice@tastudios.com", False),
    (3, 8900,   98.0, "bob@tastudios.com",   True),
    (4, 7200,   84.0, "bob@tastudios.com",   True),
    (5, 5800,   72.0, "bob@tastudios.com",   True),
    (6, 5100,   65.0, "bob@tastudios.com",   True),
    (7, 4500,   58.0, "bob@tastudios.com",   True),
    (8, 4000,   52.0, "bob@tastudios.com",   True),
    (9, 3600,   47.0, "bob@tastudios.com",   True),
    (10, 3300,  44.0, "bob@tastudios.com",   True),
]

GOV_CURVE = [
    (1, 28500, 312.0, "alice@tastudios.com", False),
    (2, 26200, 290.0, "alice@tastudios.com", False),
    (3, 7800,  105.0, "bob@tastudios.com",   True),
    (4, 6400,   88.0, "bob@tastudios.com",   True),
    (5, 5100,   74.0, "bob@tastudios.com",   True),
    (6, 4600,   67.0, "bob@tastudios.com",   True),
    (7, 4100,   60.0, "bob@tastudios.com",   True),
    (8, 3700,   54.0, "bob@tastudios.com",   True),
    (9, 3400,   49.0, "bob@tastudios.com",   True),
    (10, 3100,  45.0, "bob@tastudios.com",   True),
]


def build_replay_result(workflow, traj_id, run_num, tokens_replay, time_replay_s,
                        peer, is_replay, baseline_tokens, baseline_time_s):
    token_savings_pct = max(0, (baseline_tokens - tokens_replay) / baseline_tokens * 100)
    time_savings_pct = max(0, (baseline_time_s - time_replay_s) / baseline_time_s * 100)

    ts = BASE_TIME + timedelta(days=run_num - 1, hours=run_num, minutes=run_num * 7)
    run_id = f"replay-{uuid.uuid4().hex[:12]}"

    steps_total = 8 if "youtube" in workflow else 10
    steps_matched = steps_total if is_replay else 0
    steps_drifted = 0 if is_replay or run_num == 1 else 1

    return {
        "trajectory_id": traj_id,
        "replay_run_id": run_id,
        "workflow": workflow,
        "success": True,
        "total_steps": steps_total,
        "steps_executed": steps_total,
        "steps_matched": steps_matched,
        "steps_drifted": steps_drifted,
        "drift_point": None,
        "drift_score": round(steps_drifted / steps_total, 3),
        "fallback_to_exploration": not is_replay,
        "token_usage": {
            "estimated_replay_tokens": tokens_replay,
            "full_run_baseline_tokens": baseline_tokens,
        },
        "time_seconds": time_replay_s,
        "comparison_with_full": {
            "token_savings_pct": round(token_savings_pct, 1),
            "time_savings_pct": round(time_savings_pct, 1),
            "tokens_full": baseline_tokens,
            "tokens_replay": tokens_replay,
            "time_full_s": baseline_time_s,
            "time_replay_s": time_replay_s,
            "baseline_source": "recorded",
        },
        "per_step_results": [],
        "timestamp": iso(ts),
        "metadata": {
            "created_by": peer if not is_replay else None,
            "replayed_by": peer if is_replay else None,
            "run_number": run_num,
            "is_replay": is_replay,
        },
    }


def main():
    # Clean existing seed data
    for d in [TRAJ_DIR, REPLAY_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    # Write trajectories
    yt_traj = build_youtube_trajectory()
    yt_dir = TRAJ_DIR / "youtube_search_claude_updates"
    yt_dir.mkdir(parents=True, exist_ok=True)
    (yt_dir / f"{yt_traj['trajectory_id']}.json").write_text(json.dumps(yt_traj, indent=2))
    print(f"  Wrote trajectory: {yt_dir / yt_traj['trajectory_id']}.json")

    gov_traj = build_gov_trajectory()
    gov_dir = TRAJ_DIR / "gov_data_retrieval"
    gov_dir.mkdir(parents=True, exist_ok=True)
    (gov_dir / f"{gov_traj['trajectory_id']}.json").write_text(json.dumps(gov_traj, indent=2))
    print(f"  Wrote trajectory: {gov_dir / gov_traj['trajectory_id']}.json")

    # Write replay results
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for run_num, tokens, time_s, peer, is_replay in YT_CURVE:
        result = build_replay_result(
            "youtube_search_claude_updates", yt_traj["trajectory_id"],
            run_num, tokens, time_s, peer, is_replay,
            baseline_tokens=34200, baseline_time_s=255.0,
        )
        path = REPLAY_DIR / f"{result['replay_run_id']}.json"
        path.write_text(json.dumps(result, indent=2))
        count += 1

    for run_num, tokens, time_s, peer, is_replay in GOV_CURVE:
        result = build_replay_result(
            "gov_data_retrieval", gov_traj["trajectory_id"],
            run_num, tokens, time_s, peer, is_replay,
            baseline_tokens=28500, baseline_time_s=312.0,
        )
        path = REPLAY_DIR / f"{result['replay_run_id']}.json"
        path.write_text(json.dumps(result, indent=2))
        count += 1

    print(f"  Wrote {count} replay results to {REPLAY_DIR}")

    # Verify
    traj_count = sum(1 for _ in TRAJ_DIR.rglob("*.json"))
    replay_count = sum(1 for _ in REPLAY_DIR.glob("*.json"))
    print(f"\nSeed complete: {traj_count} trajectories, {replay_count} replay results")
    print(f"  Trajectories: {TRAJ_DIR}")
    print(f"  Replays: {REPLAY_DIR}")


if __name__ == "__main__":
    main()
