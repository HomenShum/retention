from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Union


DEFAULT_BRIEF_PATH = Path(__file__).resolve().parents[3] / "tmp" / "TA_Strategy_Brief_InHouseAgent.html"
SCENARIOS = ("optimistic", "base", "pessimistic", "custom")
COST_MODEL_PRESETS: dict[str, dict[str, float]] = {
    "optimistic": {
        "team_size": 3,
        "weeks": 5,
        "weekly_loaded_cost_per_person": 2200,
        "real_task_count": 40,
        "avg_device_minutes_per_task": 3.5,
        "device_cost_per_minute": 0.17,
        "model_eval_cost_per_task": 10,
        "judge_review_cost_per_task": 12,
        "benchmark_replays": 10,
        "benchmark_device_minutes_per_replay": 18,
        "benchmark_model_cost_per_replay": 40,
        "rerun_rate": 0.4,
        "rerun_cost_per_task": 55,
        "security_integration_fixed": 2800,
    },
    "base": {
        "team_size": 4,
        "weeks": 6,
        "weekly_loaded_cost_per_person": 2500,
        "real_task_count": 75,
        "avg_device_minutes_per_task": 4,
        "device_cost_per_minute": 0.17,
        "model_eval_cost_per_task": 12,
        "judge_review_cost_per_task": 18,
        "benchmark_replays": 20,
        "benchmark_device_minutes_per_replay": 25,
        "benchmark_model_cost_per_replay": 55,
        "rerun_rate": 0.6,
        "rerun_cost_per_task": 85,
        "security_integration_fixed": 1994,
    },
    "pessimistic": {
        "team_size": 5,
        "weeks": 8,
        "weekly_loaded_cost_per_person": 2800,
        "real_task_count": 120,
        "avg_device_minutes_per_task": 5,
        "device_cost_per_minute": 0.17,
        "model_eval_cost_per_task": 15,
        "judge_review_cost_per_task": 24,
        "benchmark_replays": 30,
        "benchmark_device_minutes_per_replay": 30,
        "benchmark_model_cost_per_replay": 70,
        "rerun_rate": 0.7,
        "rerun_cost_per_task": 75,
        "security_integration_fixed": 1845,
    },
}
SECTION_IDS: dict[tuple[str, str], str] = {
    ("brief", "Executive Summary"): "executive-summary",
    ("brief", "What We Looked At, and What We Decided"): "what-we-looked-at-and-what-we-decided",
    ("brief", "What an Investor Would Be Betting On"): "what-an-investor-would-be-betting-on",
    ("brief", "Why Now"): "why-now",
    ("brief", "1) Companies are already building AI agents at large scale"): "why-now-ai-agents-scale",
    ("brief", "2) Governments and standards bodies are starting to require proof"): "why-now-proof-requirements",
    ("brief", "3) AI agents are starting to work in teams, and that makes checking harder"): "why-now-agent-teams",
    ("brief", "4) AI value only sticks when companies change how they operate"): "why-now-operating-model",
    ("brief", "How We Should Describe Ourselves"): "how-we-should-describe-ourselves",
    ("brief", "Three Stories We Need to Tell"): "three-stories-we-need-to-tell",
    ("brief", "The Public Story (what we say on our website)"): "public-story",
    ("brief", "The Buyer Story (what we say in sales meetings)"): "buyer-story",
    ("brief", "The Trust Story (how we answer “why should I believe you?”)"): "trust-story",
    ("brief", "Why QA Is the Right Starting Point"): "why-qa-is-the-right-starting-point",
    ("brief", "How We Grow: From Product to Platform"): "how-we-grow-from-product-to-platform",
    ("brief", "Phase 1: The Agent-Driven Emulator Demo"): "phase-1-agent-driven-emulator-demo",
    ("brief", "Phase 1A: ActionSpan — The Engine Underneath"): "phase-1a-actionspan",
    ("brief", "Action-Span Timeline"): "action-span-timeline",
    ("brief", "System Architecture Map"): "system-architecture-map",
    ("brief", "Phase 2: The Validation Stop Hook — Expanding into Coding Agents"): "phase-2-validation-stop-hook",
    ("brief", "Phase 3: OpenClaw Integration / The Enterprise Agent Control Plane"): "phase-3-openclaw-control-plane",
    ("brief", "Phase 4: Private Deployment, Governance, and Chat-Based Tools"): "phase-4-private-deployment-governance",
    ("brief", "Ideas We Evaluated But Are Not Prioritizing Now"): "ideas-not-prioritizing-now",
    ("brief", "Why retention.sh Can Win"): "why-retention-can-win",
    ("brief", "ActionSpan: Our Deepest Competitive Advantage"): "actionspan-deepest-competitive-advantage",
    ("brief", "How We Make Money"): "how-we-make-money",
    ("brief", "KPI + Case Study Panel: Android World Benchmark Results"): "android-world-benchmark",
    ("brief", "What We Need to Build Right Now (and What We Do Not)"): "what-we-need-to-build-right-now",
    ("brief", "Decision Point: Hosted Device Farm — Tradeoff Analysis"): "hosted-device-tradeoff",
    ("brief", "The Evidence Flywheel"): "evidence-flywheel",
    ("brief", "Numbers That Prove the Story Is Working"): "numbers-that-prove-the-story-is-working",
    ("brief", "What Investors Should Watch Over the Next 12–18 Months"): "what-investors-should-watch-next-12-18-months",
    ("brief", "The Six-Week Proof Window"): "six-week-proof-window",
    ("brief", "What should be true by week 6"): "six-week-true-by-week-6",
    ("brief", "How the six weeks should unfold"): "six-week-how-it-unfolds",
    ("brief", "Operating rules investors should expect"): "six-week-operating-rules",
    ("brief", "What the proving sprint costs"): "sprint-cost-model",
    ("brief", "How hosted-device risk is governed"): "hosted-device-governance",
    ("brief", "Risks and Honest Answers"): "risks-and-honest-answers",
    ("brief", "Risk: We get perceived as just another QA tool"): "risk-perceived-as-qa-tool",
    ("brief", "Risk: Big companies like Google, Microsoft, or Salesforce add this to their own platforms"): "risk-big-companies-add-this",
    ("brief", "Risk: Enterprises decide to build their own checking system"): "risk-enterprises-build-their-own",
    ("brief", "Risk: We spread ourselves too thin across too many product ideas"): "risk-spread-too-thin",
    ("brief", "Build Order and Presentation Standards"): "build-order-and-presentation-standards",
    ("brief", "Investor Sound Bites"): "investor-sound-bites",
    ("brief", "Research and Evidence Behind These Claims"): "research-and-evidence-behind-these-claims",
    ("brief", "How This Aligns With What We Have Already Built"): "how-this-aligns-with-what-we-have-built",
    ("one-pager", "Core Thesis"): "onepager-core-thesis",
    ("one-pager", "Six-Week Proof Sprint"): "onepager-six-week-proof-sprint",
    ("one-pager", "Benchmark Proof Point"): "onepager-benchmark-proof-point",
    ("one-pager", "Economics"): "onepager-economics",
    ("one-pager", "Hosted Device Governance"): "onepager-hosted-device-governance",
    ("one-pager", "Why Now"): "onepager-why-now",
    ("one-pager", "Key Investor Sound Bite"): "onepager-key-investor-sound-bite",
}
COST_KEYS = tuple(COST_MODEL_PRESETS["base"].keys())
CONTAINER_PATTERNS = {
    "brief": re.compile(r'(<div id="doc-content"[^>]*>)(?P<inner>.*?)(</div><!-- /doc-content -->)', re.S),
    "one-pager": re.compile(r'(<div id="one-pager"[^>]*>)(?P<inner>.*?)(</div>\s*)(?=<script>)', re.S),
}
HEADING_PATTERN = re.compile(r'<h(?P<level>[23])(?P<attrs>[^>]*)>(?P<title>.*?)</h(?P=level)>', re.I | re.S)
TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class SectionMatch:
    scope: str
    level: int
    title: str
    section_id: str
    start: int
    end: int
    body_start: int
    body_end: int


class InvestorBriefService:
    """Single source of truth for investor brief section + calculator control."""

    def __init__(self, brief_path: Union[Path, str, None] = None) -> None:
        self.brief_path = Path(brief_path or DEFAULT_BRIEF_PATH)

    def list_sections(self) -> list[dict[str, Any]]:
        html_doc = self._read_html()
        sections: list[dict[str, Any]] = []
        for scope in CONTAINER_PATTERNS:
            inner = self._get_container_inner(html_doc, scope)
            for section in self._parse_sections(inner, scope):
                sections.append(
                    {
                        "sectionId": section.section_id,
                        "scope": scope,
                        "level": section.level,
                        "title": section.title,
                    }
                )
        return sections

    def get_state(self) -> dict[str, Any]:
        html_doc = self._read_html()
        variables = self._extract_cost_inputs(html_doc)
        scenario = self._extract_current_scenario(html_doc)
        breakdown = self.compute_cost_breakdown(variables)
        return {
            "scenario": scenario,
            "variables": variables,
            "breakdown": breakdown,
            "sections": self.list_sections(),
            "actions": [
                "get_state",
                "list_sections",
                "get_section",
                "update_section",
                "set_scenario",
                "set_variables",
                "recalculate",
            ],
            "briefPath": str(self.brief_path),
        }

    def get_section(self, section_id: str) -> dict[str, Any]:
        html_doc = self._read_html()
        scope, inner, match = self._locate_section(html_doc, section_id)
        body_html = inner[match.body_start:match.body_end].strip()
        return {
            "sectionId": section_id,
            "scope": scope,
            "level": match.level,
            "title": match.title,
            "bodyHtml": body_html,
            "bodyText": self._html_to_text(body_html),
        }

    def update_section(
        self,
        section_id: str,
        content: str,
        content_format: Literal["html", "text"] = "html",
    ) -> dict[str, Any]:
        html_doc = self._read_html()
        scope, inner, match = self._locate_section(html_doc, section_id)
        replacement = self._normalize_section_content(content, content_format)
        updated_inner = f"{inner[:match.body_start]}{replacement}{inner[match.body_end:]}"
        updated_html = self._replace_container_inner(html_doc, scope, updated_inner)
        self._write_html(updated_html)
        return self.get_section(section_id)

    def set_scenario(self, scenario: str) -> dict[str, Any]:
        if scenario not in COST_MODEL_PRESETS:
            raise ValueError(f"Unsupported scenario: {scenario}")
        html_doc = self._read_html()
        updated_html = self._apply_cost_state(html_doc, COST_MODEL_PRESETS[scenario], scenario)
        self._write_html(updated_html)
        return self.get_state()

    def set_variables(self, variables: dict[str, float | int]) -> dict[str, Any]:
        html_doc = self._read_html()
        current = self._extract_cost_inputs(html_doc)
        merged = dict(current)
        for key, value in (variables or {}).items():
            if key not in COST_KEYS:
                continue
            merged[key] = float(value)
        updated_html = self._apply_cost_state(html_doc, merged, "custom")
        self._write_html(updated_html)
        return self.get_state()

    def recalculate(self) -> dict[str, Any]:
        html_doc = self._read_html()
        variables = self._extract_cost_inputs(html_doc)
        scenario = self._extract_current_scenario(html_doc)
        updated_html = self._apply_cost_state(html_doc, variables, scenario)
        self._write_html(updated_html)
        return self.get_state()

    @staticmethod
    def compute_cost_breakdown(inputs: dict[str, float]) -> dict[str, float]:
        team_burn = inputs["team_size"] * inputs["weeks"] * inputs["weekly_loaded_cost_per_person"]
        task_device_burn = inputs["real_task_count"] * inputs["avg_device_minutes_per_task"] * inputs["device_cost_per_minute"]
        task_model_burn = inputs["real_task_count"] * inputs["model_eval_cost_per_task"]
        judge_burn = inputs["real_task_count"] * inputs["judge_review_cost_per_task"]
        benchmark_device_burn = inputs["benchmark_replays"] * inputs["benchmark_device_minutes_per_replay"] * inputs["device_cost_per_minute"]
        benchmark_model_burn = inputs["benchmark_replays"] * inputs["benchmark_model_cost_per_replay"]
        rerun_burn = inputs["real_task_count"] * inputs["rerun_rate"] * inputs["rerun_cost_per_task"]
        security_burn = inputs["security_integration_fixed"]
        total_burn = (
            team_burn
            + task_device_burn
            + task_model_burn
            + judge_burn
            + benchmark_device_burn
            + benchmark_model_burn
            + rerun_burn
            + security_burn
        )
        return {
            "teamBurn": team_burn,
            "taskDeviceBurn": task_device_burn,
            "taskModelBurn": task_model_burn,
            "judgeBurn": judge_burn,
            "benchmarkDeviceBurn": benchmark_device_burn,
            "benchmarkModelBurn": benchmark_model_burn,
            "rerunBurn": rerun_burn,
            "securityBurn": security_burn,
            "totalBurn": total_burn,
        }

    def _read_html(self) -> str:
        return self.brief_path.read_text(encoding="utf-8")

    def _write_html(self, html_doc: str) -> None:
        self.brief_path.write_text(html_doc, encoding="utf-8")

    def _get_container_inner(self, html_doc: str, scope: str) -> str:
        match = CONTAINER_PATTERNS[scope].search(html_doc)
        if not match:
            raise ValueError(f"Unable to locate container for scope: {scope}")
        return match.group("inner")

    def _replace_container_inner(self, html_doc: str, scope: str, replacement: str) -> str:
        pattern = CONTAINER_PATTERNS[scope]
        match = pattern.search(html_doc)
        if not match:
            raise ValueError(f"Unable to locate container for scope: {scope}")
        if scope == "one-pager":
            suffix = match.group(3)
            return f"{html_doc[:match.start()]}{match.group(1)}{replacement}{suffix}{html_doc[match.end():]}"
        return f"{html_doc[:match.start()]}{match.group(1)}{replacement}{match.group(3)}{html_doc[match.end():]}"

    def _locate_section(self, html_doc: str, section_id: str) -> tuple[str, str, SectionMatch]:
        for scope in CONTAINER_PATTERNS:
            inner = self._get_container_inner(html_doc, scope)
            for section in self._parse_sections(inner, scope):
                if section.section_id == section_id:
                    return scope, inner, section
        raise ValueError(f"Unknown section_id: {section_id}")

    def _parse_sections(self, inner_html: str, scope: str) -> list[SectionMatch]:
        headings = []
        for match in HEADING_PATTERN.finditer(inner_html):
            level = int(match.group("level"))
            title = self._html_to_text(match.group("title"))
            section_id = self._section_id_for(scope, title)
            headings.append((match, level, title, section_id))

        sections: list[SectionMatch] = []
        for index, (match, level, title, section_id) in enumerate(headings):
            boundary = len(inner_html)
            for next_match, next_level, _, _ in headings[index + 1:]:
                if next_level <= level:
                    boundary = next_match.start()
                    break
            sections.append(
                SectionMatch(
                    scope=scope,
                    level=level,
                    title=title,
                    section_id=section_id,
                    start=match.start(),
                    end=match.end(),
                    body_start=match.end(),
                    body_end=boundary,
                )
            )
        return sections

    def _extract_cost_inputs(self, html_doc: str) -> dict[str, float]:
        values: dict[str, float] = {}
        for key in COST_KEYS:
            pattern = re.compile(rf'<input\b[^>]*value="(?P<value>[^"]*)"[^>]*data-cost-input="{re.escape(key)}"[^>]*>', re.S)
            match = pattern.search(html_doc)
            if not match:
                raise ValueError(f"Missing cost input: {key}")
            values[key] = float(match.group("value") or 0)
        return values

    def _extract_current_scenario(self, html_doc: str) -> str:
        match = re.search(r'data-calc-block="sprint-cost"[^>]*data-current-scenario="([^"]+)"', html_doc)
        if match and match.group(1) in SCENARIOS:
            return match.group(1)
        match = re.search(r'data-calc-block="sprint-cost"[^>]*data-default-scenario="([^"]+)"', html_doc)
        return match.group(1) if match else "base"

    def _apply_cost_state(self, html_doc: str, values: dict[str, float], scenario: str) -> str:
        breakdown = self.compute_cost_breakdown(values)
        updated = html_doc
        if 'data-current-scenario="' in updated:
            updated = re.sub(
                r'(data-calc-block="sprint-cost"[^>]*data-current-scenario=")([^"]+)(")',
                rf'\g<1>{scenario}\3',
                updated,
                count=1,
            )
        elif 'data-default-scenario="' in updated:
            updated = re.sub(
                r'(data-calc-block="sprint-cost"[^>]*data-default-scenario="[^"]+")',
                rf'\1 data-current-scenario="{scenario}"',
                updated,
                count=1,
            )
        for key, value in values.items():
            updated = self._replace_input_value(updated, key, value)
        for key, value in breakdown.items():
            updated = self._replace_output_value(updated, key, value)
        return updated

    def _replace_input_value(self, html_doc: str, key: str, value: float) -> str:
        tag_pattern = re.compile(rf'<input\b[^>]*data-cost-input="{re.escape(key)}"[^>]*>', re.S)
        tag_match = tag_pattern.search(html_doc)
        if not tag_match:
            raise ValueError(f"Unable to locate input tag for: {key}")
        tag = tag_match.group(0)
        replacement = re.sub(r'value="[^"]*"', f'value="{self._format_number(value)}"', tag, count=1)
        return f"{html_doc[:tag_match.start()]}{replacement}{html_doc[tag_match.end():]}"

    def _replace_output_value(self, html_doc: str, key: str, value: float) -> str:
        pattern = re.compile(rf'(<td data-cost-output="{re.escape(key)}">)(.*?)(</td>)', re.S)
        formatted = self._format_currency(value)
        if key == "totalBurn":
            formatted = f"<strong>{formatted}</strong>"
        return pattern.sub(rf'\1{formatted}\3', html_doc, count=1)

    def _normalize_section_content(self, content: str, content_format: Literal["html", "text"]) -> str:
        stripped = content.strip()
        if not stripped:
            return "\n"
        if content_format == "text":
            blocks = [block.strip() for block in re.split(r"\n\s*\n", stripped) if block.strip()]
            return "\n" + "\n".join(f"  <p>{html.escape(block)}</p>" for block in blocks) + "\n"
        return "\n" + stripped + "\n"

    def _section_id_for(self, scope: str, title: str) -> str:
        return SECTION_IDS.get((scope, title), self._slugify(f"{scope}-{title}"))

    @staticmethod
    def _format_currency(value: float) -> str:
        return f"${round(value):,}"

    @staticmethod
    def _format_number(value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.4f}".rstrip("0").rstrip(".")

    @staticmethod
    def _html_to_text(markup: str) -> str:
        text = TAG_PATTERN.sub(" ", markup)
        text = html.unescape(text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _slugify(value: str) -> str:
        normalized = html.unescape(value).lower()
        normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
        return normalized.strip("-")