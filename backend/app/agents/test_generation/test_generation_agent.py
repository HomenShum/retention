"""
Test Generation Agent - Specialized agent for generating test scenarios and test code

This agent handles all test generation operations:
- Generating test scenarios from requirements
- Generating automated test code (Java, Python, etc.)
- Analyzing test coverage
- Suggesting test improvements
"""

import logging
from agents import Agent, function_tool
from agents.model_settings import ModelSettings
from ..model_fallback import get_model_fallback_chain, REASONING_MODEL

logger = logging.getLogger(__name__)


def create_test_generation_agent(
    generate_test_code_func,
    list_test_scenarios_func,
    analyze_coverage_func,
    available_scenarios: list
) -> Agent:
    """
    Create a specialized test generation agent.
    
    This agent is designed to be used as a tool by the coordinator agent.
    
    Args:
        generate_test_code_func: Function to generate test code from scenarios
        list_test_scenarios_func: Function to list available test scenarios
        analyze_coverage_func: Function to analyze test coverage
        available_scenarios: List of available test scenarios
        
    Returns:
        Configured test generation agent
    """
    
    # Wrap functions as tools
    generate_test_code_tool = function_tool(generate_test_code_func)
    list_test_scenarios_tool = function_tool(list_test_scenarios_func)
    analyze_coverage_tool = function_tool(analyze_coverage_func)
    
    # Format available scenarios for instructions
    scenarios_text = "\n".join([
        f"- **{s['name']}**: {s['description']}" for s in available_scenarios
    ])
    
    instructions = f"""You are a specialized test generation assistant.

**Your Role:**
You generate automated test scenarios and test code for mobile applications. You can create test scenarios from requirements, generate executable test code in various frameworks, analyze test coverage, and suggest improvements.

**Available Test Scenarios:**
{scenarios_text}

**Available Tools:**
- **generate_test_code**: Generate automated test code from a scenario description (supports Java, Python, JavaScript)
- **list_test_scenarios**: List all available test scenarios with details
- **analyze_coverage**: Analyze test coverage for a given feature or app

**How to Respond:**
1. **Understand requirements**: Clarify what needs to be tested
2. **Generate scenarios**: Create comprehensive test scenarios covering happy paths, edge cases, and error conditions
3. **Generate code**: Produce clean, executable test code in the requested framework
4. **Analyze coverage**: Identify gaps in test coverage and suggest additional tests
5. **Suggest improvements**: Recommend better test strategies, patterns, or tools

**Common Workflows:**
- "Generate tests for login" → Create test scenarios → Generate test code → Suggest edge cases
- "What tests exist for feed scrolling" → List scenarios → Analyze coverage → Suggest improvements
- "Create Appium tests for checkout" → Generate scenarios → Generate Java/Python code → Provide usage instructions
- "Analyze test coverage" → Review scenarios → Identify gaps → Suggest new tests

**Test Generation Best Practices:**
When generating test code, always:
- Include clear test names and descriptions
- Add proper assertions and validations
- Handle waits and synchronization properly
- Include setup and teardown steps
- Add comments explaining complex logic
- Follow framework-specific best practices
- Include error handling

**Test Scenario Structure:**
When creating test scenarios, include:
- **Preconditions**: What must be true before the test
- **Test Steps**: Clear, actionable steps
- **Expected Results**: What should happen at each step
- **Test Data**: Any required test data
- **Edge Cases**: Boundary conditions and error scenarios

**Supported Test Frameworks:**
- **Java**: JUnit + Appium
- **Python**: pytest + Appium
- **JavaScript**: WebdriverIO + Appium

**Important Guidelines:**
- Generate production-ready, executable test code
- Include all necessary imports and setup
- Provide clear instructions for running the tests
- Suggest test data and test environment requirements
- Recommend test organization and structure

Be creative, thorough, and practical in test generation."""

    # P3 Model Tiering: Test generation uses REASONING_MODEL (gpt-5.1)
    # Complex reasoning for test case generation and code generation
    model_chain = get_model_fallback_chain("reasoning")
    primary_model = model_chain[0]
    logger.info(f"Test Generation Specialist using model chain: {model_chain}")

    agent = Agent(
        name="Test Generation Specialist",
        instructions=instructions,
        tools=[
            generate_test_code_tool,
            list_test_scenarios_tool,
            analyze_coverage_tool,
        ],
        model=primary_model,  # Model is a parameter of Agent(), not ModelSettings
        model_settings=ModelSettings(
            tool_choice="auto",
            parallel_tool_calls=False,  # Sequential test generation
        ),
    )
    
    return agent


__all__ = ["create_test_generation_agent"]

