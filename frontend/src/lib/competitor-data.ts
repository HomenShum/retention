/**
 * Competitive landscape data for retention.sh landing page
 * Last researched: April 2026
 *
 * Sources cited inline per entry. All pricing and feature claims
 * verified via public web pages and press releases.
 */

// ─── Types ─────────────────────────────────────────────────────────
export interface CompetitorFeatures {
  crossSessionMemory: boolean;
  workflowDetection: boolean;
  stepEnforcement: boolean;
  trajectoryReplay: boolean;
  qaVerification: boolean;
  costTracking: boolean;
  driftDetection: boolean;
  selfHealing: boolean;
  openSource: boolean;
  mcpNative: boolean;
}

export interface Competitor {
  name: string;
  url: string;
  category: "memory-layer" | "coding-agent" | "qa-testing" | "platform-native";
  tagline: string;
  features: CompetitorFeatures;
  pricing: string;
  pricingDetail: string;
  limitation: string;
  benchmarks: string;
  fundingOrScale: string;
  sourceUrls: string[];
}

export interface MarketData {
  metric: string;
  value: string;
  source: string;
  sourceUrl: string;
}

export interface PlatformAnnouncement {
  platform: string;
  announcement: string;
  date: string;
  relevance: string;
  sourceUrl: string;
}

// ─── Competitors ───────────────────────────────────────────────────
export const COMPETITORS: Competitor[] = [
  {
    name: "Supermemory",
    url: "https://supermemory.ai",
    category: "memory-layer",
    tagline: "Universal Memory API for AI apps",
    features: {
      crossSessionMemory: true,
      workflowDetection: false,
      stepEnforcement: false,
      trajectoryReplay: false,
      qaVerification: false,
      costTracking: false,
      driftDetection: false,
      selfHealing: false,
      openSource: true,
      mcpNative: false,
    },
    pricing: "Free / $19/mo Pro / $399/mo Scale",
    pricingDetail:
      "Free: 1M tokens, 10K queries. Pro: 3M tokens, 100K queries. Scale: 80M tokens, 20M queries. Overages: $0.01/1K tokens, $0.10/1K queries.",
    limitation:
      "Pure memory recall — no workflow detection, step enforcement, or QA verification. Stores facts but does not understand or enforce task structure. No trajectory replay or cost tracking.",
    benchmarks:
      "85.4% on LongMemEval (production). 98.6% experimental with 6-agent swarm ensemble (not comparable to standard deployments). Sub-300ms recall.",
    fundingOrScale: "$3M raised. Solo founder from Mumbai.",
    sourceUrls: [
      "https://supermemory.ai/pricing/",
      "https://supermemory.ai/research/",
      "https://blog.supermemory.ai/we-broke-the-frontier-in-agent-memory-introducing-99-sota-memory-system/",
      "https://blog.supermemory.ai/supermemory-vs-zep/",
    ],
  },
  {
    name: "Mem0",
    url: "https://mem0.ai",
    category: "memory-layer",
    tagline: "The Memory Layer for your AI Apps",
    features: {
      crossSessionMemory: true,
      workflowDetection: false,
      stepEnforcement: false,
      trajectoryReplay: false,
      qaVerification: false,
      costTracking: false,
      driftDetection: false,
      selfHealing: false,
      openSource: true,
      mcpNative: false,
    },
    pricing: "Free tier / $19/mo Standard / $249/mo Pro",
    pricingDetail:
      "Free: 10K memories. Standard ($19/mo): vector search only. Pro ($249/mo): graph memory, entity relationships, multi-hop queries. Enterprise: custom. SOC 2 & HIPAA compliant.",
    limitation:
      "Memory-only — no workflow awareness, no step verification, no QA loop. Graph features gated behind expensive Pro tier ($249/mo). Does not publish full evaluation code for independent benchmark verification.",
    benchmarks:
      "~85% on LongMemEval (approximate, not independently verified). Supports 21 framework integrations. Published arxiv:2504.19413.",
    fundingOrScale:
      "$24M raised (Seed + Series A). Led by Kindred Ventures and Basis Set Ventures. GitHub Fund, Y Combinator, Peak XV as participants.",
    sourceUrls: [
      "https://mem0.ai/pricing",
      "https://mem0.ai/series-a",
      "https://mem0.ai/blog/state-of-ai-agent-memory-2026",
      "https://arxiv.org/abs/2504.19413",
    ],
  },
  {
    name: "Zep",
    url: "https://www.getzep.com",
    category: "memory-layer",
    tagline: "Context Engineering & Agent Memory Platform",
    features: {
      crossSessionMemory: true,
      workflowDetection: false,
      stepEnforcement: false,
      trajectoryReplay: false,
      qaVerification: false,
      costTracking: false,
      driftDetection: false,
      selfHealing: false,
      openSource: true,
      mcpNative: false,
    },
    pricing: "Usage-based credits (starts free)",
    pricingDetail:
      "Credit-based: each Episode = 1 credit. Auto-replenishes 20K credits when balance drops below 20%. Unused credits roll over 60 days. Enterprise: dedicated AWS VPC deployment available.",
    limitation:
      "Temporal knowledge graph is powerful for conversation memory but has no workflow detection, step enforcement, or QA verification. No trajectory replay or cost optimization. 63.8% on LongMemEval (GPT-4o) trails competitors.",
    benchmarks:
      "63.8% on LongMemEval (GPT-4o). Sub-200ms latency. Graphiti temporal knowledge graph engine (arxiv:2501.13956). Strong in RRF and MMR reranking.",
    fundingOrScale:
      "Open-source Graphiti engine. Enterprise deployable in customer AWS VPC.",
    sourceUrls: [
      "https://www.getzep.com/pricing/",
      "https://arxiv.org/abs/2501.13956",
      "https://blog.supermemory.ai/supermemory-vs-zep/",
    ],
  },
  {
    name: "Letta (MemGPT)",
    url: "https://letta.com",
    category: "memory-layer",
    tagline: "Platform for building stateful agents with self-improving memory",
    features: {
      crossSessionMemory: true,
      workflowDetection: false,
      stepEnforcement: false,
      trajectoryReplay: false,
      qaVerification: false,
      costTracking: false,
      driftDetection: false,
      selfHealing: true,
      openSource: true,
      mcpNative: false,
    },
    pricing: "Self-hosted free / Cloud $20-200/mo",
    pricingDetail:
      "Self-hosted: fully free, all features. Cloud: $20-200/mo depending on usage. Max plan for token-intensive workloads. ADE (Agent Development Environment) included.",
    limitation:
      "Tiered core/archival memory model is innovative but no workflow-level detection or step enforcement. No QA verification loop. No trajectory replay or cost tracking. Letta Code is memory-first coding agent but no structured QA.",
    benchmarks:
      "Letta Code is #1 model-agnostic open source agent on Terminal-Bench. Context Repositories use git-based versioning for memory.",
    fundingOrScale:
      "Originated from MemGPT research. Conversations API for shared memory across parallel agent sessions.",
    sourceUrls: [
      "https://letta.com/",
      "https://www.letta.com/pricing",
      "https://vectorize.io/articles/mem0-vs-letta",
    ],
  },
  {
    name: "Claude Code (Anthropic)",
    url: "https://www.anthropic.com/claude-code",
    category: "platform-native",
    tagline: "Anthropic's official CLI for Claude with built-in auto-memory",
    features: {
      crossSessionMemory: true,
      workflowDetection: false,
      stepEnforcement: false,
      trajectoryReplay: false,
      qaVerification: false,
      costTracking: true,
      driftDetection: false,
      selfHealing: false,
      openSource: false,
      mcpNative: true,
    },
    pricing: "Included with Claude subscription ($20-200/mo)",
    pricingDetail:
      "No separate cost — uses Claude API tokens. Claude Pro: $20/mo, Max: $100/mo, Team: $30/seat. Auto Memory + CLAUDE.md + Auto Dream are all included.",
    limitation:
      "Memory is file-based (MEMORY.md) with no structured workflow detection, step enforcement, or trajectory replay. Auto Dream prunes stale memories but has no verification or QA loop. No cost optimization for agent runs. Memories are per-project, no cross-project graph.",
    benchmarks:
      "No published memory benchmarks. Auto Dream feature consolidates and prunes memory files. 97M monthly MCP SDK downloads (March 2026).",
    fundingOrScale:
      "Part of Anthropic ($61.5B+ valuation). Agent Skills open standard launched with 75+ connectors. MCP donated to Agentic AI Foundation.",
    sourceUrls: [
      "https://medium.com/@joe.njenga/anthropic-just-added-auto-memory-to-claude-code-memory-md-i-tested-it-0ab8422754d2",
      "https://blog.laozhang.ai/en/posts/claude-code-memory",
      "https://claudefa.st/blog/guide/mechanics/auto-dream",
      "https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills",
    ],
  },
  {
    name: "OpenAI Codex",
    url: "https://openai.com/codex/",
    category: "coding-agent",
    tagline: "Cloud-first coding agent from OpenAI",
    features: {
      crossSessionMemory: false,
      workflowDetection: false,
      stepEnforcement: false,
      trajectoryReplay: false,
      qaVerification: true,
      costTracking: true,
      driftDetection: false,
      selfHealing: true,
      openSource: false,
      mcpNative: false,
    },
    pricing: "Included with ChatGPT Plus ($20/mo) to Pro ($200/mo)",
    pricingDetail:
      "Bundled with ChatGPT: Plus $20/mo, Pro $100/mo (5x limits), Pro+ $200/mo. API: codex-mini $1.50/1M input, $6/1M output (75% cache discount). Business seats dropped to $20/mo. Token-based billing as of April 2026.",
    limitation:
      "Runs in isolated cloud sandbox with internet disabled — cannot access live APIs or test against production. No persistent cross-session memory. Agent Skills are instruction bundles, not verified workflows. No trajectory replay or drift detection.",
    benchmarks:
      "Trained with RL on real-world coding tasks. Iterates on tests until passing. Agent Skills for reusable task bundles. CUA scores 38.1% on OSWorld benchmark.",
    fundingOrScale:
      "Part of OpenAI. Acquired Promptfoo for agent security testing. GPT-5.2-Codex model announced.",
    sourceUrls: [
      "https://openai.com/index/introducing-codex/",
      "https://developers.openai.com/codex/pricing",
      "https://openai.com/index/introducing-upgrades-to-codex/",
      "https://www.csoonline.com/article/4142896/openai-to-acquire-promptfoo-to-strengthen-ai-agent-security-testing.html",
    ],
  },
  {
    name: "Cursor",
    url: "https://cursor.com",
    category: "coding-agent",
    tagline: "AI-first code editor",
    features: {
      crossSessionMemory: false,
      workflowDetection: false,
      stepEnforcement: false,
      trajectoryReplay: false,
      qaVerification: false,
      costTracking: true,
      driftDetection: false,
      selfHealing: false,
      openSource: false,
      mcpNative: true,
    },
    pricing: "Free / $20/mo Pro / $60/mo Pro+ / $200/mo Ultra",
    pricingDetail:
      "Hobby: free (50 slow requests). Pro: $20/mo credit pool. Pro+: $60/mo (background agents, 3x capacity). Ultra: $200/mo ($400 credits). Teams: $40/user/mo. Credit-based billing since June 2025.",
    limitation:
      "No built-in persistent memory across sessions — removed Memories feature in v2.1.x. Rules files only persistent mechanism. No workflow detection, no QA verification, no trajectory replay. Relies on third-party MCP tools (cursor-memory, Recallium) for cross-session memory.",
    benchmarks:
      "No published reliability or memory benchmarks. Background agents (Pro+) run multi-step tasks autonomously.",
    fundingOrScale:
      "~$2.5B+ valuation. Dominant market share in AI-assisted IDE space. MCP support for third-party tools.",
    sourceUrls: [
      "https://cursor.com/pricing",
      "https://www.blockchain-council.org/ai/cursor-ai-track-memory-across-conversations/",
      "https://forum.cursor.com/t/cursor-memory-persistent-searchable-memory-for-cursor-ai/156344",
      "https://felo.ai/blog/claude-code-vs-cursor/",
    ],
  },
  {
    name: "Devin",
    url: "https://devin.ai",
    category: "coding-agent",
    tagline: "AI software engineer",
    features: {
      crossSessionMemory: false,
      workflowDetection: true,
      stepEnforcement: false,
      trajectoryReplay: false,
      qaVerification: true,
      costTracking: true,
      driftDetection: false,
      selfHealing: true,
      openSource: false,
      mcpNative: false,
    },
    pricing: "$20/mo Core + $2.25/ACU",
    pricingDetail:
      "Core: $20/mo + $2.25 per ACU (Agent Compute Unit = ~15 min work). Team: $500/mo (includes 250 ACUs, $2/additional ACU). Enterprise: custom. Dropped from $500/mo entry price with Devin 2.0.",
    limitation:
      "QA is screenshot-based — Devin records itself clicking through the app and sends a video. No structured workflow memory, no trajectory replay, no drift detection. Cannot enforce step-level verification. Usage-based ACU costs can spiral. 67% PR merge rate means 33% still fail.",
    benchmarks:
      "67% PR merge rate (up from 34% in 2024). 4x faster at problem solving. 2x more efficient in resource consumption. Litera case study: +40% test coverage, 93% faster regression cycles. EightSleep: 3x more data features shipped.",
    fundingOrScale:
      "$175M+ raised. Cognition AI (creator). Slack integration for team workflows.",
    sourceUrls: [
      "https://devin.ai/pricing/",
      "https://cognition.ai/blog/devin-annual-performance-review-2025",
      "https://venturebeat.com/programming-development/devin-2-0-is-here-cognition-slashes-price-of-ai-software-engineer-to-20-per-month-from-500",
      "https://github.com/CognitionAI/qa-devin",
    ],
  },
  {
    name: "GitHub Copilot",
    url: "https://github.com/features/copilot",
    category: "platform-native",
    tagline: "AI-powered developer with cross-agent memory",
    features: {
      crossSessionMemory: true,
      workflowDetection: false,
      stepEnforcement: false,
      trajectoryReplay: false,
      qaVerification: false,
      costTracking: false,
      driftDetection: false,
      selfHealing: false,
      openSource: false,
      mcpNative: false,
    },
    pricing: "Free / $10/mo Pro / $39/mo Pro+",
    pricingDetail:
      "Free: limited completions. Pro: $10/mo. Pro+: $39/mo. Business: $19/user/mo. Enterprise: $39/user/mo. Memory on by default for Pro and Pro+ (public preview).",
    limitation:
      "Cross-agent memory auto-discovers repo conventions but memories expire after 28 days. No workflow detection, step enforcement, or QA verification. No trajectory replay. Memory is repo-scoped, not workflow-scoped. Cannot track cost or detect drift.",
    benchmarks:
      "No published memory benchmarks. Cross-agent memory spans coding agent, CLI, and code review surfaces. Memory auto-expires at 28 days.",
    fundingOrScale:
      "Part of Microsoft/GitHub. Public preview as of March 2026.",
    sourceUrls: [
      "https://docs.github.com/en/copilot/concepts/agents/copilot-memory",
      "https://github.blog/ai-and-ml/github-copilot/building-an-agentic-memory-system-for-github-copilot/",
      "https://github.blog/changelog/2026-03-04-copilot-memory-now-on-by-default-for-pro-and-pro-users-in-public-preview/",
    ],
  },
  {
    name: "Momentic",
    url: "https://momentic.ai",
    category: "qa-testing",
    tagline: "AI-powered testing for web & mobile",
    features: {
      crossSessionMemory: false,
      workflowDetection: true,
      stepEnforcement: true,
      trajectoryReplay: true,
      qaVerification: true,
      costTracking: false,
      driftDetection: true,
      selfHealing: true,
      openSource: false,
      mcpNative: false,
    },
    pricing: "Custom (contact sales)",
    pricingDetail:
      "Enterprise pricing, contact for quotes. White-glove onboarding included. YC-backed, $15M Series A.",
    limitation:
      "Focused on app testing, not agent workflow verification. Tests are for frontend UI, not agent step enforcement. No agent memory layer. No cost tracking for agent runs. Does not understand agent decision traces or tool orchestration.",
    benchmarks:
      "2,600 users. Customers include Notion, Xero, Webflow, Retool, Bilt. $15M Series A led by Standard Capital, Dropbox Ventures.",
    fundingOrScale:
      "$15M Series A (Nov 2025). Y Combinator backed.",
    sourceUrls: [
      "https://momentic.ai/",
      "https://techcrunch.com/2025/11/24/momentic-raises-15m-to-automate-software-testing/",
    ],
  },
  {
    name: "QA Wolf",
    url: "https://www.qawolf.com",
    category: "qa-testing",
    tagline: "AI-native managed testing platform",
    features: {
      crossSessionMemory: false,
      workflowDetection: true,
      stepEnforcement: true,
      trajectoryReplay: true,
      qaVerification: true,
      costTracking: false,
      driftDetection: true,
      selfHealing: true,
      openSource: false,
      mcpNative: false,
    },
    pricing: "$60K-250K/year (managed service)",
    pricingDetail:
      "Per-test monthly fee (~$40-44/test/mo). Median annual contract: $90K. Includes test creation, maintenance, unlimited parallel runs, zero-flake guarantee. Playwright/Appium code ownership.",
    limitation:
      "Managed service priced for enterprises — minimum ~$60K/year. Tests web/mobile apps, not agent workflows. No agent memory layer. No trajectory cost optimization. Human-in-the-loop managed model, not self-service developer tool.",
    benchmarks:
      "LLM-as-a-judge assertions. MCP connection validation. Email/SMS testing. Zero-flake guarantee. 100% parallel execution.",
    fundingOrScale:
      "Established enterprise QA company. Customers at scale.",
    sourceUrls: [
      "https://www.qawolf.com/",
      "https://www.vendr.com/marketplace/qa-wolf",
      "https://bug0.com/knowledge-base/qa-wolf-pricing",
    ],
  },
  {
    name: "LangMem (LangChain)",
    url: "https://langchain-ai.github.io/langmem/",
    category: "memory-layer",
    tagline: "SDK for agent long-term memory in LangGraph",
    features: {
      crossSessionMemory: true,
      workflowDetection: false,
      stepEnforcement: false,
      trajectoryReplay: false,
      qaVerification: false,
      costTracking: false,
      driftDetection: false,
      selfHealing: false,
      openSource: true,
      mcpNative: false,
    },
    pricing: "Free (open-source SDK)",
    pricingDetail:
      "Open-source SDK. Works with any storage backend. Native integration with LangGraph Long-term Memory Store. Free to use, pay for your own infrastructure.",
    limitation:
      "SDK-level primitives only — no workflow detection, step enforcement, or QA verification. Requires LangGraph ecosystem. No trajectory replay, cost tracking, or drift detection. Developer must build all orchestration logic.",
    benchmarks:
      "No published benchmarks. Background memory manager for automatic extraction and consolidation. Memory namespacing by user/team/route.",
    fundingOrScale:
      "Part of LangChain ecosystem (raised $25M Series A). Deep Learning AI short course available.",
    sourceUrls: [
      "https://langchain-ai.github.io/langmem/",
      "https://blog.langchain.com/langmem-sdk-launch/",
      "https://www.deeplearning.ai/short-courses/long-term-agentic-memory-with-langgraph/",
    ],
  },
];

// ─── Feature comparison matrix (for landing page grid) ─────────────
export const FEATURE_MATRIX_COLUMNS = [
  { key: "crossSessionMemory", label: "Cross-Session Memory" },
  { key: "workflowDetection", label: "Workflow Detection" },
  { key: "stepEnforcement", label: "Step Enforcement" },
  { key: "trajectoryReplay", label: "Trajectory Replay" },
  { key: "qaVerification", label: "QA Verification" },
  { key: "costTracking", label: "Cost Tracking" },
  { key: "driftDetection", label: "Drift Detection" },
  { key: "selfHealing", label: "Self-Healing" },
] as const;

// ─── Market data ───────────────────────────────────────────────────
export const MARKET_DATA: MarketData[] = [
  {
    metric: "AI Developer Tools Market (2026)",
    value: "$4.5B → $10B by 2030 (17.3% CAGR)",
    source: "Virtue Market Research",
    sourceUrl:
      "https://virtuemarketresearch.com/report/ai-developer-tools-market",
  },
  {
    metric: "AI Code Tools Market (2023-2030)",
    value: "$4.86B → $26.03B (27.1% CAGR)",
    source: "Grand View Research",
    sourceUrl:
      "https://www.grandviewresearch.com/industry-analysis/ai-code-tools-market-report",
  },
  {
    metric: "Cloud AI Developer Services (2025-2026)",
    value: "$16.19B → $19.36B (19.6% CAGR), $55B by 2030",
    source: "Research and Markets / EIN Presswire",
    sourceUrl:
      "https://natlawreview.com/press-releases/cloud-ai-developer-services-market-cagr-be-236-2026-2030-55-billion-industry",
  },
  {
    metric: "AI Agents Market (2024-2030)",
    value: "$5.25B → $7.84B (2025), → $52.62B by 2030 (41% CAGR)",
    source: "Tracxn / AI Agents Directory",
    sourceUrl:
      "https://tracxn.com/d/sectors/agentic-ai/__oyRAfdUfHPjf2oap110Wis0Qg12Gd8DzULlDXPJzrzs",
  },
  {
    metric: "Agentic AI VC Funding (2026 YTD)",
    value: "$2.66B across 44 rounds (142.6% YoY increase)",
    source: "Tracxn",
    sourceUrl:
      "https://tracxn.com/d/sectors/agentic-ai/__oyRAfdUfHPjf2oap110Wis0Qg12Gd8DzULlDXPJzrzs",
  },
  {
    metric: "Total AI VC Investment (2025)",
    value: "$258.7B (61% of all global VC)",
    source: "OECD / Crunchbase",
    sourceUrl:
      "https://www.oecd.org/en/publications/venture-capital-investments-in-artificial-intelligence-through-2025_a13752f5-en/full-report.html",
  },
  {
    metric: "Global AI Spending (2026 est.)",
    value: ">$2 trillion (Gartner), $3.3T by 2029",
    source: "Vention Teams / Gartner",
    sourceUrl: "https://ventionteams.com/solutions/ai/report",
  },
  {
    metric: "MCP SDK Monthly Downloads (March 2026)",
    value: "97 million",
    source: "The New Stack",
    sourceUrl: "https://thenewstack.io/anthropic-march-2026-roundup/",
  },
];

// ─── Platform announcements ────────────────────────────────────────
export const PLATFORM_ANNOUNCEMENTS: PlatformAnnouncement[] = [
  {
    platform: "Anthropic",
    announcement:
      "Agent Skills open standard launched with 75+ commercial partners (Atlassian, Canva, Figma, Notion, Sentry). MCP donated to Agentic AI Foundation. Tool Search and Programmatic Tool Calling in API.",
    date: "2026-03/04",
    relevance:
      "Agent Skills are reusable instruction bundles — NOT verified workflows. No step enforcement, no trajectory replay, no QA loop. This is the gap retention.sh fills.",
    sourceUrl:
      "https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills",
  },
  {
    platform: "OpenAI",
    announcement:
      "Codex switched to token-based billing. Agent Skills for reusable task bundles. Acquiring Promptfoo for agent security testing. GPT-5.2-Codex announced.",
    date: "2026-04",
    relevance:
      "Codex agents run in isolated sandboxes with no internet. Agent Skills are instruction-only, no verification. Promptfoo acquisition signals demand for agent reliability tooling.",
    sourceUrl: "https://openai.com/index/introducing-upgrades-to-codex/",
  },
  {
    platform: "MCP Foundation",
    announcement:
      "Enterprise security roadmap from Anthropic, AWS, Microsoft, and OpenAI at Dev Summit. MCP now stewarded by Agentic AI Foundation (AAIF).",
    date: "2026-04-07",
    relevance:
      "MCP becoming industry standard validates the protocol layer retention.sh builds on. Enterprise security roadmap creates demand for MCP-native reliability tooling.",
    sourceUrl:
      "https://thenewstack.io/mcp-maintainers-enterprise-roadmap/",
  },
  {
    platform: "GitHub",
    announcement:
      "Copilot Memory on by default for Pro/Pro+. Cross-agent memory spans coding agent, CLI, and code review. Memories auto-expire at 28 days.",
    date: "2026-03-04",
    relevance:
      "Cross-agent memory is repo-convention discovery, not workflow verification. 28-day expiry means no long-term trajectory data. No step enforcement or QA loop.",
    sourceUrl:
      "https://github.blog/changelog/2026-03-04-copilot-memory-now-on-by-default-for-pro-and-pro-users-in-public-preview/",
  },
  {
    platform: "Microsoft",
    announcement:
      "Agent Governance Toolkit released as open-source runtime security for AI agents.",
    date: "2026-04-02",
    relevance:
      "Governance/security focus, not workflow reliability or memory. Complementary to retention.sh, not competitive.",
    sourceUrl:
      "https://opensource.microsoft.com/blog/2026/04/02/introducing-the-agent-governance-toolkit-open-source-runtime-security-for-ai-agents/",
  },
  {
    platform: "Fortune / Academia",
    announcement:
      'Research paper "Towards a Science of AI Agent Reliability" highlights that agents are benchmarked on average accuracy, not reliability across 4 dimensions: consistency, robustness, calibration, safety.',
    date: "2026-03-24",
    relevance:
      "Academic validation that reliability is the unsolved problem. Average accuracy masks wildly unreliable agent performance — exactly what retention.sh trajectory enforcement addresses.",
    sourceUrl:
      "https://fortune.com/2026/03/24/ai-agents-are-getting-more-capable-but-reliability-is-lagging-narayanan-kapoor/",
  },
];

// ─── Competitive positioning summary ───────────────────────────────
export const POSITIONING_SUMMARY = {
  gap: "Memory layers (Supermemory, Mem0, Zep, Letta, LangMem) store facts but don't understand workflows. Coding agents (Codex, Cursor, Devin) execute tasks but don't verify completion structurally. QA tools (Momentic, QA Wolf) test apps but not agent behavior. No tool combines workflow memory + step enforcement + trajectory replay + cost optimization in a single MCP-native layer.",

  retentionShDifferentiators: [
    "Workflow-aware memory: detects and remembers multi-step agent workflows, not just facts",
    "Step enforcement: verifies each step completed correctly before proceeding",
    "Trajectory replay: replays proven workflows at 98% token savings",
    "Drift detection: identifies when agent behavior deviates from learned patterns",
    "Cost tracking: measures and optimizes per-run token and time costs",
    "MCP-native: works inside Claude Code, Cursor, Windsurf — no separate tool",
    "Self-healing: auto-updates workflows when app UI changes",
  ],

  marketTiming:
    "MCP at 97M monthly downloads (March 2026). Agentic AI funding up 142.6% YoY. Fortune/academic research validating reliability as the unsolved problem. OpenAI acquiring Promptfoo signals demand. GitHub shipping cross-agent memory signals platform readiness. Market is pre-consolidation — the workflow reliability layer has no incumbent.",
} as const;

// ─── New 2026 entrants worth watching ──────────────────────────────
export const NEW_ENTRANTS_2026 = [
  {
    name: "Interloom",
    funding: "EUR 14.2M Seed (March 2026)",
    focus:
      "AI agent knowledge infrastructure — captures operational knowledge from experts into a memory layer for agents",
    sourceUrl:
      "https://www.eu-startups.com/2026/03/german-startup-interloom-lands-e14-2-million-seed-funding-for-ai-agent-knowledge-infrastructure/",
  },
  {
    name: "MemPalace",
    funding: "Unknown",
    focus:
      "Claims 96.6% on LongMemEval (raw). Memory infrastructure with published benchmark methodology.",
    sourceUrl: "https://www.mempalace.tech/benchmarks",
  },
  {
    name: "OMEGA",
    funding: "Unknown",
    focus:
      "95.4% on LongMemEval. Agent memory system with temporal reasoning. Published benchmark leaderboard.",
    sourceUrl: "https://omegamax.co/benchmarks",
  },
  {
    name: "Mastra",
    funding: "Unknown",
    focus:
      "94.87% on LongMemEval with Observational Memory architecture. Research-oriented memory system.",
    sourceUrl: "https://mastra.ai/research/observational-memory",
  },
] as const;
