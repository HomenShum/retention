/**
 * Calculates USD cost from token counts and model pricing.
 */

import {
  MODEL_PRICING,
  CACHE_READ_MULTIPLIER,
  CACHE_WRITE_MULTIPLIER,
} from "../constants.js";
import type { UsageRecord } from "../parsers/message-parser.js";

export interface CostBreakdown {
  totalUsd: number;
  inputUsd: number;
  outputUsd: number;
  cacheReadUsd: number;
  cacheWriteUsd: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  totalCacheReadTokens: number;
  totalCacheWriteTokens: number;
}

function modelPrice(model: string): { input: number; output: number } {
  // Try exact match first
  if (MODEL_PRICING[model]) return MODEL_PRICING[model];

  // Try prefix match (e.g. "claude-sonnet-4-6-20260301" → "claude-sonnet-4-6")
  for (const key of Object.keys(MODEL_PRICING)) {
    if (model.startsWith(key)) return MODEL_PRICING[key];
  }

  // Fallback: use sonnet pricing as conservative middle ground
  return { input: 3.0, output: 15.0 };
}

export function calculateCost(records: UsageRecord[]): CostBreakdown {
  let inputUsd = 0;
  let outputUsd = 0;
  let cacheReadUsd = 0;
  let cacheWriteUsd = 0;
  let totalInputTokens = 0;
  let totalOutputTokens = 0;
  let totalCacheReadTokens = 0;
  let totalCacheWriteTokens = 0;

  for (const r of records) {
    const price = modelPrice(r.model);
    const perM = 1_000_000;

    inputUsd += (r.inputTokens / perM) * price.input;
    outputUsd += (r.outputTokens / perM) * price.output;
    cacheReadUsd +=
      (r.cacheReadTokens / perM) * price.input * CACHE_READ_MULTIPLIER;
    cacheWriteUsd +=
      (r.cacheWriteTokens / perM) * price.input * CACHE_WRITE_MULTIPLIER;

    totalInputTokens += r.inputTokens;
    totalOutputTokens += r.outputTokens;
    totalCacheReadTokens += r.cacheReadTokens;
    totalCacheWriteTokens += r.cacheWriteTokens;
  }

  return {
    totalUsd: inputUsd + outputUsd + cacheReadUsd + cacheWriteUsd,
    inputUsd,
    outputUsd,
    cacheReadUsd,
    cacheWriteUsd,
    totalInputTokens,
    totalOutputTokens,
    totalCacheReadTokens,
    totalCacheWriteTokens,
  };
}
