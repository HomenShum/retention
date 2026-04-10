/**
 * `retention init` — Generate MCP config for Claude Code, Cursor, or Codex.
 *
 * Detects the user's editor and writes the correct config file.
 * No manual prompts needed — auto-detects and writes.
 *
 * Usage:
 *   npx retention init              # auto-detect
 *   npx retention init --claude     # force Claude Code
 *   npx retention init --cursor     # force Cursor
 *   npx retention init --codex      # force Codex
 */

import fs from "node:fs";
import path from "node:path";

function color(text: string, code: number): string {
  if (!process.stdout.isTTY) return text;
  return `\x1b[${code}m${text}\x1b[0m`;
}

const green = (t: string) => color(t, 32);
const dim = (t: string) => color(t, 2);
const bold = (t: string) => color(t, 1);

interface MCPConfig {
  mcpServers: {
    retention: {
      command: string;
      args: string[];
      env?: Record<string, string>;
    };
  };
}

function buildConfig(): MCPConfig {
  return {
    mcpServers: {
      retention: {
        command: "npx",
        args: ["-y", "retention-mcp"],
        env: {
          RETENTION_BACKEND:
            process.env.RETENTION_BACKEND ||
            "https://retention-backend.run.app",
        },
      },
    },
  };
}

type Editor = "claude" | "cursor" | "codex";

function detectEditor(): Editor | null {
  // Check for Claude Code
  if (
    fs.existsSync(".claude") ||
    fs.existsSync(path.join(process.env.HOME || process.env.USERPROFILE || "", ".claude"))
  ) {
    return "claude";
  }

  // Check for Cursor
  if (
    fs.existsSync(".cursor") ||
    fs.existsSync(path.join(process.env.HOME || process.env.USERPROFILE || "", ".cursor"))
  ) {
    return "cursor";
  }

  // Check for Codex
  if (
    fs.existsSync(".codex") ||
    fs.existsSync(path.join(process.env.HOME || process.env.USERPROFILE || "", ".codex"))
  ) {
    return "codex";
  }

  return null;
}

function getConfigPath(editor: Editor): string {
  switch (editor) {
    case "claude":
      return ".mcp.json";
    case "cursor":
      return ".cursor/mcp.json";
    case "codex":
      return ".codex/mcp.json";
  }
}

function getEditorName(editor: Editor): string {
  switch (editor) {
    case "claude":
      return "Claude Code";
    case "cursor":
      return "Cursor";
    case "codex":
      return "Codex";
  }
}

export async function init(args: string[]): Promise<void> {
  let editor: Editor | null = null;

  if (args.includes("--claude")) editor = "claude";
  else if (args.includes("--cursor")) editor = "cursor";
  else if (args.includes("--codex")) editor = "codex";
  else editor = detectEditor();

  if (!editor) {
    console.log(bold("retention.sh — MCP Config Generator\n"));
    console.log("Could not auto-detect your editor. Use one of:\n");
    console.log(`  ${green("retention init --claude")}   # Claude Code`);
    console.log(`  ${green("retention init --cursor")}   # Cursor`);
    console.log(`  ${green("retention init --codex")}    # Codex`);
    console.log();
    console.log(
      dim("Or manually add to your .mcp.json:")
    );
    console.log(dim(JSON.stringify(buildConfig(), null, 2)));
    return;
  }

  const configPath = getConfigPath(editor);
  const editorName = getEditorName(editor);
  const config = buildConfig();

  // Merge with existing config if present
  let existing: Record<string, unknown> = {};
  if (fs.existsSync(configPath)) {
    try {
      existing = JSON.parse(fs.readFileSync(configPath, "utf-8"));
    } catch {
      // Overwrite if corrupt
    }
  }

  const merged = {
    ...existing,
    mcpServers: {
      ...(existing.mcpServers as Record<string, unknown> || {}),
      ...config.mcpServers,
    },
  };

  // Ensure parent dir exists
  const dir = path.dirname(configPath);
  if (dir !== ".") {
    fs.mkdirSync(dir, { recursive: true });
  }

  fs.writeFileSync(configPath, JSON.stringify(merged, null, 2) + "\n");

  console.log();
  console.log(green(`✓ retention.sh MCP added to ${editorName}`));
  console.log(dim(`  Config: ${configPath}`));
  console.log();
  console.log(`Next steps:`);
  console.log(`  1. Restart ${editorName}`);
  console.log(
    `  2. Run: ${green('retention.qa_check(url="http://localhost:3000")')}`
  );
  console.log();
  console.log(
    dim("Tools available: retention.qa_check, retention.sitemap, retention.diff_crawl")
  );
  console.log(
    dim("               retention.start_workflow, retention.team.invite")
  );
  console.log();
}
