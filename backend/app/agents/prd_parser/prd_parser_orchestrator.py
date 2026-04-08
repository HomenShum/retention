"""
PRD Parser Orchestrator - Lead Agent for Multi-Agent PRD Parsing

This is the lead agent that decomposes PRD parsing into subtasks and coordinates
specialized subagents running in parallel.

Reference: Anthropic Multi-Agent Research System (June 2025)
- Orchestrator-Worker Pattern: Lead spawns 3-5 subagents in parallel
- KV-Cache Optimization: Stable prompts, append-only context
- Extended thinking for complex task decomposition
"""

import logging
from agents import Agent, function_tool
from agents.model_settings import ModelSettings
from ..model_fallback import get_model_fallback_chain

logger = logging.getLogger(__name__)

ORCHESTRATOR_INSTRUCTIONS = """You are the **PRD Parser Orchestrator**, a lead agent that analyzes Product Requirement Documents and coordinates extraction by specialized subagents.

**Your Role:**
1. Analyze the PRD structure and identify sections
2. Decompose the parsing task into subtasks
3. Delegate to specialized subagents running in parallel
4. Merge and validate results

**Workflow:**
1. **Analyze Structure**: Identify document sections (overview, requirements, user stories, etc.)
2. **Plan Extraction**: Create task assignments for subagents
3. **Spawn Subagents**: The following subagents will run in parallel:
   - Story Extractor: Extracts user stories
   - Criteria Extractor: Extracts acceptance criteria
   - Edge Case Analyzer: Identifies risks and edge cases
4. **Generate Tests**: After extraction, generate test cases
5. **Merge Results**: Combine and cross-reference all outputs

**Available Tools:**
- `analyze_prd_structure`: Analyze PRD and identify sections
- `create_extraction_plan`: Create task plan for subagents

**Input Format:**
You receive the raw PRD content and title.

**Output Format:**
Return a structured analysis plan:
```json
{
    "document_overview": "Brief summary of the PRD",
    "sections": [
        {"id": "SEC-001", "title": "Section Title", "type": "requirements", "content": "..."}
    ],
    "extraction_tasks": [
        {"task_id": "TASK-001", "agent_type": "story_extractor", "section_ids": ["SEC-001"]}
    ],
    "estimated_stories": 10,
    "estimated_criteria": 25
}
```

**Important:**
- Analyze the ENTIRE document, don't skip sections
- Identify implicit requirements, not just explicit ones
- Consider the document context when assigning tasks
- Be thorough - missing a requirement is worse than extracting too many"""


# Tool: Analyze PRD Structure
def analyze_prd_structure(prd_content: str, title: str) -> dict:
    """
    Analyze the PRD document structure and identify sections.
    
    This tool examines the PRD content to:
    1. Identify document sections (headers, paragraphs)
    2. Classify section types (overview, requirements, user stories, etc.)
    3. Extract section content for subagent processing
    
    Args:
        prd_content: The raw PRD text
        title: Document title
        
    Returns:
        Dictionary with sections and their classifications
    """
    import re
    
    sections = []
    section_counter = 0
    
    # Split by markdown headers (##, ###)
    header_pattern = r'^(#{1,3})\s+(.+)$'
    lines = prd_content.split('\n')
    
    current_section = {"id": "SEC-000", "title": title, "type": "overview", "content": "", "level": 0}
    
    for line in lines:
        match = re.match(header_pattern, line, re.MULTILINE)
        if match:
            # Save previous section
            if current_section["content"].strip():
                sections.append(current_section)
            
            section_counter += 1
            level = len(match.group(1))
            header_title = match.group(2).strip()
            
            # Classify section type
            section_type = "general"
            lower_title = header_title.lower()
            if any(kw in lower_title for kw in ["overview", "introduction", "summary"]):
                section_type = "overview"
            elif any(kw in lower_title for kw in ["requirement", "feature", "functional"]):
                section_type = "requirements"
            elif any(kw in lower_title for kw in ["user stor", "epic", "story"]):
                section_type = "user_stories"
            elif any(kw in lower_title for kw in ["acceptance", "criteria", "given", "when", "then"]):
                section_type = "acceptance_criteria"
            elif any(kw in lower_title for kw in ["technical", "architecture", "design"]):
                section_type = "technical"
            elif any(kw in lower_title for kw in ["test", "validation", "verification"]):
                section_type = "testing"
            
            current_section = {
                "id": f"SEC-{section_counter:03d}",
                "title": header_title,
                "type": section_type,
                "content": "",
                "level": level
            }
        else:
            current_section["content"] += line + "\n"
    
    # Add last section
    if current_section["content"].strip():
        sections.append(current_section)
    
    # If no sections found, treat entire content as one section
    if not sections:
        sections = [{
            "id": "SEC-001",
            "title": title,
            "type": "requirements",
            "content": prd_content,
            "level": 1
        }]
    
    return {
        "title": title,
        "section_count": len(sections),
        "sections": sections
    }


# Tool: Create Extraction Plan
def create_extraction_plan(sections: list) -> dict:
    """
    Create a task plan for parallel subagent execution.
    """
    tasks = []
    task_counter = 0
    
    story_sections = []
    criteria_sections = []
    edge_case_sections = []
    
    for section in sections:
        section_id = section.get("id", "SEC-000")
        section_type = section.get("type", "general")
        
        # Assign sections to appropriate extractors
        if section_type in ["user_stories", "requirements", "overview"]:
            story_sections.append(section_id)
        if section_type in ["acceptance_criteria", "requirements", "testing"]:
            criteria_sections.append(section_id)
        # All sections go to edge case analyzer
        edge_case_sections.append(section_id)
    
    # Create tasks
    if story_sections:
        task_counter += 1
        tasks.append({
            "task_id": f"TASK-{task_counter:03d}",
            "agent_type": "story_extractor",
            "section_ids": story_sections,
            "priority": 1
        })
    
    if criteria_sections:
        task_counter += 1
        tasks.append({
            "task_id": f"TASK-{task_counter:03d}",
            "agent_type": "criteria_extractor", 
            "section_ids": criteria_sections,
            "priority": 1
        })
    
    if edge_case_sections:
        task_counter += 1
        tasks.append({
            "task_id": f"TASK-{task_counter:03d}",
            "agent_type": "edge_case_analyzer",
            "section_ids": edge_case_sections,
            "priority": 2
        })
    
    return {
        "task_count": len(tasks),
        "tasks": tasks,
        "parallel_groups": [[t["task_id"] for t in tasks if t["priority"] == 1]],
        "sequential_tasks": [t["task_id"] for t in tasks if t["priority"] > 1]
    }


def create_prd_orchestrator_agent() -> Agent:
    """
    Create the PRD Parser Orchestrator agent.

    This is the lead agent that coordinates the multi-agent PRD parsing.
    Uses gpt-5.4 (thinking model) for complex task decomposition.

    Returns:
        Configured orchestrator agent
    """
    # Use thinking model for orchestration (gpt-5.4)
    model_chain = get_model_fallback_chain("orchestration")
    primary_model = model_chain[0]
    logger.info(f"PRD Orchestrator using model chain: {model_chain}")

    # Create tools
    tools = [
        function_tool(analyze_prd_structure),
        function_tool(create_extraction_plan),
    ]

    agent = Agent(
        name="PRD Parser Orchestrator",
        instructions=ORCHESTRATOR_INSTRUCTIONS,
        tools=tools,
        model=primary_model,
        model_settings=ModelSettings(
            tool_choice="auto",
            parallel_tool_calls=True,  # Enable parallel tool calls
            temperature=0.1,  # Low temperature for consistent orchestration
        ),
    )

    return agent


__all__ = [
    "create_prd_orchestrator_agent",
    "analyze_prd_structure",
    "create_extraction_plan",
]

