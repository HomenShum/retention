import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  Terminal,
  Clock,
  Zap,
  AlertTriangle,
  CheckCircle,
  XCircle,
  ArrowRight,
  Loader,
  Hash,
  Layers,
  Search as SearchIcon,
} from 'lucide-react'
import type { Finding } from '../lib/api'

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type Verdict = 'PASS' | 'FAIL' | 'BLOCKED'

interface RunData {
  id: string
  url: string
  verdict: Verdict
  findings: Finding[]
  timestamp: string
  duration_ms: number
  token_savings_pct: number
  tool_calls: number
}

/* ------------------------------------------------------------------ */
/*  Demo fallback                                                      */
/* ------------------------------------------------------------------ */

const DEMO_RUN: RunData = {
  id: 'demo-run-001',
  url: 'https://example.com',
  verdict: 'FAIL',
  findings: [
    { type: 'error', category: 'js-error', message: "TypeError: Cannot read properties of undefined (reading 'map')" },
    { type: 'error', category: 'rendering', message: 'Checkout form not rendered after 5s' },
    { type: 'warning', category: 'a11y', message: 'Form inputs missing labels (3 instances)' },
    { type: 'warning', category: 'a11y', message: 'Missing alt text on hero image' },
    { type: 'info', category: 'performance', message: 'LCP: 1.2s (good)' },
  ],
  timestamp: new Date().toISOString(),
  duration_ms: 12_400,
  token_savings_pct: 67,
  tool_calls: 24,
}

/* ------------------------------------------------------------------ */
/*  Sub-components                                                     */
/* ------------------------------------------------------------------ */

function VerdictBadge({ verdict }: { verdict: Verdict }) {
  const styles: Record<Verdict, string> = {
    PASS: 'bg-accent/15 text-accent border-accent/30',
    FAIL: 'bg-danger/15 text-danger border-danger/30',
    BLOCKED: 'bg-warning/15 text-warning border-warning/30',
  }
  return (
    <span className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-lg font-bold font-mono border ${styles[verdict]}`}>
      {verdict === 'PASS' && <CheckCircle className="w-5 h-5" />}
      {verdict === 'FAIL' && <XCircle className="w-5 h-5" />}
      {verdict === 'BLOCKED' && <AlertTriangle className="w-5 h-5" />}
      {verdict}
    </span>
  )
}

function StatCard({
  icon: Icon,
  label,
  value,
  accent,
}: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: string
  accent?: boolean
}) {
  return (
    <div className="p-4 rounded-xl bg-bg-card border border-border-subtle text-center">
      <Icon className={`w-5 h-5 mx-auto mb-2 ${accent ? 'text-accent' : 'text-text-muted'}`} />
      <div className={`text-xl font-bold ${accent ? 'text-accent' : 'text-text-primary'}`}>{value}</div>
      <div className="text-text-muted text-xs mt-1">{label}</div>
    </div>
  )
}

function FindingRow({ f }: { f: Finding }) {
  const colorMap = {
    error: 'text-danger border-danger/20 bg-danger/[0.06]',
    warning: 'text-warning border-warning/20 bg-warning/[0.06]',
    info: 'text-text-muted border-border-subtle bg-white/[0.02]',
  }
  const IconMap = { error: XCircle, warning: AlertTriangle, info: CheckCircle }
  const Icon = IconMap[f.type]
  return (
    <div className={`flex items-start gap-2.5 p-3 rounded-lg border ${colorMap[f.type]}`}>
      <Icon className="w-3.5 h-3.5 mt-0.5 shrink-0" />
      <div className="min-w-0">
        <span className="text-[10px] uppercase tracking-wider font-mono mr-2 opacity-70">{f.category}</span>
        <span className="text-sm text-text-secondary">{f.message}</span>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Page                                                               */
/* ------------------------------------------------------------------ */

export function Run() {
  const { id } = useParams<{ id: string }>()
  const [run, setRun] = useState<RunData | null>(null)
  const [isDemo, setIsDemo] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    fetch(`/api/pipeline/results/${id}`)
      .then((r) => {
        if (!r.ok) throw new Error(r.statusText)
        return r.json()
      })
      .then((data: RunData) => {
        setRun(data)
        setIsDemo(false)
      })
      .catch(() => {
        setRun({ ...DEMO_RUN, id: id ?? DEMO_RUN.id })
        setIsDemo(true)
      })
      .finally(() => setLoading(false))
  }, [id])

  if (loading) {
    return (
      <div className="min-h-screen bg-bg-primary flex items-center justify-center">
        <Loader className="w-6 h-6 text-accent animate-spin" />
      </div>
    )
  }

  if (!run) {
    return (
      <div className="min-h-screen bg-bg-primary flex flex-col items-center justify-center gap-4 px-6">
        <p className="text-text-secondary">Run not found.</p>
        <Link to="/" className="text-accent text-sm hover:underline no-underline">Back to home</Link>
      </div>
    )
  }

  const ts = new Date(run.timestamp)

  return (
    <div className="min-h-screen bg-bg-primary">
      {/* Nav */}
      <header className="fixed top-0 w-full z-50 backdrop-blur-md bg-bg-primary/80 border-b border-border-subtle">
        <div className="max-w-4xl mx-auto px-6 h-14 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2 text-accent font-semibold text-sm no-underline">
            <Terminal className="w-4.5 h-4.5" />
            retention.sh
          </Link>
          <div className="flex items-center gap-3 text-xs text-text-muted">
            {isDemo ? (
              <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-warning" /> Demo</span>
            ) : (
              <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-accent" /> Live</span>
            )}
            <Link to="/dashboard" className="px-3 py-1.5 rounded-lg bg-accent text-black font-medium hover:bg-accent-muted transition-colors no-underline text-sm">Dashboard</Link>
          </div>
        </div>
      </header>

      {/* Content */}
      <main className="pt-24 pb-20 px-6">
        <div className="max-w-4xl mx-auto">
          {/* Header */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-8">
            <div>
              <p className="text-[11px] uppercase tracking-[0.2em] text-text-muted mb-2 font-mono">QA Run</p>
              <h1 className="text-2xl font-bold flex items-center gap-2">
                <Hash className="w-5 h-5 text-text-muted" />
                {run.id}
              </h1>
              <p className="text-text-muted text-sm mt-1">
                {ts.toLocaleDateString()} at {ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                {run.url && (
                  <> &mdash; <span className="text-text-secondary">{run.url}</span></>
                )}
              </p>
            </div>
            <VerdictBadge verdict={run.verdict} />
          </div>

          {/* Demo banner */}
          {isDemo && (
            <div className="mb-6 p-3 rounded-xl border border-accent/20 bg-accent/5 text-sm text-text-secondary text-center">
              Showing demo data. Connect the backend for live results.
            </div>
          )}

          {/* Stat cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            <StatCard icon={Clock} label="Duration" value={`${(run.duration_ms / 1000).toFixed(1)}s`} />
            <StatCard icon={Layers} label="Tool Calls" value={String(run.tool_calls)} />
            <StatCard icon={SearchIcon} label="Findings" value={String(run.findings.length)} />
            <StatCard icon={Zap} label="Token Savings" value={`${run.token_savings_pct}%`} accent />
          </div>

          {/* Findings */}
          {run.findings.length > 0 && (
            <div className="mb-10">
              <h2 className="text-sm font-semibold text-text-secondary mb-4">Findings ({run.findings.length})</h2>
              <div className="space-y-2">
                {run.findings.map((f, i) => (
                  <FindingRow key={i} f={f} />
                ))}
              </div>
            </div>
          )}

          {/* CTA */}
          <div className="text-center pt-8 border-t border-border-subtle">
            <p className="text-text-muted text-sm mb-4">See what your AI agent missed.</p>
            <Link
              to="/#start"
              className="inline-flex items-center gap-2 px-6 py-3 rounded-lg bg-accent text-black font-semibold text-sm hover:bg-accent-muted transition-colors no-underline"
            >
              Run your own scan
              <ArrowRight className="w-4 h-4" />
            </Link>
          </div>
        </div>
      </main>
    </div>
  )
}
