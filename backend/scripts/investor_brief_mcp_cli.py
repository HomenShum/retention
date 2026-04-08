"""CLI bridge for investor brief retrieval + action execution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.investor_brief import InvestorBriefService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Investor brief MCP-compatible CLI")
    parser.add_argument(
        "action",
        choices=[
            "get_state",
            "list_sections",
            "get_section",
            "update_section",
            "set_scenario",
            "set_variables",
            "recalculate",
        ],
    )
    parser.add_argument("--brief-path", help="Override the HTML brief path")
    parser.add_argument("--section-id")
    parser.add_argument("--scenario", choices=["optimistic", "base", "pessimistic"])
    parser.add_argument("--variables", help="JSON object of partial variable overrides")
    parser.add_argument("--content", help="Inline section content")
    parser.add_argument("--content-file", help="Read section content from a file")
    parser.add_argument("--format", dest="content_format", choices=["html", "text"], default="html")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    service = InvestorBriefService(args.brief_path) if args.brief_path else InvestorBriefService()

    if args.content and args.content_file:
        parser.error("Use either --content or --content-file, not both")

    payload: dict[str, object]
    if args.action == "get_state":
        payload = service.get_state()
    elif args.action == "list_sections":
        payload = service.list_sections()
    elif args.action == "get_section":
        if not args.section_id:
            parser.error("--section-id is required for get_section")
        payload = service.get_section(args.section_id)
    elif args.action == "update_section":
        if not args.section_id:
            parser.error("--section-id is required for update_section")
        content = args.content or ""
        if args.content_file:
            content = Path(args.content_file).read_text(encoding="utf-8")
        if not content:
            parser.error("--content or --content-file is required for update_section")
        payload = service.update_section(args.section_id, content, args.content_format)
    elif args.action == "set_scenario":
        if not args.scenario:
            parser.error("--scenario is required for set_scenario")
        payload = service.set_scenario(args.scenario)
    elif args.action == "set_variables":
        variables = json.loads(args.variables or "{}")
        payload = service.set_variables(variables)
    else:
        payload = service.recalculate()

    output = {"action": args.action, "status": "ok", "result": payload}
    print(json.dumps(output, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())