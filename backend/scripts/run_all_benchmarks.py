#!/usr/bin/env python3
"""
retention.sh — Unified Benchmark Registry & Submission Pipeline

Top-level orchestrator connecting all benchmarks (internal + external) into
one system. Loads from data/benchmarks/benchmark_registry.json.

Usage:
    cd backend
    python scripts/run_all_benchmarks.py --status
    python scripts/run_all_benchmarks.py --type internal
    python scripts/run_all_benchmarks.py --type external
    python scripts/run_all_benchmarks.py --benchmark metricspro_planted
    python scripts/run_all_benchmarks.py --benchmark task_manager_planted
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REGISTRY_PATH = BACKEND_DIR / "data" / "benchmarks" / "benchmark_registry.json"
REPORTS_DIR = BACKEND_DIR / "data" / "benchmark_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# Registry I/O
# ──────────────────────────────────────────────────────────────────────────────

def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        print(f"ERROR: benchmark registry not found: {REGISTRY_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(REGISTRY_PATH) as f:
        return json.load(f)


def save_registry(registry: dict) -> None:
    registry["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    with open(REGISTRY_PATH, "w") as f:
        json.dump(registry, f, indent=2)


# ──────────────────────────────────────────────────────────────────────────────
# Status table
# ──────────────────────────────────────────────────────────────────────────────

def _score_str(b: dict) -> str:
    score = b.get("our_score")
    if score is None:
        return "-"
    # Internal benchmarks store F1 in our_score
    if b.get("type") == "internal":
        return f"F1={score:.2f}"
    # External: percentage
    return f"{score * 100:.1f}%"


def _status_display(b: dict) -> str:
    status = b.get("status", "unknown")
    notes = b.get("notes", "")
    if status == "blocked":
        # Pull the first parenthetical from notes if present
        if ":" in notes:
            reason = notes.split(":")[1].strip().split(".")[0]
            return f"blocked ({reason[:20]})"
        return "blocked"
    if status == "ready_when_docker":
        return "ready (needs Docker)"
    return status


def _last_run_str(b: dict) -> str:
    return b.get("last_run") or "-"


def print_status_table(registry: dict) -> None:
    sep = "=" * 90
    print(f"\n{sep}")
    print("  TA STUDIO — BENCHMARK STATUS TABLE")
    print(f"  Registry v{registry.get('version', '?')}  |  "
          f"Last updated: {registry.get('last_updated', '?')}")
    print(sep)

    header = (
        f"  {'BENCHMARK':<34} {'TYPE':<10} {'SCORE':<10} "
        f"{'STATUS':<26} {'LAST RUN'}"
    )
    print(header)
    print(f"  {'-' * 86}")

    for b in registry["benchmarks"]:
        name  = b["name"][:34]
        btype = b.get("type", "?")[:10]
        score = _score_str(b)
        status = _status_display(b)[:26]
        last  = _last_run_str(b)
        print(f"  {name:<34} {btype:<10} {score:<10} {status:<26} {last}")

    print(sep)

    # Summary counts
    all_b = registry["benchmarks"]
    internal = [b for b in all_b if b.get("type") == "internal"]
    external = [b for b in all_b if b.get("type") == "external_public"]
    active   = [b for b in internal if b.get("status") == "active"]
    scored   = [b for b in all_b if b.get("our_score") is not None]

    print(f"\n  Totals: {len(all_b)} benchmarks "
          f"({len(internal)} internal, {len(external)} external)")
    print(f"  Internal active/runnable: {len(active)}")
    print(f"  Benchmarks with scores: {len(scored)}")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# External benchmark guidance
# ──────────────────────────────────────────────────────────────────────────────

def print_external_status(registry: dict) -> None:
    externals = [b for b in registry["benchmarks"] if b.get("type") == "external_public"]
    sep = "=" * 70

    print(f"\n{sep}")
    print("  EXTERNAL BENCHMARK STATUS & SUBMISSION GUIDE")
    print(sep)

    for b in externals:
        print(f"\n  [{b['id']}] {b['name']}")
        print(f"    Category   : {b.get('category', 'N/A')}")
        print(f"    Owner      : {b.get('owner', 'N/A')}")
        print(f"    Paper      : {b.get('paper', 'N/A')}")
        if b.get("leaderboard") and b["leaderboard"] != "TBD - check paper":
            print(f"    Leaderboard: {b['leaderboard']}")
        if b.get("harness_repo") and b["harness_repo"] != "TBD":
            print(f"    Harness    : {b['harness_repo']}")
        if b.get("install_cmd"):
            print(f"    Install    : {b['install_cmd']}")
        tasks = b.get("tasks", "TBD")
        apps  = b.get("apps")
        task_str = f"{tasks} tasks" + (f", {apps} apps" if apps else "")
        print(f"    Tasks      : {task_str}")

        score = b.get("our_score")
        if score is not None:
            sota = b.get("sota_score")
            rank = b.get("rank", "")
            print(f"    Our score  : {score * 100:.1f}%"
                  + (f"  (SOTA: {sota * 100:.1f}%,  Rank: {rank})" if sota else ""))
        else:
            print(f"    Our score  : not yet run")

        print(f"    Status     : {b.get('status', 'unknown')}")
        sub = b.get("submission_status")
        if sub:
            print(f"    Submission : {sub}")
        priority = b.get("priority")
        if priority:
            print(f"    Priority   : {priority}")
        notes = b.get("notes")
        if notes:
            print(f"    Notes      : {notes}")

        runner = b.get("runner_script")
        if runner:
            print(f"    Run with   : python {runner}")

    print(f"\n{sep}\n")


# ──────────────────────────────────────────────────────────────────────────────
# Internal benchmark runner
# ──────────────────────────────────────────────────────────────────────────────

def _parse_f1_from_output(output: str) -> float | None:
    """
    Scan subprocess stdout for an F1 score line.
    Matches patterns like:  F1=0.462  or  f1: 0.462  or  "f1": 0.462
    """
    import re
    for line in output.splitlines():
        m = re.search(r'\bF1[=:\s]+([0-9]\.[0-9]+)', line, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return None


def run_internal_benchmark(b: dict) -> dict:
    """
    Run a single internal benchmark by calling its runner_script as a subprocess.

    Returns a result dict with at minimum: {id, name, success, f1, output, error}.
    """
    bench_id = b["id"]
    runner_raw = b.get("runner_script", "")
    if not runner_raw:
        return {
            "id": bench_id,
            "name": b["name"],
            "success": False,
            "error": "No runner_script defined in registry",
        }

    # Split into script path + any extra args embedded in the runner_script string
    parts = runner_raw.split()
    script_path = BACKEND_DIR / parts[0]
    extra_args = parts[1:]

    if not script_path.exists():
        return {
            "id": bench_id,
            "name": b["name"],
            "success": False,
            "error": f"Runner script not found: {script_path}",
        }

    cmd = [sys.executable, str(script_path)] + extra_args
    print(f"\n  Running [{bench_id}]: {' '.join(str(c) for c in cmd)}")

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(BACKEND_DIR),
            capture_output=True,
            text=True,
            timeout=600,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        combined = stdout + "\n" + stderr

        success = proc.returncode == 0
        f1 = _parse_f1_from_output(combined)

        return {
            "id": bench_id,
            "name": b["name"],
            "success": success,
            "returncode": proc.returncode,
            "f1": f1,
            "output": stdout[-3000:] if stdout else "",   # keep tail
            "error": stderr[-1000:] if not success else "",
        }
    except subprocess.TimeoutExpired:
        return {
            "id": bench_id,
            "name": b["name"],
            "success": False,
            "error": "Timeout (600s) exceeded",
        }
    except Exception as exc:
        return {
            "id": bench_id,
            "name": b["name"],
            "success": False,
            "error": str(exc),
        }


def run_all_internal(registry: dict) -> list[dict]:
    active = [
        b for b in registry["benchmarks"]
        if b.get("type") == "internal" and b.get("status") == "active"
    ]

    if not active:
        print("No active internal benchmarks found in registry.")
        return []

    print(f"\n  Running {len(active)} active internal benchmark(s)...")
    sep = "=" * 70
    print(sep)

    results = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for b in active:
        result = run_internal_benchmark(b)
        results.append(result)

        # Update registry score if we got an F1 back
        if result.get("success") and result.get("f1") is not None:
            for rb in registry["benchmarks"]:
                if rb["id"] == b["id"]:
                    # Preserve previous score as baseline before overwriting
                    if rb.get("our_score") is not None and rb.get("baseline_score") is None:
                        rb["baseline_score"] = rb["our_score"]
                    rb["our_score"] = result["f1"]
                    rb["last_run"] = now
                    break

        status_icon = "PASS" if result.get("success") else "FAIL"
        f1_str = f"F1={result['f1']:.3f}" if result.get("f1") is not None else "F1=N/A"
        print(f"  [{status_icon}] {result['name']:<40} {f1_str}")
        if result.get("error"):
            print(f"         ERROR: {result['error'][:120]}")

    print(sep)
    save_registry(registry)
    print(f"  Registry updated → {REGISTRY_PATH}")

    # Save run report
    report = {
        "run_type": "internal",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"run_all_internal_{ts}.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"  Run report saved → {report_path}\n")

    return results


def run_single_benchmark(registry: dict, bench_id: str) -> None:
    matches = [b for b in registry["benchmarks"] if b["id"] == bench_id]
    if not matches:
        available = [b["id"] for b in registry["benchmarks"]]
        print(f"ERROR: benchmark '{bench_id}' not found in registry.", file=sys.stderr)
        print(f"Available IDs: {', '.join(available)}", file=sys.stderr)
        sys.exit(1)

    b = matches[0]
    btype = b.get("type", "")

    if btype == "external_public":
        print(f"\n  [{b['id']}] {b['name']} is an EXTERNAL benchmark.")
        print(f"  It cannot be run directly by this script.")
        print(f"  Paper      : {b.get('paper', 'N/A')}")
        runner = b.get("runner_script")
        if runner:
            print(f"  Run with   : python {runner}")
        else:
            print(f"  See harness: {b.get('harness_repo', 'N/A')}")
        if b.get("install_cmd"):
            print(f"  Install    : {b['install_cmd']}")
        return

    if btype == "internal":
        status = b.get("status", "unknown")
        if status == "blocked":
            print(f"\n  [{b['id']}] {b['name']} is BLOCKED.")
            print(f"  Notes: {b.get('notes', '')}")
            sys.exit(1)
        if status == "ready_when_docker":
            print(f"\n  [{b['id']}] {b['name']} requires Docker.")
            print(f"  Setup: {b.get('notes', '')}")
            sys.exit(1)

        result = run_internal_benchmark(b)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if result.get("success") and result.get("f1") is not None:
            for rb in registry["benchmarks"]:
                if rb["id"] == b["id"]:
                    if rb.get("our_score") is not None and rb.get("baseline_score") is None:
                        rb["baseline_score"] = rb["our_score"]
                    rb["our_score"] = result["f1"]
                    rb["last_run"] = now
                    break
            save_registry(registry)
            print(f"\n  Registry updated with new score: F1={result['f1']:.3f}")

        status_icon = "PASS" if result.get("success") else "FAIL"
        print(f"\n  [{status_icon}] {result['name']}")
        if result.get("f1") is not None:
            print(f"  F1 = {result['f1']:.3f}")
        if result.get("error"):
            print(f"  ERROR: {result['error']}")
        if result.get("output"):
            print("\n  --- Output (tail) ---")
            print(result["output"][-2000:])
        return

    print(f"ERROR: unknown benchmark type '{btype}' for '{bench_id}'", file=sys.stderr)
    sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="retention.sh — Unified Benchmark Registry & Submission Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/run_all_benchmarks.py --status
  python scripts/run_all_benchmarks.py --type internal
  python scripts/run_all_benchmarks.py --type external
  python scripts/run_all_benchmarks.py --benchmark metricspro_planted
  python scripts/run_all_benchmarks.py --benchmark android_world
        """,
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print full benchmark status table and exit",
    )
    parser.add_argument(
        "--type",
        choices=["internal", "external"],
        help="Run all internal benchmarks, or show external guidance",
    )
    parser.add_argument(
        "--benchmark",
        metavar="ID",
        help="Run (or show instructions for) a specific benchmark by registry ID",
    )
    args = parser.parse_args()

    if not (args.status or args.type or args.benchmark):
        parser.print_help()
        sys.exit(0)

    registry = load_registry()

    if args.status:
        print_status_table(registry)
        return

    if args.type == "internal":
        run_all_internal(registry)
        return

    if args.type == "external":
        print_external_status(registry)
        return

    if args.benchmark:
        run_single_benchmark(registry, args.benchmark)
        return


if __name__ == "__main__":
    main()
