/**
 * Detects repeated tool call patterns across sessions.
 * A "repeat" = same tool name + same input key structure.
 */

import { createHash } from "node:crypto";
import type { ToolCall } from "../parsers/message-parser.js";

export interface ToolStats {
  name: string;
  totalCalls: number;
  uniquePatterns: number;
  repeatedCalls: number;
}

export interface RepetitionReport {
  totalCalls: number;
  uniquePatterns: number;
  repeatedCalls: number;
  repetitionPct: number;
  byTool: ToolStats[];
}

function callFingerprint(call: ToolCall): string {
  const payload = call.name + ":" + JSON.stringify(call.inputKeys);
  return createHash("sha256").update(payload).digest("hex").slice(0, 16);
}

export function detectRepetition(toolCalls: ToolCall[]): RepetitionReport {
  // Global pattern counts
  const globalPatterns = new Map<string, number>();

  // Per-tool tracking
  const toolPatterns = new Map<string, Map<string, number>>();

  for (const call of toolCalls) {
    const fp = callFingerprint(call);

    globalPatterns.set(fp, (globalPatterns.get(fp) ?? 0) + 1);

    if (!toolPatterns.has(call.name)) {
      toolPatterns.set(call.name, new Map());
    }
    const tm = toolPatterns.get(call.name)!;
    tm.set(fp, (tm.get(fp) ?? 0) + 1);
  }

  const totalCalls = toolCalls.length;
  const uniquePatterns = globalPatterns.size;

  // Repeated = total calls minus unique first-occurrences
  let repeatedCalls = 0;
  for (const count of globalPatterns.values()) {
    if (count > 1) repeatedCalls += count - 1;
  }

  const byTool: ToolStats[] = [];
  for (const [name, patterns] of toolPatterns) {
    let toolTotal = 0;
    let toolRepeated = 0;
    for (const count of patterns.values()) {
      toolTotal += count;
      if (count > 1) toolRepeated += count - 1;
    }
    byTool.push({
      name,
      totalCalls: toolTotal,
      uniquePatterns: patterns.size,
      repeatedCalls: toolRepeated,
    });
  }

  byTool.sort((a, b) => b.totalCalls - a.totalCalls);

  return {
    totalCalls,
    uniquePatterns,
    repeatedCalls,
    repetitionPct: totalCalls > 0 ? (repeatedCalls / totalCalls) * 100 : 0,
    byTool,
  };
}
