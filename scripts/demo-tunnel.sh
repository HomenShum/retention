#!/bin/bash
# Demo Setup Script - Connect to retention.sh via outbound WebSocket
#
# Architecture: Your machine connects OUT to the retention.sh server.
# No ports are opened. No tunnel needed. The connection carries your identity.

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     retention.sh - Demo Setup                               ║${NC}"
echo -e "${CYAN}║     Outbound WebSocket — no tunnel, no exposed ports         ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Check for required tools
check_tool() {
    if ! command -v $1 &> /dev/null; then
        echo -e "${YELLOW}  $1 not found${NC}"
        return 1
    fi
    echo -e "${GREEN}  ✓ $1${NC}"
    return 0
}

echo -e "${CYAN}Checking prerequisites...${NC}"
check_tool python3
check_tool adb || echo -e "${YELLOW}  (Android SDK needed for emulator control)${NC}"

echo ""
echo -e "${CYAN}How it works:${NC}"
echo ""
echo "  Your machine connects OUT to the retention.sh server via WebSocket."
echo "  No ports are opened on your machine. Nothing to scan. Nothing to attack."
echo "  The TA agent on our server drives your local emulator through this connection."
echo ""
echo -e "${CYAN}Setup options:${NC}"
echo ""
echo "  1) Quick start — add one line to .mcp.json (recommended)"
echo "  2) Start the relay daemon manually"
echo "  3) Show manual steps"
echo ""

read -p "Enter choice [1-3]: " choice

case $choice in
    1)
        echo ""
        echo -e "${GREEN}Add this to your project's .mcp.json:${NC}"
        echo ""
        echo '  {'
        echo '    "mcpServers": {'
        echo '      "retention": {'
        echo '        "command": "npx",'
        echo '        "args": ["retention-mcp@latest"],'
        echo '        "env": { "TA_API_KEY": "sk-your-api-key" }'
        echo '      }'
        echo '    }'
        echo '  }'
        echo ""
        echo -e "${YELLOW}Get your API key at: https://test-studio-xi.vercel.app/docs/install${NC}"
        echo -e "${YELLOW}Then restart Claude Code to pick up the new MCP server.${NC}"
        ;;
    2)
        echo ""
        echo -e "${GREEN}Starting outbound relay daemon...${NC}"
        echo ""
        if [ -z "${TA_API_KEY:-${RETENTION_MCP_TOKEN:-}}" ]; then
            echo -e "${YELLOW}Set your API key first:${NC}"
            echo "  export TA_API_KEY=sk-your-api-key"
            echo ""
            echo -e "${YELLOW}Get one at: https://test-studio-xi.vercel.app/docs/install${NC}"
            exit 1
        fi
        bash "$SCRIPT_DIR/tunnel-daemon.sh"
        ;;
    3)
        echo ""
        echo -e "${CYAN}Manual Setup Steps:${NC}"
        echo ""
        echo "1. Start your local backend:"
        echo "   cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
        echo ""
        echo "2. Start Android emulator:"
        echo "   emulator -avd Pixel_6_API_33"
        echo ""
        echo "3. Start the outbound relay:"
        echo "   export TA_API_KEY=sk-your-api-key"
        echo "   python -m retention-mcp"
        echo ""
        echo "4. Or add to .mcp.json for automatic connection via Claude Code"
        echo ""
        echo "No tunnel needed. No ports exposed. Your machine connects out to our server."
        ;;
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac
