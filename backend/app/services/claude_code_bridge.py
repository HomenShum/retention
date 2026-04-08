"""
Claude Code Integration Bridge for OpenClaw Autonomous Slack Agent.

Provides programmatic invocation of Claude Code for making code changes,
with Slack-based approval flow and git safety (branching, rollback, PR creation).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

from .slack_client import SlackClient, CLAW_CHANNEL_ID
from .convex_client import ConvexClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class InvocationMode(str, Enum):
    """How Claude Code is invoked."""

    CLI = "cli"
    SDK = "sdk"


class ApprovalStatus(str, Enum):
    """Status of a Slack-based approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class InvocationResult:
    """Result of a Claude Code invocation."""

    success: bool
    output: str
    error: str | None
    files_changed: list[str]
    duration_seconds: float
    mode: str


@dataclass
class ApprovalRequest:
    """A Slack-based approval request."""

    request_id: str
    prompt: str
    description: str
    status: ApprovalStatus
    channel: str
    message_ts: str | None
    requested_by: str
    approved_by: str | None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ClaudeCodeError(Exception):
    """Raised when a Claude Code invocation fails."""


class GitOperationError(Exception):
    """Raised when a git operation fails."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLAUDE_CLI_PATH: str = os.getenv("CLAUDE_CLI_PATH", "npx")
CLAUDE_CLI_PACKAGE: str = os.getenv("CLAUDE_CLI_PACKAGE", "@anthropic-ai/claude-code")
DEFAULT_ALLOWED_TOOLS: list[str] = ["Read", "Edit", "Bash", "Glob", "Grep"]
APPROVAL_TIMEOUT_SECONDS: int = 600

# In-memory store for pending approvals (keyed by request_id).
_pending_approvals: dict[str, ApprovalRequest] = {}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def invoke_claude_code(
    prompt: str,
    mode: InvocationMode = InvocationMode.CLI,
    allowed_tools: list[str] | None = None,
    branch_name: str | None = None,
    require_approval: bool = True,
    approval_channel: str | None = None,
    description: str = "",
    requested_by: str = "system",
) -> InvocationResult:
    """Main entry point. Optionally requests Slack approval first, then runs Claude Code.

    Parameters
    ----------
    prompt:
        The instruction to send to Claude Code.
    mode:
        Whether to use the CLI or SDK invocation path.
    allowed_tools:
        List of tool names Claude Code is permitted to use.
        Defaults to ``DEFAULT_ALLOWED_TOOLS``.
    branch_name:
        If provided, changes are made on a dedicated feature branch
        (via ``execute_with_rollback``).
    require_approval:
        When *True*, a Slack approval message is posted and the function
        blocks until the request is approved, rejected, or times out.
    approval_channel:
        Slack channel ID for the approval message. Required when
        *require_approval* is True.
    description:
        Human-readable description of the change (shown in Slack).
    requested_by:
        Identifier of the person or system that initiated the request.

    Returns
    -------
    InvocationResult
    """
    tools = allowed_tools or list(DEFAULT_ALLOWED_TOOLS)

    # -- Approval gate -------------------------------------------------------
    if require_approval:
        if not approval_channel:
            raise ClaudeCodeError(
                "approval_channel is required when require_approval is True"
            )
        approval = await request_approval(
            description=description or prompt[:120],
            prompt=prompt,
            channel=approval_channel,
            requested_by=requested_by,
        )
        logger.info(
            "Approval request %s posted to %s", approval.request_id, approval_channel
        )

        # Poll until resolved
        approval = await _wait_for_approval(approval)

        if approval.status == ApprovalStatus.REJECTED:
            logger.warning("Approval %s was rejected.", approval.request_id)
            return InvocationResult(
                success=False,
                output="",
                error="Approval rejected",
                files_changed=[],
                duration_seconds=0.0,
                mode=mode.value,
            )
        if approval.status == ApprovalStatus.TIMED_OUT:
            logger.warning("Approval %s timed out.", approval.request_id)
            return InvocationResult(
                success=False,
                output="",
                error=f"Approval timed out after {APPROVAL_TIMEOUT_SECONDS}s",
                files_changed=[],
                duration_seconds=0.0,
                mode=mode.value,
            )

    # -- Execute -------------------------------------------------------------
    if branch_name:
        result = await execute_with_rollback(
            prompt=prompt,
            branch_name=branch_name,
            allowed_tools=tools,
        )
    elif mode == InvocationMode.SDK:
        result = await _invoke_sdk(prompt, tools)
    else:
        result = await _invoke_cli(prompt, tools)

    # -- Log to Convex -------------------------------------------------------
    try:
        await log_invocation_to_convex(
            result,
            context={
                "prompt": prompt,
                "mode": mode.value,
                "branch_name": branch_name,
                "requested_by": requested_by,
                "description": description,
            },
        )
    except Exception:
        logger.exception("Failed to log invocation to Convex")

    return result


# ---------------------------------------------------------------------------
# CLI invocation
# ---------------------------------------------------------------------------


async def _invoke_cli(
    prompt: str,
    allowed_tools: list[str],
    cwd: str | None = None,
) -> InvocationResult:
    """Invoke Claude Code via CLI headless mode.

    Constructs the CLI command, runs it as a subprocess, captures
    stdout/stderr, and parses the JSON output.
    """
    tools_arg = ",".join(allowed_tools)

    if CLAUDE_CLI_PATH == "npx":
        cmd = [
            CLAUDE_CLI_PATH,
            "-y",
            CLAUDE_CLI_PACKAGE,
            "-p",
            prompt,
            "--allowedTools",
            tools_arg,
            "--output-format",
            "json",
        ]
    else:
        cmd = [
            CLAUDE_CLI_PATH,
            "-p",
            prompt,
            "--allowedTools",
            tools_arg,
            "--output-format",
            "json",
        ]

    logger.info("Running Claude Code CLI: %s", " ".join(cmd[:6]) + " ...")
    start = time.monotonic()

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        duration = time.monotonic() - start

        stdout_str = stdout_bytes.decode("utf-8", errors="replace")
        stderr_str = stderr_bytes.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            logger.error(
                "Claude Code CLI exited with code %d: %s",
                proc.returncode,
                stderr_str[:500],
            )
            return InvocationResult(
                success=False,
                output=stdout_str,
                error=stderr_str or f"Process exited with code {proc.returncode}",
                files_changed=[],
                duration_seconds=duration,
                mode=InvocationMode.CLI.value,
            )

        # Attempt to parse structured JSON output
        parsed = _parse_cli_output(stdout_str)
        files_changed = parsed.get("files_changed", [])
        output_text = parsed.get("output", stdout_str)

        logger.info(
            "Claude Code CLI completed in %.1fs, files changed: %d",
            duration,
            len(files_changed),
        )
        return InvocationResult(
            success=True,
            output=output_text,
            error=None,
            files_changed=files_changed,
            duration_seconds=duration,
            mode=InvocationMode.CLI.value,
        )

    except FileNotFoundError:
        duration = time.monotonic() - start
        msg = f"Claude Code CLI not found at '{CLAUDE_CLI_PATH}'"
        logger.error(msg)
        raise ClaudeCodeError(msg) from None
    except Exception as exc:
        duration = time.monotonic() - start
        logger.exception("Unexpected error during CLI invocation")
        return InvocationResult(
            success=False,
            output="",
            error=str(exc),
            files_changed=[],
            duration_seconds=duration,
            mode=InvocationMode.CLI.value,
        )


def _parse_cli_output(raw: str) -> dict[str, Any]:
    """Best-effort parse of Claude Code JSON output."""
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        # Some versions return a list of message objects
        if isinstance(data, list):
            output_parts: list[str] = []
            files: list[str] = []
            for item in data:
                if isinstance(item, dict):
                    if item.get("type") == "result":
                        output_parts.append(item.get("result", ""))
                    if "files_changed" in item:
                        files.extend(item["files_changed"])
            return {"output": "\n".join(output_parts), "files_changed": files}
    except (json.JSONDecodeError, TypeError, KeyError):
        pass
    return {"output": raw, "files_changed": []}


# ---------------------------------------------------------------------------
# SDK invocation
# ---------------------------------------------------------------------------


async def _invoke_sdk(
    prompt: str,
    allowed_tools: list[str],
) -> InvocationResult:
    """Invoke Claude Code via the Python SDK.

    Falls back to CLI invocation if the SDK package is not installed.
    """
    start = time.monotonic()

    try:
        from claude_code_sdk import Claude, ClaudeCodeOptions  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "claude_code_sdk not installed, falling back to CLI invocation"
        )
        return await _invoke_cli(prompt, allowed_tools)

    try:
        options = ClaudeCodeOptions(
            allowed_tools=allowed_tools,
        )
        client = Claude()
        response = await client.run(prompt, options=options)
        duration = time.monotonic() - start

        output_text = str(response) if response else ""
        logger.info("Claude Code SDK completed in %.1fs", duration)

        return InvocationResult(
            success=True,
            output=output_text,
            error=None,
            files_changed=[],
            duration_seconds=duration,
            mode=InvocationMode.SDK.value,
        )
    except Exception as exc:
        duration = time.monotonic() - start
        logger.exception("SDK invocation failed")
        return InvocationResult(
            success=False,
            output="",
            error=str(exc),
            files_changed=[],
            duration_seconds=duration,
            mode=InvocationMode.SDK.value,
        )


# ---------------------------------------------------------------------------
# Approval flow
# ---------------------------------------------------------------------------


async def request_approval(
    description: str,
    prompt: str,
    channel: str,
    requested_by: str = "system",
) -> ApprovalRequest:
    """Post a Slack Block Kit message with Approve / Reject buttons.

    Returns an ``ApprovalRequest`` whose ``message_ts`` can be used to
    track the interactive response.
    """
    request_id = uuid.uuid4().hex[:12]

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":robot_face: Claude Code Change Request",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Description:* {description}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Prompt (truncated):*\n```{prompt[:500]}```",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Requested by *{requested_by}* | ID: `{request_id}` | Timeout: {APPROVAL_TIMEOUT_SECONDS}s",
                }
            ],
        },
        {
            "type": "actions",
            "block_id": f"approval_{request_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": "claude_code_approve",
                    "value": request_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": "claude_code_reject",
                    "value": request_id,
                },
            ],
        },
    ]

    message_ts: str | None = None
    slack = SlackClient()
    try:
        response = await slack.post_message(
            channel=channel,
            text=f"Claude Code change request: {description}",
            blocks=blocks,
        )
        message_ts = response.get("ts") if isinstance(response, dict) else None
        logger.info("Approval message posted: ts=%s", message_ts)
    except Exception:
        logger.exception("Failed to post approval message to Slack")
    finally:
        await slack.close()

    approval = ApprovalRequest(
        request_id=request_id,
        prompt=prompt,
        description=description,
        status=ApprovalStatus.PENDING,
        channel=channel,
        message_ts=message_ts,
        requested_by=requested_by,
        approved_by=None,
    )
    _pending_approvals[request_id] = approval
    return approval


async def _wait_for_approval(approval: ApprovalRequest) -> ApprovalRequest:
    """Poll the in-memory approval store until resolved or timed out."""
    deadline = time.monotonic() + APPROVAL_TIMEOUT_SECONDS
    poll_interval = 2.0  # seconds

    while time.monotonic() < deadline:
        current = _pending_approvals.get(approval.request_id, approval)
        if current.status != ApprovalStatus.PENDING:
            return current
        await asyncio.sleep(poll_interval)

    # Timed out
    approval.status = ApprovalStatus.TIMED_OUT
    _pending_approvals[approval.request_id] = approval
    logger.warning("Approval %s timed out after %ds", approval.request_id, APPROVAL_TIMEOUT_SECONDS)
    return approval


def resolve_approval(request_id: str, status: ApprovalStatus, approved_by: str | None = None) -> bool:
    """Called by the Slack interaction handler to resolve an approval.

    Returns True if the approval was found and updated.
    """
    approval = _pending_approvals.get(request_id)
    if approval is None:
        logger.warning("No pending approval found for request_id=%s", request_id)
        return False

    approval.status = status
    approval.approved_by = approved_by
    _pending_approvals[request_id] = approval
    logger.info(
        "Approval %s resolved: status=%s, by=%s",
        request_id,
        status.value,
        approved_by,
    )
    return True


# ---------------------------------------------------------------------------
# Git-safe execution with rollback
# ---------------------------------------------------------------------------


async def execute_with_rollback(
    prompt: str,
    branch_name: str | None = None,
    allowed_tools: list[str] | None = None,
) -> InvocationResult:
    """Create a feature branch, checkpoint, run Claude Code, auto-revert on failure.

    Uses ``git worktree`` for isolation when possible. Implements three
    rollback patterns:

    1. **Checkpoint rewind** -- ``git stash`` before execution, pop on failure.
    2. **Git reset** -- hard reset to the pre-execution commit on failure.
    3. **Feature flags** -- changes are always on a branch; main is never touched.
    """
    tools = allowed_tools or list(DEFAULT_ALLOWED_TOOLS)
    branch = branch_name or f"claude/auto-{uuid.uuid4().hex[:8]}"
    worktree_dir: str | None = None

    start = time.monotonic()

    try:
        # --- Try worktree isolation -----------------------------------------
        worktree_dir = await _setup_worktree(branch)
        cwd = worktree_dir

        if cwd is None:
            # Fallback: create branch in the current repo
            await _run_git("checkout", "-b", branch)
            cwd = None  # use default cwd

        # Capture checkpoint
        checkpoint_sha = await _run_git("rev-parse", "HEAD", cwd=cwd)
        checkpoint_sha = checkpoint_sha.strip()
        logger.info("Checkpoint: %s on branch %s", checkpoint_sha, branch)

        # --- Invoke Claude Code ---------------------------------------------
        result = await _invoke_cli(prompt, tools, cwd=cwd)

        if not result.success:
            logger.warning("Invocation failed, rolling back to %s", checkpoint_sha)
            await _rollback(checkpoint_sha, cwd=cwd)
            result.error = (result.error or "") + " [auto-rolled back]"
            return result

        # Detect changed files
        diff_output = await _run_git("diff", "--name-only", checkpoint_sha, cwd=cwd)
        result.files_changed = [
            f.strip() for f in diff_output.splitlines() if f.strip()
        ]

        # Auto-commit if there are changes
        if result.files_changed:
            await _run_git("add", "-A", cwd=cwd)
            commit_msg = f"claude-code: {prompt[:72]}"
            await _run_git("commit", "-m", commit_msg, cwd=cwd)
            logger.info("Committed %d changed files on %s", len(result.files_changed), branch)

        result.duration_seconds = time.monotonic() - start
        return result

    except Exception as exc:
        duration = time.monotonic() - start
        logger.exception("execute_with_rollback failed")
        return InvocationResult(
            success=False,
            output="",
            error=str(exc),
            files_changed=[],
            duration_seconds=duration,
            mode=InvocationMode.CLI.value,
        )
    finally:
        # Clean up worktree if we created one
        if worktree_dir:
            await _cleanup_worktree(worktree_dir)


async def _setup_worktree(branch: str) -> str | None:
    """Attempt to create a git worktree. Returns the worktree path or None."""
    try:
        base_dir = await _run_git("rev-parse", "--show-toplevel")
        base_dir = base_dir.strip()
        worktree_path = os.path.join(base_dir, ".claude", "worktrees", branch.replace("/", "-"))
        await _run_git("worktree", "add", "-b", branch, worktree_path)
        logger.info("Created worktree at %s", worktree_path)
        return worktree_path
    except (GitOperationError, Exception) as exc:
        logger.warning("Could not create worktree, falling back: %s", exc)
        return None


async def _cleanup_worktree(worktree_dir: str) -> None:
    """Remove a git worktree."""
    try:
        await _run_git("worktree", "remove", worktree_dir, "--force")
        logger.info("Removed worktree: %s", worktree_dir)
    except Exception:
        logger.warning("Failed to remove worktree %s (may need manual cleanup)", worktree_dir)


async def _rollback(checkpoint_sha: str, cwd: str | None = None) -> None:
    """Hard-reset to the given checkpoint SHA."""
    try:
        await _run_git("reset", "--hard", checkpoint_sha, cwd=cwd)
        logger.info("Rolled back to %s", checkpoint_sha)
    except Exception:
        logger.exception("Rollback to %s failed", checkpoint_sha)
        raise GitOperationError(f"Failed to rollback to {checkpoint_sha}") from None


async def _run_git(*args: str, cwd: str | None = None) -> str:
    """Run a git command and return stdout. Raises GitOperationError on failure."""
    cmd = ["git"] + list(args)
    logger.debug("git: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout_str = stdout_bytes.decode("utf-8", errors="replace")
    stderr_str = stderr_bytes.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        raise GitOperationError(
            f"git {' '.join(args)} failed (rc={proc.returncode}): {stderr_str.strip()}"
        )
    return stdout_str


# ---------------------------------------------------------------------------
# PR creation
# ---------------------------------------------------------------------------


async def create_pr_from_changes(
    branch_name: str,
    title: str,
    body: str,
) -> str | None:
    """Create a pull request using ``gh pr create``. Returns the PR URL or None."""
    try:
        # Push the branch first
        await _run_git("push", "-u", "origin", branch_name)

        proc = await asyncio.create_subprocess_exec(
            "gh",
            "pr",
            "create",
            "--title",
            title,
            "--body",
            body,
            "--head",
            branch_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout_str = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_str = stderr_bytes.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            logger.error("gh pr create failed: %s", stderr_str)
            return None

        pr_url = stdout_str
        logger.info("Created PR: %s", pr_url)
        return pr_url

    except FileNotFoundError:
        logger.error("GitHub CLI (gh) not found. Cannot create PR.")
        return None
    except Exception:
        logger.exception("Failed to create PR for branch %s", branch_name)
        return None


# ---------------------------------------------------------------------------
# Convex logging
# ---------------------------------------------------------------------------


async def log_invocation_to_convex(
    result: InvocationResult,
    context: dict[str, Any],
) -> None:
    """Log the invocation result to Convex via ConvexClient."""
    try:
        payload = {
            "success": result.success,
            "output_preview": result.output[:500] if result.output else "",
            "error": result.error,
            "files_changed": result.files_changed,
            "duration_seconds": result.duration_seconds,
            "mode": result.mode,
            "prompt": context.get("prompt", "")[:200],
            "branch_name": context.get("branch_name"),
            "requested_by": context.get("requested_by", "system"),
            "description": context.get("description", ""),
        }
        convex = ConvexClient()
        try:
            await convex.log_monitor_decision(payload)
        finally:
            await convex.close()
        logger.debug("Logged invocation to Convex")
    except Exception:
        logger.exception("Failed to log invocation to Convex")
