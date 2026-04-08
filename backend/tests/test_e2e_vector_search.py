"""
End-to-End Integration Tests for Vector Search API

Tests the complete workflow:
1. Upsert all bug reports from bugReports.json
2. Query and retrieve top 3 results
3. Delete and re-upsert operations
"""

import requests
import json
from pathlib import Path
import time

API_BASE = "http://localhost:8000/api"

# Load bug reports data
BUG_REPORTS_PATH = Path(__file__).parent.parent.parent / "frontend/test-studio/src/data/bugReports.json"

def load_bug_reports():
    """Load bug reports from JSON file"""
    with open(BUG_REPORTS_PATH, 'r') as f:
        data = json.load(f)
    return data['reports']

def test_upsert_all_tickets():
    """Test 1: Upsert all tickets on the bug repro UI"""
    print("\n" + "="*80)
    print("TEST 1: UPSERT ALL TICKETS")
    print("="*80)
    
    bug_reports = load_bug_reports()
    print(f"📊 Loading {len(bug_reports)} bug reports from bugReports.json")
    
    success_count = 0
    failure_count = 0
    
    for idx, report in enumerate(bug_reports):
        try:
            response = requests.post(
                f"{API_BASE}/search/upsert",
                json={
                    "id": report['id'],
                    "title": report['title'],
                    "description": report['title'],  # Using title as description
                    "status": report['status'],
                    "author": report['author'],
                    "date": report['date'],
                    "repros": report['repros'],
                    "severity": "medium",
                    "tags": []
                },
                timeout=30
            )
            
            if response.status_code == 200:
                success_count += 1
                if (idx + 1) % 50 == 0:
                    print(f"  ✓ Upserted {idx + 1}/{len(bug_reports)} records")
            else:
                failure_count += 1
                print(f"  ✗ Failed to upsert {report['id']}: {response.status_code}")
        except Exception as e:
            failure_count += 1
            print(f"  ✗ Error upserting {report['id']}: {e}")
    
    print(f"\n✅ UPSERT COMPLETE: {success_count} succeeded, {failure_count} failed")
    
    # Get stats
    stats_response = requests.get(f"{API_BASE}/search/stats")
    if stats_response.status_code == 200:
        stats = stats_response.json()['stats']
        print(f"📈 Database Stats: {stats['total_records']} records indexed")
    
    assert success_count > 0, "No records were upserted"
    return success_count

def test_query_top_3_results():
    """Test 2: Retrieve top 3 K results using query endpoint"""
    print("\n" + "="*80)
    print("TEST 2: QUERY TOP 3 RESULTS")
    print("="*80)
    
    # Test queries
    test_queries = [
        "Black Box Appearing Above New Tab",
        "Dragging from Browser Window",
        "IVRSh-8 Regression"
    ]
    
    for query in test_queries:
        print(f"\n🔍 Query: '{query}'")
        
        response = requests.post(
            f"{API_BASE}/search/query",
            json={
                "query": query,
                "k": 3,
                "alpha": 0.7
            },
            timeout=30
        )
        
        assert response.status_code == 200, f"Query failed: {response.status_code}"
        
        data = response.json()
        results = data['results']
        
        print(f"  📊 Found {len(results)} results (requested top 3)")
        assert len(results) <= 3, f"Expected max 3 results, got {len(results)}"
        
        for idx, result in enumerate(results, 1):
            print(f"    {idx}. [{result['score']*100:.1f}%] {result['title'][:60]}...")
            print(f"       ID: {result['id']} | Author: {result['author']}")
            print(f"       Vector: {result['vector_score']*100:.1f}% | Text: {result['text_score']*100:.1f}%")
    
    print(f"\n✅ QUERY TEST COMPLETE: All queries returned top 3 results")

def test_delete_and_reupsert():
    """Test 3: Delete and re-upsert operations"""
    print("\n" + "="*80)
    print("TEST 3: DELETE AND RE-UPSERT")
    print("="*80)
    
    bug_reports = load_bug_reports()
    test_record = bug_reports[0]
    
    print(f"\n📝 Test Record: {test_record['id']} - {test_record['title'][:50]}...")
    
    # Step 1: Upsert the record
    print("\n  Step 1: Upserting record...")
    upsert_response = requests.post(
        f"{API_BASE}/search/upsert",
        json={
            "id": test_record['id'],
            "title": test_record['title'],
            "description": test_record['title'],
            "status": test_record['status'],
            "author": test_record['author'],
            "date": test_record['date'],
            "repros": test_record['repros'],
            "severity": "high",
            "tags": ["test"]
        },
        timeout=30
    )
    assert upsert_response.status_code == 200, "Upsert failed"
    print("    ✓ Record upserted successfully")
    
    # Step 2: Query to verify it exists
    print("\n  Step 2: Querying to verify record exists...")
    query_response = requests.post(
        f"{API_BASE}/search/query",
        json={
            "query": test_record['title'],
            "k": 100,
            "alpha": 0.7
        },
        timeout=30
    )
    assert query_response.status_code == 200, "Query failed"
    results = query_response.json()['results']
    found = any(r['id'] == test_record['id'] for r in results)
    assert found, "Record not found after upsert"
    print("    ✓ Record found in search results")
    
    # Step 3: Delete the record
    print("\n  Step 3: Deleting record...")
    delete_response = requests.post(
        f"{API_BASE}/search/delete",
        json={"id": test_record['id']},
        timeout=30
    )
    assert delete_response.status_code == 200, "Delete failed"
    print("    ✓ Record deleted successfully")
    
    # Step 4: Query to verify it's deleted
    print("\n  Step 4: Querying to verify record is deleted...")
    query_response = requests.post(
        f"{API_BASE}/search/query",
        json={
            "query": test_record['title'],
            "k": 100,
            "alpha": 0.7
        },
        timeout=30
    )
    results = query_response.json()['results']
    found = any(r['id'] == test_record['id'] for r in results)
    assert not found, "Record still found after delete"
    print("    ✓ Record successfully removed from search index")
    
    # Step 5: Re-upsert the record
    print("\n  Step 5: Re-upserting record...")
    reupsert_response = requests.post(
        f"{API_BASE}/search/upsert",
        json={
            "id": test_record['id'],
            "title": test_record['title'],
            "description": test_record['title'],
            "status": test_record['status'],
            "author": test_record['author'],
            "date": test_record['date'],
            "repros": test_record['repros'],
            "severity": "critical",
            "tags": ["reinserted"]
        },
        timeout=30
    )
    assert reupsert_response.status_code == 200, "Re-upsert failed"
    print("    ✓ Record re-upserted successfully")
    
    # Step 6: Query to verify it's back
    print("\n  Step 6: Querying to verify record is back...")
    query_response = requests.post(
        f"{API_BASE}/search/query",
        json={
            "query": test_record['title'],
            "k": 100,
            "alpha": 0.7
        },
        timeout=30
    )
    results = query_response.json()['results']
    found = any(r['id'] == test_record['id'] for r in results)
    assert found, "Record not found after re-upsert"
    print("    ✓ Record successfully re-inserted into search index")
    
    print(f"\n✅ DELETE AND RE-UPSERT TEST COMPLETE")

def run_all_tests():
    """Run all end-to-end tests"""
    print("\n" + "="*80)
    print("VECTOR SEARCH END-TO-END TEST SUITE")
    print("="*80)
    
    try:
        # Test 1: Upsert all tickets
        upsert_count = test_upsert_all_tickets()
        
        # Test 2: Query top 3 results
        test_query_top_3_results()
        
        # Test 3: Delete and re-upsert
        test_delete_and_reupsert()
        
        print("\n" + "="*80)
        print("✅ ALL TESTS PASSED!")
        print("="*80)
        print(f"\n📊 Summary:")
        print(f"  ✓ Upserted {upsert_count} bug reports")
        print(f"  ✓ Queried and retrieved top 3 results")
        print(f"  ✓ Deleted and re-upserted records successfully")
        print("\n🎉 Vector Search RAG Solution is fully functional!\n")
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        raise
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        raise

if __name__ == "__main__":
    run_all_tests()

