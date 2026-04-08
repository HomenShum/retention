#!/usr/bin/env python3
"""
retention.sh — Master Benchmark Suite Runner

Runs all available web benchmark apps from app_registry.json and produces
a combined scorecard across all apps.

Usage:
    cd backend
    python scripts/run_benchmark_suite.py                          # runs all available web apps
    python scripts/run_benchmark_suite.py --apps task_manager,saucedemo
    python scripts/run_benchmark_suite.py --all                    # all available apps
    python scripts/run_benchmark_suite.py --list                   # list available apps
    python scripts/run_benchmark_suite.py --max-interactions 30
    python scripts/run_benchmark_suite.py --include-docker         # also run docker apps
"""

import argparse
import asyncio
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.agents.self_testing.playwright_engine import pw_batch_test

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("benchmark_suite")

BACKEND_DIR = Path(__file__).resolve().parent.parent
BENCHMARK_APPS_DIR = BACKEND_DIR / "data" / "benchmark_apps"
REPORTS_DIR = BACKEND_DIR / "data" / "benchmark_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

REGISTRY_PATH = BENCHMARK_APPS_DIR / "app_registry.json"

# ──────────────────────────────────────────────────────────────────────────────
# Registry helpers
# ──────────────────────────────────────────────────────────────────────────────

def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        raise FileNotFoundError(f"App registry not found: {REGISTRY_PATH}")
    with open(REGISTRY_PATH) as f:
        return json.load(f)


def filter_apps(
    registry: dict,
    app_ids: list[str] | None = None,
    include_docker: bool = False,
) -> list[dict]:
    """
    Return apps that are:
      - available=true
      - platform=web  (android always skipped; docker skipped unless include_docker)
      - in app_ids if specified
    """
    apps = []
    for app in registry["apps"]:
        if not app.get("available", False):
            continue
        if app.get("platform") != "web":
            continue
        if app.get("type") == "docker" and not include_docker:
            continue
        if app_ids and app["app_id"] not in app_ids:
            continue
        apps.append(app)
    return apps


def list_apps(registry: dict) -> None:
    print(f"\n{'='*70}")
    print("  AVAILABLE BENCHMARK APPS")
    print(f"{'='*70}")
    for app in registry["apps"]:
        avail = "YES" if app.get("available") else "NO "
        plat  = app.get("platform", "web").upper()
        kind  = app.get("type", "unknown")
        bugs  = app.get("total_bugs", 0)
        tasks = app.get("total_tasks", 0)
        count = f"{bugs} bugs" if bugs else f"{tasks} tasks"
        print(
            f"  [{avail}] {app['app_id']:<20} {plat:<8} {kind:<12} "
            f"{count:<10}  {app['name']}"
        )
    print()


# ──────────────────────────────────────────────────────────────────────────────
# Result extraction helpers (mirrors run_comprehensive_benchmark.py)
# ──────────────────────────────────────────────────────────────────────────────

def extract_anomalies(test_result: dict) -> list[str]:
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

    seen, unique = set(), []
    for a in anomalies:
        key = a[:100]
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return unique


def match_anomaly_to_bug(anomaly_text: str, bugs: list[dict]) -> str | None:
    anomaly_lower = anomaly_text.lower()
    best_match, best_score = None, 0
    for bug in bugs:
        score = sum(1 for kw in bug.get("detection_keywords", []) if kw.lower() in anomaly_lower)
        if bug.get("name", "").lower() in anomaly_lower:
            score += 3
        selector = bug.get("element_selector", "").lstrip(".#")
        if selector and selector in anomaly_lower:
            score += 2
        if score > best_score and score >= 2:
            best_score = score
            best_match = bug.get("bug_id")
    return best_match


def load_manifest(manifest_file: str) -> dict | None:
    path = BENCHMARK_APPS_DIR / manifest_file
    if not path.exists():
        logger.warning(f"Manifest not found: {path}")
        return None
    with open(path) as f:
        return json.load(f)


# ──────────────────────────────────────────────────────────────────────────────
# Per-app runners
# ──────────────────────────────────────────────────────────────────────────────

async def run_local_html_app(
    app: dict,
    max_interactions: int,
    port: int = 8879,
) -> dict:
    """
    Run Phase 1 (planted bug recall) + Phase 2 (FDR) for a local_html app.

    Starts a temporary file server, runs pw_batch_test against both the
    buggy and clean builds, then tears down the server.
    """
    app_id = app["app_id"]
    manifest_file = app.get("manifest")
    html_file = app.get("html_file")
    clean_html_file = app.get("clean_html_file")

    if not manifest_file or not html_file:
        return {"error": f"{app_id}: missing manifest or html_file in registry"}

    manifest = load_manifest(manifest_file)
    if manifest is None:
        return {"error": f"{app_id}: manifest file not found ({manifest_file})"}

    clean_html_path = BENCHMARK_APPS_DIR / clean_html_file if clean_html_file else None
    has_clean = clean_html_path and clean_html_path.exists()

    # Start file server
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port),
         "--directory", str(BENCHMARK_APPS_DIR), "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1)
    logger.info(f"[{app_id}] File server on :{port}")

    try:
        bugs = manifest.get("bugs", [])
        total_planted = manifest.get("total_planted_bugs", len(bugs))

        # ── Phase 1: Buggy build ──────────────────────────────────────────
        buggy_url = f"http://localhost:{port}/{html_file}"
        logger.info(f"[{app_id}] Phase 1 — buggy build: {buggy_url}")
        t0 = time.time()
        buggy_result = await pw_batch_test(buggy_url, max_interactions=max_interactions)
        p1_duration = round(time.time() - t0, 1)

        anomalies = extract_anomalies(buggy_result)
        matched_bugs: set[str] = set()
        unmatched: list[str] = []
        for text in anomalies:
            bid = match_anomaly_to_bug(text, bugs)
            if bid:
                matched_bugs.add(bid)
            else:
                unmatched.append(text[:200])

        tp = len(matched_bugs)
        fp = len(unmatched)
        fn = total_planted - tp
        prec = tp / max(tp + fp, 1)
        rec  = tp / max(total_planted, 1)
        f1   = 2 * prec * rec / max(prec + rec, 0.001)

        # ── Phase 2: Clean build (FDR) ────────────────────────────────────
        fdr = None
        p2_duration = 0.0
        if has_clean:
            clean_url = f"http://localhost:{port}/{clean_html_file}"
            logger.info(f"[{app_id}] Phase 2 — clean build: {clean_url}")
            t0 = time.time()
            clean_result = await pw_batch_test(clean_url, max_interactions=max_interactions)
            p2_duration = round(time.time() - t0, 1)
            clean_anomalies = extract_anomalies(clean_result)
            clean_fp = len(clean_anomalies)
            clean_tn = max(max_interactions - clean_fp, 0)
            fdr = round(clean_fp / max(clean_fp + clean_tn, 1), 3)
        else:
            logger.info(f"[{app_id}] No clean build — skipping Phase 2")

        cost_usd = round(0.003 * max_interactions / 10, 4)
        total_duration = p1_duration + p2_duration

        return {
            "app_id":         app_id,
            "app_name":       app["name"],
            "app_type":       "local_html",
            "f1":             round(f1, 3),
            "precision":      round(prec, 3),
            "recall":         round(rec, 3),
            "fdr":            fdr,
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "total_bugs_planted": total_planted,
            "bugs_found":     sorted(matched_bugs),
            "bugs_missed":    sorted({b["bug_id"] for b in bugs} - matched_bugs),
            "cost_usd":       cost_usd,
            "duration_s":     total_duration,
        }

    finally:
        server.terminate()
        server.wait()
        logger.info(f"[{app_id}] File server stopped")


async def run_live_url_app(
    app: dict,
    max_interactions: int,
) -> dict:
    """
    Run pw_batch_test against a live_url app.

    For saucedemo, the agent receives a startup_url with the auto_login query
    param so it can log in as problem_user before exploring the app.
    """
    app_id = app["app_id"]
    manifest_file = app.get("manifest")

    # Build the target URL
    base_url = app.get("buggy_url") or app.get("url", "")
    if not base_url:
        return {"error": f"{app_id}: no url defined in registry"}

    # For saucedemo: pass auto_login param so the agent handles login
    buggy_user = app.get("buggy_user")
    if buggy_user:
        startup_url = f"{base_url}?auto_login={buggy_user}"
    else:
        startup_url = base_url

    manifest = load_manifest(manifest_file) if manifest_file else None
    bugs = manifest.get("bugs", []) if manifest else []
    total_planted = manifest.get("total_planted_bugs", len(bugs)) if manifest else 0

    logger.info(f"[{app_id}] Running live URL benchmark: {startup_url}")
    t0 = time.time()
    result = await pw_batch_test(startup_url, max_interactions=max_interactions)
    duration_s = round(time.time() - t0, 1)

    anomalies = extract_anomalies(result)

    # If we have a bug manifest, score precision/recall; otherwise just report counts
    if bugs and total_planted > 0:
        matched_bugs: set[str] = set()
        unmatched: list[str] = []
        for text in anomalies:
            bid = match_anomaly_to_bug(text, bugs)
            if bid:
                matched_bugs.add(bid)
            else:
                unmatched.append(text[:200])

        tp = len(matched_bugs)
        fp = len(unmatched)
        fn = total_planted - tp
        prec = tp / max(tp + fp, 1)
        rec  = tp / max(total_planted, 1)
        f1   = 2 * prec * rec / max(prec + rec, 0.001)
        bugs_found  = sorted(matched_bugs)
        bugs_missed = sorted({b["bug_id"] for b in bugs} - matched_bugs)
    else:
        # Task-based app (e.g. the_internet) — no planted bugs to score against
        tp, fp, fn, total_planted = 0, 0, 0, 0
        prec, rec, f1 = 0.0, 0.0, 0.0
        bugs_found, bugs_missed = [], []

    cost_usd = round(0.003 * max_interactions / 10, 4)

    return {
        "app_id":             app_id,
        "app_name":           app["name"],
        "app_type":           "live_url",
        "url_tested":         startup_url,
        "f1":                 round(f1, 3),
        "precision":          round(prec, 3),
        "recall":             round(rec, 3),
        "fdr":                None,   # no clean baseline for live apps
        "true_positives":     tp,
        "false_positives":    fp,
        "false_negatives":    fn,
        "total_bugs_planted": total_planted,
        "anomalies_found":    len(anomalies),
        "bugs_found":         bugs_found,
        "bugs_missed":        bugs_missed,
        "cost_usd":           cost_usd,
        "duration_s":         duration_s,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Aggregation
# ──────────────────────────────────────────────────────────────────────────────

def compute_aggregate(per_app: dict[str, dict]) -> dict:
    results = [r for r in per_app.values() if "error" not in r]

    if not results:
        return {"error": "No successful app runs to aggregate"}

    f1_vals        = [r["f1"]        for r in results if r.get("f1")        is not None]
    prec_vals      = [r["precision"] for r in results if r.get("precision") is not None]
    rec_vals       = [r["recall"]    for r in results if r.get("recall")    is not None]
    fdr_vals       = [r["fdr"]       for r in results if r.get("fdr")       is not None]
    cost_vals      = [r["cost_usd"]  for r in results if r.get("cost_usd")  is not None]
    dur_vals       = [r["duration_s"]for r in results if r.get("duration_s")is not None]

    total_planted  = sum(r.get("total_bugs_planted", 0) for r in results)
    total_found    = sum(r.get("true_positives", 0)     for r in results)
    overall_recall = round(total_found / max(total_planted, 1), 3) if total_planted > 0 else None

    def avg(vals: list) -> float | None:
        return round(sum(vals) / len(vals), 3) if vals else None

    return {
        "apps_included":      len(results),
        "avg_f1":             avg(f1_vals),
        "avg_precision":      avg(prec_vals),
        "avg_recall":         avg(rec_vals),
        "avg_fdr":            avg(fdr_vals),
        "total_bugs_planted": total_planted,
        "total_bugs_found":   total_found,
        "overall_recall":     overall_recall,
        "total_cost_usd":     round(sum(cost_vals), 4) if cost_vals else 0.0,
        "total_duration_s":   round(sum(dur_vals), 1)  if dur_vals  else 0.0,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Summary table
# ──────────────────────────────────────────────────────────────────────────────

def print_scorecard(suite_result: dict) -> None:
    sep  = "=" * 76
    sep2 = "-" * 76

    print(f"\n{sep}")
    print("  TA STUDIO — BENCHMARK SUITE SCORECARD")
    print(f"  Suite ID: {suite_result['suite_run_id']}")
    print(f"  Run at:   {suite_result['timestamp']}")
    print(sep)

    # Per-app table
    header = f"  {'App':<22} {'Type':<12} {'F1':>6} {'P':>6} {'R':>6} {'FDR':>6} {'Bugs':>8} {'Cost':>8} {'Time':>7}"
    print(header)
    print(f"  {sep2}")

    for app_id, r in suite_result["per_app_results"].items():
        if "error" in r:
            print(f"  {app_id:<22} ERROR: {r['error']}")
            continue
        f1_str   = f"{r.get('f1', 0):.3f}"
        p_str    = f"{r.get('precision', 0):.3f}"
        rec_str  = f"{r.get('recall', 0):.3f}"
        fdr_str  = f"{r['fdr']:.3f}" if r.get("fdr") is not None else "  N/A"
        tp       = r.get("true_positives", 0)
        total    = r.get("total_bugs_planted", 0)
        bugs_str = f"{tp}/{total}" if total else "  N/A"
        cost_str = f"${r.get('cost_usd', 0):.4f}"
        dur_str  = f"{r.get('duration_s', 0):.1f}s"
        name     = r.get("app_name", app_id)[:22]
        atype    = r.get("app_type", "")[:12]
        print(f"  {name:<22} {atype:<12} {f1_str:>6} {p_str:>6} {rec_str:>6} {fdr_str:>6} {bugs_str:>8} {cost_str:>8} {dur_str:>7}")

    # Aggregate row
    agg = suite_result.get("aggregate", {})
    if agg and "error" not in agg:
        print(f"  {sep2}")
        overall_label = "AGGREGATE"
        af1  = f"{agg['avg_f1']:.3f}"    if agg.get("avg_f1")  is not None else "  N/A"
        ap   = f"{agg['avg_precision']:.3f}" if agg.get("avg_precision") is not None else "  N/A"
        ar   = f"{agg['avg_recall']:.3f}" if agg.get("avg_recall") is not None else "  N/A"
        afdr = f"{agg['avg_fdr']:.3f}"   if agg.get("avg_fdr")  is not None else "  N/A"
        abugs = f"{agg['total_bugs_found']}/{agg['total_bugs_planted']}"
        acost = f"${agg['total_cost_usd']:.4f}"
        adur  = f"{agg['total_duration_s']:.1f}s"
        print(f"  {overall_label:<22} {'(all apps)':<12} {af1:>6} {ap:>6} {ar:>6} {afdr:>6} {abugs:>8} {acost:>8} {adur:>7}")

        print(f"\n  Overall recall (bugs found / bugs planted): "
              f"{agg['total_bugs_found']}/{agg['total_bugs_planted']} "
              f"= {agg.get('overall_recall', 'N/A')}")
        print(f"  Total benchmark cost: ${agg['total_cost_usd']:.4f}")
        print(f"  Total run time:       {agg['total_duration_s']:.1f}s "
              f"({agg['total_duration_s']/60:.1f} min)")

    print(f"\n{sep}\n")


# ──────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ──────────────────────────────────────────────────────────────────────────────

async def run_suite(
    app_ids: list[str] | None = None,
    max_interactions: int = 30,
    include_docker: bool = False,
) -> dict:
    registry = load_registry()
    apps = filter_apps(registry, app_ids=app_ids, include_docker=include_docker)

    if not apps:
        logger.error("No apps matched the requested filters. Use --list to see available apps.")
        sys.exit(1)

    timestamp = datetime.now(timezone.utc).isoformat()
    ts_file   = datetime.now().strftime("%Y%m%d_%H%M%S")
    suite_id  = f"suite_{ts_file}"

    logger.info(f"{'='*60}")
    logger.info(f"BENCHMARK SUITE: {suite_id}")
    logger.info(f"Apps to run: {[a['app_id'] for a in apps]}")
    logger.info(f"Max interactions per app: {max_interactions}")
    logger.info(f"{'='*60}")

    per_app_results: dict[str, dict] = {}
    apps_tested: list[str] = []

    # Use a simple port counter so parallel local servers don't collide
    # (we run sequentially to avoid browser resource contention)
    local_port = 8879

    for app in apps:
        app_id   = app["app_id"]
        app_type = app.get("type")
        logger.info(f"\n── Running [{app_id}] ({app_type}) ──")

        try:
            if app_type == "local_html":
                result = await run_local_html_app(app, max_interactions, port=local_port)
                local_port += 1  # next app gets a different port to avoid TOCTOU
            elif app_type in ("live_url", "docker"):
                result = await run_live_url_app(app, max_interactions)
            else:
                result = {"error": f"Unsupported app type: {app_type}"}

        except Exception as exc:
            logger.exception(f"[{app_id}] run failed: {exc}")
            result = {"error": str(exc), "app_id": app_id, "app_name": app.get("name", app_id)}

        per_app_results[app_id] = result
        apps_tested.append(app_id)

        if "error" in result:
            logger.warning(f"[{app_id}] FAILED: {result['error']}")
        else:
            logger.info(
                f"[{app_id}] done — F1={result.get('f1')} "
                f"P={result.get('precision')} R={result.get('recall')} "
                f"TP={result.get('true_positives')}/{result.get('total_bugs_planted')} "
                f"cost=${result.get('cost_usd')} dur={result.get('duration_s')}s"
            )

    aggregate = compute_aggregate(per_app_results)

    suite_result = {
        "suite_run_id":    suite_id,
        "timestamp":       timestamp,
        "registry_version": registry.get("version", "unknown"),
        "max_interactions": max_interactions,
        "apps_requested":  app_ids or [a["app_id"] for a in apps],
        "apps_tested":     apps_tested,
        "per_app_results": per_app_results,
        "aggregate":       aggregate,
    }

    # Save report
    report_path = REPORTS_DIR / f"{suite_id}.json"
    with open(report_path, "w") as f:
        json.dump(suite_result, f, indent=2, default=str)
    logger.info(f"Suite report saved → {report_path}")

    # Update latest.json with suite summary
    latest_path = REPORTS_DIR / "latest.json"
    latest = {}
    if latest_path.exists():
        with open(latest_path) as f:
            try:
                latest = json.load(f)
            except json.JSONDecodeError:
                latest = {}
    latest["suite_benchmark"] = {
        "suite_run_id": suite_id,
        "timestamp":    timestamp,
        "aggregate":    aggregate,
        "apps_tested":  apps_tested,
    }
    with open(latest_path, "w") as f:
        json.dump(latest, f, indent=2, default=str)

    print_scorecard(suite_result)
    return suite_result


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="retention.sh — Master Benchmark Suite Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/run_benchmark_suite.py
  python scripts/run_benchmark_suite.py --all
  python scripts/run_benchmark_suite.py --apps task_manager,saucedemo
  python scripts/run_benchmark_suite.py --list
  python scripts/run_benchmark_suite.py --max-interactions 50
  python scripts/run_benchmark_suite.py --include-docker
        """,
    )
    parser.add_argument(
        "--apps",
        default="",
        help="Comma-separated app IDs to run (default: all available web apps)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all available web apps (same as omitting --apps)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available benchmark apps and exit",
    )
    parser.add_argument(
        "--max-interactions",
        type=int,
        default=30,
        help="Max interactions per app run (default: 30)",
    )
    parser.add_argument(
        "--include-docker",
        action="store_true",
        help="Also run docker-type apps (requires containers to be running)",
    )
    args = parser.parse_args()

    registry = load_registry()

    if args.list:
        list_apps(registry)
        sys.exit(0)

    requested_ids: list[str] | None = None
    if args.apps:
        requested_ids = [a.strip() for a in args.apps.split(",") if a.strip()]

    asyncio.run(
        run_suite(
            app_ids=requested_ids,
            max_interactions=args.max_interactions,
            include_docker=args.include_docker,
        )
    )
