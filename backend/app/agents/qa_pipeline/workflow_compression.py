"""
Workflow Compression — extracts stable CRUD shortcuts from repeated trajectories.

When the same workflow has been replayed multiple times with consistent paths,
compression identifies the invariant action sequences and produces reusable
"shortcut" objects that skip exploration entirely.

Compression layers:
  1. Step deduplication — merge identical consecutive steps
  2. Stable path extraction — find the longest common subsequence across replays
  3. CRUD shortcut generation — identify create/read/update/delete patterns
  4. Checkpoint pruning — keep only the checkpoints that actually matter for validation

Usage:
    from workflow_compression import compress_workflow, get_compression_stats

    shortcut = compress_workflow("login_flow")
    # Returns a compressed trajectory with fewer steps and stable checkpoints
"""

import hashlib
import json
import logging
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..device_testing.trajectory_logger import TrajectoryLog, TrajectoryStep, get_trajectory_logger

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_COMPRESSION_DIR = _DATA_DIR / "compressed_workflows"
_COMPRESSION_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class CRUDShortcut:
    """A compressed, reusable workflow shortcut."""
    shortcut_id: str
    workflow_family: str
    task_name: str
    crud_type: str  # "create" | "read" | "update" | "delete" | "navigate" | "verify"
    stable_steps: List[Dict[str, Any]]  # minimal action sequence
    checkpoints: List[Dict[str, Any]]  # fingerprints that must match
    source_trajectories: List[str]  # trajectory IDs this was derived from
    confidence: float  # 0.0-1.0 how stable this shortcut is
    avg_steps_original: float  # avg steps in source trajectories
    steps_compressed: int  # steps in this shortcut
    compression_ratio: float  # steps_compressed / avg_steps_original
    created_at: str = ""
    times_used: int = 0


@dataclass
class CompressionResult:
    """Result of compressing a workflow family."""
    workflow_family: str
    task_name: str
    source_count: int  # number of trajectories analyzed
    shortcuts: List[CRUDShortcut]
    total_steps_before: int
    total_steps_after: int
    compression_ratio: float
    stable_path_length: int
    drift_prone_steps: List[int]  # step indices that frequently drift


def _step_fingerprint(step: TrajectoryStep) -> str:
    """Create a semantic fingerprint for a step (action + target, not exact text)."""
    action = step.action.lower().strip()
    # Normalize action text for comparison
    # Remove coordinates, specific values — keep semantic intent
    normalized = action
    for prefix in ["tap on ", "click on ", "type ", "navigate to ", "scroll ", "press ", "wait "]:
        if normalized.startswith(prefix):
            normalized = prefix + normalized[len(prefix):].split("(")[0].split("[")[0].strip()
            break
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


def _classify_crud(steps: List[TrajectoryStep]) -> str:
    """Classify a trajectory's CRUD type from its action sequence."""
    actions_text = " ".join(s.action.lower() for s in steps)

    if any(kw in actions_text for kw in ["create", "add", "new", "register", "sign up", "submit form"]):
        return "create"
    if any(kw in actions_text for kw in ["delete", "remove", "trash", "discard"]):
        return "delete"
    if any(kw in actions_text for kw in ["edit", "update", "modify", "change", "save"]):
        return "update"
    if any(kw in actions_text for kw in ["view", "read", "open", "detail", "inspect", "check"]):
        return "read"
    if any(kw in actions_text for kw in ["login", "navigate", "go to", "open app", "launch"]):
        return "navigate"
    return "verify"


def _find_longest_common_subsequence(
    sequences: List[List[str]],
) -> List[str]:
    """Find the longest common subsequence across multiple step fingerprint sequences."""
    if not sequences:
        return []
    if len(sequences) == 1:
        return sequences[0]

    # Pairwise LCS, then intersect
    def lcs_two(a: List[str], b: List[str]) -> List[str]:
        m, n = len(a), len(b)
        # DP table
        dp = [[0] * (n + 1) for _ in range(m + 1)]
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if a[i - 1] == b[j - 1]:
                    dp[i][j] = dp[i - 1][j - 1] + 1
                else:
                    dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

        # Backtrack
        result = []
        i, j = m, n
        while i > 0 and j > 0:
            if a[i - 1] == b[j - 1]:
                result.append(a[i - 1])
                i -= 1
                j -= 1
            elif dp[i - 1][j] > dp[i][j - 1]:
                i -= 1
            else:
                j -= 1
        return list(reversed(result))

    result = sequences[0]
    for seq in sequences[1:]:
        result = lcs_two(result, seq)
    return result


def _find_drift_prone_steps(trajectories: List[TrajectoryLog]) -> List[int]:
    """Find step indices that frequently drift across replays."""
    drift_counts: Counter = Counter()
    total_counts: Counter = Counter()

    for traj in trajectories:
        for step in traj.steps:
            total_counts[step.step_index] += 1
            if not step.success:
                drift_counts[step.step_index] += 1

    prone = []
    for idx, total in total_counts.items():
        if total >= 2 and drift_counts.get(idx, 0) / total > 0.3:
            prone.append(idx)
    return sorted(prone)


def compress_workflow(task_name: str, min_trajectories: int = 2) -> Optional[CompressionResult]:
    """
    Compress a workflow family by analyzing all its trajectories.

    Args:
        task_name: The workflow/task to compress
        min_trajectories: Minimum number of trajectories needed for compression

    Returns:
        CompressionResult with shortcuts, or None if insufficient data
    """
    tl = get_trajectory_logger()
    trajectories = tl.load_all_for_task(task_name)

    if len(trajectories) < min_trajectories:
        logger.info(f"Not enough trajectories for {task_name}: {len(trajectories)} < {min_trajectories}")
        return None

    # Only use successful trajectories
    successful = [t for t in trajectories if t.success]
    if len(successful) < min_trajectories:
        logger.info(f"Not enough successful trajectories for {task_name}")
        return None

    # Step 1: Create fingerprint sequences for each trajectory
    fingerprint_sequences = []
    for traj in successful:
        fps = [_step_fingerprint(s) for s in traj.steps]
        fingerprint_sequences.append(fps)

    # Step 2: Find the stable path (LCS across all trajectories)
    stable_path = _find_longest_common_subsequence(fingerprint_sequences)

    # Step 3: Extract the stable steps from the most recent trajectory
    reference = successful[-1]  # most recent
    stable_step_map = {}
    for fp in stable_path:
        for step in reference.steps:
            if _step_fingerprint(step) == fp and fp not in stable_step_map:
                stable_step_map[fp] = step
                break

    stable_steps = [stable_step_map[fp] for fp in stable_path if fp in stable_step_map]

    # Step 4: Extract checkpoints (fingerprints at stable steps)
    checkpoints = []
    for step in stable_steps:
        if step.screen_fingerprint_after:
            checkpoints.append({
                "step_index": step.step_index,
                "fingerprint": step.screen_fingerprint_after,
                "action": step.action[:80],
            })

    # Step 5: Classify CRUD type
    crud_type = _classify_crud(stable_steps)

    # Step 6: Compute metrics
    avg_steps = sum(len(t.steps) for t in successful) / len(successful)
    total_steps_before = sum(len(t.steps) for t in successful)
    total_steps_after = len(stable_steps)
    compression_ratio = total_steps_after / max(avg_steps, 1)

    # Step 7: Build shortcut
    shortcut = CRUDShortcut(
        shortcut_id=f"sc_{task_name}_{hashlib.sha256(task_name.encode()).hexdigest()[:8]}",
        workflow_family=reference.workflow_family or task_name,
        task_name=task_name,
        crud_type=crud_type,
        stable_steps=[{
            "action": s.action,
            "semantic_label": s.semantic_label or s.action[:50],
            "mcp_tool_calls": s.mcp_tool_calls,
            "screen_fingerprint_after": s.screen_fingerprint_after,
        } for s in stable_steps],
        checkpoints=checkpoints,
        source_trajectories=[t.trajectory_id for t in successful],
        confidence=min(1.0, len(successful) / 5.0),  # scales up to 5 replays
        avg_steps_original=avg_steps,
        steps_compressed=len(stable_steps),
        compression_ratio=compression_ratio,
        created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    )

    # Step 8: Find drift-prone steps
    drift_prone = _find_drift_prone_steps(successful)

    result = CompressionResult(
        workflow_family=reference.workflow_family or task_name,
        task_name=task_name,
        source_count=len(successful),
        shortcuts=[shortcut],
        total_steps_before=total_steps_before,
        total_steps_after=total_steps_after,
        compression_ratio=compression_ratio,
        stable_path_length=len(stable_path),
        drift_prone_steps=drift_prone,
    )

    # Save compressed workflow
    out_path = _COMPRESSION_DIR / f"{task_name}.json"
    out_path.write_text(json.dumps(asdict(result), indent=2, default=str))
    logger.info(
        f"Compressed {task_name}: {avg_steps:.0f} steps → {len(stable_steps)} steps "
        f"({compression_ratio:.0%} ratio, {len(successful)} source trajectories)"
    )

    return result


def get_compression_stats() -> Dict[str, Any]:
    """Get compression stats across all workflows."""
    stats = {
        "compressed_workflows": 0,
        "total_shortcuts": 0,
        "avg_compression_ratio": 0.0,
        "workflows": [],
    }

    for f in _COMPRESSION_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            stats["compressed_workflows"] += 1
            stats["total_shortcuts"] += len(data.get("shortcuts", []))
            stats["workflows"].append({
                "task_name": data.get("task_name"),
                "compression_ratio": data.get("compression_ratio", 0),
                "source_count": data.get("source_count", 0),
                "shortcuts": len(data.get("shortcuts", [])),
            })
        except Exception:
            continue

    if stats["workflows"]:
        stats["avg_compression_ratio"] = (
            sum(w["compression_ratio"] for w in stats["workflows"]) / len(stats["workflows"])
        )

    return stats


def load_shortcut(task_name: str) -> Optional[CRUDShortcut]:
    """Load a compressed shortcut for a task."""
    path = _COMPRESSION_DIR / f"{task_name}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        shortcuts = data.get("shortcuts", [])
        if shortcuts:
            return CRUDShortcut(**shortcuts[0])
    except Exception as e:
        logger.warning(f"Failed to load shortcut for {task_name}: {e}")
    return None
