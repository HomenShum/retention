"""
Workflow Agent — pure reasoning, no tools.

Analyzes CrawlResult JSON and identifies 5-10 logical user workflows.
Uses gpt-5.4 (thinking model) for deep reasoning.
"""

import logging

from agents import Agent
from agents.model_settings import ModelSettings

from ..model_fallback import THINKING_MODEL

logger = logging.getLogger(__name__)

WORKFLOW_INSTRUCTIONS = """You are a **QA workflow analyst**. Given a CrawlResult JSON describing an app's screens, components, and transitions, identify **5-10 logical user workflows**.

**Definition**: A workflow is a sequence of screens + actions that accomplishes a specific user goal.

**INPUT**: You will receive a CrawlResult JSON with:
- `screens`: list of ScreenNode objects (screen_id, screen_name, components, navigation_depth, parent_screen_id)
- `transitions`: list of ScreenTransition objects (from_screen, to_screen, action)

**ANALYSIS APPROACH**:
1. Map the screen graph using transitions and parent_screen_id.
2. Identify entry points (depth-0 screens) and leaf screens (no outgoing transitions).
3. Trace navigation chains that represent meaningful user journeys.
4. Consider CRUD operations: Create, Read, Update, Delete.
5. Consider navigation patterns: search, filter, scroll, back-navigation.
6. Consider edge cases: empty states, error handling, permission flows.

**OUTPUT**: Respond with ONLY a valid JSON object matching this schema — no markdown, no explanation:
```
{
  "app_name": "...",
  "workflows": [
    {
      "workflow_id": "wf_001",
      "name": "Create New Contact",
      "description": "User creates a new contact with name and phone number",
      "screens_involved": ["screen_001", "screen_002", "screen_003"],
      "steps": [
        {"step_number": 1, "screen_id": "screen_001", "action": "Tap FAB button", "expected_result": "New contact form appears"},
        {"step_number": 2, "screen_id": "screen_002", "action": "Enter name", "expected_result": "Name field populated"}
      ],
      "complexity": "moderate"
    }
  ]
}
```

**WORKFLOW CATEGORIES TO LOOK FOR**:
- Core CRUD workflows (create, view, edit, delete records)
- Search and navigation workflows
- Settings and configuration workflows
- Data validation workflows (what happens with invalid input?)
- Navigation integrity (back button, breadcrumb consistency)
- Empty state workflows (app with no data)

**RULES**:
- Output ONLY the JSON object, no surrounding text or markdown fences.
- Identify 5-10 workflows, covering different complexity levels.
- Each workflow must reference actual screen_ids from the CrawlResult.
- Steps must be concrete and actionable, not vague.
- Complexity: "simple" (1-2 screens), "moderate" (3-4 screens), "complex" (5+ screens or branching)."""


def create_workflow_agent(model_override: str = "") -> Agent:
    """Create the workflow reasoning agent (no tools, pure reasoning)."""
    _model = model_override or THINKING_MODEL
    agent = Agent(
        name="QA Workflow Analyst",
        instructions=WORKFLOW_INSTRUCTIONS,
        tools=[],  # Pure reasoning — no tools
        model=_model,
        model_settings=ModelSettings(
            tool_choice="none",
        ),
    )

    logger.info(f"Created QA Workflow Analyst, model={_model}")
    return agent
