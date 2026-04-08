"""
Chef Feedback Analyzer

Analyzes Chef run failures, generates improved prompts,
and manages the retry loop (max 3 attempts).

Feedback loop:
    1. Run fails → analyze error logs
    2. Classify failure type (build, deploy, runtime, timeout)
    3. Generate improved prompt with fixes
    4. Retry with improved prompt (up to max_retries)
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .types import ChefResult

logger = logging.getLogger(__name__)


# Common failure patterns and their prompt improvements
FAILURE_PATTERNS: Dict[str, Dict] = {
    "typescript_error": {
        "keywords": ["TS2", "TS7", "type error", "cannot find name", "Property"],
        "category": "build",
        "fix_hint": "Ensure all TypeScript types are properly defined. Use strict typing.",
    },
    "convex_schema": {
        "keywords": ["schema validation", "defineTable", "v.string", "convex/values"],
        "category": "build",
        "fix_hint": "Define a complete Convex schema with all required fields and validators.",
    },
    "import_error": {
        "keywords": ["Cannot find module", "Module not found", "import"],
        "category": "build",
        "fix_hint": "Use only standard npm packages. Verify all imports resolve correctly.",
    },
    "deploy_failure": {
        "keywords": ["deploy failed", "deployment error", "convex deploy"],
        "category": "deploy",
        "fix_hint": "Ensure the Convex schema is valid and all functions compile.",
    },
    "runtime_error": {
        "keywords": ["runtime error", "unhandled", "TypeError", "ReferenceError"],
        "category": "runtime",
        "fix_hint": "Add error boundaries and null checks for all data access.",
    },
    "timeout": {
        "keywords": ["timeout", "timed out", "ETIMEDOUT"],
        "category": "timeout",
        "fix_hint": "Simplify the app scope. Focus on core functionality only.",
    },
}


@dataclass
class FailureAnalysis:
    """Analysis of why a Chef run failed."""

    run_id: str
    failure_category: str  # "build", "deploy", "runtime", "timeout", "unknown"
    matched_patterns: List[str] = field(default_factory=list)
    error_summary: str = ""
    fix_hints: List[str] = field(default_factory=list)
    improved_prompt: str = ""


class FeedbackAnalyzer:
    """Analyze Chef run failures and generate improved prompts.

    Usage:
        analyzer = FeedbackAnalyzer()
        analysis = analyzer.analyze_failure(run_id, result, logs)
        improved = analyzer.generate_improved_prompt(original_prompt, analysis)
        should_retry, reason = analyzer.should_retry(run_id, attempt, analysis)
    """

    def __init__(self, max_retries: int = 3) -> None:
        self.max_retries = max_retries
        self._retry_counts: Dict[str, int] = {}

    def analyze_failure(
        self,
        run_id: str,
        result: ChefResult,
        error_log: str = "",
    ) -> FailureAnalysis:
        """Analyze why a Chef run failed.

        Args:
            run_id: The run identifier.
            result: The ChefResult (success=False expected).
            error_log: Raw error output from the run.

        Returns:
            FailureAnalysis with category, patterns, and fix hints.
        """
        analysis = FailureAnalysis(run_id=run_id, failure_category="unknown")

        if result.success:
            analysis.failure_category = "none"
            return analysis

        # Match against known failure patterns
        combined_text = error_log.lower()
        for pattern_name, pattern_info in FAILURE_PATTERNS.items():
            keywords = pattern_info["keywords"]
            if any(kw.lower() in combined_text for kw in keywords):
                analysis.matched_patterns.append(pattern_name)
                analysis.fix_hints.append(pattern_info["fix_hint"])
                if analysis.failure_category == "unknown":
                    analysis.failure_category = pattern_info["category"]

        # Build error summary
        error_lines = [l for l in error_log.splitlines() if l.strip()]
        analysis.error_summary = "\n".join(error_lines[:10]) if error_lines else "No error details"

        logger.info(
            "Failure analysis for %s: category=%s, patterns=%s",
            run_id, analysis.failure_category, analysis.matched_patterns,
        )
        return analysis

    def generate_improved_prompt(
        self,
        original_prompt: str,
        analysis: FailureAnalysis,
    ) -> str:
        """Generate an improved prompt based on failure analysis.

        Appends fix hints and constraints to the original prompt.
        """
        if not analysis.fix_hints:
            return original_prompt

        improvements = "\n".join(f"- {hint}" for hint in analysis.fix_hints)
        improved = (
            f"{original_prompt}\n\n"
            f"IMPORTANT REQUIREMENTS (from previous attempt failures):\n"
            f"{improvements}\n"
            f"- Ensure the app compiles without errors before deploying.\n"
            f"- Keep the implementation simple and focused on core features."
        )
        analysis.improved_prompt = improved
        return improved

    def should_retry(
        self,
        run_id: str,
        attempt: int,
        analysis: FailureAnalysis,
    ) -> tuple:
        """Determine if a failed run should be retried.

        Args:
            run_id: The run identifier.
            attempt: Current attempt number (1-based).
            analysis: The failure analysis.

        Returns:
            Tuple of (should_retry: bool, reason: str).
        """
        if attempt >= self.max_retries:
            return False, f"Max retries ({self.max_retries}) reached"

        if analysis.failure_category == "timeout":
            return True, "Timeout — will retry with simplified prompt"

        if analysis.failure_category in ("build", "deploy", "runtime"):
            return True, f"{analysis.failure_category} failure — will retry with fixes"

        if analysis.failure_category == "unknown":
            return attempt < 2, "Unknown failure — one more attempt"

        return False, "No retry needed"

    def get_retry_count(self, run_id: str) -> int:
        """Get current retry count for a run."""
        return self._retry_counts.get(run_id, 0)

    def increment_retry(self, run_id: str) -> int:
        """Increment and return retry count."""
        count = self._retry_counts.get(run_id, 0) + 1
        self._retry_counts[run_id] = count
        return count

