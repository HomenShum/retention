"""
Session Memory for Device Testing Agent

Tracks failures, actions, and learnings across a single navigation session
to enable learning from past failures and avoid repeating mistakes.
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class FailureRecord(BaseModel):
    """Record of a single failure during navigation"""
    timestamp: str
    action: str
    state_before: Dict[str, Any]
    state_after: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    failure_type: str  # PLANNING_ERROR, PERCEPTION_ERROR, ENVIRONMENT_ERROR, EXECUTION_ERROR
    root_cause: str
    recovery_strategy: str
    recovery_successful: Optional[bool] = None


class ActionRecord(BaseModel):
    """Record of a single action taken during navigation"""
    timestamp: str
    action: str
    state_before: Dict[str, Any]
    state_after: Optional[Dict[str, Any]] = None
    success: bool
    notes: Optional[str] = None
    # Exact MCP tool calls for this action — enables deterministic replay without re-parsing action text
    # Each entry: {"tool": str, "params": dict}
    mcp_tool_calls: Optional[List[Dict[str, Any]]] = None


class SessionMemory:
    """
    Session memory for tracking navigation history and learning from failures.
    
    This memory is scoped to a single navigation session (one task execution).
    It tracks:
    - All actions taken
    - All failures encountered
    - Patterns in failures
    - Successful recovery strategies
    """
    
    def __init__(self, task_goal: str, device_id: str):
        """
        Initialize session memory for a navigation task.
        
        Args:
            task_goal: The goal the agent is trying to achieve
            device_id: Device identifier
        """
        self.task_goal = task_goal
        self.device_id = device_id
        self.started_at = datetime.now(timezone.utc).isoformat()
        
        # History tracking
        self.actions: List[ActionRecord] = []
        self.failures: List[FailureRecord] = []
        
        # Pattern tracking
        self.repeated_failures: Dict[str, int] = {}  # action -> count
        self.successful_recoveries: List[str] = []
        
        logger.info(f"Session memory initialized for task: {task_goal}")
    
    def record_action(
        self,
        action: str,
        state_before: Dict[str, Any],
        state_after: Optional[Dict[str, Any]] = None,
        success: bool = True,
        notes: Optional[str] = None,
        mcp_tool_calls: Optional[List[Dict[str, Any]]] = None,
    ):
        """
        Record an action taken during navigation.

        Args:
            action: Description of the action
            state_before: Screen state before action
            state_after: Screen state after action
            success: Whether the action succeeded
            notes: Optional notes about the action
            mcp_tool_calls: Exact MCP tool calls made (enables deterministic replay)
        """
        record = ActionRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            action=action,
            state_before=state_before,
            state_after=state_after,
            success=success,
            notes=notes,
            mcp_tool_calls=mcp_tool_calls,
        )
        self.actions.append(record)
        logger.debug(f"Recorded action: {action} (success={success})")
    
    def record_failure(
        self,
        action: str,
        state_before: Dict[str, Any],
        state_after: Optional[Dict[str, Any]],
        error: Optional[str],
        failure_type: str,
        root_cause: str,
        recovery_strategy: str
    ) -> str:
        """
        Record a failure and return context about similar past failures.
        
        Args:
            action: The action that failed
            state_before: Screen state before action
            state_after: Screen state after action
            error: Error message
            failure_type: Type of failure (PLANNING_ERROR, etc.)
            root_cause: Root cause description
            recovery_strategy: Suggested recovery strategy
            
        Returns:
            Context string about similar past failures to help avoid repeating mistakes
        """
        record = FailureRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            action=action,
            state_before=state_before,
            state_after=state_after,
            error=error,
            failure_type=failure_type,
            root_cause=root_cause,
            recovery_strategy=recovery_strategy
        )
        self.failures.append(record)
        
        # Track repeated failures
        failure_key = f"{failure_type}:{action}"
        self.repeated_failures[failure_key] = self.repeated_failures.get(failure_key, 0) + 1
        
        logger.warning(f"Recorded failure: {action} ({failure_type})")
        
        # Generate context about similar failures
        return self._generate_failure_context(action, failure_type)
    
    def mark_recovery_successful(self, recovery_strategy: str):
        """
        Mark a recovery strategy as successful.
        
        Args:
            recovery_strategy: The recovery strategy that worked
        """
        if self.failures:
            self.failures[-1].recovery_successful = True
            self.successful_recoveries.append(recovery_strategy)
            logger.info(f"Recovery successful: {recovery_strategy}")
    
    def mark_recovery_failed(self):
        """Mark the most recent recovery attempt as failed."""
        if self.failures:
            self.failures[-1].recovery_successful = False
            logger.warning("Recovery failed")
    
    def _generate_failure_context(self, action: str, failure_type: str) -> str:
        """
        Generate context about similar past failures.
        
        Args:
            action: Current action that failed
            failure_type: Type of failure
            
        Returns:
            Context string with insights from past failures
        """
        context_parts = []
        
        # Check for repeated failures
        failure_key = f"{failure_type}:{action}"
        repeat_count = self.repeated_failures.get(failure_key, 0)
        
        if repeat_count > 1:
            context_parts.append(
                f"⚠️ WARNING: This is the {repeat_count}th time this action has failed with {failure_type}. "
                f"Consider a completely different approach."
            )
        
        # Find similar failures
        similar_failures = [
            f for f in self.failures[:-1]  # Exclude the one we just added
            if f.failure_type == failure_type
        ]
        
        if similar_failures:
            context_parts.append(f"\n📊 Past {failure_type} failures in this session: {len(similar_failures)}")
            
            # Show successful recoveries for this failure type
            successful = [f for f in similar_failures if f.recovery_successful]
            if successful:
                context_parts.append(f"✅ {len(successful)} were successfully recovered using:")
                for f in successful[-2:]:  # Show last 2 successful recoveries
                    context_parts.append(f"  - {f.recovery_strategy}")
        
        # Show overall failure patterns
        if len(self.failures) > 3:
            failure_types = [f.failure_type for f in self.failures]
            most_common = max(set(failure_types), key=failure_types.count)
            count = failure_types.count(most_common)
            if count > 2:
                context_parts.append(
                    f"\n🔍 Pattern detected: {most_common} is the most common failure type ({count} times)"
                )
        
        return "\n".join(context_parts) if context_parts else ""
    
    def get_summary(self) -> Dict[str, Any]:
        """
        Get a summary of the session.
        
        Returns:
            Dictionary with session statistics
        """
        total_actions = len(self.actions)
        successful_actions = len([a for a in self.actions if a.success])
        total_failures = len(self.failures)
        successful_recoveries = len([f for f in self.failures if f.recovery_successful])
        
        failure_types = {}
        for f in self.failures:
            failure_types[f.failure_type] = failure_types.get(f.failure_type, 0) + 1
        
        return {
            "task_goal": self.task_goal,
            "device_id": self.device_id,
            "started_at": self.started_at,
            "total_actions": total_actions,
            "successful_actions": successful_actions,
            "total_failures": total_failures,
            "successful_recoveries": successful_recoveries,
            "failure_types": failure_types,
            "repeated_failures": self.repeated_failures,
            "recovery_success_rate": (
                successful_recoveries / total_failures if total_failures > 0 else 0
            )
        }
    
    def generate_reflection_prompt(
        self,
        failed_action: str,
        failure_diagnosis: Dict[str, Any],
    ) -> str:
        """
        Generate a structured reflection prompt before retrying a failed action.

        Synthesizes past failures, recovery attempts, and patterns into a prompt
        that helps the agent reason about what went wrong and plan a better approach.

        Args:
            failed_action: The action that just failed
            failure_diagnosis: Diagnosis from the Failure Diagnosis Specialist

        Returns:
            Structured reflection prompt for the agent
        """
        # Gather past attempts for this action type
        past_attempts = [
            f for f in self.failures
            if f.action == failed_action or f.failure_type == failure_diagnosis.get("failure_type", "")
        ]

        # Format past attempts
        attempts_text = ""
        if past_attempts:
            for i, attempt in enumerate(past_attempts[-3:], 1):
                status = "✅ recovered" if attempt.recovery_successful else "❌ failed"
                attempts_text += (
                    f"  Attempt {i}: {attempt.action} → {attempt.failure_type} "
                    f"({status})\n"
                    f"    Root cause: {attempt.root_cause}\n"
                    f"    Recovery tried: {attempt.recovery_strategy}\n"
                )
        else:
            attempts_text = "  (first failure of this type)\n"

        # Identify patterns
        pattern_text = ""
        failure_key = f"{failure_diagnosis.get('failure_type', '')}:{failed_action}"
        repeat_count = self.repeated_failures.get(failure_key, 0)
        if repeat_count > 1:
            pattern_text = (
                f"\n⚠️ PATTERN: This exact failure has occurred {repeat_count} times. "
                f"A fundamentally different approach is needed.\n"
            )

        # Successful strategies to consider
        strategies_text = ""
        if self.successful_recoveries:
            strategies_text = "\n✅ Strategies that WORKED in this session:\n"
            for s in self.successful_recoveries[-3:]:
                strategies_text += f"  - {s}\n"

        return f"""🔄 REFLECTION BEFORE RETRY

**Task Goal:** {self.task_goal}
**Failed Action:** {failed_action}
**Failure Type:** {failure_diagnosis.get('failure_type', 'UNKNOWN')}
**Root Cause:** {failure_diagnosis.get('root_cause', 'Unknown')}
**Suggested Recovery:** {failure_diagnosis.get('recovery_strategy', 'None')}

**Past Attempts:**
{attempts_text}
{pattern_text}
{strategies_text}
**Reflection Questions (reason through these before acting):**
1. What assumption was incorrect about the current screen state?
2. Is there an alternative UI path to achieve the same goal?
3. Should I gather more information (screenshot/elements) before acting?
4. Would a completely different approach work better?

**Based on this reflection, choose your next action carefully.**"""

    def get_context_for_agent(self) -> str:
        """
        Get formatted context string for the agent to use in decision-making.

        Returns:
            Formatted string with session history and learnings
        """
        summary = self.get_summary()
        
        context = f"""
📝 Session Memory Context:

**Task Goal:** {self.task_goal}
**Actions Taken:** {summary['total_actions']} ({summary['successful_actions']} successful)
**Failures:** {summary['total_failures']} ({summary['successful_recoveries']} recovered)
"""
        
        if summary['failure_types']:
            context += "\n**Failure Breakdown:**\n"
            for ftype, count in summary['failure_types'].items():
                context += f"  - {ftype}: {count}\n"
        
        if self.repeated_failures:
            context += "\n**⚠️ Repeated Failures (avoid these):**\n"
            for failure_key, count in sorted(
                self.repeated_failures.items(), 
                key=lambda x: x[1], 
                reverse=True
            )[:3]:  # Show top 3
                context += f"  - {failure_key}: {count} times\n"
        
        if self.successful_recoveries:
            context += "\n**✅ Successful Recovery Strategies:**\n"
            for strategy in self.successful_recoveries[-3:]:  # Show last 3
                context += f"  - {strategy}\n"
        
        # Recent actions context
        if len(self.actions) > 0:
            context += f"\n**Recent Actions (last 3):**\n"
            for action in self.actions[-3:]:
                status = "✅" if action.success else "❌"
                context += f"  {status} {action.action}\n"
        
        return context.strip()


# =============================================================================
# CROSS-SESSION LEARNING STORE (GPT-5.4 Self-Explore Pattern)
# =============================================================================
# Persists learnings across sessions for continuous improvement.
# Implements GEPA-inspired patterns for self-evolving agents.

import json
import os
from pathlib import Path


class LearningStore:
    """
    Cross-session learning persistence for Self-Explore pattern.

    Stores:
    - Successful recovery strategies per failure type
    - Common failure patterns to avoid
    - Navigation patterns that work for specific apps
    - LLM-as-judge evaluation scores

    GPT-5.4 Feature: Enables agents to learn from past sessions and improve over time.
    """

    DEFAULT_STORE_PATH = "data/agent_learnings.json"

    def __init__(self, store_path: str = None):
        """
        Initialize the learning store.

        Args:
            store_path: Path to the JSON file for persistence (relative to backend dir)
        """
        self.store_path = store_path or self.DEFAULT_STORE_PATH
        self._learnings: Dict[str, Any] = {
            "recovery_strategies": {},  # failure_type -> list of {strategy, success_count}
            "app_patterns": {},  # app_name -> list of {goal, successful_actions}
            "failure_patterns": {},  # failure_type -> list of {context, cause, count}
            "evaluation_scores": [],  # list of {session_id, score, feedback, timestamp}
            "metadata": {
                "total_sessions": 0,
                "total_learnings": 0,
                "last_updated": None,
            }
        }
        self._load()

    def _get_full_path(self) -> Path:
        """Get the full path to the store file."""
        # Find the backend directory
        current = Path(__file__).parent
        while current.name != "backend" and current.parent != current:
            current = current.parent
        return current / self.store_path

    def _load(self):
        """Load learnings from disk."""
        path = self._get_full_path()
        try:
            if path.exists():
                with open(path, "r") as f:
                    self._learnings = json.load(f)
                logger.info(f"Loaded {self._learnings['metadata']['total_learnings']} learnings from {path}")
        except Exception as e:
            logger.warning(f"Could not load learnings from {path}: {e}")

    def _save(self):
        """Save learnings to disk."""
        path = self._get_full_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._learnings["metadata"]["last_updated"] = datetime.now(timezone.utc).isoformat()
            with open(path, "w") as f:
                json.dump(self._learnings, f, indent=2)
            logger.debug(f"Saved learnings to {path}")
        except Exception as e:
            logger.error(f"Could not save learnings to {path}: {e}")

    def record_successful_recovery(self, failure_type: str, strategy: str):
        """
        Record a successful recovery strategy for a failure type.

        Args:
            failure_type: Type of failure that was recovered from
            strategy: The recovery strategy that worked
        """
        if failure_type not in self._learnings["recovery_strategies"]:
            self._learnings["recovery_strategies"][failure_type] = []

        # Check if strategy already exists
        strategies = self._learnings["recovery_strategies"][failure_type]
        for s in strategies:
            if s["strategy"] == strategy:
                s["success_count"] += 1
                self._learnings["metadata"]["total_learnings"] += 1
                self._save()
                return

        # Add new strategy
        strategies.append({"strategy": strategy, "success_count": 1})
        self._learnings["metadata"]["total_learnings"] += 1
        self._save()
        logger.info(f"Learned new recovery strategy for {failure_type}: {strategy}")

    def record_app_pattern(self, app_name: str, goal: str, successful_actions: List[str]):
        """
        Record a successful navigation pattern for an app.

        Args:
            app_name: Name of the app (e.g., "YouTube", "Chrome")
            goal: The navigation goal achieved
            successful_actions: List of actions that achieved the goal
        """
        if app_name not in self._learnings["app_patterns"]:
            self._learnings["app_patterns"][app_name] = []

        self._learnings["app_patterns"][app_name].append({
            "goal": goal,
            "actions": successful_actions,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        # Keep only last 10 patterns per app
        self._learnings["app_patterns"][app_name] = \
            self._learnings["app_patterns"][app_name][-10:]

        self._learnings["metadata"]["total_learnings"] += 1
        self._save()
        logger.info(f"Learned app pattern for {app_name}: {goal}")

    def record_failure_pattern(self, failure_type: str, context: str, cause: str):
        """
        Record a failure pattern to avoid in future sessions.

        Args:
            failure_type: Type of failure
            context: Context when failure occurred
            cause: Root cause of failure
        """
        if failure_type not in self._learnings["failure_patterns"]:
            self._learnings["failure_patterns"][failure_type] = []

        patterns = self._learnings["failure_patterns"][failure_type]

        # Check if pattern already exists
        for p in patterns:
            if p["cause"] == cause:
                p["count"] += 1
                self._save()
                return

        # Add new pattern
        patterns.append({
            "context": context,
            "cause": cause,
            "count": 1,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        # Keep only top 20 patterns per failure type
        self._learnings["failure_patterns"][failure_type] = \
            sorted(patterns, key=lambda x: x["count"], reverse=True)[:20]

        self._save()

    def record_evaluation(self, session_id: str, score: float, feedback: str):
        """
        Record an LLM-as-judge evaluation score for a session.

        Args:
            session_id: Unique session identifier
            score: Evaluation score (0.0 to 1.0)
            feedback: Feedback from the evaluation
        """
        self._learnings["evaluation_scores"].append({
            "session_id": session_id,
            "score": score,
            "feedback": feedback,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        # Keep only last 100 evaluations
        self._learnings["evaluation_scores"] = self._learnings["evaluation_scores"][-100:]

        self._learnings["metadata"]["total_sessions"] += 1
        self._save()
        logger.info(f"Recorded evaluation for {session_id}: score={score}")

    def get_recovery_strategies(self, failure_type: str) -> List[str]:
        """
        Get successful recovery strategies for a failure type, sorted by success count.

        Args:
            failure_type: Type of failure

        Returns:
            List of recovery strategies, most successful first
        """
        strategies = self._learnings["recovery_strategies"].get(failure_type, [])
        sorted_strategies = sorted(strategies, key=lambda x: x["success_count"], reverse=True)
        return [s["strategy"] for s in sorted_strategies[:5]]  # Top 5

    def get_app_patterns(self, app_name: str, goal_keywords: List[str] = None) -> List[Dict]:
        """
        Get successful navigation patterns for an app.

        Args:
            app_name: Name of the app
            goal_keywords: Optional keywords to filter patterns by goal

        Returns:
            List of matching patterns
        """
        patterns = self._learnings["app_patterns"].get(app_name, [])

        if goal_keywords:
            # Filter by goal keywords
            filtered = []
            for p in patterns:
                goal_lower = p["goal"].lower()
                if any(kw.lower() in goal_lower for kw in goal_keywords):
                    filtered.append(p)
            return filtered[-3:]  # Last 3 matching

        return patterns[-3:]  # Last 3 overall

    def get_common_failure_patterns(self) -> str:
        """
        Get formatted string of common failure patterns to avoid.

        Returns:
            Formatted string for agent context
        """
        if not self._learnings["failure_patterns"]:
            return ""

        lines = ["📚 **Cross-Session Learnings - Common Failure Patterns:**"]
        for failure_type, patterns in self._learnings["failure_patterns"].items():
            top_patterns = sorted(patterns, key=lambda x: x["count"], reverse=True)[:3]
            if top_patterns:
                lines.append(f"\n**{failure_type}:**")
                for p in top_patterns:
                    lines.append(f"  - {p['cause']} (occurred {p['count']}x)")

        return "\n".join(lines)

    def get_average_score(self) -> float:
        """Get average evaluation score across all sessions."""
        scores = self._learnings["evaluation_scores"]
        if not scores:
            return 0.0
        return sum(s["score"] for s in scores) / len(scores)

    def get_learning_context(self, app_name: str = None, failure_type: str = None) -> str:
        """
        Get formatted learning context for the agent.

        Args:
            app_name: Optional app name to get patterns for
            failure_type: Optional failure type to get strategies for

        Returns:
            Formatted string with cross-session learnings
        """
        lines = []

        # Add recovery strategies if failure type provided
        if failure_type:
            strategies = self.get_recovery_strategies(failure_type)
            if strategies:
                lines.append(f"✅ **Proven recovery strategies for {failure_type}:**")
                for s in strategies:
                    lines.append(f"  - {s}")

        # Add app patterns if app name provided
        if app_name:
            patterns = self.get_app_patterns(app_name)
            if patterns:
                lines.append(f"\n🗺️ **Successful navigation patterns for {app_name}:**")
                for p in patterns[-2:]:  # Last 2
                    lines.append(f"  Goal: {p['goal']}")
                    lines.append(f"  Actions: {' → '.join(p['actions'][:5])}")

        # Add common failure patterns
        common_failures = self.get_common_failure_patterns()
        if common_failures:
            lines.append(f"\n{common_failures}")

        return "\n".join(lines) if lines else ""


# Global learning store instance
_learning_store: Optional[LearningStore] = None


def get_learning_store() -> LearningStore:
    """Get the global learning store instance."""
    global _learning_store
    if _learning_store is None:
        _learning_store = LearningStore()
    return _learning_store


# =============================================================================
# LLM-AS-JUDGE SESSION EVALUATOR (GPT-5.4 Self-Explore Pattern)
# =============================================================================
# Evaluates agent session performance and provides feedback for improvement.

class SessionEvaluator:
    """
    LLM-as-Judge evaluator for agent navigation sessions.

    GPT-5.4 Self-Explore Pattern:
    - Evaluates session performance on a 0.0-1.0 scale
    - Provides actionable feedback for improvement
    - Records evaluations to LearningStore for trend analysis

    Uses gpt-5.4 for evaluation (flagship model for LLM-as-judge quality).
    """

    EVAL_MODEL = "gpt-5.4"  # Flagship LLM-as-judge model

    EVALUATION_PROMPT = """You are an expert evaluator for mobile device testing agents.

Evaluate this navigation session and provide a score from 0.0 to 1.0:

**Task Goal:** {task_goal}

**Session Statistics:**
- Total actions: {total_actions}
- Successful actions: {successful_actions}
- Total failures: {total_failures}
- Successful recoveries: {successful_recoveries}
- Recovery success rate: {recovery_rate:.1%}

**Failure Breakdown:**
{failure_breakdown}

**Actions Taken:**
{actions_summary}

**Scoring Criteria:**
- 0.9-1.0: Excellent - Task completed efficiently with minimal failures
- 0.7-0.89: Good - Task completed with some recoverable failures
- 0.5-0.69: Acceptable - Task completed but with significant struggles
- 0.3-0.49: Poor - Task partially completed or required many retries
- 0.0-0.29: Failed - Task not completed or excessive failures

Respond with ONLY a JSON object (no markdown):
{{
    "score": <float 0.0-1.0>,
    "reasoning": "<1-2 sentence explanation>",
    "strengths": ["<strength 1>", "<strength 2>"],
    "improvements": ["<improvement 1>", "<improvement 2>"],
    "learned_patterns": ["<pattern to remember for future sessions>"]
}}"""

    def __init__(self, model: str = None):
        """Initialize the evaluator."""
        self.model = model or self.EVAL_MODEL
        self._client = None

    def _get_client(self):
        """Lazy-load OpenAI client."""
        if self._client is None:
            import openai
            from ...observability.tracing import get_traced_client
            self._client = get_traced_client(openai.OpenAI())
        return self._client

    def evaluate_session(self, session: SessionMemory) -> Dict[str, Any]:
        """
        Evaluate a navigation session using LLM-as-judge.

        Args:
            session: The SessionMemory to evaluate

        Returns:
            Evaluation result with score, reasoning, and feedback
        """
        import uuid

        summary = session.get_summary()

        # Format failure breakdown
        failure_breakdown = "\n".join([
            f"  - {ftype}: {count} occurrences"
            for ftype, count in summary.get("failure_types", {}).items()
        ]) or "  No failures recorded"

        # Format actions summary (last 10)
        actions = session.actions[-10:] if session.actions else []
        actions_summary = "\n".join([
            f"  {'✅' if a.success else '❌'} {a.action}"
            for a in actions
        ]) or "  No actions recorded"

        # Build prompt
        prompt = self.EVALUATION_PROMPT.format(
            task_goal=summary.get("task_goal", "Unknown"),
            total_actions=summary.get("total_actions", 0),
            successful_actions=summary.get("successful_actions", 0),
            total_failures=summary.get("total_failures", 0),
            successful_recoveries=summary.get("successful_recoveries", 0),
            recovery_rate=summary.get("recovery_success_rate", 0),
            failure_breakdown=failure_breakdown,
            actions_summary=actions_summary,
        )

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a precise evaluator. Respond only with valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                max_completion_tokens=500,  # GPT-5+ requires max_completion_tokens
                # Note: GPT-5-mini doesn't support custom temperature, uses default (1)
            )

            result_text = response.choices[0].message.content.strip()

            # Parse JSON response
            result = json.loads(result_text)

            # Validate score
            score = float(result.get("score", 0.5))
            score = max(0.0, min(1.0, score))  # Clamp to 0-1

            # Record to learning store
            session_id = str(uuid.uuid4())[:8]
            learning_store = get_learning_store()
            learning_store.record_evaluation(
                session_id=session_id,
                score=score,
                feedback=result.get("reasoning", "No reasoning provided")
            )

            # Record learned patterns to learning store
            for pattern in result.get("learned_patterns", []):
                if pattern:
                    # Try to classify the pattern
                    if "scroll" in pattern.lower() or "find" in pattern.lower():
                        learning_store.record_successful_recovery("PERCEPTION_ERROR", pattern)
                    elif "wait" in pattern.lower() or "retry" in pattern.lower():
                        learning_store.record_successful_recovery("ENVIRONMENT_ERROR", pattern)

            logger.info(f"Session evaluated: score={score:.2f}, reasoning={result.get('reasoning', 'N/A')}")

            return {
                "session_id": session_id,
                "score": score,
                "reasoning": result.get("reasoning", ""),
                "strengths": result.get("strengths", []),
                "improvements": result.get("improvements", []),
                "learned_patterns": result.get("learned_patterns", []),
                "average_score": learning_store.get_average_score(),
            }

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse evaluation response: {e}")
            return {
                "session_id": "error",
                "score": 0.5,
                "reasoning": f"Evaluation parsing failed: {e}",
                "strengths": [],
                "improvements": [],
                "learned_patterns": [],
                "average_score": 0.0,
            }
        except Exception as e:
            logger.error(f"Session evaluation failed: {e}")
            return {
                "session_id": "error",
                "score": 0.5,
                "reasoning": f"Evaluation failed: {e}",
                "strengths": [],
                "improvements": [],
                "learned_patterns": [],
                "average_score": 0.0,
            }


# Global evaluator instance
_session_evaluator: Optional[SessionEvaluator] = None


def get_session_evaluator() -> SessionEvaluator:
    """Get the global session evaluator instance."""
    global _session_evaluator
    if _session_evaluator is None:
        _session_evaluator = SessionEvaluator()
    return _session_evaluator


__all__ = [
    "SessionMemory", "FailureRecord", "ActionRecord",
    "LearningStore", "get_learning_store",
    "SessionEvaluator", "get_session_evaluator"
]

