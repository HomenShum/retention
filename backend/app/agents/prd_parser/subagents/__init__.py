"""
PRD Parser Subagents

Specialized agents for parallel PRD parsing following Anthropic's orchestrator-worker pattern.
Each subagent is optimized for a specific extraction task.

Subagents:
- StoryExtractorAgent: Extracts user stories (As a... I want... So that...)
- CriteriaExtractorAgent: Extracts acceptance criteria (Given/When/Then)
- TestCaseGeneratorAgent: Generates test cases from requirements
- EdgeCaseAnalyzerAgent: Identifies risks and edge cases
"""

from .story_extractor_agent import create_story_extractor_agent
from .criteria_extractor_agent import create_criteria_extractor_agent
from .test_case_generator_agent import create_test_case_generator_agent
from .edge_case_analyzer_agent import create_edge_case_analyzer_agent

__all__ = [
    "create_story_extractor_agent",
    "create_criteria_extractor_agent",
    "create_test_case_generator_agent",
    "create_edge_case_analyzer_agent",
]

