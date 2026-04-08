"""Register all 22 Agency agent roles as first-class AgentConfigs.

Each role from agency_roles/role_registry.py becomes a registered agent
in the AgentRegistry with:
- Role-specific tool categories (progressive disclosure)
- Role-specific skill routing (which tool subset to start with)
- spawn_deep_research available to all roles
- Full system prompt from the role contract

This means any role can be invoked via AgentRunner with the same
tool-calling loop that powers the strategy-brief orchestrator.
The swarm's _generate_role_response_with_tools can look up the
registered config instead of building one from scratch.

Follows 2026 best practices:
- Progressive disclosure (start minimal, expand on demand)
- Model tiering (roles use gpt-5.4 with high reasoning)
- Agent-as-tool pattern (spawn_deep_research for sub-investigations)
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from ..base import AgentConfig, AgentRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Role → Tool category mapping
# ---------------------------------------------------------------------------

# Tool categories are now defined on each AgencyRole.tool_categories field.
# Fallback for roles without tool_categories defined:
_DEFAULT_TOOL_CATS = ["codebase", "web_search", "spawn"]

# Skill → category mapping for progressive disclosure per role
ROLE_SKILL_MAP: Dict[str, Dict[str, list[str]]] = {
    "strategy-architect": {
        "strategy":  ["investor_brief", "web_search"],
        "codebase":  ["codebase", "investor_brief"],
        "market":    ["web_search", "investor_brief"],
        "full":      ["investor_brief", "codebase", "web_search", "slack", "spawn"],
    },
    "growth-analyst": {
        "market":    ["web_search"],
        "slack":     ["slack", "web_search"],
        "full":      ["web_search", "investor_brief", "slack", "spawn"],
    },
    "engineering-lead": {
        "codebase":  ["codebase"],
        "review":    ["codebase", "web_search"],
        "full":      ["codebase", "investor_brief", "web_search", "slack", "spawn"],
    },
    "design-steward": {
        "audit":     ["codebase"],
        "research":  ["web_search", "codebase"],
        "full":      ["codebase", "web_search", "spawn"],
    },
    "security-auditor": {
        "audit":     ["codebase"],
        "threat":    ["codebase", "web_search"],
        "full":      ["codebase", "web_search", "spawn"],
    },
    "ops-coordinator": {
        "standup":   ["slack", "codebase"],
        "blocker":   ["slack", "codebase"],
        "full":      ["slack", "codebase", "web_search", "spawn"],
    },
    # Expanded roles (from agency-agents)
    "devops-automator": {
        "deploy":    ["codebase"],
        "monitor":   ["codebase", "web_search"],
        "full":      ["codebase", "web_search", "spawn"],
    },
    "ai-engineer": {
        "eval":      ["codebase"],
        "optimize":  ["codebase", "web_search"],
        "full":      ["codebase", "web_search", "investor_brief", "spawn"],
    },
    "product-manager": {
        "discovery": ["slack", "investor_brief"],
        "planning":  ["slack", "investor_brief", "web_search"],
        "full":      ["investor_brief", "slack", "web_search", "spawn"],
    },
    "sprint-prioritizer": {
        "backlog":   ["slack", "codebase"],
        "full":      ["slack", "codebase", "investor_brief", "spawn"],
    },
    "sre": {
        "incident":  ["codebase"],
        "monitor":   ["codebase", "web_search"],
        "full":      ["codebase", "web_search", "spawn"],
    },
    "mcp-builder": {
        "design":    ["codebase"],
        "implement": ["codebase", "web_search"],
        "full":      ["codebase", "web_search", "spawn"],
    },
    "content-creator": {
        "research":  ["web_search"],
        "write":     ["web_search", "codebase", "slack"],
        "full":      ["web_search", "codebase", "slack", "spawn"],
    },
    "seo-specialist": {
        "audit":     ["web_search"],
        "optimize":  ["web_search", "codebase"],
        "full":      ["web_search", "codebase", "spawn"],
    },
    "sales-engineer": {
        "demo":      ["codebase", "investor_brief"],
        "poc":       ["codebase", "investor_brief", "slack"],
        "full":      ["codebase", "investor_brief", "web_search", "slack", "spawn"],
    },
    "support-responder": {
        "triage":    ["codebase"],
        "resolve":   ["codebase", "slack"],
        "full":      ["codebase", "slack", "web_search", "spawn"],
    },
    "qa-tester": {
        "review":    ["codebase"],
        "test":      ["codebase", "spawn"],
        "full":      ["codebase", "spawn"],
    },
    "performance-benchmarker": {
        "baseline":  ["codebase"],
        "analyze":   ["codebase", "web_search"],
        "full":      ["codebase", "web_search", "spawn"],
    },
    "technical-writer": {
        "audit":     ["codebase"],
        "write":     ["codebase", "slack"],
        "full":      ["codebase", "slack", "spawn"],
    },
    "feedback-synthesizer": {
        "collect":   ["slack"],
        "analyze":   ["slack", "web_search"],
        "full":      ["slack", "web_search", "investor_brief", "spawn"],
    },
    "data-engineer": {
        "schema":    ["codebase"],
        "pipeline":  ["codebase", "spawn"],
        "full":      ["codebase", "spawn"],
    },
    "compliance-auditor": {
        "review":    ["codebase"],
        "gap":       ["codebase", "web_search"],
        "full":      ["codebase", "web_search", "spawn"],
    },
}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def _register_agency_roles() -> None:
    """Register all 22 Agency roles as AgentConfigs in the AgentRegistry.

    Each role becomes a standalone agent that can be invoked via:
        config = AgentRegistry.get("agency-strategy-architect")
        runner = AgentRunner(config)
        result = await runner.run(question)

    The naming convention is "agency-{role-id}" to avoid collision with
    the main "strategy-brief" orchestrator agent.
    """
    try:
        from ....services.agency_roles import ROLE_REGISTRY, get_system_prompt
    except ImportError:
        logger.warning("Could not import agency_roles — skipping registration")
        return

    for role_id, role in ROLE_REGISTRY.items():
        tool_cats = role.tool_categories if role.tool_categories else _DEFAULT_TOOL_CATS
        agent_name = f"agency-{role_id}"

        # Skip if already registered (idempotent)
        if AgentRegistry.has(agent_name):
            continue

        system_prompt = get_system_prompt(role)

        AgentRegistry.register(
            AgentConfig(
                name=agent_name,
                system_prompt=system_prompt,
                tool_categories=tool_cats,
                model="gpt-5.4-mini",
                reasoning_effort="high",
                max_turns=1000,
            )
        )
        logger.info("Registered agency role agent: %s (%d tools)", agent_name, len(tool_cats))


# Auto-register on import
_register_agency_roles()
