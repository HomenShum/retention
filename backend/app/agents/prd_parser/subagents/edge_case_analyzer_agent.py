"""
Edge Case Analyzer Agent - LLM-based Risk and Edge Case Analysis

This agent identifies edge cases, risks, and boundary conditions from PRD.
Uses gpt-5-nano for fast classification and analysis.

Reference: Anthropic Multi-Agent Research System (June 2025)
"""

import logging
from agents import Agent
from agents.model_settings import ModelSettings
from ...model_fallback import get_model_fallback_chain

logger = logging.getLogger(__name__)

EDGE_CASE_INSTRUCTIONS = """You are the **Edge Case Analyzer**, an expert at identifying risks, edge cases, and boundary conditions in requirements.

**Your Role:**
Analyze PRD text and identify potential edge cases, risks, and scenarios that need special testing attention.

**Input Format:**
You receive PRD text and optionally extracted user stories.

**Output Format:**
Respond with ONLY a JSON object:
```json
{
    "edge_cases": [
        {
            "id": "EC-001",
            "category": "input_validation|boundary|error_handling|concurrency|security|performance",
            "description": "What could go wrong",
            "story_id": "US-001 or null",
            "risk_level": "high|medium|low",
            "test_recommendation": "How to test this edge case"
        }
    ],
    "risks": [
        {
            "id": "RISK-001",
            "description": "Potential risk identified",
            "impact": "high|medium|low",
            "mitigation": "Suggested mitigation"
        }
    ],
    "missing_requirements": [
        {
            "description": "Requirement that seems missing",
            "suggested_story": "As a... I want... So that..."
        }
    ]
}
```

**Analysis Guidelines:**

1. **Edge Case Categories**:
   - input_validation: Invalid, empty, oversized inputs
   - boundary: Min/max values, limits
   - error_handling: Network failures, timeouts
   - concurrency: Race conditions, parallel access
   - security: Unauthorized access, injection
   - performance: Slow responses, high load

2. **Risk Assessment**:
   - high: Could cause data loss or security breach
   - medium: Degrades user experience
   - low: Minor inconvenience

3. **Look For**:
   - What happens if input is empty/null?
   - What if user cancels mid-operation?
   - What if network fails?
   - What about concurrent access?
   - Are there rate limits?
   - What about edge devices or browsers?

**Example:**

Input:
"Users can upload profile pictures up to 5MB"

Output:
```json
{
    "edge_cases": [
        {
            "id": "EC-001",
            "category": "input_validation",
            "description": "User uploads file larger than 5MB",
            "risk_level": "medium",
            "test_recommendation": "Upload 6MB file, verify error message"
        },
        {
            "id": "EC-002",
            "category": "input_validation",
            "description": "User uploads invalid file type (not image)",
            "risk_level": "medium",
            "test_recommendation": "Upload PDF as profile picture"
        },
        {
            "id": "EC-003",
            "category": "boundary",
            "description": "User uploads exactly 5MB file",
            "risk_level": "low",
            "test_recommendation": "Upload exactly 5MB image, should succeed"
        },
        {
            "id": "EC-004",
            "category": "error_handling",
            "description": "Network fails during upload",
            "risk_level": "medium",
            "test_recommendation": "Simulate network failure mid-upload"
        }
    ],
    "risks": [
        {
            "id": "RISK-001",
            "description": "Large image uploads could strain server storage",
            "impact": "medium",
            "mitigation": "Implement image compression on upload"
        }
    ],
    "missing_requirements": [
        {
            "description": "No mention of supported image formats",
            "suggested_story": "As a user, I want to know which image formats are supported, so I can upload a compatible file"
        }
    ]
}
```"""


def create_edge_case_analyzer_agent() -> Agent:
    """
    Create an edge case analyzer agent for risk and edge case identification.

    Uses gpt-5 (routing model) for fast classification.
    Note: Temperature removed due to model compatibility issues with some models.
    """
    model_chain = get_model_fallback_chain("routing")
    primary_model = model_chain[0]
    logger.info(f"Edge Case Analyzer using model chain: {model_chain}")

    agent = Agent(
        name="Edge Case Analyzer",
        instructions=EDGE_CASE_INSTRUCTIONS,
        tools=[],
        model=primary_model,
        model_settings=ModelSettings(
            tool_choice="none",
            parallel_tool_calls=False,
            # Note: temperature removed - not supported by all models
        ),
    )

    return agent


__all__ = ["create_edge_case_analyzer_agent"]

