"""
Orchestration Run Session - Agent execution with inline LLM evaluation

This module provides the main orchestration class that:
1. Runs agent tasks with inline LLM evaluation at each step
2. Evaluates test case generation against criteria
3. Verifies device configuration matches bug reproduction requirements
4. Prevents workarounds and automatically retries when needed

Model Configuration (Industry Standard - January 2026):
- THINKING_MODEL: gpt-5.4 - For orchestration, complex reasoning, planning
- EVAL_MODEL: gpt-5-mini - For inline evaluation (quality matters!)
- PRIMARY_MODEL: gpt-5-mini - For standard tasks

Progressive Disclosure Pattern (Anthropic Agent Skills):
- Load minimal context first (name, description)
- Expand context only as needed (full SKILL.md → additional files)
- Use context compaction for long-running sessions
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from enum import Enum

from .evaluators import (
    EvaluationResult,
    TestCaseEvaluator,
    DeviceConfigVerifier,
    InlineLLMEvaluator,
    EVAL_MODEL,
    THINKING_MODEL,
)

logger = logging.getLogger(__name__)


class SessionState(Enum):
    """State of the orchestration run session"""
    INITIALIZED = "initialized"
    RUNNING = "running"
    EVALUATING = "evaluating"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StepResult:
    """Result of a single step in the session"""
    step_id: int
    step_type: str
    action: str
    result: Any
    evaluation: Optional[EvaluationResult] = None
    duration_ms: int = 0
    retried: bool = False
    retry_count: int = 0


@dataclass
class TopicContext:
    """Stable identifier for an ongoing orchestration topic."""

    id: str
    title: Optional[str] = None
    summary: Optional[str] = None

    @classmethod
    def from_value(cls, value: Optional[Any]) -> Optional["TopicContext"]:
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(id=value)
        if isinstance(value, dict):
            topic_id = value.get("id") or value.get("topic_id") or value.get("title")
            if not topic_id:
                return None
            return cls(
                id=str(topic_id),
                title=value.get("title"),
                summary=value.get("summary"),
            )
        return None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"id": self.id}
        if self.title:
            payload["title"] = self.title
        if self.summary:
            payload["summary"] = self.summary
        return payload


@dataclass
class AttachedResource:
    """Resource attached to the orchestration topic."""

    kind: str
    id: str
    title: Optional[str] = None
    status: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> Optional["AttachedResource"]:
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            kind = value.get("type") or value.get("kind")
            resource_id = value.get("id") or value.get("resource_id")
            if not kind or not resource_id:
                return None
            return cls(
                kind=str(kind),
                id=str(resource_id),
                title=value.get("title") or value.get("name"),
                status=value.get("status"),
                metadata=dict(value.get("metadata") or {}),
            )
        return None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "type": self.kind,
            "id": self.id,
        }
        if self.title:
            payload["title"] = self.title
        if self.status:
            payload["status"] = self.status
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


@dataclass
class ResourceContext:
    """Attached resources that shape orchestration behavior."""

    attached: List[AttachedResource] = field(default_factory=list)
    mode: str = "attached"

    @classmethod
    def from_value(cls, value: Optional[Any]) -> Optional["ResourceContext"]:
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if isinstance(value, list):
            attached = [item for item in (AttachedResource.from_value(v) for v in value) if item]
            return cls(attached=attached)
        if isinstance(value, dict):
            attached_values = value.get("attached") or value.get("resources") or []
            attached = [item for item in (AttachedResource.from_value(v) for v in attached_values) if item]
            return cls(attached=attached, mode=value.get("mode", "attached"))
        return None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"attached": [item.to_dict() for item in self.attached]}
        if self.mode:
            payload["mode"] = self.mode
        return payload


@dataclass
class ContinuityContext:
    """Carry-forward memory for long-running orchestration topics."""

    memory_summary: Optional[str] = None
    carry_forward: List[str] = field(default_factory=list)
    open_loops: List[str] = field(default_factory=list)

    @classmethod
    def from_value(cls, value: Optional[Any]) -> Optional["ContinuityContext"]:
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(memory_summary=value)
        if isinstance(value, dict):
            return cls(
                memory_summary=value.get("memory_summary") or value.get("summary"),
                carry_forward=list(value.get("carry_forward") or []),
                open_loops=list(value.get("open_loops") or value.get("open_questions") or []),
            )
        return None

    def has_data(self) -> bool:
        return bool(self.memory_summary or self.carry_forward or self.open_loops)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if self.memory_summary:
            payload["memory_summary"] = self.memory_summary
        if self.carry_forward:
            payload["carry_forward"] = self.carry_forward
        if self.open_loops:
            payload["open_loops"] = self.open_loops
        return payload


@dataclass
class SessionResult:
    """Final result of the orchestration run session"""
    session_id: str
    state: SessionState
    steps: List[StepResult] = field(default_factory=list)
    total_evaluations: int = 0
    passed_evaluations: int = 0
    retries: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    final_verdict: Optional[EvaluationResult] = None
    topic: Optional[TopicContext] = None
    resource_context: Optional[ResourceContext] = None
    continuity: Optional[ContinuityContext] = None

    @property
    def success_rate(self) -> float:
        if self.total_evaluations == 0:
            return 0.0
        return self.passed_evaluations / self.total_evaluations

    def to_dict(self) -> Dict:
        payload: Dict[str, Any] = {
            "session_id": self.session_id,
            "state": self.state.value,
            "steps": [{"step_id": s.step_id, "type": s.step_type, "action": s.action,
                      "evaluation": s.evaluation.to_dict() if s.evaluation else None,
                      "retried": s.retried} for s in self.steps],
            "total_evaluations": self.total_evaluations,
            "passed_evaluations": self.passed_evaluations,
            "success_rate": self.success_rate,
            "retries": self.retries,
            "duration_ms": int((self.end_time - self.start_time).total_seconds() * 1000)
                if self.start_time and self.end_time else 0,
            "final_verdict": self.final_verdict.to_dict() if self.final_verdict else None
        }
        if self.topic:
            payload["topic"] = self.topic.to_dict()
        if self.resource_context:
            payload["resource_context"] = self.resource_context.to_dict()
        if self.continuity and self.continuity.has_data():
            payload["continuity"] = self.continuity.to_dict()
        return payload


def _merge_unique(existing: List[str], new_items: Optional[List[str]]) -> List[str]:
    """Merge string lists while preserving order and uniqueness."""

    merged = list(existing)
    for item in new_items or []:
        if item and item not in merged:
            merged.append(item)
    return merged


class OrchestrationRunSession:
    """
    Main orchestration class for agent runs with inline LLM evaluation.

    Model Configuration (Industry Standard - January 2026):
    - Uses gpt-5.4 (THINKING_MODEL) for orchestration and complex reasoning
    - Uses gpt-5-mini (EVAL_MODEL) for inline evaluation (quality matters!)
    - gpt-5-nano is NOT used here (only for MCP tools, distillation)

    Key features:
    1. Inline LLM evaluation at each step using gpt-5-mini
    2. High-thinking orchestration using gpt-5.4
    3. Test case evaluation against feature criteria
    4. Device configuration verification
    5. Workaround detection and prevention
    6. Automatic retry with correction
    7. Progressive disclosure pattern for context management
    """

    def __init__(
        self,
        session_id: str,
        feature_criteria: Dict[str, Any] = None,
        device_config: Dict[str, Any] = None,
        max_retries: int = 3,
        eval_model: str = EVAL_MODEL,
        thinking_model: str = THINKING_MODEL,
        topic: Optional[Any] = None,
        resource_context: Optional[Any] = None,
        continuity: Optional[Any] = None,
    ):
        self.session_id = session_id
        self.feature_criteria = feature_criteria or {}
        self.device_config = device_config or {}
        self.max_retries = max_retries
        self.eval_model = eval_model
        self.thinking_model = thinking_model

        # Evaluators - use gpt-5-mini for quality evaluation
        self.test_evaluator = TestCaseEvaluator(model=eval_model)
        self.device_verifier = DeviceConfigVerifier(model=eval_model)
        self.inline_evaluator = InlineLLMEvaluator(model=eval_model)

        # State
        self.state = SessionState.INITIALIZED
        self.result = SessionResult(
            session_id=session_id,
            state=self.state,
            topic=TopicContext.from_value(topic),
            resource_context=ResourceContext.from_value(resource_context),
            continuity=ContinuityContext.from_value(continuity),
        )
        self._step_counter = 0
        self._actions_taken: List[Dict] = []

        logger.info(f"[OrchestrationRunSession] Initialized session {session_id}")
        logger.info(f"  Thinking model: {thinking_model} (high budget)")
        logger.info(f"  Eval model: {eval_model} (quality evaluation)")

    def set_topic(self, topic_id: str, title: Optional[str] = None, summary: Optional[str] = None) -> None:
        """Set or replace the active topic for the session."""

        self.result.topic = TopicContext(id=topic_id, title=title, summary=summary)

    def attach_resource(
        self,
        resource_type: str,
        resource_id: str,
        title: Optional[str] = None,
        status: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Attach a resource that should shape later orchestration decisions."""

        if self.result.resource_context is None:
            self.result.resource_context = ResourceContext()
        self.result.resource_context.attached.append(
            AttachedResource(
                kind=resource_type,
                id=resource_id,
                title=title,
                status=status,
                metadata=dict(metadata or {}),
            )
        )

    def update_continuity(
        self,
        memory_summary: Optional[str] = None,
        carry_forward: Optional[List[str]] = None,
        open_loops: Optional[List[str]] = None,
    ) -> None:
        """Update carry-forward memory for the current topic."""

        current = self.result.continuity or ContinuityContext()
        if memory_summary:
            current.memory_summary = memory_summary
        current.carry_forward = _merge_unique(current.carry_forward, carry_forward)
        current.open_loops = _merge_unique(current.open_loops, open_loops)
        self.result.continuity = current

    async def start(self) -> None:
        """Start the orchestration session"""
        self.state = SessionState.RUNNING
        self.result.state = self.state
        self.result.start_time = datetime.now()
        logger.info(f"[OrchestrationRunSession] Session {self.session_id} started")

    async def evaluate_step(
        self,
        step_type: str,
        action: str,
        result: Any,
        expected_outcome: str = None
    ) -> StepResult:
        """
        Evaluate a single step with inline LLM evaluation.

        Args:
            step_type: Type of step (test_generation, device_action, verification, etc.)
            action: Description of the action taken
            result: Result of the action
            expected_outcome: What was expected to happen
        """
        self._step_counter += 1
        step_id = self._step_counter
        start_time = datetime.now()

        self._actions_taken.append({"step_id": step_id, "action": action, "result": str(result)[:500]})

        # Run inline LLM evaluation based on step type
        self.state = SessionState.EVALUATING
        evaluation = await self._evaluate_by_type(step_type, action, result, expected_outcome)

        duration = int((datetime.now() - start_time).total_seconds() * 1000)

        step_result = StepResult(
            step_id=step_id, step_type=step_type, action=action,
            result=result, evaluation=evaluation, duration_ms=duration
        )

        self.result.steps.append(step_result)
        self.result.total_evaluations += 1
        if evaluation and evaluation.passed:
            self.result.passed_evaluations += 1

        self.state = SessionState.RUNNING
        logger.info(f"[Step {step_id}] {step_type}: {action} -> {'PASS' if evaluation.passed else 'FAIL'}")

        return step_result

    async def _evaluate_by_type(
        self, step_type: str, action: str, result: Any, expected: str
    ) -> EvaluationResult:
        """Route evaluation to appropriate evaluator based on step type"""
        try:
            if step_type == "test_generation":
                return await self.test_evaluator.evaluate_test_case(
                    generated_test=str(result),
                    feature_criteria=self.feature_criteria,
                    category=action,
                    expected_coverage=self.feature_criteria.get("coverage", [])
                )
            elif step_type == "device_config":
                return await self.device_verifier.verify_device_config(
                    required_config=self.device_config,
                    actual_config=result if isinstance(result, dict) else {},
                    bug_reproduction_steps=self.feature_criteria.get("steps", [])
                )
            elif step_type == "device_action":
                # For device actions, check for workarounds
                return await self.device_verifier.detect_workarounds(
                    expected_steps=self.feature_criteria.get("steps", []),
                    actual_steps=[a["action"] for a in self._actions_taken],
                    expected_outcome=expected or ""
                )
            else:
                # Generic evaluation
                prompt = f"Evaluate: {action}\nResult: {str(result)[:1000]}\nExpected: {expected or 'Not specified'}"
                return await self.inline_evaluator.evaluate(prompt)
        except Exception as e:
            logger.error(f"[OrchestrationRunSession] Evaluation error: {e}")
            return EvaluationResult(passed=False, confidence=0.0, reasoning=str(e), needs_retry=True)

    async def execute_with_retry(
        self,
        step_type: str,
        action_fn: Callable,
        action_desc: str,
        expected_outcome: str = None,
        correction_fn: Callable = None
    ) -> StepResult:
        """
        Execute an action with automatic retry on failure.

        Args:
            step_type: Type of step
            action_fn: Async function to execute
            action_desc: Description of the action
            expected_outcome: What should happen
            correction_fn: Optional function to apply corrections before retry
        """
        retry_count = 0
        last_result = None

        while retry_count <= self.max_retries:
            try:
                result = await action_fn()
                step_result = await self.evaluate_step(step_type, action_desc, result, expected_outcome)

                if step_result.evaluation and step_result.evaluation.passed:
                    return step_result

                if not step_result.evaluation or not step_result.evaluation.needs_retry:
                    return step_result

                # Retry needed
                retry_count += 1
                self.result.retries += 1
                self.state = SessionState.RETRYING
                step_result.retried = True
                step_result.retry_count = retry_count
                last_result = step_result

                logger.warning(f"[Retry {retry_count}] {action_desc}: {step_result.evaluation.retry_reason}")

                if correction_fn and retry_count < self.max_retries:
                    await correction_fn(step_result.evaluation.suggestions)

            except Exception as e:
                logger.error(f"[OrchestrationRunSession] Action failed: {e}")
                retry_count += 1

        return last_result or StepResult(
            step_id=self._step_counter + 1, step_type=step_type, action=action_desc,
            result=None, evaluation=EvaluationResult(passed=False, confidence=0.0, reasoning="Max retries exceeded")
        )

    async def finalize(self) -> SessionResult:
        """Finalize the session and compute final verdict"""
        self.result.end_time = datetime.now()

        # Final verdict based on all evaluations
        if self.result.success_rate >= 0.8:
            self.state = SessionState.COMPLETED
            verdict = EvaluationResult(
                passed=True, confidence=self.result.success_rate,
                reasoning=f"Session completed with {self.result.success_rate:.0%} success rate"
            )
        else:
            self.state = SessionState.FAILED
            failed_steps = [s for s in self.result.steps if s.evaluation and not s.evaluation.passed]
            issues = [s.evaluation.reasoning for s in failed_steps[:5] if s.evaluation]
            verdict = EvaluationResult(
                passed=False, confidence=self.result.success_rate,
                reasoning=f"Session failed with {self.result.success_rate:.0%} success rate",
                issues=issues
            )

        self.result.state = self.state
        self.result.final_verdict = verdict

        logger.info(f"[OrchestrationRunSession] Session {self.session_id} finalized: {self.state.value}")
        return self.result

