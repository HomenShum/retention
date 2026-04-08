#!/usr/bin/env python3
"""
Comprehensive QA Benchmark — 5-phase coverage.

Closes all measurement gaps in the retention.sh benchmark suite:

  Phase 1 — Planted Bug Recall      F1, precision, recall on known buggy apps
  Phase 2 — False Discovery Rate    FP rate on clean (no-bug) builds
  Phase 3 — Fix Verification        After fix, does PASS verdict hold? (loop integrity)
  Phase 4 — Branch Classification   A (Bug Found) / B (No Bug) routing accuracy
  Phase 5 — Economics               Cost per confirmed bug, time-to-verdict p50/p95

Usage:
    cd backend
    python scripts/run_comprehensive_benchmark.py [--app task_manager] [--skip-phases 2,3]
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.agents.self_testing.playwright_engine import pw_batch_test

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("comprehensive_benchmark")

BACKEND_DIR = Path(__file__).resolve().parent.parent
BENCHMARK_APPS_DIR = BACKEND_DIR / "data" / "benchmark_apps"
REPORTS_DIR = BACKEND_DIR / "data" / "benchmark_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────────

def start_file_server(directory: Path, port: int = 8878) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port),
         "--directory", str(directory), "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1)
    logger.info(f"File server started on port {port} → {directory}")
    return proc


def load_manifest(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def extract_anomalies(test_result: dict) -> list[str]:
    """Pull all anomaly/error strings out of a pw_batch_test result."""
    anomalies: list[str] = []
    phases = test_result.get("phases", {})

    detect = phases.get("detect", {})
    if isinstance(detect, dict):
        for a in detect.get("anomalies", []):
            text = a.get("description", "") if isinstance(a, dict) else str(a)
            if text:
                anomalies.append(text)

    test_ph = phases.get("test", {})
    if isinstance(test_ph, dict):
        for r in test_ph.get("test_results", []):
            if isinstance(r, dict) and r.get("success") is False:
                for err in r.get("errors_on_page", []):
                    if isinstance(err, str):
                        anomalies.append(err)

    discover = phases.get("discover", {})
    if isinstance(discover, dict):
        for err in discover.get("console_errors", []):
            if isinstance(err, str):
                anomalies.append(err)

    # Deduplicate by first 100 chars
    seen, unique = set(), []
    for a in anomalies:
        key = a[:100]
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return unique


def match_anomaly_to_bug(anomaly_text: str, bugs: list[dict]) -> Optional[str]:
    anomaly_lower = anomaly_text.lower()
    best_match, best_score = None, 0
    for bug in bugs:
        score = sum(1 for kw in bug["detection_keywords"] if kw.lower() in anomaly_lower)
        if bug["name"].lower() in anomaly_lower:
            score += 3
        if bug["element_selector"].lstrip(".#") in anomaly_lower:
            score += 2
        if score > best_score and score >= 2:
            best_score = score
            best_match = bug["bug_id"]
    return best_match


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1 — Planted Bug Recall
# ──────────────────────────────────────────────────────────────────────────────

async def phase1_planted_bug_recall(
    app_name: str,
    manifest: dict,
    port: int = 8878,
    max_interactions: int = 30,
) -> dict:
    """
    Run retention.sh against a buggy app and score precision/recall/F1.
    Returns: {precision, recall, f1, tp, fp, fn, matched_bugs, missed_bugs,
              anomalies_reported, duration_s, cost_usd_estimate}
    """
    logger.info("── Phase 1: Planted Bug Recall ──")
    bugs = manifest["bugs"]
    total_planted = manifest["total_planted_bugs"]
    entry_file = manifest.get("app_file", f"{app_name}.html")
    url = f"http://localhost:{port}/{entry_file}"

    t0 = time.time()
    result = await pw_batch_test(url, max_interactions=max_interactions)
    duration_s = round(time.time() - t0, 1)

    anomalies = extract_anomalies(result)
    matched_bugs, unmatched, match_details = set(), [], []
    for text in anomalies:
        bid = match_anomaly_to_bug(text, bugs)
        if bid:
            matched_bugs.add(bid)
            match_details.append({"anomaly": text[:200], "matched_bug": bid})
        else:
            unmatched.append(text[:200])

    bug_ids = {b["bug_id"] for b in bugs}
    tp = len(matched_bugs)
    fp = len(unmatched)
    fn = total_planted - tp

    prec = tp / max(tp + fp, 1)
    rec  = tp / max(total_planted, 1)
    f1   = 2 * prec * rec / max(prec + rec, 0.001)

    # Rough cost: ~$0.003 per bug verification run
    cost_usd_estimate = round(0.003 * max_interactions / 10, 4)

    return {
        "precision":          round(prec, 3),
        "recall":             round(rec, 3),
        "f1":                 round(f1, 3),
        "true_positives":     tp,
        "false_positives":    fp,
        "false_negatives":    fn,
        "total_planted":      total_planted,
        "anomalies_reported": len(anomalies),
        "matched_bugs":       sorted(matched_bugs),
        "missed_bugs":        sorted(bug_ids - matched_bugs),
        "match_details":      match_details,
        "unmatched_anomalies": unmatched[:10],
        "duration_s":         duration_s,
        "cost_usd_estimate":  cost_usd_estimate,
        "raw_result":         result,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — False Discovery Rate
# ──────────────────────────────────────────────────────────────────────────────

async def phase2_false_discovery_rate(
    clean_app_name: str,
    port: int = 8878,
    max_interactions: int = 30,
) -> dict:
    """
    Run retention.sh against a clean (no-bugs) app.
    Any anomaly flagged = false positive.
    Returns: {fdr, specificity, false_positives, true_negatives, duration_s}
    """
    logger.info("── Phase 2: False Discovery Rate ──")
    clean_manifest_path = BENCHMARK_APPS_DIR / f"{clean_app_name}_manifest.json"
    clean_manifest = load_manifest(clean_manifest_path) if clean_manifest_path.exists() else {}
    entry_file = clean_manifest.get("app_file", f"{clean_app_name}.html")
    url = f"http://localhost:{port}/{entry_file}"

    t0 = time.time()
    result = await pw_batch_test(url, max_interactions=max_interactions)
    duration_s = round(time.time() - t0, 1)

    anomalies = extract_anomalies(result)
    fp = len(anomalies)
    # TN = interactions that correctly produced no alert
    # Approximate: each interaction that did NOT yield a false anomaly
    tn = max(max_interactions - fp, 0)

    fdr         = fp / max(fp + tn, 1)   # false discovery rate
    specificity = tn / max(tn + fp, 1)   # true negative rate

    return {
        "false_positives":       fp,
        "true_negatives":        tn,
        "fdr":                   round(fdr, 3),
        "specificity":           round(specificity, 3),
        "anomalies_flagged":     anomalies[:10],
        "duration_s":            duration_s,
        "clean_app":             entry_file,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3 — Fix Verification Accuracy
# ──────────────────────────────────────────────────────────────────────────────

async def phase3_fix_verification(
    clean_app_name: str,
    bugs_caught_in_phase1: list[str],
    phase1_bugs: list[dict],
    port: int = 8878,
    max_interactions: int = 30,
) -> dict:
    """
    For each bug correctly caught in Phase 1, verify that the fixed (clean)
    build does NOT re-flag it.  Fix Verification Accuracy = correctly_cleared / tp_count.

    Returns: {fix_verification_accuracy, correctly_cleared, incorrectly_reflagged,
              total_verified, per_bug_result}
    """
    logger.info("── Phase 3: Fix Verification Accuracy ──")
    if not bugs_caught_in_phase1:
        logger.info("  No TPs from Phase 1 — skipping fix verification")
        return {
            "fix_verification_accuracy": None,
            "correctly_cleared": 0,
            "incorrectly_reflagged": 0,
            "total_verified": 0,
            "per_bug_result": [],
            "note": "No TPs in Phase 1 to verify",
        }

    clean_manifest_path = BENCHMARK_APPS_DIR / f"{clean_app_name}_manifest.json"
    clean_manifest = load_manifest(clean_manifest_path) if clean_manifest_path.exists() else {}
    entry_file = clean_manifest.get("app_file", f"{clean_app_name}.html")
    url = f"http://localhost:{port}/{entry_file}"

    t0 = time.time()
    result = await pw_batch_test(url, max_interactions=max_interactions)
    duration_s = round(time.time() - t0, 1)

    anomalies = extract_anomalies(result)

    # For each bug that was caught in Phase 1, check if it re-appears in clean run
    per_bug = []
    incorrectly_reflagged = 0
    for bug_id in bugs_caught_in_phase1:
        bug = next((b for b in phase1_bugs if b["bug_id"] == bug_id), None)
        if not bug:
            continue
        # Check if any anomaly still matches this bug
        still_present = any(
            match_anomaly_to_bug(a, [bug]) == bug_id for a in anomalies
        )
        per_bug.append({
            "bug_id": bug_id,
            "bug_name": bug["name"],
            "still_flagged_after_fix": still_present,
            "verdict": "FAIL — not cleared" if still_present else "PASS — correctly cleared",
        })
        if still_present:
            incorrectly_reflagged += 1

    correctly_cleared = len(bugs_caught_in_phase1) - incorrectly_reflagged
    accuracy = correctly_cleared / max(len(bugs_caught_in_phase1), 1)

    return {
        "fix_verification_accuracy": round(accuracy, 3),
        "correctly_cleared":         correctly_cleared,
        "incorrectly_reflagged":     incorrectly_reflagged,
        "total_verified":            len(bugs_caught_in_phase1),
        "per_bug_result":            per_bug,
        "duration_s":                duration_s,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Phase 4 — Branch Classification Accuracy
# ──────────────────────────────────────────────────────────────────────────────

def phase4_branch_classification(
    phase1: dict,
    phase2: dict,
) -> dict:
    """
    Derive branch routing accuracy from Phase 1 + 2 results.

    Branch A (Bug Found):  buggy app run — did retention.sh flag at least 1 real bug?
    Branch B (No Bug):     clean app run — did retention.sh correctly return 0 real bugs?

    Branch C (New Bug Emerges) requires a multi-session run (tested in golden-bug
    pipeline, not repeated here — noted as manual verification required).
    """
    logger.info("── Phase 4: Branch Classification Accuracy ──")

    # Branch A: correctly classified as "bug found" if TP > 0
    branch_a_tp = phase1["true_positives"]
    branch_a_correct = branch_a_tp > 0

    # Branch B: correctly classified as "no bug" if clean run had 0 anomalies
    branch_b_fp = phase2["false_positives"]
    branch_b_correct = branch_b_fp == 0

    correct_count = int(branch_a_correct) + int(branch_b_correct)
    total_tested = 2  # A and B tested automatically; C is manual

    return {
        "branch_a_bug_found": {
            "correct": branch_a_correct,
            "tp_count": branch_a_tp,
            "verdict": "PASS — correctly routed to Bug Found branch" if branch_a_correct
                       else "FAIL — missed all bugs, routed to No Bug branch",
        },
        "branch_b_no_bug": {
            "correct": branch_b_correct,
            "fp_count": branch_b_fp,
            "verdict": "PASS — correctly returned clean verdict" if branch_b_correct
                       else f"FAIL — {branch_b_fp} false positive(s) on clean build",
        },
        "branch_c_new_bug": {
            "correct": None,
            "verdict": "MANUAL — requires multi-session cascading bug run (golden-bug pipeline)",
        },
        "classification_accuracy": round(correct_count / total_tested, 3),
        "correct_of_tested": f"{correct_count}/{total_tested} automated branches",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Phase 5 — Economics
# ──────────────────────────────────────────────────────────────────────────────

def phase5_economics(
    phase1: dict,
    phase2: dict,
    phase3: dict,
) -> dict:
    """
    Compute business-relevant cost and speed metrics.
    """
    logger.info("── Phase 5: Economics ──")

    tp = phase1["true_positives"]
    cost_per_run = phase1.get("cost_usd_estimate", 0.003)

    # Total cost across all phases (3 runs: buggy + clean + fix-verify)
    total_cost = cost_per_run * 3

    cost_per_confirmed_bug = total_cost / max(tp, 1)

    # Time-to-verdict across all runs
    durations = [
        phase1.get("duration_s", 0),
        phase2.get("duration_s", 0),
        phase3.get("duration_s", 0),
    ]
    durations_nonzero = [d for d in durations if d > 0]
    avg_ttv = round(sum(durations_nonzero) / max(len(durations_nonzero), 1), 1)

    # Manual QA equivalence
    manual_min_per_bug = 30
    manual_hourly_rate = 50.0
    manual_cost_per_bug = (manual_min_per_bug / 60) * manual_hourly_rate

    savings_ratio = manual_cost_per_bug / max(cost_per_confirmed_bug, 0.001)

    return {
        "cost_per_buggy_run_usd":     round(cost_per_run, 4),
        "cost_per_confirmed_bug_usd": round(cost_per_confirmed_bug, 4),
        "cost_per_fix_verification":  round(cost_per_run, 4),
        "total_benchmark_cost_usd":   round(total_cost, 4),
        "avg_time_to_verdict_s":      avg_ttv,
        "avg_time_to_verdict_min":    round(avg_ttv / 60, 2),
        "manual_qa_cost_per_bug_usd": round(manual_cost_per_bug, 2),
        "cost_savings_ratio":         round(savings_ratio, 1),
        "savings_pct":                round((1 - 1 / max(savings_ratio, 1)) * 100, 1),
        "note": f"Manual QA: {manual_min_per_bug}min/bug @ ${manual_hourly_rate}/hr",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Main Orchestrator
# ──────────────────────────────────────────────────────────────────────────────

async def run_comprehensive_benchmark(
    app_name: str = "task_manager",
    clean_app_name: str = "task_manager_clean",
    max_interactions: int = 30,
    skip_phases: list[int] | None = None,
) -> dict:
    skip_phases = skip_phases or []
    port = 8878

    # Load manifests
    buggy_manifest_path = BENCHMARK_APPS_DIR / f"{app_name}_bugs.json"
    if not buggy_manifest_path.exists():
        raise FileNotFoundError(f"Bug manifest not found: {buggy_manifest_path}")
    buggy_manifest = load_manifest(buggy_manifest_path)

    clean_html = BENCHMARK_APPS_DIR / f"{clean_app_name}.html"
    if not clean_html.exists():
        raise FileNotFoundError(f"Clean app not found: {clean_html}")

    logger.info(f"{'='*60}")
    logger.info(f"COMPREHENSIVE BENCHMARK: {buggy_manifest['app_name']}")
    logger.info(f"Buggy app : {app_name}.html  ({buggy_manifest['total_planted_bugs']} bugs)")
    logger.info(f"Clean app : {clean_app_name}.html")
    logger.info(f"Max interactions: {max_interactions}")
    logger.info(f"{'='*60}")

    server = start_file_server(BENCHMARK_APPS_DIR, port)

    try:
        results: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "app_name": buggy_manifest["app_name"],
            "buggy_app": f"{app_name}.html",
            "clean_app": f"{clean_app_name}.html",
            "max_interactions": max_interactions,
        }

        # ── Phase 1 ────────────────────────────────────────────────────────
        if 1 not in skip_phases:
            p1 = await phase1_planted_bug_recall(
                app_name, buggy_manifest, port, max_interactions
            )
            results["phase1_planted_bug_recall"] = {k: v for k, v in p1.items() if k != "raw_result"}
            logger.info(
                f"Phase 1 done — F1={p1['f1']} P={p1['precision']} R={p1['recall']} "
                f"TP={p1['true_positives']}/{p1['total_planted']}"
            )
        else:
            p1 = {"true_positives": 0, "matched_bugs": [], "duration_s": 0,
                  "cost_usd_estimate": 0.003, "precision": 0, "recall": 0, "f1": 0,
                  "total_planted": buggy_manifest["total_planted_bugs"]}
            results["phase1_planted_bug_recall"] = {"skipped": True}

        # ── Phase 2 ────────────────────────────────────────────────────────
        if 2 not in skip_phases:
            p2 = await phase2_false_discovery_rate(clean_app_name, port, max_interactions)
            results["phase2_false_discovery_rate"] = p2
            logger.info(
                f"Phase 2 done — FDR={p2['fdr']} Specificity={p2['specificity']} "
                f"FP={p2['false_positives']}"
            )
        else:
            p2 = {"false_positives": 0, "true_negatives": max_interactions,
                  "fdr": 0, "specificity": 1.0, "duration_s": 0}
            results["phase2_false_discovery_rate"] = {"skipped": True}

        # ── Phase 3 ────────────────────────────────────────────────────────
        if 3 not in skip_phases:
            p3 = await phase3_fix_verification(
                clean_app_name,
                p1.get("matched_bugs", []),
                buggy_manifest["bugs"],
                port,
                max_interactions,
            )
            results["phase3_fix_verification"] = p3
            acc = p3.get("fix_verification_accuracy")
            logger.info(
                f"Phase 3 done — Fix Verification Accuracy="
                f"{acc if acc is not None else 'N/A'} "
                f"({p3['correctly_cleared']}/{p3['total_verified']})"
            )
        else:
            p3 = {"fix_verification_accuracy": None, "correctly_cleared": 0,
                  "incorrectly_reflagged": 0, "total_verified": 0,
                  "per_bug_result": [], "duration_s": 0}
            results["phase3_fix_verification"] = {"skipped": True}

        # ── Phase 4 ────────────────────────────────────────────────────────
        if 4 not in skip_phases:
            p4 = phase4_branch_classification(p1, p2)
            results["phase4_branch_classification"] = p4
            logger.info(
                f"Phase 4 done — Classification Accuracy={p4['classification_accuracy']} "
                f"({p4['correct_of_tested']})"
            )
        else:
            results["phase4_branch_classification"] = {"skipped": True}

        # ── Phase 5 ────────────────────────────────────────────────────────
        if 5 not in skip_phases:
            p5 = phase5_economics(p1, p2, p3)
            results["phase5_economics"] = p5
            logger.info(
                f"Phase 5 done — Cost/confirmed bug=${p5['cost_per_confirmed_bug_usd']} "
                f"Savings={p5['savings_pct']}% vs manual"
            )
        else:
            results["phase5_economics"] = {"skipped": True}

        # ── Summary ────────────────────────────────────────────────────────
        summary = build_summary(results)
        results["summary"] = summary
        print_summary(summary)

        # Persist
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = REPORTS_DIR / f"comprehensive_benchmark_{ts}.json"
        with open(report_path, "w") as f:
            json.dump(results, f, indent=2, default=str)

        # Update latest.json
        latest_path = REPORTS_DIR / "latest.json"
        latest = json.load(open(latest_path)) if latest_path.exists() else {}
        latest["comprehensive_benchmark"] = summary
        latest["comprehensive_benchmark"]["timestamp"] = results["timestamp"]
        json.dump(latest, open(latest_path, "w"), indent=2, default=str)

        logger.info(f"Report saved → {report_path}")
        return results

    finally:
        server.terminate()
        server.wait()
        logger.info("File server stopped")


def build_summary(results: dict) -> dict:
    p1 = results.get("phase1_planted_bug_recall", {})
    p2 = results.get("phase2_false_discovery_rate", {})
    p3 = results.get("phase3_fix_verification", {})
    p4 = results.get("phase4_branch_classification", {})
    p5 = results.get("phase5_economics", {})

    return {
        # Core QA accuracy
        "f1":                        p1.get("f1"),
        "precision":                 p1.get("precision"),
        "recall":                    p1.get("recall"),
        "true_positives":            p1.get("true_positives"),
        "false_positives_on_buggy":  p1.get("false_positives"),
        "false_negatives":           p1.get("false_negatives"),
        "total_planted_bugs":        p1.get("total_planted"),

        # FDR
        "false_discovery_rate":      p2.get("fdr"),
        "specificity":               p2.get("specificity"),
        "fp_on_clean_build":         p2.get("false_positives"),

        # Fix verification
        "fix_verification_accuracy": p3.get("fix_verification_accuracy"),
        "bugs_correctly_cleared":    p3.get("correctly_cleared"),
        "bugs_not_cleared":          p3.get("incorrectly_reflagged"),

        # Branch classification
        "branch_classification_accuracy": p4.get("classification_accuracy"),
        "branch_a_correct":          p4.get("branch_a_bug_found", {}).get("correct"),
        "branch_b_correct":          p4.get("branch_b_no_bug", {}).get("correct"),

        # Economics
        "cost_per_confirmed_bug_usd": p5.get("cost_per_confirmed_bug_usd"),
        "cost_savings_ratio":         p5.get("cost_savings_ratio"),
        "avg_time_to_verdict_min":    p5.get("avg_time_to_verdict_min"),
        "savings_pct":                p5.get("savings_pct"),
    }


def print_summary(s: dict) -> None:
    sep = "=" * 60
    print(f"\n{sep}")
    print("  COMPREHENSIVE BENCHMARK RESULTS")
    print(sep)
    print(f"\n  ── Phase 1: Planted Bug Recall ──")
    print(f"     F1={s['f1']}  Precision={s['precision']}  Recall={s['recall']}")
    print(f"     TP={s['true_positives']} / {s['total_planted_bugs']}  "
          f"FP={s['false_positives_on_buggy']}  FN={s['false_negatives']}")

    print(f"\n  ── Phase 2: False Discovery Rate ──")
    print(f"     FDR={s['false_discovery_rate']}  Specificity={s['specificity']}")
    print(f"     False positives on clean build: {s['fp_on_clean_build']}")

    print(f"\n  ── Phase 3: Fix Verification Accuracy ──")
    fva = s['fix_verification_accuracy']
    print(f"     Accuracy={fva if fva is not None else 'N/A'}  "
          f"Cleared={s['bugs_correctly_cleared']}  "
          f"Not cleared={s['bugs_not_cleared']}")

    print(f"\n  ── Phase 4: Branch Classification ──")
    print(f"     Accuracy={s['branch_classification_accuracy']}  "
          f"Branch A={'✓' if s['branch_a_correct'] else '✗'}  "
          f"Branch B={'✓' if s['branch_b_correct'] else '✗'}  "
          f"Branch C=manual")

    print(f"\n  ── Phase 5: Economics ──")
    print(f"     Cost/confirmed bug: ${s['cost_per_confirmed_bug_usd']}")
    print(f"     Savings vs manual QA: {s['savings_pct']}%  ({s['cost_savings_ratio']}x)")
    print(f"     Avg time-to-verdict: {s['avg_time_to_verdict_min']} min")

    print(f"\n{sep}\n")


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Comprehensive retention.sh QA Benchmark")
    parser.add_argument("--app", default="task_manager",
                        help="Buggy app name (without .html)")
    parser.add_argument("--clean-app", default="task_manager_clean",
                        help="Clean app name (without .html)")
    parser.add_argument("--max-interactions", type=int, default=30)
    parser.add_argument("--skip-phases", default="",
                        help="Comma-separated phase numbers to skip, e.g. '2,3'")
    args = parser.parse_args()

    skip = [int(x) for x in args.skip_phases.split(",") if x.strip().isdigit()]

    asyncio.run(run_comprehensive_benchmark(
        app_name=args.app,
        clean_app_name=args.clean_app,
        max_interactions=args.max_interactions,
        skip_phases=skip,
    ))
