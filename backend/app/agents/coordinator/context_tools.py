"""
Context-gathering tools for the Coordinator Agent.

These tools let the coordinator dynamically pull live system state
so it can be self-directing and context-aware without hand-holding.
"""

import json
import logging
import os
import subprocess
from typing import Any, Optional

logger = logging.getLogger(__name__)


from ...services.slack_client import BOT_USER_ID, SlackClient
from ...services.usage_telemetry import summarize_usage


def _parse_workspace_channels(channels: Any) -> list[str]:
    """Normalize channel input into a de-duplicated list."""
    if channels is None:
        raw_items: list[Any] = []
    elif isinstance(channels, str):
        raw_items = channels.split(',')
    elif isinstance(channels, (list, tuple, set)):
        raw_items = list(channels)
    else:
        raw_items = [channels]

    normalized: list[str] = []
    for item in raw_items:
        clean = str(item).strip().lstrip('#')
        if clean and clean not in normalized:
            normalized.append(clean)
    return normalized


def _is_human_message(message: dict) -> bool:
    user = message.get('user', '')
    return bool(user) and not message.get('bot_id') and user != BOT_USER_ID


def _compact_usage_summary(days: int = 1) -> dict:
    summary = summarize_usage(days=max(int(days or 1), 1))
    totals = summary.get('totals', {})
    return {
        'window_days': summary.get('window_days', 1),
        'generated_at': summary.get('generated_at', ''),
        'requests': totals.get('requests', 0),
        'estimated_cost_usd': totals.get('estimated_cost_usd', 0.0),
        'total_tokens': totals.get('total_tokens', 0),
        'failed_requests': totals.get('failed_requests', 0),
        'top_interfaces': summary.get('by_interface', [])[:3],
        'top_operations': summary.get('by_operation', [])[:3],
        'top_models': summary.get('by_model', [])[:3],
    }


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _allowed_workspace_channels() -> list[str]:
    return _parse_workspace_channels(os.environ.get('TA_WORKSPACE_CHANNELS', ''))


def _build_message_preview(message: dict, include_text: bool) -> dict:
    preview = {'ts': message.get('ts', '')}
    if include_text:
        preview['text'] = message.get('text', '')[:220]
    return preview


def _build_thread_summary(parent: dict, replies: list[dict], include_text: bool) -> dict:
    summary = {
        'thread_ts': parent.get('thread_ts') or parent.get('ts', ''),
        'reply_count': max(len(replies) - 1, 0),
    }
    if include_text:
        summary['parent_preview'] = parent.get('text', '')[:180]
        summary['latest_reply'] = replies[-1].get('text', '')[:180] if len(replies) > 1 else ''
    return summary


async def get_workspace_context(
    scope: str = 'overview',
    channels: str = '',
    message_limit: int = 12,
    include_threads: bool = True,
    days: int = 1,
) -> str:
    """Gather Slack workspace + usage telemetry context for operator-style questions.

    Use this when the user asks what changed across the team, what happened in Slack,
    which threads matter, or how the system is being used across the workspace.

    Args:
        scope: One of "overview", "channels", "usage", or "full"
        channels: Comma-separated channel names/IDs to prioritize. Defaults to env or core channels.
        message_limit: Messages per channel to scan (5-30 recommended).
        include_threads: Whether to fetch top active thread context.
        days: Usage telemetry lookback window in days.
    """
    raw_allowed_channels = os.environ.get('TA_WORKSPACE_CHANNELS', '')
    allowed_channels = _parse_workspace_channels(raw_allowed_channels)
    requested_channels = _parse_workspace_channels(channels or raw_allowed_channels)
    permitted_channels = [channel for channel in requested_channels if channel in allowed_channels]
    blocked_channels = [channel for channel in requested_channels if channel not in allowed_channels]
    limit = max(5, min(int(message_limit or 12), 30))
    scope = (scope or 'overview').lower()
    workspace_context_enabled = _env_flag('TA_WORKSPACE_CONTEXT_ENABLED', False)
    include_text = _env_flag('TA_WORKSPACE_ALLOW_MESSAGE_TEXT', False) and scope == 'full'

    ctx: dict[str, Any] = {
        'scope': scope,
        'slack_available': bool(os.environ.get('SLACK_BOT_TOKEN')),
        'workspace_context_enabled': workspace_context_enabled,
        'channels_requested': requested_channels[:5],
        'channels_allowed': permitted_channels[:5],
        'message_text_included': include_text,
    }
    if blocked_channels:
        ctx['channels_blocked'] = blocked_channels[:5]

    if scope in ('overview', 'usage', 'full'):
        ctx['usage'] = _compact_usage_summary(days=days)

    if scope == 'usage':
        return json.dumps(ctx, indent=2)

    if not workspace_context_enabled:
        ctx['slack_status'] = 'disabled'
        ctx['channel_summaries'] = []
        return json.dumps(ctx, indent=2)

    if not allowed_channels:
        ctx['slack_status'] = 'no_allowed_channels'
        ctx['channel_summaries'] = []
        return json.dumps(ctx, indent=2)

    if not permitted_channels:
        ctx['slack_status'] = 'no_permitted_channels'
        ctx['channel_summaries'] = []
        return json.dumps(ctx, indent=2)

    if not ctx['slack_available']:
        ctx['slack_status'] = 'unavailable'
        ctx['channel_summaries'] = []
        return json.dumps(ctx, indent=2)

    slack = SlackClient()
    try:
        channel_summaries = []
        for channel in permitted_channels[:3]:
            try:
                history = await slack.get_channel_history(channel, limit=limit)
                human_messages = [msg for msg in history if _is_human_message(msg)]
                bot_messages = [msg for msg in history if not _is_human_message(msg)]
                thread_candidates = [
                    msg for msg in history
                    if int(msg.get('reply_count', 0) or 0) > 0
                ]

                thread_summaries = []
                if include_threads and scope in ('overview', 'full'):
                    for parent in thread_candidates[:2]:
                        thread_ts = parent.get('thread_ts') or parent.get('ts', '')
                        if not thread_ts:
                            continue
                        replies = await slack.get_thread(channel, thread_ts)
                        thread_summaries.append(_build_thread_summary(parent, replies, include_text=include_text))

                channel_summaries.append({
                    'channel': channel,
                    'messages_scanned': len(history),
                    'human_messages': len(human_messages),
                    'bot_messages': len(bot_messages),
                    'active_threads': len(thread_candidates),
                    'latest_human_messages': [
                        _build_message_preview(msg, include_text=include_text)
                        for msg in human_messages[:3]
                    ],
                    'interesting_threads': thread_summaries,
                })
            except Exception as exc:
                channel_summaries.append({
                    'channel': channel,
                    'error': str(exc)[:200],
                })

        ctx['channel_summaries'] = channel_summaries
        return json.dumps(ctx, indent=2)
    finally:
        await slack.close()


async def get_app_context(
    scope: str = "overview",
) -> str:
    """Gather live system state so you can understand what's happening right now.

    Call this BEFORE answering user questions so your responses reflect reality.
    Call this proactively when starting a new conversation or when context seems stale.

    Args:
        scope: What to check. One of:
            - "overview" — Quick health + device count + active sessions (default, fast)
            - "devices" — Detailed device/emulator inventory
            - "pipelines" — QA pipeline run history and status
            - "benchmarks" — Latest benchmark results and scores
            - "sessions" — Active agent chat sessions
            - "full" — Everything (slower, use sparingly)
    """
    import httpx

    base = os.environ.get("TA_BACKEND_URL", "http://localhost:8000")
    ctx: dict = {}

    async def safe_get(path: str) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{base}{path}")
                if r.status_code == 200:
                    return r.json()
        except Exception as e:
            logger.debug(f"Context fetch failed for {path}: {e}")
        return None

    # Always include health
    health = await safe_get("/api/health")
    ctx["backend"] = "online" if health else "offline"

    if scope in ("overview", "full"):
        # Device count
        devices = await safe_get("/api/devices/available")
        if devices and isinstance(devices, list):
            online = [d for d in devices if d.get("status") == "device"]
            ctx["devices"] = {
                "total": len(devices),
                "online": len(online),
                "device_ids": [d.get("device_id") for d in online[:5]],
            }
        else:
            ctx["devices"] = {"total": 0, "online": 0, "device_ids": []}

        # Active agent sessions
        sessions = await safe_get("/api/ai-agent/sessions")
        if sessions and isinstance(sessions, list):
            running = [s for s in sessions if s.get("status") == "running"]
            ctx["agent_sessions"] = {
                "total": len(sessions),
                "running": len(running),
                "recent": [
                    {"id": s.get("session_id", "?"), "status": s.get("status")}
                    for s in sessions[:3]
                ],
            }
        else:
            ctx["agent_sessions"] = {"total": 0, "running": 0}

    if scope in ("devices", "full"):
        devices = await safe_get("/api/devices/available")
        if devices and isinstance(devices, list):
            ctx["devices_detail"] = [
                {
                    "id": d.get("device_id"),
                    "type": d.get("device_type"),
                    "status": d.get("status"),
                    "model": d.get("model"),
                    "leased": d.get("leased", False),
                }
                for d in devices
            ]
        # Check AVDs
        avds = await safe_get("/api/device-simulation/emulators/avds")
        if avds:
            ctx["available_avds"] = avds

    if scope in ("pipelines", "full"):
        results = await safe_get("/api/demo/pipeline-results")
        if results and isinstance(results, list):
            ctx["pipeline_runs"] = [
                {
                    "run_id": r.get("run_id"),
                    "app": r.get("app_id"),
                    "status": r.get("status"),
                    "stages_completed": r.get("stages_completed"),
                }
                for r in results[:5]
            ]
        else:
            ctx["pipeline_runs"] = []

    if scope in ("benchmarks", "full"):
        runs = await safe_get("/benchmarks/comparison/runs")
        if runs and isinstance(runs, list):
            ctx["benchmark_runs"] = [
                {
                    "suite_id": r.get("suite_id"),
                    "status": r.get("status"),
                    "success_rate": r.get("success_rate"),
                }
                for r in runs[:3]
            ]
        else:
            ctx["benchmark_runs"] = []

    if scope in ("sessions", "full"):
        sessions = await safe_get("/api/ai-agent/sessions")
        if sessions and isinstance(sessions, list):
            ctx["sessions_detail"] = [
                {
                    "id": s.get("session_id"),
                    "status": s.get("status"),
                    "steps": s.get("step_count", 0),
                    "tokens": s.get("total_tokens", 0),
                }
                for s in sessions[:10]
            ]

    # Setup readiness
    if scope in ("overview", "full"):
        setup = await safe_get("/api/setup/status")
        if setup:
            ctx["setup_ready"] = setup.get("ready", False)
            ctx["setup_progress"] = setup.get("progress", 0)

    return json.dumps(ctx, indent=2)


async def navigate_user(page: str, reason: str = "") -> str:
    """Tell the frontend to navigate the user to a different page.

    Use this when the user asks to go somewhere, or when you determine
    they'd benefit from seeing a specific page based on their question.

    Args:
        page: The route path to navigate to (e.g. "/demo/curated", "/demo/devices")
        reason: Brief explanation of why you're navigating them there
    """
    # This returns a structured response the frontend can intercept
    # via tool_call events in the SSE stream
    return json.dumps({
        "action": "navigate",
        "page": page,
        "reason": reason,
    })


async def suggest_next_actions() -> str:
    """Analyze current system state and suggest what the user should do next.

    Call this when the user seems unsure, asks "what should I do?",
    or when you want to proactively guide them.
    """
    import httpx

    base = os.environ.get("TA_BACKEND_URL", "http://localhost:8000")
    suggestions = []

    async def safe_get(path: str):
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{base}{path}")
                if r.status_code == 200:
                    return r.json()
        except Exception:
            pass
        return None

    # Check devices
    devices = await safe_get("/api/devices/available")
    online_count = 0
    if devices and isinstance(devices, list):
        online_count = len([d for d in devices if d.get("status") == "device"])

    if online_count == 0:
        suggestions.append({
            "priority": "high",
            "action": "Launch emulators",
            "reason": "No devices online. Most features require at least one emulator.",
            "how": "I can launch emulators for you — just say how many.",
        })

    # Check if any pipeline has run
    results = await safe_get("/api/demo/pipeline-results")
    if not results or len(results) == 0:
        suggestions.append({
            "priority": "medium",
            "action": "Run QA Pipeline",
            "reason": "No pipeline runs yet. The QA Pipeline is the core demo flow.",
            "how": "Go to /demo/curated and pick an app, or ask me to run it.",
        })

    # Check benchmark status
    runs = await safe_get("/benchmarks/comparison/runs")
    if not runs or len(runs) == 0:
        suggestions.append({
            "priority": "low",
            "action": "Run benchmarks",
            "reason": "No benchmark data. Benchmarks show TA value vs baseline.",
            "how": "Go to /demo/benchmarks and click Run Full Benchmark.",
        })

    if online_count > 0:
        suggestions.append({
            "priority": "medium",
            "action": "Try autonomous testing",
            "reason": f"{online_count} device(s) ready. You can run golden bugs or explore an app.",
            "how": "Ask me to 'run all golden bugs' or 'explore Instagram'.",
        })

    if not suggestions:
        suggestions.append({
            "priority": "info",
            "action": "System looks good",
            "reason": "Devices online, pipelines have run. You're set up.",
            "how": "Try asking me about specific test results or to run a new test.",
        })

    return json.dumps(suggestions, indent=2)


def create_context_tools() -> dict:
    """Return context-gathering tools for the coordinator."""
    return {
        "get_app_context": get_app_context,
        "get_workspace_context": get_workspace_context,
        "navigate_user": navigate_user,
        "suggest_next_actions": suggest_next_actions,
    }
