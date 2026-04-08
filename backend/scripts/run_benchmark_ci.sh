#!/bin/bash
# retention.sh — CI/CD Benchmark Gate
#
# Runs all active internal benchmarks and fails the build if any score
# regresses more than 10% below its recorded baseline.
#
# Exit codes:
#   0 — all benchmarks passed regression check
#   1 — one or more regressions detected (blocks CI)
#   2 — setup/dependency error

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$BACKEND_DIR"

# ── Activate virtualenv ──────────────────────────────────────────────────────
if [ ! -f ".venv/bin/activate" ]; then
    echo "ERROR: .venv not found at $BACKEND_DIR/.venv" >&2
    echo "       Run: python -m venv .venv && pip install -r requirements.txt" >&2
    exit 2
fi

source .venv/bin/activate
echo "Activated: $(python --version) from $(which python)"

# ── Run all active internal benchmarks ──────────────────────────────────────
echo ""
echo "============================================================"
echo " retention.sh — CI Benchmark Suite"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

python scripts/run_all_benchmarks.py --type internal

# ── Regression check ─────────────────────────────────────────────────────────
echo ""
echo "Checking for regressions (threshold: -10% of baseline)..."
echo ""

python - <<'PYEOF'
import json
import sys
from pathlib import Path

registry_path = Path("data/benchmarks/benchmark_registry.json")
if not registry_path.exists():
    print(f"ERROR: registry not found at {registry_path}", file=sys.stderr)
    sys.exit(2)

with open(registry_path) as f:
    registry = json.load(f)

regressions = []
checked = 0

for b in registry.get("benchmarks", []):
    if b.get("type") != "internal":
        continue
    current = b.get("our_score")
    baseline = b.get("baseline_score")
    if current is None or baseline is None:
        continue

    checked += 1
    threshold = baseline * 0.9   # 10% regression threshold
    if current < threshold:
        regressions.append(
            f"  {b['name']:<45} current={current:.3f}  "
            f"baseline={baseline:.3f}  threshold={threshold:.3f}"
        )

print(f"Checked {checked} benchmark(s) with baseline scores.")
print()

if regressions:
    print("REGRESSION DETECTED — the following benchmarks regressed >10%:")
    for r in regressions:
        print(r)
    print()
    print("CI FAILED: Fix regressions before merging.")
    sys.exit(1)

print("All benchmarks passed regression check.")
sys.exit(0)
PYEOF

REGRESSION_EXIT=$?

echo ""
if [ $REGRESSION_EXIT -eq 0 ]; then
    echo "============================================================"
    echo " CI PASSED — no benchmark regressions detected."
    echo "============================================================"
    exit 0
else
    echo "============================================================"
    echo " CI FAILED — benchmark regression(s) detected (see above)."
    echo "============================================================"
    exit 1
fi
