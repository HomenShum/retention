"""
Agent Sessions API - Store and retrieve detailed agent execution logs
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import json
import os
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai-agent", tags=["agent-sessions"])

# In-memory storage for now (can be replaced with database later)
_sessions_storage: Dict[str, Dict[str, Any]] = {}
_sessions_file = "agent_sessions.json"


class AgentStep(BaseModel):
    id: int
    stepNumber: int
    description: str
    command: Optional[str] = None
    target: Optional[str] = None
    thoughts: Optional[str] = None
    action: Optional[str] = None
    observation: Optional[str] = None
    results: Optional[str] = None
    requestTokens: Optional[int] = None
    responseTokens: Optional[int] = None
    responseTime: Optional[int] = None
    status: Optional[str] = None
    model: Optional[str] = None
    prompts: Optional[List[Dict[str, str]]] = None
    screenshot: Optional[str] = None


class ReportCard(BaseModel):
    id: str
    createdAt: str
    score: Optional[float] = None  # 0.0 - 1.0
    strategy: Optional[str] = None
    regressionDelta: Optional[float] = None  # positive = improvement
    notes: Optional[str] = None
    passedChecks: Optional[List[str]] = None
    failedChecks: Optional[List[str]] = None


class AgentSession(BaseModel):
    id: str
    title: str
    createdAt: str
    status: str  # running, completed, failed, paused
    steps: List[AgentStep]
    deviceId: Optional[str] = None
    goal: Optional[str] = None
    totalRequestTokens: Optional[int] = None
    totalResponseTokens: Optional[int] = None
    totalTokens: Optional[int] = None
    # Session state for resuming
    conversationHistory: Optional[List[Dict[str, str]]] = None
    lastError: Optional[str] = None
    retryAfterMs: Optional[int] = None
    # Deep-agent evaluation fields
    isGolden: Optional[bool] = False
    feedback: Optional[str] = None  # "up" | "down" | null
    reportCards: Optional[List[Dict[str, Any]]] = None


class CreateSessionRequest(BaseModel):
    title: str
    deviceId: Optional[str] = None
    goal: Optional[str] = None


class AddStepRequest(BaseModel):
    sessionId: str
    step: AgentStep


def load_sessions():
    """Load sessions from file"""
    global _sessions_storage
    if os.path.exists(_sessions_file):
        try:
            with open(_sessions_file, 'r') as f:
                _sessions_storage = json.load(f)
                logger.info(f"Loaded {len(_sessions_storage)} sessions from {_sessions_file}")
        except Exception as e:
            logger.error(f"Failed to load sessions: {e}")
            _sessions_storage = {}
    else:
        _sessions_storage = {}


def save_sessions():
    """Save sessions to file"""
    try:
        with open(_sessions_file, 'w') as f:
            json.dump(_sessions_storage, f, indent=2)
            logger.info(f"Saved {len(_sessions_storage)} sessions to {_sessions_file}")
    except Exception as e:
        logger.error(f"Failed to save sessions: {e}")


# Load sessions on startup
load_sessions()


@router.get("/sessions")
async def get_sessions():
    """Get all agent sessions"""
    sessions = list(_sessions_storage.values())
    # Sort by createdAt descending (most recent first)
    sessions.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
    return {"sessions": sessions}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get a specific agent session"""
    if session_id not in _sessions_storage:
        raise HTTPException(status_code=404, detail="Session not found")
    return _sessions_storage[session_id]


@router.post("/sessions")
async def create_session(request: CreateSessionRequest):
    """Create a new agent session"""
    session_id = f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    session = {
        "id": session_id,
        "title": request.title,
        "createdAt": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "status": "running",
        "steps": [],
        "deviceId": request.deviceId,
        "goal": request.goal,
        "isGolden": False,
        "feedback": None,
        "reportCards": []
    }
    _sessions_storage[session_id] = session
    save_sessions()
    logger.info(f"Created new session: {session_id}")
    return session


@router.post("/sessions/{session_id}/steps")
async def add_step(session_id: str, request: AddStepRequest):
    """Add a step to an existing session"""
    if session_id not in _sessions_storage:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = _sessions_storage[session_id]
    step_dict = request.step.dict()
    session["steps"].append(step_dict)
    save_sessions()
    logger.info(f"Added step {step_dict['stepNumber']} to session {session_id}")
    return {"success": True, "step": step_dict}


class UpdateStatusRequest(BaseModel):
    status: str
    lastError: Optional[str] = None
    retryAfterMs: Optional[int] = None


@router.patch("/sessions/{session_id}/status")
async def update_session_status(session_id: str, request: UpdateStatusRequest):
    """Update session status"""
    if session_id not in _sessions_storage:
        raise HTTPException(status_code=404, detail="Session not found")

    if request.status not in ["running", "completed", "failed", "paused"]:
        raise HTTPException(status_code=400, detail="Invalid status")

    _sessions_storage[session_id]["status"] = request.status
    if request.lastError:
        _sessions_storage[session_id]["lastError"] = request.lastError
    if request.retryAfterMs:
        _sessions_storage[session_id]["retryAfterMs"] = request.retryAfterMs
    save_sessions()
    logger.info(f"Updated session {session_id} status to {request.status}")
    return {"success": True, "status": request.status}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session"""
    if session_id not in _sessions_storage:
        raise HTTPException(status_code=404, detail="Session not found")

    del _sessions_storage[session_id]
    save_sessions()
    logger.info(f"Deleted session {session_id}")
    return {"success": True}


class UpdateTokensRequest(BaseModel):
    totalRequestTokens: int
    totalResponseTokens: int
    totalTokens: int


@router.patch("/sessions/{session_id}/tokens")
async def update_session_tokens(session_id: str, request: UpdateTokensRequest):
    if session_id not in _sessions_storage:
        raise HTTPException(status_code=404, detail="Session not found")

    session = _sessions_storage[session_id]
    session["totalRequestTokens"] = request.totalRequestTokens
    session["totalResponseTokens"] = request.totalResponseTokens
    session["totalTokens"] = request.totalTokens

    save_sessions()
    logger.info(f"Updated session {session_id} tokens - Request: {request.totalRequestTokens}, Response: {request.totalResponseTokens}, Total: {request.totalTokens}")
    return {"success": True}


class UpdateConversationHistoryRequest(BaseModel):
    conversationHistory: List[Dict[str, str]]


@router.patch("/sessions/{session_id}/conversation")
async def update_session_conversation(session_id: str, request: UpdateConversationHistoryRequest):
    """Update session conversation history for resuming"""
    if session_id not in _sessions_storage:
        raise HTTPException(status_code=404, detail="Session not found")

    session = _sessions_storage[session_id]
    session["conversationHistory"] = request.conversationHistory

    save_sessions()
    logger.info(f"Updated session {session_id} conversation history with {len(request.conversationHistory)} messages")
    return {"success": True}


@router.post("/sessions/{session_id}/resume")
async def resume_session(session_id: str):
    """Resume a paused or failed session"""
    if session_id not in _sessions_storage:
        raise HTTPException(status_code=404, detail="Session not found")

    session = _sessions_storage[session_id]

    # Check if session can be resumed
    if session["status"] not in ["paused", "failed"]:
        raise HTTPException(status_code=400, detail=f"Cannot resume session with status: {session['status']}")

    # Return session data for resuming
    return {
        "success": True,
        "session_id": session_id,
        "conversationHistory": session.get("conversationHistory", []),
        "lastError": session.get("lastError"),
        "retryAfterMs": session.get("retryAfterMs")
    }


# ============================================================================
# Deep-Agent Evaluation Endpoints
# ============================================================================

class ReviewRequest(BaseModel):
    feedback: Optional[str] = None   # "up" | "down" | null
    isGolden: Optional[bool] = None  # true | false | null (no-op if absent)


@router.patch("/sessions/{session_id}/review")
async def review_session(session_id: str, request: ReviewRequest):
    """Set feedback (up/down) and/or golden status for a session"""
    if session_id not in _sessions_storage:
        raise HTTPException(status_code=404, detail="Session not found")

    session = _sessions_storage[session_id]

    if request.feedback is not None:
        if request.feedback not in ("up", "down"):
            raise HTTPException(status_code=400, detail="feedback must be 'up' or 'down'")
        session["feedback"] = request.feedback

    if request.isGolden is not None:
        session["isGolden"] = request.isGolden

    save_sessions()
    logger.info(f"Reviewed session {session_id}: feedback={session.get('feedback')}, isGolden={session.get('isGolden')}")
    return {"success": True, "session": session}


@router.get("/goldens")
async def get_golden_sessions():
    """Return all sessions marked as golden (ground-truth references)"""
    goldens = [s for s in _sessions_storage.values() if s.get("isGolden")]
    goldens.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
    return {"goldens": goldens, "count": len(goldens)}


class ReportCardRequest(BaseModel):
    score: Optional[float] = None           # 0.0 – 1.0
    strategy: Optional[str] = None
    regressionDelta: Optional[float] = None
    notes: Optional[str] = None
    passedChecks: Optional[List[str]] = None
    failedChecks: Optional[List[str]] = None


@router.post("/sessions/{session_id}/report")
async def add_report_card(session_id: str, request: ReportCardRequest):
    """Append a report card (eval result) to a session"""
    if session_id not in _sessions_storage:
        raise HTTPException(status_code=404, detail="Session not found")

    session = _sessions_storage[session_id]
    if "reportCards" not in session or session["reportCards"] is None:
        session["reportCards"] = []

    card = {
        "id": f"rc-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}",
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "score": request.score,
        "strategy": request.strategy,
        "regressionDelta": request.regressionDelta,
        "notes": request.notes,
        "passedChecks": request.passedChecks or [],
        "failedChecks": request.failedChecks or [],
    }
    session["reportCards"].append(card)

    save_sessions()
    logger.info(f"Added report card to session {session_id}: score={request.score}")
    return {"success": True, "reportCard": card}

