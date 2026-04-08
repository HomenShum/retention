/**
 * `retention analyze` command.
 * Reads Claude Code JSONL, computes costs, detects repetition, estimates savings.
 */

import { readSessions } from "../parsers/jsonl-reader.js";
import { parseSession } from "../parsers/message-parser.js";
import { calculateCost } from "../analyzers/cost-calculator.js";
import { detectRepetition } from "../analyzers/repetition-detector.js";
import { estimateSavings } from "../analyzers/savings-estimator.js";
import { formatAnalysis } from "../formatters/terminal.js";
import { formatJson } from "../formatters/json.js";
import { DEFAULT_DAYS } from "../constants.js";
import type { ToolCall, UsageRecord } from "../parsers/message-parser.js";

export interface AnalyzeOptions {
  days: number;
  project?: string;
  json: boolean;
  topTools: number;
}

export function runAnalyze(opts: AnalyzeOptions): void {
  const sessions = readSessions({
    days: opts.days,
    projectFilter: opts.project,
  });

  if (sessions.length === 0) {
    console.log("");
    console.log("  retention.sh \u2014 No sessions found");
    console.log("");
    console.log("  No Claude Code sessions found in ~/.claude/projects/");
    console.log(`  (looked back ${opts.days} days)`);
    console.log("");
    console.log("  If you use Claude Code, sessions should appear automatically.");
    console.log("  Try: retention analyze --days 30");
    console.log("");
    process.exitCode = 1;
    return;
  }

  // Parse all sessions
  const allToolCalls: ToolCall[] = [];
  const allUsage: UsageRecord[] = [];
  const allModels = new Set<string>();

  for (const session of sessions) {
    const parsed = parseSession(session);
    allToolCalls.push(...parsed.toolCalls);
    allUsage.push(...parsed.usage);
    for (const m of parsed.models) allModels.add(m);
  }

  // Analyze
  const cost = calculateCost(allUsage);
  const repetition = detectRepetition(allToolCalls);
  const savings = estimateSavings(cost, repetition);

  // Simplify model names for display
  const modelNames = [...allModels].map((m) => {
    // "claude-sonnet-4-6-20260301" → "sonnet-4-6"
    const match = m.match(/claude-(\w+-[\d-]+)/);
    if (match) return match[1];
    return m;
  });

  const data = {
    sessionCount: sessions.length,
    days: opts.days,
    models: modelNames,
    cost,
    repetition,
    savings,
  };

  if (opts.json) {
    console.log(formatJson(data));
  } else {
    console.log(formatAnalysis({ ...data, topN: opts.topTools }));
  }
}
