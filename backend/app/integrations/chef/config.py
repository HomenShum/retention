"""
Chef Integration Configuration

Centralised configuration for ChefRunner.
Reads from environment variables with sensible defaults.
"""

from dataclasses import dataclass, field
from typing import Optional
import os


@dataclass
class ChefConfig:
    """Configuration for the Chef runner.

    Attributes:
        model: Default LLM model to use (e.g. "gpt-5.4").
        max_steps: Maximum agentic loop steps (matches MAX_STEPS in chefTask.ts).
        max_deploys: Maximum deploy attempts (matches MAX_DEPLOYS in chefTask.ts).
        max_tokens: Maximum token budget per generation call.
        timeout_seconds: Maximum wall-clock time for a single run.
        openai_api_key: OpenAI API key (reads OPENAI_API_KEY if not set).
        braintrust_api_key: Braintrust API key for eval logging.
        convex_deploy_key: Convex deploy key for preview deployments.
        chef_dir: Path to the vendored Chef directory.
        output_dir: Scratch directory for task outputs.
        enable_convex_deploy: Whether to deploy generated apps to Convex.
        enable_vercel_deploy: Whether to deploy generated apps to Vercel.
    """

    # Model defaults
    model: str = "gpt-5.4"
    max_steps: int = 32
    max_deploys: int = 10
    max_tokens: int = 16384
    timeout_seconds: int = 600  # 10 minutes

    # API keys (populated from env if not explicitly set)
    openai_api_key: Optional[str] = None
    braintrust_api_key: Optional[str] = None
    convex_deploy_key: Optional[str] = None

    # Paths
    chef_dir: str = "integrations/chef"
    output_dir: str = "/tmp/chef-runs"

    # Feature flags
    enable_convex_deploy: bool = False
    enable_vercel_deploy: bool = False

    def __post_init__(self) -> None:
        """Fill API keys from environment if not explicitly provided."""
        if self.openai_api_key is None:
            self.openai_api_key = os.getenv("OPENAI_API_KEY")
        if self.braintrust_api_key is None:
            self.braintrust_api_key = os.getenv("BRAINTRUST_API_KEY")
        if self.convex_deploy_key is None:
            self.convex_deploy_key = os.getenv("CONVEX_DEPLOY_KEY")

