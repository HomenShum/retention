"""
PRD Ingestion API Router

FastAPI endpoints for PRD ingestion and test generation.
"""
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import List, Optional, Dict
from dataclasses import asdict

from app.benchmarks.prd_ingestion import (
    get_prd_processor,
    PRDIngestionResult,
    GoldenBug
)
from app.agents.device_testing.mobile_mcp_client import MobileMCPClient


router = APIRouter(prefix="/api/prd", tags=["PRD Ingestion"])


class PRDIngestRequest(BaseModel):
    """Request to ingest a PRD"""
    prd_text: str
    prd_id: Optional[str] = None


class ExecuteGoldenBugRequest(BaseModel):
    """Request to execute a Golden Bug"""
    golden_bug_id: str
    device_ids: List[str]


class GoldenBugFilterRequest(BaseModel):
    """Request to filter Golden Bugs"""
    prd_id: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None


@router.post("/ingest")
async def ingest_prd(request: PRDIngestRequest) -> Dict:
    """
    Ingest a PRD and generate Golden Bugs
    
    Pipeline:
    1. Extract user stories from PRD
    2. Generate test scenarios
    3. Convert to AndroidWorld tasks
    4. Create Golden Bugs
    
    Returns:
        Summary of ingestion results
    """
    try:
        processor = get_prd_processor()
        result = await processor.ingest_prd(
            prd_text=request.prd_text,
            prd_id=request.prd_id
        )
        
        return {
            "success": True,
            "prd_id": result.prd_id,
            "summary": result.summary,
            "user_stories": [
                {
                    "id": s.id,
                    "title": s.title,
                    "description": s.description
                }
                for s in result.user_stories
            ],
            "golden_bugs": [
                {
                    "id": gb.id,
                    "title": gb.title,
                    "priority": gb.priority,
                    "steps_count": len(gb.test_steps)
                }
                for gb in result.golden_bugs
            ]
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest/file")
async def ingest_prd_file(file: UploadFile = File(...)) -> Dict:
    """
    Ingest a PRD from uploaded file
    
    Supports: .txt, .md, .pdf (text-based)
    """
    try:
        # Read file content
        content = await file.read()
        prd_text = content.decode('utf-8')
        
        # Use filename as PRD ID
        prd_id = file.filename.rsplit('.', 1)[0]
        
        processor = get_prd_processor()
        result = await processor.ingest_prd(
            prd_text=prd_text,
            prd_id=prd_id
        )
        
        return {
            "success": True,
            "prd_id": result.prd_id,
            "summary": result.summary,
            "golden_bugs_count": len(result.golden_bugs)
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/golden-bugs")
async def list_golden_bugs(
    prd_id: Optional[str] = None,
    category: Optional[str] = None,
    priority: Optional[str] = None
) -> Dict:
    """
    List Golden Bugs with optional filters
    """
    try:
        processor = get_prd_processor()
        bugs = processor.get_golden_bugs(
            prd_id=prd_id,
            category=category,
            priority=priority
        )
        
        return {
            "success": True,
            "count": len(bugs),
            "golden_bugs": [asdict(bug) for bug in bugs]
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/golden-bugs/{bug_id}")
async def get_golden_bug(bug_id: str) -> Dict:
    """Get a specific Golden Bug by ID"""
    try:
        processor = get_prd_processor()
        bugs = processor.get_golden_bugs()
        
        bug = next((b for b in bugs if b.id == bug_id), None)
        if not bug:
            raise HTTPException(status_code=404, detail=f"Golden Bug {bug_id} not found")
        
        return {
            "success": True,
            "golden_bug": asdict(bug)
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/golden-bugs/{bug_id}/execute")
async def execute_golden_bug(bug_id: str, device_ids: List[str]) -> Dict:
    """
    Execute a Golden Bug test on specified devices
    
    Args:
        bug_id: Golden Bug ID to execute
        device_ids: List of device IDs to test on
    
    Returns:
        Execution results for each device
    """
    try:
        processor = get_prd_processor()
        mobile_client = MobileMCPClient()
        
        # Get the Golden Bug
        bugs = processor.get_golden_bugs()
        bug = next((b for b in bugs if b.id == bug_id), None)
        if not bug:
            raise HTTPException(status_code=404, detail=f"Golden Bug {bug_id} not found")
        
        # Execute on devices
        results = await processor.execute_golden_bug_on_devices(
            golden_bug=bug,
            device_ids=device_ids,
            mobile_mcp_client=mobile_client
        )
        
        return {
            "success": True,
            "results": results
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/golden-bugs/execute-batch")
async def execute_golden_bugs_batch(
    golden_bug_ids: List[str],
    device_ids: List[str]
) -> Dict:
    """
    Execute multiple Golden Bugs on device fleet
    
    Args:
        golden_bug_ids: List of Golden Bug IDs
        device_ids: List of device IDs
    
    Returns:
        Aggregate results
    """
    try:
        processor = get_prd_processor()
        mobile_client = MobileMCPClient()
        
        all_results = []
        
        for bug_id in golden_bug_ids:
            bugs = processor.get_golden_bugs()
            bug = next((b for b in bugs if b.id == bug_id), None)
            
            if not bug:
                all_results.append({
                    "golden_bug_id": bug_id,
                    "error": "Bug not found",
                    "success": False
                })
                continue
            
            result = await processor.execute_golden_bug_on_devices(
                golden_bug=bug,
                device_ids=device_ids,
                mobile_mcp_client=mobile_client
            )
            all_results.append(result)
        
        # Calculate aggregate metrics
        total_executions = sum(
            len(r.get("device_results", []))
            for r in all_results
        )
        successful_executions = sum(
            sum(1 for dr in r.get("device_results", []) if dr.get("success"))
            for r in all_results
        )
        
        return {
            "success": True,
            "golden_bugs_count": len(golden_bug_ids),
            "total_executions": total_executions,
            "successful_executions": successful_executions,
            "success_rate": successful_executions / total_executions if total_executions > 0 else 0,
            "results": all_results
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/golden-bugs/export/json")
async def export_golden_bugs_json(
    prd_id: Optional[str] = None,
    category: Optional[str] = None,
    priority: Optional[str] = None
) -> Dict:
    """Export Golden Bugs as JSON"""
    try:
        processor = get_prd_processor()
        bugs = processor.get_golden_bugs(
            prd_id=prd_id,
            category=category,
            priority=priority
        )
        
        json_export = processor.export_golden_bugs_json(bugs)
        
        return {
            "success": True,
            "count": len(bugs),
            "json_export": json_export
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
