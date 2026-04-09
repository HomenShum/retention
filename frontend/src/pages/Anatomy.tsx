import { useState, useEffect } from 'react'
import { Eye, Clock, Cpu, DollarSign, AlertTriangle, Loader, CheckCircle, XCircle, ChevronDown } from 'lucide-react'
import { fetchOrDemo, type RunAnatomy, type ToolCall, DEMO_ANATOMY } from '../lib/api'
import { LiveBadge } from '../components/LiveBadge'

export function Anatomy() {
  const [anatomy, setAnatomy] = useState<RunAnatomy | null>(null)
  const [isLive, setIsLive] = useState(false)
  const [loading, setLoading] = useState(true)
  const [showAll, setShowAll] = useState(false)

  useEffect(() => {
    fetchOrDemo('/pipeline/results', { runs: [DEMO_ANATOMY] }).then(({ data, isLive: live }) => {
      // Extract the latest run from the results
      const runs = (data as { runs?: RunAnatomy[] })?.runs
      const latest = Array.isArray(runs) && runs.length > 0 ? runs[0] : DEMO_ANATOMY
      setAnatomy(latest)
      setIsLive(live)
      setLoading(false)
    })
  }, [])

  if (loading) {
    return (
      <div className="p-6">
        <h1 className="text-xl font-bold mb-1">Run Anatomy</h1>
        <p className="text-text-muted text-sm mb-6">Full trace of every tool call in your latest run</p>
        <div className="flex items-center gap-2 text-text-muted text-sm py-12 justify-center">
          <Loader className="w-4 h-4 animate-spin" />
          Connecting to backend...
        </div>
      </div>
    )
  }

  if (!anatomy) return null

  const toolBreakdown = getToolBreakdown(anatomy.tool_calls)
  const visibleCalls = showAll ? anatomy.tool_calls : anatomy.tool_calls.slice(0, 20)
  const costPerToken = anatomy.total_cost_usd / (anatomy.input_tokens + anatomy.output_tokens)
  const inputCost = anatomy.input_tokens * costPerToken
  const outputCost = anatomy.output_tokens * costPerToken

  return (
    <div className="p-6">
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold">Run Anatomy</h1>
          <p className="text-text-muted text-sm mt-1">Full trace of every tool call in your latest run</p>
        </div>
        <LiveBadge isLive={isLive} />
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        {[
          { label: 'Duration', value: `${(anatomy.duration_ms / 1000).toFixed(1)}s`, icon: Clock, color: 'text-text-primary' },
          { label: 'Tool Calls', value: anatomy.total_tool_calls.toString(), icon: Cpu, color: 'text-accent' },
          { label: 'Cost', value: `$${anatomy.total_cost_usd.toFixed(3)}`, icon: DollarSign, color: 'text-text-primary' },
          { label: 'Errors', value: anatomy.total_errors.toString(), icon: AlertTriangle, color: anatomy.total_errors > 0 ? 'text-danger' : 'text-green-400' },
        ].map(({ label, value, icon: Icon, color }) => (
          <div key={label} className="p-4 rounded-xl bg-bg-card border border-border-subtle">
            <div className="flex items-center gap-2 mb-2">
              <Icon className={`w-4 h-4 ${color}`} />
              <span className="text-[11px] uppercase tracking-[0.15em] text-text-muted">{label}</span>
            </div>
            <div className={`text-2xl font-bold ${color}`}>{value}</div>
          </div>
        ))}
      </div>

      {/* Tool breakdown table */}
      <div className="mb-6">
        <p className="text-[11px] uppercase tracking-[0.15em] text-text-muted mb-3">Tool breakdown</p>
        <div className="rounded-xl border border-border-subtle overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border-subtle bg-bg-surface">
                <th className="text-left px-4 py-3 text-[11px] uppercase tracking-[0.15em] text-text-muted font-medium">Tool</th>
                <th className="text-right px-4 py-3 text-[11px] uppercase tracking-[0.15em] text-text-muted font-medium">Calls</th>
                <th className="text-right px-4 py-3 text-[11px] uppercase tracking-[0.15em] text-text-muted font-medium">Avg Latency</th>
                <th className="text-left px-4 py-3 text-[11px] uppercase tracking-[0.15em] text-text-muted font-medium w-48">Success Rate</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {toolBreakdown.map(t => (
                <tr key={t.tool} className="hover:bg-white/[0.02] transition-colors">
                  <td className="px-4 py-2.5 font-mono text-xs text-accent">{t.tool}</td>
                  <td className="px-4 py-2.5 text-right text-text-secondary">{t.calls}</td>
                  <td className="px-4 py-2.5 text-right text-text-secondary">{t.avgLatencyMs.toFixed(0)}ms</td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <div className="flex-1 h-1.5 rounded-full bg-white/[0.06] overflow-hidden">
                        <div
                          className={`h-full rounded-full ${t.successRate === 100 ? 'bg-green-500' : t.successRate >= 80 ? 'bg-warning' : 'bg-danger'}`}
                          style={{ width: `${t.successRate}%` }}
                        />
                      </div>
                      <span className="text-xs text-text-muted w-10 text-right">{t.successRate}%</span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Timeline */}
      <div className="mb-6">
        <p className="text-[11px] uppercase tracking-[0.15em] text-text-muted mb-3">
          Call timeline ({anatomy.tool_calls.length} total)
        </p>
        <div className="relative pl-6 space-y-0">
          {/* Vertical line */}
          <div className="absolute left-[11px] top-2 bottom-2 w-px bg-border-subtle" />
          {visibleCalls.map((tc, i) => (
            <TimelineEntry key={tc.id} call={tc} index={i} />
          ))}
        </div>

        {anatomy.tool_calls.length > 20 && !showAll && (
          <button
            onClick={() => setShowAll(true)}
            className="mt-3 ml-6 inline-flex items-center gap-1.5 text-xs text-accent hover:underline"
          >
            <ChevronDown className="w-3 h-3" />
            Show all {anatomy.tool_calls.length} calls
          </button>
        )}
      </div>

      {/* Cost breakdown */}
      <div className="mb-6">
        <p className="text-[11px] uppercase tracking-[0.15em] text-text-muted mb-3">Cost breakdown</p>
        <div className="rounded-xl border border-border-subtle bg-bg-card p-4">
          <div className="flex items-center gap-4 mb-3">
            <div className="flex-1">
              <div className="flex items-center justify-between text-xs mb-1">
                <span className="text-text-muted">Input tokens</span>
                <span className="text-text-secondary">{anatomy.input_tokens.toLocaleString()} &middot; ${inputCost.toFixed(4)}</span>
              </div>
              <div className="h-2 rounded-full bg-white/[0.06] overflow-hidden">
                <div className="h-full rounded-full bg-accent/70" style={{ width: `${(anatomy.input_tokens / (anatomy.input_tokens + anatomy.output_tokens)) * 100}%` }} />
              </div>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex-1">
              <div className="flex items-center justify-between text-xs mb-1">
                <span className="text-text-muted">Output tokens</span>
                <span className="text-text-secondary">{anatomy.output_tokens.toLocaleString()} &middot; ${outputCost.toFixed(4)}</span>
              </div>
              <div className="h-2 rounded-full bg-white/[0.06] overflow-hidden">
                <div className="h-full rounded-full bg-accent/40" style={{ width: `${(anatomy.output_tokens / (anatomy.input_tokens + anatomy.output_tokens)) * 100}%` }} />
              </div>
            </div>
          </div>
          <div className="flex items-center justify-between mt-3 pt-3 border-t border-border-subtle text-sm">
            <span className="text-text-muted">Total cost</span>
            <span className="font-bold">${anatomy.total_cost_usd.toFixed(3)}</span>
          </div>
        </div>
      </div>

      {!isLive && (
        <div className="p-4 rounded-xl border border-warning/20 bg-warning/5 text-sm text-text-secondary">
          Showing demo data. Start the backend (<code className="text-accent text-xs">uvicorn app.main:app</code>) for live run anatomy.
        </div>
      )}
    </div>
  )
}

function TimelineEntry({ call, index }: { call: ToolCall; index: number }) {
  const ok = call.status === 'ok'
  return (
    <div className="flex items-start gap-3 py-1.5 relative">
      {/* Dot */}
      <div className={`w-[9px] h-[9px] rounded-full mt-1.5 shrink-0 ring-2 ring-bg-card ${ok ? 'bg-green-500' : 'bg-danger'}`} />
      <div className="flex-1 flex items-center gap-3 min-w-0">
        <span className="text-text-muted text-[10px] w-5 text-right shrink-0">{index + 1}</span>
        <span className="font-mono text-xs text-accent shrink-0">{call.tool}</span>
        {call.detail && <span className="text-xs text-text-muted truncate">{call.detail}</span>}
        <span className="ml-auto text-[10px] text-text-muted shrink-0">{call.duration_ms}ms</span>
        {ok ? (
          <CheckCircle className="w-3 h-3 text-green-500 shrink-0" />
        ) : (
          <XCircle className="w-3 h-3 text-danger shrink-0" />
        )}
      </div>
    </div>
  )
}

interface ToolSummary {
  tool: string
  calls: number
  avgLatencyMs: number
  successRate: number
}

function getToolBreakdown(calls: ToolCall[]): ToolSummary[] {
  const map = new Map<string, { total: number; ok: number; latencySum: number }>()
  for (const c of calls) {
    const entry = map.get(c.tool) || { total: 0, ok: 0, latencySum: 0 }
    entry.total++
    if (c.status === 'ok') entry.ok++
    entry.latencySum += c.duration_ms
    map.set(c.tool, entry)
  }
  return Array.from(map.entries())
    .map(([tool, { total, ok, latencySum }]) => ({
      tool,
      calls: total,
      avgLatencyMs: latencySum / total,
      successRate: Math.round((ok / total) * 100),
    }))
    .sort((a, b) => b.calls - a.calls)
}
