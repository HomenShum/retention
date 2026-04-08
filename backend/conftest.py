"""
Root pytest configuration for the backend test suite.

Excludes application modules whose filenames start with 'test_' or
end with '_test' but are NOT actual test files — they are real FastAPI
routers or OpenAI Agents SDK agent modules that live inside the `app/`
package and use relative imports that break under pytest's import
resolution when collected directly.
"""

collect_ignore = [
    # API router — not a test file despite the 'test_generation' prefix
    "app/api/test_generation.py",
    # Agent module — not a test file
    "app/agents/prd_parser/subagents/test_case_generator_agent.py",
    # Agent module — not a test file
    "app/agents/test_generation/test_generation_agent.py",
]

