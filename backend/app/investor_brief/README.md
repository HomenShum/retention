# Investor Brief Controller

This feature turns `tmp/TA_Strategy_Brief_InHouseAgent.html` into a single service-backed control surface shared by:

- the embedded browser controller (`window.taInvestorBriefController`)
- the backend API router (`/api/investor-brief/...`)
- the MCP tool surface (`ta.investor_brief.*`)
- the CLI (`backend/scripts/investor_brief_mcp_cli.py`)
- the OpenAI Agents SDK wrapper (`app.investor_brief.agent`)

## Canonical actions

- `get_state`
- `list_sections`
- `get_section`
- `update_section`
- `set_scenario`
- `set_variables`
- `recalculate`

## Sync rules

1. The HTML file is the persistent artifact.
2. Calculator values are persisted in the input `value=` attributes plus `data-current-scenario`.
3. Section updates preserve the section heading and replace only the body.
4. All surfaces use the same stable `sectionId` registry and calculator keys.

## CLI examples

- `python backend/scripts/investor_brief_mcp_cli.py get_state`
- `python backend/scripts/investor_brief_mcp_cli.py set_scenario --scenario pessimistic`
- `python backend/scripts/investor_brief_mcp_cli.py set_variables --variables '{"team_size": 5, "benchmark_replays": 30}'`
- `python backend/scripts/investor_brief_mcp_cli.py get_section --section-id sprint-cost-model`
- `python backend/scripts/investor_brief_mcp_cli.py update_section --section-id executive-summary --format text --content 'Updated summary.'`