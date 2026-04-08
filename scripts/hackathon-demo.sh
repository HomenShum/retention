#!/bin/bash
# ─────────────────────────────────────────────────
# retention.sh — Hackathon Demo Script
# "Show me my agent spend in 30 seconds"
#
# Walk up to any team using Claude Code, run this,
# and show them their OWN data. Not a demo app.
# ─────────────────────────────────────────────────

set -e

RETENTION_CLI_DIR="$(cd "$(dirname "$0")/../packages/retention-cli" && pwd)"

echo ""
echo "  retention.sh — Agent Spend Demo"
echo "  ================================"
echo ""

# Step 1: Check if CLI is built
if [ -f "$RETENTION_CLI_DIR/dist/cli.js" ]; then
  CLI="node $RETENTION_CLI_DIR/dist/cli.js"
elif command -v retention &>/dev/null; then
  CLI="retention"
else
  echo "  Building CLI..."
  cd "$RETENTION_CLI_DIR"
  npm install --silent 2>/dev/null
  npx tsc 2>/dev/null
  CLI="node $RETENTION_CLI_DIR/dist/cli.js"
  echo "  Done."
  echo ""
fi

# Step 2: Run the analysis
$CLI analyze --days 7

# Step 3: Upsell real-time tracking
echo "  ─────────────────────────────────────────"
echo "  Want real-time tracking? Add to .claude/settings.json:"
echo ""
echo '  {                                        '
echo '    "hooks": {                              '
echo '      "PostToolUse": [{                     '
echo '        "command": "retention hook",        '
echo '        "timeout_ms": 5000                  '
echo '      }]                                    '
echo '    }                                       '
echo '  }                                         '
echo ""
echo "  More: https://retention.sh"
echo ""
