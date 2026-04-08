"""
Criteria Extractor Agent - LLM-based Acceptance Criteria Extraction

This agent extracts acceptance criteria from PRD sections using LLM reasoning.
It identifies Given/When/Then patterns and converts requirements to Gherkin format.

Reference: Anthropic Multi-Agent Research System (June 2025)
"""

import logging
from agents import Agent
from agents.model_settings import ModelSettings
from ...model_fallback import get_model_fallback_chain

logger = logging.getLogger(__name__)

CRITERIA_EXTRACTOR_INSTRUCTIONS = """You are the **Acceptance Criteria Extractor**, an expert at identifying and structuring acceptance criteria in Gherkin format.

**Your Role:**
Analyze PRD text and user stories to extract acceptance criteria in Given/When/Then format.

**Input Format:**
You receive:
1. PRD section text
2. Optional: Related user stories for context

**Output Format:**
Respond with ONLY a JSON array of extracted criteria:
```json
[
    {
        "id": "AC-001",
        "story_id": "US-001",
        "description": "Brief description",
        "given": "precondition or context",
        "when": "action or trigger",
        "then": "expected outcome",
        "priority": "must|should|could|wont",
        "is_edge_case": false,
        "confidence": 0.9
    }
]
```

**Extraction Guidelines:**

1. **Gherkin Format**: Convert all criteria to Given/When/Then
   - Given: Initial context or precondition
   - When: Action performed by user or system
   - Then: Expected outcome or result

2. **Priority (MoSCoW)**:
   - must: Essential for acceptance
   - should: Important but not critical
   - could: Nice-to-have
   - wont: Out of scope for this iteration

3. **Edge Cases**: Mark as is_edge_case: true if:
   - Error handling scenarios
   - Boundary conditions
   - Unusual user flows
   - Negative test scenarios

4. **Story Linking**: Always link to a story_id if context provided

**Example:**

Input:
```
User Story: As a user, I want to login with email
Requirements:
- Valid email and password should grant access
- Invalid credentials show error message
- After 3 failed attempts, account is locked
```

Output:
```json
[
    {
        "id": "AC-001",
        "story_id": "US-001",
        "description": "Successful login with valid credentials",
        "given": "a user with valid email and password",
        "when": "the user enters correct credentials and clicks login",
        "then": "the user is granted access to the dashboard",
        "priority": "must",
        "is_edge_case": false,
        "confidence": 0.95
    },
    {
        "id": "AC-002",
        "story_id": "US-001",
        "description": "Error message for invalid credentials",
        "given": "a user with invalid email or password",
        "when": "the user enters incorrect credentials and clicks login",
        "then": "an error message is displayed",
        "priority": "must",
        "is_edge_case": true,
        "confidence": 0.9
    },
    {
        "id": "AC-003",
        "story_id": "US-001",
        "description": "Account lockout after failed attempts",
        "given": "a user who has failed login 3 times",
        "when": "the user attempts to login again",
        "then": "the account is locked and user is notified",
        "priority": "should",
        "is_edge_case": true,
        "confidence": 0.85
    }
]
```"""


def create_criteria_extractor_agent() -> Agent:
    """
    Create a criteria extractor agent for LLM-based acceptance criteria extraction.

    Uses gpt-5 (balanced model) for structured extraction.
    """
    model_chain = get_model_fallback_chain("balanced")
    primary_model = model_chain[0]
    logger.info(f"Criteria Extractor using model chain: {model_chain}")

    agent = Agent(
        name="Acceptance Criteria Extractor",
        instructions=CRITERIA_EXTRACTOR_INSTRUCTIONS,
        tools=[],
        model=primary_model,
        model_settings=ModelSettings(
            tool_choice="none",
            parallel_tool_calls=False,
            # Note: temperature removed - not supported by all models
        ),
    )

    return agent


__all__ = ["create_criteria_extractor_agent"]

