#!/bin/bash
# Setup MemGUI-Bench for retention.sh
# MIT license — emulator-only, PR-based leaderboard
# https://github.com/lgy0404/MemGUI-Bench

set -e
BACKEND_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BENCH_DIR="$BACKEND_DIR/data/external_benchmarks/memgui_bench"

echo "=== MemGUI-Bench Setup ==="
echo "Leaderboard: https://github.com/lgy0404/MemGUI-Bench"
echo "Submission: PR to docs/data/agents/ (3-5 day review)"
echo ""

# Option A: Docker (recommended)
if command -v docker &>/dev/null; then
    echo "[Docker] Pulling MemGUI-Bench container..."
    docker pull crpi-6p9eo5da91i2tx5v.cn-hangzhou.personal.cr.aliyuncs.com/memgui/memgui-bench:26020301
    echo "[Docker] Run with:"
    echo "  docker run -it --privileged --name memgui-bench -w /root/MemGUI-Bench <image> bash"
    echo "  python run.py"
    exit 0
fi

# Option B: Local clone
echo "[Local] Cloning MemGUI-Bench..."
mkdir -p "$BENCH_DIR"
cd "$BENCH_DIR"

if [ ! -d "MemGUI-Bench" ]; then
    git clone --recursive https://github.com/lgy0404/MemGUI-Bench.git
fi
cd MemGUI-Bench

echo "[Local] Installing dependencies..."
pip install -r requirements.txt 2>/dev/null || true

echo ""
echo "=== Next steps ==="
echo "1. Download MemGUI-AVD snapshot from Baidu Netdisk (code: tfnb)"
echo "   Copy to: ~/.android/avd/"
echo "2. Run: python run.py --agent retention"
echo "3. Submit results: fork repo, add JSON to docs/data/agents/, open PR"
echo ""
echo "MemGUI-Bench ready at: $BENCH_DIR/MemGUI-Bench"
