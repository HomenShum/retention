#!/usr/bin/env python3
"""Live smoke check for the public retention.sh installer path.

What this verifies:
1. https://retention.sh/install.sh serves a bash script (not SPA HTML)
2. The live token endpoint returns a token for the requested email
3. A clean-room install using the public one-liner succeeds end to end
4. The MCP config points at the downloaded proxy and backend URL
5. A short voice-style memo is produced from the result

Usage:
  python3 scripts/retention_live_check.py --email homen@retention.com
  python3 scripts/retention_live_check.py --email you@example.com --platform cursor
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from scripts.voice_memo import build_memo

INSTALLER_URL = "https://retention.sh/install.sh"
TOKEN_URL = "https://exuberant-ferret-263.convex.site/api/mcp/generate-token"
PROXY_URL = "https://retention-backend.onrender.com/mcp/setup/proxy.py"
EXPECTED_INSTALLER_FIRST_LINE = "#!/usr/bin/env bash"
EXPECTED_PROXY_FIRST_LINE = "#!/usr/bin/env python3"
BACKEND_URL = "https://retention-backend.onrender.com"

PLATFORM_TO_CONFIG = {
    "claude-code": ".mcp.json",
    "cursor": ".cursor/mcp.json",
    "openclaw": ".openclaw/mcp.json",
}


def _fetch(url: str, *, method: str = "GET", payload: Dict[str, Any] | None = None, timeout: int = 40) -> Dict[str, Any]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
        return {
            "status": response.status,
            "content_type": response.headers.get("Content-Type", ""),
            "body": body,
            "url": response.geturl(),
        }


def _redact_token(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:6]}…{value[-4:]}"


def _installer_check() -> Dict[str, Any]:
    response = _fetch(INSTALLER_URL)
    first_line = response["body"].splitlines()[0] if response["body"] else ""
    ok = (
        response["status"] == 200
        and "text/plain" in response["content_type"].lower()
        and first_line == EXPECTED_INSTALLER_FIRST_LINE
    )
    return {
        "ok": ok,
        "status": response["status"],
        "content_type": response["content_type"],
        "first_line": first_line,
        "final_url": response["url"],
        "body_size": len(response["body"]),
    }


def _token_check(email: str, platform: str) -> Dict[str, Any]:
    response = _fetch(
        TOKEN_URL,
        method="POST",
        payload={"email": email, "platform": platform},
    )
    parsed = json.loads(response["body"])
    token = str(parsed.get("token", ""))
    ok = response["status"] == 200 and bool(token)
    return {
        "ok": ok,
        "status": response["status"],
        "content_type": response["content_type"],
        "token_preview": _redact_token(token),
        "token_length": len(token),
    }


def _proxy_check() -> Dict[str, Any]:
    response = _fetch(PROXY_URL)
    first_line = response["body"].splitlines()[0] if response["body"] else ""
    ok = response["status"] == 200 and first_line == EXPECTED_PROXY_FIRST_LINE
    return {
        "ok": ok,
        "status": response["status"],
        "content_type": response["content_type"],
        "first_line": first_line,
        "body_size": len(response["body"]),
    }


def _clean_room_install(email: str, platform: str) -> Dict[str, Any]:
    config_relpath = PLATFORM_TO_CONFIG[platform]

    with tempfile.TemporaryDirectory(prefix="retention-home-") as clean_home, tempfile.TemporaryDirectory(
        prefix="retention-workdir-"
    ) as clean_workdir:
        env = os.environ.copy()
        env.update(
            {
                "HOME": clean_home,
                "RETENTION_EMAIL": email,
                "RETENTION_PLATFORM": platform,
            }
        )

        command = "curl -sL https://retention.sh/install.sh | bash"
        proc = subprocess.run(
            ["bash", "-lc", command],
            cwd=clean_workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

        config_path = Path(clean_workdir) / config_relpath
        proxy_path = Path(clean_home) / ".retention" / "proxy.py"

        config_exists = config_path.exists()
        proxy_exists = proxy_path.exists()

        config_json: Dict[str, Any] = {}
        if config_exists:
            config_json = json.loads(config_path.read_text())

        server = config_json.get("mcpServers", {}).get("retention", {})
        env_block = server.get("env", {}) if isinstance(server, dict) else {}
        args = server.get("args", []) if isinstance(server, dict) else []
        config_token = str(env_block.get("RETENTION_MCP_TOKEN", ""))

        ok = (
            proc.returncode == 0
            and config_exists
            and proxy_exists
            and env_block.get("RETENTION_URL") == BACKEND_URL
            and bool(config_token)
            and bool(args)
            and str(args[0]).endswith("/.retention/proxy.py")
        )

        return {
            "ok": ok,
            "returncode": proc.returncode,
            "config_path": config_relpath,
            "config_exists": config_exists,
            "proxy_exists": proxy_exists,
            "proxy_path": str(proxy_path),
            "retention_url": env_block.get("RETENTION_URL", ""),
            "token_preview": _redact_token(config_token),
            "args": args,
        }


def run_live_check(email: str, platform: str = "claude-code") -> Dict[str, Any]:
    if platform not in PLATFORM_TO_CONFIG:
        raise ValueError(f"Unsupported platform: {platform}")

    checks: List[Dict[str, Any]] = []

    installer = _installer_check()
    checks.append({"name": "installer", **installer})

    token = _token_check(email, platform)
    checks.append({"name": "token", **token})

    proxy = _proxy_check()
    checks.append({"name": "proxy", **proxy})

    clean_room = _clean_room_install(email, platform)
    checks.append({"name": "clean_room", **clean_room})

    all_ok = all(check["ok"] for check in checks)

    memo = build_memo(
        headline=(
            "Retention install path is live end to end"
            if all_ok
            else "Retention install path still needs attention"
        ),
        what_happened=(
            f"The public installer, token service, proxy download, and clean-room {platform} install were checked against live endpoints for {email}."
        ),
        why_it_matters=(
            "This tells us whether a brand-new user can still get from one command to a working retention.sh MCP config without hidden local setup."
        ),
        next_step=(
            "If everything passed, restart the agent and confirm retention appears in /mcp. If anything failed, fix the broken public endpoint before sharing the install link."
        ),
        evidence=[
            f"installer={installer['status']} {installer['content_type']}",
            f"token={'present' if token['ok'] else 'missing'}",
            f"proxy={proxy['status']}",
            f"clean_room_exit={clean_room['returncode']}",
        ],
    )

    return {
        "ok": all_ok,
        "email": email,
        "platform": platform,
        "checks": checks,
        "voice_memo": memo,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a live retention.sh smoke check")
    parser.add_argument("--email", required=True, help="Email to use when generating the live token")
    parser.add_argument(
        "--platform",
        default="claude-code",
        choices=sorted(PLATFORM_TO_CONFIG.keys()),
        help="Installer target platform",
    )
    args = parser.parse_args()

    try:
        result = run_live_check(email=args.email, platform=args.platform)
    except urllib.error.URLError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    print(json.dumps(result, indent=2))
    print("")
    print(result["voice_memo"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
