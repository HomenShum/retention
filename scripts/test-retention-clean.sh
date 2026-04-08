#!/usr/bin/env bash
# Clean-room test for retention.sh installer
# Simulates a brand-new user with no prior retention.sh state

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_INSTALL="$REPO_ROOT/frontend/test-studio/public/install.sh"

# By default test the live URL; pass --local to test the local file
TARGET="remote"
INSTALL_SH_CMD='curl -sL https://retention.sh/install.sh | bash'
if [ "${1:-}" = "--local" ]; then
  TARGET="local"
  INSTALL_SH_CMD="bash $LOCAL_INSTALL"
fi

CLEAN_HOME=$(mktemp -d)
CLEAN_WORKDIR=$(mktemp -d)
trap 'rm -rf "$CLEAN_HOME" "$CLEAN_WORKDIR"' EXIT

echo "=== Clean-room retention.sh test ($TARGET) ==="
echo "Fake HOME : $CLEAN_HOME"
echo "Fake CWD  : $CLEAN_WORKDIR"
echo ""

cd "$CLEAN_WORKDIR"

# Run installer with isolated HOME and pre-set email (non-interactive)
INSTALL_OUT=""
INSTALL_EXIT=0
INSTALL_OUT=$(HOME="$CLEAN_HOME" RETENTION_EMAIL="cleantest@example.com" \
  bash -c "$INSTALL_SH_CMD" 2>&1) || INSTALL_EXIT=$?

echo "=== Installer output ==="
echo "$INSTALL_OUT"
echo ""
echo "=== Exit code: $INSTALL_EXIT ==="
echo ""

echo "=== .mcp.json in CWD ==="
if [ -f "$CLEAN_WORKDIR/.mcp.json" ]; then
  cat "$CLEAN_WORKDIR/.mcp.json"
else
  echo "MISSING — installer did not write .mcp.json"
fi
echo ""

echo "=== .retention/proxy.py ==="
if [ -f "$CLEAN_HOME/.retention/proxy.py" ]; then
  head -5 "$CLEAN_HOME/.retention/proxy.py"
  echo "  ($(wc -l < "$CLEAN_HOME/.retention/proxy.py") lines total)"
else
  echo "MISSING — proxy not downloaded"
fi
echo ""

echo "=== VERDICT ==="
PASS=true
if [ "$INSTALL_EXIT" -ne 0 ]; then
  echo "FAIL: installer exited $INSTALL_EXIT"
  PASS=false
fi
if [ ! -f "$CLEAN_WORKDIR/.mcp.json" ]; then
  echo "FAIL: .mcp.json not written"
  PASS=false
fi
if [ ! -f "$CLEAN_HOME/.retention/proxy.py" ]; then
  echo "WARN: proxy.py missing — MCP agent will not start"
  PASS=false
fi
if [ "$PASS" = "true" ]; then
  echo "PASS: clean-room install succeeded"
fi

