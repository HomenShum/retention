#!/usr/bin/env bash
set -euo pipefail

# retention.sh Verification Chain
# Run: ./scripts/verify.sh
# Verifies all data, runs offline benchmarks, checks API connectivity

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "============================================================"
echo "retention.sh Verification Chain"
echo "============================================================"
echo ""

PASS=0
FAIL=0
SKIP=0

step() { echo -e "\n${YELLOW}[$1]${NC} $2"; }
pass() { echo -e "  ${GREEN}PASS${NC}  $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC}  $1"; FAIL=$((FAIL + 1)); }
skip() { echo -e "  ${YELLOW}SKIP${NC}  $1"; SKIP=$((SKIP + 1)); }

# ── Step 1: Data File Integrity ──────────────────────────────
step "1/5" "Verifying data file integrity"

if python3 "$BACKEND_DIR/scripts/verify_stats.py" 2>&1; then
    pass "verify_stats.py passed"
else
    fail "verify_stats.py failed"
fi

# ── Step 2: File Counts ──────────────────────────────────────
step "2/5" "Checking data file counts"

REPLAY_COUNT=$(find "$BACKEND_DIR/data/replay_results" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')
EVAL_COUNT=$(find "$BACKEND_DIR/data/rerun_eval" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')
BENCHMARK_COUNT=$(find "$BACKEND_DIR/data/three_lane_benchmarks" -name "3lane-*.json" 2>/dev/null | wc -l | tr -d ' ')
MANIFEST_COUNT=$(find "$BACKEND_DIR/data/rop_manifests" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')

[ "$REPLAY_COUNT" -ge 20 ] && pass "replay_results: $REPLAY_COUNT files (>= 20)" || fail "replay_results: $REPLAY_COUNT files (expected >= 20)"
[ "$EVAL_COUNT" -ge 150 ] && pass "rerun_eval: $EVAL_COUNT files (>= 150)" || fail "rerun_eval: $EVAL_COUNT files (expected >= 150)"
[ "$BENCHMARK_COUNT" -ge 1 ] && pass "three_lane_benchmarks: $BENCHMARK_COUNT files (>= 1)" || fail "three_lane_benchmarks: $BENCHMARK_COUNT files (expected >= 1)"
[ "$MANIFEST_COUNT" -ge 2 ] && pass "rop_manifests: $MANIFEST_COUNT files (>= 2)" || fail "rop_manifests: $MANIFEST_COUNT files (expected >= 2)"

# ── Step 3: Offline Benchmark Re-run ─────────────────────────
step "3/5" "Running three-lane benchmark (offline)"

if python3 "$BACKEND_DIR/scripts/run_three_lane_live.py" --offline 2>&1 | tail -5; then
    pass "three-lane offline eval completed"
else
    fail "three-lane offline eval failed"
fi

# ── Step 4: Convex API Connectivity ──────────────────────────
step "4/5" "Checking Convex API connectivity"

if [ -f "$BACKEND_DIR/.env" ]; then
    # Source env vars
    set -a
    source "$BACKEND_DIR/.env" 2>/dev/null || true
    set +a
fi

if [ -n "${CONVEX_SITE_URL:-}" ] && [ -n "${CRON_AUTH_TOKEN:-}" ]; then
    python3 "$BACKEND_DIR/scripts/verify_api.py" 2>&1 && pass "Convex API calls verified" || fail "Convex API calls failed"
else
    skip "Convex API check (CONVEX_SITE_URL or CRON_AUTH_TOKEN not set)"
fi

# ── Step 5: Frontend Fabrication Check ───────────────────────
step "5/5" "Checking for data fabrication in frontend"

FRONTEND_DIR="$ROOT_DIR/frontend/test-studio/src"
RANDOM_COUNT=$(grep -r "Math.random()" "$FRONTEND_DIR/pages/" 2>/dev/null | wc -l | tr -d ' ')
if [ "$RANDOM_COUNT" -eq 0 ]; then
    pass "No Math.random() in page data"
else
    fail "Found $RANDOM_COUNT instances of Math.random() in pages"
fi

# ── Summary ──────────────────────────────────────────────────
echo ""
echo "============================================================"
echo -e "RESULTS: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$SKIP skipped${NC}"
echo "============================================================"

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
