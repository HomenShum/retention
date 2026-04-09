import { useState, useEffect } from 'react'
import { BarChart3, Zap, Clock, CheckCircle, XCircle, Target, Loader, Terminal } from 'lucide-react'
import { fetchOrDemo, type BenchmarkResult, DEMO_BENCHMARK } from '../lib/api'
import { LiveBadge } from '../components/LiveBadge'

export function Benchmark() {
  const [bench, setBench] = useState<BenchmarkResult | null>(null)
  const [isLive, setIsLive] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchOrDemo('/benchmarks/results', DEMO_BENCHMARK).then(({ data, isLive: live }) => {
      setBench(data as BenchmarkResult)
      setIsLive(live)
      setLoading(false)
    })
  }, [])

  if (loading) {
    return (
      <div className="p-6">
        <h1 className="text-xl font-bold mb-1">Benchmark</h1>
        <p className="text-text-muted text-sm mb-6">Token and time savings with retention.sh replay</p>
        <div className="flex items-center gap-2 text-text-muted text-sm py-12 justify-center">
          <Loader className="w-4 h-4 animate-spin" />
          Connecting to backend...
        </div>
      </div>
    )
  }

  if (!bench) return null

  return (
    <div className="p-6">
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold">Benchmark</h1>
          <p className="text-text-muted text-sm mt-1">Token and time savings with retention.sh replay</p>
        </div>
        <LiveBadge isLive={isLive} />
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        {[
          { label: 'Token Savings', value: `${bench.token_savings_pct}%`, icon: Zap, color: 'text-accent' },
          { label: 'Time Savings', value: `${bench.time_savings_pct}%`, icon: Clock, color: 'text-accent' },
          { label: 'Completion Rate', value: `${bench.completion_rate}%`, icon: Target, color: bench.completion_rate >= 90 ? 'text-green-400' : 'text-warning' },
          { label: 'First-pass Success', value: `${bench.first_pass_rate}%`, icon: CheckCircle, color: bench.first_pass_rate >= 85 ? 'text-green-400' : 'text-warning' },
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

      {/* Task table */}
      <div className="mb-8">
        <p className="text-[11px] uppercase tracking-[0.15em] text-text-muted mb-3">Task-by-task results</p>
        <div className="rounded-xl border border-border-subtle overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border-subtle bg-bg-surface">
                <th className="text-left px-4 py-3 text-[11px] uppercase tracking-[0.15em] text-text-muted font-medium">Task</th>
                <th className="text-right px-4 py-3 text-[11px] uppercase tracking-[0.15em] text-text-muted font-medium">Without retention</th>
                <th className="text-right px-4 py-3 text-[11px] uppercase tracking-[0.15em] text-text-muted font-medium">With retention</th>
                <th className="text-right px-4 py-3 text-[11px] uppercase tracking-[0.15em] text-text-muted font-medium">Savings</th>
                <th className="text-center px-4 py-3 text-[11px] uppercase tracking-[0.15em] text-text-muted font-medium">Verdict</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {bench.tasks.map(task => (
                <tr key={task.name} className="hover:bg-white/[0.02] transition-colors">
                  <td className="px-4 py-2.5 font-medium">{task.name}</td>
                  <td className="px-4 py-2.5 text-right text-text-muted">
                    <span className="font-mono text-xs">{task.without_tokens.toLocaleString()} tok</span>
                    <span className="text-text-muted mx-1">/</span>
                    <span className="font-mono text-xs">{(task.without_time_ms / 1000).toFixed(1)}s</span>
                  </td>
                  <td className="px-4 py-2.5 text-right text-accent">
                    <span className="font-mono text-xs">{task.with_tokens.toLocaleString()} tok</span>
                    <span className="text-accent/50 mx-1">/</span>
                    <span className="font-mono text-xs">{(task.with_time_ms / 1000).toFixed(1)}s</span>
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <SavingsBar pct={task.savings_pct} />
                  </td>
                  <td className="px-4 py-2.5 text-center">
                    {task.verdict === 'pass' ? (
                      <CheckCircle className="w-4 h-4 text-green-400 mx-auto" />
                    ) : task.verdict === 'fail' ? (
                      <XCircle className="w-4 h-4 text-danger mx-auto" />
                    ) : (
                      <span className="text-text-muted text-xs">skip</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Methodology */}
      <div className="mb-6">
        <p className="text-[11px] uppercase tracking-[0.15em] text-text-muted mb-3">Methodology</p>
        <div className="rounded-xl border border-border-subtle bg-bg-card p-5 space-y-3 text-sm text-text-secondary">
          <p>
            Each task is run twice: once as a fresh crawl (no memory), and once using retention.sh's
            trajectory replay. The judge verifies that the replayed run produces identical or better results.
          </p>
          <p>
            Token counts are measured at the API level. Time is wall-clock from first tool call to final assertion.
            Savings percentage = 1 - (with_retention / without_retention).
          </p>
          <p className="text-text-muted text-xs">
            Measured on {new Date(bench.run_at).toLocaleDateString()} using retention.sh v0.1.0
          </p>
        </div>
      </div>

      {/* Verify yourself */}
      <div>
        <p className="text-[11px] uppercase tracking-[0.15em] text-text-muted mb-3">Verify yourself</p>
        <div className="rounded-xl border border-border-subtle bg-bg-card p-5 space-y-3">
          <p className="text-sm text-text-secondary mb-4">Run the benchmark on your own machine:</p>
          {[
            'curl -sL retention.sh/install.sh | bash',
            'retention benchmark --url http://localhost:3000',
            'retention benchmark --compare --model-a claude-sonnet-4 --model-b gpt-4o',
          ].map(cmd => (
            <div key={cmd} className="flex items-center gap-3 px-4 py-2.5 rounded-lg bg-bg-primary border border-border-muted font-mono text-xs">
              <Terminal className="w-3.5 h-3.5 text-accent shrink-0" />
              <code className="text-text-primary">{cmd}</code>
            </div>
          ))}
        </div>
      </div>

      {!isLive && (
        <div className="mt-6 p-4 rounded-xl border border-warning/20 bg-warning/5 text-sm text-text-secondary">
          Showing demo data. Start the backend (<code className="text-accent text-xs">uvicorn app.main:app</code>) for live benchmark results.
        </div>
      )}
    </div>
  )
}

function SavingsBar({ pct }: { pct: number }) {
  return (
    <div className="flex items-center gap-2 justify-end">
      <div className="w-16 h-1.5 rounded-full bg-white/[0.06] overflow-hidden">
        <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-accent font-mono text-xs font-medium w-10 text-right">{pct}%</span>
    </div>
  )
}
