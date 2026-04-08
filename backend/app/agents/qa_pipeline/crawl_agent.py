"""
Crawl Agent — trajectory-planned app crawler with device tools.

Instructions are loaded from prompts/Agent_Navigator_V1.md and composed
with skill files from prompts/skills/*.md at agent creation time.
"""

import logging
from pathlib import Path
from typing import Dict, Any, Callable

from agents import Agent, function_tool
from agents.model_settings import ModelSettings

from ..model_fallback import VISION_MODEL

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_instructions() -> str:
    """Compose agent instructions from Agent_Navigator_V1.md + skills/*.md."""
    parts = []

    main_path = _PROMPTS_DIR / "Agent_Navigator_V1.md"
    if main_path.exists():
        parts.append(main_path.read_text(encoding="utf-8").strip())
    else:
        logger.warning(f"Main prompt not found: {main_path}")
        parts.append("You are an app explorer. Discover screens by navigating the app.")

    skills_dir = _PROMPTS_DIR / "skills"
    if skills_dir.exists():
        for skill_file in sorted(skills_dir.glob("*.md")):
            parts.append(skill_file.read_text(encoding="utf-8").strip())

    return "\n\n---\n\n".join(parts)


def create_crawl_agent(
    nav_tools: Dict[str, Any],
    crawl_tools: Dict[str, Callable],
    model_override: str = "",
) -> Agent:
    """
    Create the crawl agent with trajectory-planned navigation.

    Args:
        nav_tools: Device navigation tool functions from create_autonomous_navigation_tools()
        crawl_tools: Crawl infrastructure tool functions from create_crawl_tools()

    Returns:
        Configured Agent instance
    """
    tools = []

    nav_tool_names = [
        # Hierarchy-first interaction (primary)
        "get_ui_elements",
        "tap_element",
        "tap_by_text",
        "tap_by_resource_id",
        "wait_for_element",
        "get_current_activity",
        # Supplementary
        "list_elements_on_screen",
        "take_screenshot",
        "click_at_coordinates",
        "press_button",
        "launch_app",
        "get_screen_size",
    ]
    for name in nav_tool_names:
        if name in nav_tools:
            tools.append(function_tool(nav_tools[name]))

    crawl_tool_names = [
        "save_trajectory_plan",
        "get_next_trajectory",
        "get_next_target",
        "register_screen",
        "complete_crawl",
        "get_exploration_log",
    ]
    for name in crawl_tool_names:
        if name in crawl_tools:
            tools.append(function_tool(crawl_tools[name]))

    instructions = _load_instructions()

    _model = model_override or VISION_MODEL
    agent = Agent(
        name="QA Crawl Agent",
        instructions=instructions,
        tools=tools,
        model=_model,
        model_settings=ModelSettings(
            tool_choice="auto",
            parallel_tool_calls=False,  # Sequential navigation
        ),
    )

    logger.info(f"Created QA Crawl Agent with {len(tools)} tools, model={_model}")
    return agent
