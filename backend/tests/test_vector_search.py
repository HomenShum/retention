"""
Integration tests for Vector Search Service

Tests:
1. Upsert bug reports with embeddings
2. Query with hybrid search (vector + full-text)
3. Delete bug reports
4. Verify top K results
"""

import pytest
import json
import tempfile
from typing import List, Dict, Any
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.agents.search import VectorSearchService, BugReportRecord


@pytest.fixture
def mock_openai_client():
    """Create a mock OpenAI client"""
    mock_client = Mock()
    mock_response = Mock()
    # Return a consistent embedding for testing
    mock_response.data = [Mock(embedding=[0.1] * 1536)]
    mock_client.embeddings.create.return_value = mock_response
    return mock_client


@pytest.fixture
def vector_search_service(mock_openai_client):
    """Create a vector search service instance with mocked OpenAI and isolated empty storage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_storage = str(Path(tmpdir) / "embeddings.json")
        with patch('app.agents.search.search_service.OpenAI', return_value=mock_openai_client):
            service = VectorSearchService(api_key="test-key", storage_path=tmp_storage)
            yield service


@pytest.fixture
def sample_bug_reports() -> List[Dict[str, Any]]:
    """Sample bug reports for testing"""
    return [
        {
            "id": "T234948804",
            "title": "[DIFF][BUG][IVRSh-8][Reg-v84] Black Box Appearing Above New Tab While Dragging from Browser Window",
            "description": "A black box appears above the new tab when dragging from the browser window. This occurs consistently when using the drag feature.",
            "status": "Running",
            "author": "Josh Anderson",
            "date": "1/5/2025, 12:03:22 PM",
            "repros": 3,
            "severity": "high",
            "tags": ["ui", "drag", "browser"]
        },
        {
            "id": "T234948805",
            "title": "[DIFF][BUG][IVRSh-8][Reg-v84] Black Box Appearing Above New Tab While Dragging from Browser Window",
            "description": "Similar issue with black box appearing during drag operations in the browser interface.",
            "status": "Running",
            "author": "Richmond Service User",
            "date": "1/5/2025, 12:01:22 PM",
            "repros": 3,
            "severity": "high",
            "tags": ["ui", "drag"]
        },
        {
            "id": "T242894317",
            "title": "[BUG][Twilight Android] Scroll overlaps with the content while scrolling on Share video page",
            "description": "When scrolling on the Share video page via Horizon option on Share sheet from Gallery, the scroll overlaps with content.",
            "status": "Finished",
            "author": "Richmond Service User",
            "date": "1/5/2025, 10:45:38 AM",
            "repros": 0,
            "severity": "medium",
            "tags": ["android", "scroll", "gallery"]
        }
    ]


class TestVectorSearchUpsert:
    """Test upsert functionality"""
    
    def test_upsert_single_record(self, vector_search_service, sample_bug_reports):
        """Test upserting a single bug report"""
        report = sample_bug_reports[0]
        record = BugReportRecord(**report)
        
        result = vector_search_service.upsert(record)
        
        assert result["success"] is True
        assert result["id"] == report["id"]
        assert result["embedding_dim"] == 1536
        assert vector_search_service.get_stats()["total_records"] == 1
    
    def test_upsert_multiple_records(self, vector_search_service, sample_bug_reports):
        """Test upserting multiple bug reports"""
        for report in sample_bug_reports:
            record = BugReportRecord(**report)
            result = vector_search_service.upsert(record)
            assert result["success"] is True
        
        stats = vector_search_service.get_stats()
        assert stats["total_records"] == len(sample_bug_reports)
    
    def test_upsert_overwrites_existing(self, vector_search_service, sample_bug_reports):
        """Test that upserting overwrites existing records"""
        report = sample_bug_reports[0]
        record1 = BugReportRecord(**report)
        
        result1 = vector_search_service.upsert(record1)
        assert result1["success"] is True
        
        # Upsert same ID with different data
        report["title"] = "Updated Title"
        record2 = BugReportRecord(**report)
        result2 = vector_search_service.upsert(record2)
        
        assert result2["success"] is True
        assert vector_search_service.get_stats()["total_records"] == 1
        assert vector_search_service.records[report["id"]].title == "Updated Title"

    def test_upsert_preserves_explicit_created_at(self, vector_search_service, sample_bug_reports):
        """Test that an explicit created_at value is preserved during upsert."""
        report = {**sample_bug_reports[0], "created_at": "2025-01-05T12:03:22Z"}
        record = BugReportRecord(**report)

        result = vector_search_service.upsert(record)

        assert result["success"] is True
        assert vector_search_service.records[report["id"]].created_at == report["created_at"]


class TestVectorSearchQuery:
    """Test query functionality"""
    
    def test_query_returns_top_k_results(self, vector_search_service, sample_bug_reports):
        """Test that query returns top K results"""
        # Upsert all reports
        for report in sample_bug_reports:
            record = BugReportRecord(**report)
            vector_search_service.upsert(record)
        
        # Query for black box issue
        results = vector_search_service.query("black box drag", k=2)
        
        assert len(results) <= 2
        assert all("score" in r for r in results)
        assert all("vector_score" in r for r in results)
        assert all("text_score" in r for r in results)
    
    def test_query_returns_sorted_by_score(self, vector_search_service, sample_bug_reports):
        """Test that results are sorted by score"""
        for report in sample_bug_reports:
            record = BugReportRecord(**report)
            vector_search_service.upsert(record)
        
        results = vector_search_service.query("scroll overlap", k=3)
        
        # Verify results are sorted by score (descending)
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)
    
    def test_query_with_custom_alpha(self, vector_search_service, sample_bug_reports):
        """Test query with custom alpha weight"""
        for report in sample_bug_reports:
            record = BugReportRecord(**report)
            vector_search_service.upsert(record)
        
        # Vector-heavy search
        results_vector = vector_search_service.query("black box", k=3, alpha=0.9)
        
        # Text-heavy search
        results_text = vector_search_service.query("black box", k=3, alpha=0.1)
        
        # Results should be different due to different weighting
        assert len(results_vector) > 0
        assert len(results_text) > 0
    
    def test_query_empty_database(self, vector_search_service):
        """Test query on empty database"""
        results = vector_search_service.query("test query", k=3)
        assert results == []


class TestVectorSearchDelete:
    """Test delete functionality"""
    
    def test_delete_existing_record(self, vector_search_service, sample_bug_reports):
        """Test deleting an existing record"""
        report = sample_bug_reports[0]
        record = BugReportRecord(**report)
        vector_search_service.upsert(record)
        
        assert vector_search_service.get_stats()["total_records"] == 1
        
        result = vector_search_service.delete(report["id"])
        
        assert result["success"] is True
        assert vector_search_service.get_stats()["total_records"] == 0
    
    def test_delete_nonexistent_record(self, vector_search_service):
        """Test deleting a non-existent record"""
        result = vector_search_service.delete("nonexistent_id")
        
        assert result["success"] is False
        assert "not found" in result["error"].lower()
    
    def test_delete_removes_from_indices(self, vector_search_service, sample_bug_reports):
        """Test that delete removes from all indices"""
        for report in sample_bug_reports:
            record = BugReportRecord(**report)
            vector_search_service.upsert(record)
        
        report_id = sample_bug_reports[0]["id"]
        vector_search_service.delete(report_id)
        
        # Query should not return deleted record
        results = vector_search_service.query("black box", k=10)
        result_ids = [r["id"] for r in results]
        
        assert report_id not in result_ids


class TestVectorSearchIntegration:
    """Integration tests for complete workflow"""
    
    def test_upsert_query_delete_workflow(self, vector_search_service, sample_bug_reports):
        """Test complete workflow: upsert -> query -> delete -> query"""
        # Step 1: Upsert all reports
        for report in sample_bug_reports:
            record = BugReportRecord(**report)
            result = vector_search_service.upsert(record)
            assert result["success"] is True
        
        assert vector_search_service.get_stats()["total_records"] == 3
        
        # Step 2: Query and verify results
        results = vector_search_service.query("black box", k=3)
        assert len(results) > 0
        initial_result_count = len(results)
        
        # Step 3: Delete one record
        deleted_id = sample_bug_reports[0]["id"]
        delete_result = vector_search_service.delete(deleted_id)
        assert delete_result["success"] is True
        
        assert vector_search_service.get_stats()["total_records"] == 2
        
        # Step 4: Query again and verify deleted record is gone
        results_after_delete = vector_search_service.query("black box", k=3)
        result_ids = [r["id"] for r in results_after_delete]
        
        assert deleted_id not in result_ids
    
    def test_reupsert_after_delete(self, vector_search_service, sample_bug_reports):
        """Test re-upserting a record after deletion"""
        report = sample_bug_reports[0]
        record = BugReportRecord(**report)
        
        # Upsert
        result1 = vector_search_service.upsert(record)
        assert result1["success"] is True
        
        # Delete
        delete_result = vector_search_service.delete(report["id"])
        assert delete_result["success"] is True
        
        # Re-upsert
        result2 = vector_search_service.upsert(record)
        assert result2["success"] is True
        
        assert vector_search_service.get_stats()["total_records"] == 1
        
        # Verify can query
        results = vector_search_service.query(report["title"], k=1)
        assert len(results) == 1
        assert results[0]["id"] == report["id"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

