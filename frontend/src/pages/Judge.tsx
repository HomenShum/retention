import { useState, useEffect } from 'react'
import { ShieldCheck, ChevronDown, ChevronRight, Loader, AlertTriangle, CheckCircle, XCircle, ArrowRight } from 'lucide-react'
import { fetchOrDemo, type JudgeSession, type JudgeEvent, type JudgeVerdict, type EventStatus, DEMO_JUDGE_SESSIONS } from '../lib/api'
import { LiveBadge } from '../components/LiveBadge'

const VERDICT_STYLES: Record<JudgeVerdict, { bg: string; text: string; border: string }> = {
  PASS: { bg: 'bg-green-500/10', text: 'text-green-400', border: 'border-green-500/20' },
  FAIL: { bg: 'bg-danger/10', text: 'text-danger', border: 'border-danger/20' },
  BLOCKED: { bg: 'bg-warning/10', text: 'text-warning', border: 'border-warning/20' },
}

const EVENT_COLORS: Record<EventStatus, string> = {
  followed: 'bg-green-500',
  skipped: 'bg-danger',
  diverged: 'bg-warning',
}

export function Judge() {
  const [sessions, setSessions] = useState<JudgeSession[]>([])
  const [isLive, setIsLive] = useState(false)
  const [loading, setLoading] = useState(true)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  useEffect(() => {
    fetchOrDemo('/workflow-judge/sessions', DEMO_JUDGE_SESSIONS).then(({ data, isLive: live }) => {
      setSessions(Array.isArray(data) ? data : DEMO_JUDGE_SESSIONS)
      setIsLive(live)
      setLoading(false)
      // Auto-expand first FAIL session
      const fail = (Array.isArray(data) ? data : DEMO_JUDGE_SESSIONS).find((s: JudgeSession) => s.verdict === 'FAIL')
      if (fail) setExpandedId(fail.id)
    })
  }, [])

  return (
    <div className="p-6">
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold">Workflow Judge</h1>
          <p className="text-text-muted text-sm mt-1">Step-by-step verdicts on agent workflow execution</p>
        </div>
        <LiveBadge isLive={isLive} />
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-text-muted text-sm py-12 justify-center">
          <Loader className="w-4 h-4 animate-spin" />
          Connecting to backend...
        </div>
      ) : (
        <div className="space-y-4">
          {sessions.map(session => {
            const expanded = expandedId === session.id
            const vs = VERDICT_STYLES[session.verdict]
            const followedCount = session.events.filter(e => e.status === 'followed').length
            const totalCount = session.events.length

            return (
              <div key={session.id} className="rounded-xl border border-border-subtle bg-bg-card overflow-hidden">
                {/* Session header */}
                <button
                  onClick={() => setExpandedId(expanded ? null : session.id)}
                  className="w-full flex items-center gap-4 p-4 text-left hover:bg-white/[0.02] transition-colors"
                >
                  {expanded ? <ChevronDown className="w-4 h-4 text-text-muted shrink-0" /> : <ChevronRight className="w-4 h-4 text-text-muted shrink-0" />}
                  <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-bold ${vs.bg} ${vs.text} ${vs.border} border`}>
                    <ShieldCheck className="w-3.5 h-3.5" />
                    {session.verdict}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium truncate">{session.workflow_name}</div>
                    <div className="text-text-muted text-xs mt-0.5">{session.model} &middot; {(session.duration_ms / 1000).toFixed(1)}s</div>
                  </div>
                  <div className="text-xs text-text-muted shrink-0">
                    {followedCount}/{totalCount} steps followed
                  </div>
                </button>

                {expanded && (
                  <div className="border-t border-border-subtle">
                    {/* Attention heatmap */}
                    <div className="px-4 py-3 bg-bg-surface">
                      <p className="text-[11px] uppercase tracking-[0.15em] text-text-muted mb-2">Attention heatmap</p>
                      <div className="flex gap-1 items-center">
                        {session.events.map(ev => (
                          <div
                            key={ev.step}
                            className={`h-6 rounded-sm flex-1 ${EVENT_COLORS[ev.status]} opacity-80`}
                            title={`Step ${ev.step}: ${ev.name} (${ev.status})`}
                          />
                        ))}
                      </div>
                      <div className="flex gap-4 mt-2 text-[10px] text-text-muted">
                        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-green-500 inline-block" /> Followed</span>
                        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-danger inline-block" /> Skipped</span>
                        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-warning inline-block" /> Diverged</span>
                      </div>
                    </div>

                    {/* Divergence cards */}
                    {session.events.filter(e => e.status !== 'followed').length > 0 && (
                      <div className="px-4 py-3 space-y-3">
                        <p className="text-[11px] uppercase tracking-[0.15em] text-text-muted">Divergences</p>
                        {session.events
                          .filter(e => e.status !== 'followed')
                          .map(ev => (
                            <DivergenceCard key={ev.step} event={ev} />
                          ))}
                      </div>
                    )}

                    {/* Verdict reason */}
                    <div className="px-4 py-3 border-t border-border-subtle bg-bg-surface">
                      <div className="flex items-start gap-2">
                        {session.verdict === 'PASS' ? (
                          <CheckCircle className="w-4 h-4 text-green-400 mt-0.5 shrink-0" />
                        ) : session.verdict === 'FAIL' ? (
                          <XCircle className="w-4 h-4 text-danger mt-0.5 shrink-0" />
                        ) : (
                          <AlertTriangle className="w-4 h-4 text-warning mt-0.5 shrink-0" />
                        )}
                        <div>
                          <p className="text-[11px] uppercase tracking-[0.15em] text-text-muted mb-1">Verdict reason</p>
                          <p className="text-sm text-text-secondary">{session.reason}</p>
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {!isLive && !loading && (
        <div className="mt-6 p-4 rounded-xl border border-warning/20 bg-warning/5 text-sm text-text-secondary">
          Showing demo data. Start the backend (<code className="text-accent text-xs">uvicorn app.main:app</code>) for live judge sessions.
        </div>
      )}
    </div>
  )
}

function DivergenceCard({ event }: { event: JudgeEvent }) {
  const severityColors: Record<string, string> = {
    critical: 'bg-danger/15 text-danger border-danger/20',
    high: 'bg-danger/10 text-danger border-danger/15',
    medium: 'bg-warning/10 text-warning border-warning/20',
    low: 'bg-white/[0.04] text-text-muted border-border-subtle',
  }
  const sev = event.severity || 'low'

  return (
    <div className="rounded-lg border border-border-subtle bg-bg-primary p-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs font-bold text-text-muted">Step {event.step}</span>
        <span className="text-xs text-text-secondary font-medium">{event.name}</span>
        <span className={`ml-auto text-[10px] px-1.5 py-0.5 rounded border font-medium ${severityColors[sev]}`}>
          {sev}
        </span>
        {event.status === 'skipped' && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-danger/10 text-danger border border-danger/20">skipped</span>
        )}
        {event.status === 'diverged' && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-warning/10 text-warning border border-warning/20">diverged</span>
        )}
      </div>

      {(event.expected || event.actual) && (
        <div className="grid grid-cols-2 gap-3 mb-2">
          <div>
            <p className="text-[10px] uppercase tracking-wider text-text-muted mb-1">Expected</p>
            <p className="text-xs text-text-secondary bg-bg-surface rounded px-2 py-1.5">{event.expected || '—'}</p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider text-text-muted mb-1">Actual</p>
            <p className="text-xs text-text-secondary bg-bg-surface rounded px-2 py-1.5">{event.actual || '—'}</p>
          </div>
        </div>
      )}

      {event.suggestion && (
        <div className="flex items-start gap-1.5 mt-2">
          <ArrowRight className="w-3 h-3 text-accent mt-0.5 shrink-0" />
          <p className="text-xs text-text-secondary">{event.suggestion}</p>
        </div>
      )}

      {event.nudge_count != null && event.nudge_count > 0 && (
        <p className="text-[10px] text-text-muted mt-1.5">Nudged {event.nudge_count} time{event.nudge_count > 1 ? 's' : ''} during execution</p>
      )}
    </div>
  )
}
