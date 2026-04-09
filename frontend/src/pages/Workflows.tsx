import { useState, useEffect } from 'react'
import { GitBranch, Plus, Play, Trash2, Archive, Loader } from 'lucide-react'
import { fetchOrDemo, type Workflow, DEMO_WORKFLOWS } from '../lib/api'
import { LiveBadge } from '../components/LiveBadge'

export function Workflows() {
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [isLive, setIsLive] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchOrDemo('/workflow-registry/workflows', DEMO_WORKFLOWS).then(({ data, isLive: live }) => {
      setWorkflows(Array.isArray(data) ? data : DEMO_WORKFLOWS)
      setIsLive(live)
      setLoading(false)
    })
  }, [])

  return (
    <div className="p-6">
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold">Workflows</h1>
          <p className="text-text-muted text-sm mt-1">Captured agent workflows ready for replay and judging</p>
        </div>
        <div className="flex items-center gap-3">
          <LiveBadge isLive={isLive} />
          <button className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-accent text-black font-medium text-sm hover:bg-accent-muted transition-colors">
            <Plus className="w-4 h-4" />
            Capture New
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-text-muted text-sm py-12 justify-center">
          <Loader className="w-4 h-4 animate-spin" />
          Connecting to backend...
        </div>
      ) : workflows.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="rounded-xl border border-border-subtle overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border-subtle bg-bg-surface">
                <th className="text-left px-4 py-3 text-[11px] uppercase tracking-[0.15em] text-text-muted font-medium">Name</th>
                <th className="text-left px-4 py-3 text-[11px] uppercase tracking-[0.15em] text-text-muted font-medium">Source Model</th>
                <th className="text-right px-4 py-3 text-[11px] uppercase tracking-[0.15em] text-text-muted font-medium">Events</th>
                <th className="text-right px-4 py-3 text-[11px] uppercase tracking-[0.15em] text-text-muted font-medium">Compression</th>
                <th className="text-left px-4 py-3 text-[11px] uppercase tracking-[0.15em] text-text-muted font-medium">Captured</th>
                <th className="text-right px-4 py-3 text-[11px] uppercase tracking-[0.15em] text-text-muted font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {workflows.map(wf => (
                <tr key={wf.id} className="hover:bg-white/[0.02] transition-colors">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <GitBranch className="w-3.5 h-3.5 text-accent shrink-0" />
                      <span className="font-medium truncate max-w-[280px]">{wf.name}</span>
                      {wf.status === 'archived' && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.06] text-text-muted">archived</span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-text-secondary font-mono text-xs">{wf.source_model}</td>
                  <td className="px-4 py-3 text-right text-text-secondary">{wf.events_count}</td>
                  <td className="px-4 py-3 text-right">
                    <span className="text-accent font-medium">{wf.compression_pct}%</span>
                  </td>
                  <td className="px-4 py-3 text-text-muted text-xs">{formatRelative(wf.captured_at)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1 justify-end">
                      <button className="p-1.5 rounded-md hover:bg-white/[0.06] text-text-muted hover:text-accent transition-colors" title="Replay">
                        <Play className="w-3.5 h-3.5" />
                      </button>
                      <button className="p-1.5 rounded-md hover:bg-white/[0.06] text-text-muted hover:text-text-secondary transition-colors" title="Archive">
                        <Archive className="w-3.5 h-3.5" />
                      </button>
                      <button className="p-1.5 rounded-md hover:bg-white/[0.06] text-text-muted hover:text-danger transition-colors" title="Delete">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!isLive && !loading && (
        <div className="mt-6 p-4 rounded-xl border border-warning/20 bg-warning/5 text-sm text-text-secondary">
          Showing demo data. Start the backend (<code className="text-accent text-xs">uvicorn app.main:app</code>) for live workflows.
        </div>
      )}
    </div>
  )
}

function EmptyState() {
  return (
    <div className="text-center py-16">
      <GitBranch className="w-12 h-12 text-text-muted mx-auto mb-4" />
      <h3 className="text-lg font-semibold mb-2">No workflows captured</h3>
      <p className="text-text-secondary text-sm max-w-md mx-auto mb-6">
        Capture your first agent workflow to start judging and replaying it.
      </p>
      <div className="inline-flex items-center gap-3 px-4 py-2.5 rounded-xl bg-bg-card border border-border-muted font-mono text-xs">
        <span className="text-accent">$</span>
        <code>retention capture --workflow "my-flow"</code>
      </div>
    </div>
  )
}

function formatRelative(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}
