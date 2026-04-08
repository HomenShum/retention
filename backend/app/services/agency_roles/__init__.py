"""Agency agent role contracts for retention.sh Strategy.

Converts role templates from github.com/msitarzewski/agency-agents into
stable workspace contracts with persona, process, deliverables, and
success metrics. Each role is bound to specific Slack channel patterns
and opportunity types from the monitor rubric.

Roles:
    strategy_architect — roadmap + product strategy + investor brief alignment
    growth_analyst     — market research + competitive intelligence
    engineering_lead   — code health + architecture + drift detection
    design_steward     — UI/UX + brand consistency + Impeccable-style audits
    security_auditor   — risk + compliance + eval gates (Promptfoo pattern)
    ops_coordinator    — cross-team synthesis + standup + blockers
"""

from .role_registry import (
    AgencyRole,
    ROLE_REGISTRY,
    get_role,
    get_role_for_opportunity,
    get_system_prompt,
)

__all__ = [
    "AgencyRole",
    "ROLE_REGISTRY",
    "get_role",
    "get_role_for_opportunity",
    "get_system_prompt",
]
