"""
Workflow Detector — classifies natural language prompts into retained patterns.

Maps "flywheel this" → dev.flywheel.v3, "QA this" → qa.interactive_surface_audit.v2.
Uses token overlap scoring first (fast, no API call), then LLM classification for ambiguous cases.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import WorkflowKnowledge, _WORKFLOW_DIR

logger = logging.getLogger(__name__)


@dataclass
class DetectionResult:
    """Result of workflow detection."""
    workflow_id: str
    workflow_name: str
    confidence: float  # 0-1
    method: str  # "trigger_phrase", "alias", "token_overlap", "llm_classify"
    alternatives: List[Dict[str, float]] = None  # Other candidates with scores

    def __post_init__(self):
        if self.alternatives is None:
            self.alternatives = []


def detect_workflow(
    prompt: str,
    context: str = "",
) -> Optional[DetectionResult]:
    """Detect which retained workflow a prompt maps to.

    Priority:
    1. Exact trigger phrase match (highest confidence)
    2. Alias match
    3. Token overlap scoring (fast, no API)
    4. Returns None if no match above threshold

    Args:
        prompt: The user's natural language prompt
        context: Optional additional context (repo, recent actions)
    """
    workflows = _load_all_workflows()
    if not workflows:
        return None

    prompt_lower = prompt.lower().strip()

    # ── 1. Exact trigger phrase match ──
    for wf in workflows:
        for phrase in wf.trigger_phrases:
            if phrase.lower() in prompt_lower:
                return DetectionResult(
                    workflow_id=wf.workflow_id,
                    workflow_name=wf.name,
                    confidence=0.95,
                    method="trigger_phrase",
                )

    # ── 2. Alias match ──
    for wf in workflows:
        for alias in wf.aliases:
            if alias.lower() in prompt_lower:
                return DetectionResult(
                    workflow_id=wf.workflow_id,
                    workflow_name=wf.name,
                    confidence=0.85,
                    method="alias",
                )

    # ── 3. Token overlap scoring ──
    scores = []
    prompt_tokens = _tokenize(prompt_lower)

    for wf in workflows:
        # Build token set from all workflow text
        wf_text = " ".join([
            wf.name, wf.description, wf.outcome,
            " ".join(wf.aliases),
            " ".join(wf.trigger_phrases),
            " ".join(s.name for s in wf.required_steps),
        ]).lower()
        wf_tokens = _tokenize(wf_text)

        if not wf_tokens:
            continue

        overlap = prompt_tokens & wf_tokens
        score = len(overlap) / max(len(prompt_tokens), 1)
        scores.append((wf, score))

    scores.sort(key=lambda x: x[1], reverse=True)

    if scores and scores[0][1] >= 0.15:
        best_wf, best_score = scores[0]
        alternatives = [
            {"workflow_id": wf.workflow_id, "score": round(s, 3)}
            for wf, s in scores[1:4]
            if s >= 0.1
        ]
        return DetectionResult(
            workflow_id=best_wf.workflow_id,
            workflow_name=best_wf.name,
            confidence=min(best_score * 2, 0.8),  # Scale up but cap at 0.8
            method="token_overlap",
            alternatives=alternatives,
        )

    return None


def _tokenize(text: str) -> set:
    """Simple tokenizer — split on whitespace and punctuation, remove stopwords."""
    STOPWORDS = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "do", "does", "did", "will", "would", "could", "should",
        "can", "may", "might", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "and", "or", "not", "no",
        "it", "this", "that", "these", "those", "i", "me", "my",
        "we", "our", "you", "your", "he", "she", "they", "them",
        "all", "each", "every", "both", "few", "more", "most",
        "some", "any", "just", "very", "too", "also", "how",
    }
    tokens = set(re.findall(r'\w+', text.lower()))
    return tokens - STOPWORDS


def _load_all_workflows() -> List[WorkflowKnowledge]:
    """Load all persisted workflow knowledge objects."""
    workflows = []
    if not _WORKFLOW_DIR.exists():
        return workflows
    for f in _WORKFLOW_DIR.glob("*.json"):
        wf = WorkflowKnowledge.load(f.stem)
        if wf:
            workflows.append(wf)
    return workflows
