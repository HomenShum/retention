import { Link } from 'react-router-dom'
import { Terminal, ArrowRight, Shield, Zap, GitBranch, Users, ChevronRight } from 'lucide-react'
import { useState } from 'react'

const INSTALL_CMD = 'curl -sL retention.sh/install.sh | bash'

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 2000) }}
      className="text-xs px-2 py-1 rounded bg-white/10 hover:bg-white/20 transition-colors text-text-secondary"
    >
      {copied ? 'Copied!' : 'Copy'}
    </button>
  )
}

export function Landing() {
  return (
    <div className="min-h-screen bg-bg-primary">
      {/* Nav */}
      <header className="fixed top-0 w-full z-50 backdrop-blur-md bg-bg-primary/80 border-b border-border-subtle">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-2 text-accent font-semibold">
            <Terminal className="w-5 h-5" />
            retention.sh
          </div>
          <nav className="hidden sm:flex items-center gap-6 text-sm text-text-secondary">
            <a href="#how" className="hover:text-text-primary transition-colors no-underline">How it works</a>
            <a href="#proof" className="hover:text-text-primary transition-colors no-underline">Benchmarks</a>
            <a href="#tools" className="hover:text-text-primary transition-colors no-underline">Tools</a>
            <Link to="/dashboard" className="px-3 py-1.5 rounded-lg bg-accent text-black font-medium hover:bg-accent-muted transition-colors no-underline text-sm">
              Dashboard
            </Link>
          </nav>
        </div>
      </header>

      {/* Hero */}
      <section className="pt-28 pb-14 px-6">
        <div className="max-w-3xl mx-auto text-center">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-accent/30 bg-accent/5 text-accent text-xs font-medium mb-6">
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
            Always-on workflow judge
          </div>
          <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold tracking-tight leading-[1.1] mb-6">
            AI agents forget.
            <br />
            <span className="text-accent">retention.sh remembers.</span>
          </h1>
          <p className="text-lg text-text-secondary max-w-xl mx-auto mb-10 leading-relaxed">
            Your AI coding agent re-crawls your app from scratch every QA run.
            retention.sh gives it memory -- replay saved workflows at 60-70% fewer tokens.
          </p>

          {/* Install command */}
          <div className="inline-flex items-center gap-3 px-5 py-3 rounded-xl bg-bg-card border border-border-muted font-mono text-sm mb-6">
            <span className="text-accent">$</span>
            <code className="text-text-primary">{INSTALL_CMD}</code>
            <CopyButton text={INSTALL_CMD} />
          </div>

          <div className="flex items-center justify-center gap-4 mt-6">
            <Link
              to="/dashboard"
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg bg-accent text-black font-semibold text-sm hover:bg-accent-muted transition-colors no-underline"
            >
              Open Dashboard <ArrowRight className="w-4 h-4" />
            </Link>
            <a
              href="https://github.com/HomenShum/retention"
              target="_blank"
              rel="noopener"
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg border border-border-muted text-text-secondary text-sm hover:text-text-primary hover:border-border-subtle transition-colors no-underline"
            >
              GitHub <ChevronRight className="w-3.5 h-3.5" />
            </a>
          </div>
        </div>
      </section>

      {/* How it works */}
      <section id="how" className="py-14 px-6 border-t border-border-subtle">
        <div className="max-w-4xl mx-auto">
          <h2 className="text-2xl font-bold text-center mb-12">How it works</h2>
          <div className="grid sm:grid-cols-3 gap-6">
            {[
              { step: '1', title: 'Agent writes code', desc: 'Your AI coding agent (Claude, Cursor, Windsurf) patches your app.' },
              { step: '2', title: 'retention.sh verifies', desc: 'Crawls the real app, captures evidence, produces a structured QA report.' },
              { step: '3', title: 'Memory saves tokens', desc: 'Workflow saved as trajectory. Next run replays at 60-70% fewer tokens.' },
            ].map(({ step, title, desc }) => (
              <div key={step} className="p-6 rounded-xl bg-bg-card border border-border-subtle">
                <div className="w-8 h-8 rounded-lg bg-accent/10 text-accent font-bold text-sm flex items-center justify-center mb-4">
                  {step}
                </div>
                <h3 className="font-semibold text-sm mb-2">{title}</h3>
                <p className="text-text-secondary text-sm leading-relaxed">{desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Tools */}
      <section id="tools" className="py-14 px-6 border-t border-border-subtle">
        <div className="max-w-4xl mx-auto">
          <h2 className="text-2xl font-bold text-center mb-4">Key tools</h2>
          <p className="text-text-secondary text-center mb-12 text-sm">All available via MCP -- call directly from your AI agent.</p>
          <div className="grid sm:grid-cols-2 gap-4">
            {[
              { icon: Shield, name: 'ta.qa_check(url)', desc: 'Instant QA scan -- JS errors, a11y, rendering issues' },
              { icon: GitBranch, name: 'ta.diff_crawl(url)', desc: 'Before/after comparison across code changes' },
              { icon: Zap, name: 'ta.start_workflow(url)', desc: 'Smart start -- auto-replays saved trajectory if available' },
              { icon: Users, name: 'ta.team.invite', desc: 'Share trajectory memory across your team' },
            ].map(({ icon: Icon, name, desc }) => (
              <div key={name} className="flex gap-4 p-5 rounded-xl bg-bg-card border border-border-subtle">
                <Icon className="w-5 h-5 text-accent shrink-0 mt-0.5" />
                <div>
                  <code className="text-sm font-semibold text-text-primary">{name}</code>
                  <p className="text-text-secondary text-sm mt-1">{desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Benchmark proof */}
      <section id="proof" className="py-14 px-6 border-t border-border-subtle">
        <div className="max-w-3xl mx-auto">
          <h2 className="text-2xl font-bold text-center mb-4">Benchmark proof</h2>
          <p className="text-text-secondary text-center mb-10 text-sm">
            Real API calls, verified by independent LLM judge. N=15 CSP runs.
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-10">
            {[
              { value: '63-73%', label: 'Cost savings' },
              { value: '89%', label: 'Judge agreement' },
              { value: '3', label: 'Workflow families' },
              { value: '21', label: 'Live API calls' },
            ].map(({ value, label }) => (
              <div key={label} className="text-center p-5 rounded-xl bg-bg-card border border-border-subtle">
                <div className="text-2xl font-bold text-accent">{value}</div>
                <div className="text-text-muted text-xs mt-1">{label}</div>
              </div>
            ))}
          </div>
          <div className="p-5 rounded-xl bg-bg-card border border-border-subtle">
            <h3 className="text-sm font-semibold mb-3">Verify it yourself</h3>
            <div className="space-y-2 font-mono text-xs text-text-secondary">
              <div><span className="text-accent">$</span> python backend/scripts/verify_stats.py</div>
              <div><span className="text-accent">$</span> python backend/scripts/live_retention_proof.py</div>
              <div><span className="text-accent">$</span> python backend/scripts/run_calibration.py</div>
            </div>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-14 px-6 border-t border-border-subtle">
        <div className="max-w-2xl mx-auto text-center">
          <h2 className="text-2xl font-bold mb-4">Start in 60 seconds</h2>
          <div className="inline-flex items-center gap-3 px-5 py-3 rounded-xl bg-bg-card border border-border-muted font-mono text-sm mb-6">
            <span className="text-accent">$</span>
            <code className="text-text-primary">{INSTALL_CMD}</code>
            <CopyButton text={INSTALL_CMD} />
          </div>
          <p className="text-text-muted text-sm mt-4">
            Then: <code className="text-text-secondary bg-bg-card px-2 py-0.5 rounded text-xs">ta.qa_check(url='http://localhost:3000')</code>
          </p>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-8 px-6 border-t border-border-subtle">
        <div className="max-w-5xl mx-auto flex items-center justify-between text-xs text-text-muted">
          <span>retention.sh</span>
          <div className="flex gap-4">
            <a href="https://github.com/HomenShum/retention" target="_blank" rel="noopener" className="hover:text-text-secondary transition-colors no-underline">GitHub</a>
            <Link to="/dashboard" className="hover:text-text-secondary transition-colors no-underline">Dashboard</Link>
          </div>
        </div>
      </footer>
    </div>
  )
}
