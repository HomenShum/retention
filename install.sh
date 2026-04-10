#!/usr/bin/env bash
# retention.sh — one-command install
# Usage: curl -sL https://raw.githubusercontent.com/HomenShum/retention/main/install.sh | bash

set -e

GREEN='\033[0;32m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${GREEN}${BOLD}retention.sh${NC} — the always-on workflow judge for AI coding agents"
echo ""

# Detect environment
EDITOR=""
if [ -d ".claude" ] || [ -d "$HOME/.claude" ]; then
  EDITOR="claude"
elif [ -d ".cursor" ] || [ -d "$HOME/.cursor" ]; then
  EDITOR="cursor"
elif [ -d ".codex" ] || [ -d "$HOME/.codex" ]; then
  EDITOR="codex"
fi

# Step 1: Install CLI
echo -e "${DIM}[1/3] Installing retention CLI...${NC}"
if command -v npm &> /dev/null; then
  npm install -g retention 2>/dev/null || npx retention --version 2>/dev/null || true
  echo -e "${GREEN}  ✓ CLI available${NC}"
else
  echo -e "${DIM}  npm not found — CLI install skipped. Install Node.js for CLI support.${NC}"
fi

# Step 2: Install Python SDK
echo -e "${DIM}[2/3] Installing retention Python SDK...${NC}"
if command -v pip &> /dev/null; then
  pip install retention 2>/dev/null || pip install --user retention 2>/dev/null || true
  echo -e "${GREEN}  ✓ Python SDK available${NC}"
elif command -v pip3 &> /dev/null; then
  pip3 install retention 2>/dev/null || pip3 install --user retention 2>/dev/null || true
  echo -e "${GREEN}  ✓ Python SDK available${NC}"
else
  echo -e "${DIM}  pip not found — SDK install skipped.${NC}"
fi

# Step 3: Configure MCP for detected editor
echo -e "${DIM}[3/3] Configuring MCP...${NC}"

MCP_CONFIG='{"mcpServers":{"retention":{"command":"npx","args":["-y","retention-mcp"],"env":{"RETENTION_BACKEND":"https://retention-backend.run.app"}}}}'

if [ "$EDITOR" = "claude" ]; then
  CONFIG_FILE=".mcp.json"
  if [ -f "$CONFIG_FILE" ]; then
    # Merge with existing
    if command -v python3 &> /dev/null; then
      python3 -c "
import json
existing = json.load(open('$CONFIG_FILE'))
new = json.loads('$MCP_CONFIG')
existing.setdefault('mcpServers', {}).update(new['mcpServers'])
json.dump(existing, open('$CONFIG_FILE', 'w'), indent=2)
print('  merged into existing .mcp.json')
"
    else
      echo "$MCP_CONFIG" > "$CONFIG_FILE"
    fi
  else
    echo "$MCP_CONFIG" > "$CONFIG_FILE"
  fi
  echo -e "${GREEN}  ✓ MCP configured for Claude Code ($CONFIG_FILE)${NC}"

elif [ "$EDITOR" = "cursor" ]; then
  mkdir -p .cursor
  CONFIG_FILE=".cursor/mcp.json"
  echo "$MCP_CONFIG" > "$CONFIG_FILE"
  echo -e "${GREEN}  ✓ MCP configured for Cursor ($CONFIG_FILE)${NC}"

elif [ "$EDITOR" = "codex" ]; then
  mkdir -p .codex
  CONFIG_FILE=".codex/mcp.json"
  echo "$MCP_CONFIG" > "$CONFIG_FILE"
  echo -e "${GREEN}  ✓ MCP configured for Codex ($CONFIG_FILE)${NC}"

else
  echo -e "${DIM}  No editor detected. Create .mcp.json manually:${NC}"
  echo -e "${DIM}  $MCP_CONFIG${NC}"
fi

# Team code support
if [ -n "$RETENTION_TEAM" ]; then
  echo ""
  echo -e "${GREEN}  ✓ Joined team: $RETENTION_TEAM${NC}"
fi

echo ""
echo -e "${GREEN}${BOLD}Done!${NC} retention.sh is installed."
echo ""
echo -e "  ${BOLD}Quick start:${NC}"
echo -e "    ${GREEN}retention scan${NC} https://your-app.com     # QA scan from terminal"
echo -e "    ${GREEN}retention.qa_check${NC}(url='...')             # from Claude Code / Cursor"
echo ""
echo -e "  ${BOLD}Python SDK:${NC}"
echo -e "    from retention import track"
echo -e "    track()  # auto-detects OpenAI, Anthropic, LangChain, LangGraph, CrewAI"
echo ""
echo -e "  ${DIM}Docs: https://github.com/HomenShum/retention${NC}"
echo ""
