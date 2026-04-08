"""
Pydantic models for the QA Pipeline intermediate data formats.

CrawlResult  -> WorkflowResult -> TestSuiteResult
"""

from typing import Dict, List, Optional
from pydantic import BaseModel


# ── Crawl Stage ──────────────────────────────────────────────────────────────

class ComponentInfo(BaseModel):
    element_id: int
    element_type: str               # BTN, INPUT, TOGGLE, NAV, TXT, LIST
    text: str
    coordinates: Dict[str, int]     # {x, y, width, height}
    is_interactive: bool
    leads_to: Optional[str] = None  # screen_id if navigation discovered


class ScreenNode(BaseModel):
    screen_id: str                        # "screen_001"
    screen_name: str                      # "Contact List"
    screenshot_path: str                  # Path to annotated screenshot
    screenshot_description: str           # Vision analysis text
    navigation_depth: int                 # 0=home, 1=first click, etc.
    parent_screen_id: Optional[str] = None
    trigger_action: Optional[str] = None  # "Clicked FAB (+) button"
    components: List[ComponentInfo] = []


class ScreenTransition(BaseModel):
    from_screen: str
    to_screen: str
    action: str                           # "Tap 'Create contact' FAB"
    component_id: Optional[int] = None


class CrawlResult(BaseModel):
    app_name: str
    package_name: str
    screens: List[ScreenNode] = []
    transitions: List[ScreenTransition] = []
    total_components: int = 0
    total_screens: int = 0


# ── Workflow Stage ───────────────────────────────────────────────────────────

class WorkflowStep(BaseModel):
    step_number: int
    screen_id: str
    action: str                     # "Tap the FAB button"
    expected_result: str            # "New contact form appears"


class Workflow(BaseModel):
    workflow_id: str                # "wf_001"
    name: str                      # "Create New Contact"
    description: str
    screens_involved: List[str]
    steps: List[WorkflowStep]
    complexity: str                # "simple" | "moderate" | "complex"


class WorkflowResult(BaseModel):
    app_name: str
    workflows: List[Workflow] = []


# ── Test Case Stage ──────────────────────────────────────────────────────────

class TestStep(BaseModel):
    step_number: int
    action: str
    expected_result: str


class TestCase(BaseModel):
    test_id: str                           # "tc_001"
    name: str                              # "Verify new contact creation"
    workflow_id: str
    workflow_name: str
    description: str
    preconditions: List[str] = []
    steps: List[TestStep] = []
    expected_result: str
    priority: str                          # "P0" | "P1" | "P2" | "P3"
    category: str                          # "smoke" | "regression" | "edge_case" | "negative" | "accessibility"
    pressure_point: Optional[str] = None   # "Empty name field", "Special characters"


class WorkflowSummary(BaseModel):
    workflow_id: str
    name: str
    test_count: int


class TestSuiteResult(BaseModel):
    app_name: str
    test_cases: List[TestCase] = []
    workflows: List[WorkflowSummary] = []
    total_tests: int = 0
    by_workflow: Dict[str, int] = {}
    by_priority: Dict[str, int] = {}
    by_category: Dict[str, int] = {}
