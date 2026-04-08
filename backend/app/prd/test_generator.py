"""
Test Case Generator Module.

Generates test cases from user stories and acceptance criteria.
Inspired by Specif-AI concepts for AI-driven test generation.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum

from .parser import UserStory, AcceptanceCriteria, PRDDocument

logger = logging.getLogger(__name__)


class TestType(str, Enum):
    """Type of test case."""
    FUNCTIONAL = "functional"
    UI = "ui"
    INTEGRATION = "integration"
    E2E = "e2e"
    ACCESSIBILITY = "accessibility"


class TestPriority(str, Enum):
    """Priority of test case."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class TestStep:
    """Represents a single test step."""
    order: int
    action: str
    expected_result: str
    element_hint: Optional[str] = None  # UI element to interact with


@dataclass
class TestCase:
    """Represents a generated test case."""
    id: str
    title: str
    description: str
    test_type: TestType = TestType.FUNCTIONAL
    priority: TestPriority = TestPriority.MEDIUM
    preconditions: List[str] = field(default_factory=list)
    steps: List[TestStep] = field(default_factory=list)
    expected_outcome: str = ""
    source_story_id: Optional[str] = None
    source_ac_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "test_type": self.test_type.value,
            "priority": self.priority.value,
            "preconditions": self.preconditions,
            "steps": [
                {
                    "order": s.order,
                    "action": s.action,
                    "expected_result": s.expected_result,
                    "element_hint": s.element_hint,
                }
                for s in self.steps
            ],
            "expected_outcome": self.expected_outcome,
            "source_story_id": self.source_story_id,
            "source_ac_id": self.source_ac_id,
            "tags": self.tags,
        }


class TestCaseGenerator:
    """Generates test cases from PRD documents."""

    # Keywords that suggest UI interactions
    UI_KEYWORDS = ["click", "tap", "press", "enter", "type", "select", "scroll", "swipe", "open", "close"]

    # Keywords that suggest verification
    VERIFY_KEYWORDS = ["see", "display", "show", "appear", "visible", "contain", "have", "exist"]

    def __init__(self):
        self.test_counter = 0

    def generate_from_prd(self, prd: PRDDocument) -> List[TestCase]:
        """Generate test cases from a PRD document."""
        logger.info(f"[TEST GEN] Generating tests from PRD: {prd.title}")

        test_cases = []

        for story in prd.user_stories:
            # Generate tests from acceptance criteria
            for ac in story.acceptance_criteria:
                test = self._generate_from_ac(story, ac)
                if test:
                    test_cases.append(test)

            # Generate a general test for the story if no ACs
            if not story.acceptance_criteria:
                test = self._generate_from_story(story)
                if test:
                    test_cases.append(test)

        logger.info(f"[TEST GEN] Generated {len(test_cases)} test cases")
        return test_cases

    def _generate_from_ac(self, story: UserStory, ac: AcceptanceCriteria) -> Optional[TestCase]:
        """Generate a test case from an acceptance criterion."""
        self.test_counter += 1

        steps = []

        # If Gherkin format (Given/When/Then)
        if ac.given and ac.when and ac.then:
            steps.append(TestStep(
                order=1,
                action=f"Setup: {ac.given}",
                expected_result="Precondition met",
            ))
            steps.append(TestStep(
                order=2,
                action=ac.when,
                expected_result="Action performed",
            ))
            steps.append(TestStep(
                order=3,
                action="Verify outcome",
                expected_result=ac.then,
            ))
        else:
            # Parse description for steps
            steps = self._parse_steps_from_text(ac.description)

        return TestCase(
            id=f"TC-{self.test_counter:03d}",
            title=f"Test: {ac.description[:50]}...",
            description=ac.description,
            test_type=self._infer_test_type(ac.description),
            priority=self._map_priority(ac.priority),
            steps=steps,
        )

    def _infer_test_type(self, text: str) -> TestType:
        """Infer test type from text content."""
        text_lower = text.lower()

        if any(kw in text_lower for kw in self.UI_KEYWORDS):
            return TestType.UI
        if "api" in text_lower or "endpoint" in text_lower:
            return TestType.INTEGRATION
        if "flow" in text_lower or "journey" in text_lower:
            return TestType.E2E
        if "accessibility" in text_lower or "a11y" in text_lower:
            return TestType.ACCESSIBILITY

        return TestType.FUNCTIONAL

    def _map_priority(self, priority: str) -> TestPriority:
        """Map story/AC priority to test priority."""
        mapping = {
            "must": TestPriority.CRITICAL,
            "high": TestPriority.HIGH,
            "medium": TestPriority.MEDIUM,
            "should": TestPriority.MEDIUM,
            "could": TestPriority.LOW,
            "low": TestPriority.LOW,
        }
        return mapping.get(priority.lower(), TestPriority.MEDIUM)

    def _parse_steps_from_text(self, text: str) -> List[TestStep]:
        """Parse test steps from free-form text."""
        steps = []

        # Split by common delimiters
        sentences = text.replace(". ", ".\n").split("\n")

        for i, sentence in enumerate(sentences[:5], 1):  # Limit to 5 steps
            sentence = sentence.strip()
            if not sentence or len(sentence) < 10:
                continue

            # Determine if action or verification
            is_verify = any(kw in sentence.lower() for kw in self.VERIFY_KEYWORDS)

            steps.append(TestStep(
                order=i,
                action="Verify: " + sentence if is_verify else sentence,
                expected_result="Verified" if is_verify else "Action completed",
            ))

        return steps

    def _generate_from_story(self, story: UserStory) -> Optional[TestCase]:
        """Generate a test case from a user story."""
        self.test_counter += 1

        steps = []
        if story.i_want:
            steps.append(TestStep(
                order=1,
                action=f"Perform: {story.i_want}",
                expected_result=story.so_that or "Feature works as expected",
            ))

        return TestCase(
            id=f"TC-{self.test_counter:03d}",
            title=f"Test: {story.title}",
            description=story.description,
            test_type=self._infer_test_type(story.description),
            priority=self._map_priority(story.priority),
            steps=steps or self._parse_steps_from_text(story.description),
            expected_outcome=story.so_that or "Feature works correctly",
            source_story_id=story.id,
            tags=story.tags,
        )

