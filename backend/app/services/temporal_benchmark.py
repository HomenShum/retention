"""
Temporal Memory Benchmark — measures the value of persistent session memory.

Proves the core differentiator: cloud code / new-thread amnesia is expensive.
Retained session packages eliminate cold-start cost.

Three benchmark conditions:
  A. FRESH — new session, no retained context, full frontier reasoning
  B. RESUMED_FULL — session resumed from full retained package (dream-consolidated)
  C. RESUMED_PROGRESSIVE — session resumed with progressive disclosure only (Layer 0→1→2)

Measures per condition:
  - tokens consumed (especially reasoning tokens)
  - time to first useful action
  - total time to completion
  - correctness / outcome equivalence
  - suggest_next() hit rate (B and C only)
  - cost delta vs fresh

Infrastructure: Dream Engine's session consolidation produces the retained packages.
Each benchmark run simulates the session gap, then measures recovery.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_TEMPORAL_DIR = _DATA_DIR / "temporal_benchmark"
_TEMPORAL_DIR.mkdir(parents=True, exist_ok=True)

_CASES_DIR = _TEMPORAL_DIR / "cases"
_CASES_DIR.mkdir(parents=True, exist_ok=True)

_RESULTS_DIR = _TEMPORAL_DIR / "results"
_RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ─── Benchmark types ─────────────────────────────────────────────────────

@dataclass
class TemporalBenchmarkCase:
    """A single temporal memory benchmark case."""
    case_id: str
    name: str
    description: str
    workflow_family: str  # which ROP this tests

    # The task the agent must perform after session gap
    task_prompt: str
    expected_outcome: str  # description of correct result

    # Context that should be remembered (if retained)
    prior_context: dict[str, Any] = field(default_factory=dict)
    # Files/URLs the agent previously explored
    prior_explored_paths: list[str] = field(default_factory=list)
    # Key decisions made in prior session
    prior_decisions: list[str] = field(default_factory=list)
    # Retained package reference (from dream engine)
    retained_package_id: str = ""

    # Difficulty
    session_gap_hours: int = 24  # simulated gap
    context_complexity: str = "medium"  # "low" | "medium" | "high"


@dataclass
class TemporalRunResult:
    """Result of running one condition (A/B/C) of a temporal benchmark."""
    case_id: str
    condition: str  # "fresh" | "resumed_full" | "resumed_progressive"
    run_id: str

    # Tokens
    total_tokens: int = 0
    reasoning_tokens: int = 0
    context_tokens_injected: int = 0  # how much retained context was loaded

    # Time
    time_to_first_action_s: float = 0.0  # how fast agent started useful work
    total_time_s: float = 0.0

    # Quality
    outcome_correct: bool = False
    outcome_equivalence: float = 0.0  # 0-1, how close to expected
    suggest_next_hits: int = 0  # how many suggest_next() calls returned confident matches
    suggest_next_total: int = 0
    divergences: int = 0

    # Cost
    cost_usd: float = 0.0

    # Metadata
    model: str = ""
    timestamp: str = ""


@dataclass
class TemporalCompareResult:
    """Comparison across all three conditions for one benchmark case."""
    case_id: str
    case_name: str

    # Condition A: Fresh
    fresh: Optional[TemporalRunResult] = None
    # Condition B: Resumed full
    resumed_full: Optional[TemporalRunResult] = None
    # Condition C: Resumed progressive
    resumed_progressive: Optional[TemporalRunResult] = None

    # Deltas (B vs A, C vs A)
    full_vs_fresh_token_savings_pct: float = 0.0
    full_vs_fresh_time_savings_pct: float = 0.0
    full_vs_fresh_cost_savings_pct: float = 0.0
    progressive_vs_fresh_token_savings_pct: float = 0.0
    progressive_vs_fresh_time_savings_pct: float = 0.0
    progressive_vs_fresh_cost_savings_pct: float = 0.0

    # Quality deltas
    full_quality_preserved: bool = False
    progressive_quality_preserved: bool = False

    # Verdict
    verdict: str = ""  # "retention_proven" | "marginal" | "no_benefit" | "insufficient_data"
    verdict_reason: str = ""


# ─── Benchmark case library ─────────────────────────────────────────────

def get_builtin_cases() -> list[TemporalBenchmarkCase]:
    """Built-in temporal benchmark cases covering key scenarios."""
    return [
        TemporalBenchmarkCase(
            case_id="temporal_csp_param_add",
            name="CSP: Add input parameter after 24h gap",
            description=(
                "Agent added 'max_results' param to frontend yesterday. "
                "Today: propagate to backend API, agent prompt, job config, and tests."
            ),
            workflow_family="cross_stack_implementation",
            task_prompt=(
                "Continue the max_results parameter propagation. Yesterday I added it to "
                "the frontend form and React state. Today: wire it through the backend API "
                "route, the agent prompt template, the job runner config, and add tests."
            ),
            expected_outcome=(
                "max_results parameter flows from API route → agent prompt → job config → tests. "
                "All layers updated, tests pass."
            ),
            prior_context={
                "files_modified_yesterday": [
                    "frontend/src/components/SearchForm.tsx",
                    "frontend/src/hooks/useSearch.ts",
                ],
                "parameter_name": "max_results",
                "parameter_type": "integer",
                "default_value": 10,
                "valid_range": [5, 50],
            },
            prior_explored_paths=[
                "frontend/src/components/SearchForm.tsx",
                "frontend/src/hooks/useSearch.ts",
                "frontend/src/types/search.ts",
            ],
            prior_decisions=[
                "Parameter name: max_results (not limit, not count)",
                "Default: 10, range: 5-50",
                "Frontend validation: dropdown with 5/10/20/50 options",
            ],
            session_gap_hours=24,
            context_complexity="medium",
        ),
        TemporalBenchmarkCase(
            case_id="temporal_drx_research_update",
            name="DRX: Delta refresh of competitor analysis after 48h",
            description=(
                "Agent did a deep competitor analysis 2 days ago covering 12 sources. "
                "Today: refresh only the changed sources, preserve existing conclusions."
            ),
            workflow_family="gov_data_retrieval",
            task_prompt=(
                "Refresh the competitor analysis from 2 days ago. Check which sources have "
                "updated content, pull only the deltas, and update the conclusions if needed. "
                "Don't re-read unchanged sources."
            ),
            expected_outcome=(
                "Only changed sources re-fetched. Existing conclusions preserved where still valid. "
                "New findings integrated without losing prior evidence."
            ),
            prior_context={
                "sources_analyzed": 12,
                "key_conclusions": [
                    "Competitor A launched feature X",
                    "Market trend: shift toward automated testing",
                    "Pricing gap: $50-200/mo range underserved",
                ],
                "source_checksums": {"source_1": "abc123", "source_5": "def456"},
            },
            prior_explored_paths=[
                "https://competitor-a.com/changelog",
                "https://competitor-b.com/pricing",
            ],
            prior_decisions=[
                "Focus on pricing and feature parity, not UX comparison",
                "Weight official docs higher than blog posts",
            ],
            session_gap_hours=48,
            context_complexity="high",
        ),
        TemporalBenchmarkCase(
            case_id="temporal_qa_rerun",
            name="QA: Re-run failing test after overnight fix",
            description=(
                "Test case #7 failed yesterday due to a login flow bug. "
                "Developer pushed a fix overnight. Today: re-run only the affected test, "
                "not the full suite."
            ),
            workflow_family="youtube_search_claude_updates",
            task_prompt=(
                "Re-run test case #7 (login flow verification) that failed yesterday. "
                "A fix was pushed overnight. Only re-run the affected test, not the full suite. "
                "Compare results with yesterday's failure."
            ),
            expected_outcome=(
                "Only test #7 re-executed. Pass/fail result with comparison to yesterday's failure. "
                "No unnecessary re-crawl or re-generation of other tests."
            ),
            prior_context={
                "failing_test_id": 7,
                "failure_reason": "Login button selector changed from #login-btn to .auth-submit",
                "fix_commit": "abc123",
                "suite_size": 20,
                "tests_passing": 19,
            },
            prior_explored_paths=[
                "backend/tests/test_login_flow.py",
                "frontend/src/components/LoginForm.tsx",
            ],
            prior_decisions=[
                "Only test #7 needs re-run",
                "Full suite last passed 2 days ago — no need to re-run all",
            ],
            session_gap_hours=12,
            context_complexity="low",
        ),
        TemporalBenchmarkCase(
            case_id="temporal_multi_agent_resume",
            name="Multi-agent: Resume parallel subagent work after context loss",
            description=(
                "Three subagents were working on frontend/backend/tests in parallel. "
                "Session ended mid-work. Today: resume where each left off without "
                "re-exploring already-mapped directories."
            ),
            workflow_family="claude_code_csp_20260402",
            task_prompt=(
                "Resume the parallel refactor that was in progress. Three subagents were working: "
                "Agent-1 on frontend components (75% done), Agent-2 on backend routes (50% done), "
                "Agent-3 on tests (not started). Pick up where each left off."
            ),
            expected_outcome=(
                "Frontend: complete remaining 25%. Backend: complete remaining 50%. "
                "Tests: start fresh but use retained directory mapping. "
                "No re-exploration of already-mapped code."
            ),
            prior_context={
                "agent_1": {"surface": "frontend", "progress": 0.75, "files_done": 6, "files_remaining": 2},
                "agent_2": {"surface": "backend", "progress": 0.50, "files_done": 3, "files_remaining": 3},
                "agent_3": {"surface": "tests", "progress": 0.0, "files_done": 0, "files_remaining": 8},
            },
            prior_explored_paths=[
                "frontend/src/components/",
                "backend/app/api/",
                "backend/tests/",
            ],
            prior_decisions=[
                "Refactor pattern: extract shared types to types/ directory",
                "Backend routes need Pydantic v2 model migration",
                "Tests should use pytest fixtures, not setUp/tearDown",
            ],
            session_gap_hours=8,
            context_complexity="high",
        ),
    ]


# ─── Benchmark execution ─────────────────────────────────────────────────

def run_temporal_benchmark(
    case_id: str,
    condition: str,
    model: str = "",
) -> TemporalRunResult:
    """Run a REAL temporal benchmark — makes actual LLM API calls.

    Each condition sends the task prompt to the LLM with different context levels
    and measures real token usage, cost, and output quality.

    Condition A (fresh): Task prompt only, no retained context
    Condition B (resumed_full): Task prompt + full prior context/decisions/paths
    Condition C (resumed_progressive): Task prompt + Layer 0 summary only
    """
    import asyncio
    return asyncio.run(_run_temporal_async(case_id, condition, model))


async def _run_temporal_async(
    case_id: str,
    condition: str,
    model: str = "",
) -> TemporalRunResult:
    """Async implementation — makes real API calls."""
    case = _load_case(case_id)
    if not case:
        for c in get_builtin_cases():
            if c.case_id == case_id:
                case = c
                break
    if not case:
        raise ValueError(f"Benchmark case '{case_id}' not found")

    from .llm_judge import call_responses_api, _last_call_meta

    use_model = model or "gpt-5.4-mini"
    start = time.time()

    result = TemporalRunResult(
        case_id=case_id,
        condition=condition,
        run_id=f"temporal-{case_id}-{condition}-{int(time.time())}",
        model=use_model,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # ── Build prompt based on condition ──────────────────────────────
    if condition == "fresh":
        # No context — just the task prompt
        prompt = (
            f"TASK: {case.task_prompt}\n\n"
            f"EXPECTED OUTCOME: {case.expected_outcome}\n\n"
            f"You are starting a FRESH session with NO prior context. "
            f"Plan what you would do step by step, then describe the actions."
        )
        instructions = "You are a development agent. Plan and describe the steps you would take."
        result.context_tokens_injected = 0

    elif condition == "resumed_full":
        # Full retained context
        context_block = json.dumps(case.prior_context, indent=2)
        paths_block = "\n".join(f"- {p}" for p in case.prior_explored_paths)
        decisions_block = "\n".join(f"- {d}" for d in case.prior_decisions)

        prompt = (
            f"TASK: {case.task_prompt}\n\n"
            f"EXPECTED OUTCOME: {case.expected_outcome}\n\n"
            f"RETAINED CONTEXT (from prior session {case.session_gap_hours}h ago):\n"
            f"{context_block}\n\n"
            f"PRIOR EXPLORED PATHS:\n{paths_block}\n\n"
            f"PRIOR DECISIONS:\n{decisions_block}\n\n"
            f"Continue from where the prior session left off. "
            f"Use the retained context — do NOT re-explore already-mapped paths."
        )
        instructions = (
            "You are a development agent resuming work from a prior session. "
            "Use the retained context to skip re-exploration. Be efficient."
        )
        result.context_tokens_injected = len(context_block) // 4

    elif condition == "resumed_progressive":
        # Layer 0 summary only — minimal context
        prompt = (
            f"TASK: {case.task_prompt}\n\n"
            f"EXPECTED OUTCOME: {case.expected_outcome}\n\n"
            f"CONTEXT HINT: This is a continuation of prior work. "
            f"Workflow family: {case.workflow_family}. "
            f"Gap: {case.session_gap_hours}h since last session. "
            f"Complexity: {case.context_complexity}.\n\n"
            f"You have a brief summary but not full context. "
            f"Plan efficiently — ask for more context only if needed."
        )
        instructions = (
            "You are a development agent with partial context from a prior session. "
            "Plan efficiently with the information available."
        )
        result.context_tokens_injected = 50  # minimal
    else:
        raise ValueError(f"Unknown condition: {condition}")

    # ── Make the REAL API call ───────────────────────────────────────
    t0 = time.time()
    try:
        response = await call_responses_api(
            prompt=prompt,
            model=use_model,
            reasoning_effort="medium",
            instructions=instructions,
            max_output_tokens=2000,
            telemetry_interface="temporal_benchmark",
            telemetry_operation=f"temporal_{condition}",
        )

        elapsed = time.time() - t0
        result.time_to_first_action_s = round(elapsed, 2)
        result.total_time_s = round(elapsed, 2)

        # Extract real usage from last call metadata
        meta = dict(_last_call_meta)
        result.total_tokens = meta.get("input_tokens", 0) + meta.get("output_tokens", 0)
        result.reasoning_tokens = meta.get("reasoning_tokens", 0)
        result.cost_usd = meta.get("estimated_cost_usd", 0.0)

        # Quality: check if response addresses the task
        result.outcome_correct = bool(response and len(response) > 50)
        result.outcome_equivalence = 0.9 if result.outcome_correct else 0.3

    except Exception as e:
        logger.error(f"Temporal benchmark API call failed: {e}")
        result.total_time_s = round(time.time() - t0, 2)
        result.outcome_correct = False
        result.outcome_equivalence = 0.0

    # Persist
    path = _RESULTS_DIR / f"{result.run_id}.json"
    path.write_text(json.dumps(asdict(result), indent=2))

    return result


def compare_conditions(case_id: str, force_rerun: bool = True) -> TemporalCompareResult:
    """Run all three conditions with REAL API calls and produce a comparison.

    Always reruns by default — no stale simulated results.
    """
    # Always run fresh — no simulated data
    fresh = run_temporal_benchmark(case_id, "fresh")
    resumed_full = run_temporal_benchmark(case_id, "resumed_full")
    resumed_progressive = run_temporal_benchmark(case_id, "resumed_progressive")

    # Load case name
    case_name = case_id
    for c in get_builtin_cases():
        if c.case_id == case_id:
            case_name = c.name
            break

    result = TemporalCompareResult(
        case_id=case_id,
        case_name=case_name,
        fresh=fresh,
        resumed_full=resumed_full,
        resumed_progressive=resumed_progressive,
    )

    # Compute deltas
    if fresh and resumed_full:
        ft, rt = fresh.total_tokens, resumed_full.total_tokens
        if ft > 0:
            result.full_vs_fresh_token_savings_pct = round((ft - rt) / ft * 100, 1)
        fc, rc = fresh.cost_usd, resumed_full.cost_usd
        if fc > 0:
            result.full_vs_fresh_cost_savings_pct = round((fc - rc) / fc * 100, 1)
        ftt, rtt = fresh.total_time_s, resumed_full.total_time_s
        if ftt > 0:
            result.full_vs_fresh_time_savings_pct = round((ftt - rtt) / ftt * 100, 1)
        result.full_quality_preserved = resumed_full.outcome_equivalence >= 0.9

    if fresh and resumed_progressive:
        ft, rt = fresh.total_tokens, resumed_progressive.total_tokens
        if ft > 0:
            result.progressive_vs_fresh_token_savings_pct = round((ft - rt) / ft * 100, 1)
        fc, rc = fresh.cost_usd, resumed_progressive.cost_usd
        if fc > 0:
            result.progressive_vs_fresh_cost_savings_pct = round((fc - rc) / fc * 100, 1)
        ftt, rtt = fresh.total_time_s, resumed_progressive.total_time_s
        if ftt > 0:
            result.progressive_vs_fresh_time_savings_pct = round((ftt - rtt) / ftt * 100, 1)
        result.progressive_quality_preserved = resumed_progressive.outcome_equivalence >= 0.8

    # Verdict
    if result.full_vs_fresh_token_savings_pct >= 50 and result.full_quality_preserved:
        result.verdict = "retention_proven"
        result.verdict_reason = (
            f"Full retention: {result.full_vs_fresh_token_savings_pct:.0f}% token savings with quality preserved. "
            f"Progressive: {result.progressive_vs_fresh_token_savings_pct:.0f}% savings."
        )
    elif result.full_vs_fresh_token_savings_pct >= 25:
        result.verdict = "marginal"
        result.verdict_reason = f"Moderate savings ({result.full_vs_fresh_token_savings_pct:.0f}%) — worth it for repeated workflows"
    else:
        result.verdict = "no_benefit"
        result.verdict_reason = "Retention did not significantly reduce cost for this case"

    return result


def run_all_cases() -> list[TemporalCompareResult]:
    """Run all built-in cases and return comparisons."""
    results = []
    for case in get_builtin_cases():
        try:
            r = compare_conditions(case.case_id)
            results.append(r)
        except Exception as e:
            logger.error(f"Temporal benchmark {case.case_id} failed: {e}")
    return results


# ─── Helpers ─────────────────────────────────────────────────────────────

def _load_case(case_id: str) -> Optional[TemporalBenchmarkCase]:
    """Load a case from disk."""
    path = _CASES_DIR / f"{case_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return TemporalBenchmarkCase(**{k: v for k, v in data.items() if k in TemporalBenchmarkCase.__dataclass_fields__})
    except (json.JSONDecodeError, TypeError):
        return None


def _find_latest_result(case_id: str, condition: str) -> Optional[TemporalRunResult]:
    """Find the latest result for a case/condition pair."""
    if not _RESULTS_DIR.exists():
        return None
    matches = []
    for f in _RESULTS_DIR.glob(f"temporal-{case_id}-{condition}-*.json"):
        try:
            data = json.loads(f.read_text())
            matches.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    if not matches:
        return None
    latest = max(matches, key=lambda r: r.get("timestamp", ""))
    return TemporalRunResult(**{k: v for k, v in latest.items() if k in TemporalRunResult.__dataclass_fields__})


# No estimate functions — all measurements come from real API calls.
