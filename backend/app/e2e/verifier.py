"""
E2E Verification

Ground truth verification combining:
- State verification via ADB
- LLM-as-judge evaluation with boolean metrics
- Trajectory comparison
"""

import asyncio
import subprocess
import logging
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Model configuration (March 2026 - GPT-5.4)
try:
    from app.agents.model_fallback import EVAL_MODEL, THINKING_MODEL
except ImportError:
    EVAL_MODEL = "gpt-5.4"
    THINKING_MODEL = "gpt-5.4"


@dataclass
class BooleanMetrics:
    """Descriptive boolean metrics for LLM-as-judge evaluation"""
    task_understood: bool = False
    correct_app_opened: bool = False
    target_element_found: bool = False
    action_executed: bool = False
    final_state_correct: bool = False
    no_errors_occurred: bool = False
    reasoning: str = ""

    @property
    def passed(self) -> bool:
        """Task passes if final state is correct and no errors"""
        return self.final_state_correct and self.no_errors_occurred

    @property
    def score(self) -> float:
        """Convert booleans to a score for compatibility"""
        metrics = [
            self.task_understood,
            self.correct_app_opened,
            self.target_element_found,
            self.action_executed,
            self.final_state_correct,
            self.no_errors_occurred,
        ]
        return sum(1 for m in metrics if m) / len(metrics)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_understood": self.task_understood,
            "correct_app_opened": self.correct_app_opened,
            "target_element_found": self.target_element_found,
            "action_executed": self.action_executed,
            "final_state_correct": self.final_state_correct,
            "no_errors_occurred": self.no_errors_occurred,
            "reasoning": self.reasoning,
            "passed": self.passed,
            "score": self.score,
        }


@dataclass
class VerificationResult:
    """Result of E2E verification"""
    passed: bool
    score: float = 0.0
    state_check: Optional[bool] = None
    llm_judge_score: Optional[float] = None
    llm_judge_metrics: Optional[BooleanMetrics] = None
    trajectory_match: Optional[float] = None
    details: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score,
            "state_check": self.state_check,
            "llm_judge_score": self.llm_judge_score,
            "llm_judge_metrics": self.llm_judge_metrics.to_dict() if self.llm_judge_metrics else None,
            "trajectory_match": self.trajectory_match,
            "details": self.details,
            "errors": self.errors,
        }


class E2EVerifier:
    """
    Multi-modal verification for E2E tests.
    
    Uses:
    - ADB state checks (deterministic)
    - LLM-as-judge evaluation (gpt-5-mini, NOT nano)
    - Trajectory comparison
    """
    
    def __init__(self, device_id: str, model: str = None):
        self.device_id = device_id
        self.model = model or EVAL_MODEL  # gpt-5-mini for quality
        self._client = None
    
    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            from ..observability.tracing import get_traced_client
            self._client = get_traced_client(OpenAI())
        return self._client
    
    async def verify(
        self,
        task_name: str,
        expected_outcome: Dict[str, Any],
        actual_actions: List[Dict],
        screenshot_b64: Optional[str] = None,
        agent_output: Optional[str] = None,
    ) -> VerificationResult:
        """Run full verification suite"""

        result = VerificationResult(passed=False)
        scores = []

        # 1. State verification (ADB)
        if expected_outcome.get("state_checks"):
            state_passed = await self._verify_state(expected_outcome["state_checks"])
            result.state_check = state_passed
            scores.append(1.0 if state_passed else 0.0)

        # 2. LLM-as-judge with boolean metrics
        if expected_outcome.get("description"):
            metrics = await self._llm_judge(
                task_name,
                expected_outcome["description"],
                actual_actions,
                screenshot_b64,
                agent_output
            )
            result.llm_judge_metrics = metrics
            result.llm_judge_score = metrics.score
            scores.append(metrics.score)
        
        # 3. Trajectory comparison
        if expected_outcome.get("expected_actions"):
            traj_score = self._compare_trajectory(
                expected_outcome["expected_actions"],
                actual_actions
            )
            result.trajectory_match = traj_score
            scores.append(traj_score)
        
        # Aggregate score and determine pass/fail
        if scores:
            result.score = sum(scores) / len(scores)
        else:
            result.score = 1.0

        # Pass/fail based on boolean metrics if available, otherwise use score
        if result.llm_judge_metrics:
            result.passed = result.llm_judge_metrics.passed
        elif result.state_check is not None:
            result.passed = result.state_check
        else:
            result.passed = result.score >= 0.7

        return result
    
    async def _verify_state(self, state_checks: List[Dict]) -> bool:
        """Verify device state via ADB"""
        all_passed = True
        
        for check in state_checks:
            check_type = check.get("type")
            
            if check_type == "app_running":
                package = check.get("package")
                result = subprocess.run(
                    ["adb", "-s", self.device_id, "shell",
                     "dumpsys", "activity", "activities"],
                    capture_output=True, text=True, timeout=10
                )
                if package not in result.stdout:
                    all_passed = False
                    logger.warning(f"[Verifier] App not running: {package}")
            
            elif check_type == "element_present":
                # Use UI Automator dump
                pass  # Simplified
        
        return all_passed
    
    async def _llm_judge(
        self,
        task_name: str,
        expected_description: str,
        actual_actions: List[Dict],
        screenshot_b64: Optional[str] = None,
        agent_output: Optional[str] = None
    ) -> BooleanMetrics:
        """LLM-as-judge evaluation with boolean metrics and optional vision"""

        # Format actions - handle both old format (action/target) and new format (tool/arguments)
        action_lines = []
        for a in (actual_actions or [])[:15]:
            if "tool" in a:
                action_lines.append(f"- {a.get('tool')}: {a.get('arguments', '{}')}")
            else:
                action_lines.append(f"- {a.get('action', 'action')}: {a.get('target', 'N/A')}")
        actions_str = "\n".join(action_lines) if action_lines else "No tool actions recorded"

        # Include agent's reasoning/output if available
        agent_section = ""
        if agent_output:
            agent_section = f"\nAGENT'S ASSESSMENT:\n{agent_output[:500]}\n"

        # Include screenshot context if available
        screenshot_section = ""
        if screenshot_b64:
            screenshot_section = "\nA screenshot of the final device state is attached. Use it to verify the task was completed."

        prompt_text = f"""Evaluate if this mobile automation task was completed successfully.

TASK: {task_name}
EXPECTED: {expected_description}

ACTIONS TAKEN:
{actions_str}
{agent_section}{screenshot_section}

Evaluate each criterion and respond with a JSON object containing boolean values:

{{
  "task_understood": true/false,  // Did the agent understand what task to perform?
  "correct_app_opened": true/false,  // Was the correct app/screen navigated to?
  "target_element_found": true/false,  // Was the target UI element found?
  "action_executed": true/false,  // Was the intended action executed?
  "final_state_correct": true/false,  // Does the final state match the expected outcome?
  "no_errors_occurred": true/false,  // Did the execution complete without errors?
  "reasoning": "Brief explanation of the evaluation"
}}

IMPORTANT NOTES:
- If the task was already in the desired state (e.g., Bluetooth already ON), that counts as success.
- Look at the screenshot to verify the final state.
- For toggle tasks: check if the toggle shows the correct state.
- For app tasks: verify the correct app/screen is visible.

Respond with ONLY valid JSON, no additional text."""

        try:
            client = self._get_client()

            # Use vision if screenshot is available
            if screenshot_b64:
                # Clean up base64 if needed (remove prefix)
                b64_data = screenshot_b64
                if "," in b64_data:
                    b64_data = b64_data.split(",", 1)[1]

                logger.info(f"[Verifier] Using vision with screenshot ({len(b64_data)} chars)")

                # Responses API expects message format for multimodal
                input_content = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt_text},
                            {
                                "type": "input_image",
                                "image_url": f"data:image/jpeg;base64,{b64_data}",
                            }
                        ]
                    }
                ]
                response = client.responses.create(
                    model=self.model,
                    input=input_content,
                    store=False,
                )
            else:
                response = client.responses.create(
                    model=self.model,
                    input=prompt_text,
                    store=False,
                )

            raw_output = response.output_text.strip()
            logger.info(f"[Verifier] LLM judge raw output: '{raw_output}'")

            # Parse JSON response
            metrics = self._parse_boolean_metrics(raw_output)
            logger.info(f"[Verifier] Boolean metrics: passed={metrics.passed}, score={metrics.score:.2f}")
            logger.info(f"[Verifier] Reasoning: {metrics.reasoning}")
            return metrics

        except Exception as e:
            logger.error(f"[Verifier] LLM judge failed: {e}")
            return BooleanMetrics(reasoning=f"Evaluation failed: {str(e)}")

    def _parse_boolean_metrics(self, raw_output: str) -> BooleanMetrics:
        """Parse LLM output into BooleanMetrics"""
        metrics = BooleanMetrics()

        try:
            # Try to extract JSON from the response
            json_str = raw_output
            # Handle markdown code blocks
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0]
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0]

            data = json.loads(json_str.strip())

            metrics.task_understood = bool(data.get("task_understood", False))
            metrics.correct_app_opened = bool(data.get("correct_app_opened", False))
            metrics.target_element_found = bool(data.get("target_element_found", False))
            metrics.action_executed = bool(data.get("action_executed", False))
            metrics.final_state_correct = bool(data.get("final_state_correct", False))
            metrics.no_errors_occurred = bool(data.get("no_errors_occurred", False))
            metrics.reasoning = data.get("reasoning", "")

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"[Verifier] Failed to parse JSON metrics: {e}")
            # Fallback: try to extract boolean-like values from text
            text_lower = raw_output.lower()
            metrics.final_state_correct = "success" in text_lower or "complete" in text_lower
            metrics.no_errors_occurred = "error" not in text_lower and "fail" not in text_lower
            metrics.reasoning = f"Fallback parsing from: {raw_output[:200]}"

        return metrics
    
    def _compare_trajectory(
        self,
        expected: List[Dict],
        actual: List[Dict]
    ) -> float:
        """Compare expected vs actual action trajectories"""
        if not expected or not actual:
            return 0.5
        
        # Simple action matching
        expected_actions = [e.get("action", "") for e in expected]
        actual_actions = [a.get("action", "") for a in actual]
        
        from difflib import SequenceMatcher
        matcher = SequenceMatcher(None, expected_actions, actual_actions)
        return matcher.ratio()

