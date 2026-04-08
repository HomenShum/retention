#!/usr/bin/env node
/**
 * create-retention-app — Scaffold a retention.sh project in 30 seconds.
 *
 * Usage:
 *   npx create-retention-app my-app
 *   npx create-retention-app my-app --team K7XM2P
 *
 * Creates:
 *   my-app/
 *     .mcp.json          — MCP config (auto-generated token)
 *     .claude/rules/retention.md — QA rules for Claude Code
 *     package.json        — Project with dev server
 *     src/index.html      — Simple app to QA-test
 *     README.md           — Quick start guide
 */

const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const args = process.argv.slice(2);
const projectName = args[0] || 'my-retention-app';
const teamFlag = args.indexOf('--team');
const teamCode = teamFlag !== -1 ? args[teamFlag + 1] : '';

console.log('');
console.log('\x1b[36m\x1b[1m  retention.sh\x1b[0m');
console.log(`  Creating ${projectName}...`);
console.log('');

const dir = path.resolve(projectName);

if (fs.existsSync(dir)) {
  console.log(`\x1b[31m  Directory ${projectName} already exists.\x1b[0m`);
  process.exit(1);
}

fs.mkdirSync(dir, { recursive: true });
fs.mkdirSync(path.join(dir, 'src'), { recursive: true });
fs.mkdirSync(path.join(dir, '.claude', 'rules'), { recursive: true });

// package.json
fs.writeFileSync(path.join(dir, 'package.json'), JSON.stringify({
  name: projectName,
  version: '1.0.0',
  scripts: {
    dev: 'npx serve src -l 3000',
    qa: 'echo "Run in Claude Code: ta.qa_check(url=\'http://localhost:3000\')"',
  },
}, null, 2));

// Simple HTML app to test
fs.writeFileSync(path.join(dir, 'src', 'index.html'), `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${projectName}</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: system-ui, sans-serif; background: #09090b; color: #e2e8f0; min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; }
    h1 { font-size: 2.5rem; margin-bottom: 1rem; }
    p { color: rgba(255,255,255,0.5); margin-bottom: 2rem; }
    a { color: #8b5cf6; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .card { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 2rem; max-width: 500px; width: 100%; }
    button { background: #8b5cf6; color: white; border: none; padding: 0.75rem 1.5rem; border-radius: 8px; font-size: 1rem; cursor: pointer; margin-top: 1rem; }
    button:hover { background: #7c3aed; }
    input { width: 100%; padding: 0.75rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1); background: rgba(255,255,255,0.04); color: white; font-size: 1rem; margin-bottom: 1rem; }
    nav { display: flex; gap: 2rem; margin-bottom: 3rem; }
    nav a { color: rgba(255,255,255,0.5); font-size: 0.875rem; }
    nav a:hover { color: white; }
  </style>
</head>
<body>
  <nav>
    <a href="/">Home</a>
    <a href="/about.html">About</a>
    <a href="/contact.html">Contact</a>
  </nav>
  <div class="card">
    <h1>${projectName}</h1>
    <p>Your app is running. Open Claude Code and run:</p>
    <code style="background:rgba(0,0,0,0.3);padding:0.5rem 1rem;border-radius:6px;font-size:0.875rem;color:#8b5cf6;">
      ta.qa_check(url='http://localhost:3000')
    </code>
    <br>
    <input type="text" placeholder="Search..." aria-label="Search">
    <button>Get Started</button>
  </div>
</body>
</html>
`);

// .gitignore
fs.writeFileSync(path.join(dir, '.gitignore'), `node_modules
.mcp.json
.env
`);

// QA rules
const rulesUrl = 'https://test-studio-xi.vercel.app/retention-config/rules.md';
try {
  const rules = execSync(`curl -sf "${rulesUrl}"`, { encoding: 'utf-8', timeout: 10000 });
  fs.writeFileSync(path.join(dir, '.claude', 'rules', 'retention.md'), rules);
  console.log('  \x1b[32m✓\x1b[0m QA rules installed');
} catch {
  fs.writeFileSync(path.join(dir, '.claude', 'rules', 'retention.md'), '# retention.sh QA Rules\n\nRun ta.qa_check after code changes.\n');
  console.log('  \x1b[33m⚠\x1b[0m QA rules (offline fallback)');
}

// Install retention.sh
console.log('  \x1b[33m→\x1b[0m Installing retention.sh MCP tools...');
try {
  const teamEnv = teamCode ? `RETENTION_TEAM=${teamCode} ` : '';
  execSync(`cd "${dir}" && ${teamEnv}curl -sL retention.sh/install.sh | bash`, {
    stdio: 'inherit',
    timeout: 60000,
  });
} catch {
  console.log('  \x1b[33m⚠\x1b[0m Retention install skipped (run manually: curl -sL retention.sh/install.sh | bash)');
}

// README
fs.writeFileSync(path.join(dir, 'README.md'), `# ${projectName}

Created with [retention.sh](https://retention.sh) — AI Agent Memory for Claude Code.

## Quick Start

\`\`\`bash
cd ${projectName}
npm run dev          # Start dev server on port 3000
\`\`\`

Then in Claude Code:
\`\`\`
ta.qa_check(url='http://localhost:3000')    # Instant QA scan
ta.sitemap(url='http://localhost:3000')     # Interactive site map
ta.ux_audit(url='http://localhost:3000')    # Deep UX audit
\`\`\`

## QA Loop

1. Make code changes
2. \`ta.qa_check\` → see findings
3. Fix → \`ta.diff_crawl\` → verify
4. \`ta.savings.compare\` → see how much cheaper each re-crawl gets

## Team

\`\`\`bash
ta.team.invite    # Generate Slack invite for teammates
\`\`\`
${teamCode ? `\nTeam dashboard: https://retention.sh/memory/team?team=${teamCode}\n` : ''}
`);

console.log('');
console.log(`  \x1b[32m\x1b[1m✓ ${projectName} created.\x1b[0m`);
console.log('');
console.log('  Next:');
console.log(`    cd ${projectName}`);
console.log('    npm run dev');
console.log('    # Then in Claude Code:');
console.log("    ta.qa_check(url='http://localhost:3000')");
console.log('');
