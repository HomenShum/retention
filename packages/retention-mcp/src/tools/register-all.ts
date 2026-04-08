/**
 * Register all retention.sh MCP tools on a McpServer instance.
 */
import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import { METHODOLOGY_TOPICS, METHODOLOGY_TOPIC_LIST } from '../knowledge/methodology.js';
import { KNOWN_ISSUES, ISSUE_CATEGORIES } from '../knowledge/known-issues.js';
import { CODEBASE_SECTIONS, CODEBASE_SECTION_LIST } from '../knowledge/codebase-map.js';
import { WORKFLOWS, WORKFLOW_LIST, QUICK_COMMANDS } from '../knowledge/workflows.js';
import { CONVENTIONS, AGENT_CONFIG_REFERENCE } from '../knowledge/conventions.js';

export function registerAllTools(server: McpServer): void {
  // ─── 1. getMethodology ───
  server.registerTool(
    'getMethodology',
    {
      title: 'retention.sh Methodology',
      description: `Get detailed methodology for a retention.sh pattern or technique. Topics: ${METHODOLOGY_TOPIC_LIST.join(', ')}. Use "overview" to see all topics.`,
      inputSchema: {
        topic: z.string().describe(`Topic name. Available: ${METHODOLOGY_TOPIC_LIST.join(', ')}`),
      },
    },
    async ({ topic }) => {
      const content = METHODOLOGY_TOPICS[topic];
      if (!content) {
        return {
          content: [{ type: 'text' as const, text: `Unknown topic "${topic}". Available: ${METHODOLOGY_TOPIC_LIST.join(', ')}` }],
        };
      }
      return { content: [{ type: 'text' as const, text: content }] };
    },
  );

  // ─── 2. getKnownIssues ───
  server.registerTool(
    'getKnownIssues',
    {
      title: 'retention.sh Known Issues',
      description: `Get known issues and their fixes. Categories: ${ISSUE_CATEGORIES.join(', ')}. Omit category for all issues.`,
      inputSchema: {
        category: z.string().optional().describe(`Filter by category: ${ISSUE_CATEGORIES.join(', ')}`),
      },
    },
    async ({ category }) => {
      const issues = category
        ? KNOWN_ISSUES.filter(i => i.category === category)
        : KNOWN_ISSUES;
      if (issues.length === 0) {
        return {
          content: [{ type: 'text' as const, text: `No issues found for category "${category}". Available: ${ISSUE_CATEGORIES.join(', ')}` }],
        };
      }
      const text = issues.map(i =>
        `## [${i.severity.toUpperCase()}] ${i.title}\n` +
        `**ID**: ${i.id}\n` +
        `**Symptom**: ${i.symptom}\n` +
        `**Root Cause**: ${i.rootCause}\n` +
        `**Fix**: ${i.fix}\n` +
        `**File**: ${i.file}` +
        (i.commit ? `\n**Commit**: ${i.commit}` : '')
      ).join('\n\n---\n\n');
      return { content: [{ type: 'text' as const, text: `# Known Issues (${issues.length})\n\n${text}` }] };
    },
  );

  // ─── 3. getCodebaseMap ───
  server.registerTool(
    'getCodebaseMap',
    {
      title: 'retention.sh Codebase Map',
      description: `Get codebase structure and file purposes. Sections: ${CODEBASE_SECTION_LIST.join(', ')}. Use "overview" for full map.`,
      inputSchema: {
        section: z.string().optional().describe(`Section: ${CODEBASE_SECTION_LIST.join(', ')}. Default: overview`),
      },
    },
    async ({ section }) => {
      const key = section || 'overview';
      const content = CODEBASE_SECTIONS[key];
      if (!content) {
        return {
          content: [{ type: 'text' as const, text: `Unknown section "${key}". Available: ${CODEBASE_SECTION_LIST.join(', ')}` }],
        };
      }
      return { content: [{ type: 'text' as const, text: content }] };
    },
  );

  // ─── 4. getWorkflow ───
  server.registerTool(
    'getWorkflow',
    {
      title: 'retention.sh Workflows',
      description: `Get step-by-step workflow. Available: ${WORKFLOW_LIST.join(', ')}.`,
      inputSchema: {
        name: z.string().describe(`Workflow name: ${WORKFLOW_LIST.join(', ')}`),
      },
    },
    async ({ name }) => {
      const content = WORKFLOWS[name];
      if (!content) {
        return {
          content: [{ type: 'text' as const, text: `Unknown workflow "${name}". Available: ${WORKFLOW_LIST.join(', ')}` }],
        };
      }
      return { content: [{ type: 'text' as const, text: content }] };
    },
  );

  // ─── 5. getQuickCommands ───
  server.registerTool(
    'getQuickCommands',
    {
      title: 'retention.sh Quick Commands',
      description: 'Get all quick development commands for backend, frontend, E2E, device, and git.',
    },
    async () => {
      return { content: [{ type: 'text' as const, text: QUICK_COMMANDS }] };
    },
  );

  // ─── 6. getConventions ───
  server.registerTool(
    'getConventions',
    {
      title: 'retention.sh Code Conventions',
      description: 'Get code style guidelines, patterns, and critical rules for Python backend and TypeScript frontend.',
    },
    async () => {
      return { content: [{ type: 'text' as const, text: CONVENTIONS }] };
    },
  );

  // ─── 7. getAgentConfig ───
  server.registerTool(
    'getAgentConfig',
    {
      title: 'retention.sh Agent Configuration',
      description: 'Get full agent configuration reference — models, parallel_tool_calls, reasoning levels, handoffs, and streaming setup.',
    },
    async () => {
      return { content: [{ type: 'text' as const, text: AGENT_CONFIG_REFERENCE }] };
    },
  );
}

