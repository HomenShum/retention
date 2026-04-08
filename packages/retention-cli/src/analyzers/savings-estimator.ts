/**
 * Estimates potential savings from retention.sh memory/replay.
 * Uses the 85% cap from mcp_savings.py and conservative estimates.
 */

import { MAX_SAVINGS_PCT } from "../constants.js";
import type { CostBreakdown } from "./cost-calculator.js";
import type { RepetitionReport } from "./repetition-detector.js";

export interface SavingsEstimate {
  currentSpendUsd: number;
  projectedSpendUsd: number;
  savingsUsd: number;
  savingsPct: number;
  /** Conservative: only count repeated tool calls as saveable */
  wasteUsd: number;
}

export function estimateSavings(
  cost: CostBreakdown,
  repetition: RepetitionReport
): SavingsEstimate {
  // Conservative: savings come from eliminating repeated tool call overhead.
  // Each repeated call wastes input tokens (the context window re-read).
  // We estimate repeated calls cost proportional to their share of total calls.
  const repeatShare =
    repetition.totalCalls > 0
      ? repetition.repeatedCalls / repetition.totalCalls
      : 0;

  // Waste = proportion of input cost attributable to repeated patterns.
  // Output tokens are still needed (different responses), so we only count input waste.
  const inputTotal = cost.inputUsd + cost.cacheReadUsd + cost.cacheWriteUsd;
  const wasteUsd = inputTotal * repeatShare;

  // Cap savings at MAX_SAVINGS_PCT of total spend
  const maxSavings = cost.totalUsd * MAX_SAVINGS_PCT;
  const savingsUsd = Math.min(wasteUsd, maxSavings);

  const projectedSpendUsd = cost.totalUsd - savingsUsd;
  const savingsPct =
    cost.totalUsd > 0 ? (savingsUsd / cost.totalUsd) * 100 : 0;

  return {
    currentSpendUsd: cost.totalUsd,
    projectedSpendUsd,
    savingsUsd,
    savingsPct,
    wasteUsd,
  };
}
