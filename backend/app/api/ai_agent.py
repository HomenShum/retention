"""
AI Agent API Router

Provides endpoints for AI agent operations:
- Chat with agent for test generation and bug reproduction
- Generate test cases from natural language
- Reproduce bugs automatically
- Search and analyze test scenarios
"""

from fastapi import APIRouter, HTTPException, Body, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field
import logging
import asyncio
import json
import io

from sse_starlette.sse import EventSourceResponse
from ..agents.coordinator.coordinator_service import AIAgentService, ChatMessage

logger = logging.getLogger(__name__)


# ============================================================================
# Pydantic Models
# ============================================================================

class TicketInfo(BaseModel):
    """Information about a ticket visible in the UI"""
    id: str
    title: str


class UIContext(BaseModel):
    """Context about the current UI state"""
    selectedTickets: List[TicketInfo] = Field(default_factory=list)
    ticketCount: int = 0
    selectedDevices: List[str] = Field(default_factory=list)
    currentPage: Optional[str] = None  # Current route path (e.g. "/demo/benchmarks")
    workspaceMode: bool = False  # Treat Slack/workspace activity as live context
    workspaceChannels: List[str] = Field(default_factory=list)
    workspaceIntent: Optional[str] = None


class ChatRequest(BaseModel):
    """Chat request with messages and optional UI context"""
    messages: List[ChatMessage]
    uiContext: Optional[UIContext] = None
    resumeSessionId: Optional[str] = None  # Session ID to resume from

router = APIRouter(prefix="/api/ai-agent", tags=["ai-agent"])

_ai_agent_service: AIAgentService = None

def set_ai_agent_service(service: AIAgentService):
    global _ai_agent_service
    _ai_agent_service = service

def get_ai_agent_service() -> AIAgentService:
    if _ai_agent_service is None:
        raise HTTPException(status_code=503, detail="AI Agent Service not initialized")
    return _ai_agent_service

@router.get("/scenarios")
async def get_scenarios() -> Dict[str, Any]:
    service = get_ai_agent_service()
    scenarios = service.get_available_scenarios()
    return {"scenarios": scenarios}


@router.post("/search")
async def search_tasks(query: str = Body(..., embed=True)) -> Dict[str, Any]:
    """Search for tasks matching the query"""
    service = get_ai_agent_service()
    results = service.search_tasks(query)
    return {"results": results}


@router.get("/task/{task_name}")
async def get_task_details(task_name: str) -> Dict[str, Any]:
    """Get detailed information about a specific task"""
    service = get_ai_agent_service()
    task = service.get_task_details(task_name)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_name}' not found")
    return task


@router.post("/generate-test")
async def generate_test(
    description: str = Body(..., embed=True),
    app_package: str = Body(None, embed=True),
    device_id: str = Body(None, embed=True)
) -> Dict[str, Any]:
    """Generate test code from natural language description using AI"""
    service = get_ai_agent_service()
    try:
        test_code = await service.generate_test(
            description=description,
            app_package=app_package,
            device_id=device_id
        )
        return {
            "status": "success",
            "test_code": test_code,
            "description": description,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Test generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reproduce-bug")
async def reproduce_bug(
    bug_description: str = Body(..., embed=True),
    steps: List[str] = Body(..., embed=True),
    device_id: str = Body(..., embed=True),
    app_package: str = Body(None, embed=True)
) -> Dict[str, Any]:
    """Reproduce a bug automatically using AI agent"""
    service = get_ai_agent_service()
    try:
        result = await service.reproduce_bug(
            bug_description=bug_description,
            steps=steps,
            device_id=device_id,
            app_package=app_package
        )
        return {
            "status": "success",
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Bug reproduction failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/analyze-scenario")
async def analyze_scenario(
    scenario_name: str = Body(..., embed=True)
) -> Dict[str, Any]:
    """Analyze a test scenario and generate insights"""
    service = get_ai_agent_service()
    try:
        analysis = await service.analyze_scenario(scenario_name)
        return {
            "status": "success",
            "analysis": analysis,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Scenario analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat")
async def chat_with_agent(request: ChatRequest) -> Dict[str, Any]:
    """Chat with the AI agent (non-streaming)"""
    service = get_ai_agent_service()
    response_text = await service.chat(request.messages, request.uiContext)
    return {
        "role": "assistant",
        "content": response_text,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.post("/chat/stream")
async def chat_with_agent_stream(request: ChatRequest):
    """Chat with the AI agent (streaming)"""
    service = get_ai_agent_service()

    async def event_generator():
        async for evt in service.chat_stream(request.messages, request.uiContext, request.resumeSessionId):
            if isinstance(evt, dict) and evt.get("type") == "content":
                yield {
                    "event": "message",
                    "data": json.dumps({"content": evt.get("content", "")})
                }
            elif isinstance(evt, dict) and evt.get("type") == "context_info":
                # Emit context info event
                yield {
                    "event": "context_info",
                    "data": json.dumps({
                        "stats": evt.get("stats", {})
                    })
                }
            elif isinstance(evt, dict) and evt.get("type") == "tool_call":
                # Emit tool call events
                # Convert Pydantic models in full_context to dicts for JSON serialization
                full_context = evt.get("full_context", [])
                full_context_dicts = []
                for msg in full_context:
                    if hasattr(msg, 'model_dump'):
                        # Pydantic model - convert to dict
                        full_context_dicts.append(msg.model_dump())
                    elif isinstance(msg, dict):
                        # Already a dict
                        full_context_dicts.append(msg)
                    else:
                        # Unknown type - skip
                        pass

                yield {
                    "event": "tool_call",
                    "data": json.dumps({
                        "agent_name": evt.get("agent_name", "Agent"),
                        "tool_name": evt.get("tool_name", "unknown"),
                        "tool_input": evt.get("tool_input"),
                        "tool_output": evt.get("tool_output"),
                        "status": evt.get("status", "unknown"),
                        "context_stats": evt.get("context_stats"),
                        "full_context": full_context_dicts
                    })
                }
            elif isinstance(evt, dict) and evt.get("type") == "handoff":
                # Emit handoff events (agent delegation)
                yield {
                    "event": "handoff",
                    "data": json.dumps({
                        "from_agent": evt.get("from_agent", "Unknown"),
                        "to_agent": evt.get("to_agent", "Unknown"),
                        "status": evt.get("status", "unknown"),
                        "output": evt.get("output", ""),
                        "context_stats": evt.get("context_stats")
                    })
                }
            elif isinstance(evt, dict) and evt.get("type") == "session_created":
                # Emit session created event
                yield {
                    "event": "session_created",
                    "data": json.dumps({
                        "session_id": evt.get("session_id", "")
                    })
                }
            elif isinstance(evt, dict) and evt.get("type") == "rate_limit":
                # Emit rate limit event with retry information
                yield {
                    "event": "rate_limit",
                    "data": json.dumps({
                        "type": "rate_limit",
                        "content": evt.get("content", "Rate limit exceeded"),
                        "retry_after_ms": evt.get("retry_after_ms", 60000)
                    })
                }
            elif isinstance(evt, dict) and evt.get("type") == "final":
                yield {
                    "event": "done",
                    "data": json.dumps({"content": evt.get("content", ""), "timestamp": datetime.now(timezone.utc).isoformat()})
                }
            elif isinstance(evt, dict) and evt.get("type") == "error":
                yield {
                    "event": "error",
                    "data": json.dumps({"error": evt.get("content", "Unknown error")})
                }
        # Ensure done event is always sent
        yield {
            "event": "done",
            "data": json.dumps({"timestamp": datetime.now(timezone.utc).isoformat()})
        }

    return EventSourceResponse(event_generator())


# ============================================================================
# WebSocket Endpoint
# ============================================================================

# WebSocket endpoint for simulation updates
async def simulation_websocket_handler(websocket: WebSocket, simulation_id: str):
    """WebSocket endpoint for real-time simulation updates"""
    await websocket.accept()

    service = get_ai_agent_service()

    try:
        logger.info(f"WebSocket connected for simulation {simulation_id}")

        # Send updates every second
        while True:
            status = service.get_simulation_status(simulation_id)

            if not status:
                await websocket.send_json({"error": "Simulation not found"})
                break

            # Send current status
            await websocket.send_json({
                "simulation_id": status.simulation_id,
                "task_name": status.task_name,
                "status": status.status,
                "emulator_count": status.emulator_count,
                "completed_count": status.completed_count,
                "failed_count": status.failed_count,
                "results": [
                    {
                        "device_id": r.get("device_id"),
                        "status": r.get("status"),
                        "steps": r.get("steps", []),
                    }
                    for r in status.results
                ],
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

            # Stop if simulation is complete
            if status.status in ["completed", "cancelled", "failed"]:
                break

            await asyncio.sleep(1)

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for simulation {simulation_id}")
    except Exception as e:
        logger.error(f"WebSocket error for simulation {simulation_id}: {e}")
        await websocket.send_json({"error": str(e)})


# ============================================================================
# Additional AI Agent Endpoints
# ============================================================================

@router.post("/execute-simulation")
async def execute_simulation(
    task_name: str = Body(None, embed=True),  # Optional: for backward compatibility (same task for all)
    device_ids: List[str] = Body(None, embed=True),  # Optional: for backward compatibility
    device_tasks: List[Dict[str, str]] = Body(None, embed=True),  # New: [{device_id, task_name}, ...]
    max_concurrent: int = Body(5, embed=True)
) -> Dict[str, Any]:
    """
    Execute a test simulation on multiple devices concurrently.

    Supports two modes:
    1. Single task mode (backward compatible): task_name + device_ids
    2. Multi-task mode (new): device_tasks = [{device_id, task_name}, ...]

    Deep Agent Pattern: Allows parallel execution of different tasks on different devices,
    enabling complex multi-device test orchestration.
    """
    service = get_ai_agent_service()
    try:
        # Validate input
        if device_tasks:
            # Multi-task mode
            if not isinstance(device_tasks, list) or len(device_tasks) == 0:
                raise HTTPException(status_code=400, detail="device_tasks must be a non-empty list")

            # Extract device_ids and create task mapping
            device_ids_list = [dt["device_id"] for dt in device_tasks]
            task_mapping = {dt["device_id"]: dt["task_name"] for dt in device_tasks}

            simulation_id = await service.execute_multi_task_simulation(
                device_tasks=device_tasks,
                max_concurrent=max_concurrent
            )

            return {
                "status": "success",
                "simulation_id": simulation_id,
                "mode": "multi_task",
                "device_count": len(device_tasks),
                "tasks": list(set(dt["task_name"] for dt in device_tasks)),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        elif task_name and device_ids:
            # Single task mode (backward compatible)
            simulation_id = await service.execute_simulation(
                task_name=task_name,
                device_ids=device_ids,
                max_concurrent=max_concurrent
            )
            return {
                "status": "success",
                "simulation_id": simulation_id,
                "mode": "single_task",
                "task_name": task_name,
                "device_count": len(device_ids),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        else:
            raise HTTPException(
                status_code=400,
                detail="Must provide either (task_name + device_ids) or device_tasks"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Simulation execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/simulations")
async def list_simulations() -> Dict[str, Any]:
    """List all simulations (for polling from frontend)"""
    service = get_ai_agent_service()
    simulations = service.list_simulations()
    return {
        "simulations": [s.model_dump() for s in simulations],
        "count": len(simulations),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/simulation/{simulation_id}/status")
async def get_simulation_status(simulation_id: str) -> Dict[str, Any]:
    """Get the status of a running simulation"""
    service = get_ai_agent_service()
    status = service.get_simulation_status(simulation_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"Simulation '{simulation_id}' not found")
    return status.model_dump()


@router.get("/scenarios")
async def get_scenarios():
    """Get available test scenarios"""
    service = get_ai_agent_service()
    scenarios = service.get_available_scenarios()
    return {"scenarios": scenarios}


@router.post("/search")
async def search_tasks(query: str = Body(..., embed=True)):
    """Search for tasks matching the query"""
    service = get_ai_agent_service()
    results = service.search_tasks(query)
    return {"results": results}


@router.get("/task/{task_name}")
async def get_task_details(task_name: str):
    """Get detailed information about a specific task"""
    service = get_ai_agent_service()
    task = service.get_task_details(task_name)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_name}' not found")
    return task


@router.get("/visualization")
async def get_agent_visualization():
    """
    Generate and return agent visualization as PNG image.

    Returns a visual graph showing:
    - Agents (yellow boxes)
    - Tools (green ellipses)
    - MCP Servers (grey boxes)
    - Handoffs (directed edges)
    """
    try:
        from agents.extensions.visualization import draw_graph

        service = get_ai_agent_service()
        coordinator_agent = service.get_coordinator_agent()

        if not coordinator_agent:
            raise HTTPException(status_code=500, detail="Coordinator agent not initialized")

        # Generate graph and save to bytes buffer
        graph = draw_graph(coordinator_agent)

        # Render to PNG bytes
        png_bytes = graph.pipe(format='png')

        # Return as image response
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "Content-Disposition": "inline; filename=agent_graph.png"
            }
        )

    except ImportError as e:
        logger.error(f"Visualization dependencies not installed: {e}")
        raise HTTPException(
            status_code=500,
            detail="Visualization dependencies not installed. Run: pip install 'openai-agents[viz]'"
        )
    except Exception as e:
        import traceback
        logger.error(f"Failed to generate agent visualization: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to generate visualization: {str(e)}")

