#!/bin/bash
# retention.sh - Demo Startup Script
# Starts the backend and outbound WebSocket relay for demo

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║         retention.sh - Free Demo Startup                ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗ Python 3 not found. Please install Python 3.11+${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Python 3 found${NC}"

# Check for Android emulator
EMULATOR_STATUS=$(adb devices 2>/dev/null | grep -E "emulator|device$" | head -1 || echo "")
if [ -z "$EMULATOR_STATUS" ]; then
    echo -e "${YELLOW}⚠ No Android emulator detected. Start one with: emulator -avd <avd_name>${NC}"
else
    echo -e "${GREEN}✓ Android emulator connected: $(echo $EMULATOR_STATUS | awk '{print $1}')${NC}"
fi

echo ""
echo -e "${YELLOW}Starting services...${NC}"
echo ""

# Create logs directory
mkdir -p "$PROJECT_ROOT/logs"

# 1. Start FastAPI Backend
echo -e "${BLUE}[1/2] Starting FastAPI Backend...${NC}"
cd "$PROJECT_ROOT/backend"

# Activate venv if exists, otherwise use system python
if [ -d "venv311" ]; then
    source venv311/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

# Kill any existing process on port 8000
lsof -ti:8000 | xargs kill -9 2>/dev/null || true

# Start FastAPI in background
nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > "$PROJECT_ROOT/logs/fastapi.log" 2>&1 &
FASTAPI_PID=$!
echo -e "${GREEN}✓ FastAPI started (PID: $FASTAPI_PID)${NC}"
sleep 2

# Verify FastAPI is running
if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
    echo -e "${GREEN}✓ FastAPI health check passed${NC}"
else
    echo -e "${YELLOW}⚠ FastAPI still starting... (check logs/fastapi.log)${NC}"
fi

# 2. Start Outbound WebSocket Relay
echo ""
echo -e "${BLUE}[2/2] Starting Outbound WebSocket Relay...${NC}"
cd "$PROJECT_ROOT"

SERVER_URL="${TA_STUDIO_URL:-https://retention-backend.onrender.com}"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                   DEMO IS READY!                         ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BLUE}Architecture:${NC} Outbound WebSocket (no tunnel, no exposed ports)"
echo -e "${BLUE}Server:${NC} ${GREEN}$SERVER_URL${NC}"
echo ""
echo -e "${YELLOW}How to connect:${NC}"
echo -e "  Add to your .mcp.json:"
echo -e "  ${BLUE}{\"mcpServers\":{\"retention\":{\"command\":\"npx\",\"args\":[\"retention-mcp@latest\"],\"env\":{\"TA_API_KEY\":\"sk-your-key\"}}}}${NC}"
echo ""
echo -e "  Or start the relay manually:"
echo -e "  ${BLUE}python -m retention-mcp${NC}"
echo ""
echo -e "${YELLOW}Services:${NC}"
echo -e "  FastAPI Backend:  http://localhost:8000 (PID: $FASTAPI_PID)"
echo -e "  Relay:            Connects out to $SERVER_URL/ws/agent-relay"
echo ""
echo -e "${YELLOW}Logs:${NC}"
echo -e "  FastAPI: $PROJECT_ROOT/logs/fastapi.log"
echo ""
echo -e "${YELLOW}To stop:${NC} kill $FASTAPI_PID"
echo ""

# Keep script running to show backend output
tail -f "$PROJECT_ROOT/logs/fastapi.log"
