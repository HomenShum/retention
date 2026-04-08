"""
Simulation and task management tools for AI Agent
"""
import json
from typing import List
import logging

logger = logging.getLogger(__name__)


def create_simulation_tools(service_ref):
    """
    Create simulation tools with service reference
    
    Args:
        service_ref: Reference to AIAgentService instance
    
    Returns:
        Dictionary of simulation tool functions
    """
    
    def search_tasks(query: str) -> str:
        """
        Search for test tasks matching a query
        
        Args:
            query: Search query string
        
        Returns:
            JSON string with matching tasks
        """
        logger.info(f"🔍 search_tasks tool called with query: {query}")
        results = service_ref.search_tasks(query)
        logger.info(f"🔍 search_tasks found {len(results)} results: {results}")
        return json.dumps(results)
    
    def get_task_details(task_name: str) -> str:
        """
        Get detailed information about a specific test task
        
        Args:
            task_name: Name of the task
        
        Returns:
            JSON string with task details
        """
        task = service_ref.get_task_details(task_name)
        if task:
            return json.dumps(task, indent=2)
        return json.dumps({"error": f"Task '{task_name}' not found"})
    
    async def execute_simulation(
        task_name: str, 
        device_ids: List[str], 
        max_concurrent: int = 5
    ) -> str:
        """
        Execute a test simulation on multiple emulators
        
        Args:
            task_name: Name of the test task to execute
            device_ids: List of device IDs to run on
            max_concurrent: Maximum concurrent executions
        
        Returns:
            JSON string with simulation ID and status
        """
        try:
            sim_id = await service_ref.execute_simulation(
                task_name=task_name,
                device_ids=device_ids,
                max_concurrent=max_concurrent
            )
            return json.dumps({
                "simulation_id": sim_id,
                "status": "started",
                "task_name": task_name,
                "device_count": len(device_ids)
            })
        except Exception as e:
            logger.error(f"Error executing simulation: {e}")
            return json.dumps({"error": str(e)})
    
    def get_simulation_status(simulation_id: str) -> str:
        """
        Get the current status of a running simulation
        
        Args:
            simulation_id: ID of the simulation
        
        Returns:
            JSON string with simulation status
        """
        status = service_ref.get_simulation_status(simulation_id)
        if status:
            return json.dumps(status.model_dump(), indent=2)
        return json.dumps({"error": f"Simulation '{simulation_id}' not found"})
    
    return {
        "search_tasks": search_tasks,
        "get_task_details": get_task_details,
        "execute_simulation": execute_simulation,
        "get_simulation_status": get_simulation_status
    }

