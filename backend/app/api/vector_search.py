"""
Vector Search API Router

Provides endpoints for vector search operations on bug reports:
- Upsert bug reports with embeddings
- Query using hybrid search (vector + full-text)
- Delete bug reports
- Get statistics and records
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
import logging

from ..agents.search import VectorSearchService, BugReportRecord

logger = logging.getLogger(__name__)


# ============================================================================
# Pydantic Models
# ============================================================================

class SearchUpsertRequest(BaseModel):
    """Request to upsert a bug report for search"""
    id: str
    title: str
    description: str
    status: str
    author: str
    date: str
    repros: int
    severity: str = "medium"
    tags: List[str] = Field(default_factory=list)
    created_at: Optional[str] = None


class SearchQueryRequest(BaseModel):
    """Request to query bug reports"""
    query: str
    k: int = 3
    alpha: float = 0.7


class SearchDeleteRequest(BaseModel):
    """Request to delete a bug report from search"""
    id: str

# Create router
router = APIRouter(prefix="/api/search", tags=["vector-search"])


# ============================================================================
# Dependency Injection
# ============================================================================

# This will be set by main.py during startup
_vector_search_service: VectorSearchService = None


def set_vector_search_service(service: VectorSearchService):
    """Set the vector search service instance"""
    global _vector_search_service
    _vector_search_service = service


def get_vector_search_service() -> VectorSearchService:
    """Get the vector search service instance"""
    if _vector_search_service is None:
        raise RuntimeError("Vector search service not initialized")
    return _vector_search_service


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/upsert")
async def search_upsert(request: SearchUpsertRequest) -> Dict[str, Any]:
    """
    Upsert a bug report for vector search

    Creates or updates a bug report record with:
    - Vector embedding using OpenAI text-embedding-3-small
    - Full-text search indexing
    - TOON format optimization for token efficiency

    Args:
        request: SearchUpsertRequest with bug report data

    Returns:
        Upsert result with metadata
    """
    try:
        service = get_vector_search_service()
        record = BugReportRecord(
            id=request.id,
            title=request.title,
            description=request.description,
            status=request.status,
            author=request.author,
            date=request.date,
            repros=request.repros,
            severity=request.severity,
            tags=request.tags,
            created_at=request.created_at,
        )

        result = service.upsert(record)
        return result

    except Exception as e:
        logger.error(f"Error in search upsert: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query")
async def search_query(request: SearchQueryRequest) -> Dict[str, Any]:
    """
    Query bug reports using hybrid search (vector + full-text)

    Performs:
    1. Vector similarity search using embeddings
    2. Full-text search on title and description
    3. Combines scores with configurable alpha weight

    Args:
        request: SearchQueryRequest with query and parameters

    Returns:
        Top K results with combined scores
    """
    try:
        service = get_vector_search_service()
        results = service.query(
            query_text=request.query,
            k=request.k,
            alpha=request.alpha
        )

        return {
            "success": True,
            "query": request.query,
            "results_count": len(results),
            "results": results
        }

    except Exception as e:
        logger.error(f"Error in search query: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/delete")
async def search_delete(request: SearchDeleteRequest) -> Dict[str, Any]:
    """
    Delete a bug report from vector search

    Removes record from:
    - Vector embeddings store
    - Full-text search index
    - In-memory storage

    Args:
        request: SearchDeleteRequest with record ID

    Returns:
        Delete result
    """
    try:
        service = get_vector_search_service()
        result = service.delete(request.id)
        return result

    except Exception as e:
        logger.error(f"Error in search delete: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def search_stats() -> Dict[str, Any]:
    """Get vector search service statistics"""
    try:
        service = get_vector_search_service()
        stats = service.get_stats()
        return {
            "success": True,
            "stats": stats
        }
    except Exception as e:
        logger.error(f"Error getting search stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/records")
async def get_all_records() -> Dict[str, Any]:
    """Get all embedded records for display"""
    try:
        service = get_vector_search_service()
        records = service.get_all_records()
        return {
            "success": True,
            "total": len(records),
            "records": records
        }
    except Exception as e:
        logger.error(f"Error getting all records: {e}")
        raise HTTPException(status_code=500, detail=str(e))

