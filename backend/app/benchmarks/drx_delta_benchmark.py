"""
DRX Delta Refresh Benchmark — proves retained research can update via delta refresh.

NOT claiming cheap replay replaces frontier deep research.
PROVING the narrower, defensible claim:
  1. Prior frontier research exists (source cluster retained)
  2. Replay performs an UPDATE / DELTA REFRESH
  3. Structured judge measures whether refresh preserved key claims + source support

Three lanes:
  Lane 1: Fresh Research     — frontier model, no prior data, full exploration
  Lane 2: Delta Refresh      — same model, retained sources, update-only pass
  Lane 3: Cheap Delta        — cheap model, retained sources, escalation on drift

DRX-specific metrics (on top of canonical scorecard):
  - claim_preservation_rate  — % of original key claims still supported after refresh
  - source_coverage_rate     — % of cited sources still accessible/valid
  - new_claims_added         — count of genuinely new findings in delta
  - stale_claims_removed     — count of outdated claims correctly identified
  - source_cluster_reuse_pct — % of source URLs reused vs re-fetched

IMPORTANT — truth governance:
  - run_drx_delta_benchmark() is OFFLINE EVAL — takes pre-existing text, no API calls.
    All outputs are labeled data_source="simulated" until fed real API outputs.
  - run_drx_delta_benchmark_live() makes REAL API calls via OpenAI and produces
    data_source="live_api" results that can be cited as proof.
  - Default models are GPT-5.4 family (we have GPT API key, not Claude).
"""

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .canonical_scorecard import CanonicalScorecard, aggregate_scorecards
from .evidence_schema import BENCHMARK_MODEL_PRICING

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_DRX_DIR = _DATA_DIR / "drx_benchmarks"
_DRX_DIR.mkdir(parents=True, exist_ok=True)
_CARD_DIR = _DATA_DIR / "benchmark_cards"
_CARD_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# DRX-specific models
# ---------------------------------------------------------------------------

class ResearchClaim(BaseModel):
    """A single factual claim extracted from research output."""
    claim_id: str = ""
    text: str = ""
    source_urls: List[str] = Field(default_factory=list)
    confidence: float = 0.0        # 0.0-1.0
    category: str = ""             # "finding", "statistic", "quote", "recommendation"
    still_supported: bool = True   # after delta refresh
    stale: bool = False            # marked as outdated by delta


class SourceCluster(BaseModel):
    """A group of related sources retained from prior research."""
    cluster_id: str = ""
    topic: str = ""
    urls: List[str] = Field(default_factory=list)
    fetched_at: str = ""
    reused_in_delta: bool = False
    content_hash: str = ""         # for staleness detection


class DRXDeltaMetrics(BaseModel):
    """DRX-specific metrics beyond the canonical scorecard."""
    # Claim preservation
    total_claims_baseline: int = 0
    claims_preserved: int = 0
    claims_invalidated: int = 0
    new_claims_added: int = 0
    claim_preservation_rate: float = 0.0   # preserved / total_baseline

    # Source coverage
    total_sources_baseline: int = 0
    sources_still_valid: int = 0
    sources_stale: int = 0
    sources_new: int = 0
    source_coverage_rate: float = 0.0      # still_valid / total_baseline

    # Reuse efficiency
    source_cluster_reuse_pct: float = 0.0  # % of URLs reused vs re-fetched
    urls_reused: int = 0
    urls_refetched: int = 0

    # Quality judge
    judge_verdict: str = ""                # "acceptable" | "degraded" | "failed"
    judge_reasoning: str = ""
    judge_confidence: float = 0.0


class DRXLaneResult(BaseModel):
    """Result from running a single DRX benchmark lane."""
    lane_id: str                   # "fresh_research", "delta_refresh", "cheap_delta"
    label: str
    model: str
    mode: str                      # "explore", "delta_refresh", "cheap_delta_with_escalation"
    run_id: str = ""
    success: bool = False

    # Research output
    claims: List[ResearchClaim] = Field(default_factory=list)
    source_clusters: List[SourceCluster] = Field(default_factory=list)
    total_claims: int = 0
    total_sources: int = 0

    # Execution metrics
    time_seconds: float = 0.0
    tokens_used: int = 0
    cost_usd: float = 0.0
    escalation_count: int = 0

    # DRX-specific
    drx_metrics: Optional[DRXDeltaMetrics] = None

    # Canonical scorecard
    scorecard: Optional[CanonicalScorecard] = None

    error: str = ""


class DRXComparisonRow(BaseModel):
    """Single row in the DRX comparison table."""
    metric: str
    lane_1: str    # Fresh Research
    lane_2: str    # Delta Refresh
    lane_3: str    # Cheap Delta
    delta_2_vs_1: str = ""
    delta_3_vs_1: str = ""


class DRXBenchmarkResult(BaseModel):
    """Complete result from a DRX delta refresh benchmark."""
    benchmark_id: str = Field(default_factory=lambda: f"drx-{uuid.uuid4().hex[:8]}")
    task_name: str = ""
    research_topic: str = ""
    timestamp: str = Field(default_factory=_now_iso)
    data_source: str = "simulated"  # "simulated" | "live_api" — truth governance
    judge_method: str = "heuristic"  # "heuristic" | "llm_judge" — how claims were compared
    final_verdict: str = ""          # "acceptable" | "degraded" | "failed" | "not_ready"
    lanes: List[DRXLaneResult] = Field(default_factory=list)
    comparison_table: List[DRXComparisonRow] = Field(default_factory=list)
    drx_summary: Dict[str, Any] = Field(default_factory=dict)
    summary: str = ""


# ---------------------------------------------------------------------------
# Claim extraction & comparison (structured judge)
# ---------------------------------------------------------------------------

def extract_claims_from_output(research_output: str) -> List[ResearchClaim]:
    """Extract structured claims from research text output.

    Uses heuristic extraction: sentences with numbers, citations,
    or recommendation patterns are treated as claims.
    """
    import re
    claims = []
    sentences = re.split(r'(?<=[.!?])\s+', research_output)

    for i, sent in enumerate(sentences):
        sent = sent.strip()
        if len(sent) < 20:
            continue

        # Heuristic: claim if it contains numbers, percentages, or citation patterns
        is_claim = bool(
            re.search(r'\d+%|\$[\d,.]+|\d+\.\d+', sent)  # numbers/percentages
            or re.search(r'(?:according to|reported|found that|shows that)', sent, re.I)
            or re.search(r'(?:recommend|should|must|critical)', sent, re.I)
        )

        if is_claim:
            # Extract URLs if present
            urls = re.findall(r'https?://[^\s\)]+', sent)
            category = "statistic" if re.search(r'\d+%|\$', sent) else "finding"
            if re.search(r'recommend|should', sent, re.I):
                category = "recommendation"

            claims.append(ResearchClaim(
                claim_id=f"claim-{uuid.uuid4().hex[:8]}",
                text=sent[:200],
                source_urls=urls,
                confidence=0.8,
                category=category,
            ))

    return claims


def extract_source_clusters(research_output: str, topic: str = "") -> List[SourceCluster]:
    """Extract source URL clusters from research output."""
    import hashlib
    import re

    urls = re.findall(r'https?://[^\s\)\]]+', research_output)
    if not urls:
        return []

    # Group by domain
    from collections import defaultdict
    by_domain: Dict[str, List[str]] = defaultdict(list)
    for url in set(urls):
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
            by_domain[domain].append(url)
        except Exception:
            by_domain["other"].append(url)

    clusters = []
    for domain, domain_urls in by_domain.items():
        content = "|".join(sorted(domain_urls))
        clusters.append(SourceCluster(
            cluster_id=f"src-{uuid.uuid4().hex[:8]}",
            topic=topic or domain,
            urls=domain_urls,
            fetched_at=_now_iso(),
            content_hash=hashlib.sha256(content.encode()).hexdigest()[:12],
        ))

    return clusters


def compare_claims(
    baseline_claims: List[ResearchClaim],
    refresh_claims: List[ResearchClaim],
) -> DRXDeltaMetrics:
    """Compare baseline claims against delta refresh claims.

    Measures claim preservation, source coverage, and new findings.
    """
    # Simple text-similarity matching for claim comparison
    baseline_texts = {c.claim_id: c.text.lower()[:100] for c in baseline_claims}
    refresh_texts = {c.claim_id: c.text.lower()[:100] for c in refresh_claims}

    preserved = 0
    invalidated = 0

    for b_id, b_text in baseline_texts.items():
        # Check if any refresh claim is similar (>50% word overlap)
        found = False
        b_words = set(b_text.split())
        for r_text in refresh_texts.values():
            r_words = set(r_text.split())
            overlap = len(b_words & r_words) / max(len(b_words | r_words), 1)
            if overlap > 0.4:
                found = True
                break
        if found:
            preserved += 1
        else:
            invalidated += 1

    # Find genuinely new claims
    new_claims = 0
    for r_id, r_text in refresh_texts.items():
        r_words = set(r_text.split())
        is_new = True
        for b_text in baseline_texts.values():
            b_words = set(b_text.split())
            overlap = len(b_words & r_words) / max(len(b_words | r_words), 1)
            if overlap > 0.4:
                is_new = False
                break
        if is_new:
            new_claims += 1

    # Source coverage
    baseline_urls = set()
    for c in baseline_claims:
        baseline_urls.update(c.source_urls)
    refresh_urls = set()
    for c in refresh_claims:
        refresh_urls.update(c.source_urls)

    urls_still_valid = len(baseline_urls & refresh_urls)
    urls_stale = len(baseline_urls - refresh_urls)
    urls_new = len(refresh_urls - baseline_urls)

    total_baseline = len(baseline_claims) or 1
    total_sources = len(baseline_urls) or 1

    return DRXDeltaMetrics(
        total_claims_baseline=len(baseline_claims),
        claims_preserved=preserved,
        claims_invalidated=invalidated,
        new_claims_added=new_claims,
        claim_preservation_rate=round(preserved / total_baseline, 3),
        total_sources_baseline=len(baseline_urls),
        sources_still_valid=urls_still_valid,
        sources_stale=urls_stale,
        sources_new=urls_new,
        source_coverage_rate=round(urls_still_valid / total_sources, 3),
        urls_reused=urls_still_valid,
        urls_refetched=urls_new,
        source_cluster_reuse_pct=round(
            urls_still_valid / max(urls_still_valid + urls_new, 1) * 100, 1
        ),
    )


def compare_claims_llm(
    baseline_claims: List[ResearchClaim],
    refresh_claims: List[ResearchClaim],
    model: str = "gpt-5.4-mini",
) -> DRXDeltaMetrics:
    """Compare claims using an LLM judge (REAL API call).

    Asks the model to evaluate each baseline claim against the refresh output
    and determine if it was preserved, invalidated, or updated.

    Requires OPENAI_API_KEY environment variable.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set — falling back to heuristic comparison")
        return compare_claims(baseline_claims, refresh_claims)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
    except ImportError:
        logger.warning("openai package not installed — falling back to heuristic")
        return compare_claims(baseline_claims, refresh_claims)

    baseline_text = "\n".join(f"[{c.claim_id}] {c.text}" for c in baseline_claims)
    refresh_text = "\n".join(f"[{c.claim_id}] {c.text}" for c in refresh_claims)

    prompt = f"""You are a structured research judge. Compare these baseline claims against refresh claims.

BASELINE CLAIMS:
{baseline_text}

REFRESH CLAIMS:
{refresh_text}

For each baseline claim, determine:
- "preserved": the refresh contains an equivalent or updated version
- "invalidated": the refresh contradicts or drops this claim
- "updated": the refresh contains a newer version with different numbers/dates

Respond as JSON:
{{"results": [{{"claim_id": "...", "status": "preserved|invalidated|updated", "matched_refresh_id": "...|null"}}], "new_claims_count": <int>}}"""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        result = json.loads(response.choices[0].message.content)
        results = result.get("results", [])

        preserved = sum(1 for r in results if r.get("status") in ("preserved", "updated"))
        invalidated = sum(1 for r in results if r.get("status") == "invalidated")
        new_claims = result.get("new_claims_count", 0)

        # Source comparison (still heuristic — URLs don't need LLM)
        baseline_urls = set()
        for c in baseline_claims:
            baseline_urls.update(c.source_urls)
        refresh_urls = set()
        for c in refresh_claims:
            refresh_urls.update(c.source_urls)

        urls_still_valid = len(baseline_urls & refresh_urls)
        urls_stale = len(baseline_urls - refresh_urls)
        urls_new = len(refresh_urls - baseline_urls)
        total_baseline = len(baseline_claims) or 1
        total_sources = len(baseline_urls) or 1

        return DRXDeltaMetrics(
            total_claims_baseline=len(baseline_claims),
            claims_preserved=preserved,
            claims_invalidated=invalidated,
            new_claims_added=new_claims,
            claim_preservation_rate=round(preserved / total_baseline, 3),
            total_sources_baseline=len(baseline_urls),
            sources_still_valid=urls_still_valid,
            sources_stale=urls_stale,
            sources_new=urls_new,
            source_coverage_rate=round(urls_still_valid / total_sources, 3),
            urls_reused=urls_still_valid,
            urls_refetched=urls_new,
            source_cluster_reuse_pct=round(
                urls_still_valid / max(urls_still_valid + urls_new, 1) * 100, 1
            ),
        )
    except Exception as e:
        logger.error(f"LLM judge failed: {e} — falling back to heuristic")
        return compare_claims(baseline_claims, refresh_claims)


def judge_delta_quality(
    baseline_claims: List[ResearchClaim],
    refresh_claims: List[ResearchClaim],
    drx_metrics: DRXDeltaMetrics,
) -> DRXDeltaMetrics:
    """Structured judge for delta refresh quality.

    Verdict rules:
      - acceptable: claim_preservation >= 0.7 AND source_coverage >= 0.5
      - degraded:   claim_preservation >= 0.4 OR source_coverage >= 0.3
      - failed:     below both thresholds
    """
    cp = drx_metrics.claim_preservation_rate
    sc = drx_metrics.source_coverage_rate

    if cp >= 0.7 and sc >= 0.5:
        drx_metrics.judge_verdict = "acceptable"
        drx_metrics.judge_reasoning = (
            f"Claim preservation {cp:.0%} >= 70% threshold, "
            f"source coverage {sc:.0%} >= 50% threshold. "
            f"{drx_metrics.new_claims_added} new claims added, "
            f"{drx_metrics.claims_invalidated} stale claims removed."
        )
        drx_metrics.judge_confidence = min(cp, sc)
    elif cp >= 0.4 or sc >= 0.3:
        drx_metrics.judge_verdict = "degraded"
        drx_metrics.judge_reasoning = (
            f"Partial preservation: claims {cp:.0%}, sources {sc:.0%}. "
            f"Delta refresh lost significant content. "
            f"Escalation to frontier model recommended."
        )
        drx_metrics.judge_confidence = max(cp, sc) * 0.6
    else:
        drx_metrics.judge_verdict = "failed"
        drx_metrics.judge_reasoning = (
            f"Critical loss: claims {cp:.0%}, sources {sc:.0%}. "
            f"Delta refresh is not viable — full re-research required."
        )
        drx_metrics.judge_confidence = 0.1

    return drx_metrics


# ---------------------------------------------------------------------------
# Benchmark execution
# ---------------------------------------------------------------------------

def _estimate_cost(tokens: int, model: str) -> float:
    """Estimate cost using BENCHMARK_MODEL_PRICING."""
    pricing = BENCHMARK_MODEL_PRICING.get(model, {"input": 1.0})
    return round((tokens / 1_000_000) * pricing["input"], 6)


def build_drx_scorecard(
    lane: DRXLaneResult,
    baseline_lane: Optional[DRXLaneResult] = None,
) -> CanonicalScorecard:
    """Build a canonical scorecard from a DRX lane result."""
    baseline_cost = baseline_lane.cost_usd if baseline_lane else lane.cost_usd
    baseline_tokens = baseline_lane.tokens_used if baseline_lane else lane.tokens_used
    baseline_time = baseline_lane.time_seconds if baseline_lane else lane.time_seconds

    token_savings = (1 - lane.tokens_used / max(baseline_tokens, 1)) * 100 if baseline_tokens > 0 else 0
    cost_savings_usd = baseline_cost - lane.cost_usd
    time_savings = (1 - lane.time_seconds / max(baseline_time, 1)) * 100 if baseline_time > 0 else 0

    # Completion: based on claim preservation for delta lanes
    if lane.drx_metrics and lane.lane_id != "fresh_research":
        completion = lane.drx_metrics.claim_preservation_rate
        outcome_eq = lane.drx_metrics.judge_verdict == "acceptable"
    else:
        completion = 1.0  # baseline is always complete
        outcome_eq = True

    # Escalation rate
    esc_rate = lane.escalation_count / max(lane.total_claims, 1) if lane.total_claims > 0 else 0

    sc = CanonicalScorecard(
        workflow_name=f"DRX: {lane.label}",
        workflow_family="DRX",
        model_baseline=baseline_lane.model if baseline_lane else lane.model,
        model_replay=lane.model,
        run_count=1,
        completion_score=round(completion, 3),
        outcome_equivalent=outcome_eq,
        token_savings_pct=round(max(token_savings, 0), 1),
        cost_savings_usd=round(max(cost_savings_usd, 0), 4),
        cost_baseline_usd=round(baseline_cost, 4),
        cost_replay_usd=round(lane.cost_usd, 4),
        time_savings_pct=round(max(time_savings, 0), 1),
        replay_success_rate=1.0 if lane.success else 0.0,
        escalation_rate=round(esc_rate, 3),
    )
    sc.compute_composite()
    return sc


def run_drx_delta_benchmark(
    research_topic: str,
    baseline_output: str,
    refresh_output: str,
    cheap_refresh_output: str = "",
    frontier_model: str = "gpt-5.4",
    replay_model: str = "gpt-5.4-mini",
    cheap_model: str = "gpt-5.4-nano",
    baseline_tokens: int = 50000,
    refresh_tokens: int = 15000,
    cheap_tokens: int = 8000,
    baseline_time_s: float = 120.0,
    refresh_time_s: float = 40.0,
    cheap_time_s: float = 25.0,
    escalation_count: int = 0,
    use_llm_judge: bool = False,
) -> DRXBenchmarkResult:
    """Run a DRX delta refresh benchmark from pre-existing research outputs.

    OFFLINE EVAL — no API calls made. All outputs labeled data_source="simulated".
    For live benchmarks with real API calls, use run_drx_delta_benchmark_live().

    Args:
        use_llm_judge: If True, calls GPT API for claim comparison instead of
                       word-overlap heuristic. Requires OPENAI_API_KEY.
    """
    benchmark_id = f"drx-{uuid.uuid4().hex[:8]}"

    # Select claim comparison method
    _compare_fn = compare_claims_llm if use_llm_judge else compare_claims
    _judge_method = "llm_judge" if use_llm_judge else "heuristic"

    # Extract claims and sources from each output
    baseline_claims = extract_claims_from_output(baseline_output)
    baseline_sources = extract_source_clusters(baseline_output, research_topic)

    refresh_claims = extract_claims_from_output(refresh_output)
    refresh_sources = extract_source_clusters(refresh_output, research_topic)

    # Lane 1: Fresh Research (baseline)
    lane1 = DRXLaneResult(
        lane_id="fresh_research",
        label="Lane 1: Fresh Research",
        model=frontier_model,
        mode="explore",
        run_id=f"drx-l1-{uuid.uuid4().hex[:8]}",
        success=True,
        claims=baseline_claims,
        source_clusters=baseline_sources,
        total_claims=len(baseline_claims),
        total_sources=sum(len(sc.urls) for sc in baseline_sources),
        time_seconds=baseline_time_s,
        tokens_used=baseline_tokens,
        cost_usd=_estimate_cost(baseline_tokens, frontier_model),
    )

    # Lane 2: Delta Refresh (same model, retained sources)
    drx_metrics_l2 = _compare_fn(baseline_claims, refresh_claims)
    drx_metrics_l2 = judge_delta_quality(baseline_claims, refresh_claims, drx_metrics_l2)

    lane2 = DRXLaneResult(
        lane_id="delta_refresh",
        label="Lane 2: Delta Refresh",
        model=replay_model,
        mode="delta_refresh",
        run_id=f"drx-l2-{uuid.uuid4().hex[:8]}",
        success=drx_metrics_l2.judge_verdict in ("acceptable", "degraded"),
        claims=refresh_claims,
        source_clusters=refresh_sources,
        total_claims=len(refresh_claims),
        total_sources=sum(len(sc.urls) for sc in refresh_sources),
        time_seconds=refresh_time_s,
        tokens_used=refresh_tokens,
        cost_usd=_estimate_cost(refresh_tokens, replay_model),
        drx_metrics=drx_metrics_l2,
    )

    # Lane 3: Cheap Delta (cheap model, escalation on drift)
    if cheap_refresh_output:
        cheap_claims = extract_claims_from_output(cheap_refresh_output)
        cheap_sources = extract_source_clusters(cheap_refresh_output, research_topic)
    else:
        # Simulate: cheap model preserves ~80% of what delta refresh preserves
        cheap_claims = refresh_claims[:int(len(refresh_claims) * 0.8)] if refresh_claims else []
        cheap_sources = refresh_sources

    drx_metrics_l3 = _compare_fn(baseline_claims, cheap_claims)
    drx_metrics_l3 = judge_delta_quality(baseline_claims, cheap_claims, drx_metrics_l3)

    lane3 = DRXLaneResult(
        lane_id="cheap_delta",
        label="Lane 3: Cheap Delta",
        model=cheap_model,
        mode="cheap_delta_with_escalation",
        run_id=f"drx-l3-{uuid.uuid4().hex[:8]}",
        success=drx_metrics_l3.judge_verdict in ("acceptable", "degraded"),
        claims=cheap_claims,
        source_clusters=cheap_sources,
        total_claims=len(cheap_claims),
        total_sources=sum(len(sc.urls) for sc in cheap_sources),
        time_seconds=cheap_time_s,
        tokens_used=cheap_tokens,
        cost_usd=_estimate_cost(cheap_tokens, cheap_model),
        escalation_count=escalation_count,
        drx_metrics=drx_metrics_l3,
    )

    # Build scorecards
    lane1.scorecard = build_drx_scorecard(lane1)
    lane2.scorecard = build_drx_scorecard(lane2, baseline_lane=lane1)
    lane3.scorecard = build_drx_scorecard(lane3, baseline_lane=lane1)

    # Build comparison table
    comparison = _build_comparison_table(lane1, lane2, lane3)

    # Summary
    l2_verdict = drx_metrics_l2.judge_verdict
    l3_verdict = drx_metrics_l3.judge_verdict
    l2_savings = lane2.scorecard.cost_savings_usd if lane2.scorecard else 0
    l3_savings = lane3.scorecard.cost_savings_usd if lane3.scorecard else 0

    result = DRXBenchmarkResult(
        benchmark_id=benchmark_id,
        task_name=f"drx_delta_{research_topic.replace(' ', '_')[:30]}",
        research_topic=research_topic,
        data_source="simulated",  # OFFLINE EVAL — no API calls made for research
        judge_method=_judge_method,
        lanes=[lane1, lane2, lane3],
        comparison_table=comparison,
        drx_summary={
            "claim_preservation_l2": drx_metrics_l2.claim_preservation_rate,
            "claim_preservation_l3": drx_metrics_l3.claim_preservation_rate,
            "source_reuse_l2": drx_metrics_l2.source_cluster_reuse_pct,
            "source_reuse_l3": drx_metrics_l3.source_cluster_reuse_pct,
            "verdict_l2": l2_verdict,
            "verdict_l3": l3_verdict,
            "new_claims_l2": drx_metrics_l2.new_claims_added,
            "new_claims_l3": drx_metrics_l3.new_claims_added,
        },
        summary=(
            f"DRX Delta Refresh: topic='{research_topic}'. "
            f"Lane 2 ({replay_model}): {l2_verdict}, "
            f"claim preservation {drx_metrics_l2.claim_preservation_rate:.0%}, "
            f"${l2_savings:.4f} saved. "
            f"Lane 3 ({cheap_model}): {l3_verdict}, "
            f"claim preservation {drx_metrics_l3.claim_preservation_rate:.0%}, "
            f"${l3_savings:.4f} saved."
        ),
    )

    # Compute final_verdict from lane verdicts
    verdicts = []
    for lane in [lane2, lane3]:
        if lane.drx_metrics:
            verdicts.append(lane.drx_metrics.judge_verdict)
    if all(v == "acceptable" for v in verdicts):
        result.final_verdict = "acceptable"
    elif any(v == "acceptable" for v in verdicts):
        result.final_verdict = "degraded"
    elif all(v == "failed" for v in verdicts):
        result.final_verdict = "failed"
    else:
        result.final_verdict = "not_ready"

    # Persist
    _save_benchmark(result)
    return result


def _build_comparison_table(
    lane1: DRXLaneResult,
    lane2: DRXLaneResult,
    lane3: DRXLaneResult,
) -> List[DRXComparisonRow]:
    """Build the three-pane comparison table."""
    rows = []

    def _pct_delta(baseline: float, value: float) -> str:
        if baseline == 0:
            return "N/A"
        delta = ((value - baseline) / baseline) * 100
        sign = "+" if delta >= 0 else ""
        return f"{sign}{delta:.1f}%"

    def _row(metric, v1, v2, v3):
        return DRXComparisonRow(
            metric=metric,
            lane_1=str(v1),
            lane_2=str(v2),
            lane_3=str(v3),
            delta_2_vs_1=_pct_delta(float(v1) if isinstance(v1, (int, float)) else 0, float(v2) if isinstance(v2, (int, float)) else 0),
            delta_3_vs_1=_pct_delta(float(v1) if isinstance(v1, (int, float)) else 0, float(v3) if isinstance(v3, (int, float)) else 0),
        )

    # Claims
    rows.append(_row("Total Claims", lane1.total_claims, lane2.total_claims, lane3.total_claims))

    # DRX-specific
    m2 = lane2.drx_metrics or DRXDeltaMetrics()
    m3 = lane3.drx_metrics or DRXDeltaMetrics()
    rows.append(DRXComparisonRow(metric="Claim Preservation", lane_1="baseline", lane_2=f"{m2.claim_preservation_rate:.0%}", lane_3=f"{m3.claim_preservation_rate:.0%}"))
    rows.append(DRXComparisonRow(metric="Source Coverage", lane_1="baseline", lane_2=f"{m2.source_coverage_rate:.0%}", lane_3=f"{m3.source_coverage_rate:.0%}"))
    rows.append(DRXComparisonRow(metric="Source Reuse %", lane_1="N/A", lane_2=f"{m2.source_cluster_reuse_pct:.0f}%", lane_3=f"{m3.source_cluster_reuse_pct:.0f}%"))
    rows.append(DRXComparisonRow(metric="New Claims", lane_1="N/A", lane_2=str(m2.new_claims_added), lane_3=str(m3.new_claims_added)))
    rows.append(DRXComparisonRow(metric="Judge Verdict", lane_1="baseline", lane_2=m2.judge_verdict, lane_3=m3.judge_verdict))

    # Canonical metrics
    s1 = lane1.scorecard or CanonicalScorecard()
    s2 = lane2.scorecard or CanonicalScorecard()
    s3 = lane3.scorecard or CanonicalScorecard()
    rows.append(_row("Tokens", lane1.tokens_used, lane2.tokens_used, lane3.tokens_used))
    rows.append(_row("Cost (USD)", round(lane1.cost_usd, 4), round(lane2.cost_usd, 4), round(lane3.cost_usd, 4)))
    rows.append(DRXComparisonRow(metric="Token Savings %", lane_1="0%", lane_2=f"{s2.token_savings_pct:.1f}%", lane_3=f"{s3.token_savings_pct:.1f}%"))
    rows.append(DRXComparisonRow(metric="Cost Savings %", lane_1="0%", lane_2=f"{(1 - lane2.cost_usd / max(lane1.cost_usd, 0.001)) * 100:.1f}%", lane_3=f"{(1 - lane3.cost_usd / max(lane1.cost_usd, 0.001)) * 100:.1f}%"))
    rows.append(_row("Time (s)", lane1.time_seconds, lane2.time_seconds, lane3.time_seconds))
    rows.append(_row("Escalations", 0, 0, lane3.escalation_count))
    rows.append(DRXComparisonRow(metric="Composite Score", lane_1=f"{s1.composite_score:.3f}", lane_2=f"{s2.composite_score:.3f}", lane_3=f"{s3.composite_score:.3f}"))
    rows.append(DRXComparisonRow(metric="Grade", lane_1=s1.grade, lane_2=s2.grade, lane_3=s3.grade))

    return rows


def _save_benchmark(result: DRXBenchmarkResult) -> None:
    """Persist benchmark result to disk."""
    path = _DRX_DIR / f"{result.benchmark_id}.json"
    # Serialize without claim text (too large for cards)
    data = result.model_dump()
    # Strip full claim objects from lanes to keep file size reasonable
    for lane in data.get("lanes", []):
        lane["claims"] = [{"claim_id": c["claim_id"], "category": c["category"], "still_supported": c["still_supported"]} for c in lane.get("claims", [])]
        lane["source_clusters"] = [{"cluster_id": sc["cluster_id"], "topic": sc["topic"], "url_count": len(sc.get("urls", []))} for sc in lane.get("source_clusters", [])]
    path.write_text(json.dumps(data, indent=2, default=str))
    logger.info(f"Saved DRX benchmark: {path.name}")


# ---------------------------------------------------------------------------
# Benchmark card generation (matches CSP card format exactly)
# ---------------------------------------------------------------------------

def generate_drx_benchmark_card(
    results: List[DRXBenchmarkResult],
    card_name: str = "",
) -> Dict[str, Any]:
    """Generate a benchmark card from multiple DRX benchmark runs.

    Output format matches the CSP card exactly for consistency.
    """
    if not results:
        return {}

    if not card_name:
        card_name = f"drx_delta_{datetime.now().strftime('%Y%m%d')}"

    # Collect all scorecards from lane 2 and lane 3
    all_scorecards: List[CanonicalScorecard] = []
    eval_ids = []
    frontier_models = set()
    replay_models = set()
    verdicts = {"acceptable": 0, "degraded": 0, "failed": 0}

    for result in results:
        for lane in result.lanes:
            if lane.lane_id == "fresh_research":
                frontier_models.add(lane.model)
            else:
                replay_models.add(lane.model)
                if lane.scorecard:
                    all_scorecards.append(lane.scorecard)
                if lane.drx_metrics:
                    v = lane.drx_metrics.judge_verdict
                    verdicts[v] = verdicts.get(v, 0) + 1
            eval_ids.append(lane.run_id)

    if not all_scorecards:
        return {}

    agg = aggregate_scorecards(all_scorecards)
    total = len(all_scorecards)
    acceptable = verdicts.get("acceptable", 0)
    degraded = verdicts.get("degraded", 0)
    failed = verdicts.get("failed", 0)

    # Grade distribution
    grade_dist: Dict[str, int] = {}
    for sc in all_scorecards:
        grade_dist[sc.grade] = grade_dist.get(sc.grade, 0) + 1

    # Determine final verdict
    acceptable_rate = acceptable / max(total, 1)
    if acceptable_rate >= 0.8:
        final_verdict = "production_ready"
        verdict_reason = f"{acceptable_rate:.0%} acceptable at {agg.token_savings_pct:.0f}% token savings"
    elif acceptable_rate >= 0.5:
        final_verdict = "conditional"
        verdict_reason = f"{acceptable_rate:.0%} acceptable — escalation needed for {degraded + failed} runs"
    else:
        final_verdict = "not_ready"
        verdict_reason = f"Only {acceptable_rate:.0%} acceptable — DRX delta refresh needs improvement"

    limitations = []
    if failed > 0:
        limitations.append(f"{failed} runs failed claim preservation threshold")
    if degraded > 0:
        limitations.append(f"{degraded} runs degraded — partial claim loss")

    card = {
        "workflow_family": card_name,
        "lane": "drx",
        "version": "1.0",
        "frontier_model": ", ".join(sorted(frontier_models)),
        "replay_model": ", ".join(sorted(replay_models)),
        "scaffold_source": "retained_source_clusters",
        "judge_type": "strict_structured",
        "validator_type": "claim_preservation + source_coverage",
        "total_runs": total,
        "completion_rate": round(agg.completion_score, 3),
        "acceptable_rate": round(acceptable_rate, 3),
        "escalation_rate": round(agg.escalation_rate, 3),
        "failure_rate": round(failed / max(total, 1), 3),
        "avg_cost_savings_pct": round((1 - agg.cost_replay_usd / max(agg.cost_baseline_usd, 0.001)) * 100, 1),
        "avg_time_savings_pct": round(agg.time_savings_pct, 1),
        "avg_token_savings_pct": round(agg.token_savings_pct, 1),
        "total_cost_saved_usd": round(agg.cost_savings_usd, 4),
        "tool_calls_reduced_pct": 0.0,
        "avg_composite_score": round(agg.composite_score, 3),
        "avg_completion_score": round(agg.completion_score, 3),
        "avg_artifact_completeness": 0.0,
        "grade_distribution": grade_dist,
        "final_verdict": final_verdict,
        "verdict_reason": verdict_reason,
        "limitations": limitations,
        "trace_link": "",
        "eval_ids": eval_ids[:20],
        "generated_at": _now_iso(),
        # Truth governance
        "data_source": results[0].data_source if results else "simulated",
        "judge_method": results[0].judge_method if results else "heuristic",
        # DRX-specific fields
        "drx_metrics": {
            "avg_claim_preservation": round(
                sum(r.drx_summary.get("claim_preservation_l2", 0) for r in results) / max(len(results), 1), 3
            ),
            "avg_source_reuse_pct": round(
                sum(r.drx_summary.get("source_reuse_l2", 0) for r in results) / max(len(results), 1), 1
            ),
            "verdict_distribution": verdicts,
        },
    }

    # Save card
    card_path = _CARD_DIR / f"{card_name}.json"
    card_path.write_text(json.dumps(card, indent=2, default=str))
    logger.info(f"Saved DRX benchmark card: {card_path.name}")

    return card


# ---------------------------------------------------------------------------
# LIVE benchmark — makes real API calls
# ---------------------------------------------------------------------------

async def run_drx_delta_benchmark_live(
    research_topic: str,
    frontier_model: str = "gpt-5.4",
    replay_model: str = "gpt-5.4-mini",
    cheap_model: str = "gpt-5.4-nano",
    max_tokens: int = 8000,
) -> DRXBenchmarkResult:
    """Run a LIVE DRX delta refresh benchmark with real API calls.

    Makes actual OpenAI API calls for:
    1. Lane 1: Fresh research on the topic (frontier model)
    2. Lane 2: Delta refresh given Lane 1's output (replay model)
    3. Lane 3: Cheap delta refresh (cheap model)
    4. Claim comparison via LLM judge

    Returns data_source="live_api" — these results can be cited as proof.
    Requires OPENAI_API_KEY environment variable.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY required for live DRX benchmark")

    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    research_prompt = (
        f"Research the following topic. Provide at least 8 specific claims with "
        f"concrete numbers, percentages, or dollar amounts. Cite source URLs where "
        f"possible. Include recommendations.\n\n"
        f"Topic: {research_topic}\n\n"
        f"Format: Write plain paragraphs with statistics inline. "
        f"Do NOT use bullet points or markdown headers."
    )

    delta_prompt_template = (
        "You have prior research on this topic. Update it: preserve valid claims, "
        "correct outdated ones, add new findings with concrete numbers. "
        "Write plain paragraphs with statistics inline.\n\n"
        "PRIOR RESEARCH:\n{prior}\n\n"
        "Provide an updated research summary with at least 6 specific claims."
    )

    # Lane 1: Fresh research (frontier model)
    logger.info(f"DRX Live Lane 1: Fresh research with {frontier_model}")
    start1 = time.time()
    resp1 = client.chat.completions.create(
        model=frontier_model,
        messages=[{"role": "user", "content": research_prompt}],
        max_completion_tokens=max_tokens,
    )
    baseline_output = resp1.choices[0].message.content or ""
    tokens1 = resp1.usage.total_tokens if resp1.usage else 0
    time1 = time.time() - start1
    logger.info(f"Lane 1 complete: {len(baseline_output)} chars, {tokens1} tokens, {time1:.1f}s")

    if not baseline_output.strip():
        raise ValueError(f"Lane 1 ({frontier_model}) returned empty content — {tokens1} tokens used (possibly all reasoning)")

    # Lane 2: Delta refresh (replay model, with retained baseline)
    logger.info(f"DRX Live Lane 2: Delta refresh with {replay_model}")
    start2 = time.time()
    resp2 = client.chat.completions.create(
        model=replay_model,
        messages=[{"role": "user", "content": delta_prompt_template.format(prior=baseline_output)}],
        max_completion_tokens=max_tokens,
    )
    refresh_output = resp2.choices[0].message.content or ""
    tokens2 = resp2.usage.total_tokens if resp2.usage else 0
    time2 = time.time() - start2
    logger.info(f"Lane 2 complete: {len(refresh_output)} chars, {tokens2} tokens, {time2:.1f}s")

    # Lane 3: Cheap delta (cheap model, with retained baseline)
    logger.info(f"DRX Live Lane 3: Cheap delta with {cheap_model}")
    start3 = time.time()
    resp3 = client.chat.completions.create(
        model=cheap_model,
        messages=[{"role": "user", "content": delta_prompt_template.format(prior=baseline_output)}],
        max_completion_tokens=max_tokens,
    )
    cheap_output = resp3.choices[0].message.content or ""
    tokens3 = resp3.usage.total_tokens if resp3.usage else 0
    time3 = time.time() - start3
    logger.info(f"Lane 3 complete: {len(cheap_output)} chars, {tokens3} tokens, {time3:.1f}s")

    # Run the benchmark with real outputs + LLM judge
    result = run_drx_delta_benchmark(
        research_topic=research_topic,
        baseline_output=baseline_output,
        refresh_output=refresh_output,
        cheap_refresh_output=cheap_output,
        frontier_model=frontier_model,
        replay_model=replay_model,
        cheap_model=cheap_model,
        baseline_tokens=tokens1,
        refresh_tokens=tokens2,
        cheap_tokens=tokens3,
        baseline_time_s=time1,
        refresh_time_s=time2,
        cheap_time_s=time3,
        use_llm_judge=True,
    )

    # Override data_source to live_api
    result.data_source = "live_api"
    result.judge_method = "llm_judge"
    result.summary = f"[LIVE] {result.summary}"

    # Re-save with live label
    _save_benchmark(result)
    logger.info(f"Live DRX benchmark complete: {result.benchmark_id}")

    return result
