/**
 * Model pricing per 1M tokens (March 2026).
 * Mirrored from backend/app/services/usage_telemetry.py:22-35
 */
export const MODEL_PRICING: Record<string, { input: number; output: number }> = {
  // Anthropic
  "claude-opus-4-6": { input: 15.0, output: 75.0 },
  "claude-sonnet-4-6": { input: 3.0, output: 15.0 },
  "claude-haiku-4-5": { input: 0.8, output: 4.0 },
  // OpenAI
  "gpt-5.4": { input: 2.5, output: 15.0 },
  "gpt-5.4-mini": { input: 0.75, output: 4.5 },
  "gpt-5.4-nano": { input: 0.2, output: 1.25 },
  "gpt-5": { input: 2.0, output: 8.0 },
  "gpt-5-mini": { input: 0.25, output: 1.0 },
  "gpt-5-nano": { input: 0.1, output: 0.4 },
};

/** Cache read = 10% of input price, cache write = 125% of input price */
export const CACHE_READ_MULTIPLIER = 0.1;
export const CACHE_WRITE_MULTIPLIER = 1.25;

/** Max savings cap from trajectory replay (from mcp_savings.py) */
export const MAX_SAVINGS_PCT = 0.85;

/** Default analysis window */
export const DEFAULT_DAYS = 7;
