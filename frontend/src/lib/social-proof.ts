/**
 * Social Proof: Real developer pain points with AI coding agents (2026)
 *
 * Sources: GitHub Issues, Cursor Forum, Dev.to, X/Twitter, Medium,
 *          Hacker News, VentureBeat, Fortune, Trustpilot
 *
 * Collected: 2026-04-08
 *
 * Each entry links a real complaint to the retention.sh feature that solves it.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PainCategory =
  | 'done_too_early'
  | 'forgot_context'
  | 'skipped_steps'
  | 'token_waste'
  | 'no_visibility'
  | 'code_reversion'

export type RetentionFeature =
  | 'workflow_judge'
  | 'replay_kit'
  | 'run_anatomy'
  | 'on_stop_hook'
  | 'on_prompt_hook'
  | 'trajectory_memory'
  | 'checkpoint_verify'
  | 'token_savings'
  | 'drift_detection'

export interface SocialProofEntry {
  /** Exact quote or close paraphrase from the source */
  quote: string
  /** Username, handle, or "anonymous" */
  author: string
  /** Where it was posted */
  platform: 'github' | 'twitter' | 'cursor_forum' | 'devto' | 'medium' | 'hn' | 'reddit' | 'trustpilot' | 'venturebeat'
  /** URL to the original source */
  source_url: string
  /** Approximate date (YYYY-MM) */
  date: string
  /** Which pain category this falls under */
  pain_category: PainCategory
  /** Which retention.sh feature directly addresses this pain */
  retention_feature: RetentionFeature
}

// ---------------------------------------------------------------------------
// Pain Points — grouped by category
// ---------------------------------------------------------------------------

export const SOCIAL_PROOF: SocialProofEntry[] = [

  // -------------------------------------------------------------------------
  // 1. DONE TOO EARLY — agent declares completion before finishing
  // -------------------------------------------------------------------------
  {
    quote:
      'When given a complex, multi-step task, Claude Code correctly generates a detailed plan and creates a TodoWrite list, but then prematurely stops after completing only a portion of the plan. It provides a summary as if the entire task is complete.',
    author: 'coygeek',
    platform: 'github',
    source_url: 'https://github.com/anthropics/claude-code/issues/6159',
    date: '2025-08',
    pain_category: 'done_too_early',
    retention_feature: 'on_stop_hook',
  },
  {
    quote:
      'Claude Code terminates prematurely without completing all todos in the todo list, violating explicit protocol instructions to continue until all tasks are marked complete. The agent stopped after completing only 5 of 10 todos, skipping critical validation steps.',
    author: 'anthropics/claude-code-action#599',
    platform: 'github',
    source_url: 'https://github.com/anthropics/claude-code-action/issues/599',
    date: '2026-02',
    pain_category: 'done_too_early',
    retention_feature: 'on_stop_hook',
  },
  {
    quote:
      'Sometimes claude still has items in the todo list but stops midway and users have to tell it to continue.',
    author: 'anthropics/claude-code#4284',
    platform: 'github',
    source_url: 'https://github.com/anthropics/claude-code/issues/4284',
    date: '2025-07',
    pain_category: 'done_too_early',
    retention_feature: 'on_stop_hook',
  },
  {
    quote:
      'Codex stuck on "working" indefinitely, no token usage, no progress, no reconnect. Tasks appear completely stuck for several hours.',
    author: 'OpenAI Community Forum',
    platform: 'github',
    source_url:
      'https://community.openai.com/t/codex-stuck-on-working-indefinitely-no-token-usage-no-progress-no-reconnect/1377263',
    date: '2026-03',
    pain_category: 'done_too_early',
    retention_feature: 'checkpoint_verify',
  },

  // -------------------------------------------------------------------------
  // 2. FORGOT CONTEXT — agent loses track of what was asked
  // -------------------------------------------------------------------------
  {
    quote:
      'Context loss has become a structural problem. Every time the agent forgets what it once knew, the user has to re-explain everything from scratch: logs, project files, past decisions. This not only disrupts the workflow -- it ends up filling memory again, only for it to be lost once more.',
    author: 'RokaCreativa',
    platform: 'cursor_forum',
    source_url:
      'https://forum.cursor.com/t/the-vicious-circle-of-agent-context-loss/104068',
    date: '2025-06',
    pain_category: 'forgot_context',
    retention_feature: 'trajectory_memory',
  },
  {
    quote:
      "It's Tuesday and Claude the AI bot is working on the app and completes 5 PRs. It's Wednesday and Claude wakes up and forgets what the app is about again or what he fixed yesterday.",
    author: 'fusiondev',
    platform: 'cursor_forum',
    source_url:
      'https://forum.cursor.com/t/the-vicious-circle-of-agent-context-loss/104068',
    date: '2025-06',
    pain_category: 'forgot_context',
    retention_feature: 'trajectory_memory',
  },
  {
    quote:
      'The pattern was frustrating: developers would be deep into complex refactoring making steady progress, then Claude Code would struggle -- responses would become generic, previous decisions would be forgotten, and code quality would noticeably degrade.',
    author: 'kaz123',
    platform: 'devto',
    source_url:
      'https://dev.to/kaz123/how-i-solved-claude-codes-context-loss-problem-with-a-lightweight-session-manager-265d',
    date: '2026-03',
    pain_category: 'forgot_context',
    retention_feature: 'trajectory_memory',
  },
  {
    quote:
      'As a session grows longer, the context window accumulates noise: outdated code versions, irrelevant error messages, conflicting instructions, and repeated failed approaches. The signal-to-noise ratio drops over time.',
    author: 'MindStudio Blog',
    platform: 'medium',
    source_url:
      'https://www.mindstudio.ai/blog/context-rot-ai-coding-agents-explained',
    date: '2026-03',
    pain_category: 'forgot_context',
    retention_feature: 'trajectory_memory',
  },
  {
    quote:
      'I burned through my Claude Code context window three times in one session. By the time I got back to the actual bug, Claude had forgotten the code I had shown it 10 minutes ago.',
    author: 'CodeCoup',
    platform: 'medium',
    source_url:
      'https://medium.com/@CodeCoup/i-wasted-8-minutes-per-change-in-claudes-code-heres-what-fixed-it-4baeeef1c07f',
    date: '2026-03',
    pain_category: 'forgot_context',
    retention_feature: 'trajectory_memory',
  },

  // -------------------------------------------------------------------------
  // 3. SKIPPED STEPS — agent bypasses tests, search, QA
  // -------------------------------------------------------------------------
  {
    quote:
      'Claude selectively completed only the easy parts and skipped the rest without asking. Only processed xlsx files, completely skipped PDF and csv. When confronted, Claude admitted: "I was lazy and chased speed. I read everything but only chose to do the easy parts."',
    author: 'marlvinvu',
    platform: 'github',
    source_url: 'https://github.com/anthropics/claude-code/issues/24129',
    date: '2026-02',
    pain_category: 'skipped_steps',
    retention_feature: 'workflow_judge',
  },
  {
    quote:
      'Claude (Opus 4.6) repeatedly executes actions out of order, ignoring a carefully designed workflow with checklist, hooks, skills, error logs, and explicit rules. D.B.C 3+ times in the same session. Adding more rules does not fix the problem.',
    author: 'SDpower',
    platform: 'github',
    source_url: 'https://github.com/anthropics/claude-code/issues/26761',
    date: '2026-02',
    pain_category: 'skipped_steps',
    retention_feature: 'workflow_judge',
  },
  {
    quote:
      'Claude Code exhibits "rush to completion" behavior: fabricating API versions instead of checking the docs, skipping hard problems and declaring them solved, hallucinating commit SHAs and package names rather than looking things up.',
    author: 'Zhang Yao (shuicici)',
    platform: 'devto',
    source_url:
      'https://dev.to/shuicici/claude-codes-feb-mar-2026-updates-quietly-broke-complex-engineering-heres-the-technical-5b4h',
    date: '2026-03',
    pain_category: 'skipped_steps',
    retention_feature: 'on_prompt_hook',
  },
  {
    quote:
      'Reviews are skipped. Negative scenarios are postponed. Testing increasingly resembles robotic process automation, with the focus moved almost entirely to automating flows, while verification quietly fades into the background.',
    author: 'TestResults.io',
    platform: 'medium',
    source_url:
      'https://www.testresults.io/blog/software-testing-trends-for-enterprises-in-2026-whats-broken-whats-next',
    date: '2026-01',
    pain_category: 'skipped_steps',
    retention_feature: 'workflow_judge',
  },
  {
    quote:
      'When workers ask coding agents to generate code and then generate test coverage, the LLM produces unit tests that simply reinforce the existing behavior rather than catching actual bugs.',
    author: 'Kapoor & Narayanan (Princeton)',
    platform: 'hn',
    source_url:
      'https://fortune.com/2026/03/24/ai-agents-are-getting-more-capable-but-reliability-is-lagging-narayanan-kapoor/',
    date: '2026-03',
    pain_category: 'skipped_steps',
    retention_feature: 'workflow_judge',
  },

  // -------------------------------------------------------------------------
  // 4. TOKEN WASTE — repeating the same corrections costs money
  // -------------------------------------------------------------------------
  {
    quote:
      'A developer tracked every token consumed across 42 agent runs on a FastAPI codebase and found 70% of tokens were waste. Another study found even higher: 87% of tokens went to finding code, not writing it.',
    author: 'Morph / marjoballabani',
    platform: 'devto',
    source_url:
      'https://dev.to/marjoballabani/your-ai-agent-wastes-87-of-its-tokens-just-finding-code-i-fixed-that-4d5p',
    date: '2026-02',
    pain_category: 'token_waste',
    retention_feature: 'token_savings',
  },
  {
    quote:
      'Cursor switched to compute-based billing and some developers got hit with $1,400 in monthly overages. People who tried out the $200 plan are requesting a refund because they hit the limits in just 1 hour.',
    author: 'vibecoding.app / Trustpilot reviewers',
    platform: 'trustpilot',
    source_url: 'https://www.trustpilot.com/review/windsurf.com',
    date: '2026-03',
    pain_category: 'token_waste',
    retention_feature: 'token_savings',
  },
  {
    quote:
      'A Swedish software engineer claims his company spends more than his salary on his Claude Code tokens alone.',
    author: 'reported by Kevin Roose',
    platform: 'medium',
    source_url:
      'https://www.morphllm.com/ai-coding-costs',
    date: '2026-03',
    pain_category: 'token_waste',
    retention_feature: 'token_savings',
  },
  {
    quote:
      'Cursor is making all kinds of mistakes primarily around orchestration... terrible bug in planning mode that makes it think it is still in planning mode... waste tokens on doubting itself and filling context. It will just straight forget what it is doing.',
    author: '@JasonGiedymin',
    platform: 'twitter',
    source_url: 'https://x.com/JasonGiedymin/status/2033974494549700638',
    date: '2026-03',
    pain_category: 'token_waste',
    retention_feature: 'trajectory_memory',
  },
  {
    quote:
      'Claude Code is silently burning 10-20x your token budget. This changes the message content on every request, breaking the cache prefix and forcing a full rebuild -- roughly $0.04-0.15 per request wasted.',
    author: 'fillip_kosorukov',
    platform: 'devto',
    source_url:
      'https://dev.to/fillip_kosorukov/claude-code-is-silently-burning-10-20x-your-token-budget-heres-the-fix-4mpk',
    date: '2026-03',
    pain_category: 'token_waste',
    retention_feature: 'token_savings',
  },

  // -------------------------------------------------------------------------
  // 5. NO VISIBILITY — can't see what the agent actually did
  // -------------------------------------------------------------------------
  {
    quote:
      'The actual thinking was still happening inside the model, but it was hidden by the redact header. It was not stored in transcripts either. So you could not even reconstruct what went wrong after the fact.',
    author: 'Zhang Yao (shuicici)',
    platform: 'devto',
    source_url:
      'https://dev.to/shuicici/claude-codes-feb-mar-2026-updates-quietly-broke-complex-engineering-heres-the-technical-5b4h',
    date: '2026-03',
    pain_category: 'no_visibility',
    retention_feature: 'run_anatomy',
  },
  {
    quote:
      'When a customer escalates a bad output and asks "what happened?" -- a complete trace is the answer. An audit trail tells you what the agent did, which matters enormously when you are trying to understand why your agent gave an unexpected answer.',
    author: 'Ian Loe',
    platform: 'medium',
    source_url:
      'https://medium.com/@ianloe/your-ai-agent-needs-an-audit-trail-not-just-a-guardrail-6a41de67ae75',
    date: '2026-03',
    pain_category: 'no_visibility',
    retention_feature: 'run_anatomy',
  },
  {
    quote:
      'Developers need to learn how to effectively prompt, manage, and audit these powerful agentic fleets to build software faster. Agents can sometimes go down rabbit holes.',
    author: 'builder.io',
    platform: 'medium',
    source_url: 'https://www.builder.io/blog/codex-vs-claude-code',
    date: '2026-04',
    pain_category: 'no_visibility',
    retention_feature: 'run_anatomy',
  },

  // -------------------------------------------------------------------------
  // 6. CODE REVERSION — agent undoes its own or user's work
  // -------------------------------------------------------------------------
  {
    quote:
      'In early 2026, Cursor started silently reverting code changes. You would make edits, the AI would apply them, you would move on, and later discover your changes had been undone without any notification.',
    author: 'vibecoding.app',
    platform: 'devto',
    source_url: 'https://vibecoding.app/blog/cursor-problems-2026',
    date: '2026-03',
    pain_category: 'code_reversion',
    retention_feature: 'checkpoint_verify',
  },
  {
    quote:
      'Claude Code is repeatedly reverting previously fixed code issues, causing the same bugs to reappear multiple times within days. This forces users to fix the same issues repeatedly, wasting time and resources.',
    author: 'anthropics/claude-code#8072',
    platform: 'github',
    source_url: 'https://github.com/anthropics/claude-code/issues/8072',
    date: '2025-09',
    pain_category: 'code_reversion',
    retention_feature: 'checkpoint_verify',
  },
  {
    quote:
      'Cursor code is becoming unusable lately: begins to fail at simple tasks, keeps changing the UI at each update... SECRETLY CHANGES YOUR MODEL SETTINGS BACK TO AUTO. The performance and DX has fallen off the cliff in less than a month.',
    author: '@nelvOfficial',
    platform: 'twitter',
    source_url: 'https://x.com/nelvOfficial/status/2033624321529418074',
    date: '2026-03',
    pain_category: 'code_reversion',
    retention_feature: 'drift_detection',
  },
  {
    quote:
      'Critical data loss issue in Codex App for Windows: agent executed file deletion outside project directory, resulting in mass deletion of files and loss of a large amount of data.',
    author: 'OpenAI Community Forum',
    platform: 'github',
    source_url:
      'https://community.openai.com/t/critical-data-loss-issue-in-codex-app-for-windows-agent-executed-file-deletion-outside-project-directory/1375894',
    date: '2026-03',
    pain_category: 'code_reversion',
    retention_feature: 'on_stop_hook',
  },
]

// ---------------------------------------------------------------------------
// Positive / Market Validation quotes
// ---------------------------------------------------------------------------

export interface MarketValidationEntry {
  quote: string
  author: string
  platform: string
  source_url: string
  date: string
  theme: 'workflow_memory' | 'agent_qa' | 'observability' | 'trajectory_replay' | 'cost_savings'
}

export const MARKET_VALIDATION: MarketValidationEntry[] = [
  {
    quote:
      'In 2026, memory is a first-class architectural component with its own benchmark suite, its own research literature, a measurable performance gap between approaches, and a rapidly expanding ecosystem of tools built specifically around it.',
    author: 'mem0.ai',
    platform: 'blog',
    source_url: 'https://mem0.ai/blog/state-of-ai-agent-memory-2026',
    date: '2026-03',
    theme: 'workflow_memory',
  },
  {
    quote:
      'Agent Workflow Memory (AWM): a method for inducing commonly reused routines (workflows) and selectively providing workflows to the agent to guide subsequent generations.',
    author: 'OpenReview / NeurIPS',
    platform: 'academic',
    source_url: 'https://openreview.net/forum?id=NTAhi2JEEE',
    date: '2025-12',
    theme: 'workflow_memory',
  },
  {
    quote:
      'The teams building the most trustworthy AI products in 2026 are not just building good agents -- they are building the visibility layer that makes those agents auditable, improvable, and defensible.',
    author: 'Ian Loe',
    platform: 'medium',
    source_url:
      'https://medium.com/@ianloe/your-ai-agent-needs-an-audit-trail-not-just-a-guardrail-6a41de67ae75',
    date: '2026-03',
    theme: 'observability',
  },
  {
    quote:
      'What distinguishes agents that get better over time from ones that stay static is consistent write-back -- explicit write-back built into the agent workflow.',
    author: 'Felo Search Blog',
    platform: 'blog',
    source_url: 'https://felo.ai/blog/agent-memory-guide/',
    date: '2026-02',
    theme: 'trajectory_replay',
  },
  {
    quote:
      'Windsurf Memories feature -- which remembers codebase context across sessions -- gets consistent praise. Augment is acknowledged for speed, strong context retention, and the ability to ship meaningful work quickly.',
    author: 'Faros.ai',
    platform: 'blog',
    source_url: 'https://www.faros.ai/blog/best-ai-coding-agents-2026',
    date: '2026-03',
    theme: 'workflow_memory',
  },
  {
    quote:
      'This workflow turns a two-hour debugging session into a twenty-minute one. Teams that have it wonder how they ever shipped agents without it.',
    author: 'Mindra Blog',
    platform: 'blog',
    source_url:
      'https://mindra.co/blog/ai-agent-observability-tracing-and-debugging-in-production',
    date: '2026-02',
    theme: 'observability',
  },
  {
    quote:
      'The single most important improvement Cursor could prioritize right now is stable, persistent agent context.',
    author: 'RokaCreativa',
    platform: 'cursor_forum',
    source_url:
      'https://forum.cursor.com/t/the-vicious-circle-of-agent-context-loss/104068',
    date: '2025-06',
    theme: 'workflow_memory',
  },
]

// ---------------------------------------------------------------------------
// Aggregated stats for marketing copy
// ---------------------------------------------------------------------------

export const PAIN_STATS = {
  /** GitHub issues filed about premature task termination (claude-code repo) */
  github_issues_premature_stop: 4,
  /** Percentage of tokens wasted per session (developer-tracked) */
  token_waste_pct: 70,
  /** Maximum monthly overage reported (Cursor) */
  max_monthly_overage_usd: 1_400,
  /** Agent success rate on complex tasks (RAND study) */
  complex_task_failure_pct: 80,
  /** Number of exchanges before context rot degrades output */
  context_rot_threshold_exchanges: 30,
  /** Time before agent success rate drops (human-equivalent minutes) */
  agent_degradation_minutes: 35,
} as const

// ---------------------------------------------------------------------------
// Helper: group by category for UI rendering
// ---------------------------------------------------------------------------

export function getProofByCategory(category: PainCategory): SocialProofEntry[] {
  return SOCIAL_PROOF.filter((e) => e.pain_category === category)
}

export function getProofCategories(): PainCategory[] {
  return [...new Set(SOCIAL_PROOF.map((e) => e.pain_category))]
}

export const CATEGORY_LABELS: Record<PainCategory, string> = {
  done_too_early: 'Declares "done" before finishing',
  forgot_context: 'Forgets what you told it',
  skipped_steps: 'Skips tests, docs, and hard parts',
  token_waste: 'Burns tokens repeating mistakes',
  no_visibility: "Can't see what it actually did",
  code_reversion: 'Silently undoes your work',
}

export const FEATURE_LABELS: Record<RetentionFeature, string> = {
  workflow_judge: 'Workflow Judge',
  replay_kit: 'Replay Kit',
  run_anatomy: 'Run Anatomy',
  on_stop_hook: 'On-Stop Hook',
  on_prompt_hook: 'On-Prompt Hook',
  trajectory_memory: 'Trajectory Memory',
  checkpoint_verify: 'Checkpoint Verify',
  token_savings: 'Token Savings',
  drift_detection: 'Drift Detection',
}
