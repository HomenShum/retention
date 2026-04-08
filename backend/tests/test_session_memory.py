"""
Unit tests for SessionMemory, LearningStore, and SessionEvaluator

Tests the GPT-5.4 Self-Explore pattern implementation:
1. SessionMemory - session-scoped learning
2. LearningStore - cross-session persistence
3. SessionEvaluator - LLM-as-judge evaluation
"""

import pytest
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import sys

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.agents.device_testing.session_memory import (
    SessionMemory,
    LearningStore,
    SessionEvaluator,
    FailureRecord,
    ActionRecord,
    get_learning_store,
    get_session_evaluator,
)


# =============================================================================
# SessionMemory Tests
# =============================================================================

class TestSessionMemory:
    """Tests for SessionMemory class"""

    def test_init(self):
        """Test session memory initialization"""
        session = SessionMemory(task_goal="search for video", device_id="emulator-5554")
        assert session.task_goal == "search for video"
        assert session.device_id == "emulator-5554"
        assert len(session.actions) == 0
        assert len(session.failures) == 0

    def test_record_action(self):
        """Test recording actions"""
        session = SessionMemory(task_goal="test", device_id="device-1")
        session.record_action(
            action="tap search button",
            state_before={"screen": "home"},
            state_after={"screen": "search"},
            success=True,
        )
        assert len(session.actions) == 1
        assert session.actions[0].action == "tap search button"
        assert session.actions[0].success is True

    def test_record_failure(self):
        """Test recording failures"""
        session = SessionMemory(task_goal="test", device_id="device-1")
        context = session.record_failure(
            action="tap element",
            state_before={"screen": "home"},
            state_after={"screen": "home"},
            error="Element not found",
            failure_type="PERCEPTION_ERROR",
            root_cause="Element was hidden",
            recovery_strategy="Scroll down to find element",
        )
        assert len(session.failures) == 1
        assert session.failures[0].failure_type == "PERCEPTION_ERROR"

    def test_repeated_failure_detection(self):
        """Test that repeated failures are detected"""
        session = SessionMemory(task_goal="test", device_id="device-1")

        # Record same failure twice
        session.record_failure(
            action="tap element",
            state_before={}, state_after={},
            error="Error", failure_type="PLANNING_ERROR",
            root_cause="Wrong element", recovery_strategy="Try again",
        )
        context = session.record_failure(
            action="tap element",
            state_before={}, state_after={},
            error="Error", failure_type="PLANNING_ERROR",
            root_cause="Wrong element", recovery_strategy="Try again",
        )

        assert "2nd time" in context or session.repeated_failures.get("PLANNING_ERROR:tap element", 0) == 2

    def test_mark_recovery_successful(self):
        """Test marking recovery as successful"""
        session = SessionMemory(task_goal="test", device_id="device-1")
        session.record_failure(
            action="tap", state_before={}, state_after={},
            error="err", failure_type="ERROR",
            root_cause="cause", recovery_strategy="scroll",
        )
        session.mark_recovery_successful("scroll")

        assert session.failures[0].recovery_successful is True
        assert "scroll" in session.successful_recoveries

    def test_get_summary(self):
        """Test session summary generation"""
        session = SessionMemory(task_goal="test task", device_id="device-1")
        session.record_action("action1", {}, {}, True)
        session.record_action("action2", {}, {}, False)

        summary = session.get_summary()

        assert summary["task_goal"] == "test task"
        assert summary["total_actions"] == 2
        assert summary["successful_actions"] == 1

    def test_get_context_for_agent(self):
        """Test context string for agent"""
        session = SessionMemory(task_goal="test", device_id="device-1")
        session.record_action("action1", {}, {}, True)

        context = session.get_context_for_agent()

        assert "test" in context
        assert "action1" in context


# =============================================================================
# LearningStore Tests
# =============================================================================

class TestLearningStore:
    """Tests for LearningStore class"""

    @pytest.fixture
    def temp_store(self, tmp_path):
        """Create a temporary learning store"""
        store_path = tmp_path / "test_learnings.json"
        store = LearningStore(store_path=str(store_path))
        return store

    def test_init_creates_empty_store(self, temp_store):
        """Test that initialization creates empty store"""
        assert temp_store._learnings["metadata"]["total_learnings"] == 0
        assert temp_store._learnings["recovery_strategies"] == {}

    def test_increment_recovery_count(self, temp_store):
        """Test that recovery count increments on repeated strategy"""
        temp_store.record_successful_recovery("PLANNING_ERROR", "scroll down")
        temp_store.record_successful_recovery("PLANNING_ERROR", "scroll down")

        strategies = temp_store._learnings["recovery_strategies"]["PLANNING_ERROR"]
        assert strategies[0]["success_count"] == 2

    def test_record_app_pattern(self, temp_store):
        """Test recording app navigation patterns"""
        temp_store.record_app_pattern(
            app_name="YouTube",
            goal="search for video",
            successful_actions=["tap search", "type query", "tap result"]
        )

        patterns = temp_store._learnings["app_patterns"]["YouTube"]
        assert len(patterns) == 1
        assert patterns[0]["goal"] == "search for video"
        assert len(patterns[0]["actions"]) == 3

    def test_record_failure_pattern(self, temp_store):
        """Test recording failure patterns"""
        temp_store.record_failure_pattern(
            failure_type="PERCEPTION_ERROR",
            context="Home screen",
            cause="Element obscured by popup"
        )

        patterns = temp_store._learnings["failure_patterns"]["PERCEPTION_ERROR"]
        assert len(patterns) == 1
        assert patterns[0]["cause"] == "Element obscured by popup"

    def test_record_evaluation(self, temp_store):
        """Test recording evaluation scores"""
        temp_store.record_evaluation("session-123", 0.85, "Good performance")

        scores = temp_store._learnings["evaluation_scores"]
        assert len(scores) == 1
        assert scores[0]["score"] == 0.85
        assert scores[0]["session_id"] == "session-123"

    def test_get_recovery_strategies(self, temp_store):
        """Test retrieving recovery strategies sorted by success"""
        temp_store.record_successful_recovery("ERROR", "strategy A")
        temp_store.record_successful_recovery("ERROR", "strategy B")
        temp_store.record_successful_recovery("ERROR", "strategy A")  # 2nd time

        strategies = temp_store.get_recovery_strategies("ERROR")

        assert strategies[0] == "strategy A"  # Most successful first

    def test_get_app_patterns(self, temp_store):
        """Test retrieving app patterns"""
        temp_store.record_app_pattern("Chrome", "search web", ["tap url bar", "type query"])

        patterns = temp_store.get_app_patterns("Chrome")

        assert len(patterns) == 1
        assert patterns[0]["goal"] == "search web"

    def test_get_learning_context(self, temp_store):
        """Test formatted learning context"""
        temp_store.record_successful_recovery("PLANNING_ERROR", "wait for element")
        temp_store.record_app_pattern("YouTube", "play video", ["search", "tap"])

        context = temp_store.get_learning_context(
            app_name="YouTube",
            failure_type="PLANNING_ERROR"
        )

        assert "PLANNING_ERROR" in context
        assert "YouTube" in context

    def test_persistence(self, tmp_path):
        """Test that learnings persist to disk"""
        store_path = tmp_path / "persist_test.json"

        # Create store and add data
        store1 = LearningStore(store_path=str(store_path))
        store1.record_successful_recovery("ERROR", "test strategy")

        # Create new store instance - should load from disk
        store2 = LearningStore(store_path=str(store_path))

        strategies = store2.get_recovery_strategies("ERROR")
        assert "test strategy" in strategies

    def test_average_score(self, temp_store):
        """Test average score calculation"""
        temp_store.record_evaluation("s1", 0.8, "Good")
        temp_store.record_evaluation("s2", 0.6, "OK")

        avg = temp_store.get_average_score()

        assert avg == 0.7


# =============================================================================
# SessionEvaluator Tests
# =============================================================================

class TestSessionEvaluator:
    """Tests for SessionEvaluator class"""

    @pytest.fixture
    def mock_openai_response(self):
        """Create a mock OpenAI response"""
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = json.dumps({
            "score": 0.85,
            "reasoning": "Task completed efficiently",
            "strengths": ["Good recovery", "Fast completion"],
            "improvements": ["Could reduce failures"],
            "learned_patterns": ["Scroll before tap"],
        })
        return mock_response

    @pytest.fixture
    def evaluator_with_mock(self, mock_openai_response, tmp_path):
        """Create evaluator with mocked OpenAI"""
        with patch('app.agents.device_testing.session_memory.get_learning_store') as mock_store:
            mock_store.return_value = LearningStore(store_path=str(tmp_path / "eval_test.json"))

            evaluator = SessionEvaluator()
            mock_client = Mock()
            mock_client.chat.completions.create.return_value = mock_openai_response
            evaluator._client = mock_client

            yield evaluator

    def test_evaluate_session(self, evaluator_with_mock):
        """Test session evaluation"""
        session = SessionMemory(task_goal="search video", device_id="emulator-5554")
        session.record_action("tap search", {}, {}, True)
        session.record_action("type query", {}, {}, True)

        result = evaluator_with_mock.evaluate_session(session)

        assert "score" in result
        assert result["score"] == 0.85
        assert "reasoning" in result
        assert "strengths" in result

    def test_evaluate_session_with_failures(self, evaluator_with_mock):
        """Test evaluation of session with failures"""
        session = SessionMemory(task_goal="test", device_id="device-1")
        session.record_action("action1", {}, {}, True)
        session.record_failure(
            action="tap", state_before={}, state_after={},
            error="err", failure_type="PLANNING_ERROR",
            root_cause="cause", recovery_strategy="retry",
        )

        result = evaluator_with_mock.evaluate_session(session)

        assert result["score"] is not None

    def test_invalid_json_response(self, tmp_path):
        """Test handling of invalid JSON response"""
        with patch('app.agents.device_testing.session_memory.get_learning_store') as mock_store:
            mock_store.return_value = LearningStore(store_path=str(tmp_path / "err_test.json"))

            evaluator = SessionEvaluator()
            mock_client = Mock()
            mock_response = Mock()
            mock_response.choices = [Mock()]
            mock_response.choices[0].message.content = "invalid json"
            mock_client.chat.completions.create.return_value = mock_response
            evaluator._client = mock_client

            session = SessionMemory(task_goal="test", device_id="device-1")
            session.record_action("action", {}, {}, True)

            result = evaluator.evaluate_session(session)

            assert result["score"] == 0.5  # Default on error
            assert "error" in result["session_id"].lower() or "parsing" in result["reasoning"].lower()


# =============================================================================
# Global Instance Tests
# =============================================================================

class TestGlobalInstances:
    """Tests for global instance getters"""

    def test_get_learning_store_singleton(self):
        """Test that get_learning_store returns singleton"""
        store1 = get_learning_store()
        store2 = get_learning_store()
        assert store1 is store2

    def test_get_session_evaluator_singleton(self):
        """Test that get_session_evaluator returns singleton"""
        eval1 = get_session_evaluator()
        eval2 = get_session_evaluator()
        assert eval1 is eval2
