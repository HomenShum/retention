"""
Governed Self-Improvement Loop.

Implements the closed-loop adaptation cycle that the strategy docs call
"agent gradient descent": attempt → evidence → judge → compare → propose
improvement → rerun → keep if better → revert if not.

The agent may optimize tactics, but NOT redefine governance.

Allowed to evolve:
  - prompts, routing, tool selection, retry strategy
  - memory compaction, task decomposition, evidence gathering

NOT allowed to evolve:
  - permissions, safety boundaries, data access scopes
  - production write authority, evaluation criteria without review
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ImprovementDomain(str, Enum):
    """What the improvement targets — bounded to safe domains."""
    PROMPT = "prompt"
    TOOL_SELECTION = "tool_selection"
    RETRY_STRATEGY = "retry_strategy"
    ROUTING = "routing"
    MEMORY_COMPACTION = "memory_compaction"
    TASK_DECOMPOSITION = "task_decomposition"
    EVIDENCE_GATHERING = "evidence_gathering"


class ImprovementStatus(str, Enum):
    PROPOSED = "proposed"
    TESTING = "testing"
    ACCEPTED = "accepted"
    REVERTED = "reverted"
    REJECTED = "rejected"


class FailureCluster(BaseModel):
    """Aggregated failure pattern across multiple runs."""
    cluster_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    failure_type: str
    pattern: str  # Common root cause description
    occurrence_count: int = 1
    affected_tasks: List[str] = Field(default_factory=list)
    first_seen: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_seen: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    estimated_cost_impact: float = 0.0  # Total USD lost to this failure


class ImprovementProposal(BaseModel):
    """A bounded, testable change proposed by the improvement loop."""
    proposal_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    domain: ImprovementDomain
    description: str  # What the change does
    rationale: str  # Why — tied to failure cluster or metric
    cluster_id: Optional[str] = None  # Which failure cluster triggered this

    # The actual change (e.g., a prompt delta, a config override)
    change_payload: Dict[str, Any] = Field(default_factory=dict)

    # Benchmark results
    baseline_score: Optional[float] = None
    improved_score: Optional[float] = None
    delta: Optional[float] = None

    status: ImprovementStatus = ImprovementStatus.PROPOSED
    decision_reason: str = ""


class ImprovementCycle(BaseModel):
    """One full attempt → judge → improve → rerun → decide cycle."""
    cycle_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    started_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None

    # Baseline metrics from the attempt
    baseline_metrics: Dict[str, float] = Field(default_factory=dict)

    # Failure clusters identified
    failure_clusters: List[FailureCluster] = Field(default_factory=list)

    # Proposals generated and tested
    proposals: List[ImprovementProposal] = Field(default_factory=list)

    # Accepted improvements
    accepted_count: int = 0
    reverted_count: int = 0
    net_improvement: float = 0.0  # Aggregate delta across accepted proposals


# ---------------------------------------------------------------------------
# Governed Improvement Engine
# ---------------------------------------------------------------------------

class GovernedImprovementEngine:
    """
    Closes the self-improvement loop with safety rails.

    Flow:
      1. analyze_failures() — cluster failures from recent runs
      2. propose_improvements() — generate bounded proposals per cluster
      3. test_proposal() — run benchmark with the proposed change
      4. decide() — accept if metrics improved, revert if not
      5. persist() — save accepted improvements to learning store

    Safety: proposals are restricted to ImprovementDomain enum.
    The engine cannot modify permissions, safety boundaries, or
    evaluation criteria.
    """

    STORE_PATH = "data/improvement_cycles.json"

    # Minimum improvement delta to accept a proposal
    ACCEPTANCE_THRESHOLD = 0.02  # 2% improvement required

    # Maximum proposals per cycle (prevent runaway optimization)
    MAX_PROPOSALS_PER_CYCLE = 3

    def __init__(self, store_path: Optional[str] = None):
        self.store_path = store_path or self.STORE_PATH
        self._cycles: List[Dict[str, Any]] = []
        self._load()

    def _get_full_path(self) -> Path:
        current = Path(__file__).parent
        while current.name != "backend" and current.parent != current:
            current = current.parent
        return current / self.store_path

    def _load(self):
        path = self._get_full_path()
        try:
            if path.exists():
                with open(path, "r") as f:
                    self._cycles = json.load(f)
                logger.info(f"Loaded {len(self._cycles)} improvement cycles")
        except Exception as e:
            logger.warning(f"Could not load improvement cycles: {e}")

    def _save(self):
        path = self._get_full_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                json.dump(self._cycles, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Could not save improvement cycles: {e}")

    # ----- Step 1: Cluster failures -----

    def analyze_failures(
        self,
        run_results: List[Dict[str, Any]],
    ) -> List[FailureCluster]:
        """
        Cluster failures from a batch of run results.

        Each run_result should have:
          - task_id: str
          - status: "pass" | "fail" | "blocked"
          - verdict_label: str
          - verdict_reason: str
          - cost_usd: float
        """
        # Group by (verdict_label, rough cause pattern)
        clusters: Dict[str, FailureCluster] = {}

        for run in run_results:
            if run.get("status") == "pass":
                continue

            key = run.get("verdict_label", "unknown")
            reason = run.get("verdict_reason", "")

            if key not in clusters:
                clusters[key] = FailureCluster(
                    failure_type=key,
                    pattern=reason,
                )

            cluster = clusters[key]
            cluster.occurrence_count += 1
            task_id = run.get("task_id", "")
            if task_id and task_id not in cluster.affected_tasks:
                cluster.affected_tasks.append(task_id)
            cluster.estimated_cost_impact += run.get("cost_usd", 0.0)
            cluster.last_seen = datetime.now(timezone.utc).isoformat()

        # Sort by cost impact descending — fix the most expensive first
        result = sorted(
            clusters.values(),
            key=lambda c: c.estimated_cost_impact,
            reverse=True,
        )
        logger.info(
            f"Analyzed {len(run_results)} runs → {len(result)} failure clusters"
        )
        return result

    # ----- Step 2: Propose improvements -----

    def propose_improvements(
        self,
        clusters: List[FailureCluster],
        max_proposals: int = None,
    ) -> List[ImprovementProposal]:
        """
        Generate bounded improvement proposals for the top failure clusters.

        Each proposal targets exactly one ImprovementDomain and includes
        a testable change_payload.
        """
        max_proposals = max_proposals or self.MAX_PROPOSALS_PER_CYCLE
        proposals: List[ImprovementProposal] = []

        # Map failure types to improvement domains
        domain_map = {
            "infra-failure": ImprovementDomain.RETRY_STRATEGY,
            "timeout": ImprovementDomain.RETRY_STRATEGY,
            "wrong-output": ImprovementDomain.PROMPT,
            "bug-found-flaky": ImprovementDomain.RETRY_STRATEGY,
            "flakiness-detected": ImprovementDomain.EVIDENCE_GATHERING,
            "needs-human-review": ImprovementDomain.EVIDENCE_GATHERING,
        }

        for cluster in clusters[:max_proposals]:
            domain = domain_map.get(
                cluster.failure_type, ImprovementDomain.PROMPT
            )

            proposal = ImprovementProposal(
                domain=domain,
                description=f"Address {cluster.failure_type} failures "
                            f"({cluster.occurrence_count} occurrences, "
                            f"${cluster.estimated_cost_impact:.2f} cost impact)",
                rationale=f"Cluster pattern: {cluster.pattern}",
                cluster_id=cluster.cluster_id,
                change_payload=self._generate_change(domain, cluster),
            )
            proposals.append(proposal)

        logger.info(f"Generated {len(proposals)} improvement proposals")
        return proposals

    def _generate_change(
        self,
        domain: ImprovementDomain,
        cluster: FailureCluster,
    ) -> Dict[str, Any]:
        """Generate a concrete, bounded change payload."""
        if domain == ImprovementDomain.RETRY_STRATEGY:
            return {
                "type": "retry_config",
                "max_retries": 3,
                "backoff_seconds": [2, 5, 10],
                "failure_type_filter": cluster.failure_type,
            }
        elif domain == ImprovementDomain.EVIDENCE_GATHERING:
            return {
                "type": "evidence_config",
                "capture_ui_tree": True,
                "capture_network_log": True,
                "screenshot_on_failure": True,
            }
        elif domain == ImprovementDomain.PROMPT:
            return {
                "type": "prompt_addendum",
                "addendum": f"IMPORTANT: Previous runs failed with "
                           f"'{cluster.failure_type}' ({cluster.pattern}). "
                           f"Verify expected state before proceeding.",
            }
        else:
            return {"type": "noop", "note": "No automated change available"}

    # ----- Step 3: Test a proposal -----

    def record_test_result(
        self,
        proposal: ImprovementProposal,
        baseline_score: float,
        improved_score: float,
    ) -> ImprovementProposal:
        """Record the A/B benchmark result for a proposal."""
        proposal.baseline_score = baseline_score
        proposal.improved_score = improved_score
        proposal.delta = round(improved_score - baseline_score, 6)
        proposal.status = ImprovementStatus.TESTING
        return proposal

    # ----- Step 4: Decide -----

    def decide(self, proposal: ImprovementProposal) -> ImprovementProposal:
        """
        Accept or revert the proposal based on metrics.

        Rule: accept only if delta >= ACCEPTANCE_THRESHOLD.
        This prevents noise-driven changes.
        """
        if proposal.delta is None:
            proposal.status = ImprovementStatus.REJECTED
            proposal.decision_reason = "No test results available"
            return proposal

        if proposal.delta >= self.ACCEPTANCE_THRESHOLD:
            proposal.status = ImprovementStatus.ACCEPTED
            proposal.decision_reason = (
                f"Improvement of {proposal.delta:+.4f} exceeds "
                f"threshold {self.ACCEPTANCE_THRESHOLD}"
            )
            logger.info(
                f"ACCEPTED proposal {proposal.proposal_id}: "
                f"{proposal.delta:+.4f} ({proposal.domain.value})"
            )
        else:
            proposal.status = ImprovementStatus.REVERTED
            proposal.decision_reason = (
                f"Delta {proposal.delta:+.4f} below threshold "
                f"{self.ACCEPTANCE_THRESHOLD} — reverting"
            )
            logger.info(
                f"REVERTED proposal {proposal.proposal_id}: "
                f"{proposal.delta:+.4f} ({proposal.domain.value})"
            )

        return proposal

    # ----- Step 5: Run full cycle -----

    def run_cycle(
        self,
        run_results: List[Dict[str, Any]],
        baseline_score: float,
    ) -> ImprovementCycle:
        """
        Run one complete governed improvement cycle.

        1. Cluster failures from run_results
        2. Propose bounded improvements for top clusters
        3. (Caller runs benchmarks with proposals applied)
        4. Record proposals for future testing

        The caller is responsible for actually running the A/B benchmark
        and calling record_test_result() + decide() for each proposal.
        This keeps the engine pure and testable.
        """
        cycle = ImprovementCycle(
            baseline_metrics={
                "success_rate": baseline_score,
                "total_runs": len(run_results),
                "failed_runs": sum(
                    1 for r in run_results if r.get("status") != "pass"
                ),
            }
        )

        # Step 1: Cluster
        clusters = self.analyze_failures(run_results)
        cycle.failure_clusters = clusters

        # Step 2: Propose
        proposals = self.propose_improvements(clusters)
        cycle.proposals = proposals

        # Persist cycle (proposals are in PROPOSED status)
        self._cycles.append(cycle.model_dump())
        self._save()

        logger.info(
            f"Improvement cycle {cycle.cycle_id}: "
            f"{len(clusters)} clusters → {len(proposals)} proposals"
        )
        return cycle

    def complete_cycle(self, cycle: ImprovementCycle) -> ImprovementCycle:
        """Finalize a cycle after all proposals have been tested and decided."""
        cycle.completed_at = datetime.now(timezone.utc).isoformat()
        cycle.accepted_count = sum(
            1 for p in cycle.proposals
            if p.status == ImprovementStatus.ACCEPTED
        )
        cycle.reverted_count = sum(
            1 for p in cycle.proposals
            if p.status == ImprovementStatus.REVERTED
        )
        cycle.net_improvement = sum(
            p.delta or 0.0 for p in cycle.proposals
            if p.status == ImprovementStatus.ACCEPTED
        )

        # Update persisted cycle
        for i, c in enumerate(self._cycles):
            if c.get("cycle_id") == cycle.cycle_id:
                self._cycles[i] = cycle.model_dump()
                break
        self._save()

        logger.info(
            f"Cycle {cycle.cycle_id} complete: "
            f"{cycle.accepted_count} accepted, "
            f"{cycle.reverted_count} reverted, "
            f"net improvement {cycle.net_improvement:+.4f}"
        )
        return cycle

    # ----- Utility -----

    def get_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return recent improvement cycles."""
        return self._cycles[-limit:]

    def get_accepted_improvements(self) -> List[Dict[str, Any]]:
        """Return all accepted proposals across cycles for applying to agents."""
        accepted = []
        for cycle_data in self._cycles:
            for proposal_data in cycle_data.get("proposals", []):
                if proposal_data.get("status") == "accepted":
                    accepted.append(proposal_data)
        return accepted
