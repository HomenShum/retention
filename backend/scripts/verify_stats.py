#!/usr/bin/env python3
"""
verify_stats.py — CLI verification of all data shown on the frontend.

Reads actual data files, computes stats, and reports PASS/FAIL for each metric.
Run: python backend/scripts/verify_stats.py
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

passed = 0
failed = 0
warnings = 0


def check(label: str, expected, actual, tolerance: float = 0.01):
    global passed, failed
    if isinstance(expected, float) and isinstance(actual, float):
        ok = abs(expected - actual) <= tolerance
    else:
        ok = expected == actual
    status = "PASS" if ok else "FAIL"
    if not ok:
        failed += 1
        print(f"  {status}  {label}: expected={expected}, actual={actual}")
    else:
        passed += 1
        print(f"  {status}  {label}: {actual}")


def warn(label: str, msg: str):
    global warnings
    warnings += 1
    print(f"  WARN  {label}: {msg}")


def load_all(subdir: str):
    d = DATA_DIR / subdir
    if not d.exists():
        return []
    results = []
    for f in sorted(d.glob("*.json")):
        try:
            results.append(json.loads(f.read_text()))
        except Exception:
            continue
    return results


def verify_replay_results():
    print("\n=== REPLAY RESULTS ===")
    files = load_all("replay_results")
    check("file_count", True, len(files) > 0)
    print(f"  INFO  {len(files)} replay result files found")

    if not files:
        return {}

    successes = sum(1 for f in files if f.get("success"))
    token_savings = [f.get("comparison_with_full", {}).get("token_savings_pct", 0) for f in files]
    time_savings = [f.get("comparison_with_full", {}).get("time_savings_pct", 0) for f in files]
    tokens_full = sum(f.get("comparison_with_full", {}).get("tokens_full", 0) for f in files)
    tokens_replay = sum(f.get("comparison_with_full", {}).get("tokens_replay", 0) for f in files)

    stats = {
        "total": len(files),
        "success_rate": round(successes / len(files), 3),
        "avg_token_savings": round(sum(token_savings) / len(token_savings), 1),
        "avg_time_savings": round(sum(time_savings) / len(time_savings), 1),
        "total_tokens_saved": tokens_full - tokens_replay,
    }

    check("success_rate", True, stats["success_rate"] == 1.0)
    check("avg_token_savings > 0", True, stats["avg_token_savings"] > 0)
    check("total_tokens_saved > 0", True, stats["total_tokens_saved"] > 0)

    print(f"  INFO  success_rate={stats['success_rate']}")
    print(f"  INFO  avg_token_savings={stats['avg_token_savings']}%")
    print(f"  INFO  avg_time_savings={stats['avg_time_savings']}%")
    print(f"  INFO  total_tokens_saved={stats['total_tokens_saved']:,}")

    return stats


def verify_eval_results():
    print("\n=== EVAL RESULTS ===")
    files = load_all("rerun_eval")
    check("file_count", True, len(files) > 0)
    print(f"  INFO  {len(files)} eval files found")

    if not files:
        return {}

    composites = [f.get("composite_score", 0) for f in files]
    grades = defaultdict(int)
    for f in files:
        grades[f.get("grade", "?")] += 1

    cost_baseline = sum(f.get("cost_baseline_usd", 0) for f in files)
    cost_replay = sum(f.get("cost_replay_usd", 0) for f in files)

    stats = {
        "total": len(files),
        "avg_composite": round(sum(composites) / len(composites), 3),
        "grades": dict(grades),
        "total_cost_baseline": round(cost_baseline, 2),
        "total_cost_replay": round(cost_replay, 2),
        "total_cost_saved": round(cost_baseline - cost_replay, 2),
    }

    check("avg_composite > 0.5", True, stats["avg_composite"] > 0.5)
    check("total_cost_saved > 0", True, stats["total_cost_saved"] > 0)
    check("no_F_grades", 0, grades.get("F", 0))

    print(f"  INFO  avg_composite={stats['avg_composite']}")
    print(f"  INFO  grades={stats['grades']}")
    print(f"  INFO  total_cost_saved=${stats['total_cost_saved']}")

    return stats


def verify_three_lane():
    print("\n=== THREE-LANE BENCHMARKS ===")
    files = load_all("three_lane_benchmarks")
    benchmarks = [f for f in files if f.get("benchmark_id", "").startswith("3lane-")]
    check("benchmark_count", True, len(benchmarks) > 0)
    print(f"  INFO  {len(benchmarks)} three-lane benchmarks found")

    if not benchmarks:
        return

    b = benchmarks[0]
    lanes = b.get("lanes", [])
    check("lane_count", 3, len(lanes))

    for lane in lanes:
        lid = lane.get("lane_id", "?")
        sc = lane.get("scorecard", {})
        cost_usd = lane.get("cost_usd", 0)
        cost_replay = sc.get("cost_replay_usd", 0)

        if cost_usd == 0 and cost_replay > 0:
            warn(f"lane_{lid}_cost", f"cost_usd=0 but cost_replay_usd={cost_replay} — frontend should use cost_replay_usd")

        check(f"lane_{lid}_composite > 0", True, sc.get("composite_score", 0) > 0)
        check(f"lane_{lid}_completion", 1.0, sc.get("completion_score", 0))


def verify_manifests():
    print("\n=== ROP MANIFESTS ===")
    d = DATA_DIR / "rop_manifests"
    if not d.exists():
        warn("manifests_dir", "directory does not exist")
        return

    files = list(d.glob("*.json"))
    check("manifest_count", True, len(files) >= 2)
    print(f"  INFO  {len(files)} manifest files found")

    for f in files:
        try:
            data = json.loads(f.read_text())
            check(f"manifest_{f.stem}_has_id", True, "id" in data)
            check(f"manifest_{f.stem}_has_name", True, "name" in data)
            check(f"manifest_{f.stem}_has_triggers", True, len(data.get("triggers", [])) > 0)
        except Exception as e:
            warn(f"manifest_{f.stem}", f"failed to parse: {e}")


def verify_no_fabrication():
    print("\n=== FABRICATION CHECK ===")
    # Check if ROPDashboardPage still uses Math.random
    frontend_dir = Path(__file__).resolve().parents[2] / "frontend" / "test-studio" / "src" / "pages"
    rop_page = frontend_dir / "ROPDashboardPage.tsx"
    if rop_page.exists():
        content = rop_page.read_text()
        has_random = "Math.random()" in content
        check("no_Math.random_in_ROPDashboard", False, has_random)
    else:
        warn("ROPDashboardPage", "file not found")


def main():
    print("=" * 60)
    print("retention.sh Data Verification Report")
    print(f"Data directory: {DATA_DIR}")
    print("=" * 60)

    verify_replay_results()
    verify_eval_results()
    verify_three_lane()
    verify_manifests()
    verify_no_fabrication()

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed, {warnings} warnings")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
