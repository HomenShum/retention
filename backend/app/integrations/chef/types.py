"""
Chef Integration Types

Python data models mirroring Chef test-kitchen TypeScript types.
See: integrations/chef/test-kitchen/types.ts
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ChefResult:
    """Result from a Chef test-kitchen run.

    Mirrors TypeScript:
        type ChefResult = {
            success: boolean;
            numDeploys: number;
            usage: LanguageModelUsage;
            files: Record<string, string>;
        };
    """

    success: bool
    num_deploys: int
    usage: Dict[str, int] = field(default_factory=dict)
    files: Dict[str, str] = field(default_factory=dict)
    deploy_url: Optional[str] = None
    output_dir: Optional[str] = None


@dataclass
class ChefBenchmarkResult:
    """Benchmark scoring for a Chef run.

    Scoring logic from chefScorer.ts:
        1/Deploys: success ? 1/max(1, numDeploys) : 0
        isSuccess: success ? 1 : 0
    """

    run_id: str
    score_deploys: float  # 1 / numDeploys
    score_success: float  # 1.0 or 0.0
    total_score: float

    @classmethod
    def from_chef_result(cls, run_id: str, result: ChefResult) -> "ChefBenchmarkResult":
        """Compute benchmark scores from a ChefResult."""
        score_success = 1.0 if result.success else 0.0
        score_deploys = (
            1.0 / max(1, result.num_deploys) if result.success else 0.0
        )
        total_score = (score_deploys + score_success) / 2.0
        return cls(
            run_id=run_id,
            score_deploys=score_deploys,
            score_success=score_success,
            total_score=total_score,
        )


@dataclass
class PromptEnhancement:
    """Enhancements applied to a user prompt for testability/deployability."""

    original_prompt: str
    enhanced_prompt: str
    enhancements_applied: List[str] = field(default_factory=list)


@dataclass
class ChefRunStatus:
    """Status tracking for a Chef run."""

    run_id: str
    status: str  # "pending", "running", "completed", "failed", "error"
    prompt: str
    model: str
    started_at: str
    completed_at: Optional[str] = None
    success: Optional[bool] = None
    num_deploys: Optional[int] = None
    files: Optional[Dict[str, str]] = None
    error: Optional[str] = None
    convex_deploy_url: Optional[str] = None
    vercel_deploy_url: Optional[str] = None

