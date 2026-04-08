"""
suggest_next() — Retained Operation Pattern prefix-match engine (RET-12).

Core product: instead of the LLM spending 500 tokens figuring out "I should
call read_file next," it asks Retention and gets told instantly.

How it works:
  1. Accept the current prefix of actions (what the agent has done so far)
  2. Prefix-match against stored successful trajectories / ROPs
  3. Return the most likely next action with confidence + rationale
  4. If confidence is below threshold → return None (agent reasons on its own)

Integrates with:
  - exploration_memory.py — cached crawl/workflow/test paths
  - trajectory_replay.py — stored step-by-step trajectories
  - divergence_analyzer.py — health grades inform confidence
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_TRAJECTORY_DIR = _DATA_DIR / "trajectories"
_ROP_LOG_DIR = _DATA_DIR / "rop_suggestions"
_ROP_LOG_DIR.mkdir(parents=True, exist_ok=True)

# Default confidence threshold — below this, we don't suggest
DEFAULT_MIN_CONFIDENCE = 0.65


# ─── Data types ──────────────────────────────────────────────────────────

@dataclass
class ActionPrefix:
    """A sequence of actions the agent has taken so far."""
    actions: list[str]
    context: dict[str, Any] = field(default_factory=dict)
    # Optional: current screen fingerprint, current directory, current URL
    screen_fingerprint: str = ""
    current_directory: str = ""
    current_url: str = ""
    rop_family: str = ""  # e.g. "DRX", "CSP", "" for auto-detect


@dataclass
class Suggestion:
    """A suggested next action from the prefix-match engine."""
    action: str
    confidence: float  # 0.0 - 1.0
    pattern_id: str  # trajectory or ROP that sourced this
    branch: str  # which cluster/surface this belongs to
    expected_checkpoint: str  # what we expect after this action
    reason: str  # human-readable rationale
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    # Metadata for savings tracking
    tokens_saved_estimate: int = 0
    source_trajectory_health: str = ""  # A/B/C/D/F


@dataclass
class SuggestionLog:
    """Record of a suggestion for savings tracking."""
    timestamp: str
    prefix_length: int
    suggested_action: str
    confidence: float
    pattern_id: str
    was_followed: bool = False  # updated later
    tokens_saved: int = 0


# ─── Prefix matching engine ─────────────────────────────────────────────

def _normalize_action(action: str) -> str:
    """Normalize an action string for fuzzy matching.

    Strips variable parts (UUIDs, timestamps, specific values) to find
    structural matches between trajectories.
    """
    import re
    # Remove UUIDs
    s = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '<UUID>', action)
    # Remove hex hashes
    s = re.sub(r'[0-9a-f]{16,}', '<HASH>', s)
    # Remove specific file paths but keep structure
    s = re.sub(r'/[^\s]+/([^\s/]+)', r'/.../<FILE>', s)
    # Normalize whitespace
    s = re.sub(r'\s+', ' ', s).strip()
    return s.lower()


def _action_similarity(a: str, b: str) -> float:
    """Compute similarity between two normalized action strings.

    Uses token overlap (Jaccard) for speed.
    """
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _prefix_match_score(
    prefix: list[str], trajectory_actions: list[str]
) -> tuple[float, int]:
    """Score how well a prefix matches the start of a trajectory.

    Returns (score, match_length) where:
      - score: 0.0-1.0 weighted similarity across matched steps
      - match_length: how many prefix steps matched
    """
    if not prefix or not trajectory_actions:
        return 0.0, 0

    normalized_prefix = [_normalize_action(a) for a in prefix]
    normalized_traj = [_normalize_action(a) for a in trajectory_actions]

    matched = 0
    total_sim = 0.0

    for i, pa in enumerate(normalized_prefix):
        if i >= len(normalized_traj):
            break
        sim = _action_similarity(pa, normalized_traj[i])
        if sim >= 0.3:  # minimum threshold for "same step"
            matched += 1
            total_sim += sim
        else:
            # Allow one skip (agent may have done an extra step)
            if i + 1 < len(normalized_traj):
                skip_sim = _action_similarity(pa, normalized_traj[i + 1])
                if skip_sim >= 0.3:
                    matched += 1
                    total_sim += skip_sim * 0.8  # penalty for skip
                    continue
            break  # diverged

    if matched == 0:
        return 0.0, 0

    # Score = average similarity * coverage ratio
    avg_sim = total_sim / matched
    coverage = matched / len(normalized_prefix)
    return avg_sim * coverage, matched


def _load_all_trajectories() -> list[dict[str, Any]]:
    """Load all stored trajectories from disk."""
    trajectories = []
    if not _TRAJECTORY_DIR.exists():
        return trajectories

    for task_dir in _TRAJECTORY_DIR.iterdir():
        if not task_dir.is_dir():
            continue
        for f in task_dir.glob("*.json"):
            try:
                t = json.loads(f.read_text())
                t["_task_dir"] = task_dir.name
                trajectories.append(t)
            except (json.JSONDecodeError, OSError):
                continue

    return trajectories


def _get_trajectory_health(trajectory_id: str) -> dict[str, Any]:
    """Get health grade and confidence from divergence analyzer."""
    try:
        from ...services.divergence_analyzer import analyze_divergence
        analysis = analyze_divergence()
        for t in analysis.get("trajectories", []):
            if t.get("trajectory_id") == trajectory_id:
                return t
    except Exception:
        pass
    return {}


# ─── Core API ────────────────────────────────────────────────────────────

def suggest_next(
    prefix: ActionPrefix,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    max_alternatives: int = 3,
) -> Optional[Suggestion]:
    """Suggest the next action based on prefix-matching stored trajectories.

    This is the core retention product function. Instead of the LLM spending
    tokens reasoning about what to do next, it asks here and gets an instant
    answer (or None if confidence is too low).

    Args:
        prefix: Current sequence of agent actions + context
        min_confidence: Don't suggest below this confidence (default 0.65)
        max_alternatives: Max number of alternative suggestions

    Returns:
        Suggestion with action, confidence, rationale — or None
    """
    if not prefix.actions:
        return None

    start = time.time()

    # ── Progressive Disclosure: Layer 0 routing via manifests ──────────
    # Load manifest skill cards to refine matching — if an ROP family
    # matches the current prefix context, boost confidence and use
    # Layer 3 action policy for min_confidence tuning.
    _manifest_boost = 0.0
    _manifest_id = ""
    try:
        from .rop_manifest import ROPRegistry
        _registry = ROPRegistry()
        _cards = _registry.list_cards()
        for card in _cards:
            # Layer 0: trigger matching against prefix context
            for trigger in card.get("triggers", []):
                if any(trigger.lower() in a.lower() for a in prefix.actions[-3:]):
                    _manifest_boost = 0.08
                    _manifest_id = card["id"]
                    # Layer 3: use action policy min_confidence if available
                    manifest = _registry.get(card["id"])
                    if manifest:
                        policy = manifest.action_policy()
                        policy_conf = policy.get("min_confidence")
                        if policy_conf and isinstance(policy_conf, (int, float)):
                            min_confidence = max(min_confidence, policy_conf)
                    break
            if _manifest_id:
                break
    except Exception:
        pass  # Manifests not available — proceed without

    trajectories = _load_all_trajectories()

    if not trajectories:
        logger.debug("suggest_next: no stored trajectories, returning None")
        return None

    # ── Step 1: Score all trajectories against current prefix ─────────
    candidates: list[tuple[float, int, dict, str]] = []

    for traj in trajectories:
        steps = traj.get("steps", [])
        if not steps:
            continue

        # Extract action strings from trajectory steps
        traj_actions = [s.get("action", "") for s in steps]
        score, match_len = _prefix_match_score(prefix.actions, traj_actions)

        if score < 0.2 or match_len == 0:
            continue

        # Boost score based on trajectory health
        traj_id = traj.get("trajectory_id", "")
        health = _get_trajectory_health(traj_id)
        health_boost = 0.0
        if health:
            grade = health.get("health_grade", "C")
            health_boost = {"A": 0.15, "B": 0.10, "C": 0.0, "D": -0.10, "F": -0.20}.get(grade, 0.0)

        # Boost if same ROP family
        family_boost = 0.0
        if prefix.rop_family and traj.get("workflow_family", "") == prefix.rop_family:
            family_boost = 0.1

        # Boost if same surface (web/mobile)
        surface_boost = 0.0
        if prefix.context.get("surface") and traj.get("surface") == prefix.context.get("surface"):
            surface_boost = 0.05

        # Boost if matched a manifest via progressive disclosure L0
        manifest_boost = _manifest_boost if _manifest_id else 0.0

        final_score = min(1.0, score + health_boost + family_boost + surface_boost + manifest_boost)

        # The next action is the step AFTER the matched prefix
        next_idx = match_len
        if next_idx < len(steps):
            next_action = steps[next_idx].get("action", "")
            next_checkpoint = steps[next_idx].get("screen_fingerprint_after", "")
            candidates.append((final_score, next_idx, traj, next_action))

    if not candidates:
        logger.debug("suggest_next: no matching trajectories found")
        return None

    # ── Step 2: Rank and pick best candidate ─────────────────────────
    candidates.sort(key=lambda c: c[0], reverse=True)
    best_score, best_idx, best_traj, best_action = candidates[0]

    if best_score < min_confidence:
        logger.debug(
            f"suggest_next: best score {best_score:.2f} below threshold {min_confidence}"
        )
        return None

    # Build alternatives
    alternatives = []
    for score, idx, traj, action in candidates[1:max_alternatives + 1]:
        alternatives.append({
            "action": action,
            "confidence": round(score, 3),
            "pattern_id": traj.get("trajectory_id", ""),
            "workflow": traj.get("task_name", traj.get("_task_dir", "")),
        })

    # Health info
    health = _get_trajectory_health(best_traj.get("trajectory_id", ""))
    health_grade = health.get("health_grade", "?") if health else "?"

    # Build the next checkpoint expectation
    steps = best_traj.get("steps", [])
    expected_checkpoint = ""
    if best_idx < len(steps):
        expected_checkpoint = steps[best_idx].get("screen_fingerprint_after", "")

    # Estimate tokens saved: ~500 reasoning tokens per suggestion followed
    tokens_saved = 500

    # Build reason
    match_count = len(prefix.actions)
    total_traj_steps = len(steps)
    traj_name = best_traj.get("task_name", best_traj.get("_task_dir", "unknown"))
    reason = (
        f"Matched {match_count}/{total_traj_steps} steps from trajectory '{traj_name}' "
        f"(health: {health_grade}, confidence: {best_score:.2f}). "
        f"In {health.get('total_replays', '?')} prior runs, this next step was consistent."
    )

    elapsed_ms = int((time.time() - start) * 1000)
    logger.info(
        f"suggest_next: matched in {elapsed_ms}ms, "
        f"confidence={best_score:.2f}, action='{best_action[:60]}'"
    )

    suggestion = Suggestion(
        action=best_action,
        confidence=round(best_score, 3),
        pattern_id=best_traj.get("trajectory_id", ""),
        branch=best_traj.get("workflow_family", best_traj.get("surface", "")),
        expected_checkpoint=expected_checkpoint,
        reason=reason,
        alternatives=alternatives,
        tokens_saved_estimate=tokens_saved,
        source_trajectory_health=health_grade,
    )

    # Log the suggestion for savings tracking
    _log_suggestion(suggestion, prefix)

    return suggestion


def suggest_next_for_rop(
    prefix: ActionPrefix,
    rop_type: str,
    directory_clusters: Optional[list[str]] = None,
    url_clusters: Optional[list[str]] = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> Optional[Suggestion]:
    """Suggest next action specifically for an ROP pattern.

    Enhanced version that uses ROP-specific matching:
    - DRX: matches against research trajectories, boosts URL cluster relevance
    - CSP: matches against change propagation trajectories, walks dependency paths

    Args:
        prefix: Current action sequence
        rop_type: "DRX", "CSP", "WRV", etc.
        directory_clusters: Relevant directory groups for CSP
        url_clusters: Relevant URL groups for DRX
        min_confidence: Threshold

    Returns:
        Suggestion or None
    """
    # Enrich the prefix with ROP context
    prefix.rop_family = rop_type
    if directory_clusters:
        prefix.context["directory_clusters"] = directory_clusters
    if url_clusters:
        prefix.context["url_clusters"] = url_clusters

    suggestion = suggest_next(prefix, min_confidence=min_confidence)

    if suggestion and rop_type == "CSP":
        # For CSP, also check if we've covered all required layers
        covered_layers = _detect_covered_layers(prefix.actions)
        missing = _expected_csp_layers() - covered_layers
        if missing:
            suggestion.reason += f" Uncovered layers: {', '.join(sorted(missing))}."

    return suggestion


# ─── ROP helpers ─────────────────────────────────────────────────────────

def _detect_covered_layers(actions: list[str]) -> set[str]:
    """Detect which cross-stack layers the agent has already touched."""
    layers = set()
    layer_keywords = {
        "frontend": ["react", "tsx", "jsx", "component", "css", "ui", "frontend"],
        "backend": ["api", "route", "endpoint", "fastapi", "backend", "server"],
        "prompts": ["prompt", "system_prompt", "instruction", "template"],
        "tools": ["tool", "mcp", "function_call", "tool_call"],
        "schema": ["schema", "model", "dataclass", "pydantic", "type"],
        "jobs": ["job", "task", "worker", "queue", "celery", "sandcastle"],
        "database": ["database", "db", "migration", "sql", "table", "column"],
        "tests": ["test", "spec", "pytest", "playwright", "e2e"],
    }
    for action in actions:
        action_lower = action.lower()
        for layer, keywords in layer_keywords.items():
            if any(kw in action_lower for kw in keywords):
                layers.add(layer)
    return layers


def _expected_csp_layers() -> set[str]:
    """Default expected layers for cross-stack change propagation."""
    return {"frontend", "backend", "schema", "tests"}


# ─── Divergence guard (RET-13) ───────────────────────────────────────────

def check_divergence(
    prefix: ActionPrefix,
    last_suggestion: Optional[Suggestion] = None,
) -> dict[str, Any]:
    """Check if the agent has diverged from the suggested path.

    Called after each action to determine if we should stop suggesting.

    Returns:
        {
            "diverged": bool,
            "severity": "none" | "mild" | "significant" | "critical",
            "reason": str,
            "recommendation": "continue" | "re_explore" | "stop_suggesting" | "human_check"
        }
    """
    if not last_suggestion or not prefix.actions:
        return {
            "diverged": False,
            "severity": "none",
            "reason": "No suggestion to compare against",
            "recommendation": "continue",
        }

    # Compare the last action taken with what was suggested
    actual_last = prefix.actions[-1]
    expected = last_suggestion.action

    norm_actual = _normalize_action(actual_last)
    norm_expected = _normalize_action(expected)
    similarity = _action_similarity(norm_actual, norm_expected)

    if similarity >= 0.6:
        return {
            "diverged": False,
            "severity": "none",
            "reason": f"Action matches suggestion (similarity: {similarity:.2f})",
            "recommendation": "continue",
        }

    if similarity >= 0.3:
        return {
            "diverged": True,
            "severity": "mild",
            "reason": (
                f"Action partially matches (similarity: {similarity:.2f}). "
                f"Expected: '{expected[:60]}', Got: '{actual_last[:60]}'"
            ),
            "recommendation": "continue",  # mild divergence is OK
        }

    # Significant divergence
    # Check if this is a pattern of divergence (multiple consecutive misses)
    recent_actions = prefix.actions[-3:] if len(prefix.actions) >= 3 else prefix.actions
    trajectories = _load_all_trajectories()
    still_on_some_path = False

    for traj in trajectories:
        traj_actions = [s.get("action", "") for s in traj.get("steps", [])]
        score, _ = _prefix_match_score(recent_actions, traj_actions)
        if score >= 0.3:
            still_on_some_path = True
            break

    if still_on_some_path:
        return {
            "diverged": True,
            "severity": "significant",
            "reason": (
                f"Diverged from suggestion but still on a known path. "
                f"Expected: '{expected[:60]}', Got: '{actual_last[:60]}'"
            ),
            "recommendation": "re_explore",  # re-match against trajectories
        }

    return {
        "diverged": True,
        "severity": "critical",
        "reason": (
            f"Agent is off all known paths. "
            f"Expected: '{expected[:60]}', Got: '{actual_last[:60]}'. "
            f"No stored trajectory matches recent actions."
        ),
        "recommendation": "stop_suggesting",
    }


# ─── Logging & savings tracking ─────────────────────────────────────────

def _log_suggestion(suggestion: Suggestion, prefix: ActionPrefix) -> None:
    """Log a suggestion for later savings analysis."""
    from datetime import datetime, timezone

    log_entry = SuggestionLog(
        timestamp=datetime.now(timezone.utc).isoformat(),
        prefix_length=len(prefix.actions),
        suggested_action=suggestion.action[:200],
        confidence=suggestion.confidence,
        pattern_id=suggestion.pattern_id,
    )

    log_file = _ROP_LOG_DIR / "suggestions.jsonl"
    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(asdict(log_entry)) + "\n")
    except OSError:
        pass


def mark_suggestion_followed(
    pattern_id: str, was_followed: bool, tokens_saved: int = 500
) -> None:
    """Mark whether a suggestion was actually followed by the agent.

    Called by the pipeline after the agent takes its next action.
    Updates the log for accurate savings reporting.
    """
    log_file = _ROP_LOG_DIR / "suggestions.jsonl"
    if not log_file.exists():
        return

    # Update the last entry matching this pattern_id
    lines = log_file.read_text().strip().split("\n")
    updated = False
    for i in range(len(lines) - 1, -1, -1):
        try:
            entry = json.loads(lines[i])
            if entry.get("pattern_id") == pattern_id and not entry.get("was_followed"):
                entry["was_followed"] = was_followed
                entry["tokens_saved"] = tokens_saved if was_followed else 0
                lines[i] = json.dumps(entry)
                updated = True
                break
        except (json.JSONDecodeError, KeyError):
            continue

    if updated:
        log_file.write_text("\n".join(lines) + "\n")


def get_suggestion_stats() -> dict[str, Any]:
    """Aggregate suggestion statistics for the savings dashboard."""
    log_file = _ROP_LOG_DIR / "suggestions.jsonl"
    if not log_file.exists():
        return {
            "total_suggestions": 0,
            "followed": 0,
            "follow_rate": 0.0,
            "total_tokens_saved": 0,
            "avg_confidence": 0.0,
            "by_pattern": {},
        }

    entries = []
    for line in log_file.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not entries:
        return {
            "total_suggestions": 0,
            "followed": 0,
            "follow_rate": 0.0,
            "total_tokens_saved": 0,
            "avg_confidence": 0.0,
            "by_pattern": {},
        }

    total = len(entries)
    followed = sum(1 for e in entries if e.get("was_followed"))
    tokens_saved = sum(e.get("tokens_saved", 0) for e in entries)
    avg_conf = sum(e.get("confidence", 0) for e in entries) / total

    # Group by pattern
    by_pattern: dict[str, dict] = defaultdict(lambda: {"count": 0, "followed": 0, "tokens_saved": 0})
    for e in entries:
        pid = e.get("pattern_id", "unknown")
        by_pattern[pid]["count"] += 1
        if e.get("was_followed"):
            by_pattern[pid]["followed"] += 1
            by_pattern[pid]["tokens_saved"] += e.get("tokens_saved", 0)

    return {
        "total_suggestions": total,
        "followed": followed,
        "follow_rate": round(followed / total, 3) if total else 0.0,
        "total_tokens_saved": tokens_saved,
        "avg_confidence": round(avg_conf, 3),
        "by_pattern": dict(by_pattern),
    }
