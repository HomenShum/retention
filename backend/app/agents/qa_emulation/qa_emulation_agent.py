"""
QA Emulation Agent Configs - Prompt-Versioned Agent Variants

Three agent variants for QA task emulation:
- v11_compact: All skills pre-attached, numbered auto-loaded
- v12: Lean base with on-demand skill loading
- v12_compaction: v12 + session compaction policy

Each variant uses the same tools and handoffs but different instructions
and context management strategies.

Maps to: tmp/Untitled.txt Section 1 (Prompt files → Agent definitions)
"""

import logging
from typing import Optional, List
from agents import Agent, function_tool, handoff
from agents.model_settings import ModelSettings
from openai.types.shared import Reasoning
from ..orchestration.progressive_disclosure import ProgressiveDisclosureLoader
from ..model_fallback import get_model_fallback_chain

logger = logging.getLogger(__name__)


# ============================================================================
# Shared tool: on-demand skill loader (v12/v12_compaction only)
# Wraps ProgressiveDisclosureLoader as a function_tool
# ============================================================================

# In-memory skill state per run (would be session-scoped in production)
_active_skills: List[str] = []
_loader: Optional[ProgressiveDisclosureLoader] = None

_SKILL_ALIASES = {
    "device_setup": "device_testing",
    "bug_claim_validation": "qa_emulation",
    "bug_detection": "qa_emulation",
    "anomaly_detection": "qa_emulation",
    "verdict_assembly": "qa_emulation",
    "agent_resilience": "qa_emulation",
    "resilience": "qa_emulation",
    "image_token_optimization": "qa_emulation",
    "image_optimization": "qa_emulation",
    "session_evaluation": "qa_emulation",
}


def _get_loader() -> ProgressiveDisclosureLoader:
    """Return a shared progressive-disclosure loader for QA emulation tools."""
    global _loader
    if _loader is None:
        _loader = ProgressiveDisclosureLoader()
        _loader.load_all_metadata()
    return _loader


def _resolve_skill_name(skill_name: str) -> str:
    normalized = skill_name.strip().lower()
    return _SKILL_ALIASES.get(normalized, normalized)


def _activate_skill(skill_name: str) -> str:
    loader = _get_loader()
    requested_skill = skill_name.strip()
    resolved_skill = _resolve_skill_name(requested_skill)
    context = loader.get_context_for_skill(resolved_skill, level=2)

    if not context.get("matched"):
        available_skills = ", ".join(sorted(loader.load_all_metadata().keys()))
        return (
            f"Unknown skill '{requested_skill}'. "
            f"Available repo-native skills: {available_skills}."
        )

    already_active = resolved_skill in _active_skills
    if not already_active:
        _active_skills.append(resolved_skill)
        logger.info(
            "[QA Emulation] Skill activated: %s (requested=%s)",
            resolved_skill,
            requested_skill,
        )

    if already_active:
        return (
            f"Skill '{resolved_skill}' is already active. "
            f"Active skills: {_active_skills}"
        )

    alias_note = ""
    if resolved_skill != requested_skill.lower():
        alias_note = f"Requested alias '{requested_skill}' → repo skill '{resolved_skill}'.\n\n"

    capabilities = ", ".join(context.get("capabilities", []))
    skill_doc = context.get("skill_doc", "")
    return (
        f"{alias_note}Loaded skill '{resolved_skill}'.\n"
        f"Description: {context.get('description', '')}\n"
        f"Capabilities: {capabilities}\n"
        f"Active skills: {_active_skills}\n\n"
        f"{skill_doc}"
    )


@function_tool
async def load_skill(skill_name: str) -> str:
    """
    Activate a named skill for the current QA session.

    Preferred repo-native skills: device_testing, qa_emulation.

    Backward-compatible aliases are also accepted for screenshot-derived
    names like device_setup, bug_detection, anomaly_detection,
    verdict_assembly, resilience, image_optimization, session_evaluation.

    Args:
        skill_name: Name of the skill to activate

    Returns:
        Confirmation of skill activation
    """
    return _activate_skill(skill_name)


@function_tool
async def set_phase(phase: str) -> str:
    """
    Set the current workflow phase for tracking.

    Phases: LEASE_DEVICE, LOGIN, LOAD_BUILD_OG, REPRO_ON_OG,
    LOAD_BUILD_RB1, REPRO_ON_RB1, LOAD_BUILD_RB2, REPRO_ON_RB2,
    LOAD_BUILD_RB3, REPRO_ON_RB3, GATHER_EVIDENCE, ASSEMBLE_VERDICT

    Args:
        phase: The workflow phase to set

    Returns:
        Confirmation of phase change
    """
    logger.info(f"[QA Emulation] Phase set to: {phase}")
    return f"Phase updated to: {phase}"


@function_tool
async def store_evidence(
    evidence_id: str,
    build_id: str,
    evidence_type: str,
    description: str
) -> str:
    """
    Store a piece of evidence collected during testing.

    Args:
        evidence_id: Unique ID (e.g., EV-001)
        build_id: Build this evidence is from (OG, RB1, RB2, RB3)
        evidence_type: Type (screenshot, log, video, element_dump, network_trace)
        description: What this evidence shows

    Returns:
        Confirmation of evidence storage
    """
    logger.info(f"[QA Emulation] Evidence stored: {evidence_id} ({evidence_type}) for build {build_id}")
    return f"Evidence {evidence_id} stored for build {build_id}: {description}"


# ============================================================================
# Base instructions shared across variants
# ============================================================================

BASE_INSTRUCTIONS = """You are the **QA Task Emulator**, an expert at reproducing mobile app bugs across multiple builds.

**Core Workflow:**
1. Lease a device and prepare the test environment
2. Load the Original build (OG) and attempt reproduction
3. Load Regression Builds (RB1, RB2, RB3) and attempt reproduction on each
4. Gather all evidence and assemble a final verdict

**Build Sequence (DETERMINISTIC - follow this order):**
OG → RB1 → RB2 → RB3

**For each build:**
- Load the build on the device
- Follow the reproduction steps from the bug report
- Capture screenshots and logs as evidence
- Use the Bug Detection Specialist to classify results
- Use the Anomaly Detection Specialist to check for unexpected issues
- Record evidence with store_evidence tool
- Track phase with set_phase tool

**Sub-Agent Handoffs:**
- **Bug Detection Specialist**: Hand off after each build test to classify bug reproduction
- **Anomaly Detection Specialist**: Hand off after each build test to check for unexpected issues
- **Verdict Assembly Specialist**: Hand off at the end with all results to get final verdict

**Evidence Requirements:**
- At least one screenshot per build tested
- Element dump for any screen where bug should manifest
- Logs if crash or error occurs
- Network trace if relevant

**Critical Rules:**
- NEVER skip a build in the sequence
- ALWAYS capture evidence before moving to next build
- NEVER emit a verdict without using the Verdict Assembly Specialist
- ALWAYS set the phase before starting each workflow step
"""


# ============================================================================
# V11 Compact: All skills pre-attached
# ============================================================================

V11_EXTRA = """
**Mode: V11 Compact (All Skills Pre-Loaded)**
All skills are available from the start. No need to call load_skill().
You have full access to device setup, bug detection, anomaly detection,
verdict assembly, resilience, and image optimization capabilities.
Focus on efficient execution without skill loading overhead.
"""


# ============================================================================
# V12: On-demand skill loading
# ============================================================================

V12_EXTRA = """
**Mode: V12 (On-Demand Skills)**
Start with minimal context. Use the load_skill() tool to activate
capabilities as needed during the workflow. This reduces context
window usage and allows dynamic capability expansion.

Recommended skill loading order:
1. device_testing → when leasing device / driving the app
2. qa_emulation → before build-sequence testing and final verdict assembly

Legacy aliases from earlier prompt drafts are still accepted, but prefer
repo-native skill names so the existing orchestrator workflow stays aligned.
"""


# ============================================================================
# V12 Compaction: V12 + session compaction
# ============================================================================

V12_COMPACTION_EXTRA = """
**Mode: V12 Compaction (On-Demand + Memory Compaction)**
Same as V12, but with aggressive context compaction between builds.

After each build test:
1. Preserve: evidence IDs, verdict classification, anomaly flags
2. Summarize: detailed step-by-step into 2-3 sentence summary
3. Discard: raw element dumps, verbose logs (keep references only)

This enables longer multi-build sequences without exceeding context limits.
"""


def create_qa_emulation_agent(
    prompt_version: str = "v12",
    bug_detection_agent: Agent = None,
    anomaly_detection_agent: Agent = None,
    verdict_assembly_agent: Agent = None,
    additional_tools: list = None,
    reasoning_effort: str = "high",
) -> Agent:
    """
    Create a QA emulation agent with the specified prompt version.

    Follows the coordinator agent pattern from coordinator_agent.py:
    - Handoffs to specialist subagents
    - Model tiering via get_model_fallback_chain
    - Configurable prompt variants

    Args:
        prompt_version: "v11_compact", "v12", or "v12_compaction"
        bug_detection_agent: Bug Detection Specialist for handoff
        anomaly_detection_agent: Anomaly Detection Specialist for handoff
        verdict_assembly_agent: Verdict Assembly Specialist for handoff
        additional_tools: Extra tools (device control, screenshot, etc.)
        reasoning_effort: Reasoning effort level (none/low/medium/high/xhigh)

    Returns:
        Configured QA emulation agent
    """
    # Build instructions based on variant
    version_extras = {
        "v11_compact": V11_EXTRA,
        "v12": V12_EXTRA,
        "v12_compaction": V12_COMPACTION_EXTRA,
    }
    extra = version_extras.get(prompt_version, V12_EXTRA)
    instructions = BASE_INSTRUCTIONS + extra

    # Build tools list
    tools = [set_phase, store_evidence]
    if prompt_version in ("v12", "v12_compaction"):
        tools.append(load_skill)
    if additional_tools:
        tools.extend(additional_tools)

    # Build handoffs
    handoffs_list = []
    if bug_detection_agent:
        handoffs_list.append(handoff(
            agent=bug_detection_agent,
            tool_description_override="Classify bug evidence and determine if expected bug was reproduced"
        ))
    if anomaly_detection_agent:
        handoffs_list.append(handoff(
            agent=anomaly_detection_agent,
            tool_description_override="Monitor for unexpected anomalies during bug reproduction"
        ))
    if verdict_assembly_agent:
        handoffs_list.append(handoff(
            agent=verdict_assembly_agent,
            tool_description_override="Assemble final structured verdict from all build results"
        ))

    # Model tiering: orchestration tier for QA emulation (gpt-5.4)
    model_chain = get_model_fallback_chain("orchestration")
    primary_model = model_chain[0]
    logger.info(f"QA Emulation Agent ({prompt_version}) using model chain: {model_chain}")

    agent = Agent(
        name=f"QA Task Emulator ({prompt_version})",
        instructions=instructions,
        tools=tools,
        handoffs=handoffs_list if handoffs_list else None,
        model=primary_model,
        model_settings=ModelSettings(
            tool_choice="auto",
            parallel_tool_calls=False,  # Sequential build workflow
            reasoning=Reasoning(effort=reasoning_effort),
        ),
    )

    return agent


__all__ = ["create_qa_emulation_agent", "load_skill", "set_phase", "store_evidence"]
