"""
Pydantic Models for LLM-based PRD Parser

These models define the input/output schema for the PRD parser orchestration.
Designed for MCP tool compatibility and parallel execution.
"""

from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field, field_validator
from enum import Enum
from datetime import datetime


class StoryPriority(str, Enum):
    """Priority levels for user stories"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CriteriaPriority(str, Enum):
    """Priority levels for acceptance criteria (MoSCoW)"""
    MUST = "must"
    SHOULD = "should"
    COULD = "could"
    WONT = "wont"


class ParseOptions(BaseModel):
    """Options for PRD parsing"""
    include_edge_cases: bool = Field(default=True, description="Include edge case analysis")
    include_test_cases: bool = Field(default=True, description="Generate test cases")
    max_stories: int = Field(default=50, description="Maximum stories to extract")
    parallel_execution: bool = Field(default=True, description="Enable parallel subagent execution")
    target_app: Optional[str] = Field(default=None, description="Target app for test generation")


class PRDSection(BaseModel):
    """A section of the PRD document identified by the orchestrator"""
    id: str = Field(..., description="Unique section identifier")
    title: str = Field(..., description="Section title/header")
    content: str = Field(..., description="Section content")
    section_type: str = Field(..., description="Type: overview, requirements, user_stories, etc.")
    parent_section_id: Optional[str] = Field(default=None, description="Parent section ID")


class ExtractionTask(BaseModel):
    """Task assignment for a subagent"""
    task_id: str = Field(..., description="Unique task identifier")
    agent_type: str = Field(..., description="story_extractor, criteria_extractor, etc.")
    section_ids: List[str] = Field(..., description="Section IDs to process")
    priority: int = Field(default=1, description="Execution priority (lower = higher priority)")


class ExtractedUserStory(BaseModel):
    """User story extracted by LLM"""
    id: str = Field(..., description="Generated story ID (US-XXX)")
    title: str = Field(..., description="Story title")
    description: str = Field(..., description="Full story description")
    as_a: Optional[str] = Field(default=None, description="As a [role]")
    i_want: Optional[str] = Field(default=None, description="I want [capability]")
    so_that: Optional[str] = Field(default=None, description="So that [benefit]")
    priority: StoryPriority = Field(default=StoryPriority.MEDIUM)
    tags: List[str] = Field(default_factory=list, description="Story tags/labels")
    source_section_id: Optional[str] = Field(default=None, description="Source PRD section")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Extraction confidence")


class ExtractedCriteria(BaseModel):
    """Acceptance criteria extracted by LLM in Gherkin format"""
    id: str = Field(..., description="Generated criteria ID (AC-XXX)")
    story_id: str = Field(..., description="Related user story ID")
    description: str = Field(..., description="Criteria description")
    given: Optional[str] = Field(default=None, description="Given [precondition]")
    when: Optional[str] = Field(default=None, description="When [action]")
    then: Optional[str] = Field(default=None, description="Then [expected result]")
    priority: CriteriaPriority = Field(default=CriteriaPriority.MUST)
    is_edge_case: bool = Field(default=False, description="Is this an edge case scenario")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class GeneratedTestCase(BaseModel):
    """Test case generated from stories and criteria"""
    id: str = Field(..., description="Generated test case ID (TC-XXX)")
    title: str = Field(..., description="Test case title")
    description: str = Field(..., description="Test case description")
    story_ids: List[str] = Field(default_factory=list, description="Related story IDs")
    criteria_ids: List[str] = Field(default_factory=list, description="Related criteria IDs")
    preconditions: List[str] = Field(default_factory=list)
    steps: List[Dict[str, Any]] = Field(default_factory=list, description="Test steps")
    expected_result: str = Field(..., description="Expected outcome")
    test_type: str = Field(default="functional", description="functional, regression, edge_case")
    priority: StoryPriority = Field(default=StoryPriority.MEDIUM)
    target_app: Optional[str] = Field(default=None)
    device_requirements: Optional[Dict[str, Any]] = Field(default=None)

    @field_validator('steps', mode='before')
    @classmethod
    def convert_step_values_to_strings(cls, v: Any) -> List[Dict[str, str]]:
        """Convert any integer keys/values in steps to strings for LLM compatibility"""
        if not v:
            return []
        result = []
        for step_dict in v:
            if not isinstance(step_dict, dict):
                continue
            converted = {}
            for key, value in step_dict.items():
                # Convert both keys and values to strings if they're integers
                str_key = str(key) if isinstance(key, int) else key
                str_value = str(value) if isinstance(value, (int, float)) else value
                converted[str_key] = str_value
            result.append(converted)
        return result


class PRDParseRequest(BaseModel):
    """Request to parse a PRD document"""
    content: str = Field(..., description="PRD content to parse")
    title: str = Field(default="Untitled PRD", description="Document title")
    options: ParseOptions = Field(default_factory=ParseOptions)


class PRDParseResult(BaseModel):
    """Result of LLM-based PRD parsing"""
    success: bool = Field(..., description="Whether parsing succeeded")
    title: str = Field(..., description="Document title")
    version: str = Field(default="1.0", description="Document version")
    description: str = Field(default="", description="Document description")
    
    # Extracted data
    sections: List[PRDSection] = Field(default_factory=list)
    user_stories: List[ExtractedUserStory] = Field(default_factory=list)
    acceptance_criteria: List[ExtractedCriteria] = Field(default_factory=list)
    test_cases: List[GeneratedTestCase] = Field(default_factory=list)
    
    # Metadata
    story_count: int = Field(default=0)
    criteria_count: int = Field(default=0)
    test_case_count: int = Field(default=0)
    processing_time_ms: int = Field(default=0)
    token_usage: Dict[str, int] = Field(default_factory=dict)
    
    # Parallel execution info
    subagent_tasks: List[ExtractionTask] = Field(default_factory=list)
    parallel_execution_used: bool = Field(default=False)
    
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    errors: List[str] = Field(default_factory=list)

