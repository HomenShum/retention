const BASE = '/api'

export async function fetchJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

// ---------------------------------------------------------------------------
// Generic fetch-or-demo helper
// ---------------------------------------------------------------------------
export async function fetchOrDemo<T>(path: string, demoData: T): Promise<{ data: T; isLive: boolean }> {
  try {
    const res = await fetch(`${BASE}${path}`)
    if (!res.ok) throw new Error(res.statusText)
    return { data: await res.json(), isLive: true }
  } catch {
    return { data: demoData, isLive: false }
  }
}

// ---------------------------------------------------------------------------
// Existing types (Dashboard)
// ---------------------------------------------------------------------------
export interface QAResult {
  id: string
  url: string
  status: 'pass' | 'fail' | 'running'
  findings: Finding[]
  timestamp: string
  duration_ms: number
  token_savings_pct?: number
}

export interface Finding {
  type: 'error' | 'warning' | 'info'
  category: string
  message: string
  selector?: string
  screenshot_url?: string
}

export interface SiteMapNode {
  url: string
  title: string
  depth: number
  children: SiteMapNode[]
  findings_count: number
  screenshot_url?: string
}

export interface TeamMember {
  name: string
  joined_at: string
  runs_count: number
  last_active: string
}

// ---------------------------------------------------------------------------
// Workflows
// ---------------------------------------------------------------------------
export interface Workflow {
  id: string
  name: string
  source_model: string
  events_count: number
  compression_pct: number
  captured_at: string
  status: 'active' | 'archived'
}

export const DEMO_WORKFLOWS: Workflow[] = [
  {
    id: 'wf-001',
    name: 'Checkout flow — add to cart through payment',
    source_model: 'claude-sonnet-4-20250514',
    events_count: 14,
    compression_pct: 62,
    captured_at: new Date(Date.now() - 86_400_000).toISOString(),
    status: 'active',
  },
  {
    id: 'wf-002',
    name: 'User registration with email verification',
    source_model: 'gpt-4o',
    events_count: 9,
    compression_pct: 71,
    captured_at: new Date(Date.now() - 172_800_000).toISOString(),
    status: 'active',
  },
  {
    id: 'wf-003',
    name: 'Settings page — update profile and preferences',
    source_model: 'claude-sonnet-4-20250514',
    events_count: 7,
    compression_pct: 58,
    captured_at: new Date(Date.now() - 345_600_000).toISOString(),
    status: 'archived',
  },
]

// ---------------------------------------------------------------------------
// Judge
// ---------------------------------------------------------------------------
export type JudgeVerdict = 'PASS' | 'FAIL' | 'BLOCKED'
export type EventStatus = 'followed' | 'skipped' | 'diverged'

export interface JudgeEvent {
  step: number
  name: string
  status: EventStatus
  expected?: string
  actual?: string
  severity?: 'low' | 'medium' | 'high' | 'critical'
  suggestion?: string
  nudge_count?: number
}

export interface JudgeSession {
  id: string
  workflow_id: string
  workflow_name: string
  verdict: JudgeVerdict
  reason: string
  events: JudgeEvent[]
  model: string
  started_at: string
  duration_ms: number
}

export const DEMO_JUDGE_SESSIONS: JudgeSession[] = [
  {
    id: 'js-001',
    workflow_id: 'wf-001',
    workflow_name: 'Checkout flow — add to cart through payment',
    verdict: 'FAIL',
    reason: 'Agent skipped payment validation step and did not verify order confirmation page.',
    model: 'claude-sonnet-4-20250514',
    started_at: new Date(Date.now() - 120_000).toISOString(),
    duration_ms: 34_200,
    events: [
      { step: 1, name: 'Navigate to product page', status: 'followed' },
      { step: 2, name: 'Add item to cart', status: 'followed' },
      { step: 3, name: 'Open cart drawer', status: 'followed' },
      { step: 4, name: 'Click checkout', status: 'followed' },
      { step: 5, name: 'Fill shipping address', status: 'followed' },
      { step: 6, name: 'Select shipping method', status: 'diverged', expected: 'Select "Standard Shipping"', actual: 'Skipped shipping selection entirely', severity: 'medium', suggestion: 'Add explicit wait for shipping options to load before proceeding.', nudge_count: 1 },
      { step: 7, name: 'Enter payment details', status: 'skipped', expected: 'Fill credit card form', actual: 'Step was never attempted', severity: 'critical', suggestion: 'Payment step is required. Agent may have hit a timeout or form render issue.', nudge_count: 2 },
      { step: 8, name: 'Confirm order', status: 'skipped', expected: 'Click "Place Order" and verify confirmation', actual: 'Step was never attempted', severity: 'critical', suggestion: 'Depends on payment step completing first.' },
    ],
  },
  {
    id: 'js-002',
    workflow_id: 'wf-002',
    workflow_name: 'User registration with email verification',
    verdict: 'PASS',
    reason: 'All 9 steps completed successfully with no divergences.',
    model: 'gpt-4o',
    started_at: new Date(Date.now() - 600_000).toISOString(),
    duration_ms: 18_700,
    events: [
      { step: 1, name: 'Navigate to signup page', status: 'followed' },
      { step: 2, name: 'Fill name field', status: 'followed' },
      { step: 3, name: 'Fill email field', status: 'followed' },
      { step: 4, name: 'Fill password field', status: 'followed' },
      { step: 5, name: 'Accept terms checkbox', status: 'followed' },
      { step: 6, name: 'Click "Create Account"', status: 'followed' },
      { step: 7, name: 'Verify confirmation page', status: 'followed' },
      { step: 8, name: 'Check email for verification link', status: 'followed' },
      { step: 9, name: 'Click verification link', status: 'followed' },
    ],
  },
]

// ---------------------------------------------------------------------------
// Anatomy (Run detail)
// ---------------------------------------------------------------------------
export interface ToolCall {
  id: string
  tool: string
  timestamp: string
  duration_ms: number
  status: 'ok' | 'error'
  input_tokens: number
  output_tokens: number
  detail?: string
}

export interface RunAnatomy {
  id: string
  url: string
  started_at: string
  duration_ms: number
  total_tool_calls: number
  total_cost_usd: number
  total_errors: number
  input_tokens: number
  output_tokens: number
  tool_calls: ToolCall[]
}

export const DEMO_ANATOMY: RunAnatomy = {
  id: 'run-001',
  url: 'http://localhost:3000',
  started_at: new Date(Date.now() - 300_000).toISOString(),
  duration_ms: 12_400,
  total_tool_calls: 18,
  total_cost_usd: 0.042,
  total_errors: 1,
  input_tokens: 14_230,
  output_tokens: 6_810,
  tool_calls: [
    { id: 'tc-01', tool: 'navigate', timestamp: new Date(Date.now() - 300_000).toISOString(), duration_ms: 1200, status: 'ok', input_tokens: 120, output_tokens: 340, detail: 'Loaded http://localhost:3000' },
    { id: 'tc-02', tool: 'screenshot', timestamp: new Date(Date.now() - 298_000).toISOString(), duration_ms: 800, status: 'ok', input_tokens: 80, output_tokens: 2100 },
    { id: 'tc-03', tool: 'find_elements', timestamp: new Date(Date.now() - 296_000).toISOString(), duration_ms: 340, status: 'ok', input_tokens: 200, output_tokens: 580 },
    { id: 'tc-04', tool: 'click', timestamp: new Date(Date.now() - 294_000).toISOString(), duration_ms: 150, status: 'ok', input_tokens: 90, output_tokens: 40, detail: 'Clicked "Add to Cart" button' },
    { id: 'tc-05', tool: 'wait_for_selector', timestamp: new Date(Date.now() - 292_000).toISOString(), duration_ms: 2100, status: 'ok', input_tokens: 60, output_tokens: 20 },
    { id: 'tc-06', tool: 'screenshot', timestamp: new Date(Date.now() - 290_000).toISOString(), duration_ms: 780, status: 'ok', input_tokens: 80, output_tokens: 1900 },
    { id: 'tc-07', tool: 'evaluate_js', timestamp: new Date(Date.now() - 288_000).toISOString(), duration_ms: 120, status: 'ok', input_tokens: 310, output_tokens: 85 },
    { id: 'tc-08', tool: 'click', timestamp: new Date(Date.now() - 286_000).toISOString(), duration_ms: 180, status: 'ok', input_tokens: 90, output_tokens: 40, detail: 'Clicked "Checkout" link' },
    { id: 'tc-09', tool: 'fill_form', timestamp: new Date(Date.now() - 284_000).toISOString(), duration_ms: 450, status: 'ok', input_tokens: 520, output_tokens: 60 },
    { id: 'tc-10', tool: 'screenshot', timestamp: new Date(Date.now() - 282_000).toISOString(), duration_ms: 810, status: 'ok', input_tokens: 80, output_tokens: 1950 },
    { id: 'tc-11', tool: 'click', timestamp: new Date(Date.now() - 280_000).toISOString(), duration_ms: 200, status: 'ok', input_tokens: 90, output_tokens: 40, detail: 'Clicked "Place Order"' },
    { id: 'tc-12', tool: 'wait_for_navigation', timestamp: new Date(Date.now() - 278_000).toISOString(), duration_ms: 3200, status: 'ok', input_tokens: 60, output_tokens: 20 },
    { id: 'tc-13', tool: 'screenshot', timestamp: new Date(Date.now() - 274_000).toISOString(), duration_ms: 790, status: 'ok', input_tokens: 80, output_tokens: 2000 },
    { id: 'tc-14', tool: 'find_elements', timestamp: new Date(Date.now() - 272_000).toISOString(), duration_ms: 280, status: 'ok', input_tokens: 180, output_tokens: 420 },
    { id: 'tc-15', tool: 'evaluate_js', timestamp: new Date(Date.now() - 270_000).toISOString(), duration_ms: 95, status: 'error', input_tokens: 250, output_tokens: 30, detail: 'ReferenceError: orderConfirmation is not defined' },
    { id: 'tc-16', tool: 'read_console', timestamp: new Date(Date.now() - 268_000).toISOString(), duration_ms: 60, status: 'ok', input_tokens: 40, output_tokens: 380 },
    { id: 'tc-17', tool: 'screenshot', timestamp: new Date(Date.now() - 266_000).toISOString(), duration_ms: 800, status: 'ok', input_tokens: 80, output_tokens: 1850 },
    { id: 'tc-18', tool: 'assert_visible', timestamp: new Date(Date.now() - 264_000).toISOString(), duration_ms: 110, status: 'ok', input_tokens: 120, output_tokens: 35 },
  ],
}

// ---------------------------------------------------------------------------
// Benchmark
// ---------------------------------------------------------------------------
export interface BenchmarkTask {
  name: string
  without_tokens: number
  without_time_ms: number
  with_tokens: number
  with_time_ms: number
  savings_pct: number
  verdict: 'pass' | 'fail' | 'skip'
}

export interface BenchmarkResult {
  id: string
  run_at: string
  token_savings_pct: number
  time_savings_pct: number
  completion_rate: number
  first_pass_rate: number
  tasks: BenchmarkTask[]
}

export const DEMO_BENCHMARK: BenchmarkResult = {
  id: 'bench-001',
  run_at: new Date(Date.now() - 3_600_000).toISOString(),
  token_savings_pct: 67,
  time_savings_pct: 54,
  completion_rate: 95,
  first_pass_rate: 89,
  tasks: [
    { name: 'Checkout flow', without_tokens: 4200, without_time_ms: 18000, with_tokens: 1380, with_time_ms: 7200, savings_pct: 67, verdict: 'pass' },
    { name: 'User registration', without_tokens: 2800, without_time_ms: 12000, with_tokens: 840, with_time_ms: 4800, savings_pct: 70, verdict: 'pass' },
    { name: 'Profile update', without_tokens: 1600, without_time_ms: 8000, with_tokens: 640, with_time_ms: 3600, savings_pct: 60, verdict: 'pass' },
    { name: 'Search and filter', without_tokens: 3100, without_time_ms: 14000, with_tokens: 1085, with_time_ms: 6300, savings_pct: 65, verdict: 'pass' },
    { name: 'Password reset', without_tokens: 1900, without_time_ms: 9000, with_tokens: 570, with_time_ms: 3600, savings_pct: 70, verdict: 'pass' },
    { name: 'File upload', without_tokens: 2400, without_time_ms: 11000, with_tokens: 960, with_time_ms: 5500, savings_pct: 60, verdict: 'fail' },
    { name: 'Dashboard navigation', without_tokens: 1200, without_time_ms: 6000, with_tokens: 360, with_time_ms: 2400, savings_pct: 70, verdict: 'pass' },
    { name: 'Settings toggle', without_tokens: 900, without_time_ms: 4000, with_tokens: 315, with_time_ms: 1800, savings_pct: 65, verdict: 'pass' },
  ],
}

// ---------------------------------------------------------------------------
// Compare (Model A vs B)
// ---------------------------------------------------------------------------
export interface ModelRun {
  model: string
  total_tokens: number
  total_cost_usd: number
  total_time_ms: number
  completion_rate: number
  events: ModelEvent[]
}

export interface ModelEvent {
  step: number
  name: string
  tokens: number
  time_ms: number
  status: 'ok' | 'error' | 'skipped'
}

export interface ModelComparison {
  id: string
  run_at: string
  workflow_name: string
  model_a: ModelRun
  model_b: ModelRun
}

export const DEMO_COMPARISON: ModelComparison = {
  id: 'cmp-001',
  run_at: new Date(Date.now() - 1_800_000).toISOString(),
  workflow_name: 'Checkout flow — add to cart through payment',
  model_a: {
    model: 'claude-sonnet-4-20250514',
    total_tokens: 4200,
    total_cost_usd: 0.038,
    total_time_ms: 18_400,
    completion_rate: 100,
    events: [
      { step: 1, name: 'Navigate to product', tokens: 460, time_ms: 1200, status: 'ok' },
      { step: 2, name: 'Add to cart', tokens: 320, time_ms: 800, status: 'ok' },
      { step: 3, name: 'Open cart', tokens: 280, time_ms: 600, status: 'ok' },
      { step: 4, name: 'Checkout', tokens: 510, time_ms: 2400, status: 'ok' },
      { step: 5, name: 'Fill shipping', tokens: 680, time_ms: 3200, status: 'ok' },
      { step: 6, name: 'Select shipping method', tokens: 390, time_ms: 1800, status: 'ok' },
      { step: 7, name: 'Enter payment', tokens: 720, time_ms: 4200, status: 'ok' },
      { step: 8, name: 'Confirm order', tokens: 840, time_ms: 4200, status: 'ok' },
    ],
  },
  model_b: {
    model: 'gpt-4o',
    total_tokens: 5100,
    total_cost_usd: 0.051,
    total_time_ms: 22_800,
    completion_rate: 75,
    events: [
      { step: 1, name: 'Navigate to product', tokens: 580, time_ms: 1600, status: 'ok' },
      { step: 2, name: 'Add to cart', tokens: 410, time_ms: 1100, status: 'ok' },
      { step: 3, name: 'Open cart', tokens: 350, time_ms: 900, status: 'ok' },
      { step: 4, name: 'Checkout', tokens: 620, time_ms: 3000, status: 'ok' },
      { step: 5, name: 'Fill shipping', tokens: 890, time_ms: 4200, status: 'ok' },
      { step: 6, name: 'Select shipping method', tokens: 450, time_ms: 2200, status: 'ok' },
      { step: 7, name: 'Enter payment', tokens: 0, time_ms: 0, status: 'skipped' },
      { step: 8, name: 'Confirm order', tokens: 0, time_ms: 0, status: 'skipped' },
    ],
  },
}

// ---------------------------------------------------------------------------
// Existing demo data (Dashboard)
// ---------------------------------------------------------------------------
export const DEMO_RESULTS: QAResult[] = [
  {
    id: 'run-001',
    url: 'http://localhost:3000',
    status: 'pass',
    findings: [
      { type: 'warning', category: 'a11y', message: 'Missing alt text on hero image' },
      { type: 'info', category: 'performance', message: 'LCP: 1.2s (good)' },
    ],
    timestamp: new Date(Date.now() - 300_000).toISOString(),
    duration_ms: 12_400,
    token_savings_pct: 67,
  },
  {
    id: 'run-002',
    url: 'http://localhost:3000/checkout',
    status: 'fail',
    findings: [
      { type: 'error', category: 'js-error', message: "TypeError: Cannot read properties of undefined (reading 'map')" },
      { type: 'error', category: 'rendering', message: 'Checkout form not rendered after 5s' },
      { type: 'warning', category: 'a11y', message: 'Form inputs missing labels' },
    ],
    timestamp: new Date(Date.now() - 600_000).toISOString(),
    duration_ms: 8_200,
  },
  {
    id: 'run-003',
    url: 'http://localhost:3000/settings',
    status: 'pass',
    findings: [
      { type: 'info', category: 'a11y', message: 'All form inputs have labels' },
    ],
    timestamp: new Date(Date.now() - 900_000).toISOString(),
    duration_ms: 6_100,
    token_savings_pct: 71,
  },
  {
    id: 'run-004',
    url: 'http://localhost:3000/profile',
    status: 'running',
    findings: [],
    timestamp: new Date().toISOString(),
    duration_ms: 0,
  },
]
