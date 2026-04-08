/**
 * JSON output for retention analyze --json
 */

import type { CostBreakdown } from "../analyzers/cost-calculator.js";
import type { RepetitionReport } from "../analyzers/repetition-detector.js";
import type { SavingsEstimate } from "../analyzers/savings-estimator.js";

export function formatJson(opts: {
  sessionCount: number;
  days: number;
  models: string[];
  cost: CostBreakdown;
  repetition: RepetitionReport;
  savings: SavingsEstimate;
}): string {
  return JSON.stringify(
    {
      sessions: opts.sessionCount,
      period_days: opts.days,
      models: opts.models,
      cost: {
        total_usd: round(opts.cost.totalUsd),
        input_usd: round(opts.cost.inputUsd),
        output_usd: round(opts.cost.outputUsd),
        cache_read_usd: round(opts.cost.cacheReadUsd),
        cache_write_usd: round(opts.cost.cacheWriteUsd),
        total_tokens: {
          input: opts.cost.totalInputTokens,
          output: opts.cost.totalOutputTokens,
          cache_read: opts.cost.totalCacheReadTokens,
          cache_write: opts.cost.totalCacheWriteTokens,
        },
      },
      repetition: {
        total_calls: opts.repetition.totalCalls,
        unique_patterns: opts.repetition.uniquePatterns,
        repeated_calls: opts.repetition.repeatedCalls,
        repetition_pct: round(opts.repetition.repetitionPct),
        top_tools: opts.repetition.byTool.slice(0, 20).map((t) => ({
          name: t.name,
          total_calls: t.totalCalls,
          repeated_calls: t.repeatedCalls,
        })),
      },
      savings: {
        current_spend_usd: round(opts.savings.currentSpendUsd),
        projected_spend_usd: round(opts.savings.projectedSpendUsd),
        savings_usd: round(opts.savings.savingsUsd),
        savings_pct: round(opts.savings.savingsPct),
        waste_usd: round(opts.savings.wasteUsd),
      },
    },
    null,
    2
  );
}

function round(n: number): number {
  return Math.round(n * 100) / 100;
}
