from __future__ import annotations

from typing import Any, Literal

from typing_extensions import TypedDict

from agents import Agent, Runner, function_tool

from .service import InvestorBriefService, COST_KEYS, COST_MODEL_PRESETS


class InvestorBriefVariableOverrides(TypedDict, total=False):
    team_size: float
    weeks: float
    weekly_loaded_cost_per_person: float
    real_task_count: float
    avg_device_minutes_per_task: float
    device_cost_per_minute: float
    model_eval_cost_per_task: float
    judge_review_cost_per_task: float
    benchmark_replays: float
    benchmark_device_minutes_per_replay: float
    benchmark_model_cost_per_replay: float
    rerun_rate: float
    rerun_cost_per_task: float
    security_integration_fixed: float


def create_investor_brief_agent(service: InvestorBriefService, model: str | None = None) -> Agent:
    """Create an OpenAI Agents SDK wrapper for investor-brief control."""

    @function_tool
    def get_state() -> dict[str, Any]:
        """Return calculator state, derived totals, available actions, and known section IDs."""
        return service.get_state()

    @function_tool
    def list_sections() -> list[dict[str, Any]]:
        """List all stable section IDs the brief controller can retrieve or update."""
        return service.list_sections()

    @function_tool
    def get_section(section_id: str) -> dict[str, Any]:
        """Return one section by stable section_id, including bodyHtml and bodyText."""
        return service.get_section(section_id)

    @function_tool
    def update_section(
        section_id: str,
        content: str,
        content_format: Literal["html", "text"] = "html",
    ) -> dict[str, Any]:
        """Replace the body of a section while preserving its title and section identity."""
        return service.update_section(section_id=section_id, content=content, content_format=content_format)

    @function_tool
    def set_scenario(scenario: Literal["optimistic", "base", "pessimistic"]) -> dict[str, Any]:
        """Apply a named scenario preset to the sprint-cost model and persist it back to the brief."""
        return service.set_scenario(scenario)

    @function_tool
    def set_variables(variables: InvestorBriefVariableOverrides) -> dict[str, Any]:
        """Apply partial variable overrides using canonical calculator keys, then persist custom state."""
        return service.set_variables(dict(variables))

    @function_tool
    def recalculate() -> dict[str, Any]:
        """Recompute derived cost outputs from the current persisted calculator inputs."""
        return service.recalculate()

    keys = ", ".join(COST_KEYS)
    scenarios = ", ".join(COST_MODEL_PRESETS.keys())
    return Agent(
        name="InvestorBriefController",
        instructions=(
            "You control the retention.sh investor brief through deterministic tools. "
            f"Use only the canonical scenarios ({scenarios}) and canonical variable keys ({keys}). "
            "Inspect state before editing, preserve section titles, and prefer set_variables for partial calculator changes."
        ),
        tools=[get_state, list_sections, get_section, update_section, set_scenario, set_variables, recalculate],
        model=model,
    )


async def run_investor_brief_agent(
    prompt: str,
    service: InvestorBriefService,
    model: str | None = None,
) -> dict[str, Any]:
    """Run the investor brief agent against a prompt using the shared service-backed tools."""

    agent = create_investor_brief_agent(service=service, model=model)
    result = await Runner.run(agent, prompt)
    return {
        "final_output": result.final_output,
        "tool_count": len(agent.tools),
    }