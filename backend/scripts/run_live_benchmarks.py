#!/usr/bin/env python3
"""
Live Benchmark Runner — produces real data for the competitive moat deck.

Tier 1: QA Pipeline on 4 demo apps (screens, workflows, test cases)
Tier 2: Golden Bugs evaluation (precision, recall, F1)
Tier 3: Cost & speed economics (computed from Tier 1+2)

Usage:
    cd backend
    python scripts/run_live_benchmarks.py --tier all
    python scripts/run_live_benchmarks.py --tier 1 --apps google-contacts
    python scripts/run_live_benchmarks.py --tier 2
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.agents.qa_pipeline.qa_pipeline_service import QAPipelineService
from app.agents.device_testing import MobileMCPClient, UnifiedBugReproductionService
from app.agents.device_testing.golden_bug_service import GoldenBugService
from app.benchmarks.evidence_schema import BenchmarkCost, BENCHMARK_MODEL_PRICING

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("live_benchmarks")

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BACKEND_DIR / "data" / "benchmark_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Cost estimation heuristics (tokens per agent turn)
# Crawl: ~2K input + 500 output per turn (gpt-5.4-mini)
# Workflow analysis: ~5K input + 2K output (gpt-5.4)
# Test case generation: ~8K input + 4K output (gpt-5.4)
COST_HEURISTICS = {
    "crawl_turn": {"input": 2000, "output": 500, "model": "gpt-5.4-mini"},
    "workflow": {"input": 5000, "output": 2000, "model": "gpt-5.4"},
    "testcase": {"input": 8000, "output": 4000, "model": "gpt-5.4"},
    "golden_bug_attempt": {"input": 3000, "output": 1000, "model": "gpt-5.4-mini"},
    "golden_bug_judge": {"input": 2000, "output": 200, "model": "gpt-5.4"},
}


def estimate_cost(heuristic_key: str, multiplier: int = 1) -> float:
    """Estimate cost in USD using heuristic token counts."""
    h = COST_HEURISTICS[heuristic_key]
    pricing = BENCHMARK_MODEL_PRICING.get(h["model"], {"input": 0.75, "output": 4.50})
    cost = (
        h["input"] * multiplier * pricing["input"] / 1_000_000
        + h["output"] * multiplier * pricing["output"] / 1_000_000
    )
    return round(cost, 6)


def load_demo_apps():
    """Load demo app catalog."""
    paths = [
        BACKEND_DIR / "data" / "demo_apps.json",
        Path("data/demo_apps.json"),
    ]
    for p in paths:
        if p.exists():
            with open(p) as f:
                return json.load(f).get("apps", [])
    raise FileNotFoundError("demo_apps.json not found")


async def run_tier1(device_id: str, app_filter: list[str] | None = None) -> dict:
    """Tier 1: QA Pipeline on demo apps."""
    logger.info("═══ TIER 1: QA Pipeline on Demo Apps ═══")

    apps = load_demo_apps()
    if app_filter:
        apps = [a for a in apps if a["id"] in app_filter]

    logger.info(f"Running pipeline on {len(apps)} apps: {[a['id'] for a in apps]}")

    client = MobileMCPClient()
    await client.start()

    pipeline = QAPipelineService(mobile_mcp_client=client)
    app_results = []

    for app in apps:
        app_id = app["id"]
        app_name = app["name"]
        package = app["package"]
        target_workflows = app.get("target_workflows")
        crawl_hints = app.get("crawl_hints")
        max_crawl_turns = app.get("max_crawl_turns")

        logger.info(f"── Pipeline: {app_name} ({package}) ──")
        start_time = time.time()

        crawl_turns = 0
        screens = 0
        workflows = 0
        test_cases = 0
        final_result = None

        try:
            async for event in pipeline.run_pipeline(
                app_name=app_name,
                package_name=package,
                device_id=device_id,
                target_workflows=target_workflows,
                crawl_hints=crawl_hints,
                max_crawl_turns=max_crawl_turns,
            ):
                event_type = event.get("type", "")

                if event_type == "tool_call":
                    crawl_turns += 1
                elif event_type == "crawl_progress":
                    screens = event.get("screens_found", screens)
                elif event_type == "crawl_complete":
                    screens = event.get("total_screens", screens)
                elif event_type == "workflow_identified":
                    workflows += 1
                elif event_type == "test_case_generated":
                    test_cases += 1
                elif event_type == "pipeline_complete":
                    final_result = event.get("result", {})
                    test_cases = final_result.get("total_tests", test_cases)
                    workflows = len(final_result.get("workflows", []))

        except Exception as e:
            logger.error(f"Pipeline failed for {app_name}: {e}")

        duration_s = round(time.time() - start_time, 1)

        # Estimate cost
        crawl_cost = estimate_cost("crawl_turn", crawl_turns)
        workflow_cost = estimate_cost("workflow")
        testcase_cost = estimate_cost("testcase")
        total_cost = round(crawl_cost + workflow_cost + testcase_cost, 4)

        result = {
            "app_id": app_id,
            "app_name": app_name,
            "package": package,
            "screens_discovered": screens,
            "workflows_identified": workflows,
            "test_cases_generated": test_cases,
            "crawl_turns": crawl_turns,
            "duration_s": duration_s,
            "cost_usd": total_cost,
            "cost_breakdown": {
                "crawl_usd": crawl_cost,
                "workflow_usd": workflow_cost,
                "testcase_usd": testcase_cost,
            },
        }
        app_results.append(result)
        logger.info(
            f"  ✓ {app_name}: {screens} screens, {workflows} workflows, "
            f"{test_cases} tests, {duration_s}s, ${total_cost}"
        )

        # Settle between apps
        await asyncio.sleep(3)

    await client.stop()

    # Aggregate totals
    totals = {
        "total_apps": len(app_results),
        "total_screens": sum(r["screens_discovered"] for r in app_results),
        "total_workflows": sum(r["workflows_identified"] for r in app_results),
        "total_test_cases": sum(r["test_cases_generated"] for r in app_results),
        "total_duration_s": round(sum(r["duration_s"] for r in app_results), 1),
        "total_cost_usd": round(sum(r["cost_usd"] for r in app_results), 4),
    }
    if totals["total_test_cases"] > 0:
        totals["avg_cost_per_test"] = round(
            totals["total_cost_usd"] / totals["total_test_cases"], 4
        )
    else:
        totals["avg_cost_per_test"] = 0

    logger.info(f"Tier 1 complete: {totals}")
    return {"apps": app_results, "totals": totals}


async def run_tier2(device_id: str) -> dict:
    """Tier 2: Golden Bugs Evaluation."""
    logger.info("═══ TIER 2: Golden Bugs Evaluation ═══")

    bug_repro = UnifiedBugReproductionService()
    capabilities_path = BACKEND_DIR / "capabilities.json"
    capabilities = {}
    if capabilities_path.exists():
        with open(capabilities_path) as f:
            capabilities = json.load(f)

    golden_svc = GoldenBugService(bug_repro, capabilities)
    start_time = time.time()

    try:
        report = await golden_svc.run_all_golden_bugs(
            device_id_override=device_id,
            max_attempts=3,
        )
    except Exception as e:
        logger.error(f"Golden bugs evaluation failed: {e}")
        return {
            "error": str(e),
            "precision": 0, "recall": 0, "f1": 0,
            "total_bugs": 10, "total_cost_usd": 0,
        }

    duration_s = round(time.time() - start_time, 1)
    metrics = report.metrics

    # Estimate cost: per bug = 1-3 attempts × attempt cost + judge cost
    total_attempts = sum(len(r.attempts) for r in report.runs)
    attempt_cost = estimate_cost("golden_bug_attempt", total_attempts)
    judge_cost = estimate_cost("golden_bug_judge", metrics.total_bugs)
    total_cost = round(attempt_cost + judge_cost, 4)

    per_bug = []
    for run in report.runs:
        per_bug.append({
            "bug_id": run.bug_id,
            "name": run.name if hasattr(run, "name") else run.bug_id,
            "passed": run.passed,
            "classification": run.classification,
            "attempts": len(run.attempts),
        })

    result = {
        "precision": round(metrics.precision, 3),
        "recall": round(metrics.recall, 3),
        "f1": round(metrics.f1, 3),
        "true_positives": metrics.true_positives,
        "false_positives": metrics.false_positives,
        "true_negatives": metrics.true_negatives,
        "false_negatives": metrics.false_negatives,
        "total_bugs": metrics.total_bugs,
        "bugs_passed": metrics.bugs_passed,
        "total_attempts": total_attempts,
        "total_duration_s": duration_s,
        "avg_time_per_bug_s": round(duration_s / max(metrics.total_bugs, 1), 1),
        "total_cost_usd": total_cost,
        "per_bug": per_bug,
    }

    logger.info(
        f"Tier 2 complete: P={result['precision']}, R={result['recall']}, "
        f"F1={result['f1']}, {duration_s}s, ${total_cost}"
    )
    return result


def compute_tier3(tier1: dict, tier2: dict) -> dict:
    """Tier 3: Cost & Speed Economics (computed from Tier 1+2)."""
    logger.info("═══ TIER 3: Cost & Speed Economics ═══")

    t1 = tier1.get("totals", {})
    total_tests = t1.get("total_test_cases", 0)
    total_t1_cost = t1.get("total_cost_usd", 0)
    total_t1_duration = t1.get("total_duration_s", 0)

    total_bugs = tier2.get("total_bugs", 10)
    total_t2_cost = tier2.get("total_cost_usd", 0)

    # Cost per unit
    cost_per_test = round(total_t1_cost / max(total_tests, 1), 4)
    cost_per_bug = round(total_t2_cost / max(total_bugs, 1), 4)

    # Throughput
    tests_per_hour = round(total_tests / max(total_t1_duration / 3600, 0.001), 1)
    time_to_100 = round(100 / max(tests_per_hour, 0.001) * 60, 1)  # minutes

    # Manual QA comparison
    manual_hourly_rate = 50  # USD
    manual_minutes_per_test = 15  # minutes to manually write a test case
    manual_minutes_per_bug = 30  # minutes to manually verify a bug

    manual_test_hours = total_tests * manual_minutes_per_test / 60
    manual_bug_hours = total_bugs * manual_minutes_per_bug / 60
    manual_total_hours = round(manual_test_hours + manual_bug_hours, 1)
    manual_total_cost = round(manual_total_hours * manual_hourly_rate, 2)

    automated_total_cost = round(total_t1_cost + total_t2_cost, 4)
    savings_ratio = round(manual_total_cost / max(automated_total_cost, 0.01), 0)

    result = {
        "cost_per_test_case_usd": cost_per_test,
        "cost_per_bug_verification_usd": cost_per_bug,
        "tests_per_hour": tests_per_hour,
        "time_to_100_suite_minutes": time_to_100,
        "manual_qa_hourly_rate_usd": manual_hourly_rate,
        "manual_qa_equivalent_hours": manual_total_hours,
        "manual_qa_cost_usd": manual_total_cost,
        "automated_cost_usd": automated_total_cost,
        "cost_savings_ratio": int(savings_ratio),
    }

    logger.info(f"Tier 3: ${cost_per_test}/test, {tests_per_hour} tests/hr, {savings_ratio}x savings")
    return result


async def main():
    parser = argparse.ArgumentParser(description="Live Benchmark Runner")
    parser.add_argument("--tier", default="all", help="1, 2, 3, or all")
    parser.add_argument("--device", default="emulator-5554")
    parser.add_argument("--apps", nargs="*", help="Filter apps for tier 1 (e.g., google-contacts)")
    args = parser.parse_args()

    tiers = args.tier
    device = args.device

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "device": device,
    }

    if tiers in ("1", "all"):
        report["tier1_qa_pipeline"] = await run_tier1(device, args.apps)

    if tiers in ("2", "all"):
        report["tier2_golden_bugs"] = await run_tier2(device)

    if tiers in ("3", "all"):
        t1 = report.get("tier1_qa_pipeline", {})
        t2 = report.get("tier2_golden_bugs", {})
        report["tier3_economics"] = compute_tier3(t1, t2)

    # Save report
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"live_benchmark_{ts}.json"
    latest_path = REPORTS_DIR / "latest.json"

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    with open(latest_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    logger.info(f"\n{'='*60}")
    logger.info(f"Report saved: {report_path}")
    logger.info(f"Latest link: {latest_path}")

    # Print summary
    if "tier1_qa_pipeline" in report:
        t = report["tier1_qa_pipeline"]["totals"]
        print(f"\n📊 Tier 1: {t['total_apps']} apps, {t['total_screens']} screens, "
              f"{t['total_workflows']} workflows, {t['total_test_cases']} tests, "
              f"${t['total_cost_usd']}")

    if "tier2_golden_bugs" in report:
        g = report["tier2_golden_bugs"]
        print(f"🐛 Tier 2: F1={g['f1']}, P={g['precision']}, R={g['recall']}, "
              f"${g['total_cost_usd']}")

    if "tier3_economics" in report:
        e = report["tier3_economics"]
        print(f"💰 Tier 3: ${e['cost_per_test_case_usd']}/test, "
              f"{e['tests_per_hour']} tests/hr, {e['cost_savings_ratio']}x savings")

    return report


if __name__ == "__main__":
    asyncio.run(main())
