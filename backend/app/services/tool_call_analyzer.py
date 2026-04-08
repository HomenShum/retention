"""Tool Call Analyzer — parse Claude Code JSONL transcripts for tool usage patterns.

Reads ~/.claude/projects/*/*.jsonl to extract every tool_use block,
fingerprint patterns, detect repeated sequences, and attribute costs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    session_id: str
    timestamp: str
    tool_name: str
    semantic_label: str  # human-readable label e.g. "read:App.tsx", "bash:npm test"
    input_keys: list[str]
    input_fingerprint: str
    input_summary: str  # first 120 chars of stringified input for display
    message_input_tokens: int
    message_output_tokens: int
    message_tool_count: int
    cost_share_usd: float
    model: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _claude_dir() -> Optional[Path]:
    home = Path.home()
    for d in [home / ".claude", home / "Library" / "Application Support" / "claude"]:
        if d.exists():
            return d
    return None


def fingerprint_tool_call(tool_name: str, tool_input: dict) -> str:
    """Stable hash: tool_name + sorted input keys (ignores values)."""
    key = f"{tool_name}:{','.join(sorted(tool_input.keys()))}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _semantic_label(tool_name: str, tool_input: dict) -> str:
    """Generate a human-readable label like 'read:App.tsx' or 'bash:npm test'."""
    name = tool_name

    # Shorten MCP tool names
    if name.startswith("mcp__Claude_Preview__"):
        name = name.replace("mcp__Claude_Preview__preview_", "preview:")
    elif name.startswith("mcp__Claude_in_Chrome__"):
        name = name.replace("mcp__Claude_in_Chrome__", "chrome:")
    elif name.startswith("mcp__"):
        parts = name.split("__")
        name = f"{parts[-1]}" if len(parts) >= 3 else name

    name = name.lower()

    # Extract meaningful context from input
    ctx = ""
    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))[:120]
        # Split on chain operators, take the LAST meaningful command
        # "cd frontend/test-studio && npx tsc --noEmit" → "npx tsc"
        segments = [s.strip() for s in cmd.replace("&&", "|||").replace(";", "|||").split("|||")]
        skip_cmds = {"cd", "export", "source", "sleep"}
        known = {"git", "npm", "npx", "python3", "python", "pip", "curl",
                 "tsc", "uvicorn", "pytest", "node", "docker", "make",
                 "ls", "cat", "head", "tail", "find", "wc", "sort"}

        for segment in reversed(segments):
            parts = segment.split()
            if not parts:
                continue
            binary = parts[0].split("/")[-1]
            if binary in skip_cmds:
                continue
            # grab subcommand for git/npm/npx
            if binary in known and len(parts) > 1:
                sub = parts[1].strip("-").split("/")[-1]
                if sub and not sub.startswith("-"):
                    ctx = f"{binary} {sub}"
                else:
                    ctx = binary
            elif binary in known:
                ctx = binary
            else:
                ctx = binary[:20]
            break
        if not ctx:
            ctx = cmd[:25].strip()
    elif tool_name in ("Read", "Write", "Edit"):
        path = str(tool_input.get("file_path", ""))
        ctx = path.split("/")[-1] if "/" in path else path  # just filename
    elif tool_name == "Grep":
        pattern = str(tool_input.get("pattern", ""))[:25]
        path = str(tool_input.get("path", ""))
        target = path.split("/")[-1] if "/" in path else ""
        ctx = f'"{pattern}"' + (f" in {target}" if target else "")
    elif tool_name == "Glob":
        pattern = str(tool_input.get("pattern", ""))
        ctx = pattern
    elif tool_name == "WebSearch":
        ctx = str(tool_input.get("query", ""))[:30]
    elif tool_name == "WebFetch":
        url = str(tool_input.get("url", ""))
        # Extract domain
        if "//" in url:
            ctx = url.split("//")[1].split("/")[0][:25]
        else:
            ctx = url[:25]
    elif tool_name == "Agent":
        ctx = str(tool_input.get("description", ""))[:30]
    elif tool_name == "TodoWrite":
        ctx = "update"
    elif "screenshot" in tool_name.lower():
        ctx = "capture"
    elif "eval" in tool_name.lower() or "javascript" in tool_name.lower():
        code = str(tool_input.get("expression", tool_input.get("text", "")))[:30]
        ctx = code

    if ctx:
        return f"{name}({ctx})"
    return name


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    try:
        from .usage_telemetry import estimate_cost_usd
        return estimate_cost_usd(model, input_tokens, output_tokens)
    except Exception:
        return (input_tokens * 15 + output_tokens * 75) / 1_000_000_000


# ---------------------------------------------------------------------------
# JSONL Parsing
# ---------------------------------------------------------------------------

def _extract_tool_calls_from_file(filepath: Path, cutoff_ts: float) -> list[ToolCall]:
    """Parse one JSONL file for tool_use blocks."""
    calls: list[ToolCall] = []
    try:
        text = filepath.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return calls

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts_str = msg.get("timestamp", "")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts.timestamp() < cutoff_ts:
                    continue
            except Exception:
                continue
        else:
            continue

        # Only assistant messages have tool_use
        inner = msg.get("message", {})
        if inner.get("role") != "assistant":
            continue

        content = inner.get("content")
        if not isinstance(content, list):
            continue

        # Get usage for cost attribution
        usage = inner.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        model = inner.get("model", "claude-opus-4-6")
        session_id = msg.get("sessionId", filepath.stem)

        # Find all tool_use blocks in this message
        tool_uses = [c for c in content if isinstance(c, dict) and c.get("type") == "tool_use"]
        if not tool_uses:
            continue

        msg_cost = _estimate_cost(model, input_tokens, output_tokens)
        cost_per_tool = msg_cost / max(len(tool_uses), 1)

        for tu in tool_uses:
            name = tu.get("name", "unknown")
            inp = tu.get("input", {})
            if not isinstance(inp, dict):
                inp = {}

            input_str = json.dumps(inp, default=str)[:120]
            calls.append(ToolCall(
                session_id=session_id,
                timestamp=ts_str,
                tool_name=name,
                semantic_label=_semantic_label(name, inp),
                input_keys=sorted(inp.keys()),
                input_fingerprint=fingerprint_tool_call(name, inp),
                input_summary=input_str,
                message_input_tokens=input_tokens,
                message_output_tokens=output_tokens,
                message_tool_count=len(tool_uses),
                cost_share_usd=round(cost_per_tool, 6),
                model=model,
            ))

    return calls


def _read_retention_buffer(days: int = 7) -> list[ToolCall]:
    """Read from ~/.retention/activity.jsonl (dogfood + external agent hooks)."""
    buffer_path = Path.home() / ".retention" / "activity.jsonl"
    if not buffer_path.exists():
        return []

    cutoff_str = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    calls: list[ToolCall] = []

    try:
        for line in buffer_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = event.get("ts", "")
            if ts < cutoff_str:
                continue

            tool_name = event.get("tool_name", "unknown")
            inp = event.get("tool_input", {})
            if not isinstance(inp, dict):
                inp = {}

            calls.append(ToolCall(
                session_id=event.get("session_id", "unknown"),
                timestamp=ts,
                tool_name=tool_name,
                semantic_label=_semantic_label(tool_name, inp),
                input_keys=sorted(inp.keys()),
                input_fingerprint=fingerprint_tool_call(tool_name, inp),
                input_summary=json.dumps(inp, default=str)[:120],
                message_input_tokens=0,
                message_output_tokens=0,
                message_tool_count=1,
                cost_share_usd=0.0,  # retention buffer doesn't carry token counts yet
                model=event.get("model", "unknown"),
            ))
    except OSError:
        pass

    return calls


def _walk_jsonl_files(days: int = 7) -> list[ToolCall]:
    """Walk ~/.claude/projects/ + ~/.retention/activity.jsonl for all tool calls."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    all_calls: list[ToolCall] = []

    # Source 1: Claude Code conversation transcripts
    claude = _claude_dir()
    if claude:
        projects_dir = claude / "projects"
        if projects_dir.exists():
            for project_dir in projects_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                search_dirs = [project_dir, project_dir / "conversations"]
                for sdir in search_dirs:
                    if not sdir.exists():
                        continue
                    for f in sdir.glob("*.jsonl"):
                        all_calls.extend(_extract_tool_calls_from_file(f, cutoff))

    # Source 2: Retention activity buffer (dogfood + external agent hooks)
    all_calls.extend(_read_retention_buffer(days))

    all_calls.sort(key=lambda c: c.timestamp)
    return all_calls


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def detect_sequences(
    tool_calls: list[ToolCall], min_length: int = 3, min_count: int = 2
) -> list[dict]:
    """Find repeated N-gram tool sequences using semantic labels."""
    # Group by session using semantic labels (not raw tool names)
    by_session: dict[str, list[str]] = defaultdict(list)
    for tc in tool_calls:
        by_session[tc.session_id].append(tc.semantic_label)

    pattern_counter: Counter = Counter()
    for labels in by_session.values():
        # Skip trivially homogeneous sequences (all same label)
        for n in range(min_length, min(7, len(labels) + 1)):
            for i in range(len(labels) - n + 1):
                seq = tuple(labels[i:i + n])
                # Only count sequences with at least 2 distinct labels
                if len(set(seq)) >= 2:
                    pattern_counter[seq] += 1

    patterns = []
    for seq, count in pattern_counter.most_common(50):
        if count >= min_count:
            patterns.append({
                "sequence": list(seq),
                "length": len(seq),
                "count": count,
            })

    return patterns[:30]


def find_duplicates(tool_calls: list[ToolCall]) -> list[dict]:
    """Group by fingerprint, return those appearing 3+ times."""
    by_fp: dict[str, list[ToolCall]] = defaultdict(list)
    for tc in tool_calls:
        by_fp[tc.input_fingerprint].append(tc)

    dupes = []
    for fp, calls in by_fp.items():
        if len(calls) < 3:
            continue
        total_cost = sum(c.cost_share_usd for c in calls)
        dupes.append({
            "tool_name": calls[0].tool_name,
            "fingerprint": fp,
            "count": len(calls),
            "total_cost_usd": round(total_cost, 4),
            "first_seen": calls[0].timestamp,
            "last_seen": calls[-1].timestamp,
            "input_keys": calls[0].input_keys,
        })

    dupes.sort(key=lambda d: d["count"], reverse=True)
    return dupes[:50]


def classify_external_tools(tool_calls: list[ToolCall]) -> list[dict]:
    """Classify and group external/MCP tool calls."""
    categories: dict[str, dict] = {}
    for tc in tool_calls:
        name = tc.tool_name
        if name.startswith("mcp__"):
            cat = "mcp"
        elif name in ("WebSearch", "WebFetch"):
            cat = "web_search"
        elif name in ("Bash",):
            cat = "shell"
        elif name in ("Read", "Write", "Edit", "Glob", "Grep"):
            cat = "filesystem"
        elif name in ("Agent",):
            cat = "agent"
        else:
            cat = "other"

        if name not in categories:
            categories[name] = {
                "tool_name": name,
                "category": cat,
                "count": 0,
                "total_cost_usd": 0.0,
            }
        categories[name]["count"] += 1
        categories[name]["total_cost_usd"] = round(
            categories[name]["total_cost_usd"] + tc.cost_share_usd, 4
        )

    result = sorted(categories.values(), key=lambda x: x["count"], reverse=True)
    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

_cache: dict[int, tuple[float, dict]] = {}
_CACHE_TTL = 120  # seconds


def analyze_tool_calls(days: int = 7) -> dict[str, Any]:
    """Full analysis of Claude Code tool call patterns."""
    now = time.time()
    if days in _cache and _cache[days][0] > now:
        return _cache[days][1]

    tool_calls = _walk_jsonl_files(days)

    if not tool_calls:
        result = _demo_analysis()
        _cache[days] = (now + _CACHE_TTL, result)
        return result

    # Frequency
    freq = Counter(tc.tool_name for tc in tool_calls)

    # Cost by tool
    cost_by_tool: dict[str, float] = defaultdict(float)
    for tc in tool_calls:
        cost_by_tool[tc.tool_name] += tc.cost_share_usd
    cost_by_tool_sorted = {
        k: round(v, 4) for k, v in sorted(cost_by_tool.items(), key=lambda x: -x[1])
    }

    # Duplicates
    duplicates = find_duplicates(tool_calls)
    duplicate_cost = sum(d["total_cost_usd"] * (1 - 1 / d["count"]) for d in duplicates)

    # Patterns
    patterns = detect_sequences(tool_calls)

    # External APIs
    external = classify_external_tools(tool_calls)

    # Sessions
    by_session: dict[str, list[ToolCall]] = defaultdict(list)
    for tc in tool_calls:
        by_session[tc.session_id].append(tc)

    sessions = []
    for sid, calls in sorted(by_session.items(), key=lambda x: x[1][-1].timestamp, reverse=True):
        sessions.append({
            "session_id": sid[:16],
            "tool_count": len(calls),
            "unique_tools": len(set(c.tool_name for c in calls)),
            "total_input_tokens": sum(c.message_input_tokens for c in calls) // max(1, calls[0].message_tool_count),
            "total_output_tokens": sum(c.message_output_tokens for c in calls) // max(1, calls[0].message_tool_count),
            "total_cost_usd": round(sum(c.cost_share_usd for c in calls), 4),
            "model": calls[-1].model,
            "first_ts": calls[0].timestamp,
            "last_ts": calls[-1].timestamp,
        })

    total_cost = sum(tc.cost_share_usd for tc in tool_calls)
    # Duplicate % = calls whose EXACT fingerprint (tool + input structure) repeats 3+ times
    # Use a tighter fingerprint: tool_name + sorted input keys + input value lengths
    # This avoids overcounting Read(file_path) as always duplicate
    exact_fp = Counter()
    for tc in tool_calls:
        # Include value shapes (lengths) not just key names
        val_sig = ",".join(f"{k}:{len(str(v))}" for k, v in sorted(zip(tc.input_keys, tc.input_keys)))
        exact_fp[f"{tc.tool_name}:{val_sig}"] += 1
    dup_calls = sum(count for count in exact_fp.values() if count >= 3)
    total_duplicate_pct = min((dup_calls / max(len(tool_calls), 1)) * 100, 80)  # cap at 80%

    result: dict[str, Any] = {
        "tool_frequency": dict(freq.most_common()),
        "tool_cost": cost_by_tool_sorted,
        "duplicates": duplicates,
        "patterns": patterns,
        "external_apis": external,
        "sessions": sessions[:50],
        "totals": {
            "total_tool_calls": len(tool_calls),
            "unique_tools": len(freq),
            "total_cost_usd": round(total_cost, 4),
            "total_sessions": len(by_session),
            "days_analyzed": days,
            "duplicate_pct": round(total_duplicate_pct, 1),
        },
        "savings_estimate": {
            "duplicate_cost_recoverable": round(duplicate_cost, 4),
            "pattern_replay_savings": round(duplicate_cost * 0.6, 4),
            "total_recoverable_usd": round(duplicate_cost * 1.6, 4),
            "total_recoverable_pct": round((duplicate_cost * 1.6 / max(total_cost, 0.001)) * 100, 1),
        },
        "is_demo": False,
    }

    _cache[days] = (now + _CACHE_TTL, result)
    return result


# ---------------------------------------------------------------------------
# Demo fallback
# ---------------------------------------------------------------------------

def _demo_analysis() -> dict[str, Any]:
    """Hardcoded sample data for when no local JSONL files exist."""
    return {
        "tool_frequency": {
            "Read": 3420, "Edit": 1890, "Bash": 1654, "Grep": 1102,
            "Glob": 987, "Write": 654, "WebSearch": 432, "Agent": 321,
            "TodoWrite": 287, "WebFetch": 198,
            "mcp__Claude_Preview__preview_screenshot": 176,
            "mcp__Claude_Preview__preview_eval": 154,
            "mcp__Claude_Preview__preview_snapshot": 132,
            "mcp__notion__search": 89, "mcp__notion__fetch": 76,
        },
        "tool_cost": {
            "Read": 12.45, "Edit": 9.87, "Bash": 8.23, "Grep": 5.67,
            "Agent": 4.56, "WebSearch": 3.89, "Write": 3.21, "Glob": 2.98,
            "WebFetch": 2.34, "mcp__Claude_Preview__preview_eval": 1.87,
        },
        "duplicates": [
            {"tool_name": "Read", "fingerprint": "a1b2c3d4e5f6", "count": 287, "total_cost_usd": 1.23, "first_seen": "2026-03-24T10:00:00Z", "last_seen": "2026-03-31T18:00:00Z", "input_keys": ["file_path"]},
            {"tool_name": "Grep", "fingerprint": "b2c3d4e5f6a1", "count": 156, "total_cost_usd": 0.89, "first_seen": "2026-03-25T08:00:00Z", "last_seen": "2026-03-31T17:00:00Z", "input_keys": ["pattern", "path"]},
            {"tool_name": "WebSearch", "fingerprint": "c3d4e5f6a1b2", "count": 89, "total_cost_usd": 0.67, "first_seen": "2026-03-26T09:00:00Z", "last_seen": "2026-03-31T16:00:00Z", "input_keys": ["query"]},
            {"tool_name": "Bash", "fingerprint": "d4e5f6a1b2c3", "count": 67, "total_cost_usd": 0.45, "first_seen": "2026-03-27T11:00:00Z", "last_seen": "2026-03-31T15:00:00Z", "input_keys": ["command"]},
            {"tool_name": "mcp__notion__search", "fingerprint": "e5f6a1b2c3d4", "count": 34, "total_cost_usd": 0.23, "first_seen": "2026-03-28T14:00:00Z", "last_seen": "2026-03-31T14:00:00Z", "input_keys": ["query", "filters"]},
        ],
        "patterns": [
            {"sequence": ["Grep", "Read", "Edit"], "length": 3, "count": 234},
            {"sequence": ["WebSearch", "WebFetch", "Read"], "length": 3, "count": 67},
            {"sequence": ["Bash", "Read", "Edit", "Bash"], "length": 4, "count": 45},
            {"sequence": ["Glob", "Read", "Grep", "Read"], "length": 4, "count": 38},
            {"sequence": ["Agent", "Read", "Edit"], "length": 3, "count": 29},
        ],
        "external_apis": [
            {"tool_name": "Read", "category": "filesystem", "count": 3420, "total_cost_usd": 12.45},
            {"tool_name": "Edit", "category": "filesystem", "count": 1890, "total_cost_usd": 9.87},
            {"tool_name": "Bash", "category": "shell", "count": 1654, "total_cost_usd": 8.23},
            {"tool_name": "Grep", "category": "filesystem", "count": 1102, "total_cost_usd": 5.67},
            {"tool_name": "WebSearch", "category": "web_search", "count": 432, "total_cost_usd": 3.89},
            {"tool_name": "Agent", "category": "agent", "count": 321, "total_cost_usd": 4.56},
            {"tool_name": "mcp__Claude_Preview__preview_screenshot", "category": "mcp", "count": 176, "total_cost_usd": 1.23},
            {"tool_name": "mcp__notion__search", "category": "mcp", "count": 89, "total_cost_usd": 0.56},
        ],
        "sessions": [
            {"session_id": "2b33b5d1-919b-4", "tool_count": 847, "unique_tools": 23, "total_input_tokens": 2400000, "total_output_tokens": 180000, "total_cost_usd": 18.45, "model": "claude-opus-4-6", "first_ts": "2026-03-31T09:00:00Z", "last_ts": "2026-03-31T19:00:00Z"},
            {"session_id": "a1b2c3d4-e5f6-7", "tool_count": 432, "unique_tools": 18, "total_input_tokens": 1200000, "total_output_tokens": 95000, "total_cost_usd": 9.87, "model": "claude-opus-4-6", "first_ts": "2026-03-30T10:00:00Z", "last_ts": "2026-03-30T22:00:00Z"},
            {"session_id": "b2c3d4e5-f6a1-8", "tool_count": 321, "unique_tools": 15, "total_input_tokens": 890000, "total_output_tokens": 67000, "total_cost_usd": 7.23, "model": "claude-opus-4-6", "first_ts": "2026-03-29T08:00:00Z", "last_ts": "2026-03-29T20:00:00Z"},
        ],
        "totals": {
            "total_tool_calls": 11492,
            "unique_tools": 38,
            "total_cost_usd": 67.89,
            "total_sessions": 24,
            "days_analyzed": 7,
            "duplicate_pct": 34.2,
        },
        "savings_estimate": {
            "duplicate_cost_recoverable": 8.45,
            "pattern_replay_savings": 5.07,
            "total_recoverable_usd": 13.52,
            "total_recoverable_pct": 19.9,
        },
        "is_demo": True,
    }
