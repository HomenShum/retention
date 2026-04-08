"""
Search Agent Models

Pydantic models and dataclasses specific to the search agent.
"""

from typing import List, Optional
from datetime import datetime, timezone
from dataclasses import dataclass


@dataclass
class BugReportRecord:
    """Bug report record with metadata"""
    id: str
    title: str
    description: str
    status: str
    author: str
    date: str
    repros: int
    severity: str = "medium"
    tags: List[str] = None
    embedding: Optional[List[float]] = None
    created_at: str = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc).isoformat()

