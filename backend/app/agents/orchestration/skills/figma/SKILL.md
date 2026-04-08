# Figma Skill

## What this skill is for

Use this skill when you need **grounded design context from Figma** (file structure, components/styles, variables/design tokens, comments, dev resources, and images) before generating test cases.

## Tools

- `get_figma_snapshot(figma_url?, file_key?, level?, dimensions?, node_ids?)`
  - Returns compact summaries per dimension, plus `ref_id` handles to retrieve the full payload.
- `retrieve_figma_ref(ref_id)`

## Progressive disclosure levels

- `metadata`: fetches `file` (JSON) with small depth (pages list) by default.
- `components`: adds file library outputs (components/component sets/styles).
- `full`: adds variables, comments, dev resources, image fills (and optionally rendered node images when node_ids provided).

## Notes

- Figma REST API endpoints used are documented here:
  - File endpoints: https://developers.figma.com/docs/rest-api/file-endpoints/
  - Components/styles endpoints: https://developers.figma.com/docs/rest-api/component-endpoints/
  - Variables endpoints: https://developers.figma.com/docs/rest-api/variables-endpoints/
  - Comments endpoints: https://developers.figma.com/docs/rest-api/comments-endpoints/
  - Dev resources endpoints: https://developers.figma.com/docs/rest-api/dev-resources-endpoints/
