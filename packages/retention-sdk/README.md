# retention

The always-on judge for AI agents. One-line telemetry for any provider.

## Quick start

```python
from retention import track
track()  # Auto-detects installed providers, patches them silently
```

## Supported providers

| Provider | Auto-detected | What's captured |
|----------|--------------|-----------------|
| OpenAI | Yes | Tool calls from chat completions |
| Anthropic | Yes | Tool use blocks from messages |
| LangChain | Yes | Tool start/end via callbacks |
| CrewAI | Yes | Tool execution via BaseTool |
| OpenAI Agents SDK | Yes | Traces and spans |
| Claude Agent SDK | Yes | Tool execution lifecycle |
| Generic | Manual | Any tool call via `track_event()` |

## Configuration

```python
from retention import configure, track

configure(
    providers=["openai", "anthropic"],  # Only patch these (default: all)
    scrub=True,                          # Redact secrets (default: True)
    log_path="./my-telemetry.jsonl",     # Custom log path
)
track()
```

## Manual tracking

```python
from retention.wrappers.generic import track_event
track_event("my_custom_tool", {"query": "hello", "limit": 10})
```

## Privacy

All inputs are scrubbed by default. API keys, tokens, secrets, and long values
are redacted before storage. File paths are anonymized to extensions only.

## License

MIT
