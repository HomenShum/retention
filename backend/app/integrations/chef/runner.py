"""
Chef Runner

Wraps the Chef test-kitchen via subprocess (bun).
Manages run lifecycle, captures structured output, returns ChefResult.
"""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from .config import ChefConfig
from .types import ChefResult

logger = logging.getLogger(__name__)


class ChefRunner:
    """Execute Chef test-kitchen runs via subprocess.

    Uses ``bun run test-kitchen/main.ts`` under the hood.
    Each run gets a unique ID, writes output to ``output_dir/<run_id>/``.
    """

    def __init__(self, config: ChefConfig) -> None:
        self.config = config
        self.chef_dir = Path(config.chef_dir).resolve()
        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        prompt: str,
        run_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> ChefResult:
        """Run Chef with the given prompt.

        Args:
            prompt: The user prompt describing the app to generate.
            run_id: Optional run identifier (UUID generated if omitted).
            model: Override the default model from config.

        Returns:
            A :class:`ChefResult` with success/failure, deploy count, files.
        """
        if run_id is None:
            run_id = str(uuid.uuid4())

        selected_model = model or self.config.model
        run_output_dir = self.output_dir / run_id
        run_output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Starting Chef run %s (model=%s, prompt=%s…)",
            run_id,
            selected_model,
            prompt[:60],
        )

        env = self._build_env(selected_model)

        try:
            proc = await asyncio.create_subprocess_exec(
                "bun",
                "run",
                "test-kitchen/main.ts",
                "--prompt",
                prompt,
                "--output",
                str(run_output_dir),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.chef_dir),
                env=env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.config.timeout_seconds,
            )

            stdout = stdout_bytes.decode(errors="replace")
            stderr = stderr_bytes.decode(errors="replace")
            success = proc.returncode == 0

            logger.info(
                "Chef run %s finished (rc=%s, stdout=%d bytes)",
                run_id,
                proc.returncode,
                len(stdout),
            )

            return self._parse_output(success, stdout, stderr, run_output_dir)

        except asyncio.TimeoutError:
            logger.error("Chef run %s timed out after %ds", run_id, self.config.timeout_seconds)
            return ChefResult(success=False, num_deploys=0, usage={}, files={})
        except FileNotFoundError:
            logger.error("'bun' not found. Install bun: https://bun.sh")
            return ChefResult(success=False, num_deploys=0, usage={}, files={})
        except Exception as exc:
            logger.exception("Chef run %s failed: %s", run_id, exc)
            return ChefResult(success=False, num_deploys=0, usage={}, files={})

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_env(self, model: str) -> dict:
        """Build environment variables for the subprocess."""
        env = os.environ.copy()
        if self.config.openai_api_key:
            env["OPENAI_API_KEY"] = self.config.openai_api_key
        if self.config.braintrust_api_key:
            env["BRAINTRUST_API_KEY"] = self.config.braintrust_api_key
        env["CHEF_MODEL"] = model
        return env

    def _parse_output(
        self,
        success: bool,
        stdout: str,
        stderr: str,
        run_output_dir: Path,
    ) -> ChefResult:
        """Parse Chef stdout/output directory into a ChefResult."""
        # Try to parse structured JSON from stdout
        num_deploys = 0
        usage: dict = {}
        files: dict[str, str] = {}

        deploy_url: str | None = None

        # Attempt JSON extraction from stdout
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                    if "numDeploys" in data:
                        num_deploys = data.get("numDeploys", 0)
                    if "usage" in data:
                        usage = data["usage"]
                    if "success" in data:
                        success = data["success"]
                    if "deployUrl" in data:
                        deploy_url = data["deployUrl"]
                    if "url" in data and not deploy_url:
                        candidate = data["url"]
                        if isinstance(candidate, str) and candidate.startswith("http"):
                            deploy_url = candidate
                except json.JSONDecodeError:
                    continue

            # Catch bare URLs from deploy output
            if not deploy_url and ("convex.cloud" in line or "convex.site" in line or "vercel.app" in line):
                url_candidate = line.strip()
                if url_candidate.startswith("http"):
                    deploy_url = url_candidate

        # Collect generated files from output directory
        extensions = {".ts", ".tsx", ".js", ".jsx", ".json", ".css", ".html"}
        if run_output_dir.exists():
            for file_path in run_output_dir.rglob("*"):
                if file_path.is_file() and file_path.suffix in extensions:
                    rel = str(file_path.relative_to(run_output_dir))
                    try:
                        files[rel] = file_path.read_text(errors="replace")
                    except OSError:
                        pass

        return ChefResult(
            success=success,
            num_deploys=num_deploys,
            usage=usage,
            files=files,
            deploy_url=deploy_url,
            output_dir=str(run_output_dir),
        )

