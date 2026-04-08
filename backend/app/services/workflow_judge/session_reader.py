"""
Session Reader — reads real Claude Code session data from disk.

Parses ~/.claude/projects/*/conversations/*.jsonl to extract tool calls
that the workflow judge can score against.

This is what makes the judge real — it reads actual session data,
not synthetic arrays.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Claude Code stores sessions here
_CLAUDE_DIR = Path.home() / ".claude" / "projects"


@dataclass
class SessionSummary:
    """Summary of a Claude Code session's tool calls."""
    session_id: str = ""
    session_path: str = ""
    total_lines: int = 0
    total_tool_calls: int = 0
    tool_distribution: Dict[str, int] = field(default_factory=dict)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    user_messages: List[str] = field(default_factory=list)
    files_touched: List[str] = field(default_factory=list)
    has_web_search: bool = False
    has_preview: bool = False
    has_tests: bool = False
    has_write: bool = False
    first_user_prompt: str = ""
    duration_minutes: float = 0.0


def read_current_session(
    project_path: str = "",
) -> Optional[SessionSummary]:
    """Read the most recent Claude Code session for a project.

    Args:
        project_path: Absolute path to the project directory.
                      If empty, uses the CWD-based claude project dir.
    """
    project_dir = _find_project_dir(project_path)
    if not project_dir:
        return None

    # Find most recently modified .jsonl
    jsonl_files = sorted(
        project_dir.glob("*.jsonl"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    if not jsonl_files:
        logger.warning(f"No session files in {project_dir}")
        return None

    return _parse_session(jsonl_files[0])


def read_session(session_path: str) -> Optional[SessionSummary]:
    """Read a specific session JSONL file."""
    path = Path(session_path)
    if not path.exists():
        return None
    return _parse_session(path)


def list_sessions(
    project_path: str = "",
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """List recent sessions for a project."""
    project_dir = _find_project_dir(project_path)
    if not project_dir:
        return []

    jsonl_files = sorted(
        project_dir.glob("*.jsonl"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )[:limit]

    results = []
    for f in jsonl_files:
        try:
            line_count = sum(1 for _ in open(f))
            results.append({
                "session_id": f.stem,
                "path": str(f),
                "lines": line_count,
                "modified": f.stat().st_mtime,
                "size_kb": round(f.stat().st_size / 1024, 1),
            })
        except Exception:
            continue

    return results


def _find_project_dir(project_path: str) -> Optional[Path]:
    """Find the Claude Code project directory for a given project path."""
    if project_path:
        # Convert absolute path to Claude's directory name format
        # /Users/foo/my-project → -Users-foo-my-project
        slug = project_path.replace("/", "-")
        if slug.startswith("-"):
            candidate = _CLAUDE_DIR / slug
            if candidate.exists():
                return candidate

    # Try CWD
    cwd = os.getcwd()
    slug = cwd.replace("/", "-")
    if slug.startswith("-"):
        candidate = _CLAUDE_DIR / slug
        if candidate.exists():
            return candidate

    # Fallback: find any project dir
    if _CLAUDE_DIR.exists():
        dirs = sorted(
            [d for d in _CLAUDE_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if dirs:
            return dirs[0]

    return None


def _parse_session(path: Path) -> SessionSummary:
    """Parse a JSONL session file and extract tool calls."""
    summary = SessionSummary(
        session_id=path.stem,
        session_path=str(path),
    )

    tool_dist: Dict[str, int] = {}
    tool_calls: List[Dict[str, Any]] = []
    user_msgs: List[str] = []
    files: set = set()
    first_ts = None
    last_ts = None

    try:
        with open(path) as f:
            for line_num, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                summary.total_lines += 1

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type", "")
                timestamp = data.get("timestamp")
                if timestamp:
                    if first_ts is None:
                        first_ts = timestamp
                    last_ts = timestamp

                # Extract user messages
                if msg_type == "user":
                    msg = data.get("message", {})
                    if isinstance(msg, dict):
                        content = msg.get("content", "")
                        if isinstance(content, str) and content:
                            user_msgs.append(content[:500])
                        elif isinstance(content, list):
                            for c in content:
                                if isinstance(c, dict) and c.get("type") == "text":
                                    user_msgs.append(c.get("text", "")[:500])

                # Extract tool calls from assistant messages
                elif msg_type == "assistant":
                    msg = data.get("message", {})
                    for c in msg.get("content", []):
                        if not isinstance(c, dict):
                            continue
                        if c.get("type") == "tool_use":
                            tool_name = c.get("name", "unknown")
                            tool_dist[tool_name] = tool_dist.get(tool_name, 0) + 1
                            summary.total_tool_calls += 1

                            tc = {
                                "tool": tool_name,
                                "name": tool_name,
                                "id": c.get("id", ""),
                                "timestamp": timestamp,
                            }

                            # Extract file paths from input
                            tool_input = c.get("input", {})
                            if isinstance(tool_input, dict):
                                for key in ("file_path", "path", "command", "pattern"):
                                    val = tool_input.get(key, "")
                                    if isinstance(val, str) and "/" in val and len(val) < 300:
                                        files.add(val)
                                tc["input_preview"] = str(tool_input)[:200]

                            tool_calls.append(tc)

                            # Track capabilities
                            if "WebSearch" in tool_name or "web_search" in tool_name:
                                summary.has_web_search = True
                            if "preview" in tool_name.lower():
                                summary.has_preview = True
                            if tool_name == "Bash":
                                cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
                                if any(k in cmd for k in ["test", "pytest", "jest", "lint", "typecheck"]):
                                    summary.has_tests = True
                            if tool_name in ("Write", "Edit"):
                                summary.has_write = True

    except Exception as e:
        logger.error(f"Failed to parse session {path}: {e}")

    summary.tool_distribution = tool_dist
    summary.tool_calls = tool_calls
    summary.user_messages = user_msgs
    summary.files_touched = sorted(files)
    summary.first_user_prompt = user_msgs[0] if user_msgs else ""

    # Duration
    if first_ts and last_ts:
        try:
            from datetime import datetime
            t1 = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            summary.duration_minutes = round((t2 - t1).total_seconds() / 60, 1)
        except Exception:
            pass

    return summary
