"""retention.sh CLI — install hooks, audit logged data, show status.

Usage:
    python -m retention_sh install   # Patch Claude Code settings.json with PostToolUse hook
    python -m retention_sh audit     # Show last 50 logged events (what was captured vs scrubbed)
    python -m retention_sh status    # Show buffer stats and config
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def cmd_install() -> None:
    """Add PostToolUse hook to Claude Code settings.json."""
    settings_path = Path.home() / ".claude" / "settings.json"

    hook_command = (
        'python3 -c "'
        "import sys,json,pathlib,datetime,hashlib;"
        "d=json.load(sys.stdin);"
        "p=pathlib.Path.home()/'.retention'/'activity.jsonl';"
        "p.parent.mkdir(exist_ok=True);"
        "tn=d.get('tool_name','');"
        "ti=d.get('tool_input',{});"
        # Scrub: keep keys, hash values, redact sensitive
        "si={k:('[REDACTED]' if any(s in k.lower() for s in ['password','secret','key','token','credential']) "
        "else f'*{pathlib.PurePosixPath(str(v)).suffix}' if k in ('file_path','path') "
        "else str(v)[:20] if len(str(v))<=20 else f'[{len(str(v))}c]') "
        "for k,v in ti.items()} if isinstance(ti,dict) else {};"
        "e={'ts':datetime.datetime.now(datetime.timezone.utc).isoformat(),"
        "'source':'claude-code-hook','tool_name':tn,'tool_input':si,"
        "'session_id':d.get('session_id','')};"
        "f=open(p,'a');f.write(json.dumps(e)+'\\n');f.close()"
        '"'
    )

    # Read or create settings
    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            settings = {}

    # Get or create hooks
    hooks = settings.setdefault("hooks", {})
    post_hooks = hooks.setdefault("PostToolUse", [])

    # Check if retention hook already exists
    for entry in post_hooks:
        for h in entry.get("hooks", []):
            if "retention" in h.get("command", ""):
                print("\033[36mretention.sh\033[0m: PostToolUse hook already installed.")
                print(f"  Settings: {settings_path}")
                return

    # Add our hook
    post_hooks.append({
        "matcher": ".*",
        "hooks": [{
            "type": "command",
            "command": hook_command,
        }],
    })

    # Write back
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    print("\033[32m✓\033[0m PostToolUse hook installed.")
    print(f"  Settings:  {settings_path}")
    print(f"  Buffer:    ~/.retention/activity.jsonl")
    print(f"  Privacy:   Tool names + scrubbed shapes only. No file contents or secrets.")
    print()
    print("  Restart Claude Code to activate. Every tool call will be logged.")
    print("  View analytics at: http://localhost:5173/memory?tab=analytics")


def cmd_audit() -> None:
    """Show last 50 logged events with what was captured vs scrubbed."""
    buffer = Path.home() / ".retention" / "activity.jsonl"

    if not buffer.exists():
        print("No retention buffer found at ~/.retention/activity.jsonl")
        print("Run `python -m retention_sh install` or use `from retention_sh import track; track()`")
        return

    lines = buffer.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
    recent = lines[-50:] if len(lines) > 50 else lines

    print(f"\033[36mretention.sh audit\033[0m — last {len(recent)} of {len(lines)} events")
    print(f"Buffer: {buffer} ({buffer.stat().st_size / 1024:.1f} KB)")
    print()

    for line in recent:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts = event.get("ts", "")[:19]
        tool = event.get("tool_name", "?")
        source = event.get("source", "?")
        inp = event.get("tool_input", {})
        duration = event.get("duration_ms", 0)

        # Format input preview
        inp_parts = []
        for k, v in inp.items():
            if v == "[REDACTED]":
                inp_parts.append(f"\033[31m{k}=REDACTED\033[0m")
            elif v.startswith("[") and v.endswith("]"):
                inp_parts.append(f"{k}=\033[33m{v}\033[0m")
            else:
                inp_parts.append(f"{k}={v}")
        inp_str = ", ".join(inp_parts) if inp_parts else "-"

        print(f"  {ts}  \033[36m{tool:30s}\033[0m  {duration:>5}ms  {inp_str}")

    print()
    print(f"\033[33mNote\033[0m: Values in \033[33myellow\033[0m are scrubbed. \033[31mRed\033[0m = redacted (sensitive).")
    print("  Tool names and timing are always logged. File contents and secrets are never logged.")


def cmd_status() -> None:
    """Show buffer stats and configuration."""
    buffer = Path.home() / ".retention" / "activity.jsonl"
    consent = Path.home() / ".retention" / ".consent"
    settings = Path.home() / ".claude" / "settings.json"

    print("\033[36mretention.sh status\033[0m")
    print()

    # Buffer
    if buffer.exists():
        size_kb = buffer.stat().st_size / 1024
        lines = sum(1 for _ in open(buffer, encoding="utf-8", errors="ignore"))
        print(f"  Buffer:    {buffer}")
        print(f"             {lines} events, {size_kb:.1f} KB")
    else:
        print(f"  Buffer:    not found (expected at {buffer})")

    # Consent
    print(f"  Consent:   {'✓ given' if consent.exists() else '✗ not yet'}")

    # Claude Code hook
    hook_installed = False
    if settings.exists():
        try:
            s = json.loads(settings.read_text())
            for entry in s.get("hooks", {}).get("PostToolUse", []):
                for h in entry.get("hooks", []):
                    if "retention" in h.get("command", ""):
                        hook_installed = True
        except Exception:
            pass
    print(f"  Hook:      {'✓ installed' if hook_installed else '✗ not installed (run: python -m retention_sh install)'}")
    print(f"  Dashboard: http://localhost:5173/memory?tab=analytics")
    print()
    print("  Privacy: Tool names + scrubbed shapes only.")
    print("  No file contents, API keys, or personal data collected.")


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return

    cmd = args[0]
    if cmd == "install":
        cmd_install()
    elif cmd == "audit":
        cmd_audit()
    elif cmd == "status":
        cmd_status()
    else:
        print(f"Unknown command: {cmd}")
        print("Available: install, audit, status")
        sys.exit(1)


if __name__ == "__main__":
    main()
