"""Token-overlap scoring for agent routing — claw-code pattern adapted for LLM hints.

Instead of hardcoded keyword sets (SEARCH_KEYWORDS, DEVICE_KEYWORDS, etc.),
this module computes deterministic routing scores based on token overlap between
the user's input and each agent's capabilities.

The scores are injected as HINTS into the coordinator's system prompt — the LLM
still makes the final routing decision. This respects the Core DNA constraint:
"LLM classifiers replace regex routing, agents self-direct with tools."
"""

from __future__ import annotations

import re

# Stopwords to skip during tokenization
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "and", "but", "or",
    "not", "no", "so", "if", "then", "than", "that", "this", "it", "its",
    "my", "your", "our", "their", "me", "him", "her", "us", "them",
    "i", "you", "he", "she", "we", "they", "what", "which", "who",
    "when", "where", "how", "all", "each", "every", "both", "few",
    "more", "most", "other", "some", "such", "only", "just", "also",
    "very", "please", "help", "want", "need", "like", "use",
})

# Agent capability descriptors (tokens the LLM scores against)
AGENT_CAPABILITIES: dict[str, list[str]] = {
    "Search Assistant": [
        "bug", "bugs", "issue", "issues", "crash", "crashes", "error", "errors",
        "database", "search", "find", "records", "report", "reports", "internal",
        "scenario", "scenarios", "test scenario", "history", "lookup",
    ],
    "Device Testing": [
        "device", "emulator", "phone", "android", "ios", "screen", "tap", "click",
        "swipe", "scroll", "navigate", "app", "launch", "chrome", "browser",
        "web", "website", "browse", "youtube", "instagram", "settings",
        "screenshot", "google", "home", "back", "mobile", "crawl", "test",
        "qa", "verify", "check", "run", "explore", "benchmark",
    ],
    "Test Generation": [
        "generate", "create", "write", "test case", "test cases", "prd",
        "user story", "acceptance criteria", "bdd", "gherkin", "spec",
        "specification", "requirement", "requirements", "edge case",
    ],
}


def _tokenize(text: str) -> set[str]:
    """Split text into lowercase tokens, removing stopwords and punctuation."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def compute_routing_score(user_input: str, agent_tokens: list[str]) -> float:
    """Compute 0.0-1.0 overlap score between user input and agent capability tokens."""
    input_tokens = _tokenize(user_input)
    if not input_tokens:
        return 0.0

    capability_tokens = {t.lower() for t in agent_tokens}
    overlap = input_tokens & capability_tokens

    if not overlap:
        # Check for substring matches (e.g., "screenshot" matches "screen")
        for it in input_tokens:
            for ct in capability_tokens:
                if ct in it or it in ct:
                    overlap.add(it)

    return min(1.0, len(overlap) / max(len(input_tokens), 1))


def compute_all_scores(user_input: str) -> dict[str, float]:
    """Compute routing scores for all agents.

    Returns dict like {"Search Assistant": 0.72, "Device Testing": 0.45, ...}
    """
    return {
        agent: compute_routing_score(user_input, tokens)
        for agent, tokens in AGENT_CAPABILITIES.items()
    }


def format_routing_hint(scores: dict[str, float]) -> str:
    """Format scores as a system prompt hint block.

    Example output:
        ## Routing Confidence
        Search Assistant: 0.72 | Device Testing: 0.45 | Test Generation: 0.12
        Highest signal: Search Assistant
    """
    parts = [f"{agent}: {score:.2f}" for agent, score in scores.items()]
    top_agent = max(scores, key=scores.get) if scores else "unknown"

    return (
        "\n\n## Routing Confidence (deterministic token-overlap scores)\n"
        f"{' | '.join(parts)}\n"
        f"Highest signal: {top_agent}\n"
        "Use these as a hint — your judgment takes precedence."
    )
