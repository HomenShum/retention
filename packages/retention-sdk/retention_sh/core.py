"""Core retention.sh SDK — logging, configuration, and auto-patching."""

from __future__ import annotations

import functools
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("retention_sh")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class RetentionConfig:
    """SDK configuration. Set via configure() or environment variables."""

    # Where to write the local JSONL activity buffer
    buffer_path: str = ""

    # Optional: POST events to a remote retention.sh server
    server_url: str = ""

    # Project identifier (auto-detected from git or cwd)
    project: str = ""

    # Session identifier (auto-generated if empty)
    session_id: str = ""

    # Which providers to auto-patch when track() is called
    auto_patch: list[str] = field(default_factory=lambda: [
        "openai", "anthropic", "langchain", "crewai",
        "openai_agents", "claude_agent_sdk", "pydantic_ai",
    ])

    # Set to False to disable logging (useful in tests)
    enabled: bool = True

    # Maximum input/output preview length saved per event
    max_preview: int = 200

    def __post_init__(self):
        if not self.buffer_path:
            self.buffer_path = os.environ.get(
                "RETENTION_BUFFER",
                str(Path.home() / ".retention" / "activity.jsonl"),
            )
        if not self.server_url:
            self.server_url = os.environ.get("RETENTION_SERVER", "")
        if not self.project:
            self.project = os.environ.get("RETENTION_PROJECT", _detect_project())
        if not self.session_id:
            self.session_id = os.environ.get(
                "RETENTION_SESSION",
                f"sdk-{int(time.time())}-{os.getpid()}",
            )


_config: Optional[RetentionConfig] = None


def _get_config() -> RetentionConfig:
    global _config
    if _config is None:
        _config = RetentionConfig()
    return _config


def configure(**kwargs: Any) -> RetentionConfig:
    """Configure the retention.sh SDK.

    Can be called before or after track(). All parameters are optional.

    Example:
        configure(project="founder-intel", server_url="http://localhost:8000")
    """
    global _config
    _config = RetentionConfig(**kwargs)
    return _config


def _detect_project() -> str:
    """Try to detect project name from git or cwd."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).name
    except Exception:
        pass
    return Path.cwd().name


# ---------------------------------------------------------------------------
# Event logging
# ---------------------------------------------------------------------------

def log_tool_call(
    *,
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
    tool_output: str | None = None,
    status: str = "ok",
    duration_ms: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    model: str = "",
    cost_usd: float = 0.0,
    source: str = "sdk",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Log a single tool call event to the retention.sh buffer.

    This is the low-level API. Most users should use observe() or track() instead.

    Returns the event dict that was written.
    """
    cfg = _get_config()
    if not cfg.enabled:
        return {}

    # Build raw event
    raw_input = {}
    if tool_input:
        for k, v in tool_input.items():
            raw_input[k] = str(v)[:cfg.max_preview]

    event: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "project": cfg.project,
        "session_id": cfg.session_id,
        "tool_name": tool_name,
        "tool_input": raw_input,
        "status": status,
        "duration_ms": duration_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "model": model,
        "cost_usd": cost_usd,
    }
    if metadata:
        event["metadata"] = metadata

    # Privacy scrubbing — log structure, not secrets
    from .scrub import scrub_event
    scrub_event(event)

    # First-run consent notice
    _maybe_show_consent()

    # Write to local buffer
    _write_buffer(event, cfg)

    # POST to server if configured
    if cfg.server_url:
        _post_event(event, cfg)

    return event


_consent_shown = False


def _maybe_show_consent() -> None:
    """Print first-run privacy notice (once per process, once per machine)."""
    global _consent_shown
    if _consent_shown:
        return
    _consent_shown = True

    cfg = _get_config()
    consent_marker = Path(cfg.buffer_path).parent / ".consent"
    if consent_marker.exists():
        return

    # Print notice
    print("\033[36mretention.sh\033[0m: Logging tool names + timing to ~/.retention/activity.jsonl")
    print("\033[36mretention.sh\033[0m: No file contents, API keys, or personal data collected.")
    print("\033[36mretention.sh\033[0m: Run `python -m retention_sh audit` to see what's logged.")

    try:
        consent_marker.parent.mkdir(parents=True, exist_ok=True)
        consent_marker.write_text("consented\n")
    except OSError:
        pass


def _write_buffer(event: dict, cfg: RetentionConfig) -> None:
    """Append event to local JSONL file (non-blocking, never raises)."""
    try:
        path = Path(cfg.buffer_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except Exception as e:
        logger.debug("retention.sh buffer write failed: %s", e)


def _post_event(event: dict, cfg: RetentionConfig) -> None:
    """POST event to remote server (fire-and-forget, never raises)."""
    try:
        import urllib.request
        data = json.dumps(event, default=str).encode()
        req = urllib.request.Request(
            f"{cfg.server_url.rstrip('/')}/api/analytics/ingest",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception as e:
        logger.debug("retention.sh server POST failed: %s", e)


# ---------------------------------------------------------------------------
# observe() decorator
# ---------------------------------------------------------------------------

def observe(
    fn: Optional[Callable] = None,
    *,
    name: str = "",
    source: str = "sdk",
) -> Callable:
    """Decorator/wrapper that logs any function call as a tool call event.

    Usage as decorator:
        @observe(name="search_companies")
        def search(query: str) -> list:
            ...

    Usage as wrapper:
        result = observe(search_fn, name="search")(query="AI startups")
    """
    def decorator(func: Callable) -> Callable:
        label = name or func.__name__

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.time()
            status = "ok"
            error_msg = ""
            result = None
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                error_msg = str(e)[:200]
                raise
            finally:
                duration_ms = int((time.time() - t0) * 1000)
                # Build input from kwargs (more readable than args)
                tool_input = dict(kwargs) if kwargs else {}
                if args:
                    tool_input["_args"] = [str(a)[:100] for a in args[:5]]

                log_tool_call(
                    tool_name=label,
                    tool_input=tool_input,
                    tool_output=str(result)[:200] if result else error_msg,
                    status=status,
                    duration_ms=duration_ms,
                    source=source,
                )

        return wrapper

    if fn is not None:
        return decorator(fn)
    return decorator


# ---------------------------------------------------------------------------
# track() — auto-patch all detected providers
# ---------------------------------------------------------------------------

_tracked = False


def track(**config_kwargs: Any) -> None:
    """Auto-detect and patch all installed AI providers.

    Call once at app startup:

        from retention_sh import track
        track()

    This patches OpenAI, Anthropic, LangChain, CrewAI, etc.
    to automatically log every tool call to ~/.retention/activity.jsonl.

    Pass config kwargs to customize:

        track(project="my-app", server_url="http://localhost:8000")
    """
    global _tracked
    if _tracked:
        return

    if config_kwargs:
        configure(**config_kwargs)

    cfg = _get_config()

    from . import wrappers

    for provider in cfg.auto_patch:
        try:
            patcher = getattr(wrappers, f"patch_{provider}", None)
            if patcher:
                patcher()
                logger.debug("retention.sh: patched %s", provider)
        except ImportError:
            pass  # provider not installed, skip
        except Exception as e:
            logger.debug("retention.sh: failed to patch %s: %s", provider, e)

    _tracked = True
    logger.info(
        "retention.sh: tracking active (project=%s, buffer=%s)",
        cfg.project, cfg.buffer_path,
    )
