#!/usr/bin/env bash
# run-model-benchmark.sh — Compare free models on retention.sh NemoClaw tasks
#
# Usage:
#   ./scripts/run-model-benchmark.sh                  # All models, all tasks
#   ./scripts/run-model-benchmark.sh --models 3       # Top 3 models only
#   ./scripts/run-model-benchmark.sh --tasks list_apps,system_check
#   ./scripts/run-model-benchmark.sh --quick           # Top 3, 2 fast tasks
#
set -euo pipefail

BASE_URL="${TA_STUDIO_URL:-http://localhost:8000}"
TOKEN="${RETENTION_MCP_TOKEN:-$(cat "$(dirname "$0")/../.claude/mcp-token" 2>/dev/null || echo "")}"
POLL_INTERVAL=5

# Parse args
MODELS=""
TASKS=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --models) MODELS="$2"; shift 2 ;;
    --tasks)  TASKS="$2"; shift 2 ;;
    --quick)  MODELS=3; TASKS="list_apps,system_check"; shift ;;
    *)        echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# Build request body
BODY='{'
if [[ -n "$MODELS" ]]; then
  BODY+="\"models\":$MODELS,"
fi
if [[ -n "$TASKS" ]]; then
  # Convert comma-separated to JSON array
  TASK_JSON=$(echo "$TASKS" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip().split(',')))")
  BODY+="\"tasks\":$TASK_JSON,"
fi
BODY="${BODY%,}}"  # Remove trailing comma

AUTH_HEADER=""
if [[ -n "$TOKEN" ]]; then
  AUTH_HEADER="-H \"Authorization: Bearer $TOKEN\""
fi

echo "╔══════════════════════════════════════════════════════╗"
echo "║     retention.sh — Model Comparison Benchmark          ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "Endpoint: $BASE_URL"
echo "Request:  $BODY"
echo ""

# Start benchmark
echo "Starting benchmark..."
RESULT=$(eval curl -s "$BASE_URL/mcp/tools/call" \
  -X POST \
  -H "'Content-Type: application/json'" \
  ${AUTH_HEADER:+-H "'Authorization: Bearer $TOKEN'"} \
  -d "'$(echo "{\"tool\":\"ta.benchmark.model_compare\",\"arguments\":$BODY}")'")

RUN_ID=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('result',{}).get('run_id',''))" 2>/dev/null || echo "")

if [[ -z "$RUN_ID" ]]; then
  echo "Failed to start benchmark:"
  echo "$RESULT" | python3 -m json.tool 2>/dev/null || echo "$RESULT"
  exit 1
fi

TOTAL=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('result',{}).get('total_work',0))")
echo "Run ID: $RUN_ID ($TOTAL model×task combinations)"
echo ""

# Poll for completion
echo "Running..."
while true; do
  sleep $POLL_INTERVAL
  STATUS=$(curl -s "$BASE_URL/mcp/tools/call" \
    -X POST \
    -H "Content-Type: application/json" \
    ${TOKEN:+-H "Authorization: Bearer $TOKEN"} \
    -d "{\"tool\":\"ta.benchmark.model_compare_status\",\"arguments\":{\"run_id\":\"$RUN_ID\"}}")

  RUN_STATUS=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('result',{}).get('status',''))" 2>/dev/null)
  PROGRESS=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('result',{}).get('progress',''))" 2>/dev/null)
  CURRENT=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('result',{}).get('current',''))" 2>/dev/null)

  if [[ "$RUN_STATUS" == "complete" ]]; then
    echo ""
    echo "✅ Benchmark complete!"
    echo ""
    break
  fi

  printf "\r  Progress: %s  |  Current: %s     " "$PROGRESS" "$CURRENT"
done

# Print ranking table
echo "$STATUS" | python3 -c "
import sys, json

data = json.load(sys.stdin).get('result', {})
ranking = data.get('ranking', [])
if not ranking:
    print('No results.')
    sys.exit(0)

print('┌──────┬─────────────────────────────────────────────────┬────────┬──────────┬─────────┬──────────┬───────────┬─────────────┬────────┐')
print('│ Rank │ Model                                           │ Score  │ Tool Acc │ Keyword │ Complete │ Latency   │ Tokens/sec  │ Errors │')
print('├──────┼─────────────────────────────────────────────────┼────────┼──────────┼─────────┼──────────┼───────────┼─────────────┼────────┤')
for r in ranking:
    model = r['model'][:47]
    print(f'│ {r[\"rank\"]:>4} │ {model:<47} │ {r[\"avg_score\"]:>5.3f}  │ {r[\"tool_accuracy\"]:>7.3f}  │ {r[\"keyword_hit\"]:>6.3f}  │ {r[\"completion\"]:>7.3f}  │ {r[\"avg_latency_ms\"]:>8.0f}ms │ {r[\"tokens_per_sec\"]:>10.1f}  │ {r[\"errors\"]:>6} │')
print('└──────┴─────────────────────────────────────────────────┴────────┴──────────┴─────────┴──────────┴───────────┴─────────────┴────────┘')
print()
winner = ranking[0]
print(f'🏆 Winner: {winner[\"model\"]}')
print(f'   Score: {winner[\"avg_score\"]:.3f} | Latency: {winner[\"avg_latency_ms\"]:.0f}ms | Throughput: {winner[\"tokens_per_sec\"]:.1f} tok/s')
"

echo ""
echo "Full results saved to: backend/data/benchmark_runs/model-bench-$RUN_ID/results.json"
