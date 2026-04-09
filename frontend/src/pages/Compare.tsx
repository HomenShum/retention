import { useState, useEffect } from 'react'
import { GitCompareArrows, Loader, CheckCircle, XCircle, Minus, DollarSign, Clock, Zap, Target } from 'lucide-react'
import { fetchOrDemo, type ModelComparison, DEMO_COMPARISON } from '../lib/api'
import { LiveBadge } from '../components/LiveBadge'

export function Compare() {
  const [comparison, setComparison] = useState<ModelComparison | null>(null)
  const [isLive, setIsLive] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchOrDemo('/benchmarks/comparison', DEMO_COMPARISON).then(({ data, isLive: live }) => {
      setComparison(data as ModelComparison)
      setIsLive(live)
      setLoading(false)
    })
  }, [])

  if (loading) {
    return (
      <div className="p-6">
        <h1 className="text-xl font-bold mb-1">Model Compare</h1>
        <p className="text-text-muted text-sm mb-6">Side-by-side model performance on the same workflow</p>
        <div className="flex items-center gap-2 text-text-muted text-sm py-12 justify-center">
          <Loader className="w-4 h-4 animate-spin" />
          Connecting to backend...
        </div>
      </div>
    )
  }

  if (!comparison) return null

  const { model_a, model_b } = comparison

  // Determine winners for each metric
  const tokenWinner = model_a.total_tokens <= model_b.total_tokens ? 'a' : 'b'
  const costWinner = model_a.total_cost_usd <= model_b.total_cost_usd ? 'a' : 'b'
  const timeWinner = model_a.total_time_ms <= model_b.total_time_ms ? 'a' : 'b'
  const completionWinner = model_a.completion_rate >= model_b.completion_rate ? 'a' : 'b'

  return (
    <div className="p-6">
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold">Model Compare</h1>
          <p className="text-text-muted text-sm mt-1">
            Side-by-side performance on <span className="text-text-secondary">{comparison.workflow_name}</span>
          </p>
        </div>
        <LiveBadge isLive={isLive} />
      </div>

      {/* Model headers */}
      <div className="grid grid-cols-2 gap-4 mb-6">
        <div className="p-4 rounded-xl bg-bg-card border border-border-subtle text-center">
          <p className="text-[11px] uppercase tracking-[0.15em] text-text-muted mb-1">Model A</p>
          <p className="font-mono text-sm font-bold text-accent">{model_a.model}</p>
        </div>
        <div className="p-4 rounded-xl bg-bg-card border border-border-subtle text-center">
          <p className="text-[11px] uppercase tracking-[0.15em] text-text-muted mb-1">Model B</p>
          <p className="font-mono text-sm font-bold text-text-primary">{model_b.model}</p>
        </div>
      </div>

      {/* Stat cards with winner highlighting */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <CompareCard
          label="Tokens"
          icon={Zap}
          valueA={model_a.total_tokens.toLocaleString()}
          valueB={model_b.total_tokens.toLocaleString()}
          winner={tokenWinner}
        />
        <CompareCard
          label="Cost"
          icon={DollarSign}
          valueA={`$${model_a.total_cost_usd.toFixed(3)}`}
          valueB={`$${model_b.total_cost_usd.toFixed(3)}`}
          winner={costWinner}
        />
        <CompareCard
          label="Time"
          icon={Clock}
          valueA={`${(model_a.total_time_ms / 1000).toFixed(1)}s`}
          valueB={`${(model_b.total_time_ms / 1000).toFixed(1)}s`}
          winner={timeWinner}
        />
        <CompareCard
          label="Completion"
          icon={Target}
          valueA={`${model_a.completion_rate}%`}
          valueB={`${model_b.completion_rate}%`}
          winner={completionWinner}
        />
      </div>

      {/* Side-by-side timelines */}
      <div className="mb-6">
        <p className="text-[11px] uppercase tracking-[0.15em] text-text-muted mb-3">Event timeline</p>
        <div className="grid grid-cols-2 gap-4">
          <TimelineColumn events={model_a.events} otherEvents={model_b.events} label="Model A" />
          <TimelineColumn events={model_b.events} otherEvents={model_a.events} label="Model B" />
        </div>
      </div>

      {/* Cost comparison bar chart */}
      <div className="mb-6">
        <p className="text-[11px] uppercase tracking-[0.15em] text-text-muted mb-3">Cost comparison</p>
        <div className="rounded-xl border border-border-subtle bg-bg-card p-5">
          <div className="space-y-4">
            {/* Model A bar */}
            <div>
              <div className="flex items-center justify-between text-xs mb-1">
                <span className="text-text-muted font-mono">{model_a.model}</span>
                <span className="text-accent font-medium">${model_a.total_cost_usd.toFixed(3)}</span>
              </div>
              <div className="h-6 rounded bg-white/[0.06] overflow-hidden">
                <div
                  className="h-full rounded bg-accent/60 flex items-center pl-2"
                  style={{ width: `${(model_a.total_cost_usd / Math.max(model_a.total_cost_usd, model_b.total_cost_usd)) * 100}%` }}
                >
                  <span className="text-[10px] font-mono text-black/60">{model_a.total_tokens.toLocaleString()} tokens</span>
                </div>
              </div>
            </div>
            {/* Model B bar */}
            <div>
              <div className="flex items-center justify-between text-xs mb-1">
                <span className="text-text-muted font-mono">{model_b.model}</span>
                <span className="text-text-secondary font-medium">${model_b.total_cost_usd.toFixed(3)}</span>
              </div>
              <div className="h-6 rounded bg-white/[0.06] overflow-hidden">
                <div
                  className="h-full rounded bg-white/20 flex items-center pl-2"
                  style={{ width: `${(model_b.total_cost_usd / Math.max(model_a.total_cost_usd, model_b.total_cost_usd)) * 100}%` }}
                >
                  <span className="text-[10px] font-mono text-white/40">{model_b.total_tokens.toLocaleString()} tokens</span>
                </div>
              </div>
            </div>
          </div>
          {/* Delta */}
          <div className="mt-4 pt-3 border-t border-border-subtle flex items-center justify-between text-sm">
            <span className="text-text-muted">Delta</span>
            <span className={`font-bold ${costWinner === 'a' ? 'text-accent' : 'text-text-primary'}`}>
              {costWinner === 'a' ? model_a.model : model_b.model} saves ${Math.abs(model_a.total_cost_usd - model_b.total_cost_usd).toFixed(3)} ({Math.round(Math.abs(model_a.total_cost_usd - model_b.total_cost_usd) / Math.max(model_a.total_cost_usd, model_b.total_cost_usd) * 100)}%)
            </span>
          </div>
        </div>
      </div>

      {!isLive && (
        <div className="p-4 rounded-xl border border-warning/20 bg-warning/5 text-sm text-text-secondary">
          Showing demo data. Start the backend (<code className="text-accent text-xs">uvicorn app.main:app</code>) for live comparisons.
        </div>
      )}
    </div>
  )
}

function CompareCard({ label, icon: Icon, valueA, valueB, winner }: {
  label: string
  icon: React.ComponentType<{ className?: string }>
  valueA: string
  valueB: string
  winner: 'a' | 'b'
}) {
  return (
    <div className="rounded-xl bg-bg-card border border-border-subtle overflow-hidden">
      <div className="px-3 py-2 flex items-center gap-1.5 border-b border-border-subtle bg-bg-surface">
        <Icon className="w-3.5 h-3.5 text-text-muted" />
        <span className="text-[11px] uppercase tracking-[0.15em] text-text-muted">{label}</span>
      </div>
      <div className="grid grid-cols-2 divide-x divide-border-subtle">
        <div className={`px-3 py-3 text-center ${winner === 'a' ? 'bg-accent/5 ring-1 ring-inset ring-accent/20' : ''}`}>
          <div className={`text-lg font-bold ${winner === 'a' ? 'text-accent' : 'text-text-secondary'}`}>{valueA}</div>
          {winner === 'a' && <div className="text-[9px] text-accent uppercase tracking-wider mt-0.5">winner</div>}
        </div>
        <div className={`px-3 py-3 text-center ${winner === 'b' ? 'bg-accent/5 ring-1 ring-inset ring-accent/20' : ''}`}>
          <div className={`text-lg font-bold ${winner === 'b' ? 'text-accent' : 'text-text-secondary'}`}>{valueB}</div>
          {winner === 'b' && <div className="text-[9px] text-accent uppercase tracking-wider mt-0.5">winner</div>}
        </div>
      </div>
    </div>
  )
}

function TimelineColumn({ events, otherEvents, label }: {
  events: ModelComparison['model_a']['events']
  otherEvents: ModelComparison['model_a']['events']
  label: string
}) {
  return (
    <div className="rounded-xl border border-border-subtle bg-bg-card overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border-subtle bg-bg-surface">
        <span className="text-[11px] uppercase tracking-[0.15em] text-text-muted">{label}</span>
      </div>
      <div className="divide-y divide-border-subtle">
        {events.map(ev => {
          const otherEv = otherEvents.find(o => o.step === ev.step)
          const differs = otherEv && otherEv.status !== ev.status

          return (
            <div key={ev.step} className={`flex items-center gap-2.5 px-4 py-2 ${differs ? 'bg-warning/5' : ''}`}>
              <span className="text-text-muted text-[10px] w-4 text-right shrink-0">{ev.step}</span>
              {ev.status === 'ok' ? (
                <CheckCircle className="w-3.5 h-3.5 text-green-400 shrink-0" />
              ) : ev.status === 'skipped' ? (
                <XCircle className="w-3.5 h-3.5 text-danger shrink-0" />
              ) : (
                <Minus className="w-3.5 h-3.5 text-warning shrink-0" />
              )}
              <span className="text-xs text-text-secondary truncate flex-1">{ev.name}</span>
              {ev.status !== 'skipped' && (
                <span className="text-[10px] text-text-muted font-mono shrink-0">{ev.tokens} tok</span>
              )}
              {differs && (
                <span className="text-[9px] px-1 py-0.5 rounded bg-warning/15 text-warning shrink-0">diff</span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
