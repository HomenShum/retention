#!/usr/bin/env python3
"""
Sauce Labs Android Golden Bug Benchmark — deterministic F1 scoring on mobile.

Runs the 8 Sauce Labs Android golden bug cases through retention.sh's mobile QA
pipeline and produces precision/recall/F1 scores comparable to the web
planted-bug benchmark.

Usage:
    cd backend
    python scripts/run_sauce_android_benchmark.py
    python scripts/run_sauce_android_benchmark.py --device emulator-5554
    python scripts/run_sauce_android_benchmark.py --dry-run
"""

import asyncio
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure backend package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sauce_android_benchmark")

BACKEND_DIR = Path(__file__).resolve().parent.parent
GOLDEN_BUGS_PATH = BACKEND_DIR / "data" / "benchmark_apps" / "sauce_android_golden.json"
REPORTS_DIR = BACKEND_DIR / "data" / "benchmark_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def check_emulator(device_id: str = "emulator-5554") -> str:
    """Verify an Android emulator is reachable via ADB. Returns the device serial."""
    try:
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        print("ERROR: 'adb' not found in PATH. Install Android SDK platform-tools.")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("ERROR: 'adb devices' timed out. ADB server may be unresponsive.")
        sys.exit(1)

    lines = result.stdout.strip().splitlines()
    connected = [l.split()[0] for l in lines[1:] if l.strip() and "offline" not in l]

    if not connected:
        print("ERROR: No Android emulator detected.")
        print("       Run:  emulator -avd Pixel_8 -no-audio -no-window")
        print("       Then retry this script.")
        sys.exit(1)

    if "emulator" not in result.stdout:
        print(f"ERROR: No emulator in adb devices output (found: {connected}).")
        print("       Run:  emulator -avd Pixel_8 -no-audio -no-window")
        sys.exit(1)

    # Prefer the requested device_id if available, otherwise use first emulator
    if device_id in connected:
        serial = device_id
    else:
        serial = next((d for d in connected if d.startswith("emulator-")), connected[0])
        logger.warning(
            "Requested device %s not found; using %s instead.", device_id, serial
        )

    logger.info("Emulator ready: %s", serial)
    return serial


def verify_apk_installed(serial: str, package: str) -> bool:
    """Check that the Sauce Labs APK is installed on the device."""
    result = subprocess.run(
        ["adb", "-s", serial, "shell", "pm", "list", "packages", package],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return package in result.stdout


# ---------------------------------------------------------------------------
# Golden bug loading
# ---------------------------------------------------------------------------

def load_golden_bugs() -> list[dict]:
    if not GOLDEN_BUGS_PATH.exists():
        raise FileNotFoundError(f"Golden bugs file not found: {GOLDEN_BUGS_PATH}")
    with open(GOLDEN_BUGS_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Pipeline runner (single bug)
# ---------------------------------------------------------------------------

async def run_single_bug(bug: dict, serial: str, dry_run: bool = False) -> dict:
    """
    Run one golden bug through the mobile QA pipeline.

    In real usage this delegates to QAPipelineService. A dry-run mode is
    provided so the script can be exercised end-to-end without a live agent.
    """
    bug_id = bug["bug_id"]
    package = bug["bug_report"]["app_package"]

    logger.info("Running %s: %s", bug_id, bug["name"])

    if dry_run:
        # Simulate a 50 % hit-rate for dry-run testing
        import hashlib
        h = int(hashlib.md5(bug_id.encode()).hexdigest(), 16)
        reproduced = (h % 2 == 0)
        analysis = "Reproduction Success: Yes" if reproduced else "Reproduction Success: No"
        return {
            "bug_id": bug_id,
            "reproduced": reproduced,
            "analysis": analysis,
            "duration_s": 0.1,
            "dry_run": True,
        }

    # --- Live path ---
    try:
        from app.agents.qa_pipeline.qa_pipeline_service import QAPipelineService

        # QAPipelineService expects a mobile MCP client; pass None when running
        # in standalone benchmark mode — the service degrades gracefully.
        service = QAPipelineService(mobile_mcp_client=None)

        start = time.time()
        result_chunks = []
        async for event in service.run_pipeline(
            app_name=bug["name"],
            package_name=package,
            device_id=serial,
            bug_report=bug["bug_report"],
        ):
            result_chunks.append(event)

        duration_s = round(time.time() - start, 1)

        # Collect the final analysis text from streamed events
        analysis_text = ""
        for chunk in result_chunks:
            if isinstance(chunk, dict):
                analysis_text += chunk.get("content", "") or chunk.get("text", "")

        expectation = bug["auto_check"]["expectation"]
        required_phrases = bug["auto_check"]["require_text_in_analysis"]
        reproduced = all(p in analysis_text for p in required_phrases)

        return {
            "bug_id": bug_id,
            "reproduced": reproduced,
            "analysis": analysis_text[:500],
            "duration_s": duration_s,
            "dry_run": False,
        }

    except ImportError as exc:
        logger.error("Could not import QAPipelineService: %s", exc)
        return {
            "bug_id": bug_id,
            "reproduced": False,
            "analysis": f"Import error: {exc}",
            "duration_s": 0,
            "error": str(exc),
        }
    except Exception as exc:
        logger.error("Error running %s: %s", bug_id, exc, exc_info=True)
        return {
            "bug_id": bug_id,
            "reproduced": False,
            "analysis": f"Runtime error: {exc}",
            "duration_s": 0,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_results(bug_results: list[dict], golden_bugs: list[dict]) -> dict:
    """Compute precision, recall, F1 against the golden bug set."""
    total_bugs = len(golden_bugs)
    bug_ids = {b["bug_id"] for b in golden_bugs}

    reproduced_ids = {r["bug_id"] for r in bug_results if r.get("reproduced")}
    not_reproduced_ids = bug_ids - reproduced_ids

    # For golden bug benchmarks every bug is a "planted" positive.
    # TP = correctly reproduced, FN = missed, FP = 0 by construction
    # (we only run against known bugs, so there are no false positives).
    true_positives = len(reproduced_ids & bug_ids)
    false_negatives = len(not_reproduced_ids)
    false_positives = 0  # No free-form anomaly detection — only known cases

    precision = true_positives / max(true_positives + false_positives, 1)
    recall = true_positives / max(total_bugs, 1)
    f1 = 2 * precision * recall / max(precision + recall, 0.001)

    return {
        "total_golden_bugs": total_bugs,
        "total_run": len(bug_results),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "reproduced_bugs": sorted(reproduced_ids),
        "missed_bugs": sorted(not_reproduced_ids),
    }


# ---------------------------------------------------------------------------
# Main benchmark loop
# ---------------------------------------------------------------------------

async def run_benchmark(device_id: str = "emulator-5554", dry_run: bool = False) -> dict:
    """Full benchmark: preflight → load bugs → run pipeline → score → save report."""

    # Preflight checks
    if dry_run:
        logger.info("DRY RUN mode — skipping ADB and APK checks.")
        serial = device_id
    else:
        serial = check_emulator(device_id)
        package = "com.saucelabs.mydemoapp.android"
        if not verify_apk_installed(serial, package):
            logger.warning(
                "%s not installed on %s. Run: bash backend/scripts/setup_sauce_android.sh",
                package,
                serial,
            )

    # Load golden bug definitions
    golden_bugs = load_golden_bugs()
    logger.info("Loaded %d Sauce Labs golden bugs from %s", len(golden_bugs), GOLDEN_BUGS_PATH)

    # Run each bug through the pipeline
    bug_results = []
    total_duration = 0.0
    for bug in golden_bugs:
        result = await run_single_bug(bug, serial, dry_run=dry_run)
        bug_results.append(result)
        total_duration += result.get("duration_s", 0)
        status = "REPRODUCED" if result.get("reproduced") else "MISSED"
        logger.info("  %s -> %s (%.1fs)", result["bug_id"], status, result.get("duration_s", 0))

    # Score
    scores = score_results(bug_results, golden_bugs)
    scores["total_duration_s"] = round(total_duration, 1)

    # Print summary
    sep = "=" * 60
    logger.info("\n%s", sep)
    logger.info("SAUCE LABS ANDROID BENCHMARK RESULTS")
    logger.info("%s", sep)
    logger.info("App:             Sauce Labs My Demo App (Android)")
    logger.info("Package:         com.saucelabs.mydemoapp.android")
    logger.info("Device:          %s", serial)
    logger.info("Golden bugs:     %d", scores["total_golden_bugs"])
    logger.info("True Positives:  %d", scores["true_positives"])
    logger.info("False Negatives: %d", scores["false_negatives"])
    logger.info("Precision:       %.3f", scores["precision"])
    logger.info("Recall:          %.3f", scores["recall"])
    logger.info("F1:              %.3f", scores["f1"])
    logger.info("Duration:        %.1fs", scores["total_duration_s"])
    logger.info("Reproduced:      %s", scores["reproduced_bugs"])
    logger.info("Missed:          %s", scores["missed_bugs"])
    logger.info("%s", sep)

    # Build report
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "benchmark_type": "sauce_android_golden_bugs",
        "app_name": "Sauce Labs My Demo App",
        "app_package": "com.saucelabs.mydemoapp.android",
        "device_id": serial,
        "dry_run": dry_run,
        "scores": scores,
        "bug_results": bug_results,
    }

    report_path = REPORTS_DIR / f"sauce_android_benchmark_{ts}.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Report saved: %s", report_path)

    # Update latest.json
    latest_path = REPORTS_DIR / "latest.json"
    latest: dict = {}
    if latest_path.exists():
        try:
            latest = json.loads(latest_path.read_text())
        except json.JSONDecodeError:
            pass

    latest["sauce_android_benchmark"] = {
        "app_name": "Sauce Labs My Demo App",
        "precision": scores["precision"],
        "recall": scores["recall"],
        "f1": scores["f1"],
        "true_positives": scores["true_positives"],
        "false_negatives": scores["false_negatives"],
        "total_golden": scores["total_golden_bugs"],
        "reproduced_bugs": scores["reproduced_bugs"],
        "missed_bugs": scores["missed_bugs"],
        "total_duration_s": scores["total_duration_s"],
        "device_id": serial,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    latest_path.write_text(json.dumps(latest, indent=2, default=str))

    print(f"\n{'='*50}")
    print(f"F1={scores['f1']} | P={scores['precision']} | R={scores['recall']}")
    print(f"Reproduced {scores['true_positives']}/{scores['total_golden_bugs']} golden bugs")
    print(f"{'='*50}\n")

    return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run Sauce Labs Android golden bug benchmark"
    )
    parser.add_argument(
        "--device",
        default="emulator-5554",
        help="ADB device serial (default: emulator-5554)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate pipeline results without launching agents (for CI/testing)",
    )
    args = parser.parse_args()

    asyncio.run(run_benchmark(device_id=args.device, dry_run=args.dry_run))
