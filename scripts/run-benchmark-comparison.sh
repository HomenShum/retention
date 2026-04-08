#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# retention.sh — Benchmark Comparison: No-TA vs TA-Assisted QA Loop
# ═══════════════════════════════════════════════════════════════════════════
#
# Runs the same set of tasks in two modes:
#   Mode A (claude-baseline):   Raw Playwright execution, no TA pipeline
#   Mode B (test-assurance):    Full TA pipeline with self-healing, action
#                               spans, session memory, and LLM-as-judge
#
# Produces a scorecard comparing:
#   - Success rate
#   - Time to verdict
#   - Reruns needed
#   - Evidence completeness
#   - Token cost (USD)
#
# Usage:
#   ./scripts/run-benchmark-comparison.sh                    # All tasks
#   ./scripts/run-benchmark-comparison.sh --tasks login-001,login-003
#   ./scripts/run-benchmark-comparison.sh --app-url http://localhost:3000
#   ./scripts/run-benchmark-comparison.sh --report-only      # Just show last results
#
# ═══════════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"

# Colors
C_RESET='\033[0m'
C_BOLD='\033[1m'
C_DIM='\033[2m'
C_GREEN='\033[0;32m'
C_YELLOW='\033[1;33m'
C_BLUE='\033[0;34m'
C_CYAN='\033[0;36m'
C_RED='\033[0;31m'
C_MAGENTA='\033[0;35m'

# ───────────────────────────────────────────────────────────────────────────
# Parse args
# ───────────────────────────────────────────────────────────────────────────

TASK_IDS=""
APP_URL=""
REPORT_ONLY=false
PARALLEL=2

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tasks)       TASK_IDS="$2"; shift 2 ;;
        --app-url)     APP_URL="$2"; shift 2 ;;
        --parallel)    PARALLEL="$2"; shift 2 ;;
        --report-only) REPORT_ONLY=true; shift ;;
        --help)
            echo "Usage: $0 [--tasks id1,id2] [--app-url URL] [--parallel N] [--report-only]"
            exit 0 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────

api() {
    local method=$1 path=$2
    shift 2
    curl -s -X "$method" "${BACKEND_URL}${path}" \
        -H "Content-Type: application/json" "$@"
}

check_backend() {
    if ! curl -s "${BACKEND_URL}/api/health" > /dev/null 2>&1; then
        echo -e "${C_RED}Backend not running at ${BACKEND_URL}${C_RESET}"
        echo "Start it: cd backend && uvicorn app.main:app --port 8000"
        exit 1
    fi
}

# ───────────────────────────────────────────────────────────────────────────
# Show last results
# ───────────────────────────────────────────────────────────────────────────

show_report() {
    echo ""
    echo -e "${C_CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}"
    echo -e "${C_BOLD}${C_CYAN}  BENCHMARK COMPARISON REPORT${C_RESET}"
    echo -e "${C_CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}"
    echo ""

    local runs
    runs=$(api GET "/benchmarks/comparison/runs")

    echo "$runs" | python3 -c "
import json, sys

data = json.load(sys.stdin)
runs = data.get('runs', [])

if not runs:
    print('  No benchmark runs found. Run a comparison first.')
    sys.exit(0)

# Show most recent completed run
completed = [r for r in runs if r.get('status') == 'completed']
if not completed:
    print('  No completed runs. Active runs:')
    for r in runs:
        print(f\"    {r['suite_id']}  status={r['status']}  tasks={r.get('task_count',0)}\")
    sys.exit(0)

latest = completed[-1]
suite_id = latest['suite_id']
print(f\"  Suite: {suite_id}\")
print(f\"  Status: {latest['status']}\")
print(f\"  Tasks: {latest.get('task_count', '?')}\")
print(f\"  Started: {latest.get('started_at', '?')}\")
print(f\"  Completed: {latest.get('completed_at', '?')}\")
print(f\"  suite_id={suite_id}\")
" 2>/dev/null

    # Extract suite_id and get scorecard
    local suite_id
    suite_id=$(echo "$runs" | python3 -c "
import json, sys
data = json.load(sys.stdin)
runs = data.get('runs', [])
completed = [r for r in runs if r.get('status') == 'completed']
if completed: print(completed[-1]['suite_id'])
" 2>/dev/null || echo "")

    if [ -z "$suite_id" ]; then
        return
    fi

    echo ""
    echo -e "  ${C_BLUE}Scorecard:${C_RESET}"
    echo ""

    local scorecard
    scorecard=$(api GET "/benchmarks/comparison/runs/${suite_id}/scorecard")

    echo "$scorecard" | python3 << 'PYEOF'
import json, sys

data = json.load(sys.stdin)

# Mode comparison
comp = data.get("comparison")
if comp:
    bl = comp.get("baseline", {})
    ta = comp.get("test_assurance", {})

    print("  ┌──────────────────────────────┬──────────────┬──────────────┬──────────┐")
    print("  │ Metric                       │   Baseline   │  TA-Assisted │   Delta  │")
    print("  ├──────────────────────────────┼──────────────┼──────────────┼──────────┤")

    def row(label, bl_val, ta_val, fmt=".1f", suffix="", invert=False):
        delta = ta_val - bl_val
        sign = "+" if delta >= 0 else ""
        better = (delta < 0) if invert else (delta > 0)
        color = "\033[0;32m" if better else ("\033[0;31m" if delta != 0 else "")
        reset = "\033[0m" if color else ""
        print(f"  │ {label:<28} │ {bl_val:>10{fmt}}{suffix:<2} │ {ta_val:>10{fmt}}{suffix:<2} │ {color}{sign}{delta:>6{fmt}}{suffix}{reset}  │")

    row("Success Rate",           bl.get("success_rate",0)*100,  ta.get("success_rate",0)*100, ".0f", "%")
    row("Avg Time to Verdict (s)", bl.get("avg_time_to_verdict",0), ta.get("avg_time_to_verdict",0), ".1f", "s", invert=True)
    row("Avg Reruns",              bl.get("avg_reruns",0),         ta.get("avg_reruns",0), ".1f", "", invert=True)
    row("Evidence Completeness",   bl.get("avg_artifact_completeness",0)*100, ta.get("avg_artifact_completeness",0)*100, ".0f", "%")
    row("Avg Token Cost",          bl.get("avg_token_cost",0),     ta.get("avg_token_cost",0), ".4f", "$", invert=True)
    row("Total Token Cost",        bl.get("total_token_cost",0),   ta.get("total_token_cost",0), ".4f", "$", invert=True)

    print("  ├──────────────────────────────┼──────────────┼──────────────┼──────────┤")
    row("Completed Correctly",     bl.get("completed_correctly_count",0), ta.get("completed_correctly_count",0), ".0f")
    row("Caught Failures",         bl.get("caught_failure_count",0),      ta.get("caught_failure_count",0), ".0f")
    row("Left Evidence",           bl.get("left_evidence_count",0),       ta.get("left_evidence_count",0), ".0f")
    row("Can Replay",              bl.get("can_replay_count",0),          ta.get("can_replay_count",0), ".0f")

    print("  └──────────────────────────────┴──────────────┴──────────────┴──────────┘")

    # Per-task breakdown
    per_task = comp.get("per_task", [])
    if per_task:
        print("")
        print("  Per-task breakdown:")
        print("  ┌────────────────────────┬───────────┬───────────┬──────────┬──────────┐")
        print("  │ Task                   │ BL Result │ TA Result │ BL Cost  │ TA Cost  │")
        print("  ├────────────────────────┼───────────┼───────────┼──────────┼──────────┤")
        for t in per_task[:20]:
            tid = t["task_id"][:22]
            bl_ok = "PASS" if t.get("baseline_success") else "FAIL" if t.get("baseline_success") is False else "—"
            ta_ok = "PASS" if t.get("ta_success") else "FAIL" if t.get("ta_success") is False else "—"
            bl_cost = f"${t.get('baseline_cost', 0):.4f}" if t.get("baseline_cost") is not None else "—"
            ta_cost = f"${t.get('ta_cost', 0):.4f}" if t.get("ta_cost") is not None else "—"
            print(f"  │ {tid:<22} │ {bl_ok:^9} │ {ta_ok:^9} │ {bl_cost:>8} │ {ta_cost:>8} │")
        print("  └────────────────────────┴───────────┴───────────┴──────────┴──────────┘")

else:
    print("  No comparison data available (need both modes to run)")

    # Show whatever aggregates exist
    bl_agg = data.get("baseline_aggregate")
    ta_agg = data.get("ta_aggregate")
    if bl_agg:
        print(f"\n  Baseline: {bl_agg.get('success_count',0)}/{bl_agg.get('total_tasks',0)} passed")
    if ta_agg:
        print(f"  TA-Assisted: {ta_agg.get('success_count',0)}/{ta_agg.get('total_tasks',0)} passed")
PYEOF

    echo ""
}

# ───────────────────────────────────────────────────────────────────────────
# Run benchmark
# ───────────────────────────────────────────────────────────────────────────

run_benchmark() {
    echo ""
    echo -e "${C_MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}"
    echo -e "${C_BOLD}${C_MAGENTA}  BENCHMARK COMPARISON: No-TA (Baseline) vs TA-Assisted${C_RESET}"
    echo -e "${C_MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}"
    echo ""

    # List available tasks
    echo -e "  ${C_BLUE}Available benchmark tasks:${C_RESET}"
    local tasks
    tasks=$(api GET "/benchmarks/comparison/tasks")
    echo "$tasks" | python3 -c "
import json, sys
data = json.load(sys.stdin)
tasks = data.get('tasks', [])
for t in tasks:
    print(f\"    {t['task_id']:<20} [{t['bucket']}] {t['prompt'][:60]}\")
print(f\"\n    Total: {data.get('total_count', len(tasks))} tasks\")
" 2>/dev/null || echo "    (could not list tasks)"

    # Build request
    local body="{\"parallel\": ${PARALLEL}}"
    if [ -n "$TASK_IDS" ]; then
        local ids_json
        ids_json=$(echo "$TASK_IDS" | python3 -c "
import sys; print('[' + ','.join(f'\"{t.strip()}\"' for t in sys.stdin.read().split(',')) + ']')
")
        body="{\"task_ids\": ${ids_json}, \"parallel\": ${PARALLEL}}"
    fi

    echo ""
    echo -e "  ${C_BLUE}Starting benchmark suite...${C_RESET}"
    echo -e "  ${C_DIM}Modes: claude-baseline, test-assurance${C_RESET}"
    echo -e "  ${C_DIM}Parallel: ${PARALLEL}${C_RESET}"
    echo ""

    # Start the run
    local response
    response=$(api POST "/benchmarks/comparison/run" -d "$body")

    local suite_id
    suite_id=$(echo "$response" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('suite_id', ''))
" 2>/dev/null || echo "")

    if [ -z "$suite_id" ]; then
        echo -e "${C_RED}  Failed to start benchmark:${C_RESET}"
        echo "  $response"
        exit 1
    fi

    echo -e "  ${C_GREEN}Suite started: ${suite_id}${C_RESET}"
    echo ""

    # Poll for completion
    echo -e "  ${C_BLUE}Waiting for completion...${C_RESET}"
    local status="pending"
    local last_progress=""
    while [ "$status" != "completed" ] && [ "$status" != "failed" ]; do
        sleep 3
        local run_data
        run_data=$(api GET "/benchmarks/comparison/runs/${suite_id}")
        status=$(echo "$run_data" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('status', 'unknown'))
" 2>/dev/null || echo "unknown")

        local progress
        progress=$(echo "$run_data" | python3 -c "
import json, sys
data = json.load(sys.stdin)
done = data.get('completed_tasks', 0)
total = data.get('total_work', data.get('task_count', 0))
print(f'{done}/{total}')
" 2>/dev/null || echo "?/?")

        if [ "$progress" != "$last_progress" ]; then
            echo -e "    ${C_DIM}Progress: ${progress} tasks  [${status}]${C_RESET}"
            last_progress="$progress"
        fi
    done

    if [ "$status" = "failed" ]; then
        echo -e "${C_RED}  Benchmark failed!${C_RESET}"
        api GET "/benchmarks/comparison/runs/${suite_id}" | python3 -m json.tool 2>/dev/null
        exit 1
    fi

    echo ""
    echo -e "  ${C_GREEN}Benchmark complete!${C_RESET}"

    # Show results
    show_report
}

# ───────────────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────────────

check_backend

if $REPORT_ONLY; then
    show_report
else
    run_benchmark
fi
