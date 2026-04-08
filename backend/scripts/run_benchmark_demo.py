#!/usr/bin/env python3
"""
Benchmark Demo Runner — one command to produce judged evidence.

Usage:
    python3 backend/scripts/run_benchmark_demo.py [--tasks N] [--base-url URL]

Runs N benchmark tasks (default: 5) in both modes (claude-baseline vs
test-assurance), produces evidence + scorecard, and prints a summary.

Requires:
    - pip install playwright && python3 -m playwright install chromium
    - Frontend running at base_url (default: http://localhost:5173)
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.benchmarks.evidence_schema import AgentMode
from app.benchmarks.evidence_writer import EvidenceWriter
from app.benchmarks.scorecard import ScorecardAggregator
from app.benchmarks.web_tasks.runner import BenchmarkRunner
from app.benchmarks.web_tasks.task_registry import WebTaskRegistry


def print_header(text: str):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}\n")


def print_evidence_summary(suite_id: str, writer: EvidenceWriter):
    """Print a demo-friendly evidence summary."""
    evidences = writer.list_task_evidences(suite_id)
    if not evidences:
        print("  No evidence found.")
        return

    baseline = [e for e in evidences if e.agent_mode == AgentMode.CLAUDE_BASELINE]
    ta = [e for e in evidences if e.agent_mode == AgentMode.TEST_ASSURANCE]

    print(f"  Total runs: {len(evidences)}")
    print(f"  Baseline runs: {len(baseline)}")
    print(f"  TA runs: {len(ta)}")
    print()

    # Per-task results
    print(f"  {'Task':<20} {'Mode':<20} {'Status':<8} {'Verdict':<18} {'Time':>6} {'Evidence':>9}")
    print(f"  {'-'*20} {'-'*20} {'-'*8} {'-'*18} {'-'*6} {'-'*9}")

    for ev in sorted(evidences, key=lambda e: (e.task_id, e.agent_mode.value)):
        mode_label = "Baseline" if ev.agent_mode == AgentMode.CLAUDE_BASELINE else "Test Assurance"
        status_icon = "PASS" if ev.status.value == "pass" else "FAIL"
        completeness = f"{ev.task_metrics.artifact_completeness_score:.0%}"
        duration = f"{ev.task_metrics.duration_seconds:.1f}s"
        print(
            f"  {ev.task_id:<20} {mode_label:<20} {status_icon:<8} "
            f"{ev.verdict.label.value:<18} {duration:>6} {completeness:>9}"
        )


def print_scorecard_summary(suite_id: str, writer: EvidenceWriter):
    """Print the comparison scorecard."""
    sc_data = writer.load_scorecard(suite_id)
    if not sc_data:
        print("  No scorecard found.")
        return

    comp = sc_data.get("comparison")
    if not comp:
        print("  No comparison data (need both modes).")
        return

    bl = comp["baseline"]
    ta = comp["test_assurance"]

    print(f"  {'Metric':<30} {'Baseline':>12} {'TA':>12} {'Delta':>12}")
    print(f"  {'-'*30} {'-'*12} {'-'*12} {'-'*12}")

    def fmt_pct(v): return f"{v*100:.1f}%"
    def fmt_sec(v): return f"{v:.1f}s"
    def fmt_usd(v): return f"${v:.4f}"
    def fmt_delta(v, fmt_fn, invert=False):
        s = fmt_fn(v)
        return f"+{s}" if (v > 0) != invert else s

    print(f"  {'Success Rate':<30} {fmt_pct(bl['success_rate']):>12} {fmt_pct(ta['success_rate']):>12} {fmt_delta(comp['success_rate_delta'], fmt_pct):>12}")
    print(f"  {'Avg Time':<30} {fmt_sec(bl['avg_time_to_verdict']):>12} {fmt_sec(ta['avg_time_to_verdict']):>12} {fmt_delta(comp['avg_time_delta'], fmt_sec, True):>12}")
    print(f"  {'Avg Evidence Completeness':<30} {fmt_pct(bl['avg_artifact_completeness']):>12} {fmt_pct(ta['avg_artifact_completeness']):>12} {fmt_delta(comp['avg_evidence_delta'], fmt_pct):>12}")
    print(f"  {'Total Cost':<30} {fmt_usd(bl['total_token_cost']):>12} {fmt_usd(ta['total_token_cost']):>12} {fmt_delta(comp['avg_cost_delta'], fmt_usd, True):>12}")


def print_artifact_paths(suite_id: str, writer: EvidenceWriter):
    """Show where evidence files live on disk."""
    suite_dir = writer._suite_dir(suite_id)
    if not suite_dir.exists():
        return

    print(f"  Suite directory: {suite_dir}")
    for f in sorted(suite_dir.rglob("*")):
        if f.is_file():
            rel = f.relative_to(suite_dir)
            size = f.stat().st_size
            if size > 1024:
                size_str = f"{size/1024:.1f}KB"
            else:
                size_str = f"{size}B"
            print(f"    {rel} ({size_str})")


async def main():
    parser = argparse.ArgumentParser(description="Run benchmark demo")
    parser.add_argument("--tasks", type=int, default=5, help="Number of tasks to run (default: 5)")
    parser.add_argument("--base-url", default="http://localhost:5173", help="Frontend base URL")
    parser.add_argument("--parallel", type=int, default=2, help="Parallel execution count")
    parser.add_argument("--modes", nargs="+", default=["claude-baseline", "test-assurance"],
                       help="Modes to run")
    args = parser.parse_args()

    print_header("BENCHMARK DEMO RUNNER")
    print(f"  Tasks: {args.tasks}")
    print(f"  Base URL: {args.base_url}")
    print(f"  Modes: {', '.join(args.modes)}")
    print(f"  Parallel: {args.parallel}")

    # Check if frontend is reachable
    import urllib.request
    try:
        urllib.request.urlopen(args.base_url, timeout=3)
        print(f"  Frontend: reachable")
    except Exception as e:
        print(f"\n  WARNING: Frontend not reachable at {args.base_url}")
        print(f"  Start it with: cd frontend/test-studio && npm run dev")
        print(f"  The benchmark will run but pages may fail to load.\n")

    # Initialize
    writer = EvidenceWriter()
    registry = WebTaskRegistry()
    runner = BenchmarkRunner(evidence_writer=writer, task_registry=registry)

    # Select tasks
    all_tasks = registry.list_tasks()
    task_ids = [t.task_id for t in all_tasks[:args.tasks]]
    modes = [AgentMode(m) for m in args.modes]

    print(f"\n  Running {len(task_ids)} tasks x {len(modes)} modes = {len(task_ids) * len(modes)} total runs\n")

    # Run
    print_header("EXECUTING BENCHMARK")
    start = time.time()

    def on_progress(label, done, total):
        bar = "=" * int(40 * done / total) + " " * int(40 * (1 - done / total))
        print(f"\r  [{bar}] {done}/{total} {label}", end="", flush=True)

    scorecard = await runner.run_suite(
        task_ids=task_ids,
        modes=modes,
        parallel=args.parallel,
        progress_callback=on_progress,
    )
    elapsed = time.time() - start
    print(f"\n\n  Completed in {elapsed:.1f}s\n")

    # Results
    print_header("EVIDENCE SUMMARY")
    print_evidence_summary(scorecard.suite_id, writer)

    print_header("SCORECARD COMPARISON")
    print_scorecard_summary(scorecard.suite_id, writer)

    print_header("ARTIFACT FILES")
    print_artifact_paths(scorecard.suite_id, writer)

    print_header("DEMO READY")
    print(f"  Suite ID: {scorecard.suite_id}")
    print(f"  Dashboard: {args.base_url}/demo/benchmarks")
    print(f"  API: http://localhost:8000/api/benchmarks/comparison/runs/{scorecard.suite_id}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
