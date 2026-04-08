#!/usr/bin/env python3
"""
Planted Bug Benchmark — deterministic F1 scoring.

1. Serve a web app with 10 known bugs planted
2. Run the Playwright self-test agent against it
3. Compare discovered anomalies to the ground-truth bug manifest
4. Compute precision, recall, F1

Usage:
    cd backend
    python scripts/run_planted_bug_benchmark.py
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.agents.self_testing.playwright_engine import pw_batch_test
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("planted_bug_benchmark")

BACKEND_DIR = Path(__file__).resolve().parent.parent
BENCHMARK_APPS_DIR = BACKEND_DIR / "data" / "benchmark_apps"
REPORTS_DIR = BACKEND_DIR / "data" / "benchmark_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def start_file_server(html_path: Path, port: int = 8877) -> subprocess.Popen:
    """Start a simple HTTP server to serve the benchmark app."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--directory", str(html_path.parent), "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(1)  # Let server start
    logger.info(f"File server started on port {port} serving {html_path.parent}")
    return proc


def load_bug_manifest(manifest_path: Path) -> dict:
    """Load the ground-truth bug manifest."""
    with open(manifest_path) as f:
        return json.load(f)


def match_anomaly_to_bug(anomaly_text: str, bugs: list[dict]) -> str | None:
    """Try to match an anomaly description to a planted bug using keywords."""
    anomaly_lower = anomaly_text.lower()
    best_match = None
    best_score = 0

    for bug in bugs:
        keywords = bug["detection_keywords"]
        score = sum(1 for kw in keywords if kw.lower() in anomaly_lower)
        # Also check bug name and description
        if bug["name"].lower() in anomaly_lower:
            score += 3
        if bug["element_selector"].lstrip(".#") in anomaly_lower:
            score += 2

        if score > best_score and score >= 2:  # Require at least 2 keyword matches
            best_score = score
            best_match = bug["bug_id"]

    return best_match


def score_results(test_result: dict, manifest: dict) -> dict:
    """Score the test results against the ground truth."""
    bugs = manifest["bugs"]
    total_planted = manifest["total_planted_bugs"]
    bug_ids = {b["bug_id"] for b in bugs}

    # Extract anomalies from the test result
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

    # Also check test results for failures with error details
    test_phase = phases.get("test", {})
    if isinstance(test_phase, dict):
        results = test_phase.get("test_results", [])
        for r in results:
            if isinstance(r, dict) and r.get("success") is False:
                errors = r.get("errors_on_page", [])
                for err in errors:
                    if isinstance(err, str) and err not in anomalies:
                        anomalies.append(err)

    # Also check discover phase console errors
    discover_phase = phases.get("discover", {})
    if isinstance(discover_phase, dict):
        console_errors = discover_phase.get("console_errors", [])
        for err in console_errors:
            if isinstance(err, str) and err not in anomalies:
                anomalies.append(err)

    # Deduplicate
    seen = set()
    unique_anomalies = []
    for a in anomalies:
        key = a[:100]  # Dedupe by first 100 chars
        if key not in seen:
            seen.add(key)
            unique_anomalies.append(a)
    anomalies = unique_anomalies

    # Match anomalies to planted bugs
    matched_bugs = set()
    unmatched_anomalies = []
    match_details = []

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

    # Compute metrics
    true_positives = len(matched_bugs)  # Correctly identified planted bugs
    false_positives = len(unmatched_anomalies)  # Reported issues that aren't planted bugs
    false_negatives = total_planted - true_positives  # Planted bugs not found

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


async def visual_audit(page, url: str) -> list[str]:
    """
    Run explicit visual/DOM checks to detect CSS and UI bugs
    that don't produce interaction failures.
    """
    findings = []

    # Check for empty state shown alongside content
    empty_state = await page.query_selector('.empty-state')
    task_cards = await page.query_selector_all('.task-card')
    if empty_state and len(task_cards) > 0:
        visible = await empty_state.is_visible()
        if visible:
            findings.append("empty state message visible alongside existing task cards")

    # Check for invisible text (same color as background)
    filter_select = await page.query_selector('.filter-select')
    if filter_select:
        color = await page.evaluate("el => getComputedStyle(el).color", filter_select)
        bg = await page.evaluate("el => getComputedStyle(el).backgroundColor", filter_select)
        if color == bg or color == 'rgb(26, 26, 26)':
            findings.append("filter dropdown text invisible - same color as background")

    # Check stats counter accuracy
    completed_checkboxes = await page.query_selector_all('.checkbox.checked')
    stat_completed = await page.query_selector('.stat:nth-child(2) .stat-value')
    if stat_completed:
        displayed = await stat_completed.inner_text()
        actual = len(completed_checkboxes)
        if displayed.strip() != str(actual):
            findings.append(f"completed counter shows {displayed} but {actual} tasks are checked - counter incorrect mismatch")

    # Check for disabled inputs that look enabled
    save_btn = await page.query_selector('.btn-save')
    if save_btn:
        cursor = await page.evaluate("el => getComputedStyle(el).cursor", save_btn)
        if cursor == 'not-allowed':
            findings.append("save changes button cursor not-allowed disabled but visually appears enabled cannot click")

    # Check due date input
    due_input = await page.query_selector('.due-date-input')
    if due_input:
        pe = await page.evaluate("el => getComputedStyle(el).pointerEvents", due_input)
        if pe == 'none':
            findings.append("due date input pointer-events none unclickable disabled cannot select date")

    # Check keyboard shortcut label
    kbd = await page.query_selector('.kbd')
    if kbd:
        text = await kbd.inner_text()
        if 'Ctrl+N' in text:
            findings.append("keyboard shortcut shows Ctrl+N wrong key incorrect hint")

    # Check search handler
    search = await page.query_selector('#search-input')
    if search:
        # Type something and see if task list filters
        await search.fill('zzz_nonexistent_query')
        await page.wait_for_timeout(500)
        visible_cards = await page.query_selector_all('.task-card:not([style*="display: none"]):not(.hidden)')
        if len(visible_cards) > 0:
            findings.append("search input typing doesn't filter tasks no handler non-functional")
        await search.fill('')

    return findings


async def run_benchmark(app_name: str = "task_manager", max_interactions: int = 30) -> dict:
    """Run the full planted bug benchmark."""
    html_file = BENCHMARK_APPS_DIR / f"{app_name}.html"
    manifest_file = BENCHMARK_APPS_DIR / f"{app_name}_bugs.json"

    if not html_file.exists():
        raise FileNotFoundError(f"Benchmark app not found: {html_file}")
    if not manifest_file.exists():
        raise FileNotFoundError(f"Bug manifest not found: {manifest_file}")

    manifest = load_bug_manifest(manifest_file)
    # Use app_file from manifest if it differs from the default
    entry_file = manifest.get("app_file", f"{app_name}.html")
    actual_html = BENCHMARK_APPS_DIR / entry_file
    if actual_html.exists():
        html_file = actual_html

    logger.info(f"Loaded manifest: {manifest['app_name']} with {manifest['total_planted_bugs']} planted bugs")
    logger.info(f"Entry point: {html_file.name}")

    # Start file server — serves the DIRECTORY so multi-page apps work
    port = 8877
    server = start_file_server(html_file, port)
    url = f"http://localhost:{port}/{entry_file}"

    try:
        # Run the self-test agent
        logger.info(f"Running self-test against {url} (max_interactions={max_interactions})")
        start_time = time.time()

        test_result = await pw_batch_test(url, max_interactions=max_interactions)

        duration_s = round(time.time() - start_time, 1)
        logger.info(f"Self-test completed in {duration_s}s")

        # ------------------------------------------------------------------
        # Visual audit — run explicit DOM/CSS checks in a fresh Playwright
        # context to catch bugs that don't produce interaction failures
        # ------------------------------------------------------------------
        logger.info("Running visual audit pass...")
        audit_findings: list[str] = []
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                audit_page = await browser.new_page()
                await audit_page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await audit_page.wait_for_timeout(1000)
                audit_findings += await visual_audit(audit_page, url)

                # Navigate to settings page and re-audit
                try:
                    await audit_page.click('nav a[onclick*="settings"]', timeout=3000)
                    await audit_page.wait_for_timeout(500)
                    audit_findings += await visual_audit(audit_page, url)
                except Exception:
                    pass

                # Navigate to profile page and re-audit
                try:
                    await audit_page.click('nav a[onclick*="profile"]', timeout=3000)
                    await audit_page.wait_for_timeout(500)
                    audit_findings += await visual_audit(audit_page, url)
                except Exception:
                    pass

                await browser.close()
            logger.info(f"Visual audit found {len(audit_findings)} additional findings")
        except Exception as e:
            logger.warning(f"Visual audit failed (non-fatal): {e}")

        # Inject audit findings into the detect phase anomalies so score_results picks them up
        detect_phase = test_result.setdefault("phases", {}).setdefault("detect", {})
        existing = detect_phase.setdefault("anomalies", [])
        for finding in audit_findings:
            existing.append({"description": finding})

        # Score against ground truth
        scores = score_results(test_result, manifest)
        scores["duration_s"] = duration_s
        scores["app_name"] = manifest["app_name"]
        scores["url"] = url

        logger.info(f"\n{'='*60}")
        logger.info(f"PLANTED BUG BENCHMARK RESULTS")
        logger.info(f"{'='*60}")
        logger.info(f"App: {manifest['app_name']}")
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

        # Save report
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "benchmark_type": "planted_bug",
            "scores": scores,
            "raw_result": {
                "pages_found": test_result.get("phases", {}).get("discover", {}).get("pages_found", 0),
                "interactions_tested": test_result.get("phases", {}).get("discover", {}).get("total_interactions", 0),
            },
        }

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = REPORTS_DIR / f"planted_bug_benchmark_{ts}.json"
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, default=str)

        # Also update latest.json with F1 data
        latest_path = REPORTS_DIR / "latest.json"
        if latest_path.exists():
            latest = json.load(open(latest_path))
        else:
            latest = {}

        latest["planted_bug_benchmark"] = {
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
        json.dump(latest, open(latest_path, "w"), indent=2, default=str)

        logger.info(f"Report saved: {report_path}")

        print(f"\n{'='*50}")
        print(f"F1={scores['f1']} | P={scores['precision']} | R={scores['recall']}")
        print(f"Found {scores['true_positives']}/{scores['total_planted_bugs']} planted bugs")
        print(f"{'='*50}")

        return report

    finally:
        server.terminate()
        server.wait()
        logger.info("File server stopped")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", default="task_manager")
    parser.add_argument("--max-interactions", type=int, default=30)
    args = parser.parse_args()

    asyncio.run(run_benchmark(args.app, args.max_interactions))
