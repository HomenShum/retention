"""
Search Bug Reports Tool

Provides semantic search functionality for bug reports using vector search.
"""

import json
import logging
from agents import function_tool

logger = logging.getLogger(__name__)


def create_search_bug_reports_tool(vector_search_service):
    """
    Create the search_bug_reports tool with access to vector search service.
    
    Args:
        vector_search_service: VectorSearchService instance for semantic search
        
    Returns:
        Configured function_tool for searching bug reports
    """
    
    @function_tool
    def search_bug_reports(query: str, k: int = 5) -> str:
        """
        Search bug reports using AI-powered semantic search.
        
        Use this when users ask about:
        - "bugs", "issues", "problems", "errors"
        - Specific symptoms like "crash", "freeze", "black screen"
        - Platform-specific issues like "mobile bugs", "iOS bugs", "Android bugs"
        
        Args:
            query: Natural language search query (e.g., "mobile login crashes", "black screen on startup")
            k: Number of results to return (default: 5, max: 10)
            
        Returns:
            JSON string with bug report results including title, description, severity, and match scores
        """
        try:
            if not vector_search_service:
                return json.dumps({"error": "Vector search not available", "results": []})
                
            results = vector_search_service.query(query_text=query, k=min(k, 10), alpha=0.7)
            if not results:
                return json.dumps({"message": f"No bug reports found matching '{query}'", "results": []})
            
            # Format results
            formatted = []
            for r in results:
                formatted.append({
                    "id": r["id"],
                    "title": r["title"],
                    "description": r["description"][:200] + "..." if len(r["description"]) > 200 else r["description"],
                    "status": r["status"],
                    "severity": r["severity"],
                    "author": r["author"],
                    "repros": r["repros"],
                    "match_score": f"{r['score'] * 100:.0f}%",
                })
            
            return json.dumps({
                "message": f"Found {len(formatted)} bug report(s) matching '{query}'",
                "query": query,
                "results": formatted
            }, indent=2)
        except Exception as e:
            logger.error(f"Bug report search failed: {e}")
            return json.dumps({"error": f"Search failed: {str(e)}", "results": []})
    
    return search_bug_reports

