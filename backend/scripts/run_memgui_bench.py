#!/usr/bin/env python3
"""
MemGUI-Bench runner for retention.sh.

MemGUI-Bench: 128 tasks, 26 apps, cross-session memory evaluation
GitHub: https://github.com/lgy0404/MemGUI-Bench
License: MIT
Leaderboard: PR to docs/data/agents/

Submission process:
  1. Run this script against MemGUI-AVD emulator
  2. Get retention.json output
  3. Fork lgy0404/MemGUI-Bench
  4. Add retention.json to docs/data/agents/
  5. Open PR titled "[Leaderboard] Add retention.sh"
  6. 3-5 day review SLA

Agent interface: subclass AndroidWorldAgent, implement construct_command()
"""

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("memgui_benchmark")

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BACKEND_DIR / "data" / "benchmark_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
MEMGUI_DIR = BACKEND_DIR / "data" / "external_benchmarks" / "memgui_bench" / "MemGUI-Bench"


def check_setup() -> bool:
    """Verify MemGUI-Bench is installed."""
    if not MEMGUI_DIR.exists():
        print("MemGUI-Bench not found. Run first:")
        print("  bash scripts/setup_memgui_bench.sh")
        return False
    return True


def build_submission_json(results: list[dict], agent_meta: dict) -> dict:
    """Build the leaderboard submission JSON format."""
    total = len(results)
    passed = sum(1 for r in results if r.get("success"))
    
    by_difficulty = {}
    for r in results:
        d = r.get("difficulty", "unknown")
        if d not in by_difficulty:
            by_difficulty[d] = {"total": 0, "passed": 0}
        by_difficulty[d]["total"] += 1
        if r.get("success"):
            by_difficulty[d]["passed"] += 1

    return {
        "agent_name": "retention.sh",
        "version": agent_meta.get("version", "1.0"),
        "backbone": agent_meta.get("backbone", "claude-sonnet-4-6"),
        "agent_type": "closed-source",
        "institution": "retention.sh",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "paper": None,
        "code": None,
        "capabilities": {
            "multi_modal": True,
            "memory": True,
            "planning": True,
            "reflection": True,
        },
        "results": {
            "pass_at_1": round(passed / max(total, 1), 3),
            "pass_at_3": None,  # requires 3-run evaluation
            "tasks_attempted": total,
            "tasks_succeeded": passed,
            "difficulty_breakdown": {
                d: {
                    "sr": round(v["passed"] / max(v["total"], 1), 3),
                    "passed": v["passed"],
                    "total": v["total"],
                }
                for d, v in by_difficulty.items()
            },
        },
        "per_task_results": results,
        "sota_comparison": {
            "m3a_pass_at_1": 0.328,
            "agent_s2_pass_at_3": 0.492,
            "our_vs_m3a": round(passed / max(total, 1) / 0.328, 2),
        },
    }


async def run_memgui_benchmark(
    tasks: int = 128,
    device_id: str = "emulator-5554",
    dry_run: bool = False,
) -> dict:
    """Run retention.sh against MemGUI-Bench."""

    if not dry_run and not check_setup():
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("MemGUI-Bench — retention.sh")
    logger.info(f"Tasks: {tasks} | Device: {device_id} | Dry run: {dry_run}")
    logger.info("=" * 60)

    if dry_run:
        # Simulate results for CI/testing
        import random
        rng = random.Random(42)
        difficulties = ["easy"] * 48 + ["medium"] * 42 + ["hard"] * 38
        results = []
        for i in range(min(tasks, 128)):
            diff = difficulties[i]
            pass_prob = {"easy": 0.72, "medium": 0.58, "hard": 0.41}[diff]
            results.append({
                "task_id": f"memgui_{i+1:03d}",
                "task_description": f"[simulated task {i+1}]",
                "difficulty": diff,
                "success": rng.random() < pass_prob,
                "steps_taken": rng.randint(5, 40),
                "time_s": round(rng.uniform(15, 120), 1),
                "simulated": True,
            })
    else:
        # Real run via MemGUI-Bench harness
        # The harness expects the agent to be wrapped as a subclass
        # For now, dispatch via the existing mobile MCP client
        try:
            sys.path.insert(0, str(MEMGUI_DIR))
            from run import load_tasks  # type: ignore
            task_list = load_tasks()[:tasks]
        except ImportError:
            logger.error("MemGUI-Bench harness not importable. Run setup_memgui_bench.sh first.")
            sys.exit(1)

        results = []
        for task in task_list:
            t0 = time.time()
            # TODO: wire to retention.sh's AndroidWorldExecutor
            # result = await executor.run_task(task)
            result = {"task_id": task.get("task_identifier"), "success": False, "note": "Not yet wired"}
            result["time_s"] = round(time.time() - t0, 1)
            results.append(result)
            logger.info(f"  [{task.get('task_identifier')}] {'PASS' if result['success'] else 'FAIL'}")

    submission = build_submission_json(
        results,
        {"version": "1.0", "backbone": "claude-sonnet-4-6"},
    )

    # Save
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"memgui_bench_{ts}.json"
    with open(report_path, "w") as f:
        json.dump(submission, f, indent=2, default=str)

    submission_path = REPORTS_DIR / "memgui_bench_submission_latest.json"
    with open(submission_path, "w") as f:
        json.dump(submission, f, indent=2, default=str)

    passed = submission["results"]["tasks_succeeded"]
    total = submission["results"]["tasks_attempted"]
    score = submission["results"]["pass_at_1"]
    vs_sota = submission["sota_comparison"]["our_vs_m3a"]

    print(f"\n{'='*60}")
    print(f"  MemGUI-Bench Results {'(SIMULATED)' if dry_run else ''}")
    print(f"{'='*60}")
    print(f"  Pass@1:   {score:.1%}  ({passed}/{total} tasks)")
    print(f"  vs M3A:   {vs_sota:.2f}x SOTA ({score:.1%} vs 32.8%)")
    if not dry_run:
        print(f"\n  Submission file: {submission_path}")
        print(f"  To submit to leaderboard:")
        print(f"    1. Fork: https://github.com/lgy0404/MemGUI-Bench")
        print(f"    2. Copy {submission_path.name} to docs/data/agents/")
        print(f"    3. Open PR titled: [Leaderboard] Add retention.sh")
    print(f"{'='*60}\n")

    return submission


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=int, default=128)
    parser.add_argument("--device", default="emulator-5554")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    asyncio.run(run_memgui_benchmark(
        tasks=args.tasks,
        device_id=args.device,
        dry_run=args.dry_run,
    ))
