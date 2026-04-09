import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CheckCircle, XCircle, Loader, AlertTriangle, Clock, Zap, Globe, ChevronDown, ChevronRight } from 'lucide-react'
import { type QAResult, type Finding, DEMO_RESULTS } from '../lib/api'

function StatusBadge({ status }: { status: QAResult['status'] }) {
  if (status === 'pass') return <span className="inline-flex items-center gap-1 text-xs font-medium text-green-400"><CheckCircle className="w-3.5 h-3.5" /> Pass</span>
  if (status === 'fail') return <span className="inline-flex items-center gap-1 text-xs font-medium text-danger"><XCircle className="w-3.5 h-3.5" /> Fail</span>
  return <span className="inline-flex items-center gap-1 text-xs font-medium text-text-muted"><Loader className="w-3.5 h-3.5 animate-spin" /> Running</span>
}

function FindingRow({ f }: { f: Finding }) {
  const color = f.type === 'error' ? 'text-danger' : f.type === 'warning' ? 'text-warning' : 'text-text-muted'
  const Icon = f.type === 'error' ? XCircle : f.type === 'warning' ? AlertTriangle : CheckCircle
  return (
    <div className="flex items-start gap-2 py-2 px-3 text-sm">
      <Icon className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${color}`} />
      <div>
        <span className="text-text-muted text-xs uppercase tracking-wider mr-2">{f.category}</span>
        <span className="text-text-secondary">{f.message}</span>
      </div>
    </div>
  )
}

function ResultCard({ result }: { result: QAResult }) {
  const [open, setOpen] = useState(result.status === 'fail')
  const Chevron = open ? ChevronDown : ChevronRight

  return (
    <div className="rounded-xl border border-border-subtle bg-bg-card overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-4 p-4 text-left hover:bg-white/[0.02] transition-colors"
      >
        <Chevron className="w-4 h-4 text-text-muted shrink-0" />
        <StatusBadge status={result.status} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <Globe className="w-3.5 h-3.5 text-text-muted" />
            <span className="text-sm font-medium truncate">{result.url}</span>
          </div>
        </div>
        <div className="flex items-center gap-4 text-xs text-text-muted shrink-0">
          {result.token_savings_pct && (
            <span className="inline-flex items-center gap-1 text-accent">
              <Zap className="w-3 h-3" /> {result.token_savings_pct}% saved
            </span>
          )}
          <span className="inline-flex items-center gap-1">
            <Clock className="w-3 h-3" /> {(result.duration_ms / 1000).toFixed(1)}s
          </span>
          <span>{result.findings.length} findings</span>
        </div>
      </button>
      {open && result.findings.length > 0 && (
        <div className="border-t border-border-subtle divide-y divide-border-subtle">
          {result.findings.map((f, i) => <FindingRow key={i} f={f} />)}
        </div>
      )}
    </div>
  )
}

function QAResults() {
  const [results, setResults] = useState<QAResult[]>(DEMO_RESULTS)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    fetch('/api/pipeline/results')
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data?.runs) setResults(data.runs) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const passCount = results.filter(r => r.status === 'pass').length
  const failCount = results.filter(r => r.status === 'fail').length
  const runningCount = results.filter(r => r.status === 'running').length

  return (
    <div>
      {/* Metrics bar */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        {[
          { label: 'Total runs', value: results.length, color: 'text-text-primary' },
          { label: 'Passed', value: passCount, color: 'text-green-400' },
          { label: 'Failed', value: failCount, color: 'text-danger' },
          { label: 'Running', value: runningCount, color: 'text-text-muted' },
        ].map(({ label, value, color }) => (
          <div key={label} className="p-4 rounded-xl bg-bg-card border border-border-subtle text-center">
            <div className={`text-2xl font-bold ${color}`}>{value}</div>
            <div className="text-text-muted text-xs mt-1">{label}</div>
          </div>
        ))}
      </div>

      {loading && <div className="text-text-muted text-sm mb-4">Connecting to backend...</div>}

      {/* Results list */}
      <div className="space-y-3">
        {results.map(r => <ResultCard key={r.id} result={r} />)}
      </div>

      {results === DEMO_RESULTS && (
        <div className="mt-6 p-4 rounded-xl border border-accent/20 bg-accent/5 text-sm text-text-secondary">
          Showing demo data. Start the backend (<code className="text-accent text-xs">uvicorn app.main:app</code>) for live results.
        </div>
      )}
    </div>
  )
}

function SiteMap() {
  return (
    <div className="p-8 text-center">
      <Globe className="w-12 h-12 text-text-muted mx-auto mb-4" />
      <h3 className="text-lg font-semibold mb-2">Site Map</h3>
      <p className="text-text-secondary text-sm mb-6 max-w-md mx-auto">
        Run <code className="text-accent text-xs bg-bg-card px-1.5 py-0.5 rounded">retention.sitemap(url='your-app')</code> to generate an interactive site map with screenshots and findings.
      </p>
      <div className="inline-flex items-center gap-3 px-4 py-2.5 rounded-xl bg-bg-card border border-border-muted font-mono text-xs">
        <span className="text-accent">$</span>
        <code>retention.sitemap(url='http://localhost:3000')</code>
      </div>
    </div>
  )
}

function RunHistory() {
  return (
    <div className="p-8 text-center">
      <Clock className="w-12 h-12 text-text-muted mx-auto mb-4" />
      <h3 className="text-lg font-semibold mb-2">Run History</h3>
      <p className="text-text-secondary text-sm max-w-md mx-auto">
        Historical QA runs with before/after comparisons and token savings tracking.
        Connect the backend to see live history.
      </p>
    </div>
  )
}

function Team() {
  return (
    <div className="p-8 text-center">
      <svg className="w-12 h-12 text-text-muted mx-auto mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z" />
      </svg>
      <h3 className="text-lg font-semibold mb-2">Team Memory</h3>
      <p className="text-text-secondary text-sm mb-6 max-w-md mx-auto">
        Share trajectory memory across your team. One person crawls, everyone benefits.
      </p>
      <div className="inline-flex items-center gap-3 px-4 py-2.5 rounded-xl bg-bg-card border border-border-muted font-mono text-xs">
        <span className="text-accent">$</span>
        <code>retention.team.invite</code>
      </div>
    </div>
  )
}

export function Dashboard() {
  const [params] = useSearchParams()
  const tab = params.get('tab') || 'results'

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-xl font-bold">QA Dashboard</h1>
        <p className="text-text-muted text-sm mt-1">Real-time QA results from retention.sh workflow judge</p>
      </div>

      {tab === 'results' && <QAResults />}
      {tab === 'sitemap' && <SiteMap />}
      {tab === 'history' && <RunHistory />}
      {tab === 'team' && <Team />}
    </div>
  )
}
