const BASE = '/api'

export async function fetchJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

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

// Demo data for when backend isn't connected
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
