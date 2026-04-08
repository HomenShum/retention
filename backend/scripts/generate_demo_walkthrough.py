#!/usr/bin/env python3
"""Generate a narrated mobile demo walkthrough from an Android emulator."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

if TYPE_CHECKING:
    from app.agents.device_testing.demo_walkthrough_service import (
        NarratedWalkthroughService,
        NarrationSegment,
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("demo_walkthrough")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-id", default="emulator-5554")
    parser.add_argument("--duration", type=int, default=18)
    parser.add_argument("--model", default="tts-1")
    parser.add_argument("--voice", default="alloy")
    parser.add_argument("--output-dir", default=None)
    return parser.parse_args()


def check_prerequisites(device_id: str) -> bool:
    """Verify local and device prerequisites for walkthrough generation."""
    checks = [
        (["adb", "devices"], lambda r: device_id in r.stdout, f"device {device_id} connected"),
        (["ffmpeg", "-version"], lambda r: r.returncode == 0, "ffmpeg available"),
        (["ffprobe", "-version"], lambda r: r.returncode == 0, "ffprobe available"),
        (
            ["adb", "-s", device_id, "shell", "which", "screenrecord"],
            lambda r: r.returncode == 0,
            "screenrecord available on device",
        ),
    ]
    for cmd, predicate, label in checks:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if not predicate(result):
            print(f"❌ Missing prerequisite: {label}")
            return False
        print(f"✅ {label}")

    if not os.getenv("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY is not set")
        return False
    print("✅ OPENAI_API_KEY available")
    return True


async def run_settings_walkthrough(device_id: str) -> None:
    """Drive a simple stable Settings-app demo scenario."""

    async def adb_shell(*args: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "adb",
            "-s",
            device_id,
            "shell",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)

    logger.info("Launching Settings walkthrough scenario")
    await adb_shell("am", "start", "-n", "com.android.settings/.Settings")
    await asyncio.sleep(1.2)
    await adb_shell("input", "swipe", "540", "1500", "540", "500", "350")
    await asyncio.sleep(0.8)
    await adb_shell("input", "tap", "540", "720")
    await asyncio.sleep(1.0)
    await adb_shell("input", "keyevent", "KEYCODE_BACK")
    await asyncio.sleep(0.7)
    await adb_shell("input", "swipe", "540", "500", "540", "1500", "300")
    await asyncio.sleep(0.6)
    await adb_shell("input", "keyevent", "KEYCODE_HOME")
    logger.info("Settings walkthrough scenario complete")


def build_default_segments() -> list[NarrationSegment]:
    """Return a small default narration plan for the demo script."""
    from app.agents.device_testing.demo_walkthrough_service import NarrationSegment

    return [
        NarrationSegment(
            title="Launch",
            text=(
                "We start by launching Android Settings to capture a clean, "
                "repeatable walkthrough on the emulator."
            ),
            pause_after_ms=450,
        ),
        NarrationSegment(
            title="Navigate",
            text=(
                "Next, we scroll through the settings list and open a detail "
                "screen, showing how the recording can follow a scripted QA demo."
            ),
            pause_after_ms=400,
        ),
        NarrationSegment(
            title="Wrap Up",
            text=(
                "Finally, we return back out and land on the home screen. The "
                "service combines the raw recording, narration audio, subtitles, "
                "and manifest into a shareable walkthrough artifact."
            ),
            pause_after_ms=0,
        ),
    ]


async def main() -> int:
    """Run the narrated walkthrough demo script."""
    args = parse_args()
    from app.agents.device_testing.demo_walkthrough_service import (
        NarratedWalkthroughService,
    )

    print("\n" + "=" * 60)
    print("NARRATED DEMO WALKTHROUGH")
    print("=" * 60)

    if not check_prerequisites(args.device_id):
        return 1

    service = NarratedWalkthroughService(
        device_id=args.device_id,
        output_dir=args.output_dir,
        model=args.model,
        voice=args.voice,
    )
    result = await service.generate_walkthrough(
        segments=build_default_segments(),
        duration=args.duration,
        scenario_fn=lambda: run_settings_walkthrough(args.device_id),
        stop_when_scenario_complete=True,
    )

    print("\nArtifacts:")
    print(json.dumps(result.to_dict(), indent=2))
    print(f"\n📁 Output directory: {result.output_dir}")
    print(f"🎬 Final narrated video: {result.final_video_path}")
    print(f"📝 Subtitles: {result.subtitles_path}")
    print(f"🧾 Manifest: {result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))