"""
LLM Workflow Classifier — replaces exact phrase matching with a real AI classifier.

Uses the GPT API to classify natural language prompts into workflow patterns.
No regex. No substring matching. Full LLM judge.

Also provides LLM-backed evidence evaluation for each workflow step,
replacing the heuristic pattern matching in workflow_judge.py.

Requires OPENAI_API_KEY.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _get_client():
    """Get OpenAI client. Raises if no API key."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY required for LLM classifier")
    from openai import OpenAI
    return OpenAI(api_key=api_key)


# ---------------------------------------------------------------------------
# Workflow Detection — LLM classifier
# ---------------------------------------------------------------------------

_CLASSIFY_SYSTEM = """You are a workflow classifier for a development automation system.

Given a user prompt, classify it into ONE of these workflow types, or "unknown" if none match.

WORKFLOW TYPES:
1. "dev.flywheel.v3" — Full development cycle: understand, inspect, search latest, implement across all layers, QA interactive surfaces, run verification, produce PR summary. Triggers: "flywheel", "full dev cycle", "ship this properly", "build this end to end", or any prompt that implies a multi-step implementation task.

2. "qa.interactive_surface_audit.v2" — QA audit of interactive components: enumerate surfaces, test all clickable elements, test forms, check console errors, produce evidence bundle. Triggers: "QA this", "test all components", "check all interactive elements", "full QA pass".

3. "drx.latest_industry_refresh.v1" — Research update: load prior research, search latest, compare claims, produce updated output. Triggers: "latest industry sweep", "refresh research", "what's new in this space", "update market data", "deep research".

4. "pr.premerge.fullcheck.v2" — PR readiness: review diffs, run tests, type checking, linting, write PR description. Triggers: "prep for PR", "ready for merge", "pre-merge check".

Respond with ONLY a JSON object:
{"workflow_id": "...", "confidence": 0.0-1.0, "reasoning": "one sentence"}

If the prompt clearly maps to a workflow, confidence should be 0.7+.
If ambiguous but likely, 0.4-0.7.
If no match, return workflow_id: "unknown" with confidence 0.0."""


def classify_workflow(prompt: str, model: str = "gpt-5.4-nano") -> Dict[str, Any]:
    """Classify a prompt into a workflow type using LLM.

    Returns: {"workflow_id": str, "confidence": float, "reasoning": str}
    """
    try:
        client = _get_client()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _CLASSIFY_SYSTEM},
                {"role": "user", "content": f"Classify this prompt:\n\n{prompt}"},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=200,
            temperature=0.0,
        )
        result = json.loads(resp.choices[0].message.content)
        logger.info(f"LLM classifier: '{prompt[:50]}' → {result.get('workflow_id')} (conf={result.get('confidence')})")
        return result
    except Exception as e:
        logger.error(f"LLM classifier failed: {e}")
        return {"workflow_id": "unknown", "confidence": 0.0, "reasoning": f"Classifier error: {e}"}


# ---------------------------------------------------------------------------
# Evidence Evaluation — LLM judge per step
# ---------------------------------------------------------------------------

_EVIDENCE_JUDGE_SYSTEM = """You are an evidence judge for a workflow completion system.

Given a workflow step and the evidence collected from the session, determine if the step was completed.

Respond with ONLY a JSON object:
{
  "status": "done" | "partial" | "missing",
  "confidence": 0.0-1.0,
  "evidence_found": ["brief description of matching evidence"],
  "reasoning": "one sentence"
}

Rules:
- "done": Clear evidence the step was performed (tool calls, file changes, search results)
- "partial": Some evidence but incomplete (e.g., only one file reviewed when multiple were changed)
- "missing": No evidence at all for this step
- Be strict. If the evidence is ambiguous, mark as "partial" not "done"."""


def judge_step_evidence(
    step_name: str,
    step_description: str,
    evidence_summary: str,
    model: str = "gpt-5.4-nano",
) -> Dict[str, Any]:
    """Use LLM to judge whether a specific step has sufficient evidence.

    Args:
        step_name: Name of the required step
        step_description: What the step requires
        evidence_summary: Summary of all evidence events collected

    Returns: {"status": str, "confidence": float, "evidence_found": list, "reasoning": str}
    """
    try:
        client = _get_client()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _EVIDENCE_JUDGE_SYSTEM},
                {"role": "user", "content": (
                    f"STEP: {step_name}\n"
                    f"DESCRIPTION: {step_description}\n\n"
                    f"EVIDENCE FROM SESSION:\n{evidence_summary}"
                )},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=300,
            temperature=0.0,
        )
        result = json.loads(resp.choices[0].message.content)
        return result
    except Exception as e:
        logger.error(f"Evidence judge failed for {step_name}: {e}")
        return {"status": "missing", "confidence": 0.0, "evidence_found": [], "reasoning": f"Judge error: {e}"}


# ---------------------------------------------------------------------------
# Full LLM Completion Judge — replaces heuristic scoring entirely
# ---------------------------------------------------------------------------

_COMPLETION_JUDGE_SYSTEM = """You are a workflow completion judge. You determine whether an agent should be ALLOWED to stop working.

Given:
- The detected workflow and its required steps
- All evidence collected from the session (tool calls, file reads/writes, searches, tests, artifacts)

Evaluate EACH required step and produce a final verdict.

Respond with ONLY a JSON object:
{
  "steps": [
    {
      "step_id": "...",
      "step_name": "...",
      "status": "done" | "partial" | "missing",
      "confidence": 0.0-1.0,
      "evidence": "brief description of matching evidence or 'none found'",
      "common_miss_detected": true/false
    }
  ],
  "hard_gates": {
    "no_false_completion": true/false,
    "no_fabrication": true/false,
    "all_surfaces_touched": true/false
  },
  "scores": {
    "task_success": 1-5,
    "completeness": 1-5,
    "faithfulness": 1-5,
    "efficiency": 1-5,
    "artifact_quality": 1-5,
    "safety": 1-5
  },
  "verdict": "acceptable_replay" | "acceptable_replay_with_minor_loss" | "replay_should_have_escalated" | "failed_replay" | "frontier_required",
  "confidence": 0.0-1.0,
  "missing_steps": ["step names that are missing"],
  "nudges": [{"level": "soft"|"strong"|"block", "message": "..."}],
  "summary": "one paragraph"
}

Verdict rules:
- acceptable_replay: ALL required steps done, all hard gates pass
- acceptable_replay_with_minor_loss: 1 non-critical step partial, no hard gate failures
- replay_should_have_escalated: 1+ required steps missing, should not stop
- failed_replay: 3+ steps missing or critical hard gate failure
- frontier_required: workflow too novel for current approach"""


def judge_completion(
    workflow_id: str,
    workflow_name: str,
    required_steps: List[Dict[str, Any]],
    evidence_events: List[Dict[str, Any]],
    model: str = "gpt-5.4-mini",
) -> Dict[str, Any]:
    """Run the full LLM completion judge.

    This replaces ALL heuristic scoring. The LLM evaluates every step
    against the evidence and produces the final verdict.

    Args:
        workflow_id: Detected workflow type
        workflow_name: Human-readable name
        required_steps: List of {"step_id", "name", "description", "evidence_rules"}
        evidence_events: Raw evidence events from the session

    Returns: Full judge output dict
    """
    # Build evidence summary (don't send raw events — too large)
    evidence_summary = _summarize_evidence(evidence_events)

    # Build steps description
    steps_desc = "\n".join(
        f"- {s['name']}: {s.get('description', '')}"
        for s in required_steps
    )

    prompt = (
        f"WORKFLOW: {workflow_name} ({workflow_id})\n\n"
        f"REQUIRED STEPS:\n{steps_desc}\n\n"
        f"SESSION EVIDENCE:\n{evidence_summary}"
    )

    try:
        client = _get_client()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _COMPLETION_JUDGE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=2000,
            temperature=0.0,
        )
        result = json.loads(resp.choices[0].message.content)
        result["judge_model"] = model
        result["judge_method"] = "llm_judge"
        logger.info(f"LLM completion judge: {workflow_id} → {result.get('verdict')} (conf={result.get('confidence')})")
        return result
    except Exception as e:
        logger.error(f"LLM completion judge failed: {e}")
        return {
            "verdict": "replay_should_have_escalated",
            "confidence": 0.0,
            "summary": f"Judge error: {e}",
            "judge_model": model,
            "judge_method": "error_fallback",
        }


def _summarize_evidence(events: List[Dict[str, Any]]) -> str:
    """Summarize evidence events into a compact text for the LLM judge."""
    lines = []

    file_reads = []
    file_writes = []
    searches = []
    tool_calls = []
    tests = []
    artifacts = []
    screenshots = []

    for e in events:
        t = e.get("type", "")
        tn = e.get("tool_name", "")
        path = e.get("path", e.get("file", ""))

        if t == "file_read" or tn in ("read", "cat"):
            file_reads.append(path)
        elif t == "file_write" or tn in ("write", "edit"):
            file_writes.append(path)
        elif "search" in t or "fetch" in t:
            q = e.get("query", e.get("url", str(e)[:80]))
            searches.append(str(q)[:80])
        elif "test" in t or "pytest" in tn or "lint" in tn:
            tests.append(f"{tn}: {e.get('result', 'ran')}")
        elif "screenshot" in t or "preview" in t:
            screenshots.append(tn or t)
        elif "artifact" in t or "summary" in t:
            artifacts.append(e.get("content", str(e))[:80])
        else:
            tool_calls.append(f"{tn or t}")

    if file_reads:
        lines.append(f"Files read ({len(file_reads)}): {', '.join(file_reads[:10])}")
    if file_writes:
        lines.append(f"Files written ({len(file_writes)}): {', '.join(file_writes[:10])}")
    if searches:
        lines.append(f"Searches ({len(searches)}): {'; '.join(searches[:5])}")
    if tool_calls:
        lines.append(f"Tool calls ({len(tool_calls)}): {', '.join(set(tool_calls))}")
    if tests:
        lines.append(f"Tests/verification ({len(tests)}): {'; '.join(tests[:5])}")
    if screenshots:
        lines.append(f"Screenshots/previews ({len(screenshots)}): {', '.join(screenshots[:5])}")
    if artifacts:
        lines.append(f"Artifacts ({len(artifacts)}): {'; '.join(artifacts[:3])}")

    return "\n".join(lines) if lines else "No evidence events collected."
