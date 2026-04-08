#!/usr/bin/env bash
set -euo pipefail

# retention.sh Full Benchmark Suite
# Runs all benchmarks and generates comprehensive proof data.
# Usage: ./scripts/run_full_benchmark_suite.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
PYTHON="${BACKEND_DIR}/.venv/bin/python"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "============================================================"
echo "retention.sh Full Benchmark Suite"
echo "============================================================"
echo ""

# Check Python
if [ ! -f "$PYTHON" ]; then
    PYTHON="python3"
fi

cd "$BACKEND_DIR"

# ── Step 1: Offline three-lane eval (no API needed) ──────────
echo -e "${YELLOW}[1/5]${NC} Running three-lane offline eval + multi-model comparison..."
$PYTHON scripts/run_three_lane_live.py --offline 2>&1 | grep -E "(INFO|SUMMARY|three_lane|multi_model)" | tail -5
echo -e "${GREEN}  Done${NC}"

# ── Step 2: Saucedemo benchmark (live Playwright) ────────────
echo -e "\n${YELLOW}[2/5]${NC} Running Saucedemo e-commerce benchmark..."
$PYTHON scripts/run_saucedemo_benchmark.py --max-interactions 20 2>&1 | grep -E "(F1|Precision|Recall|FDR|SUMMARY|Report)" | tail -5
echo -e "${GREEN}  Done${NC}"

# ── Step 3: Planted bug benchmark (local HTML) ───────────────
echo -e "\n${YELLOW}[3/5]${NC} Running planted bug detection benchmark..."
$PYTHON scripts/run_planted_bug_benchmark.py 2>&1 | grep -E "(F1|Precision|Recall|Found|Report)" | tail -5
echo -e "${GREEN}  Done${NC}"

# ── Step 4: Comprehensive 5-phase benchmark ──────────────────
echo -e "\n${YELLOW}[4/5]${NC} Running comprehensive 5-phase benchmark..."
$PYTHON scripts/run_comprehensive_benchmark.py 2>&1 | grep -E "(Phase|F1|FDR|Accuracy|Economics|Report)" | tail -10
echo -e "${GREEN}  Done${NC}"

# ── Step 5: Verify everything ────────────────────────────────
echo -e "\n${YELLOW}[5/5]${NC} Running verification..."
$PYTHON scripts/verify_stats.py 2>&1
echo ""

# ── Summary ──────────────────────────────────────────────────
REPLAY_COUNT=$(find "$BACKEND_DIR/data/replay_results" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')
EVAL_COUNT=$(find "$BACKEND_DIR/data/rerun_eval" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')
REPORT_COUNT=$(find "$BACKEND_DIR/data/benchmark_reports" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')
BENCHMARK_COUNT=$(find "$BACKEND_DIR/data/three_lane_benchmarks" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')

echo "============================================================"
echo "BENCHMARK SUITE COMPLETE"
echo "============================================================"
echo "  Replay results:     $REPLAY_COUNT"
echo "  Eval results:       $EVAL_COUNT"
echo "  Benchmark reports:  $REPORT_COUNT"
echo "  Three-lane + multi: $BENCHMARK_COUNT"
echo "============================================================"
