"""
Search Agent - Specialized agent for searching bug reports and test scenarios

This agent implements the "agent as tools" pattern for focused search capabilities.
"""

import logging
from agents import Agent
from agents.model_settings import ModelSettings
from openai.types.shared import Reasoning
from .tools import create_search_bug_reports_tool, create_search_test_scenarios_tool
from .search_instructions import SEARCH_AGENT_INSTRUCTIONS
from ..model_fallback import get_model_fallback_chain, ROUTING_MODEL

logger = logging.getLogger(__name__)


def create_search_agent(vector_search_service, test_scenarios: list) -> Agent:
    """
    Create a specialized search agent for bug reports and test scenarios.
    
    This agent is designed to be used as a tool by the main agent (agent-as-tool pattern).
    
    Args:
        vector_search_service: VectorSearchService instance for semantic search
        test_scenarios: List of available test scenarios
        
    Returns:
        Configured search agent
    """
    
    # Create search tools with dependencies
    search_bug_reports = create_search_bug_reports_tool(vector_search_service)
    search_test_scenarios = create_search_test_scenarios_tool(test_scenarios)

    # January 2026: Search uses ROUTING_MODEL (gpt-5)
    # Simple search/query tasks, low reasoning needed
    model_chain = get_model_fallback_chain("routing")
    primary_model = model_chain[0]
    logger.info(f"Search Assistant using model chain: {model_chain}")

    # GPT-5.4 Prompting Guide (Dec 2025): Use reasoning_effort for thinking control
    # Search tasks are simple - use LOW reasoning effort for efficiency
    agent = Agent(
        name="Search Assistant",
        instructions=SEARCH_AGENT_INSTRUCTIONS,
        tools=[search_bug_reports, search_test_scenarios],
        model=primary_model,  # gpt-5 for routing/search
        model_settings=ModelSettings(
            tool_choice="auto",
            parallel_tool_calls=True,  # P4: Enable concurrent searches for batching
            reasoning=Reasoning(effort="low"),  # GPT-5.4: Low thinking for simple search
            verbosity="low",  # GPT-5.4: Concise output for search results
        ),
    )
    
    return agent

