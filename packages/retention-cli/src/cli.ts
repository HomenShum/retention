#!/usr/bin/env node
/**
 * retention — See your agent spend in 30 seconds.
 *
 * Commands:
 *   retention analyze [--days N] [--project NAME] [--json] [--top-tools N]
 *   retention hook    (PostToolUse hook, reads stdin)
 */

import { runAnalyze } from "./commands/analyze.js";
import { runHook } from "./hooks/post-tool-use.js";
import { scan } from "./commands/scan.js";
import { init } from "./commands/init.js";
import { DEFAULT_DAYS } from "./constants.js";

function parseArgs(argv: string[]): {
  command: string;
  days: number;
  project?: string;
  json: boolean;
  topTools: number;
} {
  const args = argv.slice(2); // skip node + script
  let command = "analyze"; // default command
  let days = DEFAULT_DAYS;
  let project: string | undefined;
  let json = false;
  let topTools = 10;

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === "analyze" || arg === "hook" || arg === "help" || arg === "scan" || arg === "init") {
      command = arg;
    } else if (arg === "--days" && i + 1 < args.length) {
      days = parseInt(args[++i], 10) || DEFAULT_DAYS;
    } else if (arg === "--project" && i + 1 < args.length) {
      project = args[++i];
    } else if (arg === "--json") {
      json = true;
    } else if (arg === "--top-tools" && i + 1 < args.length) {
      topTools = parseInt(args[++i], 10) || 10;
    } else if (arg === "--help" || arg === "-h") {
      command = "help";
    }
  }

  return { command, days, project, json, topTools };
}

function printHelp(): void {
  console.log(`
  retention.sh — the always-on workflow judge for AI coding agents

  Usage:
    retention scan <url>           QA scan any URL (CI-ready, exit 1 on fail)
    retention scan <url> --json    Machine-readable output
    retention init                 Generate MCP config for Claude Code / Cursor / Codex
    retention init --claude        Force Claude Code config
    retention init --cursor        Force Cursor config
    retention init --codex         Force Codex config
    retention analyze              Analyze last 7 days of agent activity
    retention analyze --days 30    Custom time window
    retention hook                 PostToolUse hook (reads stdin)

  Quick start:
    npx retention scan https://myapp.com
    npx retention init

  More: https://retention.sh
`);
}

function main(): void {
  const opts = parseArgs(process.argv);

  switch (opts.command) {
    case "scan":
      scan(process.argv.slice(3)).catch((e) => {
        console.error(e.message);
        process.exit(1);
      });
      break;
    case "init":
      init(process.argv.slice(3)).catch((e) => {
        console.error(e.message);
        process.exit(1);
      });
      break;
    case "analyze":
      runAnalyze({
        days: opts.days,
        project: opts.project,
        json: opts.json,
        topTools: opts.topTools,
      });
      break;
    case "hook":
      runHook();
      break;
    case "help":
      printHelp();
      break;
    default:
      printHelp();
  }
}

main();
