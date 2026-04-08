#!/usr/bin/env python3
"""
Juice Shop Benchmark — deterministic F1 scoring against OWASP Juice Shop.

1. Preflight check: verify Juice Shop is reachable on http://localhost:3000
2. Load the ground-truth bug manifest (juice_shop_bugs.json)
3. Run the Playwright self-test agent against the live Docker instance
4. Compare discovered anomalies to the manifest using keyword matching
5. Compute precision, recall, F1

Usage:
    cd backend
    python scripts/run_juice_shop_benchmark.py

Prerequisites:
    Juice Shop must be running. Start it with:
        bash scripts/setup_juice_shop.sh
    or:
        docker run -d -p 3000:3000 bkimminich/juice-shop
"""

import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.agents.self_testing.playwright_engine import pw_batch_test

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("juice_shop_benchmark")

BACKEND_DIR = Path(__file__).resolve().parent.parent
BENCHMARK_APPS_DIR = BACKEND_DIR / "data" / "benchmark_apps"
REPORTS_DIR = BACKEND_DIR / "data" / "benchmark_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

JUICE_SHOP_URL = "http://localhost:3000"
MANIFEST_FILE = BENCHMARK_APPS_DIR / "juice_shop_bugs.json"


def preflight_check(url: str, timeout: int = 5) -> bool:
    """Return True if the URL is reachable within timeout seconds."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status < 500
    except Exception:
        return False


def load_bug_manifest(manifest_path: Path) -> dict:
    """Load the ground-truth bug manifest."""
    with open(manifest_path) as f:
        return json.load(f)


def match_anomaly_to_bug(anomaly_text: str, bugs: list[dict]) -> str | None:
    """Try to match an anomaly description to a known bug using keywords."""
    anomaly_lower = anomaly_text.lower()
    best_match = None
    best_score = 0

    for bug in bugs:
        keywords = bug["detection_keywords"]
        score = sum(1 for kw in keywords if kw.lower() in anomaly_lower)
        # Boost on bug name or element selector
        if bug["name"].lower() in anomaly_lower:
            score += 3
        if bug["element_selector"].lstrip(".#") in anomaly_lower:
            score += 2

        if score > best_score and score >= 2:  # Require at least 2 keyword matches
            best_score = score
            best_match = bug["bug_id"]

    return best_match


def score_results(test_result: dict, manifest: dict) -> dict:
    """Score the test results against the ground truth manifest."""
    bugs = manifest["bugs"]
    total_planted = manifest["total_planted_bugs"]
    bug_ids = {b["bug_id"] for b in bugs}

    # Extract anomalies from all phases
    anomalies = []
    phases = test_result.get("phases", {})

    # Primary source: detect phase anomalies
    detect_phase = phases.get("detect", {})
    if isinstance(detect_phase, dict):
        detect_anomalies = detect_phase.get("anomalies", [])
        if isinstance(detect_anomalies, list):
            for a in detect_anomalies:
                if isinstance(a, dict):
                    anomalies.append(a.get("description", "") or str(a))
                elif isinstance(a, str):
                    anomalies.append(a)

    # Test phase failures
    test_phase = phases.get("test", {})
    if isinstance(test_phase, dict):
        results = test_phase.get("test_results", [])
        for r in results:
            if isinstance(r, dict) and r.get("success") is False:
                errors = r.get("errors_on_page", [])
                for err in errors:
                    if isinstance(err, str) and err not in anomalies:
                        anomalies.append(err)

    # Discover phase console errors
    discover_phase = phases.get("discover", {})
    if isinstance(discover_phase, dict):
        console_errors = discover_phase.get("console_errors", [])
        for err in console_errors:
            if isinstance(err, str) and err not in anomalies:
                anomalies.append(err)

    # Deduplicate
    seen: set[str] = set()
    unique_anomalies: list[str] = []
    for a in anomalies:
        key = a[:100]
        if key not in seen:
            seen.add(key)
            unique_anomalies.append(a)
    anomalies = unique_anomalies

    # Match anomalies to planted bugs
    matched_bugs: set[str] = set()
    unmatched_anomalies: list[str] = []
    match_details: list[dict] = []

    for anomaly_text in anomalies:
        bug_id = match_anomaly_to_bug(anomaly_text, bugs)
        if bug_id:
            matched_bugs.add(bug_id)
            match_details.append({
                "anomaly": anomaly_text[:200],
                "matched_bug": bug_id,
            })
        else:
            unmatched_anomalies.append(anomaly_text[:200])

    # Compute precision / recall / F1
    true_positives = len(matched_bugs)
    false_positives = len(unmatched_anomalies)
    false_negatives = total_planted - true_positives

    precision = true_positives / max(true_positives + false_positives, 1)
    recall = true_positives / max(total_planted, 1)
    f1 = 2 * precision * recall / max(precision + recall, 0.001)

    return {
        "total_planted_bugs": total_planted,
        "total_anomalies_reported": len(anomalies),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "matched_bugs": sorted(matched_bugs),
        "missed_bugs": sorted(bug_ids - matched_bugs),
        "match_details": match_details,
        "unmatched_anomalies": unmatched_anomalies[:10],
    }


async def run_benchmark(max_interactions: int = 40) -> dict:
    """Run the full Juice Shop benchmark."""
    if not MANIFEST_FILE.exists():
        raise FileNotFoundError(f"Bug manifest not found: {MANIFEST_FILE}")

    manifest = load_bug_manifest(MANIFEST_FILE)
    logger.info(
        f"Loaded manifest: {manifest['app_name']} "
        f"with {manifest['total_planted_bugs']} bugs"
    )

    # Preflight: ensure Juice Shop is reachable
    logger.info(f"Preflight check: {JUICE_SHOP_URL}")
    if not preflight_check(JUICE_SHOP_URL):
        print(
            f"\nERROR: Juice Shop is not reachable at {JUICE_SHOP_URL}\n"
            "\nTo start it, run ONE of the following:\n"
            "  bash backend/scripts/setup_juice_shop.sh\n"
            "  docker run -d -p 3000:3000 bkimminich/juice-shop\n"
            "  docker compose -f backend/data/benchmark_apps/juice_shop_docker_compose.yml up -d\n"
        )
        sys.exit(1)

    logger.info(f"Juice Shop is reachable. Starting benchmark against {JUICE_SHOP_URL}")

    # Run the self-test agent
    logger.info(f"Running self-test (max_interactions={max_interactions})")
    start_time = time.time()

    test_result = await pw_batch_test(JUICE_SHOP_URL, max_interactions=max_interactions)

    duration_s = round(time.time() - start_time, 1)
    logger.info(f"Self-test completed in {duration_s}s")

    # Score against ground truth
    scores = score_results(test_result, manifest)
    scores["duration_s"] = duration_s
    scores["app_name"] = manifest["app_name"]
    scores["url"] = JUICE_SHOP_URL

    logger.info(f"\n{'='*60}")
    logger.info("JUICE SHOP BENCHMARK RESULTS")
    logger.info(f"{'='*60}")
    logger.info(f"App: {manifest['app_name']}")
    logger.info(f"URL: {JUICE_SHOP_URL}")
    logger.info(f"Planted bugs: {manifest['total_planted_bugs']}")
    logger.info(f"Anomalies reported: {scores['total_anomalies_reported']}")
    logger.info(f"True Positives: {scores['true_positives']}")
    logger.info(f"False Positives: {scores['false_positives']}")
    logger.info(f"False Negatives: {scores['false_negatives']}")
    logger.info(f"Precision: {scores['precision']}")
    logger.info(f"Recall: {scores['recall']}")
    logger.info(f"F1: {scores['f1']}")
    logger.info(f"Duration: {duration_s}s")
    logger.info(f"Matched: {scores['matched_bugs']}")
    logger.info(f"Missed: {scores['missed_bugs']}")

    # Save full report
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "benchmark_type": "juice_shop",
        "scores": scores,
        "raw_result": {
            "pages_found": test_result.get("phases", {}).get("discover", {}).get("pages_found", 0),
            "interactions_tested": test_result.get("phases", {}).get("discover", {}).get("total_interactions", 0),
        },
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"juice_shop_benchmark_{ts}.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    # Update latest.json
    latest_path = REPORTS_DIR / "latest.json"
    if latest_path.exists():
        with open(latest_path) as f:
            latest = json.load(f)
    else:
        latest = {}

    latest["juice_shop_benchmark"] = {
        "app_name": manifest["app_name"],
        "precision": scores["precision"],
        "recall": scores["recall"],
        "f1": scores["f1"],
        "true_positives": scores["true_positives"],
        "false_positives": scores["false_positives"],
        "false_negatives": scores["false_negatives"],
        "total_planted": scores["total_planted_bugs"],
        "total_found": scores["total_anomalies_reported"],
        "duration_s": duration_s,
        "matched_bugs": scores["matched_bugs"],
        "missed_bugs": scores["missed_bugs"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(latest_path, "w") as f:
        json.dump(latest, f, indent=2, default=str)

    logger.info(f"Report saved: {report_path}")

    print(f"\n{'='*50}")
    print(f"F1={scores['f1']} | P={scores['precision']} | R={scores['recall']}")
    print(f"Found {scores['true_positives']}/{scores['total_planted_bugs']} Juice Shop bugs")
    print(f"{'='*50}")

    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the OWASP Juice Shop benchmark.")
    parser.add_argument(
        "--max-interactions",
        type=int,
        default=40,
        help="Maximum agent interactions per test run (default: 40)",
    )
    args = parser.parse_args()

    asyncio.run(run_benchmark(max_interactions=args.max_interactions))
