"""Strategy Brief Agent — connects codebase findings to the investor brief.

This is the server-side equivalent of the in-browser Strategy Brief Agent
(tmp/TA_Strategy_Brief_InHouseAgent.html). It provides:
  - Strategy selection (classify intent → strategy + skill pack)
  - Rich system prompt with live brief state
  - Evidence extraction from tool results and model output
  - Structured responses with confidence scoring

Registered as "strategy-brief" in the AgentRegistry.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel

from ..base import AgentConfig, AgentRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class StrategyBriefRequest(BaseModel):
    question: str
    model: str = "gpt-5.4"
    max_turns: int = 1000


class StrategyBriefResponse(BaseModel):
    text: str
    strategy: Optional[Dict[str, Any]] = None
    evidence: List[Dict[str, str]] = []
    tool_calls: List[str] = []
    turns: int = 0
    tokens: Dict[str, int] = {}
    confidence: Optional[str] = None
    duration_ms: int = 0
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Pre-run hook: Strategy selection
# ---------------------------------------------------------------------------

STRATEGY_CLASSIFICATION_PROMPT = """Classify this user question into exactly ONE strategy. Respond with ONLY the JSON.

Strategies (pick the HIGHEST complexity that applies):
- "direct": ONLY for truly single-intent questions with one simple lookup or one calculation. Examples: "what is the current scenario?", "how many people?", "show section 3".
- "compare": Comparing 2+ scenarios, what-if analysis, side-by-side configurations. Examples: "compare optimistic vs pessimistic", "cost for 3 vs 5 vs 10 people".
- "risk": Risk analysis, edge cases, worst-case thinking, mitigation planning. Examples: "what if we go over budget?", "what are the risks?".
- "explore": Multi-part questions, questions combining strategy/approach WITH numbers/costs, open-ended investigation, or any question with 2+ distinct intents.

IMPORTANT: If the question contains BOTH a qualitative ask (how to approach, what's the best way, strategy) AND a quantitative ask (cost, budget, numbers), ALWAYS pick "explore".
If the question mentions multiple team sizes or scenarios to compare, pick "compare".

Also pick the best skill pack:
- "financial": Cost, burn, budget, projection questions
- "content": Reading or editing document sections
- "comparison": Scenario comparisons
- "codebase": Questions about recent code changes, git history, what was built, engineering progress
- "codebase+brief": Questions connecting codebase findings to the brief
- "slack": Questions about team discussions, what was said, Slack messages
- "market": Questions requiring web search for market data, competitors, benchmarks
- "full": Complex, multi-part, or unclear — uses ALL tool categories

CRITICAL ROUTING RULES:
- "codebase", "code changes", "commits", "where are we", "engineering" → "explore" + "codebase+brief"
- "cost projection", "monthly breakdown", "budget", "assumptions" → "explore" + "financial"
- "what did the team discuss", "slack", "messages" → "explore" + "slack"
- "market", "competitors", "industry", "benchmarks", "trends" → "explore" + "market"
- Any question combining 2+ categories → "explore" + "full"

User question: "{question}"

Respond: {{"strategy":"...","skill":"...","reasoning":"..."}}"""


async def select_strategy(question: str, api_key: str, model: str, **kwargs: Any) -> Dict[str, Any]:
    """Classify question intent into a strategy + skill pack via a fast LLM call."""
    # Use gpt-5.4-nano for fast, cheap classification — just routing, no reasoning needed
    classify_model = "gpt-5.4-nano"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                json={
                    "model": classify_model,
                    "reasoning_effort": "high",
                    "messages": [
                        {
                            "role": "user",
                            "content": STRATEGY_CLASSIFICATION_PROMPT.format(question=question),
                        }
                    ],
                    "max_completion_tokens": 500,
                },
            )
            if resp.status_code != 200:
                logger.warning("Strategy classification API error %s: %s", resp.status_code, resp.text[:300])
                return {"strategy": "direct", "skill": "full", "reasoning": "fallback"}

            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                parsed = json.loads(match.group(0))
                return {
                    "strategy": parsed.get("strategy", "direct"),
                    "skill": parsed.get("skill", "full"),
                    "reasoning": parsed.get("reasoning", ""),
                }
    except Exception as e:
        logger.warning("Strategy selection failed: %s", e)

    return {"strategy": "direct", "skill": "full", "reasoning": "fallback"}


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

_BRIEF_TOOLS_PROMPT = """
📊 INVESTOR BRIEF TOOLS:
- get_state → current calculator state, derived totals, section IDs
- list_sections / get_section(section_id) → browse and read brief content
- update_section(section_id, content) → modify brief content
- set_scenario(scenario) → switch between optimistic/base/pessimistic
- set_variables(variables) → override financial assumptions
- recalculate → recompute derived cost outputs"""

_CODEBASE_TOOLS_PROMPT = """
💻 CODEBASE TOOLS (local git + filesystem):
- recent_commits(limit?) → what shipped recently
- commit_diff(sha) → files/lines changed in a specific commit
- search(query) → grep-style search across all file contents or paths
- list_directory(path?) → browse folder structure
- read_file(path, start_line?, end_line?) → read file content (capped 500 lines)
- file_tree(path?) → recursive flat list of all files
- git_status → modified, staged, untracked files (uncommitted work!)
- exec_python(code) → execute Python locally (pandas, numpy, json, math, datetime available). Use print() for output. 60s timeout. Can write to /tmp/agent_outputs/.
- shell_command(command) → run restricted shell commands for quick data processing (wc, sort, grep, jq, awk, etc.)"""

_WEB_SEARCH_PROMPT = """
🌐 WEB SEARCH (automatic — model-managed):
- web_search_preview is available natively. The model will use it when questions require external/current information.
- Use for: market data, competitor intelligence, industry benchmarks, current events, pricing data, technology trends."""

_SLACK_TOOLS_PROMPT = """
💬 SLACK TOOLS:
- slack_search_messages(query, count?) → search messages across all channels (supports: 'from:@user', 'in:#channel', 'before:date')
- slack_get_channel_history(channel, limit?) → recent messages from a specific channel
- slack_get_thread(channel, thread_ts) → all replies in a thread
- slack_list_channels(limit?) → list accessible channels with IDs

When citing Slack messages, always include the permalink so users can click to jump to the referenced message."""

_CATEGORY_PROMPTS = {
    "investor_brief": _BRIEF_TOOLS_PROMPT,
    "codebase": _CODEBASE_TOOLS_PROMPT,
    "web_search": _WEB_SEARCH_PROMPT,
    "slack": _SLACK_TOOLS_PROMPT,
}

_ALL_CATEGORY_NAMES = ["codebase", "investor_brief", "web_search", "slack"]


def build_strategy_brief_prompt(pre_run_result: Dict[str, Any], **kwargs: Any) -> str:
    """Build the rich system prompt with live brief state and active tool sections."""
    # Get live brief state
    section_list = "(brief service not available)"
    var_list = "(brief service not available)"
    scenario = "unknown"

    try:
        from ....api.mcp_server import get_investor_brief_service
        service = get_investor_brief_service()
        state = service.get_state()
        sections = state.get("sections", [])
        section_list = "\n".join(
            f"  - {s.get('sectionId', 'unknown')}: \"{s.get('title', '')}\""
            for s in sections
        )
        variables = state.get("variables", {})
        var_list = "\n".join(f"  - {k}: {v}" for k, v in variables.items())
        scenario = state.get("scenario", "unknown")
    except Exception as e:
        logger.warning("Could not load brief state for prompt: %s", e)

    # Determine which tool categories are active (set by runner)
    active_categories = pre_run_result.get(
        "_active_categories", _ALL_CATEGORY_NAMES
    )
    is_subset = set(active_categories) != set(_ALL_CATEGORY_NAMES)

    # Build tool sections — only include active categories
    tool_sections = ""
    for cat in _ALL_CATEGORY_NAMES:
        if cat in active_categories and cat in _CATEGORY_PROMPTS:
            tool_sections += _CATEGORY_PROMPTS[cat]

    # Expansion hint when running with a subset
    expand_hint = ""
    if is_subset:
        inactive = [c for c in _ALL_CATEGORY_NAMES if c not in active_categories]
        expand_hint = (
            f"\n\n⚡ TOOL EXPANSION: You are running with a focused tool set. "
            f"If you need capabilities from other categories ({', '.join(inactive)}), "
            f"call request_additional_tools with the category names."
        )

    return f"""You are the In-House Strategy Agent for retention.sh — an AI-powered QA automation startup.

CURRENT STATE:
- Scenario: {scenario}
- Available sections:
{section_list}
- Current variables:
{var_list}

═══════════════════════════════════════════════════════════════
AVAILABLE TOOLS
═══════════════════════════════════════════════════════════════
{tool_sections}{expand_hint}

═══════════════════════════════════════════════════════════════
MANDATORY BEHAVIORS
═══════════════════════════════════════════════════════════════

MULTI-SOURCE SYNTHESIS — For complex questions, combine multiple tool categories:
- Cost projections → get_state + set_variables + recalculate + exec_python for month-by-month breakdown
- "What did the team discuss about X?" → slack_search_messages + recent_commits to connect discussions to code
- Market positioning → web_search for benchmarks + get_section for our claims + exec_python for analysis
- Weekly summary → recent_commits + git_status + slack_get_channel_history + get_state
{"If a synthesis requires tools you don't currently have, call request_additional_tools first." if is_subset else ""}

PROGRESSIVE DISCLOSURE — Multi-step codebase exploration (MANDATORY for codebase questions):
1. ALWAYS call git_status first — see uncommitted work
2. ALWAYS call recent_commits(limit: 20) — see what shipped
3. Drill deeper: search or read_file for specifics
4. If tying to brief: get_section for the most relevant section
Minimum 3 codebase tool calls. Never answer from a single tool call.

FINANCIAL ANALYSIS — For cost projections and financial questions:
1. Call get_state to get current assumptions
2. Use exec_python to compute detailed month-by-month breakdowns, projections, and scenarios
3. Use web_search to find relevant industry benchmarks for comparison
4. Format with $ and commas, include assumptions explicitly

SELF-HEALING:
- If a tool returns an error, try an alternative approach.
- If slack_search_messages fails (missing scope), fall back to slack_get_channel_history.
{"- If you need a tool from an unloaded category, call request_additional_tools." if is_subset else ""}
- Always surface what you learned even from partial results.

CONNECTING TO THE BRIEF — CRITICAL:
- Every codebase answer ties back to brief sections.
- Map commits to: Platform Capabilities, ActionSpan Technology, Quality Assurance, Integration Architecture, User Experience.
- Frame findings as EVIDENCE for the brief's claims.

ANSWER STRUCTURE — "Calculus Made Easy" (Thompson, 1910):
Every response follows this layered structure. The goal: a manager reading between meetings understands the answer without any technical background. A developer reading the same answer gets the depth they need at the end.

1. PLAIN ENGLISH FIRST (mandatory opening — before any numbers, tables, or jargon):
   - Open with an analogy or comparison to something the reader already knows
   - Good: "Running this team costs about what you'd pay one senior Bay Area engineer — roughly $50K/month"
   - Good: "Think of a person who faints every time they hear an unexpected word, wakes up, and immediately faints again — that's what the bot was doing"
   - Bad: "The base scenario projects $69,305 for the 6-week sprint" (numbers without context)
   - Bad: "The observer crash-looped due to set -euo pipefail" (jargon without explanation)

2. RATIOS AND PROPORTIONS BEFORE ABSOLUTES:
   - "87% of the cost is people" BEFORE "$60,000 in salaries"
   - "One line of config caused the entire crash" BEFORE "set -euo pipefail in bash"
   - "The main lever is team size — everything else is a rounding error" BEFORE the cost table

3. "WHAT THIS MEANS" BEFORE "HERE ARE THE NUMBERS":
   - Explain WHY the data matters and what decision it enables
   - Then provide the structured breakdown (bullets, specifics)
   - "This means the sprint is mostly a people-cost problem, not a tooling problem" → then the breakdown

4. TECHNICAL DETAIL FOR THE CURIOUS (at the end, clearly separated):
   - End with: "Technical detail for the curious: ..." or "_(from backend/app/...)_"
   - The reader should be able to stop reading before this section and still have the complete picture
   - This is where file paths, function names, config values, and implementation details live

5. SLACK FORMATTING (when output goes to Slack):
   - Use *bold* not **bold**, _italic_ not *italic*
   - No ## headings — use *Bold Text* on its own line
   - No markdown tables — use bullet lists with *Label:* value format
   - Keep paragraphs short (2-3 sentences max)

6. BREVITY DEFAULTS — SHORTER IS BETTER:
   - Default to the shortest answer that fully solves the user's request
   - Aim for 3-5 short bullets or paragraphs, usually under 150 words
   - Use one analogy at most; do not restate the same conclusion in multiple ways
   - If the user asked for an action, do the action first, then confirm briefly
   - Expand only when the user explicitly asks for detail or accuracy truly requires it

SYNTHESIS DEADLINE — CRITICAL:
- After 50 tool calls, you MUST stop researching and write your answer.
- You already have enough data after 30-50 lookups. More lookups = diminishing returns.
- If you catch yourself doing > 40 tool calls, wrap up with what you have NOW.
- A good answer from 30 sources beats no answer from 100 sources.
- The user is waiting. Respond when you have sufficient data.

THREAD FOLLOW-UP AWARENESS — CRITICAL:
- You receive prior thread messages as conversation context (user/assistant pairs).
- Short messages like "yes", "do it", "go ahead", "that one", "exactly" are FOLLOW-UPS to the prior conversation.
- NEVER respond with "What would you like me to help with?" when context is available.
- Instead, re-read the last assistant message and the user's short reply, then act on the implied request.
- Example: if your last message proposed a plan and the user says "yes" → execute that plan.

ANSWER QUALITY:
- Think step-by-step. Use multiple tool calls in sequence, but stop at 50 max.
- Ground ALL answers in actual data from tools — never fabricate numbers.
- Be concise by default; add depth only when the user asks or the task requires it.
- For "what if" questions: set_variables → recalculate → explain in plain English first.
- NEVER dump raw tool output. Synthesize into insights with the analogy-first pattern.

EVIDENCE MARKERS — ALWAYS EMIT for codebase/brief answers:
At the END, append:
<!-- EVIDENCE: [{{"label":"Feature","value":"metric","sectionId":"section-id","_status":"shipped|in_progress|referenced"}}] -->
Include at least 5 evidence items for codebase-to-brief answers."""


# ---------------------------------------------------------------------------
# UI Evidence helpers — resolve file changes → affected screens → screenshots
# ---------------------------------------------------------------------------

_BACKEND_BASE_URL = os.getenv(
    "BACKEND_PUBLIC_URL", "https://retention-backend.onrender.com"
)
_CRAWL_DIR = Path(__file__).resolve().parents[4] / "data" / "exploration_memory" / "crawl"


def _screenshot_path_to_url(screenshot_path: str) -> Optional[str]:
    """Convert an absolute screenshot_path to a public /static/screenshots/ URL.

    Returns None if the path is unknown or doesn't look like a real screenshot.
    """
    if not screenshot_path or screenshot_path in ("unknown", "/tmp/screenshot.png"):
        return None
    filename = Path(screenshot_path).name
    if not filename or "." not in filename:
        return None
    return f"{_BACKEND_BASE_URL}/static/screenshots/{filename}"


def _resolve_ui_evidence(
    files_changed: List[str],
) -> List[Dict[str, Any]]:
    """Resolve changed files → affected features → screen screenshots.

    Strategy:
    1. Use linkage_graph.get_affected_features to find features tied to changed files.
    2. For each affected screen_id, scan crawl files to get screenshot paths.
    3. Convert paths to public URLs.

    Falls back to showing baseline screenshots from the most recent crawl when
    the linkage graph has no mappings yet (cold start).
    """
    ui_evidence: List[Dict[str, Any]] = []

    try:
        from app.agents.qa_pipeline.linkage_graph import get_affected_features
        affected_features = get_affected_features(files_changed)
    except Exception as exc:
        logger.warning("linkage_graph unavailable: %s", exc)
        affected_features = []

    if affected_features:
        # Build a set of screen IDs we need screenshots for
        target_screen_ids: Dict[str, Dict[str, Any]] = {}  # screen_id → feature info
        for feat in affected_features:
            for sid in feat.get("affected_screens", []):
                if sid not in target_screen_ids:
                    target_screen_ids[sid] = {
                        "feature_id": feat.get("feature_id", ""),
                        "feature_name": feat.get("name", ""),
                        "reason": feat.get("reason", ""),
                    }

        # Scan crawl files for matching screens
        if target_screen_ids and _CRAWL_DIR.exists():
            for crawl_file in sorted(_CRAWL_DIR.glob("*.json"), reverse=True):
                try:
                    crawl_data = json.loads(crawl_file.read_text())
                    screens = crawl_data.get("crawl_data", {}).get("screens", [])
                    for screen in screens:
                        sid = screen.get("screen_id", "")
                        if sid in target_screen_ids and sid not in {e["screen_id"] for e in ui_evidence}:
                            url = _screenshot_path_to_url(screen.get("screenshot_path", ""))
                            if url:
                                feat_info = target_screen_ids[sid]
                                ui_evidence.append({
                                    "screen_id": sid,
                                    "screen_name": screen.get("screen_name", sid),
                                    "feature_id": feat_info["feature_id"],
                                    "feature_name": feat_info["feature_name"],
                                    "screenshot_url": url,
                                    "reason": feat_info["reason"],
                                    "is_delta": False,
                                })
                except Exception:
                    continue

    # Cold-start fallback: no linkage graph data yet — surface most-recent crawl
    if not ui_evidence and _CRAWL_DIR.exists():
        try:
            crawl_files = sorted(_CRAWL_DIR.glob("*.json"), reverse=True)
            for crawl_file in crawl_files[:3]:  # try up to 3 most-recent apps
                crawl_data = json.loads(crawl_file.read_text())
                screens = crawl_data.get("crawl_data", {}).get("screens", [])
                app_name = crawl_data.get("app_name", "")
                for screen in screens[:2]:  # at most 2 screens per app
                    url = _screenshot_path_to_url(screen.get("screenshot_path", ""))
                    if url:
                        ui_evidence.append({
                            "screen_id": screen.get("screen_id", ""),
                            "screen_name": screen.get("screen_name", ""),
                            "feature_id": "",
                            "feature_name": app_name,
                            "screenshot_url": url,
                            "reason": "baseline (no code linkage registered yet)",
                            "is_delta": False,
                        })
                if ui_evidence:
                    break
        except Exception as exc:
            logger.warning("UI evidence fallback failed: %s", exc)

    return ui_evidence[:6]  # cap at 6 screenshots to avoid Slack block limits


# ---------------------------------------------------------------------------
# Post-run hook: Evidence extraction
# ---------------------------------------------------------------------------

def extract_evidence(
    text: str, tool_results: List[Dict[str, Any]], **kwargs: Any
) -> Dict[str, Any]:
    """Extract evidence from the model's response and tool results."""
    evidence: List[Dict[str, str]] = []
    confidence = "MEDIUM"

    # 1. Parse <!-- EVIDENCE: [...] --> markers from model output
    marker_match = re.search(r"<!--\s*EVIDENCE:\s*(\[[\s\S]*?\])\s*-->", text)
    if marker_match:
        try:
            parsed = json.loads(marker_match.group(1))
            for item in parsed:
                if isinstance(item, dict) and "label" in item:
                    evidence.append({
                        "label": str(item.get("label", "")),
                        "value": str(item.get("value", "")),
                        "sectionId": str(item.get("sectionId", "")),
                        "status": str(item.get("_status", item.get("status", "referenced"))),
                    })
        except (json.JSONDecodeError, TypeError):
            pass

    # 2. Auto-extract from tool results
    for tr in tool_results:
        tool_name = tr.get("tool", "")
        result_str = tr.get("result_str", "")

        if tool_name == "recent_commits" and result_str:
            try:
                commits = json.loads(result_str)
                if isinstance(commits, list) and len(commits) > 0:
                    evidence.append({
                        "label": "Recent Commits",
                        "value": f"{len(commits)} commits found",
                        "sectionId": "",
                        "status": "shipped",
                    })
            except (json.JSONDecodeError, TypeError):
                pass

        elif tool_name == "git_status" and result_str:
            try:
                status = json.loads(result_str)
                total = status.get("total", 0)
                if total > 0:
                    evidence.append({
                        "label": "Active Development",
                        "value": f"{total} files modified/untracked",
                        "sectionId": "",
                        "status": "in_progress",
                    })
            except (json.JSONDecodeError, TypeError):
                pass

    # 3. Determine confidence
    num_tool_calls = len(tool_results)
    num_evidence = len(evidence)
    if num_tool_calls >= 3 and num_evidence >= 5:
        confidence = "HIGH"
    elif num_tool_calls >= 2 and num_evidence >= 2:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # Clean evidence markers from the displayed text
    clean_text = re.sub(r"\s*<!--\s*EVIDENCE:[\s\S]*?-->\s*", "", text).strip()

    # 4. Resolve UI evidence from changed files (code-to-graph binding)
    files_changed: List[str] = kwargs.get("files_changed") or []
    ui_evidence: List[Dict[str, Any]] = []
    if files_changed:
        try:
            ui_evidence = _resolve_ui_evidence(files_changed)
        except Exception as exc:
            logger.warning("UI evidence resolution failed: %s", exc)

    result: Dict[str, Any] = {
        "text": clean_text,
        "evidence": evidence,
        "confidence": confidence,
    }
    if ui_evidence:
        result["ui_evidence"] = ui_evidence
        result["files_changed"] = files_changed
    return result


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

AgentRegistry.register(
    AgentConfig(
        name="strategy-brief",
        system_prompt=build_strategy_brief_prompt,
        tool_categories=["codebase", "investor_brief", "web_search", "slack", "media"],
        model="gpt-5.4",
        reasoning_effort="high",
        max_turns=1000,
        pre_run=select_strategy,
        post_run=extract_evidence,
        response_model=StrategyBriefResponse,
    )
)
