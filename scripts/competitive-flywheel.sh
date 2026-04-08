#!/bin/bash
# Competitive Research + Self-Test Flywheel
# Runs autonomously: researches competitors, self-tests the app, identifies gaps, implements fixes
#
# Loop: Research → Self-Test → Gap Analysis → Fix → Notify

PROJ="/Users/Shared/vscode_ta/project_countdown/my-fullstack-app"
cd "$PROJ" || exit 1

PROMPT='You are the retention.sh competitive flywheel agent. You run a continuous improvement cycle.

## Phase 1: Competitive Research (5 min)
Use WebSearch to research the LATEST news, features, and pricing from these competitors:
- Momentic (momentic.ai) — AI web testing
- QA Wolf (qawolf.com) — QA-as-a-service
- Lucent (lucenthq.com) — session replay AI
- Shortest (shortest.com) — AI test runner
- Octomind — AI E2E testing
- Playwright MCP — open source competitor

For each, find: new features shipped in the last month, pricing changes, new funding, blog posts about their approach.

Save findings to: backend/data/competitive_intel/latest_scan.json with structure:
{
  "scan_date": "ISO date",
  "competitors": [
    {"name": "...", "latest_features": [...], "pricing": "...", "funding": "...", "threat_level": "low|medium|high", "our_gap": "what we lack"}
  ]
}

## Phase 2: Self-Test (3 min)
1. Ensure both servers are running (backend on 8000, frontend on 5173)
2. Call our own discover-tasks endpoint:
   curl -s -X POST http://localhost:8000/api/benchmarks/comparison/discover-tasks \
     -H "Content-Type: application/json" \
     -d "{\"url\": \"http://localhost:5173/demo\", \"label\": \"retention-self-test\", \"crawl_depth\": 1}"
3. Save discovered task count and any errors to: backend/data/competitive_intel/self_test_results.json
4. Check for: broken pages (0 elements found), console errors, missing routes, API 404s/500s

## Phase 3: Gap Analysis (2 min)
Compare competitor features against our capabilities. For each competitor feature we lack:
- Rate priority: critical (blocks sales), high (competitive disadvantage), medium (nice to have)
- Note which of our existing 22 differentiators counterbalances it
- Identify ONE improvement we can make right now (< 30 lines of code)

Write analysis to: backend/data/competitive_intel/gap_analysis.json

## Phase 4: Implement ONE Fix (5 min)
Pick the highest-priority gap or self-test failure and fix it:
- Keep changes small (1-2 files, < 30 lines)
- Run tests after: cd backend && python3 -m pytest --ignore=tests/test_test_generation.py --ignore=app/benchmarks/comprehensive_test.py -x -q
- If tests pass, commit on a branch:
  git checkout -b flywheel/$(date +%Y%m%d-%H%M) 2>/dev/null || git checkout flywheel/$(date +%Y%m%d-%H%M)
  git add -A && git commit -m "improve: <what you fixed>

Competitive flywheel: <which competitor feature inspired this>

Co-Authored-By: OpenClaw TA Agent <agent@retentions.ai>"

## Phase 5: Notify
Use scripts/notify-imessage.sh and scripts/notify-slack.sh to report:
[TA Flywheel] Competitive scan complete
- Competitors scanned: N
- Self-test: N tasks discovered, M issues found
- Gap fixed: <description>
- Branch: flywheel/<timestamp>

SAFETY RULES:
- Never modify golden_bugs.json, auth code, or API contracts
- Never force push or commit to main
- Keep fixes small and safe — one improvement per cycle
- If servers are not running, skip self-test phase and note it
- If no gaps found, report "all clear" and skip Phase 4'

bash "$PROJ/scripts/agent-run-task.sh" "Competitive Flywheel" "$PROMPT"
