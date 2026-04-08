/**
 * Monochrome terminal output for retention analyze.
 */

import type { CostBreakdown } from "../analyzers/cost-calculator.js";
import type { RepetitionReport } from "../analyzers/repetition-detector.js";
import type { SavingsEstimate } from "../analyzers/savings-estimator.js";

function usd(n: number): string {
  return "$" + n.toFixed(2);
}

function pct(n: number): string {
  return n.toFixed(1) + "%";
}

function pad(s: string, len: number): string {
  return s.padEnd(len);
}

function rpad(s: string, len: number): string {
  return s.padStart(len);
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

export function formatAnalysis(opts: {
  sessionCount: number;
  days: number;
  models: string[];
  cost: CostBreakdown;
  repetition: RepetitionReport;
  savings: SavingsEstimate;
  topN?: number;
}): string {
  const { sessionCount, days, models, cost, repetition, savings } = opts;
  const topN = opts.topN ?? 10;
  const lines: string[] = [];

  lines.push("");
  lines.push("  retention.sh \u2014 Agent Spend Analysis");
  lines.push("  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500");
  lines.push(
    `  Sessions:     ${rpad(String(sessionCount), 8)}Period: last ${days} days`
  );
  lines.push(
    `  Total spend:  ${rpad(usd(cost.totalUsd), 8)}Models: ${models.join(", ")}`
  );

  // Show billable tokens (input + output), cache separately
  const billableTokens = cost.totalInputTokens + cost.totalOutputTokens;
  lines.push(
    `  Tokens:       ${rpad(formatTokens(billableTokens), 8)}(${formatTokens(cost.totalInputTokens)} in, ${formatTokens(cost.totalOutputTokens)} out)`
  );
  if (cost.totalCacheReadTokens > 0) {
    lines.push(
      `  Cache:        ${rpad(formatTokens(cost.totalCacheReadTokens), 8)}read, ${formatTokens(cost.totalCacheWriteTokens)} written`
    );
  }

  // Top tools by call count
  lines.push("");
  lines.push("  Top tools by usage:");
  const topTools = repetition.byTool.slice(0, topN);
  for (const tool of topTools) {
    const repeatTag =
      tool.repeatedCalls > 0
        ? `  (${tool.repeatedCalls} repeated)`
        : "";
    lines.push(
      `    ${pad(tool.name, 20)} ${rpad(String(tool.totalCalls), 6)} calls${repeatTag}`
    );
  }

  // Repetition summary
  lines.push("");
  lines.push(
    `  Repetition:   ${pct(repetition.repetitionPct)} of tool calls are repeated patterns`
  );
  lines.push(`  Waste:        ${usd(savings.wasteUsd)}`);

  // Savings projection
  lines.push("");
  lines.push("  With retention.sh memory:");
  lines.push(
    `    Projected:  ${rpad(usd(savings.projectedSpendUsd), 8)} Savings: ${usd(savings.savingsUsd)} (${pct(savings.savingsPct)})`
  );

  lines.push("");
  lines.push("  Try it: curl -sL retention.sh/install.sh | bash");
  lines.push("");

  return lines.join("\n");
}
