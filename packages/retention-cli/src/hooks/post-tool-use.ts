/**
 * PostToolUse hook — logs tool calls to ~/.retention/activity.jsonl.
 *
 * Design:
 * - Reads stdin JSON: { tool_name, tool_input, session_id }
 * - Appends one line: { ts, tool, keys, session }
 * - Privacy: only tool name + input key names, never values or output
 * - Performance: single appendFileSync, <5ms target
 *
 * Usage in .claude/settings.json:
 * {
 *   "hooks": {
 *     "PostToolUse": [{
 *       "command": "retention hook",
 *       "timeout_ms": 5000
 *     }]
 *   }
 * }
 */

import { appendFileSync, mkdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

const RETENTION_DIR = join(homedir(), ".retention");
const ACTIVITY_FILE = join(RETENTION_DIR, "activity.jsonl");

let dirEnsured = false;

function ensureDir(): void {
  if (dirEnsured) return;
  if (!existsSync(RETENTION_DIR)) {
    mkdirSync(RETENTION_DIR, { recursive: true });
  }
  dirEnsured = true;
}

interface HookInput {
  tool_name?: string;
  tool_input?: Record<string, unknown>;
  session_id?: string;
}

function logToolCall(input: HookInput): void {
  ensureDir();

  const entry = {
    ts: new Date().toISOString(),
    tool: input.tool_name ?? "unknown",
    keys: input.tool_input ? Object.keys(input.tool_input).sort() : [],
    session: input.session_id ?? "unknown",
  };

  appendFileSync(ACTIVITY_FILE, JSON.stringify(entry) + "\n");
}

export function runHook(): void {
  let data = "";
  process.stdin.setEncoding("utf-8");
  process.stdin.on("data", (chunk) => {
    data += chunk;
  });
  process.stdin.on("end", () => {
    try {
      const input = JSON.parse(data) as HookInput;
      logToolCall(input);
    } catch {
      // Silently ignore malformed input — never break the agent
    }
  });
}
