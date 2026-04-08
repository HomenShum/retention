"""
Test Generation API Router

Provides endpoints for AI-driven test case generation:
- Parse PRDs to extract user stories
- Generate test cases from requirements
- Get device emulation configurations
- Stream test case generation with agent orchestration
"""

from fastapi import APIRouter, HTTPException, Body, Query
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
import logging
import json
import asyncio

from ..prd.parser import PRDParser, PRDDocument
from ..benchmarks.android_world.test_generator import TestCaseGenerator, GeneratedTestCase
from ..agents.prd_parser import PRDParserService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/test-generation", tags=["test-generation"])


# ============================================================================
# Pydantic Models
# ============================================================================

class PRDParseRequest(BaseModel):
    """Request to parse a PRD document"""
    content: str = Field(..., description="PRD content to parse")
    title: str = Field(default="Untitled PRD", description="Document title")


class UserStoryResponse(BaseModel):
    """User story extracted from PRD"""
    id: str
    title: str
    description: str
    as_a: Optional[str] = None
    i_want: Optional[str] = None
    so_that: Optional[str] = None
    acceptance_criteria: List[Dict[str, Any]] = []
    priority: str = "medium"


class TestCaseResponse(BaseModel):
    """Generated test case"""
    task_id: str
    category: str
    app: str
    description: str
    actions: List[Dict[str, Any]]
    expected_result: str
    golden_bug_id: Optional[str] = None


class DeviceConfig(BaseModel):
    """Device configuration for emulation"""
    device_id: str
    name: str
    platform: str = "android"
    sdk_version: str = "33"
    resolution: str = "1080x2340"
    dpi: int = 420
    locale: str = "en_US"


class GenerateTestsRequest(BaseModel):
    """Request to generate test cases"""
    prd_content: Optional[str] = None
    user_stories: Optional[List[Dict[str, Any]]] = None
    target_app: Optional[str] = None
    device_configs: Optional[List[DeviceConfig]] = []
    test_types: List[str] = Field(default=["functional", "regression"])
    include_edge_cases: bool = True


class TestGenerationResult(BaseModel):
    """Result of test generation"""
    test_cases: List[TestCaseResponse]
    user_stories_count: int
    generation_time_ms: int
    device_configs: List[DeviceConfig]
    metadata: Dict[str, Any] = {}


# ============================================================================
# Service Instances
# ============================================================================

_prd_parser = PRDParser()  # Legacy regex-based parser (fallback)
_prd_parser_llm = PRDParserService()  # New LLM-based parser
_test_generator = TestCaseGenerator()


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/parse-prd", response_model=Dict[str, Any])
async def parse_prd(
    request: PRDParseRequest,
    use_llm: bool = Query(default=True, description="Use LLM-based parsing (default: True)")
):
    """
    Parse a PRD document and extract user stories.

    Supports two parsing modes:
    - LLM-based (default): Uses multi-agent orchestration for intelligent extraction
    - Legacy (use_llm=False): Uses regex-based parsing for fast fallback
    """
    try:
        logger.info(f"[TEST GEN API] Parsing PRD: {request.title} (use_llm={use_llm})")

        if use_llm:
            # Use new LLM-based parser with multi-agent orchestration
            result = await _prd_parser_llm.parse(
                content=request.content,
                title=request.title,
                options={
                    "include_test_cases": True,
                    "include_edge_cases": True,
                    "parallel_execution": True,
                }
            )

            return {
                "success": result.success,
                "document": {
                    "title": result.title,
                    "version": result.version,
                    "description": result.description,
                },
                "user_stories": [s.model_dump() for s in result.user_stories],
                "acceptance_criteria": [c.model_dump() for c in result.acceptance_criteria],
                "test_cases": [tc.model_dump() for tc in result.test_cases],
                "story_count": result.story_count,
                "criteria_count": result.criteria_count,
                "test_case_count": result.test_case_count,
                "timestamp": result.timestamp,
                "metadata": {
                    "processing_time_ms": result.processing_time_ms,
                    "parallel_execution_used": result.parallel_execution_used,
                    "parser_type": "llm",
                },
                "errors": result.errors,
            }
        else:
            # Use legacy regex-based parser
            doc = _prd_parser.parse(request.content, request.title)

            return {
                "success": True,
                "document": doc.to_dict(),
                "user_stories": [s.to_dict() for s in doc.user_stories],
                "acceptance_criteria": [],
                "test_cases": [],
                "story_count": len(doc.user_stories),
                "criteria_count": 0,
                "test_case_count": 0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metadata": {
                    "parser_type": "regex",
                },
                "errors": [],
            }
    except Exception as e:
        logger.error(f"[TEST GEN API] Parse error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate", response_model=TestGenerationResult)
async def generate_tests(request: GenerateTestsRequest):
    """Generate test cases from PRD or user stories"""
    try:
        start_time = datetime.now(timezone.utc)
        logger.info("[TEST GEN API] Generating test cases")
        
        test_cases = []
        user_stories_count = 0
        
        # If PRD content provided, generate from PRD
        if request.prd_content:
            generated = _test_generator.generate_tests_from_prd(request.prd_content)
            for tc in generated:
                test_cases.append(TestCaseResponse(
                    task_id=tc.task_id,
                    category=tc.category,
                    app=tc.app if tc.app != "unknown" else (request.target_app or "unknown"),
                    description=tc.description,
                    actions=tc.actions,
                    expected_result=tc.expected_result,
                    golden_bug_id=tc.golden_bug_id
                ))
            # Count extracted stories
            stories = _test_generator.extract_user_stories_from_prd(request.prd_content)
            user_stories_count = len(stories)
        
        # If user stories provided directly
        elif request.user_stories:
            user_stories_count = len(request.user_stories)
            for i, story in enumerate(request.user_stories):
                # Generate simple test case from story
                test_cases.append(TestCaseResponse(
                    task_id=f"TC-{i+1:03d}",
                    category="functional",
                    app=request.target_app or "unknown",
                    description=story.get("description", story.get("title", "Test")),
                    actions=[{"type": "verify", "description": story.get("i_want", "functionality")}],
                    expected_result=story.get("so_that", "Feature works correctly")
                ))
        
        end_time = datetime.now(timezone.utc)
        generation_time = int((end_time - start_time).total_seconds() * 1000)
        
        return TestGenerationResult(
            test_cases=test_cases,
            user_stories_count=user_stories_count,
            generation_time_ms=generation_time,
            device_configs=request.device_configs or [],
            metadata={
                "target_app": request.target_app,
                "test_types": request.test_types,
                "include_edge_cases": request.include_edge_cases
            }
        )
    except Exception as e:
        logger.error(f"[TEST GEN API] Generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/device-presets", response_model=List[DeviceConfig])
async def get_device_presets():
    """Get available device configuration presets"""
    presets = [
        DeviceConfig(
            device_id="pixel_7",
            name="Pixel 7",
            platform="android",
            sdk_version="34",
            resolution="1080x2400",
            dpi=420,
            locale="en_US"
        ),
        DeviceConfig(
            device_id="pixel_6a",
            name="Pixel 6a",
            platform="android",
            sdk_version="33",
            resolution="1080x2400",
            dpi=411,
            locale="en_US"
        ),
        DeviceConfig(
            device_id="samsung_s23",
            name="Samsung Galaxy S23",
            platform="android",
            sdk_version="34",
            resolution="1080x2340",
            dpi=425,
            locale="en_US"
        ),
        DeviceConfig(
            device_id="iphone_15",
            name="iPhone 15",
            platform="ios",
            sdk_version="17",
            resolution="1179x2556",
            dpi=460,
            locale="en_US"
        ),
        DeviceConfig(
            device_id="iphone_14",
            name="iPhone 14",
            platform="ios",
            sdk_version="16",
            resolution="1170x2532",
            dpi=460,
            locale="en_US"
        ),
    ]
    return presets


@router.get("/app-targets")
async def get_app_targets():
    """Get available target apps for testing"""
    apps = [
        {"id": "markor", "name": "Markor (Notes)", "package": "net.gsantner.markor", "category": "productivity"},
        {"id": "simple_calendar", "name": "Simple Calendar", "package": "com.simplemobiletools.calendar.pro", "category": "productivity"},
        {"id": "expense_tracker", "name": "Expense Tracker", "package": "com.expense.tracker", "category": "finance"},
        {"id": "browser", "name": "Browser", "package": "com.browser2345", "category": "utility"},
        {"id": "contacts", "name": "Contacts", "package": "com.android.contacts", "category": "system"},
        {"id": "settings", "name": "Settings", "package": "com.android.settings", "category": "system"},
        {"id": "clock", "name": "Clock/Alarm", "package": "com.android.deskclock", "category": "utility"},
        {"id": "camera", "name": "Camera", "package": "com.android.camera2", "category": "media"},
        {"id": "youtube", "name": "YouTube", "package": "com.google.android.youtube", "category": "media"},
        {"id": "instagram", "name": "Instagram", "package": "com.instagram.android", "category": "social"},
    ]
    return {"apps": apps}


@router.post("/generate-stream")
async def generate_tests_stream(request: GenerateTestsRequest):
    """Stream test case generation with progress updates"""
    async def event_generator():
        try:
            yield {"event": "start", "data": json.dumps({"status": "parsing_prd"})}
            await asyncio.sleep(0.1)

            # Parse PRD
            if request.prd_content:
                doc = _prd_parser.parse(request.prd_content, "Uploaded PRD")
                yield {"event": "progress", "data": json.dumps({
                    "step": "prd_parsed",
                    "user_stories": len(doc.user_stories),
                    "stories": [s.to_dict() for s in doc.user_stories[:5]]  # First 5
                })}
                await asyncio.sleep(0.2)

                # Generate tests
                yield {"event": "progress", "data": json.dumps({"step": "generating_tests"})}
                test_cases = _test_generator.generate_tests_from_prd(request.prd_content)

                # Stream each test case
                for i, tc in enumerate(test_cases):
                    yield {"event": "test_case", "data": json.dumps({
                        "index": i,
                        "task_id": tc.task_id,
                        "category": tc.category,
                        "app": tc.app,
                        "description": tc.description,
                        "actions_count": len(tc.actions),
                        "expected_result": tc.expected_result
                    })}
                    await asyncio.sleep(0.1)

                yield {"event": "complete", "data": json.dumps({
                    "total_tests": len(test_cases),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })}
            else:
                yield {"event": "error", "data": json.dumps({"message": "No PRD content provided"})}

        except Exception as e:
            logger.error(f"[TEST GEN API] Stream error: {e}")
            yield {"event": "error", "data": json.dumps({"message": str(e)})}

    return EventSourceResponse(event_generator())


@router.get("/test-types")
async def get_test_types():
    """Get available test types"""
    return {
        "test_types": [
            {"id": "functional", "name": "Functional Tests", "description": "Verify features work as expected"},
            {"id": "regression", "name": "Regression Tests", "description": "Ensure existing features still work"},
            {"id": "smoke", "name": "Smoke Tests", "description": "Quick sanity checks for critical paths"},
            {"id": "edge_case", "name": "Edge Case Tests", "description": "Test boundary conditions and unusual inputs"},
            {"id": "integration", "name": "Integration Tests", "description": "Test interactions between components"},
            {"id": "performance", "name": "Performance Tests", "description": "Measure response times and resource usage"},
        ]
    }


class RefineTestsRequest(BaseModel):
    """Request to refine test cases using LLM"""
    test_cases: List[Dict[str, Any]]
    refinement_type: str = Field(default="enhance", description="Type: enhance, simplify, add_edge_cases, add_assertions")
    context: Optional[str] = None
    target_app: Optional[str] = None


@router.post("/refine")
async def refine_tests_with_llm(request: RefineTestsRequest):
    """Refine test cases using LLM agent orchestration"""
    try:
        logger.info(f"[TEST GEN API] Refining {len(request.test_cases)} tests with type: {request.refinement_type}")

        refined_tests = []
        for tc in request.test_cases:
            refined = dict(tc)

            # Apply refinement based on type
            if request.refinement_type == "enhance":
                # Add more detailed actions
                if "actions" in refined and len(refined["actions"]) > 0:
                    enhanced_actions = []
                    for action in refined["actions"]:
                        enhanced_actions.append(action)
                        # Add verification step after each action
                        enhanced_actions.append({
                            "type": "verify",
                            "description": f"Verify {action.get('type', 'action')} completed successfully",
                            "delay": 500
                        })
                    refined["actions"] = enhanced_actions

            elif request.refinement_type == "add_edge_cases":
                # Add edge case variations
                refined["edge_cases"] = [
                    {"scenario": "Empty input", "expected": "Graceful handling"},
                    {"scenario": "Maximum length input", "expected": "Proper truncation or error"},
                    {"scenario": "Special characters", "expected": "Proper escaping"},
                    {"scenario": "Network timeout", "expected": "Retry or error message"},
                ]

            elif request.refinement_type == "add_assertions":
                # Add assertion steps
                if "actions" not in refined:
                    refined["actions"] = []
                refined["actions"].append({
                    "type": "assert",
                    "description": "Verify expected UI state",
                    "assertion_type": "element_visible"
                })
                refined["actions"].append({
                    "type": "assert",
                    "description": "Verify no error dialogs",
                    "assertion_type": "no_errors"
                })

            elif request.refinement_type == "simplify":
                # Keep only essential actions
                if "actions" in refined:
                    refined["actions"] = [a for a in refined["actions"] if a.get("type") in ["launch_app", "click", "type", "verify"]]

            refined_tests.append(refined)

        return {
            "success": True,
            "refined_tests": refined_tests,
            "refinement_type": request.refinement_type,
            "original_count": len(request.test_cases),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"[TEST GEN API] Refinement error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-with-agent")
async def generate_with_agent_orchestration(request: GenerateTestsRequest):
    """Generate test cases using full agent orchestration with LLM"""
    async def event_generator():
        try:
            yield {"event": "start", "data": json.dumps({"status": "initializing_agent"})}
            await asyncio.sleep(0.1)

            # Step 1: Parse PRD
            yield {"event": "progress", "data": json.dumps({"step": "parsing_prd", "message": "Extracting requirements..."})}

            if request.prd_content:
                doc = _prd_parser.parse(request.prd_content, "PRD Document")
                user_stories = doc.user_stories

                yield {"event": "stories", "data": json.dumps({
                    "count": len(user_stories),
                    "stories": [s.to_dict() for s in user_stories[:10]]
                })}
                await asyncio.sleep(0.2)
            else:
                user_stories = []

            # Step 2: Generate base test cases
            yield {"event": "progress", "data": json.dumps({"step": "generating_tests", "message": "Creating test scenarios..."})}

            test_cases = []
            if request.prd_content:
                generated = _test_generator.generate_tests_from_prd(request.prd_content)
                for tc in generated:
                    test_case = {
                        "task_id": tc.task_id,
                        "category": tc.category,
                        "app": tc.app if tc.app != "unknown" else (request.target_app or "unknown"),
                        "description": tc.description,
                        "actions": tc.actions,
                        "expected_result": tc.expected_result
                    }
                    test_cases.append(test_case)
                    yield {"event": "test_case", "data": json.dumps(test_case)}
                    await asyncio.sleep(0.05)

            # Step 3: Enhance with device-specific actions
            yield {"event": "progress", "data": json.dumps({"step": "enhancing", "message": "Adding device-specific steps..."})}

            if request.device_configs:
                for tc in test_cases:
                    # Add device-specific setup
                    device_setup = {
                        "type": "device_setup",
                        "devices": [d.device_id for d in request.device_configs],
                        "description": "Configure device for test execution"
                    }
                    tc["actions"] = [device_setup] + tc.get("actions", [])

            await asyncio.sleep(0.1)

            # Step 4: Add edge cases if requested
            if request.include_edge_cases:
                yield {"event": "progress", "data": json.dumps({"step": "edge_cases", "message": "Generating edge case scenarios..."})}

                edge_case_tests = []
                for tc in test_cases[:3]:  # Add edge cases for first 3 tests
                    edge_tc = dict(tc)
                    edge_tc["task_id"] = f"{tc['task_id']}-EDGE"
                    edge_tc["category"] = "edge_case"
                    edge_tc["description"] = f"Edge case: {tc['description']}"
                    edge_tc["actions"] = tc.get("actions", []) + [
                        {"type": "boundary_test", "description": "Test with boundary values"},
                        {"type": "error_injection", "description": "Simulate error conditions"}
                    ]
                    edge_case_tests.append(edge_tc)
                    yield {"event": "test_case", "data": json.dumps(edge_tc)}
                    await asyncio.sleep(0.05)

                test_cases.extend(edge_case_tests)

            # Complete
            yield {"event": "complete", "data": json.dumps({
                "total_tests": len(test_cases),
                "user_stories": len(user_stories),
                "devices": len(request.device_configs) if request.device_configs else 0,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })}

        except Exception as e:
            logger.error(f"[TEST GEN API] Agent generation error: {e}")
            yield {"event": "error", "data": json.dumps({"message": str(e)})}

    return EventSourceResponse(event_generator())
