"""
Chef Deployer

Handles Convex preview deployments and Vercel preview deployments
for Chef-generated applications.
"""

import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class ChefDeployer:
    """Deploy Chef-generated apps to Convex and/or Vercel.

    Uses ``npx convex deploy --preview-create <name>`` for Convex
    and ``npx vercel --prebuilt`` for Vercel.
    """

    def __init__(
        self,
        convex_deploy_key: Optional[str] = None,
        vercel_token: Optional[str] = None,
    ) -> None:
        self.convex_deploy_key = convex_deploy_key
        self.vercel_token = vercel_token

    async def deploy_to_convex(
        self,
        app_dir: Path,
        preview_name: str,
        timeout: int = 300,
    ) -> Dict:
        """Deploy an app to a Convex preview deployment.

        Args:
            app_dir: Directory containing the generated app with convex/ folder.
            preview_name: Name for the preview deployment.
            timeout: Max seconds to wait.

        Returns:
            Dict with success, preview_name, url, and optionally error.
        """
        if not self.convex_deploy_key:
            return {
                "success": False,
                "error": "CONVEX_DEPLOY_KEY not configured",
            }

        import os

        env = os.environ.copy()
        env["CONVEX_DEPLOY_KEY"] = self.convex_deploy_key

        try:
            proc = await asyncio.create_subprocess_exec(
                "npx",
                "convex",
                "deploy",
                "--preview-create",
                preview_name,
                cwd=str(app_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )

            stdout = stdout_bytes.decode(errors="replace")
            stderr = stderr_bytes.decode(errors="replace")

            if proc.returncode != 0:
                logger.error("Convex deploy failed: %s", stderr)
                return {
                    "success": False,
                    "preview_name": preview_name,
                    "error": stderr[:500],
                }

            # Parse deployment URL from output
            url = f"https://{preview_name}.convex.cloud"
            for line in stdout.splitlines():
                if "convex.cloud" in line or "convex.site" in line:
                    url = line.strip()
                    break

            logger.info("Convex deploy succeeded: %s", url)
            return {
                "success": True,
                "preview_name": preview_name,
                "url": url,
            }

        except asyncio.TimeoutError:
            logger.error("Convex deploy timed out after %ds", timeout)
            return {"success": False, "error": f"Timed out after {timeout}s"}
        except Exception as exc:
            logger.exception("Convex deploy error: %s", exc)
            return {"success": False, "error": str(exc)}

    async def deploy_to_vercel(
        self,
        app_dir: Path,
        project_name: str,
        timeout: int = 300,
    ) -> Dict:
        """Deploy an app to Vercel as a preview.

        Args:
            app_dir: Directory containing the built app.
            project_name: Vercel project name.
            timeout: Max seconds to wait.

        Returns:
            Dict with success, url, and optionally error.
        """
        if not self.vercel_token:
            return {
                "success": False,
                "error": "VERCEL_TOKEN not configured",
            }

        import os

        env = os.environ.copy()
        env["VERCEL_TOKEN"] = self.vercel_token

        try:
            proc = await asyncio.create_subprocess_exec(
                "npx",
                "vercel",
                "--yes",
                "--token",
                self.vercel_token,
                cwd=str(app_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )

            stdout = stdout_bytes.decode(errors="replace")

            if proc.returncode != 0:
                stderr = stderr_bytes.decode(errors="replace")
                return {"success": False, "error": stderr[:500]}

            # Vercel prints the deployment URL on stdout
            url = stdout.strip().splitlines()[-1] if stdout.strip() else ""
            logger.info("Vercel deploy succeeded: %s", url)
            return {"success": True, "url": url}

        except asyncio.TimeoutError:
            return {"success": False, "error": f"Timed out after {timeout}s"}
        except Exception as exc:
            logger.exception("Vercel deploy error: %s", exc)
            return {"success": False, "error": str(exc)}

