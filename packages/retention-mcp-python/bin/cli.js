#!/usr/bin/env node
"use strict";

const { spawn, execFileSync } = require("child_process");
const path = require("path");

const PKG_DIR = path.resolve(__dirname, "..");

// --- Locate a working Python 3 interpreter -------------------------------- //

function findPython() {
  for (const bin of ["python3", "python"]) {
    try {
      const ver = execFileSync(bin, ["--version"], {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      }).trim();
      const major = parseInt((ver.match(/(\d+)\./) || [])[1], 10);
      if (major >= 3) return bin;
    } catch {
      // not found — try next
    }
  }
  return null;
}

// --- Main ----------------------------------------------------------------- //

const python = findPython();

if (!python) {
  process.stderr.write(
    [
      "",
      "  retention-mcp: Python 3 not found.",
      "",
      "  This relay requires Python 3.10+ with the 'websockets' package.",
      "  Install Python from https://www.python.org/downloads/ and then run:",
      "",
      "    pip install websockets",
      "",
      "",
    ].join("\n")
  );
  process.exit(1);
}

// Determine which script to run:
// - Default: mcp_proxy.py (MCP stdio server — works with Claude Code)
// - --relay flag: main.py (WebSocket relay for emulator forwarding)
const useRelay = process.argv.includes("--relay");
const script = useRelay ? "main.py" : "mcp_proxy.py";

const child = spawn(python, [path.join(PKG_DIR, script)], {
  stdio: "inherit",
  env: {
    ...process.env,
    PYTHONPATH: PKG_DIR + (process.env.PYTHONPATH ? path.delimiter + process.env.PYTHONPATH : ""),
  },
});

// Forward signals so the Python process shuts down cleanly.
for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    child.kill(sig);
  });
}

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
  } else {
    process.exit(code ?? 1);
  }
});
