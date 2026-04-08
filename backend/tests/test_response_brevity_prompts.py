from app.agents.registry.agents.strategy_brief import build_strategy_brief_prompt
from app.services.agency_roles.role_registry import STRATEGY_ARCHITECT, get_system_prompt
from app.services.llm_judge import compose_response


def test_strategy_brief_prompt_defaults_to_shorter_answers() -> None:
    prompt = build_strategy_brief_prompt({"_active_categories": ["codebase"]})

    assert "BREVITY DEFAULTS — SHORTER IS BETTER" in prompt
    assert "Aim for 3-5 short bullets or paragraphs, usually under 150 words" in prompt
    assert "Expand only when the user explicitly asks for detail or accuracy truly requires it" in prompt


def test_role_system_prompt_emphasizes_brevity() -> None:
    prompt = get_system_prompt(STRATEGY_ARCHITECT)

    assert "Default to concise answers" in prompt
    assert "Default to 3-6 sentences or 3-5 bullets; usually under 150 words" in prompt
    assert "Use one analogy at most, and do not repeat the same point twice" in prompt


def test_llm_judge_prompt_instructions_favor_short_responses() -> None:
    constants = compose_response.__code__.co_consts
    prompt_template = next(c for c in constants if isinstance(c, str) and "RESPONSE RULES:" in c)

    assert "Default to the shortest useful answer" in prompt_template
    assert "Expand only if the user explicitly asks for detail" in prompt_template
    assert "Use one analogy at most and do not repeat the same conclusion" in prompt_template
