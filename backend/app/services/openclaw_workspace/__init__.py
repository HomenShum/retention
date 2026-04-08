"""OpenClaw workspace contracts for retention.sh Strategy Agent.

These files define the agent's identity, role registry, tool permissions,
and memory architecture. They follow the OpenClaw pattern:
- SOUL.md: Core identity and operating principles
- AGENTS.md: Role registry and orchestration rules
- TOOLS.md: Tool access policy and execution constraints
- MEMORY.md: Memory architecture and persistence strategy

When deployed to OpenClaw, these files go into the workspace directory.
In the current Render-based deployment, they serve as documentation and
are referenced by the agency_roles module for system prompts.
"""

import os
from pathlib import Path

WORKSPACE_DIR = Path(__file__).parent


def read_contract(name: str) -> str:
    """Read a workspace contract file."""
    path = WORKSPACE_DIR / name
    if path.exists():
        return path.read_text()
    return ""


SOUL = read_contract("SOUL.md")
AGENTS = read_contract("AGENTS.md")
TOOLS = read_contract("TOOLS.md")
MEMORY = read_contract("MEMORY.md")
