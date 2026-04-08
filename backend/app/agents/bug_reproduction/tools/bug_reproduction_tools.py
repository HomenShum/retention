"""
Bug Reproduction Agent Tools

Provides tools for executing test scenarios and reproducing bugs.
"""

import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


def create_bug_reproduction_tools(service_ref):
    """
    Create bug reproduction tools with service reference.
    
    Args:
        service_ref: Reference to AIAgentService (coordinator service)
        
    Returns:
        Dictionary of tool functions
    """
    
    async def execute_test_scenario(
        scenario_name: str,
        device_id: str,
        test_steps: List[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute a predefined test scenario on a device.
        
        Args:
            scenario_name: Name of the test scenario to execute
            device_id: Device ID (e.g., "emulator-5554")
            test_steps: Optional list of test steps (if not provided, will use scenario from capabilities)
            
        Returns:
            Test execution results with screenshots and evidence
        """
        try:
            logger.info(f"Executing test scenario: {scenario_name} on device: {device_id}")
            
            # Get scenario from capabilities if test_steps not provided
            if not test_steps:
                scenarios = service_ref.capabilities.get("instagram_test_scenarios", {})
                scenario = scenarios.get(scenario_name)
                
                if not scenario:
                    return {
                        "status": "error",
                        "message": f"Scenario '{scenario_name}' not found in capabilities",
                        "available_scenarios": list(scenarios.keys())
                    }
                
                test_steps = scenario.get("steps", [])
            
            # Use unified bug reproduction service to execute scenario
            from ..bug_reproduction_service import TestScenarioInput, ExecutionMode
            
            scenario_input = TestScenarioInput(
                scenario_name=scenario_name,
                device_id=device_id,
                test_steps=test_steps
            )
            
            result = await service_ref.bug_repro_service.execute_scenario(
                scenario_input=scenario_input
            )
            
            return result.model_dump()
            
        except Exception as e:
            logger.error(f"Error executing test scenario: {e}")
            return {
                "status": "error",
                "message": str(e),
                "scenario_name": scenario_name,
                "device_id": device_id
            }
    
    async def reproduce_bug(
        bug_description: str,
        device_id: str,
        manual_steps: List[str],
        device_info: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Reproduce a bug from a manual bug report.
        
        Args:
            bug_description: Description of the bug
            device_id: Device ID (e.g., "emulator-5554")
            manual_steps: List of manual reproduction steps (natural language)
            device_info: Optional device information
            
        Returns:
            Bug reproduction results with evidence
        """
        try:
            logger.info(f"Reproducing bug: {bug_description} on device: {device_id}")
            
            # Use unified bug reproduction service to reproduce bug
            from ..bug_reproduction_service import BugReportInput, ExecutionMode
            
            bug_input = BugReportInput(
                bug_description=bug_description,
                device_info=device_info or {"device_id": device_id},
                manual_steps=manual_steps
            )
            
            result = await service_ref.bug_repro_service.reproduce_bug(
                bug_report=bug_input,
                device_id=device_id
            )
            
            return result.model_dump()
            
        except Exception as e:
            logger.error(f"Error reproducing bug: {e}")
            return {
                "status": "error",
                "message": str(e),
                "bug_description": bug_description,
                "device_id": device_id
            }
    
    async def get_execution_status(
        execution_id: str = None,
        device_id: str = None
    ) -> Dict[str, Any]:
        """
        Get the status of a test execution or bug reproduction.
        
        Args:
            execution_id: Optional execution ID
            device_id: Optional device ID to filter by
            
        Returns:
            Execution status information
        """
        try:
            # For now, return basic status
            # In the future, this could track ongoing executions
            return {
                "status": "success",
                "message": "Execution status tracking not yet implemented",
                "execution_id": execution_id,
                "device_id": device_id
            }
            
        except Exception as e:
            logger.error(f"Error getting execution status: {e}")
            return {
                "status": "error",
                "message": str(e)
            }
    
    return {
        "execute_test_scenario": execute_test_scenario,
        "reproduce_bug": reproduce_bug,
        "get_execution_status": get_execution_status,
    }


__all__ = ["create_bug_reproduction_tools"]

