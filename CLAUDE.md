# retention.sh — Claude Code Project Guide

## What This Is
retention.sh is the always-on workflow judge for AI coding agents. FastAPI backend (Python 3.11) with MCP server integration. Agents crawl apps, save replayable trajectories, and verify QA at 60-70% fewer tokens on reruns.

## Architecture
- **Backend**: FastAPI at `backend/` — agents, API routes, services
- **Packages**: `packages/` — retention-cli, retention-mcp (TS), retention-mcp-python, retention-sdk, tcwp, create-retention-app
- **Agents**: Coordinator -> Search / Device Testing / Test Generation specialists
- **ActionSpan**: 2-3 second verification clips (~7x cheaper than full session review)
- **TCWP**: Trajectory/Checkpoint/Workflow Package — portable test artifacts

## Dev Server
- Backend: `cd backend && uvicorn app.main:app --port 8000`

## Conventions
- Commit messages: `type: description` (fix:, feat:, chore:, docs:, refactor:)
- Branch naming: `feature/`, `fix/`, `chore/`
- PRs target `main`

## Key Directories
- `backend/app/agents/` — AI agent implementations
- `backend/app/api/` — FastAPI route handlers
- `packages/retention-mcp/` — MCP server (TypeScript)
- `packages/retention-mcp-python/` — MCP server (Python)
- `packages/retention-cli/` — CLI tool
- `packages/tcwp/` — Trajectory packaging
- `tests/e2e/` — Playwright end-to-end tests
- `scripts/` — Utility and deploy scripts

## Testing
- Backend: `cd backend && python -m pytest`
- E2E: `npx playwright test` (requires backend running)

## Do NOT
- Force push to main
- Skip pre-commit hooks
- Deploy without passing all test suites
- Commit .env files, API keys, or binary assets
