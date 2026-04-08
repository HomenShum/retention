"""
PRD Parser Service - Main orchestration service for LLM-based PRD parsing

This service coordinates the multi-agent PRD parsing workflow:
1. Lead orchestrator analyzes PRD structure
2. Subagents extract stories, criteria, edge cases in parallel
3. Test case generator creates test cases
4. Results are merged and returned

Reference:
- Anthropic Multi-Agent Research System (June 2025)
- LangGraph Parallel Execution Patterns
- Manus Context Engineering (July 2025)
"""

import asyncio
import logging
import json
import time
from typing import List, Dict, Any, Optional
from datetime import datetime

from agents import Runner

from .models.prd_models import (
    PRDParseRequest,
    PRDParseResult,
    ExtractedUserStory,
    ExtractedCriteria,
    GeneratedTestCase,
    PRDSection,
    ExtractionTask,
    StoryPriority,
    CriteriaPriority,
)
from .prd_parser_orchestrator import (
    create_prd_orchestrator_agent,
    analyze_prd_structure,
    create_extraction_plan,
)
from .subagents import (
    create_story_extractor_agent,
    create_criteria_extractor_agent,
    create_test_case_generator_agent,
    create_edge_case_analyzer_agent,
)

logger = logging.getLogger(__name__)


class PRDParserService:
    """
    LLM-based PRD Parser Service using multi-agent orchestration.
    
    Follows Anthropic's orchestrator-worker pattern with parallel subagents.
    Designed for MCP tool integration and future agent calling.
    """
    
    def __init__(self):
        """Initialize the PRD Parser Service"""
        self._story_counter = 0
        self._criteria_counter = 0
        self._test_counter = 0
        logger.info("PRD Parser Service initialized")
    
    async def parse(
        self,
        content: str,
        title: str = "Untitled PRD",
        options: Optional[Dict[str, Any]] = None
    ) -> PRDParseResult:
        """
        Parse a PRD document using LLM-based multi-agent orchestration.
        
        Args:
            content: The raw PRD text to parse
            title: Document title
            options: Optional parsing configuration
            
        Returns:
            PRDParseResult with extracted stories, criteria, and test cases
        """
        start_time = time.time()
        options = options or {}
        errors = []
        
        logger.info(f"[PRD Parser] Starting parse of: {title}")
        logger.info(f"[PRD Parser] Content length: {len(content)} chars")
        
        try:
            # Step 1: Analyze PRD structure (synchronous tool call)
            logger.info("[PRD Parser] Step 1: Analyzing document structure...")
            structure = analyze_prd_structure(content, title)
            sections = [
                PRDSection(
                    id=s["id"],
                    title=s["title"],
                    content=s["content"],
                    section_type=s["type"],
                )
                for s in structure.get("sections", [])
            ]
            logger.info(f"[PRD Parser] Found {len(sections)} sections")
            
            # Step 2: Create extraction plan
            logger.info("[PRD Parser] Step 2: Creating extraction plan...")
            plan = create_extraction_plan(structure.get("sections", []))
            tasks = [
                ExtractionTask(
                    task_id=t["task_id"],
                    agent_type=t["agent_type"],
                    section_ids=t["section_ids"],
                    priority=t.get("priority", 1),
                )
                for t in plan.get("tasks", [])
            ]
            logger.info(f"[PRD Parser] Created {len(tasks)} extraction tasks")
            
            # Step 3: Run parallel extraction
            logger.info("[PRD Parser] Step 3: Running parallel extraction...")
            use_parallel = options.get("parallel_execution", True)
            
            # Prepare section content for subagents
            section_content = "\n\n".join([
                f"## {s.title}\n{s.content}" for s in sections
            ])
            
            # Run subagents
            stories, criteria, edge_cases = await self._run_parallel_extraction(
                section_content, use_parallel
            )
            logger.info(f"[PRD Parser] Extracted {len(stories)} stories, {len(criteria)} criteria")
            
            # Step 4: Generate test cases
            test_cases = []
            if options.get("include_test_cases", True) and (stories or criteria):
                logger.info("[PRD Parser] Step 4: Generating test cases...")
                test_cases = await self._generate_test_cases(stories, criteria)
                logger.info(f"[PRD Parser] Generated {len(test_cases)} test cases")
            
            # Calculate processing time
            processing_time_ms = int((time.time() - start_time) * 1000)
            
            return PRDParseResult(
                success=True,
                title=title,
                description=structure.get("sections", [{}])[0].get("content", "")[:200],
                sections=sections,
                user_stories=stories,
                acceptance_criteria=criteria,
                test_cases=test_cases,
                story_count=len(stories),
                criteria_count=len(criteria),
                test_case_count=len(test_cases),
                processing_time_ms=processing_time_ms,
                subagent_tasks=tasks,
                parallel_execution_used=use_parallel,
                errors=errors,
            )
            
        except Exception as e:
            logger.error(f"[PRD Parser] Error: {e}")
            processing_time_ms = int((time.time() - start_time) * 1000)
            return PRDParseResult(
                success=False,
                title=title,
                processing_time_ms=processing_time_ms,
                errors=[str(e)],
            )

    async def _run_parallel_extraction(
        self,
        section_content: str,
        use_parallel: bool = True
    ) -> tuple[List[ExtractedUserStory], List[ExtractedCriteria], Dict[str, Any]]:
        """
        Run extraction subagents in parallel (or sequentially if disabled).

        Following Anthropic's pattern of spawning 3-5 subagents in parallel.

        Args:
            section_content: The PRD content to extract from
            use_parallel: Whether to run agents in parallel

        Returns:
            Tuple of (stories, criteria, edge_cases)
        """
        logger.info(f"[PRD Parser] Running extraction (parallel={use_parallel})")

        # Create subagents
        story_agent = create_story_extractor_agent()
        criteria_agent = create_criteria_extractor_agent()
        edge_case_agent = create_edge_case_analyzer_agent()

        async def extract_stories() -> List[ExtractedUserStory]:
            """Run story extractor agent"""
            try:
                logger.info("[PRD Parser] Story Extractor starting...")
                result = await Runner.run(
                    story_agent,
                    input=f"Extract user stories from this PRD:\n\n{section_content}"
                )
                response_text = result.final_output or ""
                stories_data = self._parse_json_response(response_text)

                stories = []
                for i, s in enumerate(stories_data if isinstance(stories_data, list) else []):
                    self._story_counter += 1
                    stories.append(ExtractedUserStory(
                        id=s.get("id", f"US-{self._story_counter:03d}"),
                        title=s.get("title", "Untitled Story"),
                        description=s.get("description", ""),
                        as_a=s.get("as_a"),
                        i_want=s.get("i_want"),
                        so_that=s.get("so_that"),
                        priority=StoryPriority(s.get("priority", "medium")),
                        tags=s.get("tags", []),
                        confidence=s.get("confidence", 0.8),
                    ))
                logger.info(f"[PRD Parser] Story Extractor found {len(stories)} stories")
                return stories
            except Exception as e:
                logger.error(f"[PRD Parser] Story extraction error: {e}")
                return []

        async def extract_criteria() -> List[ExtractedCriteria]:
            """Run criteria extractor agent"""
            try:
                logger.info("[PRD Parser] Criteria Extractor starting...")
                result = await Runner.run(
                    criteria_agent,
                    input=f"Extract acceptance criteria from this PRD:\n\n{section_content}"
                )
                response_text = result.final_output or ""
                criteria_data = self._parse_json_response(response_text)

                criteria = []
                for c in criteria_data if isinstance(criteria_data, list) else []:
                    self._criteria_counter += 1
                    criteria.append(ExtractedCriteria(
                        id=c.get("id", f"AC-{self._criteria_counter:03d}"),
                        story_id=c.get("story_id", "US-000"),
                        description=c.get("description", ""),
                        given=c.get("given"),
                        when=c.get("when"),
                        then=c.get("then"),
                        priority=CriteriaPriority(c.get("priority", "must")),
                        is_edge_case=c.get("is_edge_case", False),
                        confidence=c.get("confidence", 0.8),
                    ))
                logger.info(f"[PRD Parser] Criteria Extractor found {len(criteria)} criteria")
                return criteria
            except Exception as e:
                logger.error(f"[PRD Parser] Criteria extraction error: {e}")
                return []

        async def analyze_edge_cases() -> Dict[str, Any]:
            """Run edge case analyzer agent"""
            try:
                logger.info("[PRD Parser] Edge Case Analyzer starting...")
                result = await Runner.run(
                    edge_case_agent,
                    input=f"Analyze edge cases and risks in this PRD:\n\n{section_content}"
                )
                response_text = result.final_output or ""
                edge_data = self._parse_json_response(response_text)

                if isinstance(edge_data, dict):
                    logger.info(f"[PRD Parser] Edge Case Analyzer found {len(edge_data.get('edge_cases', []))} edge cases")
                    return edge_data
                return {"edge_cases": [], "risks": [], "missing_requirements": []}
            except Exception as e:
                logger.error(f"[PRD Parser] Edge case analysis error: {e}")
                return {"edge_cases": [], "risks": [], "missing_requirements": []}

        # Execute in parallel or sequentially
        if use_parallel:
            logger.info("[PRD Parser] Executing subagents in PARALLEL...")
            stories, criteria, edge_cases = await asyncio.gather(
                extract_stories(),
                extract_criteria(),
                analyze_edge_cases(),
            )
        else:
            logger.info("[PRD Parser] Executing subagents SEQUENTIALLY...")
            stories = await extract_stories()
            criteria = await extract_criteria()
            edge_cases = await analyze_edge_cases()

        return stories, criteria, edge_cases

    async def _generate_test_cases(
        self,
        stories: List[ExtractedUserStory],
        criteria: List[ExtractedCriteria]
    ) -> List[GeneratedTestCase]:
        """
        Generate test cases from extracted stories and criteria.

        Uses the Test Case Generator agent with gpt-5.4 (thinking model)
        for complex test design reasoning.

        Args:
            stories: Extracted user stories
            criteria: Extracted acceptance criteria

        Returns:
            List of generated test cases
        """
        if not stories and not criteria:
            logger.info("[PRD Parser] No stories or criteria to generate tests from")
            return []

        logger.info(f"[PRD Parser] Generating test cases from {len(stories)} stories, {len(criteria)} criteria")

        # Create test case generator agent
        test_agent = create_test_case_generator_agent()

        # Build context for test generation
        stories_context = "\n".join([
            f"- {s.id}: {s.title} - As a {s.as_a}, I want {s.i_want}, so that {s.so_that}"
            for s in stories
        ])

        criteria_context = "\n".join([
            f"- {c.id} ({c.story_id}): Given {c.given}, When {c.when}, Then {c.then}"
            for c in criteria
        ])

        input_prompt = f"""Generate comprehensive test cases for the following requirements:

## User Stories:
{stories_context if stories_context else "No explicit user stories provided."}

## Acceptance Criteria:
{criteria_context if criteria_context else "No explicit acceptance criteria provided."}

Generate test cases that cover:
1. Happy path scenarios for each user story
2. Edge cases and error handling
3. Boundary conditions
4. Integration between related features

Return a JSON array of test cases."""

        try:
            result = await Runner.run(
                test_agent,
                input=input_prompt
            )
            response_text = result.final_output or ""
            test_data = self._parse_json_response(response_text)

            test_cases = []
            for tc in test_data if isinstance(test_data, list) else []:
                self._test_counter += 1
                test_cases.append(GeneratedTestCase(
                    id=tc.get("id", f"TC-{self._test_counter:03d}"),
                    title=tc.get("title", "Untitled Test"),
                    description=tc.get("description", ""),
                    story_ids=tc.get("story_ids", []),
                    criteria_ids=tc.get("criteria_ids", []),
                    preconditions=tc.get("preconditions", []),
                    steps=tc.get("steps", []),
                    expected_result=tc.get("expected_result", ""),
                    test_type=tc.get("test_type", "functional"),
                    priority=StoryPriority(tc.get("priority", "medium")),
                    target_app=tc.get("target_app"),
                    device_requirements=tc.get("device_requirements"),
                ))

            logger.info(f"[PRD Parser] Generated {len(test_cases)} test cases")
            return test_cases

        except Exception as e:
            logger.error(f"[PRD Parser] Test generation error: {e}")
            return []

    def _parse_json_response(self, response_text: str) -> Any:
        """
        Parse JSON from LLM response text.

        Handles:
        - Pure JSON responses
        - JSON wrapped in markdown code blocks (```json ... ```)
        - JSON embedded in prose

        Args:
            response_text: Raw LLM response text

        Returns:
            Parsed JSON data (list or dict)
        """
        if not response_text:
            return []

        text = response_text.strip()

        # Try to extract JSON from markdown code blocks
        import re

        # Pattern 1: ```json ... ```
        json_block_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if json_block_match:
            text = json_block_match.group(1).strip()

        # Pattern 2: Look for JSON array or object
        if not text.startswith('[') and not text.startswith('{'):
            # Try to find JSON array
            array_match = re.search(r'(\[[\s\S]*\])', text)
            if array_match:
                text = array_match.group(1)
            else:
                # Try to find JSON object
                obj_match = re.search(r'(\{[\s\S]*\})', text)
                if obj_match:
                    text = obj_match.group(1)

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"[PRD Parser] JSON parse error: {e}")
            logger.debug(f"[PRD Parser] Failed to parse: {text[:200]}...")
            return []


# MCP Tool Interface - for future agent calling
async def parse_prd_mcp(
    prd_content: str,
    title: str = "Untitled PRD",
    options: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    MCP-compatible tool interface for PRD parsing.

    This is the clean async function interface that future agents can call
    through the MCP protocol.

    Args:
        prd_content: The raw PRD text to parse
        title: Document title
        options: Optional parsing configuration
            - include_edge_cases: bool (default True)
            - include_test_cases: bool (default True)
            - parallel_execution: bool (default True)
            - max_stories: int (default 50)

    Returns:
        Dictionary with parsing results (JSON-serializable for MCP)
    """
    service = PRDParserService()
    result = await service.parse(prd_content, title, options)

    # Convert to JSON-serializable dict
    return {
        "success": result.success,
        "title": result.title,
        "version": result.version,
        "description": result.description,
        "user_stories": [s.model_dump() for s in result.user_stories],
        "acceptance_criteria": [c.model_dump() for c in result.acceptance_criteria],
        "test_cases": [tc.model_dump() for tc in result.test_cases],
        "story_count": result.story_count,
        "criteria_count": result.criteria_count,
        "test_case_count": result.test_case_count,
        "processing_time_ms": result.processing_time_ms,
        "parallel_execution_used": result.parallel_execution_used,
        "timestamp": result.timestamp,
        "errors": result.errors,
    }


__all__ = ["PRDParserService", "parse_prd_mcp"]
