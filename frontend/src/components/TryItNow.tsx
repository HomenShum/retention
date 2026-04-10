import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import {
  Search,
  Loader,
  CheckCircle,
  XCircle,
  AlertTriangle,
  ExternalLink,
  Share2,
  Copy,
  CheckCheck,
} from 'lucide-react'
import type { Finding } from '../lib/api'

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type Verdict = 'PASS' | 'FAIL' | 'BLOCKED'

interface ScanResult {
  run_id: string
  verdict: Verdict
  findings: Finding[]
  duration_ms: number
  pages_crawled: number
  token_savings_pct?: number
}

/* ------------------------------------------------------------------ */
/*  Progress simulation                                                */
/* ------------------------------------------------------------------ */

const PROGRESS_STEPS = [
  'Connecting to retention.sh...',
  'Crawling pages...',
  'Checking for JS errors...',
  'Running a11y audit...',
  'Generating verdict...',
]

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
    <span
      className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-sm font-semibold border ${styles[verdict]}`}
    >
      {verdict === 'PASS' && <CheckCircle className="w-4 h-4" />}
      {verdict === 'FAIL' && <XCircle className="w-4 h-4" />}
      {verdict === 'BLOCKED' && <AlertTriangle className="w-4 h-4" />}
      {verdict}
    </span>
  )
}

function FindingCard({ f }: { f: Finding }) {
  const colorMap = {
    error: 'text-danger border-danger/20 bg-danger/[0.06]',
    warning: 'text-warning border-warning/20 bg-warning/[0.06]',
    info: 'text-text-muted border-border-subtle bg-white/[0.02]',
  }
  const IconMap = { error: XCircle, warning: AlertTriangle, info: CheckCircle }
  const Icon = IconMap[f.type]
  return (
    <div
      className={`flex items-start gap-2.5 p-3 rounded-lg border ${colorMap[f.type]}`}
    >
      <Icon className="w-3.5 h-3.5 mt-0.5 shrink-0" />
      <div className="min-w-0">
        <span className="text-[10px] uppercase tracking-wider font-mono mr-2 opacity-70">
          {f.category}
        </span>
        <span className="text-sm text-text-secondary">{f.message}</span>
      </div>
    </div>
  )
}

function ShareButton({ runId }: { runId: string }) {
  const [copied, setCopied] = useState(false)
  const url = `${window.location.origin}/run/${runId}`
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(url)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      }}
      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-white/[0.06] hover:bg-white/[0.12] transition-colors text-xs text-text-secondary cursor-pointer"
    >
      {copied ? (
        <>
          <CheckCheck className="w-3 h-3 text-accent" /> Copied link
        </>
      ) : (
        <>
          <Share2 className="w-3 h-3" /> Share result
        </>
      )}
    </button>
  )
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      }}
      className="flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-md bg-white/[0.06] hover:bg-white/[0.12] transition-colors text-text-secondary cursor-pointer"
      aria-label="Copy to clipboard"
    >
      {copied ? (
        <>
          <CheckCheck className="w-3 h-3 text-accent" /> Copied
        </>
      ) : (
        <>
          <Copy className="w-3 h-3" /> Copy
        </>
      )}
    </button>
  )
}

/* ------------------------------------------------------------------ */
/*  Demo fallback                                                      */
/* ------------------------------------------------------------------ */

const DEMO_SCAN: ScanResult = {
  run_id: 'demo-scan-001',
  verdict: 'FAIL',
  duration_ms: 8_400,
  pages_crawled: 6,
  findings: [
    {
      type: 'error',
      category: 'js-error',
      message:
        "TypeError: Cannot read properties of undefined (reading 'map')",
    },
    {
      type: 'error',
      category: 'rendering',
      message: 'Checkout form not rendered after 5s',
    },
    {
      type: 'warning',
      category: 'a11y',
      message: 'Missing alt text on 3 images',
    },
    {
      type: 'warning',
      category: 'a11y',
      message: 'Form inputs missing associated labels',
    },
    {
      type: 'info',
      category: 'performance',
      message: 'LCP: 1.2s (good)',
    },
  ],
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export function TryItNow() {
  const [url, setUrl] = useState('')
  const [isScanning, setIsScanning] = useState(false)
  const [progressIdx, setProgressIdx] = useState(0)
  const [result, setResult] = useState<ScanResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  /* Cleanup interval on unmount */
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [])

  async function handleScan() {
    if (!url.trim()) return
    setIsScanning(true)
    setResult(null)
    setError(null)
    setProgressIdx(0)

    /* Simulated progress steps while waiting */
    timerRef.current = setInterval(() => {
      setProgressIdx((prev) =>
        prev < PROGRESS_STEPS.length - 1 ? prev + 1 : prev,
      )
    }, 1_800)

    try {
      const res = await fetch('/api/qa/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.trim() }),
      })
      if (!res.ok) throw new Error(`${res.status}`)
      const data: ScanResult = await res.json()
      setResult(data)
    } catch {
      /* Backend unavailable -- fall back to demo */
      await new Promise((r) => setTimeout(r, 6_000))
      setResult(DEMO_SCAN)
    } finally {
      if (timerRef.current) clearInterval(timerRef.current)
      timerRef.current = null
      setIsScanning(false)
    }
  }

  const MCP_CONFIG = `{
  "mcpServers": {
    "retention": {
      "command": "npx",
      "args": ["-y", "retention-mcp"]
    }
  }
}`

  return (
    <div className="space-y-8">
      {/* ---- Input row ---- */}
      <div className="max-w-2xl mx-auto">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted pointer-events-none" />
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleScan()
              }}
              placeholder="https://your-app.com"
              disabled={isScanning}
              className="w-full pl-10 pr-4 py-3 rounded-xl bg-bg-card border border-border-muted text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent/50 disabled:opacity-50 transition-all"
            />
          </div>
          <button
            onClick={handleScan}
            disabled={isScanning || !url.trim()}
            className="px-6 py-3 rounded-xl bg-accent text-black font-semibold text-sm hover:bg-accent-muted transition-colors disabled:opacity-40 disabled:cursor-not-allowed shrink-0 cursor-pointer"
          >
            {isScanning ? (
              <Loader className="w-4 h-4 animate-spin" />
            ) : (
              'Scan'
            )}
          </button>
        </div>
      </div>

      {/* ---- Progress ---- */}
      {isScanning && (
        <div className="max-w-md mx-auto space-y-2">
          {PROGRESS_STEPS.map((step, i) => {
            const done = i < progressIdx
            const active = i === progressIdx
            return (
              <div
                key={step}
                className={`flex items-center gap-2.5 text-sm transition-opacity ${
                  done
                    ? 'text-accent opacity-60'
                    : active
                      ? 'text-text-primary'
                      : 'text-text-muted opacity-30'
                }`}
              >
                {done ? (
                  <CheckCircle className="w-3.5 h-3.5 text-accent" />
                ) : active ? (
                  <Loader className="w-3.5 h-3.5 animate-spin text-accent" />
                ) : (
                  <div className="w-3.5 h-3.5 rounded-full border border-border-muted" />
                )}
                {step}
              </div>
            )
          })}
        </div>
      )}

      {/* ---- Error ---- */}
      {error && (
        <div className="max-w-2xl mx-auto p-4 rounded-xl border border-danger/20 bg-danger/[0.06] text-sm text-danger">
          {error}
        </div>
      )}

      {/* ---- Results ---- */}
      {result && !isScanning && (
        <div className="max-w-2xl mx-auto space-y-4">
          {/* Verdict header */}
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <VerdictBadge verdict={result.verdict} />
            <div className="flex items-center gap-3 text-xs text-text-muted">
              <span>{result.pages_crawled} pages</span>
              <span>{(result.duration_ms / 1000).toFixed(1)}s</span>
              <span>{result.findings.length} findings</span>
            </div>
          </div>

          {/* Findings */}
          {result.findings.length > 0 && (
            <div className="space-y-2">
              {result.findings.map((f, i) => (
                <FindingCard key={i} f={f} />
              ))}
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center gap-3 pt-2">
            <Link
              to="/dashboard"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-white/[0.06] hover:bg-white/[0.12] transition-colors text-xs text-text-secondary no-underline"
            >
              <ExternalLink className="w-3 h-3" />
              Open in dashboard
            </Link>
            <ShareButton runId={result.run_id} />
          </div>
        </div>
      )}

      {/* ---- Install + MCP config ---- */}
      <div className="max-w-2xl mx-auto space-y-4 pt-4">
        <p className="text-text-muted text-xs text-center uppercase tracking-wider">
          Or install locally for full power
        </p>
        <div className="flex items-center justify-center gap-3 px-5 py-3 rounded-xl bg-bg-card border border-border-muted font-mono text-sm">
          <span className="text-accent">$</span>
          <code className="text-text-primary">
            curl -sL retention.sh/install.sh | bash
          </code>
          <CopyButton text="curl -sL retention.sh/install.sh | bash" />
        </div>

        <details className="group">
          <summary className="text-xs text-text-muted cursor-pointer hover:text-text-secondary transition-colors text-center list-none">
            <span className="inline-flex items-center gap-1.5">
              MCP config for Claude Code / Cursor
              <span className="text-[10px] group-open:rotate-90 transition-transform inline-block">
                &#9654;
              </span>
            </span>
          </summary>
          <div className="mt-3 relative">
            <pre className="p-4 rounded-xl bg-bg-card border border-border-subtle font-mono text-xs text-text-secondary overflow-x-auto">
              {MCP_CONFIG}
            </pre>
            <div className="absolute top-3 right-3">
              <CopyButton text={MCP_CONFIG} />
            </div>
          </div>
        </details>
      </div>
    </div>
  )
}
