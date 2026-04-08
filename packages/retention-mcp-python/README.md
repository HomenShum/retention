# retention-mcp

Thin local relay for retention.sh. Connects your machine to the retention.sh server via outbound WebSocket.

## Quick Start

Add to your Claude Code `.mcp.json`:

```json
{
  "mcpServers": {
    "retention": {
      "command": "npx",
      "args": ["retention-mcp@latest"],
      "env": {
        "TA_API_KEY": "sk-your-key"
      }
    }
  }
}
```

## Requirements

- Python 3.10+
- `websockets` pip package (`pip install websockets`)
- Android SDK with ADB (for emulator control)
