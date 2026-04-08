#!/usr/bin/env python3
"""Stream agent SSE events to a Slack thread, updating a single message in real-time.

Usage:
  python3 stream-agent-to-slack.py \
    --backend-url http://localhost:8000 \
    --agent strategy-brief \
    --question "What is our GTM strategy?" \
    --channel C0AM2J4G6S0 \
    --thread-ts 1234567890.123456 \
    --slack-token xoxb-...

Shows clean, manager-friendly progress (not raw JSON) and replaces with
the full formatted answer when complete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
import urllib.error

# ── Context graph hooks (non-critical — failures silenced) ─────────────
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
    from app.services.context_graph import ContextGraph, SlackAgentHooks
    _slack_graph_path = os.path.join(os.path.dirname(__file__), '..', 'backend', 'data', 'context_graphs', 'slack_agent.json')
    try:
        _slack_graph = ContextGraph.load(_slack_graph_path)
    except Exception:
        _slack_graph = ContextGraph()
    _slack_hooks = SlackAgentHooks(_slack_graph)
except Exception:
    _slack_graph = None
    _slack_hooks = None

# ── Tool → plain-English description mapping ────────────────────────────
TOOL_LABELS = {
    "get_state":        "Loading current scenario & assumptions",
    "get_section":      "Reading brief section",
    "set_variables":    "Updating financial assumptions",
    "set_scenario":     "Switching scenario",
    "recalculate":      "Recalculating financials",
    "recent_commits":   "Reviewing recent engineering activity",
    "commit_diff":      "Inspecting code changes",
    "git_status":       "Checking work in progress",
    "search":           "Searching codebase",
    "read_file":        "Reading source code",
    "list_directory":   "Browsing project structure",
    "file_tree":        "Mapping file structure",
    "exec_python":      "Running analysis",
    "shell_command":     "Running shell command",
    "web_search_preview": "Searching the web",
    "slack_search_messages": "Searching Slack messages",
    "slack_get_channel_history": "Reading Slack channel",
    "slack_get_thread":  "Reading Slack thread",
    "slack_list_channels": "Listing Slack channels",
    "request_additional_tools": "Expanding available tools",
}

# Friendly category for grouping
TOOL_CATEGORIES = {
    "get_state": "brief", "get_section": "brief", "set_variables": "brief",
    "set_scenario": "brief", "recalculate": "brief",
    "recent_commits": "codebase", "commit_diff": "codebase",
    "git_status": "codebase", "search": "codebase",
    "read_file": "codebase", "list_directory": "codebase",
    "file_tree": "codebase", "exec_python": "codebase", "shell_command": "codebase",
    "web_search_preview": "web",
    "slack_search_messages": "slack", "slack_get_channel_history": "slack",
    "slack_get_thread": "slack", "slack_list_channels": "slack",
    "request_additional_tools": "system",
}

CATEGORY_ICONS = {
    "brief": "\U0001f4ca",      # 📊
    "codebase": "\U0001f4bb",   # 💻
    "web": "\U0001f310",        # 🌐
    "slack": "\U0001f4ac",      # 💬
    "system": "\U0001f504",     # 🔄
}


def slack_api(method: str, token: str, payload: dict) -> dict:
    """Call a Slack API method."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}

PROJECT_DIR = os.path.dirname(os.path.dirname(__file__))
STATE_DIR = os.path.join(PROJECT_DIR, ".claude")
STREAM_CLAIM_DIR = os.path.join(STATE_DIR, "stream-claims")
STREAM_IN_PROGRESS_TTL_S = 20 * 60
STREAM_COMPLETED_TTL_S = 24 * 60 * 60


def _source_key(source_ts: str, question: str) -> str:
    """Build a stable request key for deduping Slack replies."""
    clean_ts = (source_ts or "").strip()
    if clean_ts:
        return f"ts:{clean_ts}"
    normalized = " ".join((question or "").split()).strip().lower()[:500]
    return f"q:{normalized}"


def _claim_paths(channel: str, thread_ts: str, agent: str, source_key: str,
                 guard_dir: str = STREAM_CLAIM_DIR) -> tuple[str, str]:
    fingerprint = hashlib.sha1(
        f"{channel}|{thread_ts}|{agent}|{source_key}".encode("utf-8")
    ).hexdigest()
    return (
        os.path.join(guard_dir, f"{fingerprint}.lock"),
        os.path.join(guard_dir, f"{fingerprint}.done"),
    )


def _cleanup_claim_dir(guard_dir: str = STREAM_CLAIM_DIR, now: float | None = None) -> None:
    now = now or time.time()
    if not os.path.isdir(guard_dir):
        return
    for name in os.listdir(guard_dir):
        path = os.path.join(guard_dir, name)
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        ttl = STREAM_COMPLETED_TTL_S if name.endswith('.done') else STREAM_IN_PROGRESS_TTL_S
        if age > ttl:
            try:
                os.remove(path)
            except OSError:
                pass


def claim_stream_request(channel: str, thread_ts: str, agent: str, source_key: str,
                         guard_dir: str = STREAM_CLAIM_DIR, now: float | None = None) -> dict:
    """Atomically claim a Slack reply slot for one inbound message.

    Prevents duplicate progress/final replies when multiple observers or retries
    race on the same Slack message.
    """
    now = now or time.time()
    os.makedirs(guard_dir, exist_ok=True)
    _cleanup_claim_dir(guard_dir=guard_dir, now=now)

    lock_path, done_path = _claim_paths(channel, thread_ts, agent, source_key, guard_dir=guard_dir)

    if os.path.exists(done_path):
        return {"claimed": False, "reason": "completed", "lock_path": lock_path, "done_path": done_path}

    if os.path.exists(lock_path):
        try:
            age = now - os.path.getmtime(lock_path)
        except OSError:
            age = 0
        if age <= STREAM_IN_PROGRESS_TTL_S:
            return {"claimed": False, "reason": "in_progress", "lock_path": lock_path, "done_path": done_path}
        try:
            os.remove(lock_path)
        except OSError:
            return {"claimed": False, "reason": "in_progress", "lock_path": lock_path, "done_path": done_path}

    payload = json.dumps({
        "channel": channel,
        "thread_ts": thread_ts,
        "agent": agent,
        "source_key": source_key,
        "claimed_at": now,
    })
    try:
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "w") as f:
            f.write(payload)
    except FileExistsError:
        return {"claimed": False, "reason": "in_progress", "lock_path": lock_path, "done_path": done_path}

    return {"claimed": True, "reason": "claimed", "lock_path": lock_path, "done_path": done_path}


def release_stream_claim(claim: dict | None) -> None:
    if not claim:
        return
    lock_path = claim.get("lock_path")
    if lock_path and os.path.exists(lock_path):
        try:
            os.remove(lock_path)
        except OSError:
            pass


def mark_stream_request_done(claim: dict | None, now: float | None = None) -> None:
    if not claim:
        return
    now = now or time.time()
    done_path = claim.get("done_path")
    lock_path = claim.get("lock_path")
    if done_path:
        try:
            with open(done_path, "w") as f:
                json.dump({"completed_at": now}, f)
        except OSError:
            pass
    if lock_path and os.path.exists(lock_path):
        try:
            os.remove(lock_path)
        except OSError:
            pass


def post_initial(token: str, channel: str, thread_ts: str) -> str:
    """Post initial 'thinking' message, return its ts."""
    result = slack_api("chat.postMessage", token, {
        "channel": channel,
        "thread_ts": thread_ts,
        "text": "\U0001f916 \u23f3 Thinking...",
    })
    return result.get("ts", "")


def update_message(token: str, channel: str, msg_ts: str, text: str):
    """Update an existing message in place."""
    result = slack_api("chat.update", token, {
        "channel": channel,
        "ts": msg_ts,
        "text": text,
    })
    if not result.get("ok"):
        print(
            f"SLACK UPDATE ERROR: {result.get('error', 'unknown')} "
            f"(len={len(text)})",
            file=sys.stderr,
        )


def post_ui_evidence_blocks(
    token: str,
    channel: str,
    thread_ts: str,
    ui_evidence: list,
    files_changed: list | None = None,
) -> None:
    """Post UI screenshots as Slack Block Kit image blocks in a thread reply.

    Groups screenshots by feature.  Each image block uses the public
    /static/screenshots/ URL so Slack can pull them remotely.
    """
    if not ui_evidence:
        return

    blocks = []

    # Header
    changed_label = ""
    if files_changed:
        short = [f.split("/")[-1] for f in files_changed[:3]]
        changed_label = f"  _Files: {', '.join(short)}{'…' if len(files_changed) > 3 else ''}_"

        # Attempt to inject code-aware graph logic
        try:
            req = urllib.request.Request(
                "http://localhost:8000/api/code-linkage/impact",
                data=json.dumps({"files_changed": files_changed}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                impact_data = json.loads(resp.read())
                features = [f["name"] for f in impact_data.get("affected_features", [])[:2]]
                codes = []
                for s_id in impact_data.get("screens_to_retest", [])[:2]:
                    # Grab first code anchor for preview
                    try:
                        s_req = urllib.request.Request(f"http://localhost:8000/api/code-linkage/screen/{s_id}")
                        with urllib.request.urlopen(s_req, timeout=1) as s_resp:
                            s_data = json.loads(s_resp.read())
                            codes.extend(a["file_path"].split("/")[-1] for a in s_data.get("anchors", [])[:1])
                    except Exception:
                        pass
                
                if features or codes:
                    changed_label += "\n  " + " \u00b7 ".join(filter(None, [
                        f"*Impacts:* {', '.join(features)}" if features else "",
                        f"*Anchors:* {', '.join(set(codes))}" if codes else ""
                    ]))
        except Exception as e:
            pass  # Fail gracefully if backend down

    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f":camera_with_flash: *UI Evidence — affected screens*\n{changed_label}",
        },
    })

    # One image block per screenshot (Slack limit: 10 image blocks per message)
    for item in ui_evidence[:6]:
        url = item.get("screenshot_url", "")
        if not url:
            continue
        screen_name = item.get("screen_name") or item.get("screen_id") or "Screen"
        feature_name = item.get("feature_name") or ""
        reason = item.get("reason") or ""
        is_baseline = not item.get("is_delta", False)

        label_parts = [screen_name]
        if feature_name:
            label_parts.append(feature_name)
        caption = " · ".join(label_parts)
        if is_baseline and "baseline" not in reason:
            caption += " _(baseline)_"

        blocks.append({
            "type": "image",
            "title": {"type": "plain_text", "text": caption[:75], "emoji": True},
            "image_url": url,
            "alt_text": caption[:75],
        })
        if reason:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"_{reason}_"}],
            })

    if len(blocks) <= 1:
        return  # Only header, nothing useful

    result = slack_api("chat.postMessage", token, {
        "channel": channel,
        "thread_ts": thread_ts,
        "text": ":camera_with_flash: UI Evidence",
        "blocks": blocks,
    })
    if not result.get("ok"):
        print(
            f"SLACK UI EVIDENCE ERROR: {result.get('error', 'unknown')}",
            file=sys.stderr,
        )


def friendly_tool_label(tool: str, args_summary: str = "") -> str:
    """Return a human-friendly description for a tool call."""
    base = TOOL_LABELS.get(tool, f"Running {tool}")
    # Add context for specific tools
    if tool == "get_section" and args_summary:
        # Extract section name from args
        import re
        m = re.search(r'section_id="([^"]+)"', args_summary)
        if m:
            section = m.group(1).replace("-", " ").title()
            return f"Reading: {section}"
    if tool == "set_scenario" and args_summary:
        import re
        m = re.search(r'scenario="([^"]+)"', args_summary)
        if m:
            return f"Switching to {m.group(1)} scenario"
    if tool == "commit_diff" and args_summary:
        import re
        m = re.search(r'sha="([^"]+)"', args_summary)
        if m:
            return f"Inspecting commit {m.group(1)[:8]}"
    if tool == "read_file" and args_summary:
        import re
        m = re.search(r'path="([^"]+)"', args_summary)
        if m:
            path = m.group(1).split("/")[-1]  # Just filename
            return f"Reading {path}"
    if tool == "search" and args_summary:
        import re
        m = re.search(r'query="([^"]+)"', args_summary)
        if m:
            return f'Searching for "{m.group(1)[:30]}"'
    return base


def build_progress_message(
    strategy_line: str,
    phases: dict,
    current_action: str,
    elapsed: float,
    tool_count: int,
) -> str:
    """Build clean, manager-friendly progress message."""
    lines = ["\U0001f916 \u23f3 *Working on your question...*"]

    if strategy_line:
        lines.append(f"_{strategy_line}_")

    lines.append("")

    # Show completed phases as compact summaries
    for category, actions in phases.items():
        icon = CATEGORY_ICONS.get(category, "\u2699\ufe0f")
        done = [a for a in actions if a["done"]]
        pending = [a for a in actions if not a["done"]]

        if done:
            label = {"brief": "Brief", "codebase": "Codebase", "web": "Web", "slack": "Slack"}.get(category, category.title())
            lines.append(f"{icon} *{label}:* {len(done)} lookups completed")

    # Show current action
    if current_action:
        lines.append(f"\u23f3 _{current_action}..._")

    lines.append("")
    lines.append(f"_{tool_count} steps \u00b7 {elapsed:.0f}s elapsed_")

    return "\n".join(lines)


# ── Final answer formatting ──────────────────────────────────────────────

SLACK_MAX_CHARS = 3900  # Slack hard limit is 4000; leave margin for safety


def _build_evidence_block(data: dict) -> str:
    """Build the evidence section as a string."""
    lines = []
    evidence = data.get("evidence", [])
    if evidence:
        lines.append("")
        lines.append("\u2500" * 30)
        lines.append("*\U0001f50d Evidence & Traceability:*")
        for e in evidence[:10]:
            label = e.get("label", "")
            value = e.get("value", "")
            status = e.get("status", "")
            section = e.get("sectionId", "")
            if status == "shipped":
                dot = "\u2705"
            elif status == "in_progress":
                dot = "\U0001f535"
            else:
                dot = "\u26aa"
            line = f"  {dot} *{label}*: {value}"
            if section:
                section_title = section.replace("-", " ").title()
                line += f"  _\u2192 {section_title}_"
            lines.append(line)
    return "\n".join(lines)


def _build_footer(data: dict) -> str:
    """Build the telemetry footer as a string."""
    tool_calls = data.get("tool_calls", [])
    duration = data.get("duration_ms", 0)
    confidence = data.get("confidence", "")
    strategy = data.get("strategy", {})
    strategy_name = strategy.get("strategy", "") if isinstance(strategy, dict) else ""
    tokens = data.get("tokens", {})
    model = data.get("model", "gpt-5.4")

    meta = [f"*{model}*"]
    if confidence:
        meta.append(f"Confidence: *{confidence}*")
    if strategy_name:
        meta.append(strategy_name)
    if tool_calls:
        meta.append(f"{len(tool_calls)} lookups")
    if duration:
        meta.append(f"{duration / 1000:.0f}s")
    total_tok = tokens.get("total", 0)
    if total_tok:
        meta.append(f"{total_tok:,} tokens")
    estimated_cost = data.get("estimated_cost_usd", 0)
    if estimated_cost:
        meta.append(f"${estimated_cost:,.4f}")

    lines = ["", "\u2500" * 30, "_" + " \u00b7 ".join(meta) + "_"]
    return "\n".join(lines)


def _md_to_slack(text: str) -> str:
    """Convert Markdown to Slack mrkdwn format.

    Slack doesn't support: ## headings, **bold**, markdown tables, ### headings.
    Slack uses: *bold*, _italic_, `code`, ```code blocks```, > blockquote.
    """
    import re

    lines = text.split("\n")
    out: list = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # --- Headings: ## Foo → *Foo* (bold line)
        m = re.match(r"^#{1,4}\s+(.+)$", line)
        if m:
            out.append(f"*{m.group(1).strip()}*")
            i += 1
            continue

        # --- Markdown table detection: | col1 | col2 |
        if re.match(r"^\s*\|.*\|", line):
            table_lines = []
            while i < len(lines) and re.match(r"^\s*\|.*\|", lines[i]):
                table_lines.append(lines[i])
                i += 1
            # Convert table to readable format
            out.append(_md_table_to_slack(table_lines))
            continue

        # --- Bold: **text** → *text*  (but don't touch already-single *)
        line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)

        out.append(line)
        i += 1

    return "\n".join(out)


def _md_table_to_slack(table_lines: list) -> str:
    """Convert markdown table lines to a Slack-readable format.

    Input:  ['| Cost | Amount |', '|---|---|', '| Team | $10,000 |']
    Output: formatted text with aligned columns.
    """
    import re

    rows = []
    for line in table_lines:
        # Skip separator rows (|---|---|)
        if re.match(r"^\s*\|[\s\-:]+\|", line):
            continue
        # Parse cells
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)

    if not rows:
        return ""

    def _cell_to_slack(cell: str) -> str:
        """Convert **bold** in a cell to *bold* for Slack."""
        return re.sub(r"\*\*(.+?)\*\*", r"*\1*", cell.strip())

    # If we have a header row + data rows, format nicely
    if len(rows) >= 2:
        header = rows[0]
        data = rows[1:]
        lines = []
        for row in data:
            parts = []
            for j, cell in enumerate(row):
                c = _cell_to_slack(cell)
                if j < len(header) and header[j]:
                    h = re.sub(r"\*+", "", header[j]).strip()
                    if c:
                        parts.append(f"*{h}:* {c}")
                elif c:
                    parts.append(c)
            lines.append("  ".join(parts))
        return "\n".join(lines)
    else:
        return "  ".join(_cell_to_slack(c) for c in rows[0])


def _split_text_at_boundary(text: str, max_len: int) -> tuple:
    """Split text at a clean boundary (paragraph, heading, or bullet).

    Returns (chunk, remaining).
    """
    if len(text) <= max_len:
        return text, ""

    # Try to split at a paragraph break (double newline)
    search_zone = text[:max_len]
    last_para = search_zone.rfind("\n\n")
    if last_para > max_len // 3:  # Don't split too early
        return text[:last_para].rstrip(), text[last_para:].lstrip()

    # Try to split at a heading (## or ###)
    last_heading = max(search_zone.rfind("\n## "), search_zone.rfind("\n### "))
    if last_heading > max_len // 3:
        return text[:last_heading].rstrip(), text[last_heading:].lstrip()

    # Try to split at a bullet point
    last_bullet = search_zone.rfind("\n- ")
    if last_bullet > max_len // 3:
        return text[:last_bullet].rstrip(), text[last_bullet:].lstrip()

    # Last resort: split at last newline
    last_nl = search_zone.rfind("\n")
    if last_nl > max_len // 4:
        return text[:last_nl].rstrip(), text[last_nl:].lstrip()

    # Hard split
    return text[:max_len], text[max_len:]


def format_final_multi(data: dict) -> list:
    """Format the final agent result as a list of Slack messages.

    Returns a list of strings. The first message replaces the progress message
    (via chat.update). Subsequent messages are posted as new thread replies.
    Long answers are split at clean paragraph/heading/bullet boundaries.
    """
    raw_text = data.get("text", "").strip()
    if not raw_text:
        return ["\U0001f916 No response generated."]

    # Convert Markdown → Slack mrkdwn
    text = _md_to_slack(raw_text)

    evidence_block = _build_evidence_block(data)
    footer = _build_footer(data)
    suffix = evidence_block + "\n" + footer if evidence_block else footer
    prefix = "\U0001f916 "

    # If everything fits in one message, return it
    full_msg = prefix + text + "\n" + suffix
    if len(full_msg) <= SLACK_MAX_CHARS:
        return [full_msg]

    # Split the answer text across multiple messages
    messages = []
    remaining = text
    part_num = 0
    total_parts_estimate = (len(text) // (SLACK_MAX_CHARS - 200)) + 1

    while remaining:
        part_num += 1
        is_first = (part_num == 1)

        # Reserve space for part header and suffix on last message
        part_header = prefix if is_first else f"_\u2026 continued ({part_num}/{total_parts_estimate})_\n\n"

        # Check if remaining fits with suffix in this message
        candidate = part_header + remaining + "\n" + suffix
        if len(candidate) <= SLACK_MAX_CHARS:
            messages.append(candidate)
            remaining = ""
            break

        # Split: this chunk gets no suffix, just the text
        available = SLACK_MAX_CHARS - len(part_header) - 20  # margin
        chunk, remaining = _split_text_at_boundary(remaining, available)

        messages.append(part_header + chunk)

    # If we never appended the suffix (edge case), add it as final message
    if messages and suffix not in messages[-1]:
        combined_suffix = "\n" + suffix
        if len(messages[-1]) + len(combined_suffix) <= SLACK_MAX_CHARS:
            messages[-1] += combined_suffix
        elif len(suffix.strip()) <= SLACK_MAX_CHARS:
            messages.append(suffix.strip())
        else:
            # Evidence + footer together exceed a single message — split them
            footer_part = footer.strip()
            if evidence_block:
                max_evidence_len = SLACK_MAX_CHARS - len(footer_part) - 20
                trimmed_evidence = evidence_block.strip()
                if len(trimmed_evidence) > max_evidence_len:
                    trimmed_evidence = trimmed_evidence[:max_evidence_len].rsplit("\n", 1)[0] + "\n_… evidence truncated_"
                messages.append(trimmed_evidence + "\n" + footer_part)
            else:
                messages.append(footer_part)

    # Update part counts now that we know the actual total
    total = len(messages)
    if total > 1:
        for i in range(1, total):
            old_header = f"_\u2026 continued ({i + 1}/{total_parts_estimate})_"
            new_header = f"_\u2026 continued ({i + 1}/{total})_"
            messages[i] = messages[i].replace(old_header, new_header, 1)

    return messages


# ── Thread context fetcher ───────────────────────────────────────────────

BOT_USER_ID = "U0ALSPANA1G"


def fetch_thread_context(token: str, channel: str, thread_ts: str,
                         current_message: str = "") -> list:
    """Fetch thread history and build conversation context for the agent.

    Returns a list of {"role": "user"|"assistant", "content": "..."} messages.
    Limits to last 10 messages normally, or last 20 if the current user message
    is short (under 20 chars). Short follow-ups like "yes", "do it", "expand on
    that" need more prior context to be understood correctly.
    """
    result = slack_api("conversations.replies", token, {
        "channel": channel,
        "ts": thread_ts,
        "limit": 40,
    })
    messages = result.get("messages", [])
    if not messages:
        return []

    context = []
    for m in messages[:-1]:  # Exclude the last message (current question)
        user = m.get("user", "")
        text = m.get("text", "").strip()
        if not text:
            continue
        # Bot messages are "assistant", everything else is "user"
        role = "assistant" if user == BOT_USER_ID else "user"
        # Truncate long messages
        if len(text) > 2000:
            text = text[:2000] + "\n... (truncated)"
        context.append({"role": role, "content": text})

    # Short follow-ups ("yes", "do it", etc.) need more history to make sense,
    # so keep 20 exchanges instead of the default 10.
    keep = 20 if len(current_message.strip()) < 20 else 10
    return context[-keep:]


# ── Main streaming loop ─────────────────────────────────────────────────

def stream_agent(args):
    """Main streaming loop."""
    source_key = _source_key(getattr(args, "source_ts", ""), args.question)
    claim = claim_stream_request(args.channel, args.thread_ts, args.agent, source_key)
    if not claim.get("claimed"):
        print(
            f"SKIP duplicate Slack reply ({claim.get('reason')}) for {source_key}",
            file=sys.stderr,
        )
        return

    msg_ts = post_initial(args.slack_token, args.channel, args.thread_ts)
    if not msg_ts:
        release_stream_claim(claim)
        print("ERROR: Failed to post initial Slack message", file=sys.stderr)
        sys.exit(1)

    # Context graph: record incoming message
    _task_node_id = None
    if _slack_hooks:
        try:
            task_node = _slack_hooks.on_message_received(
                args.channel, args.thread_ts, "user", args.question, [],
            )
            _task_node_id = task_node.id
        except Exception:
            pass

    # ── Prior context from contextual graph (conversation continuity) ────
    prior_context = ""
    if _slack_graph:
        try:
            relevant = []
            for nid, node in _slack_graph._nodes.items():
                if node.kind == "task" and hasattr(node, 'metadata'):
                    meta = node.metadata or {}
                    # Match tasks from the same channel
                    if meta.get('channel') == args.channel:
                        relevant.append(node)

            # Most recent tasks first
            relevant.sort(key=lambda n: n.created_at, reverse=True)
            if relevant[:5]:
                lines = ["Prior context from this user:"]
                for task in relevant[:5]:
                    meta = task.metadata or {}
                    text = (task.intent or '')[:80]
                    # Find outcome via graph edges
                    outcome_text = ""
                    try:
                        for edge in _slack_graph.get_edges(task.id, direction="out"):
                            if edge.edge_type.value in (
                                'ACTION_EXPECTED_RESULT',
                                'OUTCOME_JUDGED_AS',
                            ):
                                outcome_node = _slack_graph.get_node(edge.to_id)
                                if outcome_node and hasattr(outcome_node, 'evidence'):
                                    resp = (outcome_node.evidence or {}).get('response_text', '')
                                    if resp:
                                        outcome_text = f" -> {resp[:60]}"
                                        break
                    except Exception:
                        pass
                    lines.append(f"  - [{task.created_at[:10]}] {text}{outcome_text}")
                prior_context = "\n".join(lines)
        except Exception:
            pass

    # Build context from thread history if available
    context = []
    if args.thread_ts and args.include_context:
        context = fetch_thread_context(args.slack_token, args.channel, args.thread_ts,
                                              current_message=args.question)

    # Prepend thread history into the question so the agent always sees it,
    # regardless of whether the backend supports the context field.
    question_with_context = args.question

    # Enrich with prior context from the graph for conversation continuity
    if prior_context:
        question_with_context = f"{prior_context}\n\nCurrent request: {question_with_context}"
    if context:
        thread_summary = "\n".join(
            f"{'[Bot]' if c['role'] == 'assistant' else '[User]'}: {c['content']}"
            for c in context
        )
        # Truncate if too long (keep last 6000 chars)
        if len(thread_summary) > 6000:
            thread_summary = "... (earlier messages truncated)\n" + thread_summary[-6000:]
        # Preserve any prior graph context already prepended to question_with_context
        current_msg = question_with_context if prior_context else args.question
        question_with_context = (
            f"[THREAD CONTEXT — previous messages in this thread:]\n"
            f"{thread_summary}\n\n"
            f"[CURRENT MESSAGE:]\n{current_msg}"
        )

    payload_dict = {"question": question_with_context, "max_turns": 1000}
    if context:
        payload_dict["context"] = context
    payload = json.dumps(payload_dict).encode("utf-8")

    url = f"{args.backend_url}/api/agents/{args.agent}/stream"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )

    # State
    phases: dict = {}           # category -> list of {label, done}
    strategy_line = ""
    current_action = ""
    last_update = 0
    tool_count = 0
    completed = False

    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            current_event = "status"
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")

                if line.startswith("event: "):
                    current_event = line[7:]
                    continue

                if not line.startswith("data: "):
                    continue

                data_str = line[6:]
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                now = time.time()

                if current_event == "status":
                    msg = data.get("message", "")
                    if msg and "Strategy:" in msg:
                        strategy_line = msg
                    elif msg:
                        elapsed = data.get("elapsed_s", 0)
                        progress = build_progress_message(
                            strategy_line, phases, current_action, elapsed, tool_count,
                        )
                        if now - last_update >= 3:
                            update_message(args.slack_token, args.channel, msg_ts, progress)
                            last_update = now

                elif current_event == "tool_start":
                    tool = data.get("tool", "?")
                    args_summary = data.get("args_summary", "")
                    elapsed = data.get("elapsed_s", 0)

                    label = friendly_tool_label(tool, args_summary)
                    current_action = label
                    category = TOOL_CATEGORIES.get(tool, "other")

                    if category not in phases:
                        phases[category] = []
                    phases[category].append({"label": label, "done": False})

                    progress = build_progress_message(
                        strategy_line, phases, current_action, elapsed, tool_count,
                    )
                    if now - last_update >= 3:
                        update_message(args.slack_token, args.channel, msg_ts, progress)
                        last_update = now

                elif current_event == "tool_done":
                    tool = data.get("tool", "?")
                    elapsed = data.get("elapsed_s", 0)
                    tool_count = data.get("total_tools", tool_count + 1)

                    # Mark the latest pending action in this category as done
                    category = TOOL_CATEGORIES.get(tool, "other")
                    if category in phases:
                        for a in reversed(phases[category]):
                            if not a["done"]:
                                a["done"] = True
                                break

                    current_action = ""

                    progress = build_progress_message(
                        strategy_line, phases, current_action, elapsed, tool_count,
                    )
                    if now - last_update >= 3:
                        update_message(args.slack_token, args.channel, msg_ts, progress)
                        last_update = now

                elif current_event == "done":
                    messages = format_final_multi(data)

                    # First message: update the progress message in place
                    update_message(args.slack_token, args.channel, msg_ts, messages[0])

                    # Additional messages: post as new thread replies
                    for extra_msg in messages[1:]:
                        time.sleep(0.5)  # Slack rate limit courtesy
                        slack_api("chat.postMessage", args.slack_token, {
                            "channel": args.channel,
                            "thread_ts": args.thread_ts,
                            "text": extra_msg,
                        })

                    ui_evidence = data.get("ui_evidence")
                    if ui_evidence:
                        post_ui_evidence_blocks(
                            args.slack_token,
                            args.channel,
                            args.thread_ts,
                            ui_evidence,
                            data.get("files_changed"),
                        )

                    completed = True

                    # Context graph: record response posted
                    if _slack_hooks and _task_node_id:
                        try:
                            response_text = data.get("text", "")[:512]
                            _slack_hooks.on_response_posted(_task_node_id, response_text, args.thread_ts)
                        except Exception:
                            pass

                    print(
                        f"DONE: {data.get('turns', 0)} turns, "
                        f"{tool_count} tools, "
                        f"{data.get('duration_ms', 0)}ms, "
                        f"{len(messages)} message(s)"
                    )
                    break

                elif current_event == "error":
                    err = data.get("message", "Unknown error")
                    update_message(
                        args.slack_token,
                        args.channel,
                        msg_ts,
                        f"\U0001f916 \u26a0\ufe0f Something went wrong: {err}",
                    )
                    completed = True
                    print(f"ERROR: {err}", file=sys.stderr)
                    break

    except urllib.error.URLError as e:
        update_message(
            args.slack_token,
            args.channel,
            msg_ts,
            "\U0001f916 \u26a0\ufe0f Could not reach the backend. Please try again.",
        )
        print(f"URL ERROR: {e}", file=sys.stderr)
    except Exception as e:
        update_message(
            args.slack_token,
            args.channel,
            msg_ts,
            "\U0001f916 \u26a0\ufe0f Something unexpected happened. Please try again.",
        )
        print(f"ERROR: {e}", file=sys.stderr)

    if not completed:
        update_message(
            args.slack_token,
            args.channel,
            msg_ts,
            "\U0001f916 \u26a0\ufe0f The request timed out. "
            "Try a shorter question or ask about one topic at a time.",
        )

    # Persist context graph to disk
    if _slack_graph:
        try:
            os.makedirs(os.path.dirname(_slack_graph_path), exist_ok=True)
            _slack_graph.save(_slack_graph_path)
        except Exception:
            pass

    mark_stream_request_done(claim)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stream agent to Slack thread")
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--thread-ts", required=True)
    parser.add_argument("--source-ts", default="",
                        help="Slack timestamp of the inbound message being answered")
    parser.add_argument("--slack-token", required=True)
    parser.add_argument("--include-context", action="store_true", default=False,
                        help="Fetch thread history as conversation context for follow-ups")
    args = parser.parse_args()
    stream_agent(args)
