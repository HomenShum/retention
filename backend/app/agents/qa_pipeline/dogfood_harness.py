"""
Dogfood Harness — ingest our own Claude Code sessions and judge them.

This is how TA drinks its own kool-aid:
  1. Read Claude Code session transcripts from ~/.claude/projects/
  2. Extract tool calls, file operations, searches as evidence
  3. Run the workflow judge (LLM mode) on the evidence
  4. Report what was done, what was missed, what the verdict is
  5. Feed corrections back into the policy learner

Usage:
    from dogfood_harness import DogfoodHarness
    harness = DogfoodHarness(use_llm=True)
    result = harness.judge_session(session_dir, prompt="flywheel this")
    print(result.human_readable)
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .completion_gate import CompletionGate, CompletionGateResult, GateDecision, WorkflowState
from .policy_learner import PolicyLearner
from .workflow_judge import JudgeOutput, RunEvidence, WorkflowJudge, collect_evidence_from_events

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Session transcript parsing
# ---------------------------------------------------------------------------

class SessionEvidence(BaseModel):
    """Evidence extracted from a Claude Code session transcript."""
    session_id: str = ""
    prompt: str = ""
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    file_reads: List[str] = Field(default_factory=list)
    file_writes: List[str] = Field(default_factory=list)
    searches: List[Dict[str, Any]] = Field(default_factory=list)
    bash_commands: List[str] = Field(default_factory=list)
    total_events: int = 0


def extract_evidence_from_transcript(messages: List[Dict[str, Any]]) -> SessionEvidence:
    """Parse a Claude Code session transcript into structured evidence.

    Claude Code transcripts are JSON arrays of message objects with
    role, content, and tool_use/tool_result blocks.
    """
    evidence = SessionEvidence()
    events = []

    for msg in messages:
        role = msg.get("role", "")

        # Extract user prompts
        if role == "user":
            content = msg.get("content", "")
            if isinstance(content, str) and not evidence.prompt:
                evidence.prompt = content[:500]
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text" and not evidence.prompt:
                            evidence.prompt = block.get("text", "")[:500]
                        elif block.get("type") == "tool_result":
                            # Tool result from previous call
                            pass

        # Extract tool calls from assistant messages
        if role == "assistant":
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_name = block.get("name", "")
                        tool_input = block.get("input", {})

                        event = {
                            "type": "tool_call",
                            "tool_name": tool_name,
                        }
                        event.update(tool_input)

                        # Categorize
                        tn = tool_name.lower()
                        if tn in ("read", "cat"):
                            event["type"] = "file_read"
                            path = tool_input.get("file_path", tool_input.get("path", ""))
                            event["path"] = path
                            evidence.file_reads.append(path)
                        elif tn in ("write", "edit"):
                            event["type"] = "file_write"
                            path = tool_input.get("file_path", tool_input.get("path", ""))
                            event["path"] = path
                            evidence.file_writes.append(path)
                        elif tn == "bash":
                            cmd = tool_input.get("command", "")
                            evidence.bash_commands.append(cmd)
                            # Detect test/search/build from bash commands
                            cmd_lower = cmd.lower()
                            if any(kw in cmd_lower for kw in ("pytest", "npm test", "jest", "lint", "typecheck", "tsc", "mypy")):
                                event["type"] = "test"
                            elif any(kw in cmd_lower for kw in ("git push", "git commit")):
                                event["type"] = "artifact"
                                event["content"] = cmd
                        elif tn in ("websearch", "web_search"):
                            event["type"] = "web_search"
                            evidence.searches.append(tool_input)
                        elif tn == "webfetch":
                            event["type"] = "fetch"
                            evidence.searches.append(tool_input)
                        elif tn == "glob":
                            event["type"] = "tool_call"
                        elif tn == "grep":
                            event["type"] = "tool_call"

                        evidence.tool_calls.append(event)
                        events.append(event)

    evidence.total_events = len(events)
    return evidence


# ---------------------------------------------------------------------------
# Dogfood Harness
# ---------------------------------------------------------------------------

class DogfoodResult(BaseModel):
    """Result of dogfooding a session through the judge."""
    session_id: str = ""
    prompt: str = ""
    workflow_detected: str = ""
    judge_output: Optional[JudgeOutput] = None
    gate_result: Optional[CompletionGateResult] = None
    evidence_summary: Dict[str, int] = Field(default_factory=dict)
    human_readable: str = ""
    judged_at: str = ""
    use_llm: bool = False


class DogfoodHarness:
    """Ingests Claude Code sessions and judges them.

    The implement → dogfood → judge → fix loop:
    1. We work in Claude Code (this session)
    2. The harness reads our transcript
    3. The judge evaluates our work against workflow policies
    4. We see what we missed
    5. We fix it
    6. We run the harness again
    """

    def __init__(self, use_llm: bool = False, llm_model: str = "gpt-5.4-mini"):
        self._use_llm = use_llm
        self._llm_model = llm_model
        self._gate = CompletionGate(use_llm=use_llm, llm_model=llm_model)
        self._learner = PolicyLearner()

    def judge_session_from_events(
        self,
        prompt: str,
        events: List[Dict[str, Any]],
        session_id: str = "",
    ) -> DogfoodResult:
        """Judge a session from a list of evidence events.

        This is the main entry point for dogfooding.
        """
        result = DogfoodResult(
            session_id=session_id,
            prompt=prompt,
            judged_at=_now_iso(),
            use_llm=self._use_llm,
        )

        # Initialize gate state
        state = self._gate.on_prompt_submit(prompt, session_id=session_id)
        result.workflow_detected = state.workflow_id

        # Feed all events through the gate
        for event in events:
            tool_name = event.get("tool_name", event.get("type", ""))
            file_path = event.get("path", event.get("file_path", ""))
            self._gate.on_tool_use(state, tool_name, event, file_path=file_path)

        # Run the stop gate
        gate_result = self._gate.on_stop(state)
        result.gate_result = gate_result
        result.judge_output = gate_result.judge_output

        # Evidence summary
        evidence = collect_evidence_from_events(events)
        result.evidence_summary = {
            "tool_calls": len(evidence.tool_calls),
            "file_reads": len(evidence.file_reads),
            "file_writes": len(evidence.file_writes),
            "searches": len(evidence.searches),
            "tests": len(evidence.test_runs),
            "screenshots": len(evidence.screenshots),
            "artifacts": len(evidence.artifacts),
        }

        # Human readable
        lines = [
            f"Session: {session_id or 'unnamed'}",
            f"Prompt: {prompt[:100]}",
            f"Workflow: {state.workflow_id or 'unknown'}",
            f"Gate decision: {gate_result.decision.value}",
        ]
        if gate_result.missing_steps:
            lines.append(f"Missing: {', '.join(gate_result.missing_steps)}")
        if gate_result.nudge_messages:
            lines.append("Nudges:")
            for n in gate_result.nudge_messages[:5]:
                lines.append(f"  - {n}")
        lines.append(f"Evidence: {result.evidence_summary}")
        result.human_readable = "\n".join(lines)

        return result

    def judge_transcript(
        self,
        transcript_path: str,
        prompt_override: str = "",
    ) -> DogfoodResult:
        """Judge a Claude Code session from a transcript JSON file."""
        path = Path(transcript_path)
        if not path.exists():
            return DogfoodResult(
                human_readable=f"Transcript not found: {transcript_path}",
                judged_at=_now_iso(),
            )

        messages = json.loads(path.read_text())
        if not isinstance(messages, list):
            messages = messages.get("messages", [])

        # Extract evidence
        session_evidence = extract_evidence_from_transcript(messages)
        prompt = prompt_override or session_evidence.prompt

        # Convert to events for the gate
        events = session_evidence.tool_calls

        return self.judge_session_from_events(
            prompt=prompt,
            events=events,
            session_id=path.stem,
        )

    def record_correction(self, message: str, workflow_id: str = "") -> None:
        """Record a user correction for policy learning."""
        self._learner.record_correction(message, workflow_id=workflow_id)

    def get_correction_analysis(self, workflow_id: str = "") -> Dict[str, Any]:
        """Get analysis of correction patterns."""
        return self._learner.get_correction_analysis(workflow_id)
