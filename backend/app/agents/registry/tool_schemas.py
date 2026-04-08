"""Centralized OpenAI function-calling tool schemas, organized by category.

Each category maps MCP tool names to OpenAI function-calling definitions.
The AgentRunner uses these to build the tool list for any registered agent
based on its declared tool_categories.
"""

from __future__ import annotations

from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Codebase tools (ta.codebase.*)
# ---------------------------------------------------------------------------

CODEBASE_TOOLS: Dict[str, dict] = {
    "ta.codebase.recent_commits": {
        "type": "function",
        "function": {
            "name": "recent_commits",
            "description": "Get recent git commits. Returns sha, message, author, date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "number", "description": "Max commits (default 20)"},
                    "path": {"type": "string", "description": "Filter by file path prefix"},
                },
                "required": [],
            },
        },
    },
    "ta.codebase.commit_diff": {
        "type": "function",
        "function": {
            "name": "commit_diff",
            "description": "Get files changed in a specific commit.",
            "parameters": {
                "type": "object",
                "properties": {"sha": {"type": "string", "description": "Commit SHA"}},
                "required": ["sha"],
            },
        },
    },
    "ta.codebase.search": {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search codebase by keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms"},
                    "search_type": {"type": "string", "description": "'code' or 'path'"},
                },
                "required": ["query"],
            },
        },
    },
    "ta.codebase.read_file": {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repo-relative file path"},
                    "start_line": {"type": "number"},
                    "end_line": {"type": "number"},
                },
                "required": ["path"],
            },
        },
    },
    "ta.codebase.list_directory": {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files in a directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory path"}},
                "required": [],
            },
        },
    },
    "ta.codebase.file_tree": {
        "type": "function",
        "function": {
            "name": "file_tree",
            "description": "Get recursive file tree.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Root path"}},
                "required": [],
            },
        },
    },
    "ta.codebase.git_status": {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Get git status (modified/staged/untracked files).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    "ta.codebase.exec_python": {
        "type": "function",
        "function": {
            "name": "exec_python",
            "description": "Execute Python code locally. Libraries available: json, math, datetime, collections, csv, re, statistics. For data analysis: pandas, numpy. Use print() for output. 60-second timeout. Can write files to /tmp/agent_outputs/.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "Python code to execute. Use print() to produce output."}},
                "required": ["code"],
            },
        },
    },
    "ta.codebase.shell_command": {
        "type": "function",
        "function": {
            "name": "shell_command",
            "description": "Run a restricted shell command for quick data processing. Allowed: wc, sort, uniq, head, tail, jq, date, cal, ls, cat, grep, find, du, df, echo, awk, sed, tr, cut, paste, column. Blocked: rm, mv, cp, chmod, sudo, curl, wget, ssh, python. 30-second timeout.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "Shell command to run"}},
                "required": ["command"],
            },
        },
    },
    "ta.codebase.write_file": {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or overwrite a file in the repository. Use for code changes, config edits, etc. Path must be relative to repo root. Cannot write outside the repo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repo-relative file path"},
                    "content": {"type": "string", "description": "Full file content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    "ta.codebase.run_tests": {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run pytest on specified or auto-discovered test files for changed code. Returns pass/fail, stdout, and failure details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {"type": "array", "items": {"type": "string"}, "description": "Test file paths. If empty, auto-discovers tests for git-changed files."},
                    "timeout": {"type": "number", "description": "Timeout in seconds (default 120)."},
                },
                "required": [],
            },
        },
    },
    "ta.codebase.create_pull_request": {
        "type": "function",
        "function": {
            "name": "create_pull_request",
            "description": "Create a GitHub PR: creates branch, commits staged changes, pushes, opens PR with AI-generated description. Returns PR URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "branch_name": {"type": "string", "description": "Branch name (e.g. 'feat/add-tests')"},
                    "title": {"type": "string", "description": "PR title (concise, under 70 chars)"},
                    "body": {"type": "string", "description": "PR description. If empty, AI generates from diff."},
                    "base": {"type": "string", "description": "Base branch (default: main)"},
                },
                "required": ["branch_name", "title"],
            },
        },
    },
    "ta.codebase.merge_pull_request": {
        "type": "function",
        "function": {
            "name": "merge_pull_request",
            "description": "Merge a GitHub PR by number. Squash-merges by default.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pr_number": {"type": "number", "description": "PR number to merge"},
                    "merge_method": {"type": "string", "description": "'merge', 'squash', or 'rebase' (default: squash)"},
                },
                "required": ["pr_number"],
            },
        },
    },
    "ta.codebase.create_github_issue": {
        "type": "function",
        "function": {
            "name": "create_github_issue",
            "description": "Create a GitHub issue for tracking bugs, tasks, or feature requests. Returns the issue URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Issue title"},
                    "body": {"type": "string", "description": "Issue body (markdown)"},
                    "labels": {"type": "array", "items": {"type": "string"}, "description": "Labels (e.g. ['bug', 'agent-created'])"},
                },
                "required": ["title"],
            },
        },
    },
    "ta.codebase.git_commit_and_push": {
        "type": "function",
        "function": {
            "name": "git_commit_and_push",
            "description": "Stage changed files, run an AI code review on the diff, and if approved commit + push to GitHub. Creates a revert-friendly checkpoint tag before pushing. The AI reviewer checks for: security issues, broken imports, syntax errors, and unintended changes. Returns the review verdict and commit SHA.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Commit message (concise, imperative mood)"},
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Repo-relative file paths to stage. If empty, stages all modified files.",
                    },
                    "skip_review": {
                        "type": "boolean",
                        "description": "Skip AI review (only for trivial changes like typo fixes). Default false.",
                    },
                },
                "required": ["message"],
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Investor brief tools (ta.investor_brief.*)
# ---------------------------------------------------------------------------

INVESTOR_BRIEF_TOOLS: Dict[str, dict] = {
    "ta.investor_brief.get_state": {
        "type": "function",
        "function": {
            "name": "get_state",
            "description": "Return the current investor-brief calculator state, derived totals, and section IDs.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    "ta.investor_brief.list_sections": {
        "type": "function",
        "function": {
            "name": "list_sections",
            "description": "List all stable investor-brief section IDs.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    "ta.investor_brief.get_section": {
        "type": "function",
        "function": {
            "name": "get_section",
            "description": "Retrieve one investor-brief section by stable section_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string", "description": "Stable section ID"},
                },
                "required": ["section_id"],
            },
        },
    },
    "ta.investor_brief.update_section": {
        "type": "function",
        "function": {
            "name": "update_section",
            "description": "Replace the body of an investor-brief section.",
            "parameters": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string", "description": "Stable section ID to update"},
                    "content": {"type": "string", "description": "Replacement body content"},
                    "content_format": {"type": "string", "description": "'html' or 'text'"},
                },
                "required": ["section_id", "content"],
            },
        },
    },
    "ta.investor_brief.set_scenario": {
        "type": "function",
        "function": {
            "name": "set_scenario",
            "description": "Apply a named sprint-cost scenario preset (optimistic, base, pessimistic).",
            "parameters": {
                "type": "object",
                "properties": {
                    "scenario": {"type": "string", "description": "One of: optimistic, base, pessimistic"},
                },
                "required": ["scenario"],
            },
        },
    },
    "ta.investor_brief.set_variables": {
        "type": "function",
        "function": {
            "name": "set_variables",
            "description": "Apply partial calculator variable overrides using canonical keys.",
            "parameters": {
                "type": "object",
                "properties": {
                    "variables": {"type": "object", "description": "Partial variable overrides"},
                },
                "required": ["variables"],
            },
        },
    },
    "ta.investor_brief.recalculate": {
        "type": "function",
        "function": {
            "name": "recalculate",
            "description": "Recompute derived cost outputs from current calculator inputs.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
}


# ---------------------------------------------------------------------------
# Web search tools (OpenAI native web_search_preview)
# ---------------------------------------------------------------------------

# web_search_preview is a native /v1/responses tool type — not a function.
# It uses {"type": "web_search_preview"} directly (no function wrapper).
# The runner passes non-function tools through unchanged.
WEB_SEARCH_TOOLS: Dict[str, dict] = {
    "__web_search_preview__": {
        "type": "web_search_preview",
        "search_context_size": "medium",
    },
}


# ---------------------------------------------------------------------------
# Slack tools (ta.slack.*)
# ---------------------------------------------------------------------------

SLACK_TOOLS: Dict[str, dict] = {
    "ta.slack.search_messages": {
        "type": "function",
        "function": {
            "name": "slack_search_messages",
            "description": "Search Slack messages across all channels the bot has access to. Returns matching messages with channel, author, timestamp, and permalink. Use for finding discussions, decisions, and context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (supports Slack search syntax: 'from:@user', 'in:#channel', 'before:2026-03-15')"},
                    "count": {"type": "number", "description": "Max results to return (default 10, max 50)"},
                },
                "required": ["query"],
            },
        },
    },
    "ta.slack.get_channel_history": {
        "type": "function",
        "function": {
            "name": "slack_get_channel_history",
            "description": "Get recent messages from a specific Slack channel. Returns messages with author, timestamp, and text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Channel ID (e.g. C0AM2J4G6S0) or channel name (e.g. #claw-communications)"},
                    "limit": {"type": "number", "description": "Number of messages (default 20, max 100)"},
                },
                "required": ["channel"],
            },
        },
    },
    "ta.slack.get_thread": {
        "type": "function",
        "function": {
            "name": "slack_get_thread",
            "description": "Get all replies in a Slack thread. Returns the parent message and all replies with author, timestamp, and text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Channel ID"},
                    "thread_ts": {"type": "string", "description": "Thread parent timestamp (e.g. '1710000000.000000')"},
                },
                "required": ["channel", "thread_ts"],
            },
        },
    },
    "ta.slack.list_channels": {
        "type": "function",
        "function": {
            "name": "slack_list_channels",
            "description": "List Slack channels the bot has access to. Returns channel names and IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "number", "description": "Max channels (default 50)"},
                },
                "required": [],
            },
        },
    },
    "ta.slack.add_reaction": {
        "type": "function",
        "function": {
            "name": "slack_add_reaction",
            "description": "Add an emoji reaction to a Slack message. Use for lightweight acknowledgment (e.g. eyes, white_check_mark, brain).",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Channel ID"},
                    "timestamp": {"type": "string", "description": "Message timestamp to react to"},
                    "emoji": {"type": "string", "description": "Emoji name without colons (e.g. 'eyes', 'white_check_mark', 'brain')"},
                },
                "required": ["channel", "timestamp", "emoji"],
            },
        },
    },
    "ta.slack.post_message": {
        "type": "function",
        "function": {
            "name": "slack_post_message",
            "description": "Post a message to a Slack channel or thread. Use Slack mrkdwn (*bold*, _italic_, `code`). Auto-splits messages over 3900 chars.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Channel ID"},
                    "text": {"type": "string", "description": "Message text in Slack mrkdwn format"},
                    "thread_ts": {"type": "string", "description": "Thread timestamp to reply in (optional)"},
                },
                "required": ["channel", "text"],
            },
        },
    },
    "ta.slack.arbitrate_conflict": {
        "type": "function",
        "function": {
            "name": "slack_arbitrate_conflict",
            "description": "When multiple agent roles disagree, synthesize their positions into a single recommendation with dissent noted. Posts the arbitration as a reply in the thread.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Channel ID"},
                    "thread_ts": {"type": "string", "description": "Thread with conflicting opinions"},
                    "topic": {"type": "string", "description": "What the agents disagree about"},
                },
                "required": ["channel", "thread_ts", "topic"],
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Agent self-spawning — spawn_deep_research
# ---------------------------------------------------------------------------

SPAWN_RESEARCH_TOOLS: Dict[str, dict] = {
    "agent.spawn_deep_research": {
        "type": "function",
        "function": {
            "name": "spawn_deep_research",
            "description": (
                "Spawn a full sub-investigation using the OpenClaw orchestrator agent. "
                "The child agent gets codebase, investor brief, web search, and Slack "
                "tools with up to 8 tool-calling turns. Use this when you need to look "
                "up specific data rather than speculating. Returns a research brief with "
                "evidence, tool calls used, and confidence level."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": (
                            "The specific question to investigate. Be precise — "
                            "e.g. 'What are our competitors charging for QA automation?' "
                            "or 'What did the investor brief say about Q2 targets?'"
                        ),
                    },
                },
                "required": ["question"],
            },
        },
    },
    "agent.spawn_parallel_research": {
        "type": "function",
        "function": {
            "name": "spawn_parallel_research",
            "description": (
                "Run multiple deep-research sub-investigations concurrently. "
                "Each question spawns its own OpenClaw orchestrator child agent "
                "and all run in parallel via asyncio.gather(). Use this when you "
                "have several independent questions to research at once."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "List of specific questions to investigate in parallel. "
                            "Each should be precise and self-contained."
                        ),
                    },
                    "max_per_question": {
                        "type": "integer",
                        "description": "Max words per research response (default 200).",
                        "default": 200,
                    },
                },
                "required": ["questions"],
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Media tools — YouTube transcript extraction, URL content fetching
# ---------------------------------------------------------------------------

MEDIA_TOOLS: Dict[str, dict] = {
    "ta.media.youtube_transcript": {
        "type": "function",
        "function": {
            "name": "youtube_transcript",
            "description": (
                "Extract the transcript/captions from a YouTube video. "
                "Use this when a user shares a YouTube link and wants you to "
                "analyze, summarize, or discuss the video content. "
                "Returns the full text transcript with timestamps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "YouTube URL or video ID (e.g. 'https://youtu.be/abc123' or 'abc123')",
                    },
                    "languages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Preferred languages (default: ['en'])",
                    },
                },
                "required": ["url"],
            },
        },
    },
    "ta.media.generate_slides": {
        "type": "function",
        "function": {
            "name": "generate_slides",
            "description": (
                "Generate a visual HTML slide deck and post it to Slack. "
                "Use this when you want to create a presentation, pitch deck, "
                "status report, competitive analysis, or deep sim summary as "
                "a shareable slide deck. The output is a self-contained HTML "
                "file with animations that opens in any browser. "
                "Styles: brutalist-mono, midnight-aurora, paper-craft, "
                "neon-terminal, watercolor-wash, glass-morphism."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The presentation topic/title",
                    },
                    "slides": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "body": {"type": "string"},
                            },
                            "required": ["title", "body"],
                        },
                        "description": "List of slides, each with title and body content",
                    },
                    "style": {
                        "type": "string",
                        "description": "Visual style preset (default: brutalist-mono)",
                    },
                },
                "required": ["topic", "slides"],
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Perception tools (ta.perception.*)
# Three-level agent context: on-page, browser, OS
# ---------------------------------------------------------------------------

PERCEPTION_TOOLS: Dict[str, dict] = {
    "ta.perception.get_context": {
        "type": "function",
        "function": {
            "name": "perception_get_context",
            "description": "Get the current perception context for the active level. Returns context deck (active/on-deck/recent/memory/evidence/hover), agent presence, pending approvals, and recent tool receipts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Cockpit session ID"},
                },
                "required": ["session_id"],
            },
        },
    },
    "ta.perception.switch_level": {
        "type": "function",
        "function": {
            "name": "perception_switch_level",
            "description": "Switch the active perception level. Levels: 'on_page' (app-scoped copilot), 'browser' (tab-scoped operator), 'os' (desktop-scoped assistant).",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Cockpit session ID"},
                    "level": {"type": "string", "description": "Target level: on_page, browser, or os"},
                },
                "required": ["session_id", "level"],
            },
        },
    },
    "ta.perception.select_entity": {
        "type": "function",
        "function": {
            "name": "perception_select_entity",
            "description": "Set the focused entity in on-page context (doc, run, ticket, bug). Pushes it to the active slot in the context deck.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Cockpit session ID"},
                    "entity": {"type": "object", "description": "Entity data (id, title, etc.)"},
                    "entity_type": {"type": "string", "description": "Entity type: doc, run, ticket, bug, session"},
                },
                "required": ["session_id", "entity", "entity_type"],
            },
        },
    },
    "ta.perception.update_surfaces": {
        "type": "function",
        "function": {
            "name": "perception_update_surfaces",
            "description": "Update the on-page surface registry — panels, drawers, tabs, cards visible in the app.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Cockpit session ID"},
                    "surfaces": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "surface_id": {"type": "string"},
                                "surface_type": {"type": "string"},
                                "label": {"type": "string"},
                                "visible": {"type": "boolean"},
                                "active": {"type": "boolean"},
                            },
                            "required": ["surface_id", "surface_type"],
                        },
                        "description": "List of UI surfaces",
                    },
                    "active_surface_id": {"type": "string", "description": "Which surface is active"},
                },
                "required": ["session_id", "surfaces"],
            },
        },
    },
    "ta.perception.browser_navigate": {
        "type": "function",
        "function": {
            "name": "perception_browser_navigate",
            "description": "Update browser page state — URL, title, optional screenshot and DOM snapshot. Use after navigating to a new page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Cockpit session ID"},
                    "url": {"type": "string", "description": "Current page URL"},
                    "title": {"type": "string", "description": "Page title"},
                    "screenshot_b64": {"type": "string", "description": "Base64 PNG screenshot (optional)"},
                    "dom_snapshot": {"type": "string", "description": "Simplified DOM / accessibility tree (optional)"},
                },
                "required": ["session_id", "url", "title"],
            },
        },
    },
    "ta.perception.request_approval": {
        "type": "function",
        "function": {
            "name": "perception_request_approval",
            "description": "Request human approval for a risky action. Creates an approval gate that blocks execution until the user approves or denies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Cockpit session ID"},
                    "action_description": {"type": "string", "description": "What the agent wants to do"},
                    "risk_level": {"type": "string", "description": "Risk level: low, medium, high, critical"},
                },
                "required": ["session_id", "action_description"],
            },
        },
    },
    "ta.perception.get_snapshot": {
        "type": "function",
        "function": {
            "name": "perception_get_snapshot",
            "description": "Get the full cockpit state snapshot — all three perception levels, workspaces, agents, approvals, and receipts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Cockpit session ID"},
                },
                "required": ["session_id"],
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Category registry
# ---------------------------------------------------------------------------

TOOL_CATEGORIES: Dict[str, Dict[str, dict]] = {
    "codebase": CODEBASE_TOOLS,
    "investor_brief": INVESTOR_BRIEF_TOOLS,
    "web_search": WEB_SEARCH_TOOLS,
    "slack": SLACK_TOOLS,
    "spawn": SPAWN_RESEARCH_TOOLS,
    "media": MEDIA_TOOLS,
    "perception": PERCEPTION_TOOLS,
}


def get_tools_for_categories(categories: List[str]) -> List[dict]:
    """Return OpenAI function-calling tool defs for the given categories."""
    tools: List[dict] = []
    for cat in categories:
        if cat in TOOL_CATEGORIES:
            tools.extend(TOOL_CATEGORIES[cat].values())
    return tools


# ---------------------------------------------------------------------------
# Skill → Category mapping (progressive tool disclosure)
# The strategy classifier outputs a "skill" that determines which tool
# categories are relevant. This avoids loading all 19 tools for every query.
# ---------------------------------------------------------------------------

SKILL_TO_CATEGORIES: Dict[str, List[str]] = {
    "financial":      ["investor_brief", "codebase", "spawn"],
    "content":        ["investor_brief", "spawn"],
    "comparison":     ["investor_brief", "codebase", "spawn"],
    "codebase":       ["codebase", "spawn"],
    "codebase+brief": ["codebase", "investor_brief", "spawn"],
    "slack":          ["slack", "codebase", "spawn"],
    "market":         ["web_search", "investor_brief", "spawn"],
    "perception":     ["perception", "codebase", "spawn"],
    "full":           ["codebase", "investor_brief", "web_search", "slack", "spawn", "media", "perception"],
}

# All known category names for reference
ALL_CATEGORIES = list(TOOL_CATEGORIES.keys())


def get_categories_for_skill(skill: str, agent_categories: List[str]) -> List[str]:
    """Return the tool categories for a given skill, intersected with agent's declared categories."""
    skill_cats = SKILL_TO_CATEGORIES.get(skill, SKILL_TO_CATEGORIES["full"])
    # Only include categories the agent is actually configured for
    return [c for c in skill_cats if c in agent_categories]


def get_tools_for_skill(skill: str, agent_categories: List[str]) -> List[dict]:
    """Return tool defs filtered by skill classification + agent config."""
    active = get_categories_for_skill(skill, agent_categories)
    return get_tools_for_categories(active)


# ---------------------------------------------------------------------------
# Expand-tools meta-tool (for progressive disclosure)
# Added to the tool list when running with a subset so the model can request
# additional categories mid-run if the initial set proves insufficient.
# ---------------------------------------------------------------------------

EXPAND_TOOLS_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "request_additional_tools",
        "description": (
            "Request additional tool categories if the current set is insufficient. "
            "Available categories: codebase (git, files, exec_python), "
            "investor_brief (brief sections, scenarios, variables), "
            "web_search (internet search for market data), "
            "slack (search messages, channel history). "
            "Call this if you need capabilities not currently available."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Category names to add: 'codebase', 'investor_brief', 'web_search', 'slack'",
                },
                "reason": {
                    "type": "string",
                    "description": "Why these additional tools are needed",
                },
            },
            "required": ["categories"],
        },
    },
}


# ---------------------------------------------------------------------------
# Unified function-name → MCP-name mapping
# Auto-built from all tool schemas so dispatch works across categories.
# ---------------------------------------------------------------------------

def _build_func_to_mcp() -> Dict[str, str]:
    """Build a mapping from OpenAI function names to MCP tool names."""
    mapping: Dict[str, str] = {}
    for _cat_tools in TOOL_CATEGORIES.values():
        for mcp_name, schema in _cat_tools.items():
            # Skip native tool types (e.g. web_search_preview) — no function wrapper
            if "function" not in schema:
                continue
            fn_name = schema["function"]["name"]
            mapping[fn_name] = mcp_name
    return mapping


FUNC_TO_MCP: Dict[str, str] = _build_func_to_mcp()


# Also support short names used in the HTML frontend
SHORT_NAME_MAP: Dict[str, str] = {
    "get_recent_commits": "ta.codebase.recent_commits",
    "get_commit_diff": "ta.codebase.commit_diff",
    "search_codebase": "ta.codebase.search",
    "list_directory": "ta.codebase.list_directory",
    "get_file_tree": "ta.codebase.file_tree",
}
