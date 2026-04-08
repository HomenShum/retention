"""
Test generation tools for AI Agent
"""
import json
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def create_test_generation_tools(service_ref):
    """
    Create test generation tools with service reference
    
    Args:
        service_ref: Reference to AIAgentService instance
    
    Returns:
        Dictionary of test generation tool functions
    """
    
    async def generate_test_code(
        scenario_description: str,
        test_framework: str = "PYTHON",
        include_setup: bool = True
    ) -> str:
        """
        Generate automated test code from a scenario description
        
        Args:
            scenario_description: Natural language description of what to test
            test_framework: Test framework (JAVA, PYTHON, JAVASCRIPT)
            include_setup: Whether to include setup/teardown code
        
        Returns:
            JSON string with generated test code
        """
        try:
            logger.info(f"🧪 Generating test code for: {scenario_description}")
            
            # Use MCP Appium client to generate tests
            if hasattr(service_ref, 'appium_mcp') and service_ref.appium_mcp:
                # Get a session to use for test generation
                sessions = service_ref.appium_mcp.sessions
                if sessions:
                    # Use first available session's client
                    session = next(iter(sessions.values()))
                    if session.client:
                        test_code = await session.client.generate_tests(
                            scenario=scenario_description,
                            test_framework=test_framework.upper()
                        )
                        
                        if test_code:
                            return json.dumps({
                                "success": True,
                                "test_code": test_code,
                                "framework": test_framework,
                                "scenario": scenario_description,
                                "message": f"Generated {test_framework} test code"
                            }, indent=2)
            
            # Fallback: Generate basic test structure
            test_code = _generate_basic_test_template(
                scenario_description,
                test_framework,
                include_setup
            )
            
            return json.dumps({
                "success": True,
                "test_code": test_code,
                "framework": test_framework,
                "scenario": scenario_description,
                "message": f"Generated basic {test_framework} test template"
            }, indent=2)
            
        except Exception as e:
            logger.error(f"Error generating test code: {e}")
            return json.dumps({"error": str(e)})
    
    def list_test_scenarios() -> str:
        """
        List all available test scenarios with details
        
        Returns:
            JSON string with test scenarios
        """
        try:
            scenarios = service_ref.get_available_scenarios()
            
            # Enrich with additional details
            detailed_scenarios = []
            for scenario in scenarios:
                details = service_ref.get_task_details(scenario['name'])
                if details:
                    detailed_scenarios.append(details)
                else:
                    detailed_scenarios.append(scenario)
            
            return json.dumps({
                "total_scenarios": len(detailed_scenarios),
                "scenarios": detailed_scenarios
            }, indent=2)
            
        except Exception as e:
            logger.error(f"Error listing test scenarios: {e}")
            return json.dumps({"error": str(e)})
    
    def analyze_coverage(feature_name: str = None) -> str:
        """
        Analyze test coverage for a feature or the entire app
        
        Args:
            feature_name: Optional feature name to analyze (e.g., 'login', 'feed')
        
        Returns:
            JSON string with coverage analysis
        """
        try:
            scenarios = service_ref.get_available_scenarios()
            
            if feature_name:
                # Filter scenarios related to the feature
                related_scenarios = [
                    s for s in scenarios
                    if feature_name.lower() in s['name'].lower() or
                       feature_name.lower() in s.get('description', '').lower()
                ]
            else:
                related_scenarios = scenarios
            
            # Analyze coverage
            analysis = {
                "feature": feature_name or "All Features",
                "total_scenarios": len(related_scenarios),
                "scenarios": related_scenarios,
                "coverage_areas": _analyze_coverage_areas(related_scenarios),
                "gaps": _identify_coverage_gaps(related_scenarios, feature_name),
                "recommendations": _generate_coverage_recommendations(related_scenarios, feature_name)
            }
            
            return json.dumps(analysis, indent=2)
            
        except Exception as e:
            logger.error(f"Error analyzing coverage: {e}")
            return json.dumps({"error": str(e)})
    
    return {
        "generate_test_code": generate_test_code,
        "list_test_scenarios": list_test_scenarios,
        "analyze_coverage": analyze_coverage
    }


def _generate_basic_test_template(scenario: str, framework: str, include_setup: bool) -> str:
    """Generate a basic test template when MCP is not available"""
    
    if framework.upper() == "PYTHON":
        return f"""import pytest
from appium import webdriver
from appium.options.android import UiAutomator2Options

class Test{scenario.replace(' ', '')}:
    '''Test: {scenario}'''
    
    {'@pytest.fixture(scope="class")' if include_setup else ''}
    {'def setup_driver(self):' if include_setup else ''}
    {'    options = UiAutomator2Options()' if include_setup else ''}
    {'    options.platform_name = "Android"' if include_setup else ''}
    {'    driver = webdriver.Remote("http://localhost:4723", options=options)' if include_setup else ''}
    {'    yield driver' if include_setup else ''}
    {'    driver.quit()' if include_setup else ''}
    
    def test_{scenario.lower().replace(' ', '_')}(self{',' if include_setup else ''} {'setup_driver' if include_setup else ''}):
        '''Test: {scenario}'''
        # TODO: Implement test steps
        pass
"""
    
    elif framework.upper() == "JAVA":
        return f"""import io.appium.java_client.android.AndroidDriver;
import io.appium.java_client.android.options.UiAutomator2Options;
import org.junit.jupiter.api.*;
import java.net.URL;

public class {scenario.replace(' ', '')}Test {{
    private AndroidDriver driver;
    
    {'@BeforeEach' if include_setup else ''}
    {'public void setUp() throws Exception {' if include_setup else ''}
    {'    UiAutomator2Options options = new UiAutomator2Options();' if include_setup else ''}
    {'    options.setPlatformName("Android");' if include_setup else ''}
    {'    driver = new AndroidDriver(new URL("http://localhost:4723"), options);' if include_setup else ''}
    {'}' if include_setup else ''}
    
    @Test
    public void test{scenario.replace(' ', '')}() {{
        // TODO: Implement test steps for: {scenario}
    }}
    
    {'@AfterEach' if include_setup else ''}
    {'public void tearDown() {' if include_setup else ''}
    {'    if (driver != null) {' if include_setup else ''}
    {'        driver.quit();' if include_setup else ''}
    {'    }' if include_setup else ''}
    {'}' if include_setup else ''}
}}
"""
    
    else:  # JavaScript
        return f"""const {{ remote }} = require('webdriverio');

describe('{scenario}', () => {{
    let driver;
    
    {'before(async () => {' if include_setup else ''}
    {'    const options = {' if include_setup else ''}
    {'        capabilities: {' if include_setup else ''}
    {'            platformName: "Android",' if include_setup else ''}
    {'            "appium:automationName": "UiAutomator2"' if include_setup else ''}
    {'        }' if include_setup else ''}
    {'    };' if include_setup else ''}
    {'    driver = await remote(options);' if include_setup else ''}
    {'});' if include_setup else ''}
    
    it('should {scenario.lower()}', async () => {{
        // TODO: Implement test steps
    }});
    
    {'after(async () => {' if include_setup else ''}
    {'    await driver.deleteSession();' if include_setup else ''}
    {'});' if include_setup else ''}
}});
"""


def _analyze_coverage_areas(scenarios: list) -> Dict[str, Any]:
    """Analyze what areas are covered by existing scenarios"""
    areas = {}
    for scenario in scenarios:
        name = scenario.get('name', '')
        # Categorize by common patterns
        if 'login' in name.lower():
            areas.setdefault('Authentication', []).append(name)
        elif 'feed' in name.lower() or 'scroll' in name.lower():
            areas.setdefault('Content Browsing', []).append(name)
        elif 'post' in name.lower() or 'upload' in name.lower():
            areas.setdefault('Content Creation', []).append(name)
        elif 'search' in name.lower():
            areas.setdefault('Search', []).append(name)
        elif 'profile' in name.lower() or 'settings' in name.lower():
            areas.setdefault('User Management', []).append(name)
        else:
            areas.setdefault('Other', []).append(name)
    
    return areas


def _identify_coverage_gaps(scenarios: list, feature: str = None) -> list:
    """Identify potential gaps in test coverage"""
    gaps = []
    
    # Common test types to check for
    test_types = {
        'error_handling': ['error', 'invalid', 'failure'],
        'edge_cases': ['empty', 'max', 'min', 'boundary'],
        'performance': ['load', 'stress', 'performance'],
        'security': ['auth', 'permission', 'security'],
        'accessibility': ['accessibility', 'a11y', 'screen reader']
    }
    
    scenario_names = ' '.join([s.get('name', '').lower() for s in scenarios])
    
    for test_type, keywords in test_types.items():
        if not any(keyword in scenario_names for keyword in keywords):
            gaps.append(f"Missing {test_type.replace('_', ' ')} tests")
    
    return gaps


def _generate_coverage_recommendations(scenarios: list, feature: str = None) -> list:
    """Generate recommendations for improving test coverage"""
    recommendations = []
    
    if len(scenarios) < 5:
        recommendations.append("Consider adding more test scenarios for comprehensive coverage")
    
    # Check for common missing scenarios
    scenario_names = ' '.join([s.get('name', '').lower() for s in scenarios])
    
    if 'error' not in scenario_names:
        recommendations.append("Add error handling and negative test scenarios")
    
    if 'performance' not in scenario_names and 'load' not in scenario_names:
        recommendations.append("Add performance and load testing scenarios")
    
    if 'accessibility' not in scenario_names:
        recommendations.append("Add accessibility testing scenarios")
    
    if feature:
        recommendations.append(f"Consider edge cases specific to {feature} functionality")
    
    return recommendations

