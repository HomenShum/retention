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
    if (arg === "analyze" || arg === "hook" || arg === "help") {
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
  retention.sh — See your agent spend in 30 seconds

  Usage:
    retention analyze              Analyze last 7 days of agent activity
    retention analyze --days 30    Custom time window
    retention analyze --json       Machine-readable output
    retention analyze --project X  Filter by project name
    retention hook                 PostToolUse hook (reads stdin)

  Setup real-time tracking:
    Add to .claude/settings.json:
    {
      "hooks": {
        "PostToolUse": [{
          "command": "retention hook",
          "timeout_ms": 5000
        }]
      }
    }

  More: https://retention.sh
`);
}

function main(): void {
  const opts = parseArgs(process.argv);

  switch (opts.command) {
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
