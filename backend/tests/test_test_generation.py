"""
Tests for Test Case Generation API

Tests:
1. Parse PRD endpoint
2. Generate test cases endpoint
3. Device presets endpoint
4. App targets endpoint
5. Test types endpoint
6. Refine tests endpoint
"""

import pytest
import sys
from pathlib import Path
from fastapi.testclient import TestClient

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.main import app


@pytest.fixture
def client():
    """Create a test client for the FastAPI app"""
    return TestClient(app)


class TestDevicePresets:
    """Tests for device presets endpoint"""

    def test_get_device_presets(self, client):
        """Test getting device presets returns valid data"""
        response = client.get("/api/test-generation/device-presets")
        assert response.status_code == 200
        
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0
        
        # Check first device has required fields
        device = data[0]
        assert "device_id" in device
        assert "name" in device
        assert "platform" in device
        assert "sdk_version" in device
        assert "resolution" in device
        assert "dpi" in device

    def test_device_presets_include_android_and_ios(self, client):
        """Test that presets include both Android and iOS devices"""
        response = client.get("/api/test-generation/device-presets")
        data = response.json()

        platforms = {d["platform"].lower() for d in data}
        assert "android" in platforms
        assert "ios" in platforms


class TestAppTargets:
    """Tests for app targets endpoint"""

    def test_get_app_targets(self, client):
        """Test getting app targets returns valid data"""
        response = client.get("/api/test-generation/app-targets")
        assert response.status_code == 200
        
        data = response.json()
        assert "apps" in data
        assert isinstance(data["apps"], list)
        assert len(data["apps"]) > 0
        
        # Check first app has required fields
        app = data["apps"][0]
        assert "id" in app
        assert "name" in app
        assert "package" in app
        assert "category" in app


class TestTestTypes:
    """Tests for test types endpoint"""

    def test_get_test_types(self, client):
        """Test getting test types returns valid data"""
        response = client.get("/api/test-generation/test-types")
        assert response.status_code == 200
        
        data = response.json()
        assert "test_types" in data
        assert isinstance(data["test_types"], list)
        assert len(data["test_types"]) > 0
        
        # Check first test type has required fields
        test_type = data["test_types"][0]
        assert "id" in test_type
        assert "name" in test_type
        assert "description" in test_type


class TestParsePRD:
    """Tests for PRD parsing endpoint"""

    def test_parse_prd_with_user_stories(self, client):
        """Test parsing PRD content with user stories"""
        prd_content = """
        As a user, I want to create notes so that I can capture my ideas.
        As a user, I want to search notes so that I can find information quickly.
        
        Acceptance Criteria:
        - Notes should support rich text formatting
        - Search should return results in real-time
        """
        
        response = client.post(
            "/api/test-generation/parse-prd",
            json={"content": prd_content, "title": "Notes App PRD"}
        )
        assert response.status_code == 200
        
        data = response.json()
        assert "user_stories" in data
        assert isinstance(data["user_stories"], list)
        assert len(data["user_stories"]) >= 1

    def test_parse_prd_empty_content(self, client):
        """Test parsing empty PRD content"""
        response = client.post(
            "/api/test-generation/parse-prd",
            json={"content": "", "title": "Empty PRD"}
        )
        assert response.status_code == 200
        
        data = response.json()
        assert "user_stories" in data
        # Empty content should return empty or minimal stories
        assert isinstance(data["user_stories"], list)


class TestGenerateTests:
    """Tests for test generation endpoint"""

    def test_generate_tests_basic(self, client):
        """Test basic test generation"""
        response = client.post(
            "/api/test-generation/generate",
            json={
                "prd_content": "As a user, I want to create notes.",
                "target_app": "markor",
                "test_types": ["functional"],
                "include_edge_cases": False
            }
        )
        assert response.status_code == 200

        data = response.json()
        assert "test_cases" in data
        assert isinstance(data["test_cases"], list)

    def test_generate_tests_with_edge_cases(self, client):
        """Test generation with edge cases enabled"""
        response = client.post(
            "/api/test-generation/generate",
            json={
                "prd_content": "As a user, I want to create notes so that I can capture ideas.",
                "target_app": "markor",
                "test_types": ["functional", "edge_case"],
                "include_edge_cases": True
            }
        )
        assert response.status_code == 200

        data = response.json()
        assert "test_cases" in data
        assert len(data["test_cases"]) > 0

    def test_generate_tests_with_user_stories(self, client):
        """Test generation with pre-parsed user stories"""
        user_stories = [
            {
                "id": "US-001",
                "title": "Create Note",
                "description": "User can create a new note",
                "priority": "high",
                "acceptance_criteria": [
                    {"id": "AC-001", "description": "Note is saved successfully"}
                ]
            }
        ]

        response = client.post(
            "/api/test-generation/generate",
            json={
                "user_stories": user_stories,
                "target_app": "markor",
                "test_types": ["functional"],
                "include_edge_cases": False
            }
        )
        assert response.status_code == 200

        data = response.json()
        assert "test_cases" in data


class TestRefineTests:
    """Tests for test refinement endpoint"""

    def test_refine_tests_enhance(self, client):
        """Test enhancing test cases"""
        test_cases = [
            {
                "task_id": "TC-001",
                "category": "functional",
                "app": "markor",
                "description": "Create a new note",
                "actions": [{"type": "tap", "description": "Tap create button"}],
                "expected_result": "Note is created"
            }
        ]

        response = client.post(
            "/api/test-generation/refine",
            json={
                "test_cases": test_cases,
                "refinement_type": "enhance",
                "target_app": "markor"
            }
        )
        assert response.status_code == 200

        data = response.json()
        assert "refined_tests" in data
        assert isinstance(data["refined_tests"], list)

    def test_refine_tests_add_edge_cases(self, client):
        """Test adding edge cases to test cases"""
        test_cases = [
            {
                "task_id": "TC-001",
                "category": "functional",
                "app": "markor",
                "description": "Create a new note",
                "actions": [{"type": "tap", "description": "Tap create button"}],
                "expected_result": "Note is created"
            }
        ]

        response = client.post(
            "/api/test-generation/refine",
            json={
                "test_cases": test_cases,
                "refinement_type": "add_edge_cases",
                "target_app": "markor"
            }
        )
        assert response.status_code == 200

        data = response.json()
        assert "refined_tests" in data

    def test_refine_tests_simplify(self, client):
        """Test simplifying test cases"""
        test_cases = [
            {
                "task_id": "TC-001",
                "category": "functional",
                "app": "markor",
                "description": "Create a new note with title and body content",
                "actions": [
                    {"type": "tap", "description": "Tap create button"},
                    {"type": "type", "description": "Enter title"},
                    {"type": "type", "description": "Enter body"},
                    {"type": "tap", "description": "Save"}
                ],
                "expected_result": "Note is created and saved"
            }
        ]

        response = client.post(
            "/api/test-generation/refine",
            json={
                "test_cases": test_cases,
                "refinement_type": "simplify",
                "target_app": "markor"
            }
        )
        assert response.status_code == 200

        data = response.json()
        assert "refined_tests" in data


class TestGenerateWithAgent:
    """Tests for agent-based generation endpoint"""

    def test_generate_with_agent_returns_stream(self, client):
        """Test that agent generation returns a streaming response"""
        response = client.post(
            "/api/test-generation/generate-with-agent",
            json={
                "prd_content": "As a user, I want to create notes.",
                "target_app": "markor",
                "test_types": ["functional"],
                "include_edge_cases": False
            }
        )
        # Should return 200 for streaming response
        assert response.status_code == 200

        # Content type should be event-stream for SSE
        assert "text/event-stream" in response.headers.get("content-type", "")

