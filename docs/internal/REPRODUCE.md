# Reproduce retention.sh Benchmarks

Every number shown on the retention.sh dashboard is verifiable. This guide walks you through reproducing our results on your own machine.

## Quick Verify (< 30 seconds, no API keys needed)

```bash
# Verify all data files match what the frontend displays
python backend/scripts/verify_stats.py
```

This reads the actual JSON files in `backend/data/` and checks every metric. Expected output: `24 passed, 0 failed`.

## Full Verification Chain

```bash
# One command — runs all verification steps
./scripts/verify.sh
```

This script:
1. Verifies data file integrity (verify_stats.py)
2. Runs the three-lane benchmark offline (no device needed)
3. Checks Convex API connectivity (if env keys present)
4. Compares computed stats against frontend-displayed values
5. Outputs a verification report with PASS/FAIL per metric

## Step-by-Step Manual Verification

### Step 1: Clone and Install

```bash
git clone <repo>
cd my-fullstack-app/backend
pip install -r requirements.txt
```

### Step 2: Verify Data Integrity

```bash
python scripts/verify_stats.py
```

What it checks:
- 20 replay results in `data/replay_results/` — success rate, token savings, time savings
- 157 eval results in `data/rerun_eval/` — composite scores, grades, cost savings
- 2 three-lane benchmarks in `data/three_lane_benchmarks/` — lane costs, scores
- 2 ROP manifests in `data/rop_manifests/` — schema validity
- Zero fabricated data (no Math.random in frontend code)

### Step 3: Reproduce Three-Lane Benchmark (Offline)

```bash
# Re-evaluate existing replay data under current model pricing
python scripts/run_three_lane_live.py --offline
```

This reads the 20 replay results and re-scores them. You should see the same numbers as the dashboard:
- Lane 1 (Frontier): composite ~0.81, cost ~$0.12
- Lane 2 (Retained): composite ~0.77, 74% cost savings
- Lane 3 (Small Model): composite ~0.82, 99.2% cost savings

### Step 4: Run Live Benchmark (Requires Emulator)

```bash
# Requires: Android emulator running + adb connected
python scripts/run_three_lane_live.py --live \
  --app-url http://localhost:5173 \
  --task-name my_workflow
```

This runs fresh discovery → replay → cheap-model-replay on a real device.

### Step 5: Verify API Connectivity

```bash
# Requires: backend .env with CONVEX_SITE_URL and CRON_AUTH_TOKEN
python scripts/verify_api.py
```

Tests real API calls to Convex (trajectory sync, token verification, savings recording).

### Step 6: Start Backend and Check Live Stats

```bash
cd backend
uvicorn app.main:app --port 8000

# In another terminal:
curl http://localhost:8000/api/stats/live | python -m json.tool
```

The `/api/stats/live` endpoint scans all data files in real-time and returns verified aggregated stats. Every number includes a `source_files` count so you know exactly how many files contributed.

## What the Data Files Contain

| Directory | Files | What's Inside |
|-----------|-------|---------------|
| `data/replay_results/` | 20 | Each replay: steps matched/drifted, token savings %, time savings %, success |
| `data/rerun_eval/` | 157 | Each eval: 10-metric scorecard (completion, F1, cost, composite, grade) |
| `data/three_lane_benchmarks/` | 5 | Three-lane comparisons (frontier vs retained vs cheap model) |
| `data/trajectories/` | 2 | Full step-by-step paths with MCP tool calls for replay |
| `data/rop_manifests/` | 2 | DRX (Deep Research) and CSP (Cross-Stack Change) patterns |

## Key Numbers to Verify

| Metric | Our Claim | Source | How to Check |
|--------|-----------|--------|-------------|
| 20 replay runs, 100% success | `data/replay_results/` | `ls data/replay_results/*.json \| wc -l` |
| 67.8% avg token savings | Computed from 20 files | `python scripts/verify_stats.py` |
| 157 evals, avg composite 0.779 | `data/rerun_eval/` | `python scripts/verify_stats.py` |
| $23.91 total cost saved | $31.16 baseline - $7.25 replay | `python scripts/verify_stats.py` |
| Grade distribution: 141 B, 16 C | From 157 eval files | `python scripts/verify_stats.py` |
| Lane 3 cost: $0.0072 | Haiku replay pricing | `python scripts/run_three_lane_live.py --offline` |

## Environment Variables

For full verification including API calls, set these in `backend/.env`:

```bash
# Required for live stats + Convex sync
CONVEX_SITE_URL=https://your-deployment.convex.site
CRON_AUTH_TOKEN=your-cron-token

# Required for LLM-based benchmarks
OPENAI_API_KEY=sk-your-key

# Optional
RETENTION_MCP_TOKEN=sk-ret-your-token
```

Without env keys, the verification script still works — it checks data file integrity, not API connectivity.
