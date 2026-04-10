/**
 * `retention scan <url>` — Run QA on any URL from the terminal.
 *
 * Calls the retention.sh backend, streams results, prints verdict.
 * Exit code 1 on FAIL (CI-ready from day 1).
 *
 * Usage:
 *   npx retention scan https://myapp.com
 *   npx retention scan http://localhost:3000
 *   npx retention scan https://myapp.com --json
 */

import https from "node:https";
import http from "node:http";

const BACKEND =
  process.env.RETENTION_BACKEND || "https://retention-backend.run.app";
const TOKEN = process.env.RETENTION_MCP_TOKEN || "";

interface Finding {
  type: "error" | "warning" | "info";
  category: string;
  message: string;
}

interface QAResult {
  url: string;
  status: string;
  verdict: string;
  findings: Finding[];
  duration_ms?: number;
  source?: string;
}

function color(text: string, code: number): string {
  if (!process.stdout.isTTY) return text;
  return `\x1b[${code}m${text}\x1b[0m`;
}

const red = (t: string) => color(t, 31);
const green = (t: string) => color(t, 32);
const yellow = (t: string) => color(t, 33);
const dim = (t: string) => color(t, 2);
const bold = (t: string) => color(t, 1);

function printResult(result: QAResult, jsonMode: boolean): void {
  if (jsonMode) {
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  console.log();
  console.log(bold("retention.sh QA Report"));
  console.log(dim("─".repeat(50)));
  console.log(`  URL:      ${result.url}`);
  console.log(
    `  Verdict:  ${
      result.verdict === "PASS"
        ? green("PASS ✓")
        : result.verdict === "BLOCKED"
          ? yellow("BLOCKED ⊘")
          : red("FAIL ✗")
    }`
  );
  if (result.duration_ms) {
    console.log(`  Duration: ${result.duration_ms}ms`);
  }
  if (result.source) {
    console.log(`  Source:   ${dim(result.source)}`);
  }
  console.log();

  const errors = result.findings.filter((f) => f.type === "error");
  const warnings = result.findings.filter((f) => f.type === "warning");
  const infos = result.findings.filter((f) => f.type === "info");

  if (errors.length > 0) {
    console.log(red(`  Errors (${errors.length}):`));
    for (const e of errors) {
      console.log(red(`    ✗ [${e.category}] ${e.message}`));
    }
  }
  if (warnings.length > 0) {
    console.log(yellow(`  Warnings (${warnings.length}):`));
    for (const w of warnings) {
      console.log(yellow(`    ⚠ [${w.category}] ${w.message}`));
    }
  }
  if (infos.length > 0) {
    console.log(dim(`  Info (${infos.length}):`));
    for (const i of infos) {
      console.log(dim(`    ℹ [${i.category}] ${i.message}`));
    }
  }

  console.log();
  console.log(
    dim(`  ${result.findings.length} findings total. Powered by retention.sh`)
  );
  console.log();
}

async function callBackend(url: string): Promise<QAResult> {
  const body = JSON.stringify({ url });
  const endpoint = `${BACKEND}/api/qa/check`;

  return new Promise((resolve, reject) => {
    const lib = endpoint.startsWith("https") ? https : http;
    const req = lib.request(
      endpoint,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}),
        },
        timeout: 120_000,
      },
      (res) => {
        let data = "";
        res.on("data", (chunk: Buffer) => {
          data += chunk.toString();
        });
        res.on("end", () => {
          try {
            resolve(JSON.parse(data));
          } catch {
            reject(new Error(`Invalid response: ${data.slice(0, 200)}`));
          }
        });
      }
    );
    req.on("error", reject);
    req.on("timeout", () => {
      req.destroy();
      reject(new Error("Request timed out after 120s"));
    });
    req.write(body);
    req.end();
  });
}

async function fallbackCheck(url: string): Promise<QAResult> {
  return new Promise((resolve) => {
    const lib = url.startsWith("https") ? https : http;
    const req = lib.get(url, { timeout: 10_000 }, (res) => {
      resolve({
        url,
        status: res.statusCode === 200 ? "pass" : "fail",
        verdict: res.statusCode === 200 ? "PASS" : "FAIL",
        findings: [
          {
            type: "info",
            category: "http",
            message: `HTTP ${res.statusCode}`,
          },
        ],
        source: "fallback (backend unavailable)",
      });
    });
    req.on("error", (e) => {
      resolve({
        url,
        status: "fail",
        verdict: "BLOCKED",
        findings: [
          {
            type: "error",
            category: "connectivity",
            message: `Cannot reach ${url}: ${e.message}`,
          },
        ],
        source: "fallback",
      });
    });
    req.on("timeout", () => {
      req.destroy();
      resolve({
        url,
        status: "fail",
        verdict: "BLOCKED",
        findings: [
          { type: "error", category: "timeout", message: `Timeout reaching ${url}` },
        ],
        source: "fallback",
      });
    });
  });
}

export async function scan(args: string[]): Promise<void> {
  const jsonMode = args.includes("--json");
  const url = args.find((a) => !a.startsWith("--"));

  if (!url) {
    console.error(red("Usage: retention scan <url>"));
    console.error(dim("  Example: retention scan https://myapp.com"));
    console.error(dim("  Example: retention scan http://localhost:3000 --json"));
    process.exit(1);
  }

  // Normalize URL
  const fullUrl = url.startsWith("http") ? url : `https://${url}`;

  console.log(dim(`Scanning ${fullUrl}...`));

  let result: QAResult;
  try {
    result = await callBackend(fullUrl);
  } catch {
    console.log(dim("Backend unavailable, falling back to direct check..."));
    result = await fallbackCheck(fullUrl);
  }

  printResult(result, jsonMode);

  // CI-ready: exit 1 on failure
  if (result.verdict === "FAIL" || result.verdict === "BLOCKED") {
    process.exit(1);
  }
}
