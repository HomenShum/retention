#!/usr/bin/env python3
"""
Flicker Detection Proof-of-Concept — Real Emulator Test

Runs the full 4-layer flicker detection pipeline on a real Android emulator.
Creates deliberate visual changes (rapid app switching, toggle toggling)
to generate detectable flicker events, then analyzes the recording.

Usage:
    cd backend
    python3 scripts/test_flicker_detection.py

Prerequisites:
    - Android emulator running (emulator-5554)
    - ffmpeg installed
    - adb available
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agents.device_testing.flicker_detection_service import (
    FlickerDetectionService,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("flicker_test")

DEVICE_ID = "emulator-5554"


async def check_prerequisites() -> bool:
    """Verify all prerequisites are met."""
    print("\n" + "=" * 60)
    print("PREREQUISITE CHECK")
    print("=" * 60)

    # Check adb
    r = subprocess.run(["adb", "devices"], capture_output=True, text=True)
    if DEVICE_ID not in r.stdout:
        print(f"❌ Device {DEVICE_ID} not found")
        return False
    print(f"✅ Device {DEVICE_ID} connected")

    # Check ffmpeg
    r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
    if r.returncode != 0:
        print("❌ ffmpeg not installed")
        return False
    print(f"✅ ffmpeg available")

    # Check screenrecord
    r = subprocess.run(
        ["adb", "-s", DEVICE_ID, "shell", "which", "screenrecord"],
        capture_output=True, text=True)
    print(f"✅ screenrecord available on device")

    return True


async def create_flicker_scenario():
    """
    Create deliberate visual changes on the emulator to test detection.

    Strategy: Use fast adb shell commands to create rapid screen transitions.
    All commands are fire-and-forget with minimal sleep to fit within recording.
    """
    logger.info("🎬 Starting flicker scenario: rapid navigation + app switching")

    async def adb_shell(cmd_str: str):
        """Run adb shell command (fire-and-forget style)."""
        proc = await asyncio.create_subprocess_exec(
            "adb", "-s", DEVICE_ID, "shell", cmd_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        await asyncio.wait_for(proc.communicate(), timeout=10)

    async def tap(x: int, y: int):
        await adb_shell(f"input tap {x} {y}")

    async def back():
        await adb_shell("input keyevent KEYCODE_BACK")

    async def home():
        await adb_shell("input keyevent KEYCODE_HOME")

    # Pre-launch Settings
    await adb_shell("am start -n com.android.settings/.Settings")
    await asyncio.sleep(1.0)

    # Phase 1: Rapid in/out navigation (creates screen transitions)
    logger.info("  Phase 1: Rapid navigation")
    for i in range(4):
        await tap(540, 600)   # tap a settings item
        await asyncio.sleep(0.15)
        await back()
        await asyncio.sleep(0.15)

    # Phase 2: App switching (home → settings → home → settings)
    logger.info("  Phase 2: App switching")
    for _ in range(3):
        await home()
        await asyncio.sleep(0.2)
        await adb_shell("am start -n com.android.settings/.Settings")
        await asyncio.sleep(0.3)

    # Phase 3: Rapid tapping in same area (visual feedback flicker)
    logger.info("  Phase 3: Rapid tapping")
    for _ in range(6):
        await tap(540, 700)
        await asyncio.sleep(0.08)

    # Phase 4: Scroll rapidly (creates motion blur / frame changes)
    logger.info("  Phase 4: Rapid scrolling")
    for _ in range(3):
        await adb_shell("input swipe 540 1200 540 400 100")
        await asyncio.sleep(0.1)
        await adb_shell("input swipe 540 400 540 1200 100")
        await asyncio.sleep(0.1)

    logger.info("🎬 Scenario complete")


async def main():
    print("\n" + "=" * 60)
    print("FLICKER DETECTION — PROOF OF CONCEPT")
    print("=" * 60)

    if not await check_prerequisites():
        sys.exit(1)

    # Initialize service (adaptive threshold enabled by default)
    svc = FlickerDetectionService(DEVICE_ID, adaptive_threshold=True)
    print(f"\nSession: {svc.session_id}")
    print(f"Output:  {svc.output_dir}")
    print(f"Optimizations: scene-filter, parallel SSIM, adaptive threshold, "
          f"JPEG, timeline viz")

    # Run detection with scenario
    print("\n" + "=" * 60)
    print("RUNNING OPTIMIZED DETECTION PIPELINE (v2)")
    print("=" * 60)

    report = await svc.run_detection(
        duration=12,
        scenario_fn=create_flicker_scenario,
        package="com.android.settings",
        fps=15,  # 15fps for faster analysis (still catches >66ms flicker)
        record_size="720x1280",
        use_scene_filter=True,
        gpt_verify=False,  # Set True to enable Layer 3 GPT-5.4
        cleanup_frames=False,
    )

    # Print results
    print("\n" + "=" * 60)
    print("RESULTS (v2 — Optimized)")
    print("=" * 60)
    rd = report.to_dict()
    print(json.dumps(rd, indent=2))

    # Summary
    print(f"\n{'=' * 60}")
    print("PERFORMANCE SUMMARY")
    print(f"{'=' * 60}")
    print(f"⏱️  Analysis time: {report.analysis_time_seconds:.1f}s "
          f"(baseline was 18.8s)")
    print(f"🖼️  Scene-filtered frames: {report.total_scene_frames} "
          f"(vs ~175 unfiltered)")
    print(f"📊 SSIM timeline: {report.ssim_timeline_path}")
    if report.surface_delta:
        sd = report.surface_delta
        print(f"📈 SurfaceFlinger delta: {sd.frames_during_test} frames, "
              f"{sd.janky_during_test} janky ({sd.jank_pct_during_test:.1f}%)")

    print(f"\n📁 All artifacts in: {svc.output_dir}")
    print(f"📊 Report: {svc.output_dir}/report.json")
    print(f"🎬 Video: {report.video_path}")
    print(f"🖼️  Frames: {svc.frames_dir}")
    if report.flicker_events:
        for i, ev in enumerate(report.flicker_events):
            print(f"\n🔴 Flicker #{i+1}: {ev.pattern} ({ev.severity})")
            print(f"   Time: {ev.start_time:.3f}s - {ev.end_time:.3f}s")
            print(f"   Duration: {ev.duration_ms:.0f}ms")
            print(f"   SSIM: {[round(s,3) for s in ev.ssim_scores[:6]]}")
            if ev.logcat_events:
                print(f"   Logcat: {len(ev.logcat_events)} correlated events")
            if ev.gpt_analysis:
                print(f"   GPT-5.4: {ev.gpt_analysis[:100]}")
    else:
        print("\n✅ No flicker events detected (screen was stable)")

    return report


if __name__ == "__main__":
    asyncio.run(main())

