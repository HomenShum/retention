"""
Search Agent Module

Provides semantic search capabilities for bug reports and test scenarios.
"""

from .search_agent import create_search_agent
from .search_service import VectorSearchService
from .models import BugReportRecord

__all__ = [
    "create_search_agent",
    "VectorSearchService",
    "BugReportRecord",
]
