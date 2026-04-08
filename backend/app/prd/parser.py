"""
PRD Parser Module.

Parses Product Requirement Documents to extract:
- User stories
- Acceptance criteria
- Feature descriptions
- Test scenarios
"""

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class StoryType(str, Enum):
    """Type of user story."""
    FEATURE = "feature"
    BUG_FIX = "bug_fix"
    ENHANCEMENT = "enhancement"
    EPIC = "epic"


@dataclass
class AcceptanceCriteria:
    """Represents acceptance criteria for a user story."""
    id: str
    description: str
    given: Optional[str] = None
    when: Optional[str] = None
    then: Optional[str] = None
    priority: str = "must"  # must, should, could


@dataclass
class UserStory:
    """Represents a user story extracted from PRD."""
    id: str
    title: str
    description: str
    story_type: StoryType = StoryType.FEATURE
    as_a: Optional[str] = None  # As a [role]
    i_want: Optional[str] = None  # I want [capability]
    so_that: Optional[str] = None  # So that [benefit]
    acceptance_criteria: List[AcceptanceCriteria] = field(default_factory=list)
    priority: str = "medium"  # high, medium, low
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "story_type": self.story_type.value,
            "as_a": self.as_a,
            "i_want": self.i_want,
            "so_that": self.so_that,
            "acceptance_criteria": [
                {
                    "id": ac.id,
                    "description": ac.description,
                    "given": ac.given,
                    "when": ac.when,
                    "then": ac.then,
                    "priority": ac.priority,
                }
                for ac in self.acceptance_criteria
            ],
            "priority": self.priority,
            "tags": self.tags,
        }


@dataclass
class PRDDocument:
    """Represents a parsed PRD document."""
    title: str
    version: str
    description: str
    user_stories: List[UserStory] = field(default_factory=list)
    raw_content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "version": self.version,
            "description": self.description,
            "user_stories": [s.to_dict() for s in self.user_stories],
            "story_count": len(self.user_stories),
            "metadata": self.metadata,
        }


class PRDParser:
    """Parser for Product Requirement Documents."""

    # Patterns for extracting user stories
    STORY_PATTERNS = [
        r"(?:As a|As an)\s+(.+?),?\s+I want\s+(.+?),?\s+so that\s+(.+?)(?:\.|$)",
        r"User Story[:\s]+(.+?)(?:\n|$)",
        r"Feature[:\s]+(.+?)(?:\n|$)",
        r"##\s+(.+?)(?:\n|$)",  # Markdown headers
    ]

    AC_PATTERNS = [
        r"Given\s+(.+?)\s+When\s+(.+?)\s+Then\s+(.+?)(?:\.|$)",
        r"(?:AC|Acceptance Criteria)[:\s]+(.+?)(?:\n|$)",
        r"(?:- |\* |\d+\. )(.+?)(?:\n|$)",  # Bullet points
    ]

    def __init__(self):
        self.story_counter = 0
        self.ac_counter = 0

    def parse(self, content: str, title: str = "Untitled PRD") -> PRDDocument:
        """Parse PRD content and extract structured data."""
        logger.info(f"[PRD] Parsing document: {title}")

        # Create document
        doc = PRDDocument(
            title=title,
            version="1.0",
            description=self._extract_description(content),
            raw_content=content,
        )

        # Extract user stories
        doc.user_stories = self._extract_user_stories(content)

        logger.info(f"[PRD] Extracted {len(doc.user_stories)} user stories")
        return doc

    def _extract_description(self, content: str) -> str:
        """Extract document description from first paragraph."""
        lines = content.strip().split("\n")
        for line in lines[:5]:
            line = line.strip()
            if line and not line.startswith("#") and len(line) > 20:
                return line[:200]
        return "No description available"

    def _extract_user_stories(self, content: str) -> List[UserStory]:
        """Extract user stories from content."""
        stories = []

        # Try standard "As a... I want... So that..." pattern
        pattern = r"As a[n]?\s+(.+?),?\s+I want\s+(.+?),?\s+so that\s+(.+?)(?:\.|\n|$)"
        for match in re.finditer(pattern, content, re.IGNORECASE | re.DOTALL):
            self.story_counter += 1
            story = UserStory(
                id=f"US-{self.story_counter:03d}",
                title=f"User Story {self.story_counter}",
                description=match.group(0).strip(),
                as_a=match.group(1).strip(),
                i_want=match.group(2).strip(),
                so_that=match.group(3).strip(),
            )
            stories.append(story)

        # Extract from markdown headers if no stories found
        if not stories:
            stories = self._extract_from_markdown(content)

        # Extract acceptance criteria for each story
        for story in stories:
            story.acceptance_criteria = self._extract_acceptance_criteria(
                content, story.description
            )

        return stories

    def _extract_from_markdown(self, content: str) -> List[UserStory]:
        """Extract stories from markdown headers."""
        stories = []

        # Look for ## headers as story titles
        header_pattern = r"##\s+(.+?)(?:\n|$)"
        for match in re.finditer(header_pattern, content):
            self.story_counter += 1
            title = match.group(1).strip()

            # Get content after header until next header
            start = match.end()
            next_header = re.search(r"\n##\s+", content[start:])
            if next_header:
                desc = content[start:start + next_header.start()].strip()
            else:
                desc = content[start:start + 500].strip()

            story = UserStory(
                id=f"US-{self.story_counter:03d}",
                title=title,
                description=desc[:300] if desc else title,
                i_want=title,
            )
            stories.append(story)

        return stories

    def _extract_acceptance_criteria(self, content: str, story_desc: str) -> List[AcceptanceCriteria]:
        """Extract acceptance criteria related to a story."""
        criteria = []

        # Find Given/When/Then patterns
        gherkin_pattern = r"Given\s+(.+?)\s+When\s+(.+?)\s+Then\s+(.+?)(?:\.|\n|$)"
        for match in re.finditer(gherkin_pattern, content, re.IGNORECASE | re.DOTALL):
            self.ac_counter += 1
            ac = AcceptanceCriteria(
                id=f"AC-{self.ac_counter:03d}",
                description=match.group(0).strip()[:100],
                given=match.group(1).strip(),
                when=match.group(2).strip(),
                then=match.group(3).strip(),
            )
            criteria.append(ac)

        # Find bullet-point acceptance criteria
        bullet_pattern = r"(?:- |\* )\s*(?:AC|Criteria|Must|Should|Can)[:.]?\s*(.+?)(?:\n|$)"
        for match in re.finditer(bullet_pattern, content, re.IGNORECASE):
            self.ac_counter += 1
            ac = AcceptanceCriteria(
                id=f"AC-{self.ac_counter:03d}",
                description=match.group(1).strip()[:200],
            )
            criteria.append(ac)

        return criteria[:10]  # Limit to 10 ACs per story
