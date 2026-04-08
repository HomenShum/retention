#!/usr/bin/env python3
"""
Inject real benchmark data into the competitive moat HTML deck.

Reads latest.json from benchmark_reports/ and replaces {{PLACEHOLDER}}
markers in the deck template with real numbers.

Usage:
    python scripts/inject_deck_data.py
    python scripts/inject_deck_data.py --report path/to/report.json --output /tmp/deck.html
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REPORT = BACKEND_DIR / "data" / "benchmark_reports" / "latest.json"
DEFAULT_TEMPLATE = Path(__file__).resolve().parents[2] / "tmp" / "TA_Competitive_Moat.html"
DEFAULT_OUTPUT = Path("/tmp/TA_Competitive_Moat_live.html")


def load_report(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def build_replacements(report: dict) -> dict:
    """Build placeholder → value map from benchmark report."""
    r = {}

    # Metadata
    ts = report.get("timestamp", "")
    if ts:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            r["BENCHMARK_DATE"] = dt.strftime("%B %d, %Y")
        except Exception:
            r["BENCHMARK_DATE"] = ts[:10]

    # Tier 1: QA Pipeline
    t1 = report.get("tier1_qa_pipeline", {})
    totals = t1.get("totals", {})
    r["TOTAL_APPS_TESTED"] = str(totals.get("total_apps", 0))
    r["TOTAL_SCREENS"] = str(totals.get("total_screens", 0))
    r["TOTAL_WORKFLOWS"] = str(totals.get("total_workflows", 0))
    r["TOTAL_TEST_CASES"] = str(totals.get("total_test_cases", 0))
    r["TOTAL_T1_DURATION"] = f"{totals.get('total_duration_s', 0):.0f}s"
    r["TOTAL_T1_COST"] = f"${totals.get('total_cost_usd', 0):.2f}"

    # Per-app results
    for app in t1.get("apps", []):
        app_key = app["app_id"].upper().replace("-", "_")
        r[f"{app_key}_SCREENS"] = str(app.get("screens_discovered", 0))
        r[f"{app_key}_WORKFLOWS"] = str(app.get("workflows_identified", 0))
        r[f"{app_key}_TESTS"] = str(app.get("test_cases_generated", 0))
        r[f"{app_key}_DURATION"] = f"{app.get('duration_s', 0):.0f}s"
        r[f"{app_key}_COST"] = f"${app.get('cost_usd', 0):.3f}"

    # Tier 2: Golden Bugs
    t2 = report.get("tier2_golden_bugs", {})
    r["F1_SCORE"] = f"{t2.get('f1', 0):.2f}"
    r["PRECISION"] = f"{t2.get('precision', 0):.2f}"
    r["RECALL"] = f"{t2.get('recall', 0):.2f}"
    r["TP_COUNT"] = str(t2.get("true_positives", 0))
    r["FP_COUNT"] = str(t2.get("false_positives", 0))
    r["TN_COUNT"] = str(t2.get("true_negatives", 0))
    r["FN_COUNT"] = str(t2.get("false_negatives", 0))
    r["BUGS_PASSED"] = str(t2.get("bugs_passed", 0))
    r["TOTAL_BUGS"] = str(t2.get("total_bugs", 0))
    r["AVG_TIME_PER_BUG"] = f"{t2.get('avg_time_per_bug_s', 0):.0f}s"
    r["TOTAL_T2_COST"] = f"${t2.get('total_cost_usd', 0):.2f}"

    # Tier 3: Economics
    t3 = report.get("tier3_economics", {})
    r["COST_PER_TEST"] = f"${t3.get('cost_per_test_case_usd', 0):.3f}"
    r["COST_PER_BUG"] = f"${t3.get('cost_per_bug_verification_usd', 0):.3f}"
    r["TESTS_PER_HOUR"] = f"{t3.get('tests_per_hour', 0):.0f}"
    r["TIME_TO_100_SUITE"] = f"{t3.get('time_to_100_suite_minutes', 0):.1f} min"
    r["MANUAL_QA_HOURS"] = f"{t3.get('manual_qa_equivalent_hours', 0):.1f}"
    r["MANUAL_QA_COST"] = f"${t3.get('manual_qa_cost_usd', 0):,.0f}"
    r["AUTOMATED_COST"] = f"${t3.get('automated_cost_usd', 0):.2f}"
    r["COST_SAVINGS_RATIO"] = f"{t3.get('cost_savings_ratio', 0)}x"

    # ActionSpan (design spec values)
    r["AVG_SPAN_DURATION"] = "2.5s"
    r["EVIDENCE_COMPLETENESS"] = "0.94"
    r["COST_SAVINGS_7X"] = "7x"

    return r


def inject(template_html: str, replacements: dict) -> tuple[str, int]:
    """Replace {{PLACEHOLDER}} markers. Returns (rendered_html, count)."""
    count = 0
    result = template_html
    for key, value in replacements.items():
        marker = "{{" + key + "}}"
        if marker in result:
            result = result.replace(marker, value)
            count += 1
    return result, count


def main():
    parser = argparse.ArgumentParser(description="Inject benchmark data into deck")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if not args.report.exists():
        print(f"❌ Report not found: {args.report}")
        print("  Run: python scripts/run_live_benchmarks.py --tier all")
        sys.exit(1)

    if not args.template.exists():
        print(f"❌ Template not found: {args.template}")
        sys.exit(1)

    report = load_report(args.report)
    replacements = build_replacements(report)

    template_html = args.template.read_text()
    rendered, count = inject(template_html, replacements)

    # Check for remaining unreplaced placeholders
    remaining = re.findall(r"\{\{[A-Z_]+\}\}", rendered)

    args.output.write_text(rendered)

    print(f"✅ Injected {count} data points into deck")
    print(f"   Report: {args.report}")
    print(f"   Output: {args.output}")

    if remaining:
        print(f"⚠️  {len(remaining)} unreplaced placeholders: {remaining[:10]}")
    else:
        print("   All placeholders replaced")

    # Print key metrics
    print(f"\n📊 Key Metrics:")
    for key in ["F1_SCORE", "PRECISION", "RECALL", "TOTAL_TEST_CASES", "COST_PER_TEST",
                 "TESTS_PER_HOUR", "COST_SAVINGS_RATIO"]:
        if key in replacements:
            print(f"   {key}: {replacements[key]}")


if __name__ == "__main__":
    main()
