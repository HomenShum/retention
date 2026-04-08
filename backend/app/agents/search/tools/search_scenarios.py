"""
Search Test Scenarios Tool

Provides search functionality for available test scenarios/tasks.
"""

import json
import logging
from agents import function_tool

logger = logging.getLogger(__name__)


def create_search_test_scenarios_tool(test_scenarios):
    """
    Create the search_test_scenarios tool with access to test scenarios.
    
    Args:
        test_scenarios: List of available test scenarios
        
    Returns:
        Configured function_tool for searching test scenarios
    """
    
    @function_tool
    def search_test_scenarios(query: str) -> str:
        """
        Search available test scenarios/tasks.
        
        Use this when users ask about:
        - "tests", "test cases", "scenarios", "tasks"
        - "what tests can I run"
        - Specific test names like "login test", "feed scrolling"
        
        Args:
            query: Search query for test scenarios
            
        Returns:
            JSON string with matching test scenarios
        """
        try:
            query_lower = query.lower()
            results = [
                s for s in test_scenarios
                if query_lower in s["name"].lower() or query_lower in s["description"].lower()
            ]
            
            if not results:
                return json.dumps({
                    "message": f"No test scenarios found matching '{query}'",
                    "available_scenarios": [s["name"] for s in test_scenarios],
                    "results": []
                })
            
            return json.dumps({
                "message": f"Found {len(results)} test scenario(s) matching '{query}'",
                "query": query,
                "results": results
            }, indent=2)
        except Exception as e:
            logger.error(f"Test scenario search failed: {e}")
            return json.dumps({"error": f"Search failed: {str(e)}", "results": []})
    
    return search_test_scenarios

