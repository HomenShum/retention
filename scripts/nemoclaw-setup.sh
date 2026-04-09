#!/bin/bash
# NemoClaw Setup — NVIDIA Nemotron + OpenShell + retention.sh
#
# This script configures the open agent stack for retention.sh:
#   Model:   NVIDIA Nemotron 3 Super (120B MoE, 12B active, 1M context)
#   Runtime: OpenShell (sandboxed execution) or retention.sh sandbox
#   Harness: retention.sh MCP (46+ tools) or LangChain Deep Agents
#
# Usage:
#   ./scripts/nemoclaw-setup.sh              # Interactive setup
#   ./scripts/nemoclaw-setup.sh --check      # Check current config
#   ./scripts/nemoclaw-setup.sh --test       # Run integration test

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[nemoclaw]${NC} $*"; }
ok()    { echo -e "${GREEN}[ok]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
fail()  { echo -e "${RED}[fail]${NC} $*"; }

# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

check_config() {
    echo ""
    info "Checking NemoClaw configuration..."
    echo ""

    # 1. NVIDIA API Key
    if [ -n "${NVIDIA_API_KEY:-}" ]; then
        ok "NVIDIA_API_KEY is set (${#NVIDIA_API_KEY} chars)"
    else
        fail "NVIDIA_API_KEY not set"
        echo "  Get one from: https://build.nvidia.com"
        echo "  Then: export NVIDIA_API_KEY=nvapi-..."
    fi

    # 2. retention.sh backend
    TA_URL="${TA_MCP_ENDPOINT:-http://localhost:8000/mcp}"
    if curl -s "${TA_URL%/mcp}/api/health" 2>/dev/null | grep -q '"ok"'; then
        ok "retention.sh backend is running at ${TA_URL%/mcp}"
        # Check tool count
        TOKEN_FILE="$PROJECT_ROOT/.claude/mcp-token"
        if [ -f "$TOKEN_FILE" ]; then
            TOKEN=$(cat "$TOKEN_FILE")
            TOOL_COUNT=$(curl -s -H "Authorization: Bearer $TOKEN" "${TA_URL}/tools" 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
            ok "MCP tools available: $TOOL_COUNT"
        fi
    else
        fail "retention.sh backend not reachable at ${TA_URL%/mcp}"
        echo "  Start it: cd backend && .venv/bin/python -m uvicorn app.main:app --port 8000"
    fi

    # 3. TA MCP Token
    if [ -n "${RETENTION_MCP_TOKEN:-}" ]; then
        ok "RETENTION_MCP_TOKEN is set"
    elif [ -f "$PROJECT_ROOT/.claude/mcp-token" ]; then
        ok "RETENTION_MCP_TOKEN found in .claude/mcp-token"
    else
        fail "RETENTION_MCP_TOKEN not set and .claude/mcp-token not found"
    fi

    # 4. Nemotron base URL
    NEMOTRON_URL="${NEMOTRON_BASE_URL:-https://integrate.api.nvidia.com/v1}"
    ok "Nemotron endpoint: $NEMOTRON_URL"

    # 5. OpenShell (optional)
    if command -v openshell &>/dev/null; then
        ok "OpenShell CLI installed: $(openshell --version 2>/dev/null || echo 'unknown version')"
    else
        warn "OpenShell not installed (optional — retention.sh has its own sandbox)"
        echo "  Install: https://docs.nvidia.com/openshell/latest/installation.html"
    fi

    # 6. LangChain (optional)
    if python3 -c "import langchain_core" 2>/dev/null; then
        ok "langchain-core installed (Deep Agents integration available)"
    else
        warn "langchain-core not installed (optional)"
        echo "  Install: pip install langchain-core langchain-mcp-adapters"
    fi

    echo ""
}

# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

run_test() {
    info "Running NemoClaw integration test..."
    echo ""

    TOKEN_FILE="$PROJECT_ROOT/.claude/mcp-token"
    TOKEN=""
    if [ -f "$TOKEN_FILE" ]; then
        TOKEN=$(cat "$TOKEN_FILE")
    fi

    TA_URL="${TA_MCP_ENDPOINT:-http://localhost:8000/mcp}"

    # Test 1: NemoClaw status
    info "Test 1: retention.nemoclaw.status"
    RESULT=$(curl -s -X POST "${TA_URL}/tools/call" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $TOKEN" \
        -d '{"tool":"retention.nemoclaw.status","arguments":{}}' 2>/dev/null)
    echo "  $RESULT"

    AVAILABLE=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('result',{}).get('available','?'))" 2>/dev/null || echo "error")
    if [ "$AVAILABLE" = "True" ]; then
        ok "NemoClaw is available — Nemotron API key configured"
    else
        warn "NemoClaw not available — set NVIDIA_API_KEY to enable"
    fi

    # Test 2: Tool bridge (fetch tool count)
    info "Test 2: DeepAgentBridge tool fetch"
    TOOL_COUNT=$(curl -s -H "Authorization: Bearer $TOKEN" "${TA_URL}/tools" 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "error")
    if [ "$TOOL_COUNT" -gt 0 ] 2>/dev/null; then
        ok "$TOOL_COUNT tools available for NemoClaw agent"
    else
        fail "Could not fetch tools from $TA_URL"
    fi

    echo ""
    info "Integration test complete."
}

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

interactive_setup() {
    echo ""
    echo "======================================="
    echo "  NemoClaw Setup — Open Agent Stack"
    echo "======================================="
    echo ""
    echo "This configures NVIDIA Nemotron 3 Super to drive retention.sh QA tools."
    echo ""

    # Step 1: NVIDIA API Key
    if [ -z "${NVIDIA_API_KEY:-}" ]; then
        echo "Step 1: NVIDIA API Key"
        echo "  Visit https://build.nvidia.com to create a free API key."
        echo "  Select 'Nemotron 3 Super' model and click 'Get API Key'."
        echo ""
        read -p "  Enter your NVIDIA API key (nvapi-...): " API_KEY
        if [ -n "$API_KEY" ]; then
            echo "export NVIDIA_API_KEY=\"$API_KEY\"" >> "$PROJECT_ROOT/.env"
            export NVIDIA_API_KEY="$API_KEY"
            ok "Saved to .env"
        fi
    else
        ok "NVIDIA_API_KEY already set"
    fi

    # Step 2: Ensure TA backend is running
    echo ""
    echo "Step 2: retention.sh Backend"
    TA_URL="${TA_MCP_ENDPOINT:-http://localhost:8000/mcp}"
    if curl -s "${TA_URL%/mcp}/api/health" 2>/dev/null | grep -q '"ok"'; then
        ok "Backend is running"
    else
        info "Starting backend..."
        cd "$PROJECT_ROOT/backend"
        .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &
        sleep 3
        if curl -s "http://localhost:8000/api/health" 2>/dev/null | grep -q '"ok"'; then
            ok "Backend started"
        else
            fail "Could not start backend. Start manually:"
            echo "  cd backend && .venv/bin/python -m uvicorn app.main:app --port 8000"
        fi
    fi

    echo ""
    echo "Step 3: Verify"
    check_config

    echo ""
    info "Setup complete. Test NemoClaw with:"
    echo ""
    echo '  # Via MCP tool call:'
    echo '  curl -X POST http://localhost:8000/mcp/tools/call \'
    echo '    -H "Content-Type: application/json" \'
    echo '    -H "Authorization: Bearer $(cat .claude/mcp-token)" \'
    echo "    -d '{\"tool\":\"retention.nemoclaw.status\",\"arguments\":{}}'"
    echo ""
    echo '  # Via Python:'
    echo '  from app.integrations.nemoclaw import NemoClawAgent'
    echo '  agent = NemoClawAgent()'
    echo '  result = await agent.run("Test checkout on https://mystore.com")'
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

case "${1:-}" in
    --check)  check_config ;;
    --test)   run_test ;;
    *)        interactive_setup ;;
esac
