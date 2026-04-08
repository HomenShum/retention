#!/usr/bin/env python3
"""
Live Three-Lane Benchmark Runner — produces real eval data.

Modes:
  --live       Full live run: emulator → explore → replay → eval (requires device)
  --offline    Eval existing replay results under multiple model pricings (no device)

Usage:
    cd backend
    python scripts/run_three_lane_live.py --offline
    python scripts/run_three_lane_live.py --offline --replay-ids replay-abc,replay-def
    python scripts/run_three_lane_live.py --live --frontier gpt-5.4:xhigh --small gpt-5.4-mini:high
"""

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("three_lane_live")

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPLAY_DIR = BACKEND_DIR / "data" / "replay_results"
EVAL_DIR = BACKEND_DIR / "data" / "rerun_eval"


# ─── Offline mode: eval existing replays ────────────────────────────────

def run_offline(args):
    """Evaluate existing replay results under multiple model pricings."""
    from app.benchmarks.three_lane_benchmark import (
        run_three_lane_eval_offline,
        run_multi_model_eval_offline,
        get_available_models,
        list_benchmark_results,
    )
    from app.benchmarks.rerun_eval import list_eval_results

    # Get replay IDs
    if args.replay_ids:
        replay_ids = [r.strip() for r in args.replay_ids.split(",")]
    else:
        # Use all available replay results
        if not REPLAY_DIR.exists():
            logger.error(f"No replay results at {REPLAY_DIR}")
            return
        files = sorted(REPLAY_DIR.glob("*.json"))
        if not files:
            logger.error("No replay result files found")
            return
        replay_ids = [f.stem for f in files[:10]]  # Cap at 10 for speed

    logger.info(f"Using {len(replay_ids)} replay results")

    # ── Three-lane eval ──
    if len(replay_ids) >= 3:
        logger.info("Running three-lane eval (first 3 replays as Lane 1/2/3)...")
        result = run_three_lane_eval_offline(
            task_name=args.task_name or "offline_eval",
            lane1_replay_id=replay_ids[0],
            lane2_replay_id=replay_ids[1],
            lane3_replay_id=replay_ids[2],
            frontier_model=args.frontier,
            small_model=args.small,
        )
        print(f"\n{'='*60}")
        print(f"THREE-LANE BENCHMARK: {result.benchmark_id}")
        print(f"{'='*60}")
        for lane in result.lanes:
            sc = lane.scorecard
            if sc:
                print(f"  {lane.label}: grade={sc.grade} composite={sc.composite_score:.2f} "
                      f"cost_savings={sc.cost_savings_pct:.1f}% cost=${sc.cost_replay_usd:.4f}")
        if result.comparison_table:
            print(f"\nComparison Table:")
            print(f"  {'Metric':<25s} {'Lane 1':>8s} {'Lane 2':>8s} {'Lane 3':>8s}")
            print(f"  {'-'*25} {'-'*8} {'-'*8} {'-'*8}")
            for row in result.comparison_table:
                print(f"  {row.metric:<25s} {row.lane_1:>8s} {row.lane_2:>8s} {row.lane_3:>8s}")
        print()

    # ── Multi-model eval ──
    logger.info("Running multi-model eval across all available models...")
    models = args.models.split(",") if args.models else None
    multi = run_multi_model_eval_offline(
        task_name=args.task_name or "multi_model_eval",
        replay_result_ids=replay_ids,
        models=models,
    )
    print(f"\n{'='*60}")
    print(f"MULTI-MODEL COMPARISON: {multi.benchmark_id}")
    print(f"{'='*60}")
    print(f"  {'Model':<28s} {'Comp':>6s} {'F1':>6s} {'Cost%':>7s} {'$/Run':>10s} {'Grade':>6s}")
    print(f"  {'-'*28} {'-'*6} {'-'*6} {'-'*7} {'-'*10} {'-'*6}")
    for m in multi.models:
        print(f"  {m.label:<28s} {m.composite_score:>6.2f} "
              f"{m.targeting_f1:>6.2f} {m.cost_savings_pct:>6.1f}% "
              f"${m.cost_per_run_usd:>9.4f} {m.grade:>6s}")
    print(f"\n  {multi.summary}")

    # ── Summary ──
    evals = list_eval_results()
    benchmarks = list_benchmark_results()
    print(f"\nTotal saved evals: {len(evals)}")
    print(f"Total saved benchmarks: {len(benchmarks)}")


# ─── Live mode: run on real emulator ────────────────────────────────────

async def run_live(args):
    """Run three-lane benchmark on a real emulator."""
    from app.benchmarks.three_lane_benchmark import run_three_lane_benchmark

    # Check for running emulators
    logger.info("Checking for running emulators...")
    try:
        result = subprocess.run(
            ["adb", "devices", "-l"],
            capture_output=True, text=True, timeout=5,
        )
        lines = [l for l in result.stdout.strip().split("\n")[1:] if l.strip() and "device" in l]
        if not lines:
            logger.error("No emulators running. Start one with: emulator -avd <avd_name>")
            logger.info("Available AVDs:")
            avd_result = subprocess.run(
                ["emulator", "-list-avds"],
                capture_output=True, text=True, timeout=5,
            )
            print(avd_result.stdout)
            return
        device_id = lines[0].split()[0]
        logger.info(f"Using device: {device_id}")
    except FileNotFoundError:
        logger.error("adb not found. Set ANDROID_HOME and add platform-tools to PATH.")
        return

    # Initialize mobile client
    logger.info("Initializing mobile MCP client...")
    try:
        from app.agents.device_testing import MobileMCPClient
        mobile_client = MobileMCPClient()
        await mobile_client.start()
    except Exception as e:
        logger.error(f"Failed to start mobile client: {e}")
        logger.info("Falling back to offline mode with existing replay data...")
        run_offline(args)
        return

    # Run three-lane benchmark
    logger.info(f"Starting three-lane benchmark: frontier={args.frontier} small={args.small}")
    try:
        result = await run_three_lane_benchmark(
            task_name=args.task_name or "live_benchmark",
            mobile_client=mobile_client,
            device_id=device_id,
            app_url=args.app_url,
            frontier_model=args.frontier,
            small_model=args.small,
        )

        print(f"\n{'='*60}")
        print(f"LIVE THREE-LANE BENCHMARK: {result.benchmark_id}")
        print(f"{'='*60}")
        for lane in result.lanes:
            sc = lane.scorecard
            if sc:
                print(f"  {lane.label}: grade={sc.grade} composite={sc.composite_score:.2f}")
        print(f"\n  {result.summary}")

        # Run multi-model eval on the produced replay IDs
        replay_ids = [lane.run_id for lane in result.lanes if lane.run_id]
        if replay_ids:
            logger.info("Running multi-model eval on live results...")
            from app.benchmarks.three_lane_benchmark import run_multi_model_eval_offline
            multi = run_multi_model_eval_offline(
                task_name=args.task_name or "live_multi_model",
                replay_result_ids=replay_ids,
            )
            print(f"\nMulti-model comparison: {len(multi.models)} models evaluated")
            print(f"  {multi.summary}")

    except Exception as e:
        logger.error(f"Live benchmark failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            await mobile_client.stop()
        except Exception:
            pass


# ─── Dual-source cost tracking ──────────────────────────────────────────

def sync_usage_tracking():
    """Sync ccusage data and report combined costs."""
    try:
        from app.services.ccusage_tracker import sync_ccusage_to_telemetry, get_ccusage_costs
        from app.services.usage_telemetry import summarize_usage

        logger.info("Syncing ccusage (Claude Code) telemetry...")
        synced = sync_ccusage_to_telemetry(days=1)
        logger.info(f"Synced {synced} ccusage events")

        # Get combined summary
        summary = summarize_usage(days=1)
        total = summary.get("totals", {})
        by_interface = summary.get("by_interface", {})

        print(f"\n{'='*60}")
        print(f"USAGE TRACKING (last 24h)")
        print(f"{'='*60}")
        print(f"  Total tokens: {total.get('total_tokens', 0):,}")
        print(f"  Total cost:   ${total.get('total_cost_usd', 0):.4f}")
        print(f"\n  By source:")
        for iface, data in by_interface.items():
            print(f"    {iface:<20s} {data.get('total_tokens', 0):>10,} tokens  ${data.get('total_cost_usd', 0):>8.4f}")

    except Exception as e:
        logger.warning(f"Usage tracking sync skipped: {e}")


# ─── CLI ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Three-Lane Benchmark Runner")
    parser.add_argument("--offline", action="store_true", help="Eval existing replay data (no device needed)")
    parser.add_argument("--live", action="store_true", help="Run on real emulator")
    parser.add_argument("--frontier", default="gpt-5.4:xhigh", help="Frontier model (default: gpt-5.4:xhigh)")
    parser.add_argument("--small", default="gpt-5.4-mini:high", help="Small model (default: gpt-5.4-mini:high)")
    parser.add_argument("--task-name", default="", help="Workflow/task name")
    parser.add_argument("--app-url", default="", help="App URL for web testing")
    parser.add_argument("--replay-ids", default="", help="Comma-separated replay result IDs")
    parser.add_argument("--models", default="", help="Comma-separated model IDs for multi-model eval")
    parser.add_argument("--track-usage", action="store_true", help="Sync and report ccusage + API costs")

    args = parser.parse_args()

    # Default to offline if neither specified
    if not args.offline and not args.live:
        args.offline = True

    if args.track_usage:
        sync_usage_tracking()

    if args.offline:
        run_offline(args)
    elif args.live:
        asyncio.run(run_live(args))

    # Always report usage at the end
    if args.track_usage or args.live:
        sync_usage_tracking()


if __name__ == "__main__":
    main()
