"""
LLM-based PRD Parser Module

Multi-agent orchestration system for parsing Product Requirement Documents.
Follows industry best practices from Anthropic, Manus, OpenAI, and LangGraph.

Architecture:
- Orchestrator-Worker Pattern: Lead agent decomposes PRD, coordinates subagents
- Parallel Execution: Multiple subagents process sections simultaneously
- KV-Cache Optimization: Stable prompts, append-only context
- MCP Tool Interface: Clean callable interface for future agents

Components:
- PRDParserOrchestrator: Lead agent that analyzes PRD structure
- StoryExtractorAgent: Extracts user stories (As a... I want... So that...)
- CriteriaExtractorAgent: Extracts acceptance criteria (Given/When/Then)
- TestCaseGeneratorAgent: Generates test cases from requirements
- EdgeCaseAnalyzerAgent: Identifies risks and edge cases

Reference:
- Anthropic Multi-Agent Research System (June 2025)
- Manus Context Engineering (July 2025)
- LangGraph Parallel Execution Patterns
- OpenAI Agents SDK MCP Integration
"""

from .prd_parser_service import PRDParserService
from .models.prd_models import (
    PRDParseRequest,
    PRDParseResult,
    ExtractedUserStory,
    ExtractedCriteria,
    GeneratedTestCase,
    ParseOptions,
)

__all__ = [
    "PRDParserService",
    "PRDParseRequest",
    "PRDParseResult",
    "ExtractedUserStory",
    "ExtractedCriteria",
    "GeneratedTestCase",
    "ParseOptions",
]

