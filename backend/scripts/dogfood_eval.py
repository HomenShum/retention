#!/usr/bin/env python3
"""
Self-Test Flywheel Dogfood Eval — LLM Judge Rubric

Runs the full batch_test against a target URL, scores each dimension,
saves results to backend/data/eval_runs/, and prints a summary.

Usage:
    python scripts/dogfood_eval.py [URL] [MAX_INTERACTIONS]
    python scripts/dogfood_eval.py http://localhost:5173 20
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.self_testing.playwright_engine import pw_batch_test


def score_discovery(result: dict) -> tuple[float, str]:
    """Score discovery completeness (0-1)."""
    disc = result.get("phases", {}).get("discover", {})
    pages = disc.get("pages_found", 0)
    interactions = disc.get("total_interactions", 0)
    empty_pages = sum(1 for p in disc.get("pages", {}).values() if p.get("element_count", 0) == 0)

    if pages == 0:
        return 0.0, "No pages discovered"

    score = min(1.0, pages / 8.0) * 0.5  # pages coverage (50%)
    score += min(1.0, interactions / 30.0) * 0.3  # interaction richness (30%)
    score += max(0, 1.0 - empty_pages / max(pages, 1)) * 0.2  # no empty pages (20%)
    rationale = f"{pages} pages, {interactions} interactions, {empty_pages} empty pages"
    return round(score, 3), rationale


def score_test_execution(result: dict) -> tuple[float, str]:
    """Score test execution accuracy and diversity (0-1)."""
    test = result.get("phases", {}).get("test", {})
    results = test.get("test_results", [])
    if not results:
        return 0.0, "No tests executed"

    from collections import Counter
    types = Counter(r["action"] for r in results)
    pages = set(r["page"] for r in results)
    total_pages = result.get("summary", {}).get("pages_found", 1)

    type_diversity = len(types) / 3.0  # out of 3 types
    page_coverage = len(pages) / max(total_pages, 1)
    has_inputs = types.get("input", 0) > 0
    has_buttons = types.get("button", 0) > 0

    score = page_coverage * 0.4 + type_diversity * 0.3 + (0.15 if has_inputs else 0) + (0.15 if has_buttons else 0)
    rationale = f"{len(results)} tests, types={dict(types)}, pages={len(pages)}/{total_pages}"
    return round(min(1.0, score), 3), rationale


def score_anomaly_detection(result: dict) -> tuple[float, str]:
    """Score anomaly detection precision (0-1)."""
    detect = result.get("phases", {}).get("detect", {})
    anomalies = detect.get("anomalies", [])
    count = detect.get("anomaly_count", 0)

    if count == 0:
        return 1.0, "No anomalies — clean app (or no coverage)"

    # Check for deduplication (no two anomalies should have identical core descriptions)
    cores = set()
    for a in anomalies:
        desc = a.get("description", "")
        core = desc.split(": ", 1)[-1] if ": " in desc else desc
        cores.add(core)

    dedup_ratio = len(cores) / max(count, 1)
    has_severity = all("severity" in a for a in anomalies)

    score = dedup_ratio * 0.6 + (0.2 if has_severity else 0) + 0.2  # base credit for finding anything
    rationale = f"{count} anomalies, {len(cores)} unique cores, dedup_ratio={dedup_ratio:.2f}"
    return round(min(1.0, score), 3), rationale


def score_trace_quality(result: dict) -> tuple[float, str]:
    """Score source trace quality (0-1)."""
    trace = result.get("phases", {}).get("trace", {})
    if not trace or trace.get("total_matches", 0) == 0:
        anomalies = result.get("phases", {}).get("detect", {}).get("anomaly_count", 0)
        if anomalies == 0:
            return 1.0, "No anomalies to trace"
        return 0.0, "No trace matches found"

    matches = trace.get("frontend_matches", []) + trace.get("backend_matches", [])
    # Check no generated files
    generated = sum(1 for m in matches if "_generated/" in m["file"] or "node_modules/" in m["file"])
    has_tsx = any(m["file"].endswith((".tsx", ".jsx")) for m in matches)

    score = 0.4  # base for having matches
    score += 0.3 * (1.0 - generated / max(len(matches), 1))  # penalize generated
    score += 0.3 if has_tsx else 0.1  # bonus for component files

    rationale = f"{len(matches)} matches, {generated} generated (excluded), has_tsx={has_tsx}"
    return round(min(1.0, score), 3), rationale


def score_fix_quality(result: dict) -> tuple[float, str]:
    """Score fix suggestion quality (0-1)."""
    suggestions = result.get("phases", {}).get("suggest", {}).get("suggestions", [])
    if not suggestions:
        anomalies = result.get("phases", {}).get("detect", {}).get("anomaly_count", 0)
        if anomalies == 0:
            return 1.0, "No anomalies to fix"
        return 0.0, "No fix suggestions generated"

    has_specific_fix = sum(1 for s in suggestions if "Fix options" in s.get("suggested_fix", ""))
    has_playwright_test = sum(1 for s in suggestions if "test(" in s.get("regression_test", ""))

    score = (has_specific_fix / len(suggestions)) * 0.5 + (has_playwright_test / len(suggestions)) * 0.5
    rationale = f"{len(suggestions)} suggestions, {has_specific_fix} specific fixes, {has_playwright_test} with Playwright tests"
    return round(score, 3), rationale


def score_coverage(result: dict) -> tuple[float, str]:
    """Score coverage breadth (0-1)."""
    test = result.get("phases", {}).get("test", {})
    results = test.get("test_results", [])
    total_pages = result.get("summary", {}).get("pages_found", 1)

    pages = set(r["page"] for r in results)
    from collections import Counter
    types = Counter(r["action"] for r in results)

    page_ratio = len(pages) / max(total_pages, 1)
    type_ratio = len(types) / 3.0
    input_tested = any(r.get("input_tests") for r in results)

    score = page_ratio * 0.5 + type_ratio * 0.3 + (0.2 if input_tested else 0)
    rationale = f"{len(pages)}/{total_pages} pages, {len(types)}/3 types, input_multi_val={input_tested}"
    return round(min(1.0, score), 3), rationale


DIMENSIONS = [
    ("Discovery Completeness", 0.20, score_discovery),
    ("Test Execution Accuracy", 0.25, score_test_execution),
    ("Anomaly Detection Precision", 0.20, score_anomaly_detection),
    ("Source Trace Quality", 0.15, score_trace_quality),
    ("Fix Suggestion Quality", 0.10, score_fix_quality),
    ("Coverage Breadth", 0.10, score_coverage),
]


async def run_eval(url: str, max_interactions: int = 20) -> dict:
    """Run full eval and return structured results."""
    print(f"Running batch_test on {url} (max={max_interactions})...")
    result = await pw_batch_test(url, max_interactions=max_interactions)

    scores = []
    weighted_total = 0.0
    for name, weight, scorer in DIMENSIONS:
        score, rationale = scorer(result)
        weighted_total += score * weight
        scores.append({
            "name": name,
            "weight": weight,
            "score": score,
            "rationale": rationale,
        })

    grade_map = [(0.95, "A+"), (0.90, "A"), (0.85, "A-"), (0.80, "B+"), (0.75, "B"),
                 (0.70, "B-"), (0.65, "C+"), (0.60, "C"), (0.50, "D"), (0.0, "F")]
    grade = next(g for threshold, g in grade_map if weighted_total >= threshold)

    eval_result = {
        "eval_date": datetime.now(timezone.utc).isoformat(),
        "target_url": url,
        "max_interactions": max_interactions,
        "summary": result.get("summary", {}),
        "dimensions": scores,
        "overall_score": round(weighted_total, 3),
        "grade": grade,
        "raw_result": result,
    }
    return eval_result


def print_report(eval_result: dict) -> None:
    """Print human-readable eval report."""
    print("\n" + "=" * 60)
    print(f"  SELF-TEST FLYWHEEL EVAL — {eval_result['grade']} ({eval_result['overall_score']:.3f})")
    print("=" * 60)
    print(f"  URL: {eval_result['target_url']}")
    print(f"  Date: {eval_result['eval_date']}")
    s = eval_result.get("summary", {})
    print(f"  Pages: {s.get('pages_found', '?')}  Tests: {s.get('interactions_tested', '?')}  Anomalies: {s.get('anomalies_found', '?')}  Fixes: {s.get('fixes_suggested', '?')}")
    print("-" * 60)
    for d in eval_result["dimensions"]:
        bar = "█" * int(d["score"] * 20) + "░" * (20 - int(d["score"] * 20))
        print(f"  {d['name']:30s} {bar} {d['score']:.2f}  (w={d['weight']:.2f})")
        print(f"    {d['rationale']}")
    print("-" * 60)
    print(f"  OVERALL: {eval_result['overall_score']:.3f}  GRADE: {eval_result['grade']}")
    print("=" * 60)


async def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5173"
    max_int = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    eval_result = await run_eval(url, max_int)
    print_report(eval_result)

    # Save to eval_runs/
    runs_dir = Path(__file__).resolve().parent.parent / "data" / "eval_runs"
    runs_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = runs_dir / f"eval_{ts}.json"

    # Don't save raw_result to keep files small
    save_result = {k: v for k, v in eval_result.items() if k != "raw_result"}
    out_path.write_text(json.dumps(save_result, indent=2))
    print(f"\nSaved to {out_path}")

    # Load history and show trend
    runs = sorted(runs_dir.glob("eval_*.json"))
    if len(runs) >= 2:
        prev = json.loads(runs[-2].read_text())
        delta = eval_result["overall_score"] - prev["overall_score"]
        arrow = "↑" if delta > 0 else "↓" if delta < 0 else "→"
        print(f"Trend: {prev['overall_score']:.3f} → {eval_result['overall_score']:.3f} ({arrow} {abs(delta):.3f})")

    return eval_result


if __name__ == "__main__":
    asyncio.run(main())
