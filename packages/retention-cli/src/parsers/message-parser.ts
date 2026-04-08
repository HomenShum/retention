/**
 * Extracts tool calls and usage data from parsed JSONL messages.
 */

import type { RawMessage, Session } from "./jsonl-reader.js";

export interface ToolCall {
  name: string;
  inputKeys: string[];
  sessionId: string;
  project: string;
}

export interface UsageRecord {
  model: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  sessionId: string;
}

export interface ParsedSession {
  id: string;
  project: string;
  toolCalls: ToolCall[];
  usage: UsageRecord[];
  models: Set<string>;
}

function extractToolCalls(
  msg: RawMessage,
  sessionId: string,
  project: string
): ToolCall[] {
  const calls: ToolCall[] = [];
  const content = msg.message?.content;
  if (!Array.isArray(content)) return calls;

  for (const block of content) {
    if (
      typeof block === "object" &&
      block !== null &&
      "type" in block &&
      (block as Record<string, unknown>).type === "tool_use"
    ) {
      const b = block as Record<string, unknown>;
      const name = typeof b.name === "string" ? b.name : "unknown";
      const input = typeof b.input === "object" && b.input !== null ? b.input : {};
      calls.push({
        name,
        inputKeys: Object.keys(input as Record<string, unknown>).sort(),
        sessionId,
        project,
      });
    }
  }
  return calls;
}

function extractUsage(msg: RawMessage, sessionId: string): UsageRecord | null {
  const u = msg.message?.usage;
  const model = msg.message?.model;
  if (!u || !model) return null;

  return {
    model,
    inputTokens: u.input_tokens ?? 0,
    outputTokens: u.output_tokens ?? 0,
    cacheReadTokens: u.cache_read_input_tokens ?? 0,
    cacheWriteTokens: u.cache_creation_input_tokens ?? 0,
    sessionId,
  };
}

export function parseSession(session: Session): ParsedSession {
  const toolCalls: ToolCall[] = [];
  const usage: UsageRecord[] = [];
  const models = new Set<string>();

  for (const msg of session.messages) {
    // Skip non-message entries
    if (msg.type === "queue-operation") continue;

    // Extract tool calls from assistant messages
    if (msg.message?.role === "assistant") {
      toolCalls.push(
        ...extractToolCalls(msg, session.id, session.project)
      );
    }

    // Extract usage from any message with usage data
    const record = extractUsage(msg, session.id);
    if (record) {
      usage.push(record);
      models.add(record.model);
    }
  }

  return { id: session.id, project: session.project, toolCalls, usage, models };
}
