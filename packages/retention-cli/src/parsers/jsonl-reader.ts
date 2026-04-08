/**
 * Reads Claude Code JSONL transcripts from ~/.claude/projects/.
 * Each .jsonl file is one conversation session.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, basename } from "node:path";
import { homedir } from "node:os";

export interface RawMessage {
  type?: string;
  message?: {
    role?: string;
    model?: string;
    content?: unknown;
    usage?: {
      input_tokens?: number;
      output_tokens?: number;
      cache_read_input_tokens?: number;
      cache_creation_input_tokens?: number;
    };
  };
}

export interface Session {
  id: string;
  project: string;
  filePath: string;
  messages: RawMessage[];
  modifiedAt: number;
}

function findClaudeDir(): string | null {
  const dir = join(homedir(), ".claude");
  try {
    statSync(dir);
    return dir;
  } catch {
    return null;
  }
}

function listProjects(claudeDir: string): string[] {
  const projectsDir = join(claudeDir, "projects");
  try {
    return readdirSync(projectsDir)
      .filter((name) => {
        const full = join(projectsDir, name);
        try {
          return statSync(full).isDirectory();
        } catch {
          return false;
        }
      })
      .map((name) => join(projectsDir, name));
  } catch {
    return [];
  }
}

function parseJsonlFile(filePath: string): RawMessage[] {
  const messages: RawMessage[] = [];
  try {
    const content = readFileSync(filePath, "utf-8");
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        messages.push(JSON.parse(trimmed) as RawMessage);
      } catch {
        // Skip malformed lines
      }
    }
  } catch {
    // Skip unreadable files
  }
  return messages;
}

export function readSessions(options: {
  days?: number;
  projectFilter?: string;
}): Session[] {
  const claudeDir = findClaudeDir();
  if (!claudeDir) return [];

  const cutoff = options.days
    ? Date.now() - options.days * 24 * 60 * 60 * 1000
    : 0;

  const sessions: Session[] = [];
  const projectDirs = listProjects(claudeDir);

  for (const projectDir of projectDirs) {
    const projectName = basename(projectDir);

    if (
      options.projectFilter &&
      !projectName.toLowerCase().includes(options.projectFilter.toLowerCase())
    ) {
      continue;
    }

    let files: string[];
    try {
      files = readdirSync(projectDir).filter((f) => f.endsWith(".jsonl"));
    } catch {
      continue;
    }

    for (const file of files) {
      const filePath = join(projectDir, file);
      try {
        const stat = statSync(filePath);
        if (cutoff && stat.mtimeMs < cutoff) continue;

        const messages = parseJsonlFile(filePath);
        if (messages.length === 0) continue;

        sessions.push({
          id: basename(file, ".jsonl"),
          project: projectName,
          filePath,
          messages,
          modifiedAt: stat.mtimeMs,
        });
      } catch {
        // Skip inaccessible files
      }
    }
  }

  return sessions.sort((a, b) => b.modifiedAt - a.modifiedAt);
}
