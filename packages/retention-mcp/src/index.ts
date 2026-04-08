#!/usr/bin/env node
/**
 * retention.sh MCP Server
 *
 * Domain knowledge, patterns, bug fixes, and workflows for AI agents
 * working on the retention.sh mobile test automation platform.
 *
 * Usage:
 *   npx retention-mcp
 *   claude mcp add retention -- npx -y retention-mcp
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { registerAllTools } from './tools/register-all.js';

const server = new McpServer(
  {
    name: 'retention-mcp',
    version: '1.3.0',
  },
  {
    capabilities: {
      logging: {},
    },
  },
);

// Register all domain knowledge tools
registerAllTools(server);

// Connect via stdio transport (for local process-spawned integrations)
const transport = new StdioServerTransport();
await server.connect(transport);
